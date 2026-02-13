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
        pytest.skip("node is required for selection_utils.js unit tests")
    return subprocess.run(
        [node, "-e", script],
        check=False,
        text=True,
        capture_output=True,
        cwd=Path(__file__).resolve().parents[2],
    )


def test_selection_utils_shift_range_selects_contiguous_ids() -> None:
    proc = _run_node(
        """
const { nextSelection } = require('./app/static/selection_utils.js');
const orderedIds = ['acc1','acc2','acc3','acc4','acc5'];
let selectedIds = [];
let anchorId = '';
({ selectedIds, anchorId } = nextSelection({ orderedIds, clickedId: 'acc2', selectedIds, anchorId }));
({ selectedIds, anchorId } = nextSelection({ orderedIds, clickedId: 'acc4', selectedIds, anchorId, shift: true }));
process.stdout.write(JSON.stringify({ selectedIds, anchorId }));
""".strip()
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["selectedIds"] == ["acc2", "acc3", "acc4"]
    assert payload["anchorId"] == "acc2"


def test_selection_utils_shift_works_with_anchor_even_if_nothing_selected() -> None:
    proc = _run_node(
        """
const { nextSelection } = require('./app/static/selection_utils.js');
const orderedIds = ['a','b','c','d','e'];
let selectedIds = [];
let anchorId = 'b';
({ selectedIds, anchorId } = nextSelection({ orderedIds, clickedId: 'd', selectedIds, anchorId, shift: true }));
process.stdout.write(JSON.stringify({ selectedIds, anchorId }));
""".strip()
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["selectedIds"] == ["b", "c", "d"]
    assert payload["anchorId"] == "b"


def test_selection_utils_ctrl_toggle_keeps_existing_selection() -> None:
    proc = _run_node(
        """
const { nextSelection } = require('./app/static/selection_utils.js');
const orderedIds = ['a','b','c','d'];
let selectedIds = [];
let anchorId = '';
({ selectedIds, anchorId } = nextSelection({ orderedIds, clickedId: 'a', selectedIds, anchorId }));
({ selectedIds, anchorId } = nextSelection({ orderedIds, clickedId: 'c', selectedIds, anchorId, ctrl: true }));
({ selectedIds, anchorId } = nextSelection({ orderedIds, clickedId: 'a', selectedIds, anchorId, ctrl: true }));
process.stdout.write(JSON.stringify({ selectedIds, anchorId }));
""".strip()
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["selectedIds"] == ["c"]
    assert payload["anchorId"] == "a"


def test_selection_utils_reconcile_does_not_reselect_on_refresh() -> None:
    proc = _run_node(
        """
const Selection = require('./app/static/selection_utils.js');
const { reconcileSelection } = Selection;
const existingIds = ['acc1','acc2','acc3'];
const selectedIds = [];
const anchorId = '';
const result = reconcileSelection({ existingIds, selectedIds, anchorId });
process.stdout.write(JSON.stringify(result));
""".strip()
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["selectedIds"] == []
    assert payload["anchorId"] == ""


def test_selection_utils_shift_then_reconcile_then_shift_again() -> None:
    proc = _run_node(
        """
const Selection = require('./app/static/selection_utils.js');
const { nextSelection, reconcileSelection } = Selection;

const orderedIds = ['a','b','c','d','e'];
let selectedIds = [];
let anchorId = '';

({ selectedIds, anchorId } = nextSelection({ orderedIds, clickedId: 'b', selectedIds, anchorId }));
({ selectedIds, anchorId } = nextSelection({ orderedIds, clickedId: 'd', selectedIds, anchorId, shift: true }));

// Simulate refresh removing 'c'
({ selectedIds, anchorId } = reconcileSelection({ existingIds: ['a','b','d','e'], selectedIds, anchorId }));

({ selectedIds, anchorId } = nextSelection({
  orderedIds: ['a','b','d','e'],
  clickedId: 'e',
  selectedIds,
  anchorId,
  shift: true,
}));

process.stdout.write(JSON.stringify({ selectedIds, anchorId }));
""".strip()
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["selectedIds"] == ["b", "d", "e"]
    assert payload["anchorId"] == "b"
