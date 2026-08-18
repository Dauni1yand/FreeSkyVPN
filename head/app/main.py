import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routers import connect, health, nodes, pushes, sni, subscriptions, users
from app.config import get_settings
from app.services.scheduler import sni_maintenance_loop

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Runs SNI pool upkeep in the background — see app/services/scheduler.py."""
    task = None
    if get_settings().sni_maintenance_enabled:
        task = asyncio.create_task(sni_maintenance_loop())
        logger.info("SNI maintenance loop started")
    try:
        yield
    finally:
        if task is not None:
            task.cancel()


app = FastAPI(title="FreeSkyVPN Head", lifespan=lifespan)

# /health stays unauthenticated so load balancers and uptime checks can use it;
# every other router requires the service token (see app/api/auth.py).
app.include_router(health.router)
app.include_router(users.router)
app.include_router(nodes.router)
app.include_router(connect.router)
app.include_router(subscriptions.router)
app.include_router(pushes.router)
app.include_router(sni.router)
