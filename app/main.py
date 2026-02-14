from __future__ import annotations

from contextlib import asynccontextmanager
from hashlib import sha256
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

from app.core.clients.http import close_http_client, init_http_client
from app.core.config.startup_log import log_startup_config
from app.core.handlers import add_exception_handlers
from app.core.middleware import (
    add_api_unhandled_error_middleware,
    add_request_decompression_middleware,
    add_request_id_middleware,
)
from app.core.request_logs.flush_scheduler import build_request_logs_flush_scheduler
from app.core.usage.refresh_scheduler import build_usage_refresh_scheduler
from app.db.session import close_db, init_db
from app.modules.accounts import api as accounts_api
from app.modules.dashboard import api as dashboard_api
from app.modules.health import api as health_api
from app.modules.oauth import api as oauth_api
from app.modules.proxy import api as proxy_api
from app.modules.request_logs import api as request_logs_api
from app.modules.settings import api as settings_api
from app.modules.usage import api as usage_api


def _compute_dashboard_asset_version(static_dir: Path) -> str:
    asset_names = (
        "index.css",
        "selection_utils.js",
        "ui_utils.js",
        "state_defaults.js",
        "sort_utils.js",
        "index.js",
    )
    digest = sha256()
    for name in asset_names:
        digest.update((static_dir / name).read_bytes())
    return digest.hexdigest()[:12]


def _render_dashboard_index(index_html: Path, asset_version: str) -> str:
    html = index_html.read_text(encoding="utf-8")
    return html.replace("__ASSET_VERSION__", asset_version)


class DashboardStaticFiles(StaticFiles):
    def __init__(self, directory: Path, *, asset_version: str, html: bool = True) -> None:
        super().__init__(directory=str(directory), html=html)
        self._asset_version = asset_version
        self._static_dir = directory

    async def get_response(self, path: str, scope) -> Response:
        if path in {"", ".", "index.html"}:
            index_html = self._static_dir / "index.html"
            rendered = _render_dashboard_index(index_html, self._asset_version)
            response = Response(rendered, media_type="text/html")
            response.headers["Cache-Control"] = "no-cache"
            return response
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


@asynccontextmanager
async def lifespan(_: FastAPI):
    log_startup_config()
    await init_db()
    await init_http_client()
    scheduler = build_usage_refresh_scheduler()
    request_logs_flusher = build_request_logs_flush_scheduler()
    await scheduler.start()
    await request_logs_flusher.start()

    try:
        yield
    finally:
        await request_logs_flusher.stop()
        await scheduler.stop()
        try:
            await close_http_client()
        finally:
            await close_db()


def create_app() -> FastAPI:
    app = FastAPI(title="codex-lb", version="0.1.0", lifespan=lifespan)

    add_request_decompression_middleware(app)
    add_request_id_middleware(app)
    add_api_unhandled_error_middleware(app)
    add_exception_handlers(app)

    app.include_router(proxy_api.router)
    app.include_router(proxy_api.v1_router)
    app.include_router(proxy_api.usage_router)
    app.include_router(accounts_api.router)
    app.include_router(dashboard_api.router)
    app.include_router(usage_api.router)
    app.include_router(request_logs_api.router)
    app.include_router(oauth_api.router)
    app.include_router(settings_api.router)
    app.include_router(health_api.router)

    static_dir = Path(__file__).parent / "static"
    index_html = static_dir / "index.html"
    asset_version = _compute_dashboard_asset_version(static_dir)

    def _dashboard_index_response() -> Response:
        rendered = _render_dashboard_index(index_html, asset_version)
        response = Response(rendered, media_type="text/html")
        response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/", include_in_schema=False)
    async def root_redirect():
        return RedirectResponse(url="/dashboard", status_code=302)

    @app.get("/accounts", include_in_schema=False)
    async def spa_accounts():
        return _dashboard_index_response()

    @app.get("/settings", include_in_schema=False)
    async def spa_settings():
        return _dashboard_index_response()

    app.mount(
        "/dashboard",
        DashboardStaticFiles(directory=static_dir, html=True, asset_version=asset_version),
        name="dashboard",
    )

    return app


app = create_app()
