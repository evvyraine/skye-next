# ruff: noqa: E501
"""Skye web — Streamlit demo.

Variant A: this app is a thin client of the existing aiohttp API. It reads the
existing ``skye_session`` cookie (or ``?session=`` / ``SKYE_SESSION`` for local
testing) and never touches SQLite, so the bot process stays the single writer.

Without ``SKYE_API_URL`` it runs against an in-memory ``DemoClient`` so the UI
can be previewed instantly.

Run with:  ./streamlit/run.sh
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import streamlit as st

import ui
from project_list import project_list
from skye_api import (
    ChatFile,
    Client,
    DemoClient,
    Project,
    SkyeAPI,
    SkyeAPIError,
    Upload,
    User,
)

HERE = Path(__file__).parent
API_URL = os.environ.get("SKYE_API_URL", "").strip().rstrip("/")

st.set_page_config(
    page_title="Skye App",
    page_icon="✦",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
/* Only st.chat_input */
[data-testid="stChatInput"] {
    border-radius: 28px !important;
    overflow: hidden;  /* clips inner background to the rounded shape */
}

/* The actual visible input shell */
[data-testid="stChatInput"] > div {
    border-radius: 28px !important;
}

/* Covers nested elements in Streamlit versions with deeper wrappers */
[data-testid="stChatInput"] * {
    border-radius: inherit;
}
</style>
""", unsafe_allow_html=True)

# --------------------------------------------------------------------------- clients


def _token() -> str | None:
    try:
        cookie = st.context.cookies.get("skye_session")
    except Exception:  # pragma: no cover - no request context
        cookie = None
    if cookie:
        return str(cookie)
    query = st.query_params.get("session")
    if isinstance(query, list):
        query = query[0] if query else None
    if query:
        return str(query)
    return os.environ.get("SKYE_SESSION") or None


@st.cache_resource(show_spinner=False)
def _live_client(base_url: str, token: str) -> SkyeAPI:
    return SkyeAPI(base_url, token)


def _demo_client() -> DemoClient:
    client = st.session_state.get("_demo_client")
    if client is None:
        client = DemoClient()
        st.session_state["_demo_client"] = client
    return client


def _client() -> tuple[Client | None, bool]:
    """Returns (client, is_live). Client is None when live and signed out."""
    if API_URL:
        token = _token()
        return (_live_client(API_URL, token) if token else None), True
    return _demo_client(), False


def _auth_url() -> str:
    return os.environ.get("SKYE_AUTH_URL") or (f"{API_URL}/auth/telegram" if API_URL else "/auth/telegram")


def _dismiss_create() -> None:
    st.session_state.show_create = False


def _dismiss_edit() -> None:
    st.session_state.edit_id = None


def _dismiss_delete() -> None:
    st.session_state.delete_id = None


# --------------------------------------------------------------------------- shell


def main() -> None:
    st.session_state.setdefault("selected_id", None)
    st.session_state.setdefault("show_create", False)
    st.session_state.setdefault("edit_id", None)
    st.session_state.setdefault("delete_id", None)

    ui.inject_theme()
    client, is_live = _client()

    user: User | None = None
    allowed = False
    if client is not None:
        if is_live:
            try:
                user, allowed = client.me()
            except SkyeAPIError as exc:
                st.error(exc.message)
        else:
            demo_user = st.session_state.get("demo_user")
            if demo_user is not None:
                user, allowed = demo_user, True

    if user is None:
        login_screen(is_live)
        return
    if not allowed:
        denied_screen()
        return

    app_screen(client, user, is_live)  # type: ignore[arg-type]

    if st.session_state.show_create:
        create_dialog(client)  # type: ignore[arg-type]
    elif st.session_state.edit_id:
        project = _find_project(client, st.session_state.edit_id)
        if project is None:
            st.session_state.edit_id = None
        else:
            edit_dialog(client, project)  # type: ignore[arg-type]
    elif st.session_state.delete_id:
        project = _find_project(client, st.session_state.delete_id)
        if project is None:
            st.session_state.delete_id = None
        else:
            delete_dialog(client, project)  # type: ignore[arg-type]


def _find_project(client: Client, project_id: str) -> Project | None:
    try:
        return next((item for item in client.list_projects() if item.id == project_id), None)
    except SkyeAPIError:
        return None


