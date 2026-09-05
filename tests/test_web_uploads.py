from skye.attachments import is_audio_upload, openai_file_parts


def test_photos_become_vision_inputs() -> None:
    parts = openai_file_parts("shot.png", "image/png", b"png-bytes")
    assert parts[1]["type"] == "input_image"
    assert str(parts[1]["image_url"]).startswith("data:image/png;base64,")


def test_photos_are_always_inline_data_urls() -> None:
    parts = openai_file_parts("shot.png", "image/png", b"png-bytes")
    assert parts[1] == {
        "type": "input_image",
        "detail": "auto",
        "image_url": "data:image/png;base64,cG5nLWJ5dGVz",
    }


def test_pdfs_keep_detail_auto() -> None:
    parts = openai_file_parts("notes.pdf", "application/pdf", b"%PDF")
    assert parts[1]["type"] == "input_file"
    assert parts[1]["detail"] == "auto"


def test_documents_are_always_inline_file_data() -> None:
    parts = openai_file_parts("notes.pdf", "application/pdf", b"%PDF")

    assert parts[1] == {
        "type": "input_file",
        "filename": "notes.pdf",
        "file_data": "data:application/pdf;base64,JVBERg==",
        "detail": "auto",
    }


def test_audio_uploads_include_transcript_and_native_audio() -> None:
    parts = openai_file_parts("voice.ogg", "audio/ogg", b"audio", transcript="Transcript")

    assert parts[0] == {
        "type": "input_text",
        "text": "Attached audio transcript (voice.ogg):\nTranscript",
    }
    assert parts[1] == {
        "type": "input_audio",
        "input_audio": {"data": "YXVkaW8=", "format": "ogg"},
    }


def test_audio_uploads_are_detected() -> None:
    assert is_audio_upload("voice.webm", "audio/webm")
    assert is_audio_upload("clip.ogg", "application/octet-stream")
    assert not is_audio_upload("notes.pdf", "application/pdf")
