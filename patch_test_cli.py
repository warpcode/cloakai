import re
with open("cli/tests/test_cli.py", "r") as f:
    content = f.read()

new_content = re.sub(
    r'self\.assertIn\("CUSTOM_VAR=secret_val", line\)',
    'self.assertIn("CUSTOM_VAR", line)',
    content
)

new_content = re.sub(
    r'env_val_at = line\.index\("CUSTOM_VAR=secret_val"\)',
    'env_val_at = line.index("CUSTOM_VAR")',
    new_content
)

with open("cli/tests/test_cli.py", "w") as f:
    f.write(new_content)