def login_screen(is_live: bool) -> None:
    st.html(
        '<div class="sk-hero">'
        f'<img class="sk-mark" src="{ui.asset("logo-mark.svg")}" alt="Skye" />'
        '<div class="sk-gradient-text" style="font-size:2.1rem;line-height:1">skye</div>'
        '<div class="sk-muted" style="font-size:1.05rem">Спокойный личный ассистент.</div>'
        "</div>"
    )
    with st.container(horizontal_alignment="center"):
        if is_live:
            st.link_button("Continue with Telegram", _auth_url(), icon=":material/send:", type="primary")
            st.caption("Вход через Telegram OIDC. Доступ есть только у своих.")
        else:
            if st.button(
                "Continue with Telegram", icon=":material/send:", type="primary"
            ):
                st.session_state.demo_user = User(id=1, name="Alex", username="alex")
                st.rerun()
            st.caption("Demo mode — вход симулирован, данные живут в памяти.")


def denied_screen() -> None:
    st.html(
        '<div class="sk-hero">'
        f'<img class="sk-mark" src="{ui.asset("logo-mark.svg")}" alt="Skye" />'
        '<div class="sk-gradient-text" style="font-size:1.8rem">Доступ закрыт</div>'
        '<div class="sk-muted">Этот бот приватный.</div>'
        "</div>"
    )


# --------------------------------------------------------------------------- app


def app_screen(client: Client, user: User, is_live: bool) -> None:
    projects = _projects(client)
    _sidebar(client, user, projects, is_live)
    selected = _selected(projects)
    if selected is None:
        st.html(
            '<div class="sk-hero">'
            '<div class="sk-gradient-text" style="font-size:1.6rem">Пока пусто</div>'
            '<div class="sk-muted">Создай первый проект.</div></div>'
        )
        return
    _header(client, selected)
    messages, files = _messages(client, selected.id)
    ui.render_history(messages, {item.id: item for item in files}, client)
    _composer(client, selected)


def _projects(client: Client) -> list[Project]:
    try:
        return client.list_projects()
    except SkyeAPIError as exc:
        st.error(exc.message)
        return []


def _messages(client: Client, project_id: str) -> tuple[list[Any], list[ChatFile]]:
    try:
        return client.list_messages(project_id)
    except SkyeAPIError as exc:
        st.error(exc.message)
        return [], []


def _selected(projects: list[Project]) -> Project | None:
    if not projects:
        return None
    current = next((item for item in projects if item.id == st.session_state.selected_id), None)
    if current is None:
        current = next((item for item in projects if item.is_inbox), projects[0])
        st.session_state.selected_id = current.id
    return current


SIDEBAR_KEY = "skye_projects"


def _sidebar(client: Client, user: User, projects: list[Project], is_live: bool) -> None:
    state = st.session_state.get(SIDEBAR_KEY)
    query = str(state.get("query", "")) if isinstance(state, dict) else ""
    needle = query.strip().lower()
    visible = [
        project
        for project in projects
        if not needle or needle in f"{project.name} {project.last_message_preview}".lower()
    ]
    with st.sidebar:
        st.html(
            '<div style="display:flex;align-items:center;gap:.55rem;padding:.1rem .2rem .9rem">'
            f'<img src="{ui.asset("logo-mark.svg")}" style="width:30px;height:30px" alt="" />'
            + ui.wordmark(19)
            + "</div>"
        )
        event = project_list(
            projects=[_project_item(item) for item in visible],
            selected=st.session_state.selected_id,
            query=query,
            empty="Ничего не найдено." if needle else "Пока нет проектов.",
        )
        if event:
            _handle_project_event(client, event)

        with st.container(border=True):
            st.html(
                '<div style="font-size:.86rem;font-weight:600">'
                f"{_escape(user.name)}</div>"
                + (
                    f'<div class="sk-muted" style="font-size:.76rem">@{_escape(user.username)}</div>'
                    if user.username
                    else ""
                )
            )
            if (
                st.menu_button(
                    "Account",
                    ["Log out"],
                    format_func=lambda item: f":material/logout: {item}",
                    icon=":material/person:",
                    width="stretch",
                    key="account_menu",
                )
                == "Log out"
            ):
                if is_live:
                    try:
                        client.logout()
                    except SkyeAPIError:
                        pass
                else:
                    st.session_state.pop("demo_user", None)
                st.rerun()


def _header(client: Client, project: Project) -> None:
    with st.container(horizontal=True, vertical_alignment="center", gap="small"):
        st.html(ui.project_badge(project.icon, project.color, size=40))
        with st.container(width="stretch"):
            st.html(
                f'<div style="font-size:1.12rem;font-weight:650;letter-spacing:-.01em">'
                f"{_escape(project.name)}</div>"
            )
        if st.button(
            ":material/tune:",
            key=f"settings_{project.id}",
            type="tertiary",
            help="Project settings",
        ):
            st.session_state.edit_id = project.id
            st.rerun()


