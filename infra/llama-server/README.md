# llama-server mode aliases (Vulkan) + LiteLLM proxy

Design for "many modes, one resident model" on the Vega 7 rig. llama-server has
no native per-request reasoning toggle yet (upstream discussion #23351; the
`enable_thinking` route via `chat_template_kwargs` is the request-level lever
that still works, and token-budget capping stays startup-side), so the

`low/medium/high` effect is replicated by LiteLLM injecting per-request
`extra_body` fields and routing every alias to the **same** llama-server
process. Nothing is ever loaded twice or unloaded to switch modes.

## Layout

| File | Role |
| --- | --- |
| `make_litellm_config.py` | Reads `models.ini` sections, emits the LiteLLM `model_list` with `-low` / `-medium` / `-high` aliases per group |
| `entrypoint.sh` | Starts the router (`--models-preset`, `--models-max 1`), waits for `/health`, then shepherds LiteLLM in front |
| root `Dockerfile` / `docker-compose.yml` | Vulkan passthrough image (`/dev/dri` + RADV), latest llama.cpp Ubuntu x64 release binary |

## Run

```sh
RENDER_GID=$(stat -c %g /dev/dri/renderD128) docker compose up --build
```

Clients hit `http://localhost:8080/v1` with `Authorization: Bearer $LITELLM_MASTER_KEY`
(default `sk-cloak-local`), using model names like `reason`, `reason-low`,
`reason-medium`, `reason-high`. Embedding/rerank groups get their plain alias
only (`embed`, `rerank`).

## Probe that a mode actually toggles

```sh
curl -fsS http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${LITELLM_MASTER_KEY:-sk-cloak-local}" \
  -d '{"model":"reason-low","messages":[{"role":"user","content":"2+2?"}],"max_tokens":256}' \
  | jq '.choices[0].message'
```

Count `reasoning_content` tokens on `reason-low` vs `reason-high`. If a
template family ignores per-request `enable_thinking` (the >= b8322 Qwen3.5
deprecation pattern), all three modes collapse to the group default - the
probe makes that visible instead of assumed.

## Honest caveats

- **Token-budget caps are still not per-request.** True `--reasoning-budget`
  ladders (like `[utility]`'s budget 128) remain per-section startup config in
  `models.ini`. When upstream lands a first-class per-request field (extend
  `MODE_BODIES` in `make_litellm_config.py` at that point).
- **The run.sh thermal guard is host-side by design.** The container cannot
  reparent CPU boost / EPP / OverDrive clocks / `timeout_period`; keep `run.sh`
  if you need those rails while serving from Docker.
- **Web UI is off** (`--no-webui`) - `8082` is router-internal; uncomment its
  port mapping in `docker-compose.yml` to debug.
