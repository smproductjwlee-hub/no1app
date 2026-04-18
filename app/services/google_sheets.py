"""Read-only Google Sheets access via service account."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, TYPE_CHECKING

from googleapiclient.discovery import build

from app.services.gcp_auth import credentials_sheets

if TYPE_CHECKING:
    from app.core.config import Settings

def project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def resolve_credentials_path(path_str: str) -> Path:
    p = Path(path_str).expanduser()
    if p.is_absolute():
        return p
    return (project_root() / p).resolve()


def _escape_sheet_title(title: str) -> str:
    return title.replace("'", "''")


@lru_cache(maxsize=8)
def _build_sheets_service(
    use_adc: bool,
    key_path: str,
    imp_email: str,
) -> Any:
    creds = credentials_sheets(
        use_adc_impersonate=use_adc,
        key_file=key_path if not use_adc else "",
        impersonate_service_account=imp_email if use_adc else "",
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def get_sheets_service(settings: "Settings") -> Any:
    if settings.google_use_adc_impersonate:
        return _build_sheets_service(
            True,
            "",
            (settings.google_impersonate_service_account or "").strip(),
        )
    resolved = str(resolve_credentials_path(settings.google_credentials_path))
    return _build_sheets_service(False, resolved, "")


def sheet_title_for_gid(service: Any, spreadsheet_id: str, gid: int) -> str:
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    for sheet in meta.get("sheets", []):
        props = sheet.get("properties") or {}
        if props.get("sheetId") == gid:
            title = props.get("title")
            if title:
                return title
    raise ValueError(f"No sheet with gid={gid} in spreadsheet {spreadsheet_id}")


def fetch_sheet_grid(service: Any, spreadsheet_id: str, sheet_gid: int) -> list[list[str]]:
    title = sheet_title_for_gid(service, spreadsheet_id, sheet_gid)
    escaped = _escape_sheet_title(title)
    range_a1 = f"'{escaped}'!A:ZZ"
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=range_a1)
        .execute()
    )
    raw = result.get("values") or []
    # Normalize ragged rows to str lists
    out: list[list[str]] = []
    for row in raw:
        out.append([str(c) if c is not None else "" for c in row])
    return out


def _unique_headers(headers: list[str]) -> list[str]:
    counts: dict[str, int] = {}
    result: list[str] = []
    for i, h in enumerate(headers):
        base = (h or "").strip() or f"column_{i}"
        n = counts.get(base, 0)
        counts[base] = n + 1
        result.append(base if n == 0 else f"{base}_{n}")
    return result


def grid_to_records(grid: list[list[str]], header_row_1based: int) -> list[dict[str, str]]:
    if header_row_1based < 1:
        raise ValueError("header_row_1based must be >= 1")
    idx = header_row_1based - 1
    if idx >= len(grid):
        return []
    headers = _unique_headers(grid[idx])
    records: list[dict[str, str]] = []
    for row in grid[idx + 1 :]:
        if not row or not any((c or "").strip() for c in row):
            continue
        rec: dict[str, str] = {}
        for j, key in enumerate(headers):
            rec[key] = row[j] if j < len(row) else ""
        records.append(rec)
    return records
