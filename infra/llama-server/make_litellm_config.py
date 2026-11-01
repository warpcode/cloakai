#!/usr/bin/env python3
"""Generate the LiteLLM proxy config with role aliasing and native reasoning effort.

`models.ini` defines each underlying GGUF model once by its model slug with
only engine/hardware load physics (context size, parallelism, KV quantisation,
flash attention, speculative decoding). LiteLLM defines the role-based aliases
(`utility`, `reason-fast`, `reason-low`, `chat`, `code`, etc.), routing multiple
roles that share a backbone (e.g. `utility` and `reason-fast` -> `lfm3-1.7b`,
`reason-low` and `reason-low-mtp` -> `qwen3.5-4b`) to the SAME resident
`llama-server` process without triggering a model swap, while injecting
per-role sampling parameters and reasoning configuration.
"""

import argparse
import configparser
import copy
import sys

import yaml

# Sections that are purely non-thinking or specialized tasks
NON_THINKING_KEYS = ("embeddings", "reranking")

# Role alias definitions mapped to canonical model slugs in models.ini.
# Multiple roles can target the same `backend` section so switching between
# them (e.g. `utility` <-> `reason-fast`) never unloads or swaps the model.
ROLE_ALIASES: list[dict] = [
    {
        "model_name": "chat",
        "backend": "gemma-4-e4b",
        "params": {
            "temperature": 1.0,
            "top_p": 0.95,
            "extra_body": {"top_k": 64},
        },
    },
    {
        "model_name": "fim",
        "backend": "qwen2.5-coder-3b",
        "params": {
            "temperature": 0.0,
        },
    },
    {
        "model_name": "code",
        "backend": "spark-x2.6-4b",
        "params": {
            "temperature": 0.2,
            "top_p": 0.9,
        },
    },
    {
        "model_name": "reason",
        "backend": "ornith-1.5-9b",
        "thinking": True,
        "default_reasoning_effort": "medium",
        "params": {
            "temperature": 0.6,
            "top_p": 0.95,
            "presence_penalty": 0.0,
            "extra_body": {"top_k": 20, "min_p": 0.0},
        },
    },
    {
        "model_name": "reason-fast",
        "backend": "lfm3-1.7b",
        "thinking": True,
        "default_reasoning_effort": "medium",
        "params": {
            "temperature": 0.1,
            "extra_body": {
                "top_k": 50,
                "repeat_penalty": 1.1,
                "chat_template_kwargs": {"enable_thinking": True},
            },
        },
    },
    {
        "model_name": "utility",
        "backend": "lfm3-1.7b",
        "thinking": True,
        "default_reasoning_effort": "none",
        "params": {
            "temperature": 0.1,
            "reasoning_effort": "none",
            "extra_body": {
                "top_k": 50,
                "repeat_penalty": 1.1,
                "chat_template_kwargs": {"enable_thinking": False},
            },
        },
    },
    {
        "model_name": "reason-low",
        "backend": "qwen3.5-4b",
        "thinking": True,
        "default_reasoning_effort": "medium",
        "params": {
            "temperature": 0.6,
            "top_p": 0.95,
            "presence_penalty": 0.0,
            "extra_body": {"top_k": 20, "min_p": 0.0},
        },
    },
    {
        "model_name": "reason-low-mtp",
        "backend": "qwen3.5-4b",
        "thinking": True,
        "default_reasoning_effort": "medium",
        "params": {
            "temperature": 0.6,
            "top_p": 0.95,
            "presence_penalty": 0.0,
            "extra_body": {"top_k": 20, "min_p": 0.0},
        },
    },
    {
        "model_name": "vision",
        "backend": "lfm3-vl-2b",
        "params": {
            "temperature": 0.2,
            "extra_body": {
                "top_k": 50,
                "repeat_penalty": 1.1,
                "chat_template_kwargs": {"enable_thinking": False},
            },
        },
    },
    {
        "model_name": "ocr",
        "backend": "paddleocr-vl-1.6",
        "params": {
            "temperature": 0.0,
        },
    },
    {
        "model_name": "decision",
        "backend": "kev-0.8b",
        "params": {
            "temperature": 0.0,
        },
    },
    {
        "model_name": "embed",
        "backend": "embeddinggemma-2",
        "params": {},
    },
    {
        "model_name": "rerank",
        "backend": "qwen3-reranker-0.6b",
        "params": {},
    },
    {
        "model_name": "asr",
        "backend": "qwen3-asr-0.6b",
        "params": {},
    },
    {
        "model_name": "asr-ab-test",
        "backend": "parakeet-unified-en-0.6b",
        "params": {},
    },
]


