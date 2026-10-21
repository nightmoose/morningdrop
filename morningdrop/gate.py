"""Validate drops with DataContractGate's local (offline) validator."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from contractgate import Contract

from .drops import Drop, coerce_csv_record, field_types, load_records


@dataclass
class FileReport:
    file: str
    dataset: str
    contract: Optional[str]
    rows: int = 0
    passed: int = 0
    failed: int = 0
    # (field, kind) -> count, plus a couple of example messages per pair
    violations: Counter = field(default_factory=Counter)
    examples: Dict[str, List[str]] = field(default_factory=dict)
    error: Optional[str] = None
    inferred_contract: Optional[str] = None

    @property
    def status(self) -> str:
        if self.error or (self.rows and self.failed / self.rows > 0.05):
            return "red"
        if self.failed or self.contract is None:
            return "yellow"
        return "green"

    def facts(self, top: int = 5) -> Dict[str, Any]:
        """Compact, model-friendly view. No raw rows go to the model."""
        return {
            "file": self.file,
            "dataset": self.dataset,
            "contract": self.contract or "none (draft inferred)",
            "rows": self.rows,
            "passed": self.passed,
            "failed": self.failed,
            "error": self.error,
            "top_violations": [
                {
                    "field": f,
                    "kind": k,
                    "count": c,
                    "example": (self.examples.get(f"{f}|{k}") or [""])[0],
                }
                for (f, k), c in self.violations.most_common(top)
            ],
        }


class Gatekeeper:
    def __init__(self, contracts_dir: Path):
        self.contracts_dir = Path(contracts_dir)
        self.raw: Dict[str, Dict[str, Any]] = {}
        self._compiled: Dict[str, Any] = {}
        for p in sorted(self.contracts_dir.glob("*.yaml")):
            text = p.read_text(encoding="utf-8")
            data = yaml.safe_load(text)
            self.raw[data["name"]] = data
            self._compiled[data["name"]] = Contract.from_yaml(text).compile()

    def check(self, drop: Drop) -> FileReport:
        rep = FileReport(file=drop.path.name, dataset=drop.dataset,
                         contract=drop.dataset if drop.dataset in self._compiled else None)
        try:
            records = load_records(drop.path)
        except Exception as e:  # unreadable drop is itself the headline
            rep.error = f"could not parse file: {e}"
            return rep
        rep.rows = len(records)
        if rep.contract is None:
            return rep
        compiled = self._compiled[rep.contract]
        types = field_types(self.raw[rep.contract])
        is_csv = drop.path.suffix.lower() == ".csv"
        for rec in records:
            if is_csv:
                rec = coerce_csv_record(rec, types)
            vr = compiled.validate(rec)
            if vr.passed:
                rep.passed += 1
                continue
            rep.failed += 1
            for v in vr.violations:
                key = (v.field, v.kind.value)
                rep.violations[key] += 1
                ex = rep.examples.setdefault(f"{v.field}|{v.kind.value}", [])
                if len(ex) < 2:
                    ex.append(v.message)
        return rep
