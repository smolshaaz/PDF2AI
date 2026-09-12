# Preserves native PyMuPDF, Qt and ONNX binaries and bundles all local models.
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules
root = Path(SPECPATH).parent
data = []
binaries = []
hidden = [
    "pdf2ai.workers.worker_cli",
    "pymupdf.layout",
    "pymupdf4llm.ocr.rapidocr_api",
    "pymupdf4llm.ocr.rapidocr_391_backend",
]
for package in ["pymupdf", "pymupdf4llm", "rapidocr", "onnxruntime"]:
    data += collect_data_files(package)
    binaries += collect_dynamic_libs(package)
hidden += collect_submodules("rapidocr", filter=lambda name: not any(part in name for part in [".pytorch", ".paddle", ".openvino", ".tensorrt", ".mnn"]))
a = Analysis([str(root / "app.py")], pathex=[str(root / "src")], binaries=binaries, datas=data, hiddenimports=hidden,
             excludes=["torch", "paddle", "openvino", "tensorflow", "tkinter", "pytest"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="PDF2AI", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="PDF2AI")
