"""Local printed-document OCR: fast recognition, targeted language/quality retries."""
from importlib.resources import files
import logging
import os
from pathlib import Path
import threading
import time
import numpy as np

_ENGINE = _SMALL = _ARABIC = _ARABIC_V4 = _FONT = None
_LOCK = threading.Lock()
LAST_LOW_CONFIDENCE = False
LAST_METRICS = {}


def asset_path(*parts):
    return Path(str(files("pdf2ai.assets").joinpath(*parts)))


def required_assets():
    return tuple(asset_path("models", name) for name in (
        "PP-OCRv6_rec_tiny.onnx", "arabic_PP-OCRv5_rec_mobile.onnx",
        "arabic_PP-OCRv4_rec_mobile.onnx",
    )) + (asset_path("fonts", "NotoSansArabic.ttf"), asset_path("models", "PP-OCRv6_det_tiny.onnx"))


def runtime_params():
    return {
        "Global.log_level": "critical", "Global.text_score": 0.0,
        "Rec.rec_batch_num": 6,
        "EngineConfig.onnxruntime.intra_op_num_threads": min(4, os.cpu_count() or 2),
        "EngineConfig.onnxruntime.inter_op_num_threads": 1,
        "EngineConfig.onnxruntime.enable_cpu_mem_arena": True,
    }


def _make_recognizer(model, language, model_type, ocr_version):
    from rapidocr.ch_ppocr_rec import TextRecognizer
    from rapidocr.main import DEFAULT_CFG_PATH
    from rapidocr.utils.parse_parameters import ParseParams
    cfg = ParseParams.update_batch(ParseParams.load(DEFAULT_CFG_PATH), {
        **runtime_params(), "Rec.model_path": str(model),
        "Rec.lang_type": language, "Rec.model_type": model_type,
        "Rec.ocr_version": ocr_version,
    })
    cfg.Rec.engine_cfg = cfg.EngineConfig[cfg.Rec.engine_type.value]
    cfg.Rec.font_path = None
    cfg.Rec.model_root_dir = model.parent
    return TextRecognizer(cfg.Rec)


def initialize():
    global _ENGINE, _FONT
    if _ENGINE is not None:
        return
    with _LOCK:
        if _ENGINE is not None:
            return
        if any(not p.is_file() for p in required_assets()):
            raise FileNotFoundError("Missing bundled OCR assets")
        import cv2
        import pymupdf
        from rapidocr import RapidOCR
        from rapidocr.utils.typings import ModelType
        cv2.setNumThreads(1)
        logger = logging.getLogger("RapidOCR")
        logger.handlers.clear()
        logger.propagate = False
        _FONT = pymupdf.Font(fontfile=str(required_assets()[3]))
        _ENGINE = RapidOCR(params={**runtime_params(),
            "Det.model_path": str(required_assets()[4]), "Det.model_type": ModelType.TINY,
            "Rec.model_path": str(required_assets()[0]), "Rec.model_type": ModelType.TINY})


def recognizer(kind):
    global _SMALL, _ARABIC, _ARABIC_V4
    from rapidocr.utils.typings import LangRec, ModelType, OCRVersion
    if kind == "arabic":
        if _ARABIC is None:
            _ARABIC = _make_recognizer(required_assets()[1], LangRec.ARABIC, ModelType.MOBILE, OCRVersion.PPOCRV5)
        return _ARABIC
    if kind == "arabic_retry":
        if _ARABIC_V4 is None:
            # v4 emits logical order; disable RapidOCR's display reversal.
            _ARABIC_V4 = _make_recognizer(required_assets()[2], LangRec.EN, ModelType.MOBILE, OCRVersion.PPOCRV4)
        return _ARABIC_V4
    if _SMALL is None:
        import rapidocr
        _SMALL = _make_recognizer(Path(rapidocr.__file__).parent / "models" / "PP-OCRv6_rec_small.onnx",
                                 LangRec.CH, ModelType.SMALL, OCRVersion.PPOCRV6)
    return _SMALL


