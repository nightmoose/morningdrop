"""One overnight pass: find new drops -> DataContractGate -> Gemma -> AISchemaGate -> report."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from . import tracing
from .drops import find_new, load_records
from .gate import FileReport, Gatekeeper
from .infer import infer_contract
from .llm import LLM, LLMConfig
from .summarize import Summary, by_severity, summarize


@dataclass
class Config:
    watch: Path = Path("drops")
    contracts: Path = Path("contracts")
    reports: Path = Path("reports")
    state: Path = Path(".morningdrop/state.json")
    llm: LLMConfig = field(default_factory=LLMConfig)
    max_retries: int = 2

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Config":
        raw: Dict[str, Any] = {}
        if path and Path(path).exists():
            raw = yaml.safe_load(Path(path).read_text()) or {}
        base = Path(path).resolve().parent if path else Path.cwd()
        llm_raw = dict(raw.pop("llm", {}) or {})
        env = {"backend": "MORNINGDROP_LLM_BACKEND", "base_url": "MORNINGDROP_LLM_URL",
               "model": "MORNINGDROP_LLM_MODEL", "api_key": "MORNINGDROP_LLM_API_KEY"}
        for k, var in env.items():
            if os.environ.get(var):
                llm_raw[k] = os.environ[var]
        cfg = cls(llm=LLMConfig(**llm_raw), max_retries=int(raw.pop("max_retries", 2)))
        for k in ("watch", "contracts", "reports", "state"):
            p = Path(raw.get(k, getattr(cfg, k)))
            setattr(cfg, k, p if p.is_absolute() else base / p)
        return cfg


@dataclass
class RunResult:
    reports: List[FileReport]
    summary: Optional[Summary]
    report_path: Optional[Path]
    seconds: float


def _load_state(p: Path) -> Dict[str, Any]:
    return json.loads(p.read_text()) if p.exists() else {"seen": {}}


def run_once(cfg: Config, llm: Optional[LLM] = None, now: Optional[datetime] = None) -> RunResult:
    t0 = time.monotonic()
    now = now or datetime.now()
    llm = llm or LLM(cfg.llm)
    state = _load_state(cfg.state)
    with tracing.transaction("morningdrop.run"):
        drops = find_new(cfg.watch, state["seen"].keys())
        if not drops:
            return RunResult([], None, None, time.monotonic() - t0)
        gate = Gatekeeper(cfg.contracts)
        reports: List[FileReport] = []
        for d in drops:
            with tracing.span("gen_ai.execute_tool", "datacontractgate.validate", file=d.path.name):
                rep = gate.check(d)
            if rep.contract is None and not rep.error:
                draft_dir = cfg.contracts / "_proposed"
                draft_dir.mkdir(parents=True, exist_ok=True)
                draft = draft_dir / f"{d.dataset}.yaml"
                draft.write_text(yaml.safe_dump(infer_contract(d.dataset, load_records(d.path)),
                                                sort_keys=False))
                rep.inferred_contract = str(draft.relative_to(cfg.contracts.parent))
            reports.append(rep)
        summary = summarize(reports, llm, cfg.max_retries)
        path = write_report(cfg, reports, summary, now, model=cfg.llm.model if cfg.llm.backend != "mock" else "mock model")
        for d in drops:
            state["seen"][d.sha256] = {"file": d.path.name, "at": now.isoformat(timespec="seconds")}
        cfg.state.parent.mkdir(parents=True, exist_ok=True)
        cfg.state.write_text(json.dumps(state, indent=1))
    return RunResult(reports, summary, path, time.monotonic() - t0)


ICON = {"green": "🟢", "yellow": "🟡", "red": "🔴"}


def render_markdown(reports: List[FileReport], s: Summary, now: datetime, model: str) -> str:
    out = [f"# Morning report, {now:%a %b %d %Y}", "",
           f"{ICON[s.status]} **{s.line1}**", f"{s.line2}", ""]
    if s.fixes:
        out += ["## Suggested fixes", ""]
        out += [f"- `{f['file']}` · `{f['field']}` · *{f['kind']}*: {f['suggestion']}" for f in s.fixes]
        out.append("")
    out += ["## Drops", "", "| File | Contract | Rows | Passed | Failed | Status |",
            "|---|---|---:|---:|---:|---|"]
    for r in by_severity(reports):
        out.append(f"| `{r.file}` | {r.contract or '—'} | {r.rows} | {r.passed} | {r.failed} | {ICON[r.status]} |")
    out.append("")
    for r in by_severity(reports):
        if r.violations or r.error or r.inferred_contract:
            out += [f"### {r.file}", ""]
            if r.error:
                out.append(f"- Error: {r.error}")
            if r.inferred_contract:
                out.append(f"- No contract yet. Drafted one at `{r.inferred_contract}`; review it and move it into `contracts/` to start enforcing.")
            for (f, k), c in r.violations.most_common():
                out.append(f"- `{f}` {k}: {c} row(s) (e.g. {r.examples[f'{f}|{k}'][0]})")
            out.append("")
    if s.dropped_fixes:
        out += [f"<sub>AISchemaGate quarantined {len(s.dropped_fixes)} ungrounded suggestion(s) from the model; see the JSON report.</sub>", ""]
    how = (f"written by `{model}` on attempt {s.attempts}, checked by AISchemaGate"
           if s.source == "model" else
           "skipped: every drop passed, so the model was not woken up" if s.source == "quiet-night" else
           f"template fallback (model output rejected {len(s.rejections)}x by AISchemaGate)")
    out.append(f"<sub>Summary {how}. Rows validated locally with DataContractGate. Nothing left this machine.</sub>")
    return "\n".join(out) + "\n"


def write_report(cfg: Config, reports: List[FileReport], s: Summary, now: datetime, model: str) -> Path:
    cfg.reports.mkdir(parents=True, exist_ok=True)
    md = cfg.reports / f"{now:%Y-%m-%d}.md"
    md.write_text(render_markdown(reports, s, now, model), encoding="utf-8")
    (cfg.reports / f"{now:%Y-%m-%d}.json").write_text(json.dumps({
        "summary": s.to_dict(),
        "files": [dict(r.facts(top=50), status=r.status, inferred_contract=r.inferred_contract)
                  for r in reports],
    }, indent=1))
    (cfg.reports / "latest.txt").write_text(f"{ICON[s.status]} {s.line1}\n{s.line2}\n", encoding="utf-8")
    return md
