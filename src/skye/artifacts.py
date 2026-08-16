from __future__ import annotations

import re
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any

import structlog
from openai import AsyncOpenAI

log = structlog.get_logger()

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


@dataclass(frozen=True, slots=True)
class _FileRef:
    container_id: str
    file_id: str
    path: str
    size: int | None = None
    created_at: int | None = None


def without_sandbox_links(text: str) -> str:
    """Replace sandbox markdown links with their labels so Telegram is not given a dead URL."""
    return _SANDBOX_LINK.sub(r"\1", text)


def package_files(files: Sequence[tuple[str, bytes]]) -> tuple[GeneratedFile, ...]:
    """Turn container paths into Telegram documents. A directory becomes a zip."""
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


async def collect_container_files(
    client: AsyncOpenAI | None,
    result: object,
    max_bytes: int,
    *,
    created_after: int | None = None,
) -> tuple[GeneratedFile, ...]:
    if client is None:
        return ()
    cited, container_ids = inspect_container_files(result)
    listed = await _list_files(client, container_ids)
    selected = _select_files(cited, listed, created_after)
    if not selected:
        return ()
    downloaded: list[tuple[str, bytes]] = []
    for item in selected:
        if item.size is not None and item.size > max_bytes:
            log.warning(
                "container_file_too_large",
                path=item.path,
                bytes=item.size,
            )
            continue
        data = await _download(client, item)
        if data is None:
            continue
        if len(data) > max_bytes:
            log.warning(
                "container_file_too_large",
                path=item.path,
                bytes=len(data),
            )
            continue
        downloaded.append((item.path, data))
    packaged = package_files(downloaded)
    if packaged:
        log.info(
            "container_files_ready",
            count=len(packaged),
            names=[item.filename for item in packaged],
        )
    return packaged


def inspect_container_files(result: object) -> tuple[tuple[_FileRef, ...], frozenset[str]]:
    cited: dict[str, _FileRef] = {}
    container_ids: set[str] = set()
    for item in _output_items(result):
        container_id = _container_id(item)
        if container_id:
            container_ids.add(container_id)
        for annotation in _annotations(item):
            if _field(annotation, "type") != "container_file_citation":
                continue
            container_id = _text(_field(annotation, "container_id"))
            file_id = _text(_field(annotation, "file_id"))
            if not container_id or not file_id:
                continue
            container_ids.add(container_id)
            path = _citation_path(annotation)
            cited[file_id] = _FileRef(container_id, file_id, path)
    return tuple(cited.values()), frozenset(container_ids)


def _select_files(
    cited: Sequence[_FileRef],
    listed: Sequence[_FileRef],
    created_after: int | None,
) -> list[_FileRef]:
    selected: dict[str, _FileRef] = {}
    cited_ids = {item.file_id for item in cited}
    for item in cited:
        selected[item.file_id] = item
    for item in listed:
        if not _keep_path(item.path):
            continue
        if item.file_id in cited_ids:
            current = selected[item.file_id]
            selected[item.file_id] = _FileRef(
                item.container_id,
                item.file_id,
                item.path or current.path,
                item.size if item.size is not None else current.size,
                item.created_at,
            )
            continue
        if (
            created_after is not None
            and item.created_at is not None
            and item.created_at < created_after
        ):
            continue
        selected.setdefault(item.file_id, item)
    return [item for item in selected.values() if _keep_path(item.path)]


def _citation_path(annotation: object) -> str:
    filename = _text(_field(annotation, "filename")) or "file"
    if filename.startswith(DATA_ROOT):
        return filename
    return f"{DATA_ROOT}/{filename.lstrip('/')}"


def _keep_path(path: str) -> bool:
    relative = _relative(path)
    if not relative:
        return False
    return not any(_skip_part(part) for part in PurePosixPath(relative).parts)


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


def _output_items(result: object) -> list[object]:
    items: list[object] = []
    for response in _field(result, "raw_responses") or ():
        items.extend(_field(response, "output") or ())
    for item in _field(result, "new_items") or ():
        items.append(_field(item, "raw_item") or item)
    return items


def _annotations(item: object) -> list[object]:
    found: list[object] = []
    contents = _field(item, "content")
    if isinstance(contents, list):
        for content in contents:
            annotations = _field(content, "annotations")
            if isinstance(annotations, list):
                found.extend(annotations)
    annotations = _field(item, "annotations")
    if isinstance(annotations, list):
        found.extend(annotations)
    return found


def _container_id(item: object) -> str | None:
    environment = _field(item, "environment")
    if environment is not None:
        value = _text(_field(environment, "container_id"))
        if value:
            return value
    value = _text(_field(item, "container_id"))
    return value or None


def _field(obj: object, name: str) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name)
    return getattr(obj, name, None)


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


async def _list_files(client: AsyncOpenAI, container_ids: frozenset[str]) -> list[_FileRef]:
    listed: list[_FileRef] = []
    for container_id in container_ids:
        try:
            async for item in client.containers.files.list(container_id, limit=100):
                source = _text(_field(item, "source"))
                if source == "user":
                    continue
                file_id = _text(_field(item, "id"))
                path = _text(_field(item, "path"))
                if not file_id or not path:
                    continue
                size = _field(item, "bytes")
                created_at = _field(item, "created_at")
                listed.append(
                    _FileRef(
                        _text(_field(item, "container_id")) or container_id,
                        file_id,
                        path,
                        size if isinstance(size, int) else None,
                        created_at if isinstance(created_at, int) else None,
                    )
                )
        except Exception as error:
            log.warning(
                "container_list_failed",
                error=type(error).__name__,
            )
    return listed


async def _download(client: AsyncOpenAI, item: _FileRef) -> bytes | None:
    try:
        content = await client.containers.files.content.retrieve(
            item.file_id,
            container_id=item.container_id,
        )
    except Exception as error:
        log.warning(
            "container_file_download_failed",
            error=type(error).__name__,
        )
        return None
    data = getattr(content, "content", None)
    if isinstance(data, bytes):
        return data
    read = getattr(content, "read", None)
    if callable(read):
        result = read()
        if isinstance(result, bytes):
            return result
    return None
