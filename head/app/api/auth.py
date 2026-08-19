"""Two secrets, because they protect two different things.

**The service token** (`X-Service-Token`, `HEAD_SECRET_KEY`) ships inside
the Android APK. Anyone who unzips one has it. That is not a flaw to fix —
there is no way to put a secret in an installable file and keep it — it is a
fact to design around. So it is treated as what it actually is: a weak
signal that a request came from our client rather than from a scanner
sweeping the internet. It guards nothing on its own.

**The admin token** (`X-Admin-Token`, `ADMIN_API_TOKEN`) never leaves the
server. The bot and the provisioning scripts run there and can hold a real
secret; the app cannot. Everything that is not the app's own surface sits
behind it.

The distinction was not there at first, and the consequence was a cluster of
holes that all had the same shape. With the APK's token alone one could:
grant oneself unlimited access through `/admin/grant-access`; redeem an ad
token through `/ad/verify` without watching anything; register a rogue node
into the pool and be handed every user's traffic; read `/pushes/pending` and
collect Telegram ids alongside working `vless://` links; and restart the
whole fleet through `/xray-updates/decide`.

What the app is allowed to reach with the service token alone is therefore
deliberately small: register a device, the `/me/*` endpoints scoped by its
own bearer token, and the routing policy. Nothing there acts on anyone
else's behalf.
"""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from app.config import get_settings


def _compare(supplied: str | None, expected: str, *, what: str) -> None:
    if not expected or expected == "change-me":
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{what} is unset or left at its default; refusing to serve",
        )
    # Constant time, so the value cannot be recovered by measuring replies.
    if supplied is None or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"invalid {what}")


def require_service_token(x_service_token: Annotated[str | None, Header()] = None) -> None:
    """Says "this is our client build". Guards the app's own surface only."""
    _compare(x_service_token, get_settings().head_secret_key, what="service token")


def require_admin_token(x_admin_token: Annotated[str | None, Header()] = None) -> None:
    """Says "this call came from our own server".

    Required by everything that acts on another user's behalf, changes the
    fleet, or reads across accounts. The app never sends it and must never
    be given it — a secret compiled into a downloadable file is public.
    """
    _compare(x_admin_token, get_settings().admin_api_token, what="admin token")


ServiceAuth = Depends(require_service_token)
AdminAuth = Depends(require_admin_token)
