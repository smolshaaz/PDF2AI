"""Reproducible platform-local Qt/Nuitka build, including a frozen smoke test."""
import configparser
from pathlib import Path
import shutil
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
subprocess.run([sys.executable, str(root / "scripts" / "collect_licenses.py")], check=True)
config = configparser.ConfigParser()
config.read(root / "pysidedeploy.spec")
config["app"]["project_dir"] = str(root)
config["app"]["input_file"] = str(root / "app.py")
config["app"]["exec_directory"] = str(root / "dist")
config["app"]["icon"] = ""
config["python"]["python_path"] = sys.executable
config["qt"]["modules"] = "Core,Gui,Widgets"
config["qt"]["plugins"] = ""
config["nuitka"]["macos.permissions"] = ""
if sys.platform == "win32":
    config["nuitka"]["extra_args"] = config["nuitka"]["extra_args"].replace("--output-filename=PDF2AI", "--output-filename=PDF2AI.exe") + " --windows-console-mode=disable"
build = root / ".build"
build.mkdir(exist_ok=True)
(root / "dist").mkdir(exist_ok=True)
with (build / "deploy.spec").open("w") as stream:
    config.write(stream)
exe = Path(sys.executable).parent / ("pyside6-deploy.exe" if sys.platform == "win32" else "pyside6-deploy")
# Remove only generated compiler output so a failed build cannot pass using an old executable.
shutil.rmtree(root / "deployment", ignore_errors=True)
subprocess.run([str(exe), "-c", str(build / "deploy.spec"), "--force", "--keep-deployment-files", "--nuitka-version", "4.2.1", "--extra-ignore-dirs", ".build,build,dist,tests,third_party_licenses"], cwd=root, check=True)
# pyside6-deploy keeps standalone application contents under deployment/. Copy
# the WHOLE folder, never just the executable (which needs adjacent libraries).
if sys.platform == "darwin":
    candidates = list((root / "deployment").glob("*.app/Contents/MacOS/PDF2AI"))
else:
    filename = "PDF2AI.exe" if sys.platform == "win32" else "PDF2AI"
    candidates = list((root / "deployment").glob(f"*.dist/{filename}"))
if not candidates:
    raise SystemExit("No standalone executable found. Inspect deployment output; do not distribute an incomplete build.")
binary = max(candidates, key=lambda p: p.stat().st_mtime_ns)
if sys.platform == "darwin":
    bundle = binary.parents[2]
    target = root / "dist" / "PDF2AI.app"
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(bundle, target)
    packaged_binary = target / "Contents" / "MacOS" / binary.name
else:
    target = root / "dist" / "PDF2AI"
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(binary.parent, target)
    packaged_binary = target / binary.name
for name in ("README.md", "THIRD_PARTY_NOTICES.md"):
    shutil.copy2(root / name, target / name)
licenses = root / "third_party_licenses"
if licenses.exists():
    shutil.copytree(licenses, target / "third_party_licenses", dirs_exist_ok=True)
smoke = build / "packaged-smoke"
smoke.mkdir(exist_ok=True)
(smoke / "PASS.txt").unlink(missing_ok=True)
subprocess.run([str(packaged_binary), "--self-test", str(smoke)], check=True, timeout=300)
if not (build / "packaged-smoke" / "PASS.txt").exists():
    raise SystemExit("Packaged smoke test did not produce PASS.txt")
print(f"Distribute the entire folder: {target}")
