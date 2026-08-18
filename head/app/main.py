from fastapi import FastAPI

from app.api.routers import connect, health, nodes, pushes, subscriptions, users

app = FastAPI(title="FreeSkyVPN Head")

# /health stays unauthenticated so load balancers and uptime checks can use it;
# every other router requires the service token (see app/api/auth.py).
app.include_router(health.router)
app.include_router(users.router)
app.include_router(nodes.router)
app.include_router(connect.router)
app.include_router(subscriptions.router)
app.include_router(pushes.router)