def _arabic_ratio(text):
    letters = [c for c in text if c.isalpha()]
    count = sum("\u0600" <= c <= "\u06ff" or "\u0750" <= c <= "\u077f" or "\u08a0" <= c <= "\u08ff" for c in letters)
    return count, count / max(1, len(letters))


def is_arabic(text, score):
    count, share = _arabic_ratio(text)
    return count >= 2 and share >= .3 and score >= .45


def language_probes(boxes, width):
    """Sample top/middle/bottom on BOTH sides, even for confident English.

    Detect the English-left/Arabic-right case without recognizing every English
    line twice. Original box coordinates remain intact for Layout.
    """
    groups = [[], []]
    for i, box in enumerate(boxes):
        groups[int(float(np.mean(box[:, 0])) >= width / 2)].append(i)
    probes = set()
    for group in groups:
        group.sort(key=lambda i: float(np.mean(boxes[i][:, 1])))
        if group:
            probes.update((group[0], group[len(group) // 2], group[-1]))
    return groups, probes


def full_ocr(image, progress=None):
    global LAST_LOW_CONFIDENCE, LAST_METRICS
    initialize()
    from rapidocr.ch_ppocr_rec.typings import TextRecInput
    from rapidocr.main import RapidOCRError
    from rapidocr.utils.process_img import map_boxes_to_original
    LAST_METRICS = {}
    LAST_LOW_CONFIDENCE = False

    def stage(name, function):
        if progress:
            progress(name)
        started = time.monotonic()
        result = function()
        LAST_METRICS[name] = round(time.monotonic() - started, 3)
        return result

    original = _ENGINE.load_img(image)
    processed, operations = _ENGINE.preprocess_img(original)
    try:
        crops, detection = stage("Finding printed text", lambda: _ENGINE.detect_and_crop(processed, operations))
    except RapidOCRError:
        return []
    # Avoid the classifier's false per-line flips. Retry orientation only where
    # recognition is uncertain, preserving normal upright printed lines.
    primary = stage("Reading printed text", lambda: _ENGINE.text_rec(TextRecInput(img=crops, return_word_box=False)))
    texts, scores = list(primary.txts or ()), list(primary.scores)
    if not texts:
        return []
    count = len(texts)

    def recognize_indices(kind, indices):
        if not indices:
            return {}
        result = stage("Checking " + kind.replace("_", " "), lambda: recognizer(kind)(
            TextRecInput(img=[crops[i] for i in indices], return_word_box=False)))
        return {i: (t, float(s)) for i, t, s in zip(indices, result.txts or (), result.scores)}

    groups, probes = language_probes(detection.boxes, processed.shape[1])
    uncertain = {i for i in range(count) if scores[i] < .85 or
                 any(c.isalpha() and ord(c) > 591 for c in texts[i])}
    arabic = recognize_indices("arabic", sorted(probes | uncertain))
    arabic_regions = [group for group in groups if any(
        i in arabic and is_arabic(*arabic[i]) for i in group)]
    extra = {i for group in arabic_regions for i in group} - arabic.keys()
    arabic.update(recognize_indices("arabic", sorted(extra)))
    arabic_lines = {i for i, candidate in arabic.items() if is_arabic(*candidate)}

    upside_down = [i for i in range(count) if scores[i] < .80 and i not in arabic_lines]
    if upside_down:
        flipped = [np.ascontiguousarray(np.rot90(crops[i], 2)) for i in upside_down]
        retry = stage("Checking text orientation", lambda: _ENGINE.text_rec(TextRecInput(img=flipped, return_word_box=False)))
        for i, crop, text, score in zip(upside_down, flipped, retry.txts or (), retry.scores):
            if score > scores[i]:
                crops[i], texts[i], scores[i] = crop, text, float(score)
    # A single small printed-text retry replaces the 81 MB handwriting model.
    low = [i for i in range(count) if scores[i] < .85 and i not in arabic_lines]
    for i, (text, score) in recognize_indices("printed_text", low).items():
        if score > scores[i]:
            texts[i], scores[i] = text, score
    low_arabic = [i for i in arabic_lines if arabic[i][1] < .80]
    for i, candidate in recognize_indices("arabic_retry", low_arabic).items():
        if is_arabic(*candidate) and candidate[1] > arabic[i][1]:
            arabic[i] = candidate
    from bidi.algorithm import get_display
    for i in arabic_lines:
        # PyMuPDF reorders the positioned PDF text layer during extraction.
        texts[i], scores[i] = get_display(arabic[i][0]), arabic[i][1]
    LAST_LOW_CONFIDENCE = any(s < .80 for s in scores)
    LAST_METRICS["lines"] = count
    LAST_METRICS["printed_retries"] = len(low)
    LAST_METRICS["arabic_lines"] = len(arabic_lines)
    boxes = map_boxes_to_original(detection.boxes.copy(), operations, original.shape[0], original.shape[1])
    return list(zip(boxes, texts, scores))


def smoke_test():
    initialize()
    from rapidocr.ch_ppocr_rec.typings import TextRecInput
    _ENGINE.text_det(np.full((64, 128, 3), 255, dtype=np.uint8))
    request = TextRecInput(img=[np.full((48, 160, 3), 255, dtype=np.uint8)], return_word_box=False)
    _ENGINE.text_rec(request)
    recognizer("arabic")(request)
    if not _FONT.has_glyph(ord("ه")):
        raise RuntimeError("Bundled font lacks Arabic glyphs")


def exec_ocr(page, dpi=300, pixmap=None, language=None, keep_ocr_text=False, progress=None):
    initialize()
    import pymupdf
    from pymupdf4llm.ocr.exec_ocr_interface import ocr_text
    from pymupdf4llm.ocr.get_culled_pixmap import get_pixmap
    healthy, replace = [], []
    for block in page.get_text("dict", flags=pymupdf.TEXT_ACCURATE_BBOXES)["blocks"]:
        for line in block.get("lines", ()):
            for span in line["spans"]:
                if ocr_text(span):
                    if keep_ocr_text:
                        return
                    replace.append(span["bbox"])
                elif "\ufffd" in span["text"]:
                    replace.append(span["bbox"])
                else:
                    healthy.append(span["bbox"])
    # Photographs sometimes have pixel dimensions stored as PDF points. Never
    # expand a 2000px photo into an 8333px raster just because its page is huge.
    dpi = max(36, min(int(dpi), int(72 * 3000 / max(page.rect.width, page.rect.height))))
    pix, empty = get_pixmap(page.get_displaylist(), dpi=dpi, rects=healthy, empty_threshold=250)
    if empty:
        return
    image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
    result = full_ocr(image, progress)
    matrix = pymupdf.Rect(pix.irect).torect(page.rect)
    for rect in replace:
        page.add_redact_annot(rect)
    if replace:
        page.apply_redactions(images=0, graphics=0)
    page.insert_font(fontname="pdf2aiocr", fontbuffer=_FONT.buffer)
    for box, text, score in result:
        if not text.strip():
            continue
        rect = pymupdf.Rect(float(np.min(box[:, 0])), float(np.min(box[:, 1])),
                           float(np.max(box[:, 0])), float(np.max(box[:, 1]))) * matrix
        # The official adapter assumes a font with near-unit line height.
        # Noto Arabic's ascender/descender span >2 em: using box height as font
        # size makes adjacent OCR lines overlap and destroys table columns.
        size = rect.height / (_FONT.ascender - _FONT.descender)
        origin = pymupdf.Point(rect.x0, rect.y0 + size * _FONT.ascender)
        width = _FONT.text_length(text, fontsize=size)
        if width > 0:
            page.insert_text(origin, text, fontsize=size, fontname="pdf2aiocr", render_mode=3,
                             morph=(origin, pymupdf.Matrix(rect.width / width, 1)))
