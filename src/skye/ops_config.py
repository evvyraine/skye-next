"""Operator config: describe :class:`Settings`, validate edits, and apply overrides.

The panel is a thin, typed surface over the process environment. Every editable
field maps to one ``Settings`` field and one environment variable, so the panel
never invents configuration the runtime does not already understand.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast, get_args, get_origin, get_type_hints

from pydantic import ValidationError

from .config import Settings

FieldKind = Literal[
    "text",
    "secret",
    "int",
    "float",
    "bool",
    "select",
    "list",
    "path",
    "url",
]

GROUPS: tuple[str, ...] = (
    "Model provider",
    "Telegram",
    "Web chat",
    "Runtime",
    "Groups",
    "Media",
    "Sandbox",
    "Observability",
    "Advanced",
)

FIELD_GROUPS: dict[str, str] = {
    "openai_api_key": "Model provider",
    "openrouter_api_key": "Model provider",
    "skye_provider_api_key": "Model provider",
    "skye_provider_base_url": "Model provider",
    "skye_default_model": "Model provider",
    "skye_default_reasoning": "Model provider",
    "skye_image_model": "Model provider",
    "skye_image_api_key": "Model provider",
    "skye_image_base_url": "Model provider",
    "skye_audio_api_key": "Model provider",
    "skye_audio_base_url": "Model provider",
    "skye_transcription_model": "Model provider",
    "skye_speech_model": "Model provider",
    "skye_speech_voice": "Model provider",
    "skye_exa_api_key": "Model provider",
    "composio_api_key": "Model provider",
    "telegram_bot_token": "Telegram",
    "skye_owner_ids": "Telegram",
    "skye_web_origin": "Web chat",
    "skye_web_host": "Web chat",
    "skye_web_port": "Web chat",
    "skye_web_files_path": "Web chat",
    "telegram_login_client_id": "Web chat",
    "telegram_login_client_secret": "Web chat",
    "skye_max_turns": "Runtime",
    "skye_run_timeout_seconds": "Runtime",
    "skye_compaction_threshold_tokens": "Runtime",
    "skye_max_context_tokens": "Runtime",
    "skye_max_output_tokens": "Runtime",
    "skye_tpm_budget": "Runtime",
    "skye_max_concurrent_runs": "Runtime",
    "skye_max_attachment_bytes": "Runtime",
    "skye_group_context_messages": "Groups",
    "skye_group_context_message_chars": "Groups",
    "skye_group_context_total_chars": "Groups",
    "skye_media_group_settle_seconds": "Groups",
    "skye_native_media": "Media",
    "skye_youtube_transcript_max_chars": "Media",
    "skye_youtube_proxy_url": "Media",
    "skye_sandbox_enabled": "Sandbox",
    "skye_sandbox_image": "Sandbox",
    "skye_sandbox_timeout_seconds": "Sandbox",
    "skye_sandbox_allow_network": "Sandbox",
    "skye_sandbox_volume": "Sandbox",
    "skye_sandbox_work_dir": "Sandbox",
    "skye_sandbox_ttl_seconds": "Sandbox",
    "skye_sandbox_scope_bytes": "Sandbox",
    "skye_sandbox_total_bytes": "Sandbox",
    "skye_sandbox_max_concurrent": "Sandbox",
    "skye_sandbox_allowed_domains": "Sandbox",
    "skye_ops_enabled": "Observability",
    "skye_ops_capture_payloads": "Observability",
    "skye_ops_capture_media": "Observability",
    "skye_ops_media_path": "Observability",
    "skye_ops_log_retention_days": "Observability",
    "skye_ops_log_max_rows": "Observability",
    "skye_ops_trace_retention_days": "Observability",
    "skye_ops_trace_max_rows": "Observability",
    "skye_ops_max_body_bytes": "Observability",
    "skye_tracing": "Observability",
    "skye_database_path": "Advanced",
    "skye_base_prompt_path": "Advanced",
    "skye_proxy_url": "Advanced",
}

FIELD_LABELS: dict[str, str] = {
    "openai_api_key": "OpenAI key (legacy)",
    "openrouter_api_key": "OpenRouter key",
    "skye_provider_api_key": "Provider key",
    "skye_provider_base_url": "Provider base URL",
    "skye_default_model": "Default model",
    "skye_default_reasoning": "Reasoning effort",
    "skye_image_model": "Image model",
    "skye_image_api_key": "Image key",
    "skye_image_base_url": "Image base URL",
    "skye_audio_api_key": "Audio key",
    "skye_audio_base_url": "Audio base URL",
    "skye_transcription_model": "Transcription model",
    "skye_speech_model": "Speech model",
    "skye_speech_voice": "Speech voice",
    "skye_exa_api_key": "Exa key",
    "composio_api_key": "Composio key",
    "telegram_bot_token": "Bot token",
    "skye_owner_ids": "Owner user ids",
    "skye_web_origin": "Public origin",
    "skye_web_host": "Listen host",
    "skye_web_port": "Listen port",
    "skye_web_files_path": "Web files path",
    "telegram_login_client_id": "Telegram login client id",
    "telegram_login_client_secret": "Telegram login client secret",
    "skye_max_turns": "Max turns per run",
    "skye_run_timeout_seconds": "Run timeout",
    "skye_compaction_threshold_tokens": "Compaction threshold",
    "skye_max_context_tokens": "Max context tokens",
    "skye_max_output_tokens": "Max output tokens",
    "skye_tpm_budget": "Tokens per minute budget",
    "skye_max_concurrent_runs": "Max concurrent runs",
    "skye_max_attachment_bytes": "Max attachment size",
    "skye_group_context_messages": "Group context messages",
    "skye_group_context_message_chars": "Chars per group message",
    "skye_group_context_total_chars": "Group context total chars",
    "skye_media_group_settle_seconds": "Media group settle time",
    "skye_native_media": "Native media inputs",
    "skye_youtube_transcript_max_chars": "YouTube transcript cap",
    "skye_youtube_proxy_url": "YouTube proxy URL",
    "skye_sandbox_enabled": "Sandbox enabled",
    "skye_sandbox_image": "Sandbox image",
    "skye_sandbox_timeout_seconds": "Sandbox timeout",
    "skye_sandbox_allow_network": "Sandbox network access",
    "skye_sandbox_volume": "Sandbox volume",
    "skye_sandbox_work_dir": "Sandbox work dir",
    "skye_sandbox_ttl_seconds": "Workspace idle TTL",
    "skye_sandbox_scope_bytes": "Bytes per workspace",
    "skye_sandbox_total_bytes": "Bytes across workspaces",
    "skye_sandbox_max_concurrent": "Sandbox concurrency",
    "skye_sandbox_allowed_domains": "Reserved egress domains",
    "skye_ops_enabled": "Panel enabled",
    "skye_ops_capture_payloads": "Capture model payloads",
    "skye_ops_capture_media": "Capture images and files",
    "skye_ops_media_path": "Captured media path",
    "skye_ops_log_retention_days": "Log retention (days)",
    "skye_ops_log_max_rows": "Log row cap",
    "skye_ops_trace_retention_days": "Request retention (days)",
    "skye_ops_trace_max_rows": "Request row cap",
    "skye_ops_max_body_bytes": "Stored body cap",
    "skye_tracing": "Tracing",
    "skye_database_path": "Database path",
    "skye_base_prompt_path": "Base prompt path",
    "skye_proxy_url": "HTTP proxy URL",
}

FIELD_DESCRIPTIONS: dict[str, str] = {
    "openai_api_key": "Used only when no provider key is set. OpenRouter wins when both are set.",
    "openrouter_api_key": "Selects OpenRouter automatically and fills in its base URL.",
    "skye_provider_api_key": "Key for any OpenAI-compatible endpoint. Wins over the legacy keys.",
    "skye_provider_base_url": "API root only. A pasted /chat/completions suffix is dropped.",
    "skye_default_model": "Model slug used when a chat has no override.",
    "skye_default_reasoning": "Reasoning effort requested from the model.",
    "skye_image_model": "Used by generate_image and edit_image.",
    "skye_image_api_key": "Defaults to the provider key when empty.",
    "skye_image_base_url": "Defaults to the provider base URL when empty.",
    "skye_audio_api_key": "Defaults to the provider key when empty.",
    "skye_audio_base_url": "Defaults to the provider base URL when empty.",
    "skye_transcription_model": "Speech-to-text model for voice notes and uploads.",
    "skye_speech_model": "Text-to-speech model for send_voice.",
    "skye_speech_voice": "Default voice for text-to-speech.",
    "skye_exa_api_key": "Enables web_search and web_fetch. Without it those tools stay detached.",
    "composio_api_key": "Enables hosted app connectors through Composio.",
    "telegram_bot_token": "From BotFather. Required to start polling.",
    "skye_owner_ids": "Telegram user ids that bypass access rules and see this panel.",
    "skye_web_origin": "Public origin for Telegram login redirects and cookies.",
    "skye_web_host": "Interface the web server binds to.",
    "skye_web_port": "Port the web server listens on.",
    "skye_web_files_path": "Data directory for uploads and generated files.",
    "telegram_login_client_id": "From BotFather, for the Login Widget.",
    "telegram_login_client_secret": "From BotFather, for the Login Widget.",
    "skye_max_turns": "Upper bound on model turns inside one run.",
    "skye_run_timeout_seconds": "Wall-clock limit for a single run.",
    "skye_compaction_threshold_tokens": "Trim history past this estimate.",
    "skye_max_context_tokens": "Hard cap. Must exceed the compaction threshold.",
    "skye_max_output_tokens": "Reserved output budget per model call.",
    "skye_tpm_budget": "Shared tokens-per-minute ceiling. Must cover one maximum-size request.",
    "skye_max_concurrent_runs": "Runs allowed to speak to the provider at once.",
    "skye_max_attachment_bytes": "Largest accepted upload, in bytes.",
    "skye_group_context_messages": "New group messages attached to a run.",
    "skye_group_context_message_chars": "Per-message character cap for group context.",
    "skye_group_context_total_chars": "Total group context cap. Must exceed the per-message cap.",
    "skye_media_group_settle_seconds": "Quiet window before a photo album is processed.",
    "skye_native_media": "Send raw audio and documents instead of transcribing and extracting.",
    "skye_youtube_transcript_max_chars": "Character cap for YouTube transcripts.",
    "skye_youtube_proxy_url": "Optional proxy for YouTube. Cloud IPs are often blocked.",
    "skye_sandbox_enabled": "Runs shell_exec, python, read_file and write_file in Docker.",
    "skye_sandbox_image": "Docker image used for one-off sandbox commands.",
    "skye_sandbox_timeout_seconds": "Per-command time limit.",
    "skye_sandbox_allow_network": "Let sandbox containers reach the network.",
    "skye_sandbox_volume": "Shared Docker volume holding persistent workspaces.",
    "skye_sandbox_work_dir": "Mount point of the workspace volume inside containers.",
    "skye_sandbox_ttl_seconds": "Delete a workspace after this much idle time. 0 disables.",
    "skye_sandbox_scope_bytes": "Per-scope storage cap in bytes.",
    "skye_sandbox_total_bytes": "Storage cap across all scopes in bytes.",
    "skye_sandbox_max_concurrent": "Sandbox commands allowed at once.",
    "skye_sandbox_allowed_domains": "Reserved for sandbox egress filtering.",
    "skye_ops_enabled": "Serve the operator panel and its API to owner accounts.",
    "skye_ops_capture_payloads": "Store full model requests and responses.",
    "skye_ops_capture_media": "Store images and files seen in captured requests and responses.",
    "skye_ops_media_path": "Directory for captured media. Keep it on the data volume.",
    "skye_ops_log_retention_days": "Logs older than this are pruned.",
    "skye_ops_log_max_rows": "Hard cap on stored log rows.",
    "skye_ops_trace_retention_days": "Captured requests older than this are pruned.",
    "skye_ops_trace_max_rows": "Hard cap on stored request rows.",
    "skye_ops_max_body_bytes": "Largest request or response body stored as text.",
    "skye_tracing": "Provider-side tracing. Disabled by policy.",
    "skye_database_path": "Fixed at deploy time. Changing it while running is not supported.",
    "skye_base_prompt_path": "Override for the packaged BASE_PROMPT.md.",
    "skye_proxy_url": "Routes provider and Telegram traffic through a proxy.",
}

ADVANCED_FIELDS: frozenset[str] = frozenset(
    {
        "skye_compaction_threshold_tokens",
        "skye_max_context_tokens",
        "skye_max_output_tokens",
        "skye_tpm_budget",
        "skye_native_media",
        "skye_youtube_transcript_max_chars",
        "skye_youtube_proxy_url",
        "skye_media_group_settle_seconds",
        "skye_sandbox_volume",
        "skye_sandbox_work_dir",
        "skye_sandbox_ttl_seconds",
        "skye_sandbox_scope_bytes",
        "skye_sandbox_total_bytes",
        "skye_sandbox_allowed_domains",
        "skye_ops_media_path",
        "skye_ops_max_body_bytes",
        "skye_base_prompt_path",
        "skye_proxy_url",
    }
)

READ_ONLY_FIELDS: frozenset[str] = frozenset({"skye_database_path", "skye_web_files_path"})

_SECRET_MARKERS = ("key", "token", "secret", "password", "authorization")


@dataclass(frozen=True, slots=True)
class FieldSpec:
    key: str
    env: str
    label: str
    description: str
    group: str
    kind: FieldKind
    secret: bool
    read_only: bool
    advanced: bool
    choices: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    default: Any = None


def _resolve_hint(key: str, hints: dict[str, Any]) -> Any:
    return hints.get(key, str)


def _literal_choices(hint: Any) -> tuple[str, ...]:
    origin = get_origin(hint)
    if origin is Literal:
        return tuple(str(item) for item in get_args(hint))
    return ()


def _kind_for(key: str, hint: Any) -> FieldKind:
    choices = _literal_choices(hint)
    if choices:
        return "select"
    origin = get_origin(hint)
    if hint is bool or origin is bool:
        return "bool"
    if hint is int or origin is int:
        return "int"
    if hint is float or origin is float:
        return "float"
    if hint is Path:
        return "path"
    if origin in (frozenset, tuple, set, list):
        return "list"
    if any(marker in key for marker in _SECRET_MARKERS):
        return "secret"
    if key.endswith("_url"):
        return "url"
    return "text"


def _bounds(hint: Any, metadata: tuple[Any, ...]) -> tuple[float | None, float | None]:
    minimum: float | None = None
    maximum: float | None = None
    for item in metadata:
        low = getattr(item, "ge", None)
        high = getattr(item, "le", None)
        gt = getattr(item, "gt", None)
        lt = getattr(item, "lt", None)
        if low is not None:
            minimum = float(low)
        if gt is not None:
            minimum = float(gt)
        if high is not None:
            maximum = float(high)
        if lt is not None:
            maximum = float(lt)
    return minimum, maximum


def describe_fields() -> tuple[FieldSpec, ...]:
    """Return every editable settings field in a stable, grouped order."""
    hints = get_type_hints(Settings)
    specs: list[FieldSpec] = []
    for key, info in Settings.model_fields.items():
        hint = _resolve_hint(key, hints)
        metadata = tuple(info.metadata or ())
        minimum, maximum = _bounds(hint, metadata)
        default = info.get_default(call_default_factory=True)
        secret = any(marker in key for marker in _SECRET_MARKERS)
        specs.append(
            FieldSpec(
                key=key,
                env=key.upper(),
                label=FIELD_LABELS.get(key, key.replace("_", " ").capitalize()),
                description=FIELD_DESCRIPTIONS.get(
                    key, "Environment setting. A restart applies the change."
                ),
                group=FIELD_GROUPS.get(key, "Advanced"),
                kind=_kind_for(key, hint),
                secret=secret,
                read_only=key in READ_ONLY_FIELDS,
                advanced=key in ADVANCED_FIELDS,
                choices=_literal_choices(hint),
                minimum=minimum,
                maximum=maximum,
                default=default if isinstance(default, int | float | str | bool) else None,
            )
        )
    order = {name: index for index, name in enumerate(GROUPS)}
    specs.sort(key=lambda item: (order.get(item.group, len(GROUPS)), item.key))
    return tuple(specs)


def effective_values(settings: Settings) -> dict[str, Any]:
    return {key: getattr(settings, key) for key in Settings.model_fields}


def merge_changes(settings: Settings, changes: dict[str, Any]) -> dict[str, Any]:
    merged = effective_values(settings)
    merged.update(changes)
    return merged


def validate_values(
    settings: Settings, changes: dict[str, Any]
) -> tuple[Settings | None, dict[str, str]]:
    """Validate the proposed configuration without touching the running process.

    Returns ``(validated, errors)``. Errors map a field name to a plain,
    actionable message. Cross-field rules are enforced exactly as at startup.
    """
    merged = merge_changes(settings, changes)
    try:
        return Settings(**merged), {}
    except ValidationError as error:
        errors: dict[str, str] = {}
        for item in error.errors():
            location = item.get("loc") or ()
            field_name = str(location[0]) if location else "__root__"
            message = _friendly_error(item.get("msg", "Invalid value"), field_name)
            errors.setdefault(field_name, message)
        return None, errors


def _friendly_error(message: str, field_name: str) -> str:
    text = message.removeprefix("Value error, ").strip()
    label = FIELD_LABELS.get(field_name, field_name)
    if not text:
        return f"{label} is not valid."
    if text[:1].islower():
        text = text[:1].upper() + text[1:]
    return f"{label}: {text}."


def env_sources() -> set[str]:
    """Environment variables currently present in the process."""
    return {spec.env for spec in describe_fields() if os.environ.get(spec.env) is not None}


def coerce_change(spec: FieldSpec, raw: Any) -> Any:
    """Convert a JSON form value into the type the settings validator expects."""
    if raw is None:
        return None
    if spec.kind == "bool":
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}
    if spec.kind == "int":
        if isinstance(raw, bool) or not isinstance(raw, int | str):
            return raw
        text = str(raw).strip()
        return int(text) if text else None
    if spec.kind == "float":
        if isinstance(raw, bool) or not isinstance(raw, int | float | str):
            return raw
        text = str(raw).strip()
        return float(text) if text else None
    if spec.kind == "list":
        if isinstance(raw, str):
            return raw
        if isinstance(raw, list | tuple | set | frozenset):
            return list(raw)
        return raw
    if isinstance(raw, str):
        return raw.strip()
    return cast(Any, raw)
