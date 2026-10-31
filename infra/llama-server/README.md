# llama-server (Vulkan) + LiteLLM native reasoning proxy

Design for "many configurations, one resident model" on the Vega 7 rig. Rather than
creating clutter with dozens of `-*` suffixed model aliases (`utility-fast`, `utility-low`,
`reason-medium`, etc.), LiteLLM serves as the model catalog gateway that advertises
and supports native OpenAI `reasoning_effort` parameters on canonical model names.

## Native Reasoning Effort Support

Clients (such as GitHub Copilot, Cursor, OpenAI Python SDK, or cURL) query the canonical
base model name (`reason`, `reason-fast`, `utility`, etc.) and supply standard OpenAI
`reasoning_effort` levels:

| Effort Level | Behavior | Supported Models |
| :--- | :--- | :--- |
| `"none"` | Thinking is disabled completely; generates content immediately | `utility` (default), `reason`, `reason-fast`, `reason-low`, `reason-low-mtp` |
| `"low"` | Thinking enabled | `reason`, `reason-fast`, `reason-low`, `reason-low-mtp`, `utility` |
| `"medium"` | Thinking enabled (default for reasoning seats) | `reason` (default), `reason-fast` (default), `reason-low` (default), `reason-low-mtp` (default), `utility` |
| `"high"` | Thinking enabled | `reason`, `reason-fast`, `reason-low`, `reason-low-mtp`, `utility` |

### Separation of Responsibilities

- **LiteLLM layer** (`make_litellm_config.py`):
  Advertises `supports_reasoning: true`, `reasoning_effort_levels`, and `supported_openai_params: ["reasoning_effort"]`
  via `model_info`. For `utility`, defaults to `reasoning_effort: "none"` for deterministic non-thinking speed unless explicitly requested.
- **llama-server layer** (`models.ini`):
  Contains ONLY the canonical base models with short names and REQUIRED hardware configurations
  (flash attention, KV cache quantisation, context size, ubatch size, parallelism,
  and sampler plates). No preset budget rungs.

## Model Catalog

Canonical models exposed directly without `-*` suffixes:
- General / Chat: `chat`
- Coding: `code`, `fim`
- Reasoning: `reason`, `reason-fast`, `reason-low`, `reason-low-mtp`
- Structured & Utility: `utility`, `decision`
- Multimodal: `vision`, `ocr`
- Search & Audio: `embed`, `rerank`, `asr`, `asr-ab-test`

## Layout

| File | Role |
| --- | --- |
| `make_litellm_config.py` | Reads `models.ini`, emits the LiteLLM `model_list`: base passthrough aliases + the six-rung ladder per family targeting `openai/{family}` with `extra_body` |
| `entrypoint.sh` | Starts the router (`--models-preset`, `--models-max 1`), waits for `/health`, then shepherds LiteLLM in front |
| root `Dockerfile` / `docker-compose.yml` | Vulkan passthrough image (`/dev/dri` + RADV), latest llama.cpp Ubuntu x64 release binary |

## Run

```sh
RENDER_GID=$(stat -c %g /dev/dri/renderD128) docker compose up --build
```

Clients hit `http://localhost:8080/v1` with `Authorization: Bearer $LITELLM_MASTER_KEY`
(default `sk-cloak-local`), using canonical model names like `reason`, `reason-fast`,
or `utility`, optionally passing `"reasoning_effort": "none" | "low" | "medium" | "high"`.

## Probing Reasoning Effort

```sh
# 1. Non-thinking generation (immediate response, no thinking tokens)
curl -fsS http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${LITELLM_MASTER_KEY:-sk-cloak-local}" \
  -d '{"model":"utility","messages":[{"role":"user","content":"2+2?"}],"reasoning_effort":"none","max_tokens":256}' \
  | jq '{reasoning: (.choices[0].message.reasoning_content // "" | length), content: .choices[0].message.content}'

# 2. Reasoning-enabled generation (includes reasoning_content)
curl -fsS http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${LITELLM_MASTER_KEY:-sk-cloak-local}" \
  -d '{"model":"reason","messages":[{"role":"user","content":"2+2?"}],"reasoning_effort":"medium","max_tokens":256}' \
  | jq '{reasoning: (.choices[0].message.reasoning_content // "" | length), content: .choices[0].message.content}'
```

Compare `reasoning` length across `utility-fast` (0), `-low` (<= 256 tokens),
and `-max`.

## Notes & Rails

- **The run.sh thermal guard is host-side by design.** The container cannot
  reparent CPU boost / EPP / OverDrive clocks / `timeout_period`; keep `run.sh`
  if you need those rails while serving from Docker.
- **Web UI is off** (`--no-webui`) - `8082` is router-internal; uncomment its
  port mapping in `docker-compose.yml` to debug.
