from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Final
from urllib.parse import SplitResult, urlsplit, urlunsplit

from app.core.config.settings import BASE_DIR, Settings, get_settings

logger = logging.getLogger(__name__)

_PROXY_ENV_KEYS: Final[tuple[str, ...]] = ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY")
_REDACT_VALUE: Final[str] = "***"
_PROXY_USERINFO_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)?(?P<userinfo>[^@/]+)@(?P<rest>.*)$"
)


@dataclass(frozen=True, slots=True)
class StartupEnvSnapshot:
    values: dict[str, str | None]

    @classmethod
    def from_process_env(cls) -> StartupEnvSnapshot:
        values: dict[str, str | None] = {}
        for key in _PROXY_ENV_KEYS:
            values[key] = os.environ.get(key) or os.environ.get(key.lower())
        for key, value in os.environ.items():
            if key.startswith("CODEX_LB_"):
                values[key] = value
        return cls(values=values)


def log_startup_config() -> None:
    settings = get_settings()
    if not settings.startup_log_config and not settings.startup_log_env:
        return

    env_files = (BASE_DIR / ".env", BASE_DIR / ".env.local")
    env_file_status = ", ".join(f"{path.name}={'present' if path.exists() else 'missing'}" for path in env_files)
    logger.info("Startup config: env_files=[%s]", env_file_status)

    if settings.startup_log_env:
        snapshot = StartupEnvSnapshot.from_process_env()
        _log_env_snapshot(snapshot)

    if settings.startup_log_config:
        _log_settings(settings)


def _log_env_snapshot(snapshot: StartupEnvSnapshot) -> None:
    items = sorted(snapshot.values.items(), key=lambda kv: kv[0])
    logger.info("Startup env snapshot (allowlist):")
    for key, value in items:
        if value is None:
            logger.info("  %s=<unset>", key)
            continue
        if key.startswith("CODEX_LB_"):
            logger.info("  %s=%s", key, _redact_generic_env_value(key, value))
            continue
        if key in _PROXY_ENV_KEYS:
            logger.info("  %s=%s", key, _redact_proxy_url(value))
            continue
        logger.info("  %s=%s", key, value)


def _log_settings(settings: Settings) -> None:
    # `mode="json"` converts Path -> str and other non-JSON types.
    data = settings.model_dump(mode="json")
    items = sorted(data.items(), key=lambda kv: kv[0])
    logger.info("Startup settings snapshot:")
    for key, value in items:
        logger.info("  %s=%s", key, _redact_setting_value(key, value))


def _redact_generic_env_value(key: str, value: str) -> str:
    upper = key.upper()
    if any(token in upper for token in ("ACCESS_TOKEN", "REFRESH_TOKEN", "ID_TOKEN", "PASSWORD", "SECRET", "COOKIE")):
        return _REDACT_VALUE
    if "DATABASE_URL" in upper:
        return _REDACT_VALUE
    if upper.endswith("_KEY") and not upper.endswith("_KEY_FILE"):
        return _REDACT_VALUE
    return value


def _redact_setting_value(key: str, value: object) -> object:
    upper = key.upper()
    if any(token in upper for token in ("DATABASE_URL",)):
        return _REDACT_VALUE
    return value


def _redact_proxy_url(value: str) -> str:
    # Common formats:
    # - http://user:pass@host:port
    # - user:pass@host:port (scheme omitted)
    # - http://host:port
    # - socks5://user:pass@host:port
    if not value:
        return value

    # First, handle the scheme-omitted variant which urlsplit() treats as a path (netloc="").
    match = _PROXY_USERINFO_RE.match(value)
    if match:
        scheme = match.group("scheme") or ""
        userinfo = match.group("userinfo")
        rest = match.group("rest")
        redacted_userinfo = f"{_REDACT_VALUE}:{_REDACT_VALUE}" if ":" in userinfo else _REDACT_VALUE
        return f"{scheme}{redacted_userinfo}@{rest}"

    try:
        split = urlsplit(value)
    except Exception:
        return _REDACT_VALUE

    if not split.netloc:
        return value

    if "@" not in split.netloc:
        return value

    userinfo, hostport = split.netloc.rsplit("@", 1)
    if ":" in userinfo:
        redacted_userinfo = f"{_REDACT_VALUE}:{_REDACT_VALUE}"
    else:
        redacted_userinfo = _REDACT_VALUE

    redacted = SplitResult(
        scheme=split.scheme,
        netloc=f"{redacted_userinfo}@{hostport}",
        path=split.path,
        query=split.query,
        fragment=split.fragment,
    )
    return urlunsplit(redacted)
