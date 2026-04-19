"""スタッフアカウント顔写真（静的ファイル・正方形 JPEG）。"""

from __future__ import annotations

import time
from io import BytesIO
from pathlib import Path

STATIC_ROOT = Path(__file__).resolve().parent.parent.parent / "static"
AVATAR_DIR = STATIC_ROOT / "uploads" / "staff-avatars"
ADMIN_AVATAR_DIR = STATIC_ROOT / "uploads" / "admin-avatars"

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
