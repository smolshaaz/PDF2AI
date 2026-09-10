"""Copy installed runtime dependency notices for distribution."""
from importlib.metadata import distribution
from pathlib import Path
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
import shutil

root = Path(__file__).resolve().parents[1]
target = root / "third_party_licenses"
target.mkdir(exist_ok=True)
pending = ["pdf2ai-desktop"]
seen = set()
lines = []
while pending:
    name = canonicalize_name(pending.pop())
    if name in seen:
        continue
    seen.add(name)
    dist = distribution(name)
    for dependency in dist.requires or []:
        req = Requirement(dependency)
        if req.marker is None or req.marker.evaluate({"extra": ""}):
            pending.append(req.name)
    if name == "pdf2ai-desktop":
        continue
    license_name = dist.metadata.get("License-Expression") or dist.metadata.get("License") or "See bundled license files/upstream metadata"
    lines.append(f"{dist.metadata['Name']} {dist.version}\n{license_name}\n")
    for entry in dist.files or []:
        if entry.name.upper().startswith(("LICENSE", "COPYING", "NOTICE", "COPYRIGHT")):
            source = Path(dist.locate_file(entry))
            if source.is_file():
                destination = target / name / str(entry).replace("../", "")
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
(target / "DEPENDENCIES.txt").write_text("\n".join(sorted(lines)), encoding="utf-8")
print(f"Collected notices for {len(seen) - 1} runtime distributions")
