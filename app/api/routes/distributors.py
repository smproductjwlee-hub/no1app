"""販売代理店 (Distributor) 関連 API — Phase 2.3.

- Super Admin: 대리점 CRUD, 도매가 조정, 상태 토글, 삭제
- Distributor Admin: 본인 정보 조회·연락처 갱신, 산하 워크스페이스 일람
"""

from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field

from app.api.deps import (
    get_current_session,
    require_distributor_admin,
    require_super_admin,
    require_super_or_distributor_admin,
    run_db,
)
from app.services.distributors import distributors as distributors_store
from app.services.stores import Session, workspaces


router = APIRouter(prefix="/distributors", tags=["distributors"])


# ============================================================
# Schemas
# ============================================================


_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{1,18}[a-z0-9]$")


class DistributorOut(BaseModel):
    id: str
    slug: str
    name: str
    contact_person: str
    contact_phone: str
    contact_email: str
    owner_email: str
    wholesale_starter: int
    wholesale_business: int
    wholesale_enterprise: int
    wholesale_mvp_fee: int
    status: str
    created_at: float
    updated_at: float


class DistributorCreateIn(BaseModel):
    slug: str = Field(..., min_length=3, max_length=20)
    name: str = Field(..., min_length=1, max_length=200)
    owner_email: str = Field("", max_length=200)
    owner_password: str = Field("", max_length=200)
    contact_person: str = Field("", max_length=100)
    contact_phone: str = Field("", max_length=50)
    contact_email: str = Field("", max_length=200)
    wholesale_starter: int = Field(8000, ge=0)
    wholesale_business: int = Field(6500, ge=0)
    wholesale_enterprise: int = Field(5000, ge=0)
    wholesale_mvp_fee: int = Field(5000000, ge=0)


class DistributorContactPatchIn(BaseModel):
    name: Optional[str] = Field(None, max_length=200)
    contact_person: Optional[str] = Field(None, max_length=100)
    contact_phone: Optional[str] = Field(None, max_length=50)
    contact_email: Optional[str] = Field(None, max_length=200)


class DistributorWholesalePatchIn(BaseModel):
    wholesale_starter: Optional[int] = Field(None, ge=0)
    wholesale_business: Optional[int] = Field(None, ge=0)
    wholesale_enterprise: Optional[int] = Field(None, ge=0)
    wholesale_mvp_fee: Optional[int] = Field(None, ge=0)


class DistributorLoginPatchIn(BaseModel):
    owner_email: Optional[str] = Field(None, max_length=200)
    new_password: Optional[str] = Field(None, min_length=4, max_length=200)


class DistributorStatusPatchIn(BaseModel):
    status: str = Field(..., pattern="^(active|suspended)$")


class DistributorDeleteResult(BaseModel):
    distributors_deleted: int
    workspaces_deleted: int
    files_deleted: int


class DistributorWorkspaceRow(BaseModel):
    id: str
    slug: str
    name: str
    company_name: str
    logo_url: Optional[str] = None
    assigned_plan: str
    retail_price_starter: Optional[int] = None
    retail_price_business: Optional[int] = None
    retail_price_enterprise: Optional[int] = None
    created_at: float


class WorkspaceCreateByDistributorIn(BaseModel):
    """대리점이 자기 산하에 신규 고객사 (워크스페이스) 추가."""
    name: str = Field(..., min_length=1, max_length=200)
    slug: str = Field(..., min_length=3, max_length=20)
    owner_password: str = Field(..., min_length=4, max_length=200)
    company_name: str = Field("", max_length=200)
    logo_url: Optional[str] = Field(None, max_length=500)
    primary_color: Optional[str] = Field(None, max_length=20)
    retail_price_starter: Optional[int] = Field(None, ge=0)
    retail_price_business: Optional[int] = Field(None, ge=0)
    retail_price_enterprise: Optional[int] = Field(None, ge=0)
    assigned_plan: str = Field("starter", pattern="^(starter|business|enterprise)$")


# ============================================================
# Helpers
# ============================================================


def _to_out(d) -> DistributorOut:
    return DistributorOut(
        id=d.id,
        slug=d.slug,
        name=d.name,
        contact_person=d.contact_person,
        contact_phone=d.contact_phone,
        contact_email=d.contact_email,
        owner_email=d.owner_email,
        wholesale_starter=d.wholesale_starter,
        wholesale_business=d.wholesale_business,
        wholesale_enterprise=d.wholesale_enterprise,
        wholesale_mvp_fee=d.wholesale_mvp_fee,
        status=d.status,
        created_at=d.created_at,
        updated_at=d.updated_at,
    )


