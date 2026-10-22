"""Real Gemma via Ollama. Skipped unless MORNINGDROP_LIVE=1.

    MORNINGDROP_LIVE=1 MORNINGDROP_LLM_MODEL=gemma3:4b pytest tests/test_live_gemma.py -s
"""
import os

import pytest

from morningdrop.agent import Config, run_once

pytestmark = pytest.mark.skipif(os.environ.get("MORNINGDROP_LIVE") != "1", reason="live model test")


def test_live_run(workdir):
    (workdir / "morningdrop.yaml").write_text(
        "watch: drops\ncontracts: contracts\nreports: reports\nstate: .state/s.json\n"
        f"llm:\n  backend: ollama\n  model: {os.environ.get('MORNINGDROP_LLM_MODEL', 'gemma3:4b')}\n")
    res = run_once(Config.load(workdir / "morningdrop.yaml"))
    print(res.summary.line1, res.summary.line2, res.summary.source, res.summary.rejections, sep="\n")
    assert res.summary.status == "red"  # holds for the model *and* the fallback: never a wrong status
