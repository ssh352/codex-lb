from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _run_node(script: str) -> subprocess.CompletedProcess[str]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for state_defaults.js unit tests")
    assert node is not None
    return subprocess.run(
        [node, "-e", script],
        check=False,
        text=True,
        capture_output=True,
        cwd=Path(__file__).resolve().parents[2],
    )


def test_default_accounts_state_sorts_by_email() -> None:
    proc = _run_node(
        """
const { createDefaultAccountsState } = require('./app/static/state_defaults.js');
process.stdout.write(JSON.stringify(createDefaultAccountsState()));
""".strip()
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["sortKey"] == "quotaResetSecondary"
    assert payload["sortDirection"] == "asc"
