"""The /ws WebSocket that pushes live incident events to dashboards."""
from typing import Optional

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from backend_core import manager, require_auth

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: Optional[str] = None):
    # Browsers can't set headers on a WebSocket, so the dashboard passes its
    # session token as ?token=. Same check as every HTTP endpoint.
    try:
        payload = require_auth(f"Bearer {token}" if token else None)
    except HTTPException:
        await websocket.close(code=4401)
        return
    await manager.connect(websocket, payload)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
