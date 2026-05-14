"""販売代理店 (Distributor) 関連 API — Phase 2.3.

- Super Admin: 대리점 CRUD, 도매가 조정, 상태 토글, 삭제
- Distributor Admin: 본인 정보 조회·연락처 갱신, 산하 워크스페이스 일람
"""

from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, EmailStr, Field

from app.api.deps import (
    get_current_session,
    require_distributor_admin,
    require_super_admin,
    require_super_or_distributor_admin,
    run_db,
)
from app.services.distributors import distributors as distributors_store
from app.services.staff_avatar_files import (
    delete_workspace_logo_file,
    save_workspace_logo_jpeg,
)
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
# Billing Report — 도매 모델 기반
# ============================================================


class SuperBillingDistRow(BaseModel):
    """운영자 (super_admin) 용: 대리점별 청구 합계."""
    distributor_id: str
    distributor_slug: str
    distributor_name: str
    contact_person: str
    contact_email: str
    ws_count: int                       # 산하 워크스페이스 총 수
    ws_starter: int
    ws_business: int
    ws_enterprise: int
    monthly_wholesale_total: int        # Σ(ws 도매가) — 매월 청구
    mvp_count_this_month: int            # 이번달 신규 가입 워크스페이스 수
    mvp_total: int                       # mvp_count × MVP 도매가
    grand_total: int                     # monthly + mvp


class SuperBillingReport(BaseModel):
    year: int
    month: int
    rows: list[SuperBillingDistRow]
    totals: dict[str, int]               # {"monthly_wholesale": N, "mvp": N, "grand_total": N, "ws_count": N}


class DistMarginRow(BaseModel):
    """대리점 본인 화면용: 워크스페이스별 마진."""
    ws_id: str
    slug: str
    name: str
    company_name: str
    plan: str
    wholesale: int                       # 본인이 운영자에게 지불하는 도매가
    retail: int                           # 본인이 엔드유저에게 청구한 소매가
    margin: int                           # retail - wholesale
    margin_pct: int                       # (margin / retail) × 100. retail 0 이면 0
    created_at: float


class DistMarginReport(BaseModel):
    year: int
    month: int
    distributor_id: str
    distributor_slug: str
    distributor_name: str
    rows: list[DistMarginRow]
    totals: dict[str, int]               # {"revenue": Σretail, "cost": Σwholesale, "margin": Σmargin, "ws_count": N}


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


# ============================================================
# Billing Report endpoints
# ============================================================


def _is_in_year_month(ts: float, year: int, month: int) -> bool:
    """UNIX 秒 → 해당 연·월에 속하는지. 로컬 타임존 기준."""
    import datetime as _dt
    if not ts:
        return False
    try:
        d = _dt.datetime.fromtimestamp(float(ts))
    except (TypeError, ValueError, OSError):
        return False
    return d.year == year and d.month == month


def _resolve_year_month(year: Optional[int], month: Optional[int]) -> tuple[int, int]:
    """미지정 시 현재 연·월 사용."""
    import datetime as _dt
    now = _dt.datetime.now()
    return (year or now.year, month or now.month)


def _wholesale_for_plan(d, plan: str) -> int:
    if plan == "enterprise":
        return d.wholesale_enterprise
    if plan == "business":
        return d.wholesale_business
    return d.wholesale_starter


def _retail_for_plan(ws, plan: str) -> int:
    if plan == "enterprise":
        return ws.retail_price_enterprise or 0
    if plan == "business":
        return ws.retail_price_business or 0
    return ws.retail_price_starter or 0


def _compute_super_billing(year: int, month: int):
    """운영자가 각 대리점에 청구할 합계 계산.

    - 매월 도매 합계: Σ(워크스페이스 도매가) per 대리점
    - 이번달 MVP 합계: 이번달 created_at 의 워크스페이스 수 × 대리점 MVP 도매가
    - c-direct (직판) 는 제외
    """
    distributors_list = distributors_store.list_all()
    rows: list[dict] = []
    sum_monthly = 0
    sum_mvp = 0
    sum_ws_count = 0
    for d in distributors_list:
        if d.slug == "c-direct":
            continue
        ws_list = workspaces.list_by_distributor(d.id)
        monthly_total = 0
        st = bu = en = 0
        mvp_count = 0
        for ws in ws_list:
            plan = (ws.assigned_plan or "starter").lower()
            if plan == "enterprise":
                en += 1
            elif plan == "business":
                bu += 1
            else:
                st += 1
            monthly_total += _wholesale_for_plan(d, plan)
            if _is_in_year_month(ws.created_at, year, month):
                mvp_count += 1
        mvp_total = mvp_count * (d.wholesale_mvp_fee or 0)
        grand = monthly_total + mvp_total
        rows.append({
            "distributor_id": d.id,
            "distributor_slug": d.slug,
            "distributor_name": d.name,
            "contact_person": d.contact_person,
            "contact_email": d.contact_email,
            "ws_count": len(ws_list),
            "ws_starter": st,
            "ws_business": bu,
            "ws_enterprise": en,
            "monthly_wholesale_total": int(monthly_total),
            "mvp_count_this_month": mvp_count,
            "mvp_total": int(mvp_total),
            "grand_total": int(grand),
        })
        sum_monthly += monthly_total
        sum_mvp += mvp_total
        sum_ws_count += len(ws_list)
    return {
        "rows": rows,
        "totals": {
            "monthly_wholesale": int(sum_monthly),
            "mvp": int(sum_mvp),
            "grand_total": int(sum_monthly + sum_mvp),
            "ws_count": int(sum_ws_count),
        },
    }


