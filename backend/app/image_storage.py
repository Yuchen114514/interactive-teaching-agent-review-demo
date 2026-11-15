from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

try:
    from PIL import Image as PillowImage
except ImportError:  # pragma: no cover - optional dependency fallback
    PillowImage = None


ALLOWED_IMAGE_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def validate_image_upload(filename: str, content_type: str, size: int, max_bytes: int = 10 * 1024 * 1024) -> None:
    if content_type not in ALLOWED_IMAGE_CONTENT_TYPES:
        raise ValueError("Only image uploads are allowed")
    if size <= 0:
        raise ValueError("Uploaded image is empty")
    if size > max_bytes:
        raise ValueError("Uploaded image exceeds size limit")
    if not filename:
        raise ValueError("Uploaded image filename is missing")


def generate_image_id() -> str:
    return uuid4().hex


def build_image_storage_path(upload_root: Path, image_id: str, filename: str) -> Path:
    suffix = Path(filename).suffix.lower()
    safe_suffix = suffix if suffix in ALLOWED_IMAGE_CONTENT_TYPES.values() else ".bin"
    # 中文注释：按 ID 前缀分片存目录，避免单目录文件过多，后续迁服务器时也更容易整体搬迁。
    relative_dir = Path("images") / image_id[0:4] / image_id[4:8]
    return upload_root / relative_dir / f"{image_id}{safe_suffix}"


def build_image_public_url(api_base_origin: str, image_id: str) -> str:
    return f"{api_base_origin.rstrip('/')}/api/agents/media/images/{image_id}"


def build_thumbnail_public_url(api_base_origin: str, image_id: str) -> str:
    return f"{api_base_origin.rstrip('/')}/api/agents/media/images/{image_id}/thumbnail"


def build_thumbnail_storage_path(storage_path: Path) -> Path:
    return storage_path.with_name(f"{storage_path.stem}_thumb{storage_path.suffix or '.png'}")


def ensure_thumbnail_file(storage_path: Path, max_size: tuple[int, int] = (320, 320)) -> Path:
    thumbnail_path = build_thumbnail_storage_path(storage_path)
    if thumbnail_path.exists():
        return thumbnail_path

    thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
    if PillowImage is None:
        shutil.copyfile(storage_path, thumbnail_path)
        return thumbnail_path

    with PillowImage.open(storage_path) as image:
        image.thumbnail(max_size)
        save_format = image.format or "PNG"
        image.save(thumbnail_path, format=save_format)
    return thumbnail_path
