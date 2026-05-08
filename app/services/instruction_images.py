"""管理者が送信する指示用画像（ワークスペースごと・Google には送らない）。"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, status

STATIC_ROOT = Path(__file__).resolve().parent.parent.parent / "static"
INSTR_IMG_DIR = STATIC_ROOT / "uploads" / "instruction-images"

_MAX_BYTES = 5 * 1024 * 1024
_ALLOWED_CT = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/gif",
    }
)
_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def _sniff_image_type(raw: bytes) -> Optional[str]:
    if len(raw) < 12:
        return None
    if raw[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return None


def ensure_dir(workspace_id: str) -> Path:
    d = INSTR_IMG_DIR / workspace_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def delete_workspace_dir(workspace_id: str) -> int:
    """指定ワークスペースの指示画像ディレクトリを丸ごと削除（best-effort）。
    削除した個別ファイル数を返す。ディレクトリが存在しなければ 0。
    """
    d = INSTR_IMG_DIR / workspace_id
    if not d.is_dir():
        return 0
    count = 0
    try:
        for child in d.iterdir():
            try:
                if child.is_file():
                    child.unlink()
                    count += 1
            except OSError:
                pass
        try:
            d.rmdir()
        except OSError:
            pass
    except Exception:
        pass
    return count


def save_instruction_image_bytes(workspace_id: str, raw: bytes, content_type: str) -> str:
    """Validate and save image bytes; return URL path under /static/…"""
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct not in _ALLOWED_CT:
        sniff = _sniff_image_type(raw)
        if sniff:
            ct = sniff
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="unsupported_image_type",
            )
    if len(raw) > _MAX_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="image_too_large")
    ext = _EXT.get(ct, ".jpg")
    fid = str(uuid.uuid4())
    dest = ensure_dir(workspace_id) / f"{fid}{ext}"
    dest.write_bytes(raw)
    return f"/static/uploads/instruction-images/{workspace_id}/{fid}{ext}"


def is_allowed_instruction_image_url(workspace_id: str, url: str) -> bool:
    u = (url or "").strip().split("?")[0].split("#")[0]
    if not u.startswith("/static/uploads/instruction-images/"):
        return False
    parts = u.strip("/").split("/")
    # '', static, uploads, instruction-images, {ws}, filename
    if len(parts) < 6:
        return False
    return parts[4] == workspace_id
