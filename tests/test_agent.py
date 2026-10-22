import shutil
from datetime import datetime
from pathlib import Path

from morningdrop.agent import Config, run_once
from morningdrop.cli import main

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 25, 6, 0)


def test_overnight_run_end_to_end(workdir):
    cfg = Config.load(workdir / "morningdrop.yaml")
    res = run_once(cfg, now=NOW)
    md = (workdir / "reports" / "2026-10-25.md").read_text()
    assert res.summary.source == "model"
    assert "orders_2026-10-24.csv" in md and "🔴" in md
    assert (workdir / "contracts" / "_proposed" / "shipments.yaml").exists()
    assert len((workdir / "reports" / "latest.txt").read_text().splitlines()) == 2
    # second run, same files: nothing to do
    assert run_once(cfg, now=NOW).summary is None
    # next night: a clean drop
    shutil.copy(ROOT / "samples" / "drops_day2" / "orders_2026-10-25.csv", workdir / "drops")
    res = run_once(cfg, now=datetime(2026, 10, 26, 6, 0))
    assert res.summary.status == "green" and len(res.reports) == 1
    assert res.summary.source == "quiet-night"


def test_env_overrides_backend(workdir, monkeypatch):
    monkeypatch.setenv("MORNINGDROP_LLM_MODEL", "gemma3:1b")
    assert Config.load(workdir / "morningdrop.yaml").llm.model == "gemma3:1b"


def test_cli_run(workdir, capsys):
    assert main(["-c", str(workdir / "morningdrop.yaml"), "run"]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 2


def test_cli_infer(workdir, capsys):
    assert main(["infer", str(workdir / "drops" / "shipments_2026-10-24.csv"), "--name", "shipments"]) == 0
    assert "name: shipments" in capsys.readouterr().out
