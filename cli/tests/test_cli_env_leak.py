import unittest
from cli import registry as reg

class TestEnvLeak(unittest.TestCase):
    def test_extra_env_does_not_leak_values(self):
        extra = {"MY_SECRET": "secret_value"}
        doc = {"agents": {"dev": {"image": "img", "entrypoints": {"run": "echo"}}}}
        argv = reg.build_argv(doc, "dev", "agents", prompt="x", extra_env=extra)

        # Check if the secret value is in argv
        self.assertFalse(any("secret_value" in a for a in argv))
        self.assertTrue(any(a == "MY_SECRET" for a in argv))

if __name__ == '__main__':
    unittest.main()
