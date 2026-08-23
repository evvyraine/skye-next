from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit

CITATION_START = "\ue200"
CITATION_DELIMITER = "\ue202"
CITATION_STOP = "\ue201"

_FAMILY = r"(?:[A-Za-z]+)?"
_FIELDS = r"(?:" + re.escape(CITATION_DELIMITER) + r"[A-Za-z0-9_-]+)*"
_TOKEN = re.compile(
    re.escape(CITATION_START) + _FAMILY + _FIELDS + re.escape(CITATION_STOP),
)
_UNTERMINATED = re.compile(
    re.escape(CITATION_START)
    + _FAMILY
    + r"(?:"
    + re.escape(CITATION_DELIMITER)
    + r"[A-Za-z0-9_-]+)+"
)
_PUA = re.compile(r"[\ue200-\ue2ff]+")
_BRACKET = re.compile(r"【(?:turn\d+[A-Za-z]+\d+|\d+†[^】]*)】")
_CITE_PREFIX = re.compile(
    r"\bcite(?:\s+|" + re.escape(CITATION_DELIMITER) + r")*(?=turn\d+[A-Za-z]+\d+)",
    re.IGNORECASE,
)
_SOURCE_ID = re.compile(r"\bturn\d+[A-Za-z]+\d+\b")
_HTTPS_URL = re.compile(r"https://[^\s<>\]\)\"']+", re.IGNORECASE)
_PLACEHOLDER = "\x00URL{index}\x00"


def sanitize_citations(
    text: str,
    annotations: Sequence[object] | None = None,
) -> str:
    """Remove model citation wrappers from user-visible text.

    Known https sources from response annotations replace the matching token.
    Unknown markers are dropped. Existing https URLs in the sentence stay.
    """
    if not text:
        return text
    updated = _replace_annotated_tokens(text, annotations)
    protected, urls = _protect_https_urls(updated)
    stripped = _TOKEN.sub("", protected)
    stripped = _UNTERMINATED.sub("", stripped)
    stripped = _PUA.sub("", stripped)
    stripped = _BRACKET.sub("", stripped)
    stripped = _CITE_PREFIX.sub("", stripped)
    stripped = _SOURCE_ID.sub("", stripped)
    restored = _restore_https_urls(stripped, urls)
    if restored == text:
        return text
    return _tidy(restored, text)


def url_citations(result: object) -> tuple[object, ...]:
    """Collect hosted web_search url_citation annotations from a run result."""
    found: list[object] = []
    for item in _output_items(result):
        for annotation in _item_annotations(item):
            if _field(annotation, "type") == "url_citation":
                found.append(annotation)
    return tuple(found)


def _replace_annotated_tokens(text: str, annotations: Sequence[object] | None) -> str:
    if not annotations:
        return text
    replacements: list[tuple[int, int, str]] = []
    for annotation in annotations:
        url = _https_url(_field(annotation, "url"))
        if not url:
            continue
        start = _field(annotation, "start_index")
        end = _field(annotation, "end_index")
        if not isinstance(start, int) or not isinstance(end, int):
            continue
        if start < 0 or end > len(text) or start >= end:
            continue
        span = text[start:end]
        if not _is_citation_span(span):
            continue
        replacements.append((start, end, _markdown_link(url, _field(annotation, "title"))))
    updated = text
    for start, end, replacement in sorted(replacements, key=lambda item: item[0], reverse=True):
        updated = updated[:start] + replacement + updated[end:]
    return updated


def _is_citation_span(span: str) -> bool:
    stripped = span.strip()
    if not stripped:
        return False
    if CITATION_START in stripped or CITATION_STOP in stripped or CITATION_DELIMITER in stripped:
        return True
    if _BRACKET.fullmatch(stripped):
        return True
    remainder = _TOKEN.sub("", stripped)
    remainder = _BRACKET.sub("", remainder)
    remainder = _CITE_PREFIX.sub("", remainder)
    remainder = _SOURCE_ID.sub("", remainder)
    remainder = _PUA.sub("", remainder)
    return not remainder.strip()


def _markdown_link(url: str, title: object) -> str:
    label = title.strip() if isinstance(title, str) else ""
    label = _PUA.sub("", label).strip()
    if (
        label
        and label != url
        and "\n" not in label
        and "[" not in label
        and "]" not in label
        and len(label) <= 120
    ):
        return f"[{label}]({url})"
    return url


def _https_url(value: object) -> str:
    if not isinstance(value, str):
        return ""
    url = value.strip()
    if not url.lower().startswith("https://") or any(ch.isspace() for ch in url):
        return ""
    host = urlsplit(url).netloc.split("@")[-1]
    if not host:
        return ""
    return url


def _protect_https_urls(text: str) -> tuple[str, list[str]]:
    urls: list[str] = []

    def replace(match: re.Match[str]) -> str:
        urls.append(match.group(0))
        return _PLACEHOLDER.format(index=len(urls) - 1)

    return _HTTPS_URL.sub(replace, text), urls


def _restore_https_urls(text: str, urls: Sequence[str]) -> str:
    restored = text
    for index, url in enumerate(urls):
        restored = restored.replace(_PLACEHOLDER.format(index=index), url)
    return restored


def _tidy(text: str, original: str) -> str:
    cleaned = re.sub(r"[^\S\n]{2,}", " ", text)
    cleaned = re.sub(r"[^\S\n]+([,.;:!?])", r"\1", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    if not original[:1].isspace():
        cleaned = cleaned.lstrip(" \t")
    if not original[-1:].isspace():
        cleaned = cleaned.rstrip(" \t")
    return cleaned


def _output_items(result: object) -> list[object]:
    items: list[object] = []
    for response in _field(result, "raw_responses") or ():
        items.extend(_field(response, "output") or ())
    for item in _field(result, "new_items") or ():
        items.append(_field(item, "raw_item") or item)
    return items


def _item_annotations(item: object) -> list[object]:
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


def _field(obj: object, name: str) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name)
    return getattr(obj, name, None)
