import re
with open("cli/registry.py", "r") as f:
    content = f.read()

# Fix build_env
new_content = re.sub(
    r'if extra_env:\n\s+for var, value in sorted\(extra_env\.items\(\)\):\n\s+if value is not None:\n\s+out \+= \["--env", f"\{var\}=\{value\}"\]\n\s+else:\n\s+out \+= \["--env", var\]',
    'if extra_env:\n        for var in sorted(extra_env.keys()):\n            out += ["--env", var]',
    content
)

# Fix run_agents
new_content = re.sub(
    r'done = subprocess\.run\(argv, check=False, timeout=timeout\)',
    'spawn_env = os.environ.copy()\n        if env:\n            spawn_env.update(env)\n        done = subprocess.run(argv, check=False, timeout=timeout, env=spawn_env)',
    new_content
)

# Fix run_mcp
new_content = re.sub(
    r'os\.execvp\("docker", argv\)',
    'spawn_env = os.environ.copy()\n    if env:\n        spawn_env.update(env)\n    os.execvpe("docker", argv, spawn_env)',
    new_content
)

with open("cli/registry.py", "w") as f:
    f.write(new_content)
