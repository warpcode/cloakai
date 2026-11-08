import os
from cli import registry as reg

# Let's test if a prompt with shell characters gets injected
doc = {"agents": {"dev": {"image": "img", "entrypoints": {"run": "echo"}}}}

argv = reg.build_argv(doc, "dev", "agents", prompt="hello; rm -rf /")
print(argv)
