"""Bounded raster validation and atomic writes within a managed content-hash store."""

from __future__ import annotations

import hashlib
import io
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from unfold.provider import GenerationError

MAX_IMAGE_BYTES = 4_000_000
MAX_PIXELS = 16_000_000


@dataclass(frozen=True)
class StoredFile:
    digest: str
    relative_path: str
    mime: str
    size: int
    width: int
    height: int

    def dump(self) -> dict[str, Any]:
        return asdict(self)


class FileStore:
    def __init__(self, directory: Path):
        self.directory = directory.resolve() / "resources"

    def path(self, relative: str) -> Path:
        if not re.fullmatch(r"files/[0-9a-f]{64}\.(png|jpg|webp)", relative):
            raise GenerationError("invalid_input", "invalid managed file reference")
        candidate = self.directory / relative
        if not candidate.resolve().is_relative_to(self.directory):
            raise GenerationError("invalid_input", "file reference escapes managed directory")
        return candidate

    def verify(self, stored: StoredFile) -> bool:
        path = self.path(stored.relative_path)
        return (
            path.is_file()
            and path.stat().st_size == stored.size
            and hashlib.sha256(path.read_bytes()).hexdigest() == stored.digest
        )

    def write_image(self, data: bytes) -> StoredFile:
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise GenerationError("budget_exceeded", "image exceeds four-megabyte file limit")
        try:
            with Image.open(io.BytesIO(data)) as image:
                formats = {
                    "PNG": ("png", "image/png"),
                    "JPEG": ("jpg", "image/jpeg"),
                    "WEBP": ("webp", "image/webp"),
                }
                if image.format not in formats or getattr(image, "n_frames", 1) != 1:
                    raise GenerationError(
                        "capability_limit", "only static PNG/JPEG/WebP images supported"
                    )
                width, height = image.size
                if width * height > MAX_PIXELS or min(width, height) < 1:
                    raise GenerationError("budget_exceeded", "image exceeds pixel limit")
                extension, mime = formats[image.format]
                image.load()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise GenerationError("domain_validation", "invalid raster image") from exc
        digest = hashlib.sha256(data).hexdigest()
        stored = StoredFile(digest, f"files/{digest}.{extension}", mime, len(data), width, height)
        target = self.path(stored.relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file():
            if not self.verify(stored):
                raise GenerationError("domain_validation", "existing hash file is damaged")
            return stored
        with tempfile.NamedTemporaryFile(
            dir=target.parent, prefix=".staging-", delete=False
        ) as temp:
            temporary = Path(temp.name)
            try:
                temp.write(data)
                temp.flush()
                os.fsync(temp.fileno())
                os.replace(temporary, target)
                descriptor = os.open(target.parent, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            finally:
                temporary.unlink(missing_ok=True)
        return stored

    def unreferenced(self, references: set[str]) -> list[str]:
        return sorted(
            str(path.relative_to(self.directory))
            for path in (self.directory / "files").glob("*")
            if path.is_file() and str(path.relative_to(self.directory)) not in references
        )
