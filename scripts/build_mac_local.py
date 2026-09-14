"""Build a standalone Mac app for local testing, without publishing a release."""
from pathlib import Path
import subprocess
import sys

if sys.platform != "darwin":
    raise SystemExit("Run this script on macOS.")
root = Path(__file__).resolve().parents[1]
subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm",
    "--distpath", str(root / "dist"), "--workpath", str(root / ".build" / "mac"),
    str(root / "scripts" / "PDF2AI-pyinstaller.spec")], cwd=root, check=True)
app = root / "dist" / "PDF2AI.app"
subprocess.run(["/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister",
                "-f", str(app)], check=True)
print(app)
