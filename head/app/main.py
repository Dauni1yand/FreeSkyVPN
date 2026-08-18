from fastapi import FastAPI

from app.api.routers import health, nodes, users

app = FastAPI(title="FreeSkyVPN Head")

app.include_router(health.router)
app.include_router(users.router)
app.include_router(nodes.router)
