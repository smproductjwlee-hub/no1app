from __future__ import annotations

import json
import time
from typing import Any, Optional

from fastapi import APIRouter, WebSocket, status
from starlette.websockets import WebSocketDisconnect

from app.services.instruction_history import create_round, record_reply
from app.services.staff_accounts import staff_accounts
from app.services.stores import Role, sessions
from app.ws.manager import manager

router = APIRouter(tags=["realtime"])


def _worker_tokens_in_group(workspace_id: str, group_id: str) -> set[str]:
    """グループに属する登録アカウントのうち、現在オンラインのワーカー接続トークン。"""
    ids = set(staff_accounts.list_account_ids_in_group(workspace_id, group_id))
    if not ids:
        return set()
    out: set[str] = set()
    for client_token, _ws in manager.iter_connections(workspace_id):
        if not client_token:
            continue
        s = sessions.get(client_token)
        if s is None or s.role != Role.WORKER:
            continue
        if s.staff_account_id and s.staff_account_id in ids:
            out.add(client_token)
    return out


def _count_workers_online(workspace_id: str) -> int:
    n = 0
    for sess_token, _ws in manager.iter_connections(workspace_id):
        if not sess_token:
            continue
        s = sessions.get(sess_token)
        if s and s.role == Role.WORKER:
            n += 1
    return n


def _all_worker_session_tokens(workspace_id: str) -> set[str]:
    out: set[str] = set()
    for sess_token, _ws in manager.iter_connections(workspace_id):
        if not sess_token:
            continue
        s = sessions.get(sess_token)
        if s and s.role == Role.WORKER:
            out.add(sess_token)
    return out


def _recipients_payload(workspace_id: str, tokens: set[str]) -> list[dict]:
    rows: list[dict] = []
    for tok in tokens:
        s = sessions.get(tok)
        if s is None or s.role != Role.WORKER:
            continue
        rows.append(
            {
                "token": tok,
                "label": (s.user_label or "").strip() or "?",
                "staff_account_id": s.staff_account_id,
            },
        )
    return rows


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
                workers_online = _count_workers_online(workspace_id)
                raw_group = payload.get("target_group_id")
                targets = payload.get("target_tokens")
                delivered = 0
                mode_str = "broadcast"
                token_set: set[str] = set()
                target_gid: Optional[str] = None
                if isinstance(raw_group, str) and raw_group.strip():
                    target_gid = raw_group.strip()
                    token_set = _worker_tokens_in_group(workspace_id, target_gid)
                    mode_str = "group"
                elif isinstance(targets, list) and len(targets) > 0:
                    token_set = {t for t in targets if isinstance(t, str) and t.strip()}
                    if not token_set:
                        mode_str = "broadcast"
                        token_set = _all_worker_session_tokens(workspace_id)
                    else:
                        mode_str = "targeted"
                else:
                    token_set = _all_worker_session_tokens(workspace_id)
                    mode_str = "broadcast"

                rec_payload = _recipients_payload(workspace_id, token_set)
                instruction_id = create_round(
                    workspace_id,
                    body,
                    mode_str,
                    rec_payload,
                    target_group_id=target_gid,
                )
                msg = {"type": "instruction", "text": body, "ts": ts, "instruction_id": instruction_id}
                if mode_str == "group":
                    if not token_set:
                        delivered = 0
                    else:
                        delivered = await manager.send_json_to_tokens(workspace_id, msg, token_set)
                elif mode_str == "targeted":
                    delivered = await manager.send_json_to_tokens(workspace_id, msg, token_set)
                else:
                    delivered = await manager.broadcast_json_to_workers(workspace_id, msg)

                warning: Optional[str] = None
                if workers_online == 0:
                    warning = "no_workers_online"
                elif delivered == 0:
                    warning = (
                        "no_matching_recipients"
                        if mode_str in ("targeted", "group")
                        else "delivery_failed"
                    )

                await websocket.send_json(
                    {
                        "type": "instruction_sent",
                        "instruction_id": instruction_id,
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
                raw_iid = payload.get("instruction_id")
                iid: Optional[str] = None
                if isinstance(raw_iid, str) and raw_iid.strip():
                    iid = raw_iid.strip()
                    record_reply(
                        workspace_id,
                        iid,
                        sess.token,
                        sess.user_label or "?",
                        sess.staff_account_id,
                        btn,
                    )
                wlab = (sess.user_label or "").strip() or "?"
                out: dict[str, Any] = {
                    "type": "worker_response",
                    "button": btn,
                    "ts": time.time(),
                    "worker_label": wlab,
                }
                if iid:
                    out["instruction_id"] = iid
                if sess.staff_account_id:
                    out["staff_account_id"] = sess.staff_account_id
                await manager.broadcast_json(workspace_id, out)
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
