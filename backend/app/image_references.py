from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urlparse

from sqlmodel import Session, select

from app.models import AgentMessageImage, ImageAsset


LOCAL_IMAGE_URL_PREFIX = "/api/agents/media/images/"


def extract_local_image_id(image_ref: str) -> Optional[str]:
    parsed = urlparse(image_ref)
    image_path = parsed.path.rstrip("/")
    if not image_path.startswith(LOCAL_IMAGE_URL_PREFIX):
        return None
    image_id = image_path.removeprefix(LOCAL_IMAGE_URL_PREFIX).strip()
    return image_id or None


def get_orphaned_image_ids(db: Session, image_ids: Iterable[str]) -> list[str]:
    unique_ids = list(dict.fromkeys(image_ids))
    orphaned: list[str] = []
    for image_id in unique_ids:
        still_used = db.exec(
            select(AgentMessageImage.id).where(AgentMessageImage.image_id == image_id).limit(1)
        ).first()
        if still_used is None:
            orphaned.append(image_id)
    return orphaned


def delete_orphaned_images(db: Session, image_ids: Iterable[str]) -> list[str]:
    orphaned_ids = get_orphaned_image_ids(db, image_ids)
    deleted: list[str] = []
    for image_id in orphaned_ids:
        asset = db.get(ImageAsset, image_id)
        if asset is None:
            continue

        storage_path = Path(asset.storage_path)
        if storage_path.exists():
            storage_path.unlink()
        db.delete(asset)
        deleted.append(image_id)

    if deleted:
        db.commit()
    return deleted
