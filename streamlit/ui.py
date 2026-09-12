# ruff: noqa: E501
"""Presentation layer: landing tokens, theme CSS, and chat rendering."""

from __future__ import annotations

import html
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

import streamlit as st

from skye_api import ChatFile, Message, Upload

if TYPE_CHECKING:
    from skye_api import Client

PROJECT_ICONS: tuple[str, ...] = (
    "cloud",
    "chat-bubble-left-right",
    "code-bracket",
    "cog-6-tooth",
    "briefcase",
    "academic-cap",
    "heart",
    "sparkles",
    "globe-alt",
    "paint-brush",
    "beaker",
    "musical-note",
    "camera",
    "folder",
    "light-bulb",
    "star",
)

PROJECT_COLORS: tuple[str, ...] = (
    "zinc",
    "red",
    "orange",
    "amber",
    "green",
    "teal",
    "blue",
    "violet",
)

PROJECT_PASTELS: dict[str, str] = {
    "zinc": "#C9C5D8",
    "red": "#F0A7AC",
    "orange": "#F4C08A",
    "amber": "#EAD37A",
    "green": "#9FDCAB",
    "teal": "#8FD8C8",
    "blue": "#9CC6F2",
    "violet": "#BFA9F0",
}

COLOR_LABELS: dict[str, str] = {
    "zinc": "Granite",
    "red": "Coral",
    "orange": "Tangerine",
    "amber": "Honey",
    "green": "Meadow",
    "teal": "Lagoon",
    "blue": "Sky",
    "violet": "Lavender",
}

# Dark tonal shade of each pastel, used for the icon inside its badge.
PROJECT_INKS: dict[str, str] = {
    "zinc": "#4A4657",
    "red": "#7A2E36",
    "orange": "#7A4A16",
    "amber": "#6E5A12",
    "green": "#1F5E33",
    "teal": "#0F5A50",
    "blue": "#1E4C7A",
    "violet": "#4A2E86",
}

ICON_LABELS: dict[str, str] = {
    "cloud": "Cloud",
    "chat-bubble-left-right": "Chat",
    "code-bracket": "Code",
    "cog-6-tooth": "Settings",
    "briefcase": "Briefcase",
    "academic-cap": "Academic",
    "heart": "Heart",
    "sparkles": "Sparkles",
    "globe-alt": "Globe",
    "paint-brush": "Paint",
    "beaker": "Beaker",
    "musical-note": "Music",
    "camera": "Camera",
    "folder": "Folder",
    "light-bulb": "Idea",
    "star": "Star",
}

MATERIAL_ICONS: dict[str, str] = {
    "cloud": "cloud",
    "chat-bubble-left-right": "forum",
    "code-bracket": "code",
    "cog-6-tooth": "settings",
    "briefcase": "work",
    "academic-cap": "school",
    "heart": "favorite",
    "sparkles": "auto_awesome",
    "globe-alt": "public",
    "paint-brush": "brush",
    "beaker": "science",
    "musical-note": "music_note",
    "camera": "photo_camera",
    "folder": "folder",
    "light-bulb": "lightbulb",
    "star": "star",
}

ASSISTANT_AVATAR = ":material/auto_awesome:"
USER_AVATAR = ":material/person:"

_STATIC = Path(__file__).parent / "static"

# server.baseUrlPath prefix (e.g. "beta" when deployed behind /beta). Empty
# locally, so asset URLs stay /app/static/...
_BASE_PATH = "/" + os.environ.get("SKYE_BASE_PATH", "").strip().strip("/")
_BASE_PATH = "" if _BASE_PATH == "/" else _BASE_PATH


def asset(name: str) -> str:
    """URL for a file under static/, respecting the deployed base path."""
    return f"{_BASE_PATH}/app/static/{name}"


