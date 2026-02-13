check: lint type fe-check test

test:
    uv run --group dev pytest

# Short aliases.
t: test
ci: check

lint:
    uv run --group dev ruff check .

type:
    uv run --group dev pyright

fe-syntax:
    sh -c 'command -v node >/dev/null 2>&1 || { echo "node not found; skipping fe-syntax"; exit 0; }; node --check app/static/index.js; node --check app/static/selection_utils.js; node --check app/static/sort_utils.js; node --check app/static/state_defaults.js; node --check app/static/ui_utils.js'

fe-assets:
    uv run --group dev python scripts/check_frontend_assets.py

fe-unit:
    uv run --group dev pytest tests/unit/test_frontend_selection_utils.py tests/unit/test_frontend_sort_utils.py tests/unit/test_frontend_state_defaults.py tests/unit/test_frontend_ui_utils.py

fe-check: fe-syntax fe-assets
