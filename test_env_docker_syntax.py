# In cli/registry.py, build_env builds flags for the extra_env like:
# ["--env", f"{var}={value}"] if value is not None, else ["--env", var]
# Wait, let's look at the memory rule or Docker behavior:
# `--env VAR=value` in argv makes the value visible in `ps`.
# `--env VAR` pulls the value from the host environment, hiding it from `ps`.

# Look at test_cli.py test: "test_cli_never_embeds_a_credential_in_argv":
# "A value in argv is readable by `ps` and lands in shell history. `--env NAME`
# forwards the operator's own variable by name instead, so the secret never
# appears in a command line."

# BUT cli/registry.py does:
# if extra_env:
#     for var, value in sorted(extra_env.items()):
#         if value is not None:
#             out += ["--env", f"{var}={value}"]
#         else:
#             out += ["--env", var]

# AND cli/main.py does:
# env={v: os.environ[v] for v in (args.env or []) if v in os.environ}
# Which means extra_env is a dict mapping VAR -> os.environ[VAR].
# And then registry.py does:
# out += ["--env", f"{var}={value}"]

# THIS IS A LEAK. The value from the environment is being injected into the argv.