def inject_theme() -> None:
    st.html(
        """
        <link rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Material+Symbols+Rounded:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200" />
        <style>
        /* Colours, radii and fonts come from .streamlit/config.toml. This block
           only covers what the theme config cannot: hiding deploy chrome, the
           chat bubbles, and a few custom HTML helpers. Surfaces use currentColor
           so they follow the active theme without st.context.theme detection,
           which Streamlit documents as unreliable right after a theme change. */
        [data-testid="stAppDeployButton"], [data-testid="stDecoration"],
        [data-testid="stStatusWidget"], footer { display: none !important; }
        .block-container { padding-top: 4.5rem; max-width: 46rem; }

        /* Chat bubbles. */
        [data-testid="stChatMessage"] {
          background: color-mix(in srgb, currentColor 5%, transparent);
          border: 1px solid color-mix(in srgb, currentColor 11%, transparent);
          border-radius: 1.25rem;
          padding: 0.85rem 1.05rem;
        }
        [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
          background: color-mix(in srgb, currentColor 9%, transparent);
        }

        /* Keep the composer aligned with the conversation column. */
        [data-testid="stBottomBlockContainer"] {
          max-width: 46rem;
          margin: 0 auto;
        }

        .material-symbols-rounded {
          font-family: "Material Symbols Rounded";
          font-weight: normal; font-style: normal;
          font-size: 1.25rem; line-height: 1; display: inline-block;
          -webkit-font-feature-settings: "liga";
          font-feature-settings: "liga";
          -webkit-font-smoothing: antialiased;
        }

        /* Custom HTML helpers (hero, project badges, attachment chips). */
        .sk-hero {
          display: flex; flex-direction: column; align-items: center;
          text-align: center; gap: 0.9rem; padding: 2rem 0 1rem;
        }
        .sk-mark { width: 64px; height: 64px; filter: drop-shadow(0 10px 24px rgb(94 96 206 / 0.28)); }
        .sk-gradient-text {
          background: linear-gradient(-8.2deg, #7400b8 12.283%, #6930c3 31.142%, #5e60ce 50%, #5390d9 68.858%, #4a86c4 87.717%);
          -webkit-background-clip: text; background-clip: text;
          color: transparent; font-weight: 600; letter-spacing: -0.03em;
        }
        .sk-muted { color: currentColor; opacity: 0.6; }
        .sk-badge {
          display: inline-flex; align-items: center; justify-content: center;
          border-radius: 0.85rem; line-height: 1;
        }
        .sk-attachment {
          display: flex; align-items: center; gap: 0.65rem;
          border: 1px solid color-mix(in srgb, currentColor 14%, transparent);
          border-radius: 0.85rem; padding: 0.5rem 0.75rem;
          background: color-mix(in srgb, currentColor 5%, transparent);
        }
        .sk-attachment .name { font-size: 0.85rem; font-weight: 600; }
        .sk-attachment .meta { font-size: 0.72rem; opacity: 0.7; }
        </style>
        """
    )


def wordmark(height: int = 19) -> str:
    """Inline the wordmark so its currentColor fill follows the theme."""
    svg = (_STATIC / "logo-wordmark.svg").read_text()
    return svg.replace("<svg ", f'<svg style="height:{height}px;width:auto;display:block" ', 1)


def material_icon(icon_key: str, *, size: int = 20, color: str | None = None) -> str:
    name = MATERIAL_ICONS.get(icon_key, "folder")
    style = f"font-size:{size}px;"
    if color:
        style += f"color:{color};"
    return f'<span class="material-symbols-rounded" style="{style}">{name}</span>'


def project_badge(icon_key: str, color_key: str, *, size: int = 38) -> str:
    pastel = PROJECT_PASTELS.get(color_key, "#C9C5D8")
    ink = PROJECT_INKS.get(color_key, "#4A4657")
    font = int(size * 0.52)
    return (
        f'<span class="sk-badge" style="width:{size}px;height:{size}px;'
        f"background:{pastel};\">{material_icon(icon_key, size=font, color=ink)}</span>"
    )


def material_name(icon_key: str) -> str:
    return MATERIAL_ICONS.get(icon_key, "folder")


def format_bytes(value: int) -> str:
    if value < 1024:
        return f"{value} B"
    if value < 1024 * 1024:
        return f"{round(value / 1024)} KB"
    return f"{value / 1024 / 1024:.1f} MB"