def _project_item(project: Project) -> dict[str, Any]:
    return {
        "id": project.id,
        "name": project.name,
        "icon": project.icon,
        "color": project.color,
        "pinned": project.pinned,
        "when": ui.format_when(project.last_message_at),
        "is_inbox": project.is_inbox,
        "deletable": project.deletable,
    }


def _handle_project_event(client: Client, event: dict[str, Any]) -> None:
    kind = event.get("type")
    project_id = event.get("id")
    if kind == "new":
        st.session_state.show_create = True
    elif kind == "select" and project_id:
        st.session_state.selected_id = str(project_id)
    elif kind == "pin" and project_id:
        _call(lambda: client.pin_project(str(project_id)))
    elif kind == "edit" and project_id:
        st.session_state.edit_id = str(project_id)
    elif kind == "delete" and project_id:
        st.session_state.delete_id = str(project_id)
    st.rerun()


def _composer(client: Client, project: Project) -> None:
    submission = st.chat_input(
        "Message Skye",
        accept_file="multiple",
        accept_audio=True,
        max_upload_size=25,
        submit_mode="stop",
    )
    if not submission:
        return
    text = (submission.text or "").strip()
    uploads: list[Upload] = []
    for item in submission.files or []:
        uploads.append(
            Upload(
                name=item.name,
                mime=item.type or "application/octet-stream",
                data=item.getvalue(),
            )
        )
    audio = getattr(submission, "audio", None)
    if audio is not None:
        uploads.append(Upload(name="voice.wav", mime="audio/wav", data=audio.getvalue()))
    if not text and not uploads:
        return

    with st.chat_message("user", avatar=ui.USER_AVATAR):
        if text:
            st.markdown(text)
        ui.render_uploads(uploads)

    _stream(client, project.id, text, uploads)
    st.rerun()


def _stream(client: Client, project_id: str, text: str, uploads: list[Upload]) -> None:
    with st.chat_message("assistant", avatar=ui.ASSISTANT_AVATAR):
        steps = st.container()
        files_area = st.container()
        text_area = st.empty()
        thought: Any = None
        tool_seen: set[str] = set()
        tool_output: dict[str, Any] = {}
        chunks: list[str] = []
        user_echoed = False
        try:
            for event in client.stream_reply(project_id, text, uploads):
                data = event.data
                if event.kind == "tool":
                    if thought is None:
                        with steps:
                            thought = st.status(
                                ":shimmer[Thinking]", type="compact", state="running"
                            )
                    tool_id = str(data.get("id") or data.get("name") or "tool")
                    if tool_id not in tool_seen:
                        with thought:
                            container = st.expander(
                                str(data.get("label") or "Step"),
                                icon=ui.tool_icon(data.get("name")),
                            )
                            tool_seen.add(tool_id)
                            with container:
                                if data.get("args"):
                                    st.code(str(data["args"]), language="json")
                                tool_output[tool_id] = st.empty()
                    if (
                        data.get("status") == "done"
                        and data.get("output")
                        and tool_id in tool_output
                    ):
                        tool_output[tool_id].caption(str(data["output"]))
                elif event.kind == "assistant":
                    chunks.append(str(data.get("text") or ""))
                    text_area.markdown("\n\n".join(part for part in chunks if part))
                elif event.kind == "user":
                    user_echoed = True
                elif event.kind in {"file", "image"}:
                    if not user_echoed:
                        continue
                    file = ChatFile.from_payload(data)
                    with files_area:
                        ui.render_file(file, client)
                elif event.kind == "notice":
                    st.info(str(data.get("message") or ""))
                elif event.kind == "error":
                    st.error(str(data.get("message") or "Something went wrong."))
        except SkyeAPIError as exc:
            st.error(exc.message)
        finally:
            if thought is not None:
                thought.update(label="Thought for a moment", state="complete")


# --------------------------------------------------------------------------- dialogs


