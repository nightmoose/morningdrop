from morningdrop.drops import Drop, coerce_csv_record, find_new, sha256
from morningdrop.gate import Gatekeeper


def test_dataset_name_strips_date(tmp_path):
    p = tmp_path / "orders_2026-10-24.csv"
    p.write_text("a\n1\n")
    assert Drop(p, "x").dataset == "orders"
    assert Drop(tmp_path / "customers-20261024.jsonl", "x").dataset == "customers"


def test_csv_coercion_keeps_bad_values_raw():
    rec = coerce_csv_record({"q": "3", "p": "9.5", "b": "true", "bad": "three", "empty": ""},
                            {"q": "integer", "p": "float", "b": "boolean", "bad": "integer"})
    assert rec == {"q": 3, "p": 9.5, "b": True, "bad": "three"}


def test_sample_orders_violations(workdir):
    gate = Gatekeeper(workdir / "contracts")
    p = workdir / "drops" / "orders_2026-10-24.csv"
    rep = gate.check(Drop(p, sha256(p)))
    assert (rep.rows, rep.failed) == (240, 63)
    assert rep.violations[("currency", "enum_violation")] == 60
    assert rep.violations[("quantity", "range_violation")] == 2
    assert rep.violations[("customer_id", "missing_required_field")] == 1
    assert rep.status == "red"
    assert rep.rules["quantity"]["min"] == 1


def test_unknown_dataset_has_no_contract(workdir):
    gate = Gatekeeper(workdir / "contracts")
    p = workdir / "drops" / "shipments_2026-10-24.csv"
    rep = gate.check(Drop(p, sha256(p)))
    assert rep.contract is None and rep.status == "yellow"


def test_unparseable_file_is_red(workdir):
    p = workdir / "drops" / "orders_broken.json"
    p.write_text("{not json")
    rep = Gatekeeper(workdir / "contracts").check(Drop(p, sha256(p)))
    assert rep.error and rep.status == "red"


def test_find_new_skips_seen(workdir):
    drops = find_new(workdir / "drops", [])
    assert len(drops) == 3
    assert find_new(workdir / "drops", [d.sha256 for d in drops]) == []
