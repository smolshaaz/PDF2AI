# Third-party notices

PDF2AI uses these runtime components, with their licenses retained in
`third_party_licenses/`. The build regenerates that directory from installed
runtime distributions. `DEPENDENCIES.txt` includes transitive dependencies.

| Component | Tested version | Upstream license declaration |
|---|---|---|
| PyMuPDF, PyMuPDF4LLM, PyMuPDF Layout | 1.28.2 | GNU AGPL v3 or Artifex commercial license |
| PySide6 / Qt for Python | 6.11.2 | LGPL v3 / GPL alternatives; Qt component terms apply |
| RapidOCR and its packaged models | 3.9.2 | Apache 2.0 package; retain upstream model notices |
| ONNX Runtime | 1.29.0 | MIT |
| NumPy | installed pinned environment | BSD |
| OpenCV | installed pinned environment | Apache 2.0 and included third-party notices |

These notices do not replace upstream license terms. Distribution/use must
comply with the chosen dependency licenses, including corresponding-source and
notice obligations where applicable. A proprietary redistribution may need
commercial licensing from Artifex. No commercial license is supplied here.

Official technical references consulted during implementation:

- [PyMuPDF4LLM API](https://pymupdf.readthedocs.io/en/latest/pymupdf4llm/api.html)
- [Official OCR plugins](https://pymupdf.readthedocs.io/en/latest/pymupdf4llm/ocr-plugins.html)
- [Qt pyside6-deploy](https://doc.qt.io/qtforpython-6/deployment/deployment-pyside6-deploy.html)

The installed 1.28.2 implementation was inspected directly. Its public
`to_markdown(*args, **kwargs)` wrapper dispatches to Layout by default. The Layout
signature supports all options used by PDF2AI and defaults `ocr_dpi` to 150.
Page chunk metadata supplies `page_number` (1-based); production conversion
validates it. The official adapter now supports `rapidocr` 3.9.x directly,
so no custom OCR adapter or monkey-patch is used.
