from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AssetRef:
    raw: str
    asset_path: str


_DASHBOARD_ASSET_RE = re.compile(r"""(?:src|href)\s*=\s*["'](/dashboard/[^"']+)["']""")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _extract_dashboard_asset_refs(html: str) -> list[AssetRef]:
    refs: list[AssetRef] = []
    for match in _DASHBOARD_ASSET_RE.finditer(html):
        raw = match.group(1)
        if not raw:
            continue
        asset_path = raw.split("?", 1)[0].split("#", 1)[0]
        refs.append(AssetRef(raw=raw, asset_path=asset_path))
    return refs


def _resolve_static_path(asset_path: str) -> Path:
    if not asset_path.startswith("/dashboard/"):
        raise ValueError(f"Unexpected asset path: {asset_path}")
    name = asset_path.removeprefix("/dashboard/").lstrip("/")
    if not name:
        raise ValueError(f"Unexpected asset path: {asset_path}")
    return _repo_root() / "app" / "static" / name


def main() -> int:
    index_html = _repo_root() / "app" / "static" / "index.html"
    html = index_html.read_text(encoding="utf-8")
    refs = _extract_dashboard_asset_refs(html)
    missing: list[str] = []
    for ref in refs:
        path = _resolve_static_path(ref.asset_path)
        if not path.exists():
            missing.append(f"{ref.raw} -> {path.as_posix()}")
    if missing:
        details = "\n".join(missing)
        raise SystemExit(f"Missing dashboard assets referenced from index.html:\n{details}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
