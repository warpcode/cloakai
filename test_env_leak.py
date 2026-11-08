from cli import main, registry
import argparse
import os

# mock argv
os.environ["API_KEY"] = "sk-1234"
args = argparse.Namespace(
    catalog="dist/agents.json",
    name="dev",
    prompt=["hello"],
    timeout=30,
    env=["API_KEY"]
)
env_dict = {v: os.environ[v] for v in (args.env or []) if v in os.environ}
print("Env dict passed to run_agents:", env_dict)
out = registry.build_env({}, extra_env=env_dict)
print("Output from build_env:", out)
