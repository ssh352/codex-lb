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
        pytest.skip("node is required for ui_utils.js unit tests")
    return subprocess.run(
        [node, "-e", script],
        check=False,
        text=True,
        capture_output=True,
        cwd=Path(__file__).resolve().parents[2],
    )


def test_format_account_id_short_truncates_long_values() -> None:
    proc = _run_node(
        """
const { formatAccountIdShort } = require('./app/static/ui_utils.js');
process.stdout.write(JSON.stringify({
  empty: formatAccountIdShort(''),
  short: formatAccountIdShort('acc_short'),
  long: formatAccountIdShort('acc_1234567890abcdef'),
}));
""".strip()
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["empty"] == ""
    assert payload["short"] == "acc"
    assert payload["long"] == "acc"
