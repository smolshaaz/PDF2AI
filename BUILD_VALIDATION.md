# Build and test record

Environment: macOS ARM64, Python 3.13.5. Target product: Windows x64 desktop utility.

- `pytest -q`: **27 passed**; seven upstream PyMuPDF SWIG deprecation warnings.
- Native Qt window launch and conversion self-test: passed.
- Final `python scripts/build.py`: completed successfully, including its own
  packaged executable smoke test and `PASS.txt` check.
- Digital text, a basic table, repeated headers/footers and 4-point text: retained
  in actual PyMuPDF4LLM output. Healthy native text test fails if OCR is invoked.
- Generated image-only PDF: recognized by the local RapidOCR callback.
- Generated mixed scanned page: English handwriting and Arabic were retained;
  final Markdown contained logical-order Arabic and rejected reversed codepoints.
- Generated 150-DPI scan: its 6-point footnote was mangled at 150 OCR DPI and
  recovered exactly at the production 300-DPI setting.
- Generated 300-page PDF: **300 page markers and 300 tiny-text footnotes retained**,
  no review warnings; extraction and writing took 108.27 seconds on this host
  while a build was also running. This is a synthetic check, not an accuracy or
  speed guarantee for insurance documents or image-heavy PDFs.
- Queue: corrupt document followed by a valid document, duplicate paths, picker,
  drop event, remove, stop/close confirmation, and responsive GUI timer verified.
- UTF-8, concurrent filename collisions, disk-write cleanup, permission errors,
  absent OCR models, blank pages and blocked Python network connections verified.
- Windows CPython 3.13 / win_amd64 wheel availability resolved for all runtime
  constraints except antlr4-python3-runtime 4.9.3, which is distributed as pure
  Python source and built successfully here. No C compiler is needed for that
  dependency. This resolution check is not a Windows execution test.

## Local frozen verification

A PyInstaller 6.22.2 standalone executable was built and passed its local
`--self-test`: Qt window, file-signalled subprocess worker, native extraction,
image-only OCR, side-by-side English handwriting and Arabic, logical-order RTL
Markdown, page markers, UTF-8 and a responsive GUI timer. All models were local.
The worker never uses a multiprocessing pipe.
The default build script uses this verified freezer. To reproduce the raw
freezer check separately:

```powershell
pip install pyinstaller==6.22.2
pyinstaller --noconfirm scripts/PDF2AI-pyinstaller.spec
.\dist\PDF2AI\PDF2AI.exe --self-test .\.build\freezer-smoke
```

The official Qt deployment route was also exercised through Python optimization
and native C compilation. That lengthy build was deliberately stopped in favor
of the locally verified freezer; a completed Nuitka binary is not claimed.
`pysidedeploy.spec` and `scripts/build_qt.py` retain the alternative configuration.
The default PyInstaller route does not compile PyMuPDF's generated wrappers and
uses native Qt/ONNX collection hooks. Windows execution still needs verification.

No Windows host is available in this environment, so no Windows executable,
installer, clean-PC portability, signing, or Windows runtime compatibility is
claimed as tested. The complete source, pinned runtime constraints, deployment
configuration and automated packaged self-test are supplied.
