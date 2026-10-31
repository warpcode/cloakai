#!/usr/bin/env python3
"""Generate the LiteLLM proxy config with native reasoning effort support.

Exposes only the canonical base models defined in models.ini (no -* suffixes).
Thinking-capable families advertise native reasoning support and effort levels
via model_info so standard OpenAI reasoning clients (e.g. Copilot, Cursor) can
pass reasoning_effort directly ("none", "low", "medium", "high").
"""

import argparse
import configparser
import sys

import yaml

# Sections that have reasoning / thinking capabilities
THINKING_MODELS = {
    "reason",
    "reason-fast",
    "reason-low",
    "reason-low-mtp",
    "utility",
}

# Sections that are purely non-thinking or specialized tasks
NON_THINKING_KEYS = ("embeddings", "reranking")


def build_config(ini: configparser.ConfigParser, api_base: str) -> dict:
    sections = [s for s in ini.sections() if s != "*"]
    base_params = {"api_base": api_base, "api_key": "llama-local"}

    model_list = []
    for name in sections:
        params = {"model": f"openai/{name}", **base_params}

        # Utility defaults to deterministic non-thinking unless reasoning_effort is explicitly requested
        if name == "utility":
            params["reasoning_effort"] = "none"

        entry = {
            "model_name": name,
            "litellm_params": params,
        }

        # Advertise native reasoning support on thinking-capable models
        if name in THINKING_MODELS and not any(k in ini[name] for k in NON_THINKING_KEYS):
            entry["model_info"] = {
                "supports_reasoning": True,
                "reasoning_effort_levels": ["none", "low", "medium", "high"],
                "default_reasoning_effort": "none" if name == "utility" else "medium",
                "supported_openai_params": ["reasoning_effort"],
            }

        model_list.append(entry)

    return {
        "model_list": model_list,
        "litellm_settings": {"drop_params": True},
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
