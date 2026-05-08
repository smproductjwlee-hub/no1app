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
