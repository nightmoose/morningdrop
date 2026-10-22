from contractgate import Contract
import yaml

from morningdrop.drops import load_records
from morningdrop.infer import infer_contract


def test_inferred_contract_compiles_and_accepts_its_sample(workdir):
    recs = load_records(workdir / "drops" / "shipments_2026-10-24.csv")
    c = infer_contract("shipments", recs)
    ents = {e["name"]: e for e in c["ontology"]["entities"]}
    assert ents["weight_kg"]["type"] == "float"
    assert ents["signature_required"]["type"] == "boolean"
    assert ents["carrier"]["enum"] == ["DHL", "UPS", "USPS"]
    assert ents["shipped_on"]["pattern"].startswith("^\\d{4}")
    compiled = Contract.from_yaml(yaml.safe_dump(c)).compile()
    from morningdrop.drops import coerce_csv_record, field_types
    types = field_types(c)
    assert all(compiled.validate(coerce_csv_record(r, types)).passed for r in recs)
