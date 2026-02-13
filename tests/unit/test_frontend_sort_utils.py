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
        pytest.skip("node is required for sort_utils.js unit tests")
    return subprocess.run(
        [node, "-e", script],
        check=False,
        text=True,
        capture_output=True,
        cwd=Path(__file__).resolve().parents[2],
    )


def test_sort_utils_quota_reset_then_remaining_asc() -> None:
    proc = _run_node(
        """
const { sortAccounts } = require('./app/static/sort_utils.js');
const accounts = [
  {
    id: 'a',
    email: 'a@example.com',
    usage: { secondaryRemainingPercent: 10 },
    resetAtSecondary: '2026-01-01T00:00:00Z',
  },
  {
    id: 'b',
    email: 'b@example.com',
    usage: { secondaryRemainingPercent: 5 },
    resetAtSecondary: '2026-01-01T00:00:00Z',
  },
  {
    id: 'c',
    email: 'c@example.com',
    usage: { secondaryRemainingPercent: 1 },
    resetAtSecondary: '2025-12-31T00:00:00Z',
  },
];
const sorted = sortAccounts(accounts, { sortKey: 'quotaResetSecondary', sortDirection: 'asc' });
process.stdout.write(JSON.stringify(sorted.map(a => a.id)));
""".strip()
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    # earliest reset first: c (2025-12-31), then a/b (2026-01-01),
    # tie breaks by remaining asc: b (5) then a (10)
    assert payload == ["c", "b", "a"]
