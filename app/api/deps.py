from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, Query, status

from app.services.stores import Role, Session, sessions


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