@st.dialog("New project", on_dismiss=_dismiss_create)
def create_dialog(client: Client) -> None:
    with st.container(border=True):
        name = st.text_input("Name", placeholder="Name your project")
    with st.container(border=True):
        color = (
            st.pills(
                "Color",
                list(ui.PROJECT_COLORS),
                default="violet",
                format_func=lambda item: ui.COLOR_LABELS.get(item, item),
            )
            or "violet"
        )
        icon = (
            st.pills(
                "Icon",
                list(ui.PROJECT_ICONS),
                default="sparkles",
                format_func=lambda item: f":material/{ui.material_name(item)}: {ui.ICON_LABELS.get(item, item)}",
            )
            or "sparkles"
        )
    st.html(
        '<div style="display:flex;justify-content:center;padding:.4rem 0 .2rem">'
        + ui.project_badge(icon, color, size=56)
        + "</div>"
    )
    if st.button("Create project", icon=":material/auto_awesome:", type="primary", width="stretch"):
        if not name.strip():
            st.warning("Give the project a name.")
            return
        try:
            project = client.create_project(
                name=name, instructions="", icon=icon, color=color
            )
        except SkyeAPIError as exc:
            st.error(exc.message)
            return
        st.session_state.selected_id = project.id
        st.session_state.show_create = False
        st.rerun()


@st.dialog("Project settings", on_dismiss=_dismiss_edit)
def edit_dialog(client: Client, project: Project) -> None:
    tab_info, tab_look, tab_manage = st.tabs(["Info", "Look", "Manage"])
    with tab_info:
        with st.container(border=True):
            name = st.text_input("Name", value=project.name, disabled=project.is_inbox)
            instructions = st.text_area(
                "Instructions",
                value=project.instructions,
                height=150,
                max_chars=12_000,
                placeholder="e.g. You are a concise product coach.",
            )
    with tab_look:
        with st.container(border=True):
            color = (
                st.pills(
                    "Color",
                    list(ui.PROJECT_COLORS),
                    default=project.color,
                    format_func=lambda item: ui.COLOR_LABELS.get(item, item),
                )
                or project.color
            )
            icon = (
                st.pills(
                    "Icon",
                    list(ui.PROJECT_ICONS),
                    default=project.icon,
                    format_func=lambda item: f":material/{ui.material_name(item)}: {ui.ICON_LABELS.get(item, item)}",
                )
                or project.icon
            )
            st.html(
                '<div style="display:flex;justify-content:center;padding:.4rem 0 .2rem">'
                + ui.project_badge(icon, color, size=56)
                + "</div>"
            )
    with tab_manage:
        with st.container(border=True):
            if not project.is_inbox:
                label = "Unpin project" if project.pinned else "Pin project"
                if st.button(
                    label, icon=":material/push_pin:", width="stretch", key=f"pin_{project.id}"
                ):
                    _call(lambda: client.pin_project(project.id))
                    st.rerun()
            if st.button(
                "Reset this chat",
                icon=":material/restart_alt:",
                width="stretch",
                key=f"reset_{project.id}",
            ):
                _call(lambda: client.reset_project(project.id))
                st.rerun()
            if project.deletable and st.button(
                "Delete project",
                icon=":material/delete:",
                width="stretch",
                key=f"delete_{project.id}",
            ):
                st.session_state.delete_id = project.id
                st.session_state.edit_id = None
                st.rerun()

    if st.button(
        "Save changes", icon=":material/check:", type="primary", width="stretch"
    ):
        patch: dict[str, object] = {
            "instructions": instructions,
            "icon": icon,
            "color": color,
        }
        if not project.is_inbox:
            patch["name"] = name
        try:
            client.update_project(project.id, **patch)
        except SkyeAPIError as exc:
            st.error(exc.message)
            return
        st.session_state.edit_id = None
        st.rerun()


@st.dialog("Delete project", on_dismiss=_dismiss_delete)
def delete_dialog(client: Client, project: Project) -> None:
    with st.container(border=True):
        st.markdown(
            f"Удалить **{_escape(project.name)}**? История и файлы проекта будут стёрты."
        )
    with st.container(horizontal=True, horizontal_alignment="distribute"):
        if st.button("Cancel", width="stretch", key="cancel_delete"):
            st.session_state.delete_id = None
            st.rerun()
        if st.button(
            "Delete", icon=":material/delete:", type="primary", width="stretch", key="confirm_delete"
        ):
            try:
                client.delete_project(project.id)
            except SkyeAPIError as exc:
                st.error(exc.message)
                return
            if st.session_state.selected_id == project.id:
                st.session_state.selected_id = None
            st.session_state.delete_id = None
            st.rerun()


def _call(action: Any) -> None:
    try:
        action()
    except SkyeAPIError as exc:
        st.error(exc.message)


def _escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


main()
