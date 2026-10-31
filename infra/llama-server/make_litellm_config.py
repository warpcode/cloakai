#!/usr/bin/env python3
"""Generate the LiteLLM proxy config that exposes low/medium/high reasoning
modes for every llama-server router group defined in models.ini.

Design (matches the 2026-10-30 discussion): llama-server has NO native
per-request reasoning toggle (chat_template_kwargs enable_thinking is the
request-level lever still honoured; discussion #23351 tracks a first-class
field), so the proxy injects per-request fields via extra_body and routes
every alias to the SAME llama-server model. One resident copy, many modes.

Sections with `embeddings =` or `reranking =` get a base alias only - thinking
modes are meaningless for embedding/rerank lanes. OCR/vision/asr keep the
base alias plus modes (harmless; unused template kwargs are ignored by Jinja).
"""

import argparse
import configparser
import copy

import yaml

# <group>-low/medium/high -> per-request body injected by LiteLLM.
# Kept minimal and honest: low = scaffold off, medium = scaffold on at vendor
# default effort, high = scaffold on with explicit high effort. Token-budget
# caps (the true --reasoning-budget 0/128/4096 ladder) remain per-section
# startup config in models.ini, because the server does not accept a
# per-request budget field yet (#23351). When it does, extend MODE_BODIES with
# {"reasoning_budget": N} and regenerate.
MODE_BODIES = {
    "low": {"chat_template_kwargs": {"enable_thinking": False}},
    "medium": {"chat_template_kwargs": {"enable_thinking": True}},
    "high": {
        "chat_template_kwargs": {"enable_thinking": True},
        "reasoning_effort": "high",
    },
}

NON_THINKING_KEYS = ("embeddings", "reranking")

# Sections whose NAME already encodes lane behaviour get base-alias only:
# [reason-low] is a baked throughput tier, [reason-low-mtp] a spec-decode
# variant, [X-ab-test] an A/B candidate. Double suffixes (reason-low-low) would
# be pure noise in client model pickers.
EXCLUDED_NAME_SUFFIXES = ("-ab-test",)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-preset", required=True)
    parser.add_argument("--api-base", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    ini = configparser.ConfigParser()  # '#' and ';' lines are treated as comments
    ini.read(args.models_preset)
    groups = [s for s in ini.sections() if s != "*"]

    base = {"api_base": args.api_base, "api_key": "llama-local"}
    model_list = []
    for group in groups:
        # Base alias: plain passthrough, untouched per-seat sampler defaults.
        model_list.append(
            {
                "model_name": group,
                "litellm_params": {"model": f"openai/{group}", **base},
            }
        )
        if any(k in ini[group] for k in NON_THINKING_KEYS):
            continue
        if group.endswith(EXCLUDED_NAME_SUFFIXES):
            continue
        if group.rsplit("-", 1)[-1] in MODE_BODIES:
            continue
        for mode, body in MODE_BODIES.items():
            model_list.append(
                {
                    "model_name": f"{group}-{mode}",
                    "litellm_params": {
                        "model": f"openai/{group}",
                        # deepcopy so safe_dump writes each block literally
                        # instead of emitting YAML anchors (&id001 / *id001)
                        "extra_body": copy.deepcopy(body),
                        **base,
                    },
                }
            )

    config = {
        "model_list": model_list,
        "litellm_settings": {"drop_params": True},
    }
    with open(args.output, "w", encoding="utf-8") as handle:
        yaml.safe_dump(config, handle, sort_keys=False)

    names = ", ".join(m["model_name"] for m in model_list)
    print(f"[litellm-config] {len(model_list)} aliases -> {args.output}: {names}")


if __name__ == "__main__":
    main()