def format_when(value: str | None) -> str:
    if not value:
        return ""
    try:
        stamp = time.mktime(time.strptime(value, "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return ""
    delta = time.time() - stamp
    if delta < 60:
        return "now"
    if delta < 3600:
        return f"{int(delta // 60)}m"
    if delta < 86_400:
        return f"{int(delta // 3600)}h"
    return time.strftime("%b %d", time.localtime(stamp))


def render_history(
    messages: list[Message], files_by_id: dict[str, ChatFile], client: Client
) -> None:
    index = 0
    while index < len(messages):
        message = messages[index]
        if message.role == "tool":
            tools: list[Message] = []
            while index < len(messages) and messages[index].role == "tool":
                tools.append(messages[index])
                index += 1
            reply = None
            if index < len(messages) and messages[index].role == "assistant":
                reply = messages[index]
                index += 1
            _assistant_message(tools, reply, files_by_id, client)
            continue
        if message.role == "user":
            _user_message(message, files_by_id, client)
        elif message.role == "assistant":
            _assistant_message([], message, files_by_id, client)
        index += 1


def _user_message(message: Message, files_by_id: dict[str, ChatFile], client: Client) -> None:
    with st.chat_message("user", avatar=USER_AVATAR):
        if message.text:
            st.markdown(message.text)
        attachments(message.file_ids, files_by_id, client)


def _assistant_message(
    tools: list[Message],
    message: Message | None,
    files_by_id: dict[str, ChatFile],
    client: Client,
) -> None:
    with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
        if tools:
            with st.status("Thought for a moment", type="compact", state="complete"):
                for tool in tools:
                    _tool_step(tool)
        if message is not None and message.text:
            st.markdown(message.text)
        if message is not None:
            attachments(message.file_ids, files_by_id, client)


def _tool_step(tool: Message) -> None:
    label = tool.text or tool.tool_name or "Step"
    with st.status(label, type="step", state="complete"):
        if tool.tool_args:
            st.code(tool.tool_args, language="json")
        if tool.tool_output:
            st.caption(tool.tool_output)


def attachments(
    file_ids: tuple[str, ...], files_by_id: dict[str, ChatFile], client: Client
) -> None:
    for file_id in file_ids:
        file = files_by_id.get(file_id)
        if file is not None:
            render_file(file, client)


def render_file(file: ChatFile, client: Client) -> None:
    if file.mime.startswith("image/"):
        data = client.thumbnail_bytes(file.id) or client.file_bytes(file.id)
        if data:
            st.image(data, width=340)
        return
    if file.mime.startswith("audio/"):
        data = client.file_bytes(file.id)
        if data:
            st.audio(data)
        return
    if file.mime.startswith("video/"):
        data = client.file_bytes(file.id)
        if data:
            st.video(data)
        return
    _document_chip(file, client)


def render_uploads(uploads: list[Upload]) -> None:
    for upload in uploads:
        if upload.mime.startswith("image/"):
            st.image(upload.data, width=340)
        elif upload.mime.startswith("audio/"):
            st.audio(upload.data)
        else:
            st.html(
                f'<div class="sk-attachment">{material_icon("folder", size=22)}'
                f'<div><div class="name">{html.escape(upload.name)}</div>'
                f'<div class="meta">{format_bytes(len(upload.data))} · '
                f"{html.escape(upload.mime)}</div></div></div>"
            )


def _document_chip(file: ChatFile, client: Client) -> None:
    data = client.file_bytes(file.id)
    st.html(
        f'<div class="sk-attachment">{material_icon("folder", size=22)}'
        f'<div><div class="name">{html.escape(file.filename)}</div>'
        f'<div class="meta">{format_bytes(file.size)} · {html.escape(file.mime)}</div></div></div>'
    )
    if data:
        st.download_button(
            "Download",
            data=data,
            file_name=file.filename,
            mime=file.mime,
            key=f"dl_{file.id}",
        )
