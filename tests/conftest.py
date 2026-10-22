import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def workdir(tmp_path):
    shutil.copytree(ROOT / "contracts", tmp_path / "contracts",
                    ignore=shutil.ignore_patterns("_proposed"))
    shutil.copytree(ROOT / "samples" / "drops", tmp_path / "drops")
    (tmp_path / "morningdrop.yaml").write_text(
        "watch: drops\ncontracts: contracts\nreports: reports\nstate: .state/state.json\n"
        "llm:\n  backend: mock\n  model: mock\n")
    return tmp_path
