import subprocess
import os
import sys

def main():
    os.environ["SUPER_SECRET_TOKEN"] = "my_token_value_123"

    # Check what argv gets built for CLI main
    from cli import main
    import argparse

    args = argparse.Namespace(
        command="agents",
        catalog="dist/agents.json",
        name="dev",
        prompt=["hello"],
        timeout=30,
        env=["SUPER_SECRET_TOKEN"]
    )

    # We will patch reg.run_agents to see what args it receives
    from cli import registry as reg
    original_run_agents = reg.run_agents

    received_env = {}

    def mock_run_agents(doc, name, prompt, timeout, env):
        nonlocal received_env
        received_env = env
        return 0

    reg.run_agents = mock_run_agents

    main._cmd_agents(args)

    print("Received env:", received_env)

    # Let's see what build_env does
    argv = reg.build_argv(reg.load("dist/agents.json"), "dev", "agents", extra_env=received_env)

    for a in argv:
        if "SUPER_SECRET_TOKEN" in a:
            print("Found in argv:", a)

if __name__ == "__main__":
    main()
