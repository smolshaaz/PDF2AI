# Preserves native PyMuPDF, Qt and ONNX binaries and bundles all local models.
from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules
root = Path(SPECPATH).parent
data = []
binaries = []
assets = root / "src" / "pdf2ai" / "assets"
bundled_models = {"PP-OCRv6_det_tiny.onnx", "arabic_PP-OCRv5_rec_mobile.onnx"}
for asset in assets.rglob("*"):
    if asset.is_file() and (asset.suffix != ".onnx" or asset.name in bundled_models):
        if "__pycache__" not in asset.parts and asset.suffix != ".pyc":
            data.append((str(asset), str(Path("pdf2ai/assets") / asset.relative_to(assets).parent)))
hidden = [
    "pdf2ai.extraction.multilingual_ocr",
    "pdf2ai.extraction.acceleration",
    "pdf2ai.workers.worker_cli",
    "pymupdf.layout",
    "pymupdf4llm.ocr.rapidocr_api",
    "pymupdf4llm.ocr.rapidocr_391_backend",
]
for package in ["pymupdf", "pymupdf4llm", "rapidocr", "onnxruntime"]:
    data += [entry for entry in collect_data_files(package)
             if Path(entry[0]).name != "PP-OCRv6_det_small.onnx"]
    binaries += collect_dynamic_libs(package)
# onnxruntime-directml exposes the same import package as the CPU wheel;
# collect_dynamic_libs includes its DirectML/provider DLLs for portable builds.
hidden += collect_submodules("rapidocr", filter=lambda name: not any(part in name for part in [".pytorch", ".paddle", ".openvino", ".tensorrt", ".mnn"]))
a = Analysis([str(root / "app.py")], pathex=[str(root / "src")], binaries=binaries, datas=data, hiddenimports=hidden,
             excludes=["torch", "paddle", "openvino", "tensorflow", "tkinter", "pytest"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="PDF2AI", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="PDF2AI")
if sys.platform == "darwin":
    app = BUNDLE(coll, name="PDF2AI.app", bundle_identifier="local.pdf2ai.desktop",
                 info_plist={"NSHighResolutionCapable": True,
                    "NSDocumentsFolderUsageDescription": "Read the PDFs you select and save their converted files locally."})
