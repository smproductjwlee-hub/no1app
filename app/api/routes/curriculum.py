from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.core.config import Settings, get_settings
from app.services.google_sheets import (
    fetch_sheet_grid,
    get_sheets_service,
    grid_to_records,
    resolve_credentials_path,
)

router = APIRouter(prefix="/curriculum", tags=["curriculum"])


class CurriculumSheetOut(BaseModel):
    spreadsheet_id: str
    sheet_gid: int
    header_row: int
    row_count: int
    rows: list[dict[str, str]]


def _load_curriculum(
    *,
    settings: Settings,
    spreadsheet_id: str,
    sheet_gid: int,
    header_row: int,
    raw: bool,
) -> Any:
    if not settings.google_use_adc_impersonate:
        cred_path = resolve_credentials_path(settings.google_credentials_path)
        if not Path(cred_path).is_file():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Credentials file not found: {cred_path}",
            )
    else:
        if not (settings.google_impersonate_service_account or "").strip():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Set GOOGLE_IMPERSONATE_SERVICE_ACCOUNT and run: gcloud auth application-default login",
            )
    try:
        service = get_sheets_service(settings)
        grid = fetch_sheet_grid(service, spreadsheet_id, sheet_gid)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Google Sheets error: {exc}",
        ) from exc

    if raw:
        return {
            "spreadsheet_id": spreadsheet_id,
            "sheet_gid": sheet_gid,
            "values": grid,
        }

    rows = grid_to_records(grid, header_row)
    return CurriculumSheetOut(
        spreadsheet_id=spreadsheet_id,
        sheet_gid=sheet_gid,
        header_row=header_row,
        row_count=len(rows),
        rows=rows,
    )


@router.get("/kaigo")
async def get_kaigo_curriculum(
    settings: Settings = Depends(get_settings),
    raw: bool = Query(False, description="Return raw grid from Sheets instead of keyed rows"),
) -> Any:
    """개호 시나리오 스프레드시트 데이터."""
    return _load_curriculum(
        settings=settings,
        spreadsheet_id=settings.kaigo_spreadsheet_id,
        sheet_gid=settings.kaigo_sheet_gid,
        header_row=settings.kaigo_header_row,
        raw=raw,
    )


@router.get("/food")
async def get_food_curriculum(
    settings: Settings = Depends(get_settings),
    raw: bool = Query(False, description="Return raw grid from Sheets instead of keyed rows"),
) -> Any:
    """외식 시나리오 스프레드시트 데이터."""
    return _load_curriculum(
        settings=settings,
        spreadsheet_id=settings.food_spreadsheet_id,
        sheet_gid=settings.food_sheet_gid,
        header_row=settings.food_header_row,
        raw=raw,
    )


@router.get("/food-glossary")
async def get_food_glossary(
    settings: Settings = Depends(get_settings),
    raw: bool = Query(False, description="Return raw grid from Sheets instead of keyed rows"),
) -> Any:
    """음식업계 분야별 단어 리스트 스프레드시트 데이터."""
    return _load_curriculum(
        settings=settings,
        spreadsheet_id=settings.food_glossary_spreadsheet_id,
        sheet_gid=settings.food_glossary_sheet_gid,
        header_row=settings.food_glossary_header_row,
        raw=raw,
    )


@router.get("/course-list")
async def get_course_list_curriculum(
    settings: Settings = Depends(get_settings),
    raw: bool = Query(False, description="Return raw grid from Sheets instead of keyed rows"),
) -> Any:
    """日本語コース一覧（東南アジア向け） 등 코스 목록 시트."""
    return _load_curriculum(
        settings=settings,
        spreadsheet_id=settings.course_list_spreadsheet_id,
        sheet_gid=settings.course_list_sheet_gid,
        header_row=settings.course_list_header_row,
        raw=raw,
    )


@router.get("/extra")
async def get_extra_curriculum(
    settings: Settings = Depends(get_settings),
    raw: bool = Query(False, description="Return raw grid from Sheets instead of keyed rows"),
) -> Any:
    """세 번째 공유 스프레드시트 데이터 (설정: extra_*)."""
    return _load_curriculum(
        settings=settings,
        spreadsheet_id=settings.extra_spreadsheet_id,
        sheet_gid=settings.extra_sheet_gid,
        header_row=settings.extra_header_row,
        raw=raw,
    )
