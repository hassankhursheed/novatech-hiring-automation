"""Document storage (claim-check pattern): files are stored once and referenced by key everywhere else.

LocalStorage writes to a Docker volume. The interface is small on purpose so an S3/GCS/Supabase Storage
implementation can replace it without touching callers.
"""

import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

_KEY_PATTERN = re.compile(r"^cv/\d{4}/\d{2}/[0-9a-f]{32}\.(pdf|docx)$")


class InvalidStorageKeyError(ValueError):
    pass


def validate_cv_key(key: str) -> str:
    if not _KEY_PATTERN.match(key or ""):
        raise InvalidStorageKeyError("invalid document reference")
    return key


def new_cv_key(extension: str) -> str:
    now = datetime.now(UTC)
    return f"cv/{now:%Y}/{now:%m}/{uuid.uuid4().hex}.{extension}"


class Storage(Protocol):
    def save(self, key: str, data: bytes) -> None: ...
    def save_text(self, key: str, text: str) -> None: ...
    def exists(self, key: str) -> bool: ...
    def read_text(self, key: str) -> str | None: ...


class LocalStorage:
    def __init__(self, root: str) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self._root / key).resolve()
        if self._root not in path.parents:  # defence in depth against path traversal
            raise InvalidStorageKeyError("invalid document reference")
        return path

    def save(self, key: str, data: bytes) -> None:
        path = self._path(validate_cv_key(key))
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)  # atomic publish

    def save_text(self, key: str, text: str) -> None:
        path = self._path(validate_cv_key(key)).with_suffix(".txt")
        path.write_text(text, encoding="utf-8")

    def exists(self, key: str) -> bool:
        try:
            return self._path(validate_cv_key(key)).is_file()
        except InvalidStorageKeyError:
            return False

    def read_text(self, key: str) -> str | None:
        try:
            path = self._path(validate_cv_key(key)).with_suffix(".txt")
        except InvalidStorageKeyError:
            return None
        return path.read_text(encoding="utf-8") if path.is_file() else None
