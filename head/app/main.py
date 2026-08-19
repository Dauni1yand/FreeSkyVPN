import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse

from app.admin.deps import NotLoggedIn
from app.admin.router import router as admin_router
from app.api.routers import (
    connect,
    health,
    me,
    nodes,
    pushes,
    sni,
    users,
    xray_updates,
)
from app.config import get_settings
from app.services.scheduler import (
    access_expiry_loop,
    sni_maintenance_loop,
    tier_reconciliation_loop,
    xray_update_apply_loop,
    xray_update_check_loop,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Background upkeep: SNI freshness, placement, expiry, Xray updates.

    All of them live in app/services/scheduler.py.

    The apply loop starts even when update checking is switched off: it
    exists to act on approvals, and approvals can still arrive from the
    admin panel for proposals raised earlier.
    """
    tasks = []
    settings = get_settings()
    if settings.background_jobs_enabled:
        if settings.sni_maintenance_enabled:
            tasks.append(asyncio.create_task(sni_maintenance_loop()))
            logger.info("SNI maintenance loop started")
        tasks.append(asyncio.create_task(tier_reconciliation_loop()))
        logger.info("tier reconciliation loop started")
        # The one loop that actually ends sessions. Without it a single
        # watched ad buys an unlimited tunnel.
        tasks.append(asyncio.create_task(access_expiry_loop()))
        logger.info("access expiry loop started")
        if settings.xray_update_check_enabled:
            tasks.append(asyncio.create_task(xray_update_check_loop()))
            logger.info("Xray update check loop started")
        tasks.append(asyncio.create_task(xray_update_apply_loop()))
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()


app = FastAPI(title="FreeSkyVPN Head", lifespan=lifespan)

# /health stays unauthenticated so load balancers and uptime checks can use it;
# every other router requires the service token (see app/api/auth.py).
app.include_router(health.router)
app.include_router(users.router)
app.include_router(me.router)
app.include_router(me.admin_router)
app.include_router(nodes.router)
app.include_router(connect.router)
app.include_router(pushes.router)
app.include_router(sni.router)
app.include_router(xray_updates.router)
app.include_router(admin_router)


@app.exception_handler(NotLoggedIn)
async def _redirect_to_login(_request: Request, _exc: NotLoggedIn) -> RedirectResponse:
    return RedirectResponse("/admin/login", status_code=303)
