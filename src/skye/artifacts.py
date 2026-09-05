from __future__ import annotations

import re
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path, PurePosixPath

DATA_ROOT = "/mnt/data"
_SKIP_NAMES = frozenset({"__pycache__", ".git", ".DS_Store"})
_SKIP_SUFFIXES = frozenset({".pyc", ".pyo"})
_SANDBOX_LINK = re.compile(
    r"\[([^\]]+)\]\((?:sandbox:|file://|/mnt/data/)[^)\s]+\)",
    re.IGNORECASE,
)
_MAX_INDIVIDUAL_FILES = 10


@dataclass(frozen=True, slots=True)
class GeneratedFile:
    filename: str
    data: bytes


def without_sandbox_links(text: str) -> str:
    """Replace sandbox markdown links with their labels so Telegram is not given a dead URL."""
    return _SANDBOX_LINK.sub(r"\1", text)


def package_files(files: Sequence[tuple[str, bytes]]) -> tuple[GeneratedFile, ...]:
    """Turn sandbox paths into Telegram documents. A directory becomes a zip."""
    relative = [(_relative(path), data) for path, data in files if _relative(path)]
    if not relative:
        return ()
    first_dirs = {rel.split("/", 1)[0] if "/" in rel else "" for rel, _ in relative}
    if "" not in first_dirs and len(first_dirs) == 1 and len(relative) > 1:
        directory = next(iter(first_dirs))
        prefix = f"{directory}/"
        return (
            _zip(
                directory,
                [(rel[len(prefix) :], data) for rel, data in relative],
            ),
        )
    if len(relative) > _MAX_INDIVIDUAL_FILES:
        return (_zip("files", relative),)
    return tuple(GeneratedFile(_filename(rel), data) for rel, data in relative)


def _relative(path: str) -> str:
    normalized = path.replace("\\", "/").strip()
    if not normalized.startswith(f"{DATA_ROOT}/"):
        return ""
    relative = normalized[len(DATA_ROOT) + 1 :].lstrip("/")
    return relative if relative and not relative.endswith("/") else ""


def _skip_part(part: str) -> bool:
    return part in _SKIP_NAMES or Path(part).suffix.lower() in _SKIP_SUFFIXES


def _filename(relative: str) -> str:
    name = PurePosixPath(relative).name.strip() or "file"
    return name[:200]


def _zip(name: str, files: Sequence[tuple[str, bytes]]) -> GeneratedFile:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for arcname, data in files:
            safe = arcname.replace("\\", "/").lstrip("/")
            if not safe or safe.endswith("/") or ".." in PurePosixPath(safe).parts:
                continue
            archive.writestr(safe, data)
    return GeneratedFile(f"{_filename(name)}.zip", buffer.getvalue())
