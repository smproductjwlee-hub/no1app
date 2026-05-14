from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Optional, TypeVar

from fastapi import Depends, HTTPException, Query, status

from app.services.stores import Role, Session, sessions


T = TypeVar("T")


async def run_db(fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """同期 SQLite 呼び出しをスレッドプールで実行。
    sqlite3 は async 非対応で、ハンドラから直接呼ぶとイベントループを止める。
    asyncio.to_thread はデフォルトの ThreadPoolExecutor で実行され、
    FastAPI の startup でプールサイズを 64 まで広げてある（concurrency_setup.py）。
    """
    return await asyncio.to_thread(fn, *args, **kwargs)


async def get_session_token(
    token: Optional[str] = Query(None, description="Session token from QR login"),
) -> str:
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing session token",
        )
    return token


async def get_current_session(
    token: str = Depends(get_session_token),
) -> Session:
    sess = sessions.get(token)
    if sess is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
        )
    return sess


async def require_worker(sess: Session = Depends(get_current_session)) -> Session:
    if sess.role != Role.WORKER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Worker role required")
    return sess


async def require_admin(sess: Session = Depends(get_current_session)) -> Session:
    if sess.role != Role.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return sess


# ============================================================
# Phase 2.3 — 3계층 멀티테넌시용 권한 dependencies
# ============================================================


async def require_super_admin(sess: Session = Depends(get_current_session)) -> Session:
    if sess.role != Role.SUPER_ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin required")
    return sess


async def require_distributor_admin(sess: Session = Depends(get_current_session)) -> Session:
    """대리점 관리자만 통과. distributor_id 가 비어 있으면 403."""
    if sess.role != Role.DISTRIBUTOR_ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Distributor admin required")
    if not sess.distributor_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Distributor not assigned")
    return sess


async def require_super_or_distributor_admin(sess: Session = Depends(get_current_session)) -> Session:
    """슈퍼·대리점 어느 쪽이든 통과 (목록 조회 등 공유 엔드포인트용)."""
    if sess.role not in (Role.SUPER_ADMIN, Role.DISTRIBUTOR_ADMIN):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin or distributor required")
    return sess


def can_access_workspace(sess: Session, workspace_distributor_id: str) -> bool:
    """워크스페이스에 대한 접근 권한 판정.
    - super_admin: 항상 가능
    - distributor_admin: 자기 산하 워크스페이스만
    - admin/worker: 자기 워크스페이스만 (sess.workspace_id 로 별도 검증 필요)
    """
    if sess.role == Role.SUPER_ADMIN:
        return True
    if sess.role == Role.DISTRIBUTOR_ADMIN:
        return bool(sess.distributor_id and sess.distributor_id == workspace_distributor_id)
    return False
