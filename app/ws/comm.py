from __future__ import annotations

import json
import time
from typing import Any, Optional

from fastapi import APIRouter, WebSocket, status
from starlette.websockets import WebSocketDisconnect

from app.services.stores import Role, sessions
from app.ws.manager import manager

router = APIRouter(tags=["realtime"])


def _count_workers_online(workspace_id: str) -> int:
    n = 0
    for sess_token, _ws in manager.iter_connections(workspace_id):
        if not sess_token:
            continue
        s = sessions.get(sess_token)
        if s and s.role == Role.WORKER:
            n += 1
    return n


@router.websocket("/ws")
async def comm_websocket(websocket: WebSocket) -> None:
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    sess = sessions.get(token)
    if sess is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    if sess.role == Role.SUPER_ADMIN:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    workspace_id = sess.workspace_id
    await manager.connect(workspace_id, websocket, sess.token)
    try:
        await manager.broadcast_json(
            workspace_id,
            {
                "type": "presence",
                "event": "join",
                "workspace_id": workspace_id,
                "role": sess.role.value,
            },
        )
        while True:
            text = await websocket.receive_text()
            try:
                payload: dict[str, Any] = json.loads(text)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "detail": "invalid_json"})
                continue

            msg_type = payload.get("type")
            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
                continue

            if msg_type == "instruction":
                if sess.role != Role.ADMIN:
                    await websocket.send_json({"type": "error", "detail": "admin_only"})
                    continue
                raw = payload.get("text", "")
                body = raw if isinstance(raw, str) else str(raw)
                ts = time.time()
                msg = {"type": "instruction", "text": body, "ts": ts}
                workers_online = _count_workers_online(workspace_id)
                targets = payload.get("target_tokens")
                delivered = 0
                mode_str = "broadcast"
                if isinstance(targets, list) and len(targets) > 0:
                    token_set = {t for t in targets if isinstance(t, str) and t.strip()}
                    if not token_set:
                        delivered = await manager.broadcast_json_to_workers(workspace_id, msg)
                        mode_str = "broadcast"
                    else:
                        delivered = await manager.send_json_to_tokens(workspace_id, msg, token_set)
                        mode_str = "targeted"
                else:
                    delivered = await manager.broadcast_json_to_workers(workspace_id, msg)
                    mode_str = "broadcast"

                warning: Optional[str] = None
                if workers_online == 0:
                    warning = "no_workers_online"
                elif delivered == 0:
                    warning = "no_matching_recipients" if mode_str == "targeted" else "delivery_failed"

                await websocket.send_json(
                    {
                        "type": "instruction_sent",
                        "delivered": delivered,
                        "workers_online": workers_online,
                        "mode": mode_str,
                        "warning": warning,
                    }
                )
                continue

            if msg_type == "worker_response":
                if sess.role != Role.WORKER:
                    await websocket.send_json({"type": "error", "detail": "worker_only"})
                    continue
                btn = payload.get("button", "")
                if btn not in ("OK", "REPEAT", "NG", "CUSTOM"):
                    await websocket.send_json({"type": "error", "detail": "invalid_button"})
                    continue
                await manager.broadcast_json(
                    workspace_id,
                    {
                        "type": "worker_response",
                        "button": btn,
                        "ts": time.time(),
                    },
                )
                continue

            await websocket.send_json(
                {"type": "error", "detail": "unknown_type", "allowed": ["ping", "instruction", "worker_response"]},
            )
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(workspace_id, websocket)
        await manager.broadcast_json(
            workspace_id,
            {
                "type": "presence",
                "event": "leave",
                "workspace_id": workspace_id,
                "role": sess.role.value,
            },
        )
