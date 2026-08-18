"""Shared plumbing for the admin panel: templating, auth guard, flashes."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated
from urllib.parse import quote, unquote

from fastapi import Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.services import crypto
from app.services.admin_auth import SESSION_COOKIE, read_session

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

FLASH_COOKIE = "freesky_flash"


class NotLoggedIn(Exception):
    """Raised by the guard; turned into a redirect by the exception handler."""


def current_admin(request: Request) -> str:
    token = request.cookies.get(SESSION_COOKIE)
    username = read_session(token) if token else None
    if username is None:
        raise NotLoggedIn
    return username


CurrentAdmin = Annotated[str, Depends(current_admin)]


def render(request: Request, template: str, admin: str | None, **context):
    """Render a page with the chrome every admin page needs.

    Flash messages travel in a cookie rather than server-side session state:
    every admin action is a POST that redirects, and a cookie survives that
    redirect without the head having to keep any per-user memory.
    """
    flash = request.cookies.get(FLASH_COOKIE)
    messages = []
    if flash:
        kind, _, text = unquote(flash).partition("|")
        messages.append((kind, text))

    response = templates.TemplateResponse(
        request,
        template,
        {
            "admin": admin,
            "messages": messages,
            "secrets_ok": crypto.is_configured(),
            **context,
        },
    )
    if flash:
        response.delete_cookie(FLASH_COOKIE)
    return response


def redirect_with_flash(url: str, kind: str, message: str) -> RedirectResponse:
    response = RedirectResponse(url, status_code=303)
    # Percent-encoded because these messages are in Russian and HTTP headers
    # are latin-1: a Cyrillic cookie value raises on the way out.
    # Trimmed first: browsers cap a cookie around 4 KB, and a long
    # provisioning traceback would otherwise silently drop the whole message.
    value = quote(f"{kind}|{message[:1200]}")
    response.set_cookie(FLASH_COOKIE, value, httponly=True, samesite="lax")
    return response
