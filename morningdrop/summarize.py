"""Ask Gemma for the morning summary, then make AISchemaGate check it.

The model never gets raw rows, only counts and violation messages. Its
answer must pass the `morning_summary` contract (AISchemaGate, same engine
as DataContractGate). On failure we send the violations back as a repair
prompt; after the retry budget, we fall back to a plain template so the
morning report always exists.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from importlib import resources
from typing import Any, Dict, List

from aischemagate import Contract, LLMResponseError, extract_json

from . import tracing
from .gate import FileReport
from .llm import LLM

SYSTEM = """You are an on-call data engineer's overnight assistant.
You get contract-validation results for last night's data drops (FACTS).
Reply with one JSON object:
- status: the worst "status" among the files in FACTS ("green", "yellow" or "red").
- line1: one short sentence (under 150 characters) saying what broke overnight,
  naming worst_file and how many of its rows failed.
- line2: one short sentence (under 150 characters) saying what to fix first.
- fixes: one entry per real problem in FACTS: {file, field, kind, suggestion}.
  kind is "fix_data" (the data is wrong), "change_contract" (the contract is
  too strict) or "investigate". Use only files and fields from FACTS.
  Each violation lists the contract_rule it broke; quote those limits exactly.
  A file with contract "none" has no rules yet: suggest "change_contract"
  (review the drafted contract), never "fix_data".
  Use an empty list only if every file passed.