def _compute_my_margin(distributor_id: str, year: int, month: int):
    """대리점 본인 화면용 — 산하 ws별 매출·비용·마진."""
    d = distributors_store.get(distributor_id)
    if d is None:
        return None
    ws_list = workspaces.list_by_distributor(distributor_id)
    rows: list[dict] = []
    revenue = cost = margin = 0
    for ws in ws_list:
        plan = (ws.assigned_plan or "starter").lower()
        whp = _wholesale_for_plan(d, plan)
        rtp = _retail_for_plan(ws, plan)
        m = max(rtp - whp, 0) if rtp > 0 else 0
        pct = int(round((m / rtp) * 100)) if rtp > 0 else 0
        rows.append({
            "ws_id": ws.id,
            "slug": ws.slug or "",
            "name": ws.name,
            "company_name": ws.company_name or "",
            "plan": plan,
            "wholesale": int(whp),
            "retail": int(rtp),
            "margin": int(m),
            "margin_pct": pct,
            "created_at": ws.created_at,
        })
        revenue += rtp
        cost += whp
        margin += m
    return {
        "distributor_id": d.id,
        "distributor_slug": d.slug,
        "distributor_name": d.name,
        "rows": rows,
        "totals": {
            "revenue": int(revenue),
            "cost": int(cost),
            "margin": int(margin),
            "ws_count": len(ws_list),
        },
    }


@router.get("/billing-report", response_model=SuperBillingReport)
async def super_billing_report(
    year: Optional[int] = None,
    month: Optional[int] = None,
    _sess: Session = Depends(require_super_admin),
) -> SuperBillingReport:
    """운영자 (super_admin) 용 — 대리점별 청구 합계.

    응답: 각 대리점에 청구할 금액 (도매 ws 합계 + 이번달 MVP).
    """
    y, m = _resolve_year_month(year, month)
    data = await run_db(_compute_super_billing, y, m)
    return SuperBillingReport(year=y, month=m, rows=data["rows"], totals=data["totals"])


@router.get("/me/billing-report", response_model=DistMarginReport)
async def my_billing_report(
    year: Optional[int] = None,
    month: Optional[int] = None,
    sess: Session = Depends(require_distributor_admin),
) -> DistMarginReport:
    """대리점 본인 용 — 자기 산하 ws 별 매출·비용·마진.

    응답: 자기 영업 활동의 실시간 수익성 모니터링용.
    소매가는 자기 영업 비밀이므로 본인만 봄.
    """
    y, m = _resolve_year_month(year, month)
    data = await run_db(_compute_my_margin, sess.distributor_id, y, m)
    if data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="distributor not found")
    return DistMarginReport(year=y, month=m, **data)


# ============================================================
# Phase 2.8 — 워크스페이스 로고 업로드 (대리점이 산하 ws 브랜딩)
# ============================================================


class LogoUploadResult(BaseModel):
    workspace_id: str
    logo_url: str


def _verify_workspace_ownership(sess: Session, workspace_id: str):
    """대리점 admin 이 자기 산하 워크스페이스에 대한 접근권 보유 확인."""
    ws = workspaces.get(workspace_id)
    if ws is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="workspace not found")
    if ws.distributor_id != sess.distributor_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    return ws


@router.post("/me/workspaces/{workspace_id}/logo", response_model=LogoUploadResult)
async def upload_workspace_logo(
    workspace_id: str,
    file: UploadFile = File(...),
    sess: Session = Depends(require_distributor_admin),
) -> LogoUploadResult:
    """대리점이 자기 산하 워크스페이스의 로고 이미지를 업로드.

    - 정사각형 중앙 크롭 + 256x256 JPEG 으로 정규화
    - workspaces.logo_url 갱신 (캐시 무효화용 ?t=timestamp 동봉)
    """
    await run_db(_verify_workspace_ownership, sess, workspace_id)
    content = await file.read()
    if not content:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="empty file")
    try:
        ts = await run_db(save_workspace_logo_jpeg, workspace_id, content)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    # 캐시 무효화를 위해 timestamp 쿼리 동봉
    new_url = f"/static/uploads/workspace-logos/{workspace_id}.jpg?t={int(ts)}"
    await run_db(workspaces.set_logo_url, workspace_id, new_url)
    return LogoUploadResult(workspace_id=workspace_id, logo_url=new_url)


# ============================================================
# Phase 2.10 — 자동 플랜 업그레이드 이벤트 조회 (계약서 §5.5)
# ============================================================


