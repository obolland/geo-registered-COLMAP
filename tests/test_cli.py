import subprocess
import sys

import georoute


def test_version_command_prints_package_version():
    result = subprocess.run(
        [sys.executable, "-m", "georoute", "--version"], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == f"georoute {georoute.__version__}"
