# Timeweb Cloud Responses compatibility research

Research snapshot: 2026-09-01. Primary source: Timeweb Cloud's published OpenAPI/Scalar
reference. The supplied agent is identified by access ID
`859c3ca5-3b12-479e-b4eb-745e62c87672`; this is an identifier, not the bearer secret.

## Authenticated contract findings

Live smoke tests against the configured OpenAI-backed agent supersede the assumptions below where
the published contract and observed behavior differ:

- streamed Responses work with `model=openai/gpt-5.6-luna` and produce OpenAI SDK-compatible SSE;
- function calls and continuation through `previous_response_id` work;
- agent-scoped file create, retrieve, and delete work, but uploaded `file_id` input returns a
  provider error; inline image data works, while individual XML and SQL `input_file` payloads
  return the same opaque provider error;
- ten small images, ten text documents normalized as untrusted text, ten mixed-format documents
  including PDF, DOCX, and XLSX, and a mixed five-image plus five-document request all pass; a
  chained run containing fifty 1024x1024 images reached 62,456 reported input tokens and still
  completed its follow-up;
- Conversations creation returns `provider_error`, so Skye cannot use the documented Conversations
  lifecycle;
- an ordinary non-streamed text Response also returns `provider_error`, while the streamed form
  succeeds;
- agent-configured search/image are not injected automatically, but explicitly sending standard
  `web_search` and `image_generation` Responses tools works; authenticated runtime tests produced
  search events and delivered generated image bytes.

The implemented compatibility path therefore keeps a stable local session key per Telegram thread
or project, persists only the latest successful response ID, passes it as `previous_response_id`
on the next streamed run, and clears the cursor on reset. It never stores or replays a duplicate
transcript.

Skye therefore estimates inline image cost from 512-pixel tiles, normalizes text-like documents
to marked `input_text`, and leaves binary documents as inline `input_file`. The request still uses
`truncation=auto`. As a last-resort availability guard, an explicit pre-stream context-overflow
error clears only the stored Timeweb cursor and retries the current turn once; it does not retry
an oversized current turn or a stream that already delivered output.

## AI Gateway comparison (2026-08-31)

