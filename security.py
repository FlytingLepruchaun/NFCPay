"""Small CSRF protections for browser-authenticated form submissions."""

import hmac
import secrets

from flask import abort, current_app, request, session


def csrf_token() -> str:
    """Return the session-bound token rendered into browser forms."""
    token = session.get("_csrf_token")
    if token is None:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def validate_csrf_request() -> None:
    """Reject unsafe browser requests that do not present the session token."""
    if (
        not current_app.config.get("CSRF_ENABLED", True)
        or request.method in {"GET", "HEAD", "OPTIONS", "TRACE"}
        or request.blueprint == "api"
    ):
        return

    expected = session.get("_csrf_token")
    supplied = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
    if not isinstance(expected, str) or not isinstance(supplied, str):
        abort(400, description="A valid CSRF token is required.")
    if not hmac.compare_digest(expected, supplied):
        abort(400, description="A valid CSRF token is required.")
