"""The office converter starts inside a guest whose hard limits are already stricter than its own."""

import subprocess
import sys

import pytest

CHILD = """
import resource
from documents.word.legacy import LegacyDocConversionLimits, _resource_limiter
resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
_resource_limiter(LegacyDocConversionLimits(max_open_files=128))()
print(resource.getrlimit(resource.RLIMIT_NOFILE))
"""


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX resource limits")
def test_a_stricter_inherited_limit_is_kept_instead_of_failing_the_start():
    # Raising a hard limit is refused; that used to abort the converter start inside the sandbox.
    output = subprocess.run([sys.executable, "-c", CHILD], capture_output=True, text=True, check=True).stdout
    assert output.strip() == "(64, 64)"
