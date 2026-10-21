"""Find new data drops and load them as records.

A "drop" is any .csv, .json or .jsonl file in the watched folder. A file is
new if its content hash has not been seen before, so re-runs are idempotent
and a vendor re-sending the same file does not wake anyone up.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

SUPPORTED = {".csv", ".json", ".jsonl", ".ndjson"}
_DATE_SUFFIX = re.compile(r"[_\-.]?(\d{4}-?\d{2}-?\d{2})(T\d+)?$")


@dataclass
class Drop:
    path: Path
    sha256: str

    @property
    def dataset(self) -> str:
        """`orders_2026-10-24.csv` -> `orders`."""
        stem = self.path.stem
        return _DATE_SUFFIX.sub("", stem) or stem


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def find_new(folder: Path, seen: Iterable[str]) -> List[Drop]:
    seen = set(seen)
    out = []
    for p in sorted(Path(folder).iterdir()):
        if p.is_file() and p.suffix.lower() in SUPPORTED and not p.name.startswith("."):
            digest = sha256(p)
            if digest not in seen:
                out.append(Drop(p, digest))
    return out


def load_records(path: Path) -> List[Dict[str, Any]]:
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8-sig")
    if suffix == ".csv":
        return [dict(r) for r in csv.DictReader(text.splitlines())]
    if suffix in (".jsonl", ".ndjson"):
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    data = json.loads(text)
    if isinstance(data, dict):
        data = data.get("records") or data.get("data") or [data]
    return list(data)


def _coerce(value: Any, ftype: str) -> Any:
    """CSV gives us strings. Coerce toward the contract's declared type, but
    leave the raw string in place when it doesn't parse, so DataContractGate
    reports an honest type_mismatch instead of us hiding the problem."""
    if not isinstance(value, str):
        return value
    v = value.strip()
    if ftype == "integer":
        if re.fullmatch(r"[-+]?\d+", v):
            return int(v)
    elif ftype in ("float", "number"):
        try:
            return float(v)
        except ValueError:
            pass
    elif ftype == "boolean":
        if v.lower() in ("true", "1", "yes"):
            return True
        if v.lower() in ("false", "0", "no"):
            return False
    return value


def coerce_csv_record(rec: Dict[str, Any], types: Dict[str, str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in rec.items():
        if v == "" or v is None:
            continue  # empty CSV cell == missing field
        out[k] = _coerce(v, types.get(k, "string"))
    return out


def field_types(contract_raw: Optional[Dict[str, Any]]) -> Dict[str, str]:
    if not contract_raw:
        return {}
    ents = (contract_raw.get("ontology") or {}).get("entities") or []
    return {e["name"]: str(e.get("type", "string")) for e in ents}
