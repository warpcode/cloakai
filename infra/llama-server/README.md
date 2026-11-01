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
  Defines all role-based model aliases (`chat`, `code`, `fim`, `reason`, `reason-fast`, `utility`, `reason-low`, `vision`, `ocr`, `decision`, `embed`, `rerank`, `asr`), routing multiple roles that share an underlying GGUF model (e.g. `utility` and `reason-fast` -> `openai/lfm3-1.7b`, `reason-low` and `reason-low-mtp` -> `openai/qwen3.5-4b`) to the **same resident `llama-server` section** so switching between them never triggers a model swap. Also injects per-role sampling parameters (`temperature`, `top_p`, `top_k`, `repeat_penalty`, `min_p`, `presence_penalty`, `chat_template_kwargs`) and advertises `supports_reasoning: true`, `reasoning_effort_levels`, and `supported_openai_params: ["reasoning_effort"]` via `model_info`.
- **llama-server layer** (`models.ini`):
  Contains a **single entry per unique underlying model** named by model slug (`[lfm3-1.7b]`, `[qwen3.5-4b]`, `[ornith-1.5-9b]`, etc.) with ONLY the core engine/hardware load physics (flash attention, KV cache quantisation, context size, batch/ubatch size, parallelism, and speculative decoding).

## Model Catalog & Backend Mapping

| Role Alias (LiteLLM) | Backend Section (`models.ini`) | Underlying GGUF |
| :--- | :--- | :--- |
| `chat` | `[gemma-4-e4b]` | `unsloth/gemma-4-E4B-it-GGUF:IQ4_XS` |
| `fim` | `[qwen2.5-coder-3b]` | `Qwen/Qwen2.5-Coder-3B-Instruct-GGUF:Q4_K_M` |
| `code` | `[spark-x2.6-4b]` | `bartowski/Spark-X2.6-4B-GGUF:Q5_K_M` |
| `reason` | `[ornith-1.5-9b]` | `protoLabsAI/Ornith-1.5-9B-MTP-GGUF:IQ4_XS` |
| `reason-fast`, `utility` | `[lfm3-1.7b]` | `LiquidAI/LFM3-1.7B-GGUF:QAD-Q4_0` |
| `reason-low`, `reason-low-mtp` | `[qwen3.5-4b]` | `unsloth/Qwen3.5-4B-MTP-GGUF:UD-Q4_K_XL` |
| `vision` | `[lfm3-vl-2b]` | `LiquidAI/LFM3-VL-2B-GGUF:QAD-Q4_0` |
| `ocr` | `[paddleocr-vl-1.6]` | `PaddlePaddle/PaddleOCR-VL-1.6-GGUF:Q8_0` |
| `decision` | `[kev-0.8b]` | `ggml-org/Kev-0.8B-GGUF:Q8_0` |
| `embed` | `[embeddinggemma-2]` | `ggml-org/embeddinggemma-2-GGUF:Q8_0` |
| `rerank` | `[qwen3-reranker-0.6b]` | `mradermacher/Qwen3-Reranker-0.6B-GGUF:Q8_0` |
| `asr` | `[qwen3-asr-0.6b]` | `ggml-org/Qwen3-ASR-0.6B-GGUF:Q8_0` |
| `asr-ab-test` | `[parakeet-unified-en-0.6b]` | `handy-computer/parakeet-unified-en-0.6b-gguf:Q5_K_M` |

## Layout

| File | Role |
| --- | --- |
| `make_litellm_config.py` | Reads `models.ini`, emits the LiteLLM `model_list` mapping role aliases (`utility`, `reason-fast`, etc.) to their canonical `openai/{model-slug}` backend with per-role sampling and reasoning params |
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