def _to_ws_row(ws) -> DistributorWorkspaceRow:
    return DistributorWorkspaceRow(
        id=ws.id,
        slug=ws.slug or "",
        name=ws.name,
        company_name=ws.company_name or "",
        logo_url=ws.logo_url,
        assigned_plan=ws.assigned_plan or "starter",
        retail_price_starter=ws.retail_price_starter,
        retail_price_business=ws.retail_price_business,
        retail_price_enterprise=ws.retail_price_enterprise,
        created_at=ws.created_at,
    )


# ============================================================
# Super Admin endpoints
# ============================================================


@router.get("", response_model=list[DistributorOut])
async def list_distributors(
    _sess: Session = Depends(require_super_admin),
) -> list[DistributorOut]:
    """전체 대리점 일람 (Super Admin)."""
    rows = await run_db(distributors_store.list_all)
    return [_to_out(d) for d in rows]


@router.post("", response_model=DistributorOut, status_code=status.HTTP_201_CREATED)
async def create_distributor(
    body: DistributorCreateIn,
    _sess: Session = Depends(require_super_admin),
) -> DistributorOut:
    """대리점 신규 등록 (Super Admin)."""
    slug = body.slug.strip().lower()
    if not _SLUG_RE.match(slug):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="slug must be lowercase alphanumeric + hyphen, start/end with alphanumeric, 3-20 chars",
        )
    try:
        d = await run_db(
            distributors_store.create,
            slug,
            body.name,
            owner_email=body.owner_email,
            owner_password=body.owner_password,
            contact_person=body.contact_person,
            contact_phone=body.contact_phone,
            contact_email=body.contact_email,
            wholesale_starter=body.wholesale_starter,
            wholesale_business=body.wholesale_business,
            wholesale_enterprise=body.wholesale_enterprise,
            wholesale_mvp_fee=body.wholesale_mvp_fee,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    return _to_out(d)


# 주의: /me/... 라우트는 /{distributor_id}/... 보다 먼저 정의해야 함.
# 그렇지 않으면 FastAPI 가 path-param 으로 매치해버려 권한 검증이 어긋남.


@router.get("/me/self", response_model=DistributorOut)
async def get_me(
    sess: Session = Depends(require_distributor_admin),
) -> DistributorOut:
    """대리점 관리자 본인 정보."""
    d = await run_db(distributors_store.get, sess.distributor_id)
    if d is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="distributor not found")
    return _to_out(d)


@router.get("/me/workspaces", response_model=list[DistributorWorkspaceRow])
async def list_my_workspaces(
    sess: Session = Depends(require_distributor_admin),
) -> list[DistributorWorkspaceRow]:
    """대리점 관리자가 본인 산하 워크스페이스 일람."""
    rows = await run_db(workspaces.list_by_distributor, sess.distributor_id)
    return [_to_ws_row(ws) for ws in rows]


@router.post("/me/workspaces", response_model=DistributorWorkspaceRow, status_code=status.HTTP_201_CREATED)
async def create_my_workspace(
    body: WorkspaceCreateByDistributorIn,
    sess: Session = Depends(require_distributor_admin),
) -> DistributorWorkspaceRow:
    """대리점이 자기 산하에 신규 고객사 추가.

    - slug 는 본 대리점 내 unique
    - owner_password 는 즉시 hash 되어 저장 (메일 발송 없음, 테스트 환경)
    - retail_price_* 는 영업 비밀 (운영자 super admin 도 안 봄)
    """
    slug = body.slug.strip().lower()
    if not _SLUG_RE.match(slug):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="slug must be lowercase alphanumeric + hyphen, start/end with alphanumeric, 3-20 chars",
        )
    from app.services.distributors import hash_password
    try:
        ws = await run_db(
            workspaces.create,
            body.name,
            distributor_id=sess.distributor_id,
            slug=slug,
            owner_password_hash=hash_password(body.owner_password),
            company_name=body.company_name,
            logo_url=body.logo_url,
            primary_color=body.primary_color,
            retail_price_starter=body.retail_price_starter,
            retail_price_business=body.retail_price_business,
            retail_price_enterprise=body.retail_price_enterprise,
            assigned_plan=body.assigned_plan,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    return _to_ws_row(ws)


@router.get("/{distributor_id}", response_model=DistributorOut)
async def get_distributor(
    distributor_id: str,
    sess: Session = Depends(require_super_or_distributor_admin),
) -> DistributorOut:
    """대리점 단건 조회. distributor_admin 은 본인 만."""
    if sess.role.value == "distributor_admin" and sess.distributor_id != distributor_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    d = await run_db(distributors_store.get, distributor_id)
    if d is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="distributor not found")
    return _to_out(d)


