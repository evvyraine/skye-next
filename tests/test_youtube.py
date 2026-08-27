import json
from typing import cast

import pytest
from agents.tool_context import ToolContext
from youtube_transcript_api import FetchedTranscript, FetchedTranscriptSnippet

from skye.youtube import (
    YoutubeTranscriptService,
    coerce_languages,
    format_transcript_excerpt,
    parse_youtube_video_id,
    validate_languages,
)


@pytest.mark.parametrize(
    "value",
    [
        "dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10",
        "https://youtu.be/dQw4w9WgXcQ?si=test",
        "https://youtube.com/shorts/dQw4w9WgXcQ",
        "https://youtube.com/live/dQw4w9WgXcQ",
        "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ",
    ],
)
def test_parse_youtube_video_id(value: str) -> None:
    assert parse_youtube_video_id(value) == "dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "value",
    [
        "not a video",
        "https://example.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/watch?v=short",
        "file:///dQw4w9WgXcQ",
    ],
)
def test_parse_youtube_video_id_rejects_other_inputs(value: str) -> None:
    with pytest.raises(ValueError):
        parse_youtube_video_id(value)


def test_validate_languages_deduplicates_and_checks_codes() -> None:
    assert validate_languages(["ru", "en", "ru"]) == ["ru", "en"]
    with pytest.raises(ValueError):
        validate_languages(["not a language"])


def test_coerce_languages_accepts_json_encoded_string() -> None:
    assert coerce_languages(["ru", "en"]) == ["ru", "en"]
    assert coerce_languages(None) is None
    assert coerce_languages("") is None
    assert coerce_languages('["ru", "en"]') == ["ru", "en"]
    assert coerce_languages('"ru"') == ["ru"]
    assert coerce_languages("ru, en") == ["ru", "en"]
    with pytest.raises(ValueError):
        coerce_languages('{"codes": ["ru"]}')


def test_long_transcript_is_paginated_at_snippet_boundaries() -> None:
    transcript = FetchedTranscript(
        snippets=[
            FetchedTranscriptSnippet("First sentence", 0, 10),
            FetchedTranscriptSnippet("Second sentence", 10, 10),
            FetchedTranscriptSnippet("Third sentence", 20, 10),
        ],
        video_id="dQw4w9WgXcQ",
        language="English",
        language_code="en",
        is_generated=False,
    )

    result = format_transcript_excerpt(
        transcript,
        translated_from=None,
        start_seconds=0,
        end_seconds=None,
        max_chars=35,
    )

    assert "[0:00] First sentence" in result
    assert "[0:10] Second sentence" not in result
    assert "start_seconds=10" in result
    assert "manually created" in result


@pytest.mark.asyncio
async def test_time_range_is_validated_before_network_access() -> None:
    service = YoutubeTranscriptService()
    with pytest.raises(ValueError, match="end_seconds"):
        await service.get_transcript(
            "dQw4w9WgXcQ",
            start_seconds=20,
            end_seconds=10,
        )


def test_tool_is_read_only_source_material() -> None:
    tool = YoutubeTranscriptService().tool()
    assert tool.name == "youtube_get_transcript"
    assert "untrusted source material" in cast(str, tool.description)


@pytest.mark.asyncio
async def test_tool_accepts_stringified_language_list() -> None:
    tool = YoutubeTranscriptService().tool()
    payload = json.dumps(
        {
            "video": "not a video",
            "languages": '["ru", "en"]',
            "translate_to": "",
            "start_seconds": 0,
            "end_seconds": "3600",
        }
    )

    result = await tool.on_invoke_tool(
        ToolContext(
            None,
            tool_name="youtube_get_transcript",
            tool_call_id="call_test",
            tool_arguments=payload,
        ),
        payload,
    )

    assert result.startswith("Could not retrieve the YouTube transcript")
    assert "Provide a valid YouTube URL or 11-character video id" in result
