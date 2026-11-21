"""Per-request / per-session TrainingPeaks cookie for HTTP MCP.

Pacific (and other HTTP clients) send the athlete cookie as
``X-TP-Auth-Cookie``. We bind it to a contextvar for the current request and
to the MCP session id so later posts in the same session reuse it.
"""

from __future__ import annotations

from contextvars import ContextVar

COOKIE_HEADER = "x-tp-auth-cookie"
# Also accept the env-var style name some clients send literally.
COOKIE_HEADER_ALT = "tp-auth-cookie"
SESSION_HEADER = "mcp-session-id"

_request_cookie: ContextVar[str | None] = ContextVar(
    "tp_request_cookie", default=None
)
_session_cookies: dict[str, str] = {}


def get_request_cookie() -> str | None:
    value = _request_cookie.get()
    if value and value.strip():
        return value.strip()
    return None


def set_request_cookie(cookie: str | None):
    return _request_cookie.set(cookie.strip() if cookie else None)


def reset_request_cookie(token) -> None:
    _request_cookie.reset(token)


def remember_session_cookie(session_id: str, cookie: str) -> None:
    cleaned = session_id.strip()
    value = cookie.strip()
    if cleaned and value:
        _session_cookies[cleaned] = value


def cookie_for_session(session_id: str | None) -> str | None:
    if not session_id:
        return None
    return _session_cookies.get(session_id.strip())


def forget_session_cookie(session_id: str) -> None:
    _session_cookies.pop(session_id.strip(), None)
