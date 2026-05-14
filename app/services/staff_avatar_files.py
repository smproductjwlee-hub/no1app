"""スタッフアカウント顔写真（静的ファイル・正方形 JPEG）。"""

from __future__ import annotations

import time
from io import BytesIO
from pathlib import Path

STATIC_ROOT = Path(__file__).resolve().parent.parent.parent / "static"
AVATAR_DIR = STATIC_ROOT / "uploads" / "staff-avatars"
ADMIN_AVATAR_DIR = STATIC_ROOT / "uploads" / "admin-avatars"
# Phase 2.8: 워크스페이스 로고 (대리점이 산하 고객사 브랜딩에 사용)
WORKSPACE_LOGO_DIR = STATIC_ROOT / "uploads" / "workspace-logos"

MAX_BYTES = 3 * 1024 * 1024
OUT_SIZE = 256


def ensure_dir() -> None:
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)


def file_path(account_id: str) -> Path:
    return AVATAR_DIR / f"{account_id}.jpg"


def delete_file(account_id: str) -> None:
    p = file_path(account_id)
    if p.is_file():
        p.unlink()


def save_square_jpeg(account_id: str, data: bytes) -> float:
    """正方形に中央クロップして JPEG で保存。戻り値は avatar_updated_at 用タイムスタンプ。"""
    if len(data) > MAX_BYTES:
        raise ValueError("file too large")
    ensure_dir()
    out_bytes: bytes
    try:
        from PIL import Image  # type: ignore[import-untyped]

        im = Image.open(BytesIO(data))
        im = im.convert("RGB")
        w, h = im.size
        side = min(w, h)
        left = (w - side) // 2
        top = (h - side) // 2
        im = im.crop((left, top, left + side, top + side))
        im = im.resize((OUT_SIZE, OUT_SIZE), Image.Resampling.LANCZOS)
        buf = BytesIO()
        im.save(buf, format="JPEG", quality=88)
        out_bytes = buf.getvalue()
    except ImportError:
        out_bytes = data
        if len(out_bytes) > MAX_BYTES:
            raise ValueError("file too large")

    dest = file_path(account_id)
    dest.write_bytes(out_bytes)
    return time.time()


def ensure_admin_dir() -> None:
    ADMIN_AVATAR_DIR.mkdir(parents=True, exist_ok=True)


def admin_file_path(workspace_id: str) -> Path:
    return ADMIN_AVATAR_DIR / f"{workspace_id}.jpg"


def delete_admin_file(workspace_id: str) -> None:
    p = admin_file_path(workspace_id)
    if p.is_file():
        p.unlink()


def save_admin_square_jpeg(workspace_id: str, data: bytes) -> float:
    """管理者プロフィール用（ワークスペース単位・1枚）。"""
    if len(data) > MAX_BYTES:
        raise ValueError("file too large")
    ensure_admin_dir()
    out_bytes: bytes
    try:
        from PIL import Image  # type: ignore[import-untyped]

        im = Image.open(BytesIO(data))
        im = im.convert("RGB")
        w, h = im.size
        side = min(w, h)
        left = (w - side) // 2
        top = (h - side) // 2
        im = im.crop((left, top, left + side, top + side))
        im = im.resize((OUT_SIZE, OUT_SIZE), Image.Resampling.LANCZOS)
        buf = BytesIO()
        im.save(buf, format="JPEG", quality=88)
        out_bytes = buf.getvalue()
    except ImportError:
        out_bytes = data
        if len(out_bytes) > MAX_BYTES:
            raise ValueError("file too large")

    dest = admin_file_path(workspace_id)
    dest.write_bytes(out_bytes)
    return time.time()


# ============================================================
# Phase 2.8 — 워크스페이스 로고 (좌상단 브랜딩)
# ============================================================


def ensure_workspace_logo_dir() -> None:
    WORKSPACE_LOGO_DIR.mkdir(parents=True, exist_ok=True)


def workspace_logo_file_path(workspace_id: str) -> Path:
    return WORKSPACE_LOGO_DIR / f"{workspace_id}.jpg"


def delete_workspace_logo_file(workspace_id: str) -> None:
    p = workspace_logo_file_path(workspace_id)
    if p.is_file():
        p.unlink()


def save_workspace_logo_jpeg(workspace_id: str, data: bytes) -> float:
    """워크스페이스 로고를 정사각형 JPEG (256x256) 으로 저장.

    좌상단 표시 영역이 h-12 w-12 (48x48) 이므로 256x256 이면 retina 까지 충분.
    """
    if len(data) > MAX_BYTES:
        raise ValueError("file too large")
    ensure_workspace_logo_dir()
    out_bytes: bytes
    try:
        from PIL import Image  # type: ignore[import-untyped]

        im = Image.open(BytesIO(data))
        # 투명도 보존하려면 RGBA 였겠지만, 좌상단은 흰 배경 위에 표시되므로 RGB 로 평탄화
        if im.mode in ("RGBA", "LA"):
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")
        w, h = im.size
        side = min(w, h)
        left = (w - side) // 2
        top = (h - side) // 2
        im = im.crop((left, top, left + side, top + side))
        im = im.resize((OUT_SIZE, OUT_SIZE), Image.Resampling.LANCZOS)
        buf = BytesIO()
        im.save(buf, format="JPEG", quality=90)
        out_bytes = buf.getvalue()
    except ImportError:
        out_bytes = data
        if len(out_bytes) > MAX_BYTES:
            raise ValueError("file too large")

    dest = workspace_logo_file_path(workspace_id)
    dest.write_bytes(out_bytes)
    return time.time()
