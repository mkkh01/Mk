"""Authentication dependencies for protected operational APIs."""
from __future__ import annotations

import hmac
import os

from fastapi import HTTPException, Request, status


def require_dashboard_auth(request: Request) -> None:
    """Require a constant-time validated bearer/API token for operational APIs.

    The token is intentionally read at request time so deployments can rotate
    it without importing application modules with stale configuration.
    """
    expected = os.getenv("DASHBOARD_API_TOKEN", "").strip()
    supplied = request.headers.get("Authorization", "")
    if supplied.lower().startswith("bearer "):
        supplied = supplied[7:].strip()
    else:
        supplied = request.headers.get("X-API-Key", "").strip()

    if not expected or not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )


__all__ = ["require_dashboard_auth"]
