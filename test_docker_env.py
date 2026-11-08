import subprocess
import os

os.environ["MY_SECRET"] = "super_secret_value"
subprocess.run(["docker", "run", "--rm", "--env", "MY_SECRET", "alpine", "env"])
