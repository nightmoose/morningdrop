"""Draft a DataContractGate contract from a sample when a dataset has none.

DataContractGate's hosted service has a richer inferrer; this is a small,
offline one so a brand-new dataset gets a contract proposal the first
night it appears. The draft is compiled with DataContractGate before it is
written, so it is always a valid contract. It is a *proposal*: MorningDrop
never enforces an inferred contract until a human moves it into contracts/.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

import yaml
from contractgate import Contract

from .drops import _coerce

ENUM_MAX = 8


def _guess_type(values: List[Any]) -> str:
    kinds = set()
    for v in values:
        if isinstance(v, bool):
            kinds.add("boolean")
        elif isinstance(v, int):
            kinds.add("integer")
        elif isinstance(v, float):
            kinds.add("float")
        elif isinstance(v, str):
            for t in ("integer", "float", "boolean"):
                if _coerce(v, t) is not v:
                    kinds.add(t)
                    break
            else:
                kinds.add("string")
        else:
            kinds.add("any")
    if kinds <= {"integer"}:
        return "integer"
    if kinds <= {"integer", "float"}:
        return "float"
    if kinds == {"boolean"}:
        return "boolean"
    return "string" if kinds <= {"string", "integer", "float"} else "any"


def infer_contract(name: str, records: List[Dict[str, Any]]) -> Dict[str, Any]:
    fields: Dict[str, List[Any]] = {}
    for r in records:
        for k, v in r.items():
            if v not in ("", None):
                fields.setdefault(k, []).append(v)
    n = len(records)
    entities = []
    for k, vals in fields.items():
        t = _guess_type(vals)
        ent: Dict[str, Any] = {"name": k, "type": t, "required": len(vals) == n}
        if t in ("integer", "float"):
            nums = [_coerce(v, t) for v in vals]
            ent["min"], ent["max"] = min(nums), max(nums)
            if t == "integer":
                ent["min"], ent["max"] = int(ent["min"]), int(ent["max"])
        elif t == "string":
            distinct = sorted({str(v) for v in vals})
            if all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(v)) for v in vals):
                ent["pattern"] = r"^\d{4}-\d{2}-\d{2}$"
            elif len(distinct) <= ENUM_MAX and n >= 2 * len(distinct):
                ent["enum"] = distinct
        entities.append(ent)
    contract = {
        "version": "1.0",
        "name": name,
        "description": f"Inferred by MorningDrop from {n} sample rows. Review before enforcing.",
        "ontology": {"entities": entities},
    }
    Contract.from_yaml(yaml.safe_dump(contract, sort_keys=False)).compile()  # must be valid
    return contract