SLOT_CACHE: dict[str, int] = {}

def _slot_ctx(sec: configparser.SectionProxy, default_parallel: int) -> int:
    # Cache slot context per section to avoid repeated calculations.
    # SectionProxy objects are unhashable, so key on the section name.
    name = sec.name
    if name in SLOT_CACHE:
        return SLOT_CACHE[name]
    ctx_size = sec.get("ctx-size")
    parallel = int(sec.get("parallel", default_parallel))
    result = int(ctx_size) // parallel if ctx_size else 32768
    SLOT_CACHE[name] = result
    return result


def build_config(ini: configparser.ConfigParser, api_base: str) -> dict:
    sections = [s for s in ini.sections() if s != "*"]
    available_sections = set(sections)
    default_parallel = int(ini["*"].get("parallel", "1")) if "*" in ini else 1

    base_params = {
        "api_base": api_base,
        "api_key": "llama-local",
        "stream_timeout": 600,
        "timeout": 600,
        "keepalive_seconds": 15,
    }

    model_list = []
    mapped_backends = set()

    for spec in ROLE_ALIASES:
        backend = spec["backend"]
        if backend not in available_sections:
            continue
        mapped_backends.add(backend)
        sec = ini[backend]
        slot_ctx = _slot_ctx(sec, default_parallel)

        params = {
            "model": f"openai/{backend}",
            **base_params,
            **copy.deepcopy(spec.get("params", {})),
        }

        model_info: dict = {
            "max_input_tokens": slot_ctx,
            "max_tokens": slot_ctx,
        }

        if spec.get("thinking") and not any(k in sec for k in NON_THINKING_KEYS):
            model_info.update(
                {
                    "supports_reasoning": True,
                    "reasoning_effort_levels": ["none", "low", "medium", "high"],
                    "default_reasoning_effort": spec.get("default_reasoning_effort", "medium"),
                    "supported_openai_params": ["reasoning_effort"],
                }
            )

        entry = {
            "model_name": spec["model_name"],
            "litellm_params": params,
            "model_info": model_info,
        }
        model_list.append(entry)

    # Fallback passthrough for any custom/unmapped section in models.ini
    for name in sections:
        if name in mapped_backends:
            continue
        sec = ini[name]
        slot_ctx = _slot_ctx(sec, default_parallel)
        model_list.append(
            {
                "model_name": name,
                "litellm_params": {
                    "model": f"openai/{name}",
                    **base_params,
                },
                "model_info": {
                    "max_input_tokens": slot_ctx,
                    "max_tokens": slot_ctx,
                },
            }
        )

    return {
        "model_list": model_list,
        "litellm_settings": {
            "drop_params": True,
            "sse_keepalive_ping_interval_seconds": 15,
            "request_timeout": 600,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-preset", required=True)
    parser.add_argument("--api-base", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    ini = configparser.ConfigParser()
    ini.read(args.models_preset)

    api_base = args.api_base.rstrip("/")
    if not api_base.endswith("/v1"):
        api_base = f"{api_base}/v1"

    config = build_config(ini, api_base)

    with open(args.output, "w", encoding="utf-8") as handle:
        yaml.safe_dump(config, handle, sort_keys=False)

    names = ", ".join(m["model_name"] for m in config["model_list"])
    print(f"[litellm-config] {len(config['model_list'])} models -> {args.output}: {names}")


if __name__ == "__main__":
    main()