Name the file, field and count. No greetings. JSON only."""

JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["green", "yellow", "red"]},
        "line1": {"type": "string", "minLength": 10, "maxLength": 150},
        "line2": {"type": "string", "minLength": 10, "maxLength": 150},
        "fixes": {"type": "array", "items": {"type": "object", "properties": {
            "file": {"type": "string"}, "field": {"type": "string"},
            "kind": {"type": "string", "enum": ["fix_data", "change_contract", "investigate"]},
            "suggestion": {"type": "string", "maxLength": 220}},
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
    dropped_fixes: List[Dict[str, str]] = field(default_factory=list)  # quarantined, ungrounded fixes
    raw: List[str] = field(default_factory=list)  # every raw model answer, for the audit trail

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()


def build_facts(reports: List[FileReport]) -> Dict[str, Any]:
    files = []
    for r in by_severity(reports):
        f = r.facts()
        f["status"] = r.status
        files.append(f)
    return {
        "worst_status": worst_status(reports),
        "worst_file": by_severity(reports)[0].file if reports else None,
        "files_with_failures": [r.file for r in reports if r.failed or r.error],
        "files": files,
    }


_RANK = {"red": 0, "yellow": 1, "green": 2}


def by_severity(reports: List[FileReport]) -> List[FileReport]:
    return sorted(reports, key=lambda r: (_RANK[r.status], -r.failed, r.file))


def worst_status(reports: List[FileReport]) -> str:
    st = {r.status for r in reports}
    return "red" if "red" in st else ("yellow" if "yellow" in st else "green")


def _grounding(data: Dict[str, Any], reports: List[FileReport]) -> List[str]:
    """Shape is AISchemaGate's job; these checks tie the answer to the facts.
    Small models invent files, echo the instructions, or call a red night green."""
    known = {r.file: {f for (f, _k) in r.violations} for r in reports}
    problems = []
    want = worst_status(reports)
    if data.get("status") != want:
        problems.append(f"status is '{data.get('status')}' but the worst file status in FACTS is '{want}'")
    for key in ("line1", "line2"):
        if "characters" in data.get(key, "") or "one sentence" in data.get(key, "").lower():
            problems.append(f"{key} repeats the instructions instead of describing the data")
    worst = by_severity(reports)[0] if reports else None
    if worst and worst.status != "green":
        both = f"{data.get('line1', '')} {data.get('line2', '')}"
        if worst.file not in both and worst.dataset not in both:
            problems.append(f"line1/line2 must name the worst file, {worst.file}")
    has_failures = any(r.failed or r.error for r in reports)
    if has_failures and not data.get("fixes"):
        problems.append("fixes is empty but FACTS has failing rows")
    # Any number the model writes must already appear somewhere in FACTS.
    allowed = {0, 1, 2} | {int(n) for n in re.findall(r"\d+", json.dumps(build_facts(reports)))}
    texts = {"line1": data.get("line1", ""), "line2": data.get("line2", "")}
    texts.update({f"fixes[{i}].suggestion": fx.get("suggestion", "")
                  for i, fx in enumerate(data.get("fixes") or []) if isinstance(fx, dict)})
    for key, text in texts.items():
        text = str(text)
        for r in reports:
            text = text.replace(r.file, "")
        text = re.sub(r"\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}", "", text)
        made_up = sorted({int(n) for n in re.findall(r"\b\d+\b", text)} - allowed)
        if made_up:
            problems.append(f"{key} mentions {made_up}, which do not appear anywhere in FACTS")
    for i, fx in enumerate(data.get("fixes") or []):
        if fx.get("file") in known and not known[fx["file"]] and fx.get("kind") == "fix_data":
            problems.append(f"fixes[{i}]: {fx['file']} has no failing rows, so there is no data to fix")
        if fx.get("file") not in known:
            problems.append(f"fixes[{i}].file '{fx.get('file')}' is not one of the files in FACTS")
        elif known[fx["file"]] and fx.get("field") not in known[fx["file"]] and fx.get("kind") != "investigate":
            problems.append(f"fixes[{i}].field '{fx.get('field')}' has no violations in {fx['file']}")
    return problems


def fallback(reports: List[FileReport]) -> Summary:
    bad = [r for r in by_severity(reports) if r.status != "green"]
    worst = worst_status(reports)
    total_failed = sum(r.failed for r in reports)
    line1 = (f"{len(reports)} drop(s) checked overnight; {total_failed} rows failed their contracts."
             if total_failed else f"{len(reports)} drop(s) checked overnight; every row passed.")
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
    if all(r.status == "green" for r in reports):
        # A quiet night doesn't need a model. The laziest correct answer wins.
        s = fallback(reports)
        s.source = "quiet-night"
        return s
    contract = _summary_contract()
    facts = build_facts(reports)
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": "FACTS:" + json.dumps(facts, indent=1)},
    ]
    rejections: List[List[str]] = []
    raws: List[str] = []
    for attempt in range(1, max_retries + 2):
        with tracing.span("gen_ai.chat", f"chat {llm.cfg.model}",
                          **{"gen_ai.request.model": llm.cfg.model, "attempt": attempt}):
            try:
                # Nudge the temperature up on retries so a stuck model can get unstuck.
                raw = llm.chat(messages, json_schema=JSON_SCHEMA,
                               temperature=llm.cfg.temperature + 0.3 * (attempt - 1))
            except Exception as e:
                rejections.append([f"model call failed: {e}"])
                break
        raws.append(raw if isinstance(raw, str) else json.dumps(raw))
        with tracing.span("gen_ai.execute_tool", "aischemagate.validate"):
            try:
                data = extract_json(raw)
                problems = [f"field '{v.field}': {v.message}" for v in contract.validate(data).violations]
            except LLMResponseError as e:
                data, problems = {}, [f"response was not a JSON object: {e}"]
            problems += _grounding(data, reports) if not problems else []
        if not problems:
            return Summary(data["status"], data["line1"], data["line2"], data["fixes"],
                           "model", attempt, rejections, raw=raws)
        # If only individual fixes are wrong, quarantine those fixes and keep
        # the rest, the same way DataContractGate quarantines bad rows.
        bad_idx = {int(m.group(1)) for p in problems if (m := re.match(r"fixes\[(\d+)\]", p))}
        if bad_idx and all(p.startswith("fixes[") for p in problems):
            kept = [fx for i, fx in enumerate(data["fixes"]) if i not in bad_idx]
            if kept:
                return Summary(data["status"], data["line1"], data["line2"], kept, "model",
                               attempt, rejections + [problems],
                               dropped_fixes=[data["fixes"][i] for i in sorted(bad_idx)], raw=raws)
        rejections.append(problems)
        messages = messages + [
            {"role": "assistant", "content": raw if isinstance(raw, str) else json.dumps(data)},
            {"role": "user", "content": "AISchemaGate rejected that JSON:\n- " + "\n- ".join(problems)
             + "\nReturn ONLY the corrected JSON object."},
        ]
    fb = fallback(reports)
    fb.attempts = len(rejections)
    fb.rejections = rejections
    fb.raw = raws
    return fb
