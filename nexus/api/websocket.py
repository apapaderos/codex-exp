"""
WebSocket endpoint for live audio streaming.

URL: ws://host/sessions/{session_id}/stream

Protocol:
  Client → Server (binary frames): raw PCM audio chunks (16-bit, 16kHz, mono)
  Client → Server (text frames):   JSON control messages
  Server → Client (text frames):   JSON events (transcript, state updates, alerts)

Control messages from client:
  {"type": "ping"}
  {"type": "set_speaker", "name": "..."}

Events pushed to client:
  {"type": "transcript_interim", "text": "...", "speaker": "..."}
  {"type": "transcript_final",   "text": "...", "speaker": "...", "sequence": N}
  {"type": "assumption_captured", "assumption": {...}}
  {"type": "tension_captured",    "tension": {...}}
  {"type": "key_moment_flagged",  "moment": {...}, "alert": true}
  {"type": "energy_updated",      "level": "HIGH|MEDIUM|LOW"}
  {"type": "error",               "message": "..."}
  {"type": "pong"}
"""
from __future__ import annotations

import asyncio
import json
from uuid import UUID

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from fastapi.websockets import WebSocketState

from nexus.db.database import AsyncSessionLocal
from nexus.modes.capture import run_capture_loop
from nexus.services import state_manager
from nexus.services.speech import SpeechSession

log = structlog.get_logger(__name__)

router = APIRouter(tags=["websocket"])


@router.websocket("/sessions/{session_id}/stream")
async def audio_stream(websocket: WebSocket, session_id: UUID):
    """
    Main session WebSocket.

    Auth: The token is expected as a query parameter `?token=<jwt>` because
    browser WebSocket API does not support custom headers.
    We validate it on connect.
    """
    # ── Auth ─────────────────────────────────────────────────────────────────
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    from nexus.auth import get_current_user
    from fastapi.security import HTTPAuthorizationCredentials
    try:
        user = await get_current_user(
            HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        )
    except Exception:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # ── Session check ─────────────────────────────────────────────────────────
    if state_manager.get_state(session_id) is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    log.info("websocket_connected", session_id=str(session_id), user=user.oid)

    speech = SpeechSession(str(session_id))
    broadcast: asyncio.Queue = asyncio.Queue()

    # ── Launch capture loop as concurrent task ────────────────────────────────
    capture_task = asyncio.create_task(
        run_capture_loop(session_id, speech, broadcast),
        name=f"capture-{session_id}",
    )

    # ── Broadcast task — drains the queue and pushes events to client ─────────
    async def send_broadcasts():
        while True:
            event = await broadcast.get()
            if event is None:
                break
            if websocket.client_state == WebSocketState.CONNECTED:
                try:
                    await websocket.send_json(event)
                except Exception:
                    break

    broadcast_task = asyncio.create_task(send_broadcasts(), name=f"broadcast-{session_id}")

    try:
        while True:
            message = await websocket.receive()

            if message["type"] == "websocket.disconnect":
                break

            elif message.get("bytes"):
                # Raw audio chunk → feed to Azure Speech
                speech.feed(message["bytes"])

            elif message.get("text"):
                # Control message
                try:
                    ctrl = json.loads(message["text"])
                except json.JSONDecodeError:
                    continue

                if ctrl.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})

                elif ctrl.get("type") == "set_speaker":
                    # Speaker diarization hint — noted for future transcript segments
                    pass

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.error("websocket_error", error=str(exc), session_id=str(session_id))
    finally:
        # Drain remaining audio and close Speech session
        await speech.close()

        # Signal broadcast task to finish
        await broadcast.put(None)
        broadcast_task.cancel()
        capture_task.cancel()

        # Persist final state
        async with AsyncSessionLocal() as db:
            if state_manager.get_state(session_id):
                await state_manager.persist_state(session_id, db)
                await db.commit()

        log.info("websocket_closed", session_id=str(session_id))
