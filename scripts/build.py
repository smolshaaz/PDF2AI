"""Build a self-contained folder and test the actual bundled executable."""
from pathlib import Path
import shutil
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
subprocess.run([sys.executable, str(root / "scripts" / "collect_licenses.py")], check=True)
build = root / ".build"
build.mkdir(exist_ok=True)
# Clean only our generated artifact. Never allow a stale binary to pass testing.
target = root / "dist" / "PDF2AI"
shutil.rmtree(target, ignore_errors=True)
subprocess.run([
    sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
    "--distpath", str(root / "dist"), "--workpath", str(build / "freezer"),
    str(root / "scripts" / "PDF2AI-pyinstaller.spec"),
], cwd=root, check=True)
binary = target / ("PDF2AI.exe" if sys.platform == "win32" else "PDF2AI")
if not binary.is_file():
    raise SystemExit("The build did not create the executable. Do not distribute it.")
for name in ("README.md", "THIRD_PARTY_NOTICES.md", "BUILD_VALIDATION.md", "screenshot.png"):
    shutil.copy2(root / name, target / name)
shutil.copytree(root / "third_party_licenses", target / "third_party_licenses", dirs_exist_ok=True)
smoke = build / "packaged-smoke"
smoke.mkdir(exist_ok=True)
for name in ("PASS.txt", "FAIL.txt"):
    (smoke / name).unlink(missing_ok=True)
subprocess.run([str(binary), "--self-test", str(smoke)], check=True, timeout=300)
if not (smoke / "PASS.txt").is_file():
    raise SystemExit("The bundled application did not pass its smoke test.")
print(f"Build verified. Distribute the entire folder: {target}")