@router.patch("/{distributor_id}/contact", response_model=DistributorOut)
async def patch_contact(
    distributor_id: str,
    body: DistributorContactPatchIn,
    sess: Session = Depends(require_super_or_distributor_admin),
) -> DistributorOut:
    """연락처·이름 갱신. 대리점 본인도 자기 정보 갱신 가능 (좌상단 표시용)."""
    if sess.role.value == "distributor_admin" and sess.distributor_id != distributor_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    d = await run_db(
        distributors_store.update_contact,
        distributor_id,
        name=body.name,
        contact_person=body.contact_person,
        contact_phone=body.contact_phone,
        contact_email=body.contact_email,
    )
    if d is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="distributor not found")
    return _to_out(d)


@router.patch("/{distributor_id}/wholesale", response_model=DistributorOut)
async def patch_wholesale(
    distributor_id: str,
    body: DistributorWholesalePatchIn,
    _sess: Session = Depends(require_super_admin),
) -> DistributorOut:
    """도매가 갱신 (Super Admin 전용)."""
    d = await run_db(
        distributors_store.update_wholesale,
        distributor_id,
        wholesale_starter=body.wholesale_starter,
        wholesale_business=body.wholesale_business,
        wholesale_enterprise=body.wholesale_enterprise,
        wholesale_mvp_fee=body.wholesale_mvp_fee,
    )
    if d is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="distributor not found")
    return _to_out(d)


@router.patch("/{distributor_id}/login", response_model=DistributorOut)
async def patch_login(
    distributor_id: str,
    body: DistributorLoginPatchIn,
    _sess: Session = Depends(require_super_admin),
) -> DistributorOut:
    """로그인 정보 (메일·PW) 갱신 (Super Admin 전용).

    테스트 환경: PW 재발급 흐름을 단순화. Super Admin 이 직접 새 PW 를 발급하고
    대리점에 알려주는 방식 (이메일 발송 없음).
    """
    d = await run_db(
        distributors_store.update_owner_login,
        distributor_id,
        owner_email=body.owner_email,
        new_password=body.new_password,
    )
    if d is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="distributor not found")
    return _to_out(d)


@router.patch("/{distributor_id}/status", response_model=DistributorOut)
async def patch_status(
    distributor_id: str,
    body: DistributorStatusPatchIn,
    _sess: Session = Depends(require_super_admin),
) -> DistributorOut:
    """active / suspended 토글 (Super Admin 전용)."""
    try:
        d = await run_db(distributors_store.set_status, distributor_id, body.status)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    if d is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="distributor not found")
    return _to_out(d)


@router.delete("/{distributor_id}", response_model=DistributorDeleteResult)
async def delete_distributor(
    distributor_id: str,
    confirm: str = "",
    _sess: Session = Depends(require_super_admin),
) -> DistributorDeleteResult:
    """대리점 + 산하 워크스페이스 완전 삭제 (Super Admin 전용).

    confirm=DELETE 쿼리 인자 필수 (실수 방지).
    c-direct 는 삭제 불가.
    """
    if confirm != "DELETE":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="confirm=DELETE query parameter required",
        )
    try:
        counts = await run_db(distributors_store.delete_with_cascade, distributor_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    return DistributorDeleteResult(
        distributors_deleted=int(counts.get("distributors", 0) or 0),
        workspaces_deleted=int(counts.get("workspaces", 0) or 0),
        files_deleted=int(counts.get("files_deleted", 0) or 0),
    )


@router.get("/{distributor_id}/workspaces", response_model=list[DistributorWorkspaceRow])
async def list_distributor_workspaces(
    distributor_id: str,
    sess: Session = Depends(require_super_or_distributor_admin),
) -> list[DistributorWorkspaceRow]:
    """산하 워크스페이스 일람. distributor_admin 은 본인 산하만."""
    if sess.role.value == "distributor_admin" and sess.distributor_id != distributor_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    rows = await run_db(workspaces.list_by_distributor, distributor_id)
    return [_to_ws_row(ws) for ws in rows]


# (Distributor Admin endpoints 는 위쪽 /me/self, /me/workspaces 로 통합됨)
