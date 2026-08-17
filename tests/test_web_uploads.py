from skye.attachments import is_audio_upload, openai_file_parts


def test_photos_become_vision_inputs() -> None:
    parts = openai_file_parts("shot.png", "image/png", b"png-bytes")
    assert parts[1]["type"] == "input_image"
    assert str(parts[1]["image_url"]).startswith("data:image/png;base64,")


def test_pdfs_keep_detail_auto() -> None:
    parts = openai_file_parts("notes.pdf", "application/pdf", b"%PDF")
    assert parts[1]["type"] == "input_file"
    assert parts[1]["detail"] == "auto"


def test_audio_uploads_are_detected() -> None:
    assert is_audio_upload("voice.webm", "audio/webm")
    assert is_audio_upload("clip.ogg", "application/octet-stream")
    assert not is_audio_upload("notes.pdf", "application/pdf")