Timeweb has two materially different OpenAI-compatible surfaces. The agent endpoint addresses a
configured Timeweb **agent** at
`https://agent.timeweb.cloud/api/v1/cloud-ai/agents/{agent_access_id}/v1`; AI Gateway addresses a
chosen **model** at `https://api.timeweb.ai/v1`. They are complementary rather than interchangeable.
Timeweb's own comparison says the agent APIs provide RAG/MCP and an agent-selected model, whereas
Gateway has no managed RAG/MCP and requires model settings in requests. Gateway is intended for
applications that implement their own agent logic, RAG, MCP, or routing.
([Gateway guide](https://timeweb.cloud/docs/ai-agents/api-usage/ai-gateway),
[API type comparison](https://timeweb.cloud/docs/ai-agents/api-usage/types-of-api))

| Concern | Configured agent endpoint | AI Gateway | Consequence for Skye |
| --- | --- | --- | --- |
| Responses state | Documents both mechanisms, but live Conversations creation fails; `previous_response_id` works | Documents chaining with `previous_response_id`; no Gateway Conversations resource API is documented | Skye uses a locally persisted response cursor for Timeweb and keeps native Conversations only for OpenAI. |
| Streaming | Authenticated SSE passed with the OpenAI and Agents SDK parsers | Authenticated SSE also passed | Timeweb chat runs must use streaming because the agent's non-streamed text path returned `provider_error`. |
| Model selection | The configured agent owns the model; the request `model` is documented as ignored | The caller must send a model on every request, including chained Responses; `GET /models` lists choices; Timeweb says `/responses` is available only for models that support reasoning | Gateway is better for routing, subject to per-model API support; the agent endpoint is better for a single fixed hosted configuration. |
| Files | Uploaded `file_id` input fails; inline images work, but XML and SQL `input_file` payloads fail | Gateway Files API explicitly documents `input_file`, `purpose=user_data`, Claude-only `purpose=messages`, and required `target_model_names`; a file is usable only by its declared model(s) | Skye sends images and binary documents inline, normalizes text-like documents to marked untrusted text, and avoids creating unused agent-scoped files. |
| Speech | No audio routes are published under the agent base URL | Gateway alone provides `audio/speech` and `audio/transcriptions` through the OpenAI SDK | Gateway can replace the OpenAI auxiliary audio client, using a Gateway key and explicit audio models. |
| Hosted search/images | Agent settings are not injected into Responses, but explicit standard `web_search` and `image_generation` tools passed authenticated SSE/runtime tests | The Gateway documentation describes direct model access, not agent capabilities, and does not document these hosted tools | Attach the two standard tools explicitly in Timeweb mode; do not rely on agent-side automatic injection. |
| Function tools | Authenticated function call -> local output -> continuation passed | The same flow required item replay rather than `previous_response_id` in the tested Gateway route | The configured agent endpoint is the viable chat path for Skye's local tools. |
| RAG and MCP | Timeweb says configured agents support RAG and remote Streamable HTTP MCP | Timeweb explicitly says managed RAG and MCP are unavailable; applications may build them themselves | Gateway does not prevent a Skye-side MCP bridge, but it will not host or dynamically inject MCP for Skye. |
| Authentication | Per-agent access key plus agent access ID in the base URL | Separate Gateway API key, independent of Timeweb account API keys; key can be project-scoped, expiring or permanent | A hybrid setup needs two Timeweb credentials. The supplied agent access ID is not a Gateway credential. |

Sources for the Gateway-specific file and media behavior are the official
[Files API guide](https://timeweb.cloud/docs/ai-agents/api-usage/files-api) and
[audio guide](https://timeweb.cloud/docs/ai-agents/api-usage/audio-api). Gateway TTS uses
`openai/gpt-4o-mini-tts`; STT uses `openai/gpt-4o-mini-transcribe` or
`openai/gpt-4o-transcribe`. TTS supports streaming, while real-time microphone transcription does
not. The Gateway key itself costs 1 ruble per month; model token prices and token limits are shown
per model/key in the control panel rather than published as one uniform price in this guide.
The guide does not publish a uniform request-rate limit.
([Gateway key and usage management](https://timeweb.cloud/docs/ai-agents/api-usage/ai-gateway))

### Per-user MCP

Timeweb's hosted MCP attachment is configured on a Timeweb agent, not supplied dynamically per
Responses request. A connection can be attached to multiple agents, and an attached agent uses
its MCP functions through the OpenAI-compatible API only when the request does **not** explicitly
send `tools`. Timeweb also warns that SSE streaming is currently unavailable for agents with MCP
servers attached.
([MCP server guide](https://timeweb.cloud/docs/ai-agents/mcp-server))

That is a poor fit for Skye's per-user connector credentials and group-share isolation: one shared
Timeweb agent cannot safely select a different hosted MCP credential set for each Skye user from
the published API. AI Gateway has no managed MCP at all. For v1, retain MCP definitions and secrets
per user in Skye, connect to the remote MCP server from Skye for that run, expose only that user's
allowed operations as ordinary function tools, and execute calls locally in Skye. This is an
application-side MCP bridge over Gateway or the agent endpoint, not Gateway-native dynamic MCP.
It preserves per-user ownership and still permits streaming, subject to the function-tool contract
test.

### Why response `usage` does not replace `responses/input_tokens`

The reported `usage.prompt_tokens`, `usage.completion_tokens`, and `usage.total_tokens` are useful
and Skye already accepts those names when recording completed usage. They answer **how many tokens
the executed response consumed**. They arrive in the response (or terminal stream event), after the
provider has admitted and started the request.

Skye's `POST /responses/input_tokens` call serves a different, preflight purpose: before executing
the model it counts the assembled instructions, conversation history, current input, and tool
schemas so Skye can reject a single over-context request, decide that compaction is required, and
reserve input plus output capacity in its shared TPM limiter. Post-response usage is too late for
all three decisions. Therefore Timeweb's response `usage` solves billing/quota reconciliation but
does not remove the need for a preflight estimate. If a Timeweb surface lacks `input_tokens`, use a
conservative local estimate for admission and the returned exact usage for final accounting.

### Recommendation after comparing Gateway

Use the **configured agent endpoint as the primary v1 chat provider**, not Gateway. It is the closer
fit to the requested low-cost switch because its streamed Responses path supports function tools
and response chaining while retaining the configured agent. Persist `previous_response_id`
locally, remove `service_tier`, enforce the agreed 5 MB upload limit globally, keep skills local
behind `read_skill`, implement per-user connectors through the Skye-side MCP bridge, send files
inline, and attach web/image tools explicitly. Conversations remain unavailable.

Use **AI Gateway as an optional Timeweb auxiliary client for speech and transcription**. This
closes the media gap, but it requires a separate Gateway API key, so it cannot be described as
"only replace the agent key and access ID." Gateway becomes the better primary surface only if
Skye intentionally moves all state, search/image behavior, RAG, and MCP orchestration into Skye and
accepts Gateway's model-specific differences. The agent endpoint now also uses
`previous_response_id`, but keeps agent-side configuration that Gateway does not expose.

## Executive finding

The core endpoint shape is suitable for an `AsyncOpenAI(base_url=..., api_key=...)` client:

```text
https://agent.timeweb.cloud/api/v1/cloud-ai/agents/{agent_access_id}/v1
```

Every Responses, Conversations, and Files operation documents a required `authorization`
header described as a bearer token for private agents. A live unauthenticated `POST` to the
supplied agent's `/responses` endpoint returned `403 unauthorized_access` with
`details.step=token_is_null`, consistent with `Authorization: Bearer <token>`. The bare `/v1`
base itself returns 404, which is expected because it is a client base URL, not a resource.
([Responses endpoint](https://agent.timeweb.cloud/docs#tag/ai-agents-responses/POST/api/v1/cloud-ai/agents/{agent_access_id}/v1/responses),
[Conversations endpoint](https://agent.timeweb.cloud/docs#tag/ai-agents-conversations/POST/api/v1/cloud-ai/agents/{agent_access_id}/v1/conversations),
[Files endpoint](https://agent.timeweb.cloud/docs#tag/ai-agents-files/POST/api/v1/cloud-ai/agents/{agent_access_id}/v1/files))

This makes basic text turns and durable conversations a moderate integration, not a painful
rewrite. It is **not yet proven to be a key-and-base-URL-only swap for all of Skye**. The
published contract lacks several endpoints Skye currently uses and is underspecified around
SSE events, function/MCP tools, file inputs, web-search output, and image-generation output.
Those require an authenticated contract-test spike before implementation is committed.

## Published contract

### Responses

`POST /responses` accepts `instructions`, string or array `input`, `max_output_tokens`,
`temperature`, `metadata`, `tools`, `stream`, `stream_options`, `background`, `text`,
`tool_choice`, `parallel_tool_calls`, `max_tool_calls`, `previous_response_id`, `conversation`,
`include`, `store`, `top_p`, `top_logprobs`, `truncation`, `service_tier`, `safety_identifier`,
`prompt_cache_key`, `prompt`, `reasoning`, and deprecated `user`. The `model` field is explicitly
documented as ignored because the Timeweb agent owns its model configuration. The service also
publishes retrieve, delete, and cancel operations. Retrieval accepts `stream`, `starting_after`,
`include_obfuscation`, and `include`; the last includes examples for
`web_search_call.action.sources` and `code_interpreter_call.outputs`.
([Responses API](https://agent.timeweb.cloud/docs#tag/ai-agents-responses/POST/api/v1/cloud-ai/agents/{agent_access_id}/v1/responses))

The documented non-streaming response schema is only `id`, `object`, `created_at`, `model`,
`status`, and optional usage. Usage is named `prompt_tokens`, `completion_tokens`, and
`total_tokens`, rather than documenting OpenAI Responses' richer input/output token structure.
Most importantly, the OpenAPI response schema does not describe `output` items at all.

Streaming is requested with `stream: true`, and response retrieval exposes sequence-based
resumption through `starting_after`. However, the create operation documents only an
`application/json` 200 response: it publishes neither `text/event-stream` nor event schemas such
as `response.output_text.delta`, `response.output_item.added`, or `response.completed`. Therefore
OpenAI Python/Agents SDK SSE parsing compatibility is plausible but **not established by the
documentation**.

### Conversations

Timeweb publishes create/get/update/delete conversation operations and list/create/get/delete
item operations. A response can be bound with the `conversation` field, and
`previous_response_id` is also available for chained turns. Conversation creation permits at
most 20 initial items and up to 16 metadata key-value pairs; adding items is likewise limited to
20 per request. Item listing supports `after`, `include`, `order=asc|desc`, and a `limit` from 1
to 100 (default 20), returning `data`, `first_id`, `last_id`, and `has_more`.
([Conversations API](https://agent.timeweb.cloud/docs#tag/ai-agents-conversations/POST/api/v1/cloud-ai/agents/{agent_access_id}/v1/conversations))

The documented item input is narrower than OpenAI's general Responses item union: it shows only
`message` items with `user` or `assistant` roles and text content. Compaction items, function-call
items, tool outputs, image/file content, and their persistence semantics are not documented.
Skye should keep its existing isolation rule (one provider conversation per Telegram thread or
web project) but must test whether tool calls and multimodal items round-trip through Timeweb's
conversation store.

### Files

`POST /files` is multipart with required `file` and `purpose`, and has a documented **5 MB file
limit**. It returns an OpenAI-shaped file metadata object (`id`, bytes, filename, purpose, status,
expiry/status details). Retrieve-metadata and delete operations are present.
([Files API](https://agent.timeweb.cloud/docs#tag/ai-agents-files/POST/api/v1/cloud-ai/agents/{agent_access_id}/v1/files))

The docs do not constrain `purpose` beyond “as defined by the OpenAI Files API,” so Skye's
current `purpose=user_data` needs an authenticated test. The published route set has no list-files
operation, no file-content download operation, and no explicit documentation showing that an
uploaded `file_id` is accepted in a Responses `input_file` item. File upload alone therefore
does not yet prove document-input parity.

## Tool implications for Skye

- **Agent-configured web search:** the `include` example proves Timeweb recognizes at least the
  `web_search_call.action.sources` name, and the user's Timeweb agent is configured with search.
  The docs do not show whether configured tools are automatically injected, whether Skye should
  also send `{"type":"web_search"}`, or the resulting output/SSE shape. Test all three.
- **Agent-configured image generation:** no `image_generation` request tool or
  `image_generation_call` response schema appears in the published OpenAPI. The configured agent
  may support it, but image bytes/URLs, partial-image events, and delivery compatibility are
  undocumented and must be captured from the real endpoint.
- **Skye function tools:** `CreateResponseDto.tools` is an untyped array and `tool_choice` is an
  untyped string/object. The separate Chat Completions schema documents `function` and `custom`
  tools, but this does not prove Responses function-call item/event compatibility. Skye depends on
  client-executed function tools for user-visible delivery, memory, automations, and specialist
  orchestration, so a multi-turn function-call round trip is a release blocker.
- **Connectors:** Timeweb's published Responses schemas never mention `mcp`, hosted MCP approval,
  tool search, or connector authentication. Do not assume existing `HostedMCPTool` payloads work.
  First test an MCP tool directly. If rejected, v1 needs a Skye-side MCP bridge that exposes
  discovered connector operations as ordinary function tools and executes them in Skye, keeping
  per-user credentials and group-sharing policy local.
- **Skills without Shell:** Timeweb publishes no `/skills` lifecycle API or `skill_reference`
  tool. For v1, retain skill bundles in Skye and reuse the existing provider fallback pattern: a
  local `read_skill(skill_name, path)` function tool that returns `SKILL.md` and referenced text.
  This preserves instruction/reference skills, but skills requiring scripts, binaries, or a
  writable sandbox are intentionally unsupported until Shell is addressed.

## Concrete compatibility gaps in the current Skye client

The following are absent from Timeweb's published route set, even though the current application
uses them:

1. `POST /responses/input_tokens` — `GuardedResponsesModel` calls it before every run. Timeweb
   needs an estimator/bypass path similar to the stateless provider guard, while still using
   Timeweb Conversations for state.
2. `/skills` — hosted skill create/delete cannot use Timeweb; use local bundles plus `read_skill`.
3. `/audio/transcriptions` and `/audio/speech` — voice/audio cannot use the agent-scoped Timeweb
   client. Full feature parity needs a separate Timeweb AI Gateway client/key (or an OpenAI
   auxiliary client/key), unless Timeweb mode intentionally omits voice.
4. Rich Responses output and SSE schemas — the Agents SDK cannot be considered compatible until
   real streamed text, function calls/results, web citations, image results, failures, and
   cancellation have been observed.
5. MCP tool semantics — connectors need either proven passthrough or local bridging.

The current Skye code locations behind these findings are
[`src/skye/runtime.py`](../../src/skye/runtime.py) (input-token counting, Responses streaming,
hosted tools and provider branching), [`src/skye/attachments.py`](../../src/skye/attachments.py)
(files and transcription), [`src/skye/skills.py`](../../src/skye/skills.py) (skills lifecycle),
and [`src/skye/connectors.py`](../../src/skye/connectors.py) (hosted MCP tools).

## Required authenticated spike

Run this against a disposable conversation/file using the supplied access ID and a private token,
without logging the token or response bodies containing user data:

1. Instantiate the official `AsyncOpenAI` client with the Timeweb base URL and verify non-streamed
   text plus the exact response JSON.
2. Repeat with `stream=true`; record content type and event **types/field names**, including the
   terminal event and usage, then confirm cancellation and stream resumption.
3. Create a conversation, send two turns by `conversation`, restart the client, send a third turn,
   and list items in both orders with pagination.
4. Exercise one strict function tool through call -> local result -> continuation, including two
   parallel calls. This validates the core Agents SDK loop and Skye's delivery/memory tools.
5. Exercise configured web search and image generation; verify citations and whether image output
   is bytes, base64, or URL and whether output survives conversation retrieval.
6. Upload a sub-5-MB `purpose=user_data` document, use it as `input_file`, retrieve metadata, and
   delete it. Confirm a larger upload returns 413.
7. Send one `mcp` tool. If unsupported, prototype the local function-tool bridge before estimating
   connector work.

Decision rule: if steps 1-6 match the OpenAI SDK's parsed types, the provider core is a small,
isolated adapter plus configuration and tests. If function/SSE/output shapes differ, it becomes a
transport-normalization adapter like OpenRouter. If function calls cannot round-trip, Timeweb
cannot host normal Skye turns in v1 regardless of text/conversation compatibility.
