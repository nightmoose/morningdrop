"""Ask Gemma for the morning summary, then make AISchemaGate check it.

The model never gets raw rows, only counts and violation messages. Its
answer must pass the `morning_summary` contract (AISchemaGate, same engine
as DataContractGate). On failure we send the violations back as a repair
prompt; after the retry budget, we fall back to a plain template so the
morning report always exists.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from importlib import resources
from typing import Any, Dict, List

from aischemagate import Contract, LLMResponseError, extract_json

from . import tracing
from .gate import FileReport
from .llm import LLM

SYSTEM = """You are an on-call data engineer's overnight assistant.
You get validation results for last night's data drops. Write the morning
summary as JSON with exactly these keys:
  status: "green" | "yellow" | "red"  (worst file wins)
  line1:  one sentence, what happened overnight (10-160 chars)
  line2:  one sentence, the first thing to do this morning (10-160 chars)
  fixes:  list of {file, field, kind, suggestion}; kind is "fix_data",
          "change_contract" or "investigate". Only use files and fields that
          appear in FACTS. Empty list if everything passed.
Be concrete: name the file, the field and the count. No greetings. JSON only."""

JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["green", "yellow", "red"]},
        "line1": {"type": "string"},
        "line2": {"type": "string"},
        "fixes": {"type": "array", "items": {"type": "object", "properties": {
            "file": {"type": "string"}, "field": {"type": "string"},
            "kind": {"type": "string", "enum": ["fix_data", "change_contract", "investigate"]},
            "suggestion": {"type": "string"}},
            "required": ["file", "field", "kind", "suggestion"]}},
    },
    "required": ["status", "line1", "line2", "fixes"],
}


def _summary_contract():
    text = resources.files("morningdrop").joinpath("summary_contract.yaml").read_text()
    return Contract.from_yaml(text).compile()


@dataclass
class Summary:
    status: str
    line1: str
    line2: str
    fixes: List[Dict[str, str]]
    source: str                      # "model" or "fallback"
    attempts: int
    rejections: List[List[str]] = field(default_factory=list)  # AISchemaGate violations per rejected attempt

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()


def build_facts(reports: List[FileReport]) -> Dict[str, Any]:
    files = []
    for r in reports:
        f = r.facts()
        f["status"] = r.status
        files.append(f)
    return {"files": files}


def _grounding(data: Dict[str, Any], reports: List[FileReport]) -> List[str]:
    """Beyond shape: every fix must point at a real file (and, where we know
    them, a real field). Small models like to invent columns."""
    known = {r.file: {f for (f, _k) in r.violations} for r in reports}
    problems = []
    for i, fx in enumerate(data.get("fixes") or []):
        if fx.get("file") not in known:
            problems.append(f"fixes[{i}].file '{fx.get('file')}' is not one of the files in FACTS")
    return problems


def fallback(reports: List[FileReport]) -> Summary:
    rank = {"red": 0, "yellow": 1, "green": 2}
    bad = sorted((r for r in reports if r.status != "green"), key=lambda r: (rank[r.status], -r.failed))
    worst = "red" if any(r.status == "red" for r in reports) else ("yellow" if bad else "green")
    total_failed = sum(r.failed for r in reports)
    line1 = f"{len(reports)} drops checked overnight; {total_failed} rows failed their contracts."
    if bad:
        r = bad[0]
        top = r.violations.most_common(1)
        what = f"{top[0][0][0]} ({top[0][0][1]}, {top[0][1]} rows)" if top else (r.error or "no contract yet")
        line2 = f"Start with {r.file}: {what}."
    else:
        line2 = "Everything passed. Nothing to do."
    fixes = [
        {"file": r.file, "field": f, "kind": "investigate",
         "suggestion": f"{c} row(s) failed {k} on {f}."}
        for r in bad for (f, k), c in r.violations.most_common(2)
    ]
    return Summary(worst, line1[:160], line2[:160], fixes, "fallback", 0)


def summarize(reports: List[FileReport], llm: LLM, max_retries: int = 2) -> Summary:
    contract = _summary_contract()
    facts = build_facts(reports)
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": "FACTS:" + json.dumps(facts, indent=1)},
    ]
    rejections: List[List[str]] = []
    for attempt in range(1, max_retries + 2):
        with tracing.span("gen_ai.chat", f"chat {llm.cfg.model}",
                          **{"gen_ai.request.model": llm.cfg.model, "attempt": attempt}):
            try:
                raw = llm.chat(messages, json_schema=JSON_SCHEMA)
            except Exception as e:
                rejections.append([f"model call failed: {e}"])
                break
        with tracing.span("gen_ai.execute_tool", "aischemagate.validate"):
            try:
                data = extract_json(raw)
                problems = [f"field '{v.field}': {v.message}" for v in contract.validate(data).violations]
            except LLMResponseError as e:
                data, problems = {}, [f"response was not a JSON object: {e}"]
            problems += _grounding(data, reports) if not problems else []
        if not problems:
            return Summary(data["status"], data["line1"], data["line2"], data["fixes"],
                           "model", attempt, rejections)
        rejections.append(problems)
        messages = messages + [
            {"role": "assistant", "content": raw if isinstance(raw, str) else json.dumps(data)},
            {"role": "user", "content": "AISchemaGate rejected that JSON:\n- " + "\n- ".join(problems)
             + "\nReturn ONLY the corrected JSON object."},
        ]
    fb = fallback(reports)
    fb.attempts = len(rejections)
    fb.rejections = rejections
    return fb
