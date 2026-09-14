from pathlib import Path
import subprocess
import sys
import pytest


def test_browser_worker_flow():
    pytest.importorskip('fastapi')
    result = subprocess.run([sys.executable, str(Path(__file__).with_name('web_smoke.py'))],
        capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
