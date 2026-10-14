"""FastAPI routers, one per area of the API.

backend.py mounts every router in ALL_ROUTERS. Registration order only
matters for overlapping paths, and no two modules here share a path prefix.
"""
from routers import auth
from routers import realtime
from routers import ai_core
from routers import hardware
from routers import cameras
from routers import incidents
from routers import records
from routers import admin
from routers import report_requests
from routers import devteam_jurisdictions
from routers import devteam_applications
from routers import devteam_users
from routers import devteam_oversight
from routers import devteam_models

ALL_ROUTERS = [
    auth.router,
    realtime.router,
    ai_core.router,
    hardware.router,
    cameras.router,
    incidents.router,
    records.router,
    admin.router,
    report_requests.router,
    devteam_jurisdictions.router,
    devteam_applications.router,
    devteam_users.router,
    devteam_oversight.router,
    devteam_models.router,
]