class PlanUpgradeEvent(BaseModel):
    id: str
    workspace_id: str
    workspace_slug: str
    workspace_name: str
    distributor_id: str
    distributor_slug: str
    distributor_name: str
    year_month: str
    from_plan: str
    to_plan: str
    triggered_api_chars: int
    threshold: int
    created_at: float


def _enrich_upgrade_events(rows: list[dict]) -> list[PlanUpgradeEvent]:
    """원시 이벤트 row 에 워크스페이스·대리점 이름 등 추가 정보 결합."""
    out: list[PlanUpgradeEvent] = []
    ws_cache: dict = {}
    d_cache: dict = {}
    for r in rows:
        wid = r["workspace_id"]
        if wid not in ws_cache:
            ws_cache[wid] = workspaces.get(wid)
        ws = ws_cache[wid]
        did = r["distributor_id"] or (ws.distributor_id if ws else "")
        if did and did not in d_cache:
            d_cache[did] = distributors_store.get(did)
        d = d_cache.get(did) if did else None
        out.append(PlanUpgradeEvent(
            id=r["id"],
            workspace_id=wid,
            workspace_slug=ws.slug if ws else "",
            workspace_name=ws.name if ws else "(deleted)",
            distributor_id=did or "",
            distributor_slug=d.slug if d else "",
            distributor_name=d.name if d else "",
            year_month=r["year_month"],
            from_plan=r["from_plan"],
            to_plan=r["to_plan"],
            triggered_api_chars=r["triggered_api_chars"],
            threshold=r["threshold"],
            created_at=r["created_at"],
        ))
    return out


@router.get("/plan-upgrade-events", response_model=list[PlanUpgradeEvent])
async def list_plan_upgrades_super(
    limit: int = 50,
    _sess: Session = Depends(require_super_admin),
) -> list[PlanUpgradeEvent]:
    """운영자: 모든 대리점·워크스페이스의 자동 플랜 업그레이드 이력."""
    from app.services.translation_usage import list_upgrade_events
    raw = await run_db(list_upgrade_events, limit, None)
    return await run_db(_enrich_upgrade_events, raw)


@router.get("/me/plan-upgrade-events", response_model=list[PlanUpgradeEvent])
async def list_plan_upgrades_my(
    limit: int = 50,
    sess: Session = Depends(require_distributor_admin),
) -> list[PlanUpgradeEvent]:
    """대리점: 자기 산하 워크스페이스의 자동 플랜 업그레이드 이력."""
    from app.services.translation_usage import list_upgrade_events
    raw = await run_db(list_upgrade_events, limit, sess.distributor_id)
    return await run_db(_enrich_upgrade_events, raw)


@router.delete("/me/workspaces/{workspace_id}/logo")
async def delete_workspace_logo(
    workspace_id: str,
    sess: Session = Depends(require_distributor_admin),
) -> dict:
    """대리점이 자기 산하 워크스페이스의 로고를 제거.

    파일 삭제 + workspaces.logo_url = NULL.
    """
    await run_db(_verify_workspace_ownership, sess, workspace_id)
    await run_db(delete_workspace_logo_file, workspace_id)
    await run_db(workspaces.set_logo_url, workspace_id, None)
    return {"ok": True, "workspace_id": workspace_id}


# ============================================================
# Phase 2.9 — 점장 PW 재발급 (대리점이 산하 점장의 PW 를 직접 리셋)
# ============================================================


class WorkspacePasswordResetIn(BaseModel):
    new_password: str = Field(..., min_length=4, max_length=200)


class WorkspacePasswordResetResult(BaseModel):
    ok: bool
    workspace_id: str
    workspace_slug: str
    workspace_name: str
    new_password: str  # 평문 반환 (대리점이 점장에게 직접 전달)


@router.patch(
    "/me/workspaces/{workspace_id}/password",
    response_model=WorkspacePasswordResetResult,
)
async def reset_workspace_password(
    workspace_id: str,
    body: WorkspacePasswordResetIn,
    sess: Session = Depends(require_distributor_admin),
) -> WorkspacePasswordResetResult:
    """대리점이 자기 산하 점장의 PW 를 재발급.

    - 점장이 PW 를 잊었을 때 대리점이 즉시 새 PW 발급 (테스트 단계는 이메일 없음)
    - 새 PW 는 응답에 평문으로 동봉되어 대리점이 점장에게 직접 전달
    - 기존 점장 JWT 세션은 만료 전까지 유효 (revocation list 미도입). 보안이 중요하면
      app.session_token_ttl_seconds 를 짧게 설정.
    """
    ws = await run_db(_verify_workspace_ownership, sess, workspace_id)
    from app.services.distributors import hash_password
    new_hash = hash_password(body.new_password)
    await run_db(workspaces.set_owner_password_hash, workspace_id, new_hash)
    return WorkspacePasswordResetResult(
        ok=True,
        workspace_id=ws.id,
        workspace_slug=ws.slug or "",
        workspace_name=ws.name,
        new_password=body.new_password,
    )


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
