from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Optional

from fastapi import WebSocket

from app.services.stores import Role, sessions


class ConnectionManager:
    """Workspace-scoped WebSocket: (session_token, socket) tuples. token None = admin."""

    def __init__(self) -> None:
        self._rooms: dict[str, list[tuple[Optional[str], WebSocket]]] = defaultdict(list)

    async def connect(
        self, workspace_id: str, websocket: WebSocket, session_token: Optional[str]
    ) -> None:
        await websocket.accept()
        self._rooms[workspace_id].append((session_token, websocket))

    def disconnect(self, workspace_id: str, websocket: WebSocket) -> None:
        conns = self._rooms.get(workspace_id)
        if not conns:
            return
        self._rooms[workspace_id] = [(t, w) for (t, w) in conns if w is not websocket]
        if not self._rooms[workspace_id]:
            self._rooms.pop(workspace_id, None)

    async def broadcast_json(self, workspace_id: str, message: dict[str, Any]) -> int:
        """Fan-out to all sockets in the room. Returns count of successful sends."""
        raw = json.dumps(message, ensure_ascii=False)
        dead: list[WebSocket] = []
        sent = 0
        for _, ws in list(self._rooms.get(workspace_id, [])):
            try:
                await ws.send_text(raw)
                sent += 1
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(workspace_id, ws)
        return sent

    async def send_json_to_tokens(
        self,
        workspace_id: str,
        message: dict[str, Any],
        session_tokens: set[str],
    ) -> int:
        """Send only to sockets whose session token is in the set. Returns send count."""
        raw = json.dumps(message, ensure_ascii=False)
        dead: list[WebSocket] = []
        sent = 0
        for sess_token, ws in list(self._rooms.get(workspace_id, [])):
            if sess_token is None:
                continue
            if sess_token not in session_tokens:
                continue
            try:
                await ws.send_text(raw)
                sent += 1
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(workspace_id, ws)
        return sent

    async def broadcast_json_to_workers(
        self, workspace_id: str, message: dict[str, Any]
    ) -> int:
        """指示など: 管理者ソケットは除き、WORKER のみへ送る。"""
        raw = json.dumps(message, ensure_ascii=False)
        dead: list[WebSocket] = []
        sent = 0
        for sess_token, ws in list(self._rooms.get(workspace_id, [])):
            if not sess_token:
                continue
            s = sessions.get(sess_token)
            if s is None or s.role != Role.WORKER:
                continue
            try:
                await ws.send_text(raw)
                sent += 1
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(workspace_id, ws)
        return sent

    async def broadcast_json_to_admins(
        self, workspace_id: str, message: dict[str, Any]
    ) -> int:
        """スタッフ→管理者メッセージなど: ADMIN ロールの接続のみ。"""
        raw = json.dumps(message, ensure_ascii=False)
        dead: list[WebSocket] = []
        sent = 0
        for sess_token, ws in list(self._rooms.get(workspace_id, [])):
            if not sess_token:
                continue
            s = sessions.get(sess_token)
            if s is None or s.role != Role.ADMIN:
                continue
            try:
                await ws.send_text(raw)
                sent += 1
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(workspace_id, ws)
        return sent

    def iter_connections(self, workspace_id: str) -> list[tuple[Optional[str], WebSocket]]:
        return list(self._rooms.get(workspace_id, []))


manager = ConnectionManager()
