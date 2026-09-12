"""Automatic local English, handwriting, and Arabic OCR for PyMuPDF4LLM."""
from importlib.resources import files
import logging
import os
from pathlib import Path
import threading

import numpy as np

_ENGINE = None
_HANDWRITING = None
_SMALL = None
_ARABIC = None
_ARABIC_V4 = None
_FONT = None
_LOCK = threading.Lock()
LAST_LOW_CONFIDENCE = False


def runtime_params():
    # Bound competing ONNX thread pools on office laptops; recognize sorted
    # line crops in batches without running four models over the whole page.
    return {
        "Global.log_level": "critical",
        "Global.text_score": 0.0,
        "Rec.rec_batch_num": 16,
        "EngineConfig.onnxruntime.intra_op_num_threads": min(4, os.cpu_count() or 2),
        "EngineConfig.onnxruntime.inter_op_num_threads": 1,
    }


def asset_path(*parts: str) -> Path:
    """Return an on-disk asset path in source and frozen one-folder builds."""
    return Path(str(files("pdf2ai.assets").joinpath(*parts)))


def required_assets() -> tuple[Path, ...]:
    return (
        asset_path("models", "ch_PP-OCRv5_rec_server.onnx"),
        asset_path("models", "arabic_PP-OCRv5_rec_mobile.onnx"),
        asset_path("models", "arabic_PP-OCRv4_rec_mobile.onnx"),
        asset_path("fonts", "NotoSansArabic.ttf"),
        asset_path("models", "PP-OCRv6_rec_tiny.onnx"),
    )


def _make_recognizer(model: Path, language, model_type, ocr_version):
    """Create a RapidOCR recognizer around one bundled PaddleOCR model."""
    from rapidocr.ch_ppocr_rec import TextRecognizer
    from rapidocr.main import DEFAULT_CFG_PATH
    from rapidocr.utils.parse_parameters import ParseParams
    cfg = ParseParams.load(DEFAULT_CFG_PATH)
    cfg = ParseParams.update_batch(
        cfg,
        {
            **runtime_params(),
            "Rec.model_path": str(model),
            "Rec.ocr_version": ocr_version,
            "Rec.lang_type": language,
            "Rec.model_type": model_type,
        },
    )
    cfg.Rec.engine_cfg = cfg.EngineConfig[cfg.Rec.engine_type.value]
    cfg.Rec.font_path = None
    cfg.Rec.model_root_dir = model.parent
    return TextRecognizer(cfg.Rec)


def initialize() -> None:
    """Load all local inference sessions exactly once in this worker process."""
    global _ENGINE, _HANDWRITING, _ARABIC, _ARABIC_V4, _FONT
    if _ENGINE is not None:
        return
    with _LOCK:
        if _ENGINE is not None:
            return
        missing = [path.name for path in required_assets() if not path.is_file()]
        if missing:
            raise FileNotFoundError("Missing bundled OCR assets: " + ", ".join(missing))

        from rapidocr import RapidOCR
        from rapidocr.utils.typings import LangRec, ModelType, OCRVersion
        import pymupdf

        rapid_logger = logging.getLogger("RapidOCR")
        rapid_logger.handlers.clear()
        rapid_logger.propagate = False
        rapid_logger.setLevel(logging.CRITICAL)

        engine = RapidOCR(params={**runtime_params(), "Rec.model_path": str(required_assets()[4]), "Rec.model_type": ModelType.TINY})
        _ARABIC = _make_recognizer(
            required_assets()[1],
            LangRec.ARABIC,
            ModelType.MOBILE,
            OCRVersion.PPOCRV5,
        )
        # PP-OCRv4's Arabic ONNX model emits logical-order text. Marking the
        # recognizer as English prevents RapidOCR from reversing it for display.
        _FONT = pymupdf.Font(fontfile=str(required_assets()[3]))
        _ENGINE = engine


def fallback_recognizer(arabic=False):
    global _HANDWRITING, _ARABIC_V4
    from rapidocr.utils.typings import LangRec, ModelType, OCRVersion
    if arabic:
        if _ARABIC_V4 is None:
            _ARABIC_V4 = _make_recognizer(required_assets()[2], LangRec.EN, ModelType.MOBILE, OCRVersion.PPOCRV4)
        return _ARABIC_V4
    if _HANDWRITING is None:
        _HANDWRITING = _make_recognizer(required_assets()[0], LangRec.CH, ModelType.SERVER, OCRVersion.PPOCRV5)
    return _HANDWRITING


def _arabic_ratio(text: str) -> tuple[int, float]:
    letters = [char for char in text if char.isalpha()]
    arabic = sum(
        "\u0600" <= char <= "\u06ff"
        or "\u0750" <= char <= "\u077f"
        or "\u08a0" <= char <= "\u08ff"
        for char in letters
    )
    return arabic, arabic / max(1, len(letters))


def _select(primary, handwriting, arabic, arabic_v4):
    """Choose one recognition per detected line without duplicating content."""
    texts = []
    scores = []
    results = (primary, handwriting, arabic, arabic_v4)
    for index in range(len(primary.txts)):
        candidates = tuple(
            (result.txts[index], result.scores[index])
            for result in results
            if result.txts is not None
            and result.scores is not None
            and index < len(result.txts)
            and index < len(result.scores)
        )
        arabic_candidates = []
        for result in (arabic, arabic_v4):
            if (
                result.txts is None
                or result.scores is None
                or index >= len(result.txts)
                or index >= len(result.scores)
            ):
                continue
            text, score = result.txts[index], result.scores[index]
            count, share = _arabic_ratio(text)
            if count >= 2 and share >= 0.30 and score >= 0.45:
                arabic_candidates.append((text, score))
        if arabic_candidates:
            chosen = max(arabic_candidates, key=lambda item: item[1])
            # PyMuPDF's positioned text extraction applies RTL ordering to the
            # inserted OCR layer. Store display order so the extracted Markdown
            # receives logical Unicode order rather than reversed Arabic words.
            from bidi.algorithm import get_display

            chosen = (get_display(chosen[0]), chosen[1])
        else:
            chosen = max(candidates, key=lambda item: item[1])
        texts.append(chosen[0])
        scores.append(float(chosen[1]))
    return tuple(texts), tuple(scores)


def full_ocr(image: np.ndarray):
    """Return PyMuPDF4LLM's expected ``(box, text, score)`` tuples."""
    global LAST_LOW_CONFIDENCE, _SMALL
    initialize()
    from rapidocr.ch_ppocr_rec.typings import TextRecInput
    from rapidocr.main import RapidOCRError

    original = _ENGINE.load_img(image)
    processed, operations = _ENGINE.preprocess_img(original)
    try:
        crops, detection = _ENGINE.detect_and_crop(processed, operations)
        rotated, classification = _ENGINE.cls_and_rotate(crops)
        request = TextRecInput(img=rotated, return_word_box=False)
        primary = _ENGINE.text_rec(request)
    except RapidOCRError:
        return []

    from rapidocr.ch_ppocr_rec.typings import TextRecOutput
    count = len(primary.txts or ())
    # The orientation classifier can flip an otherwise healthy printed line.
    # Try the opposite orientation cheaply before loading larger recognizers.
    indices = [i for i in range(count) if primary.scores[i] < .85]
    if indices:
        flipped = [np.ascontiguousarray(np.rot90(rotated[i], 2)) for i in indices]
        retry = _ENGINE.text_rec(TextRecInput(img=flipped, return_word_box=False))
        texts, scores = list(primary.txts), list(primary.scores)
        for i, crop, text, score in zip(indices, flipped, retry.txts or (), retry.scores):
            if score > scores[i]:
                rotated[i] = crop
                texts[i], scores[i] = text, score
        primary.txts, primary.scores = tuple(texts), tuple(scores)
    # Retry uncertain tiny-model crops with the larger v6 recognizer. Confident
    # printed text completes after one recognition pass.
    indices = [i for i in range(count) if primary.scores[i] < .97]
    if indices:
        if _SMALL is None:
            import rapidocr
            from rapidocr.utils.typings import LangRec, ModelType, OCRVersion
            _SMALL = _make_recognizer(Path(rapidocr.__file__).parent / "models" / "PP-OCRv6_rec_small.onnx", LangRec.CH, ModelType.SMALL, OCRVersion.PPOCRV6)
        retry = _SMALL(TextRecInput(img=[rotated[i] for i in indices], return_word_box=False))
        texts, scores = list(primary.txts), list(primary.scores)
        for i, text, score in zip(indices, retry.txts or (), retry.scores):
            if score > scores[i]:
                texts[i], scores[i] = text, score
        primary.txts, primary.scores = tuple(texts), tuple(scores)
    arabic = TextRecOutput(txts=tuple("" for _ in range(count)), scores=[0.0] * count)
    indices = [i for i in range(count) if primary.scores[i] < .97 or
               any(c.isalpha() and ord(c) > 591 for c in primary.txts[i])]
    if indices:
        retry = _ARABIC(TextRecInput(img=[rotated[i] for i in indices], return_word_box=False))
        texts, scores = list(arabic.txts), list(arabic.scores)
        for i, text, score in zip(indices, retry.txts or (), retry.scores):
            texts[i], scores[i] = text, score
        arabic.txts, arabic.scores = tuple(texts), scores
    handwriting = TextRecOutput(txts=tuple("" for _ in range(count)), scores=[0.0] * count)
    arabic_v4 = TextRecOutput(txts=tuple("" for _ in range(count)), scores=[0.0] * count)
    arabic_lines = {
        i for i, (text, score) in enumerate(zip(arabic.txts or (), arabic.scores))
        if _arabic_ratio(text)[0] >= 2 and _arabic_ratio(text)[1] >= .3 and score >= .45
    }
    # Route uncertain/non-Latin text to Arabic; reserve the heavy handwriting
    # recognizer for lines still uncertain after the two v6 passes.
    for is_arabic, target in ((False, handwriting), (True, arabic_v4)):
        if is_arabic:
            indices = [i for i in arabic_lines if arabic.scores[i] < .90]
        else:
            indices = [i for i in range(count) if i not in arabic_lines and primary.scores[i] < .97]
        if indices:
            retry = fallback_recognizer(is_arabic)(TextRecInput(img=[rotated[i] for i in indices], return_word_box=False))
            texts, scores = list(target.txts), list(target.scores)
            for i, text, score in zip(indices, retry.txts or (), retry.scores):
                texts[i], scores[i] = text, score
            target.txts, target.scores = tuple(texts), scores
    primary.txts, primary.scores = _select(primary, handwriting, arabic, arabic_v4)
    LAST_LOW_CONFIDENCE = any(score < .80 for score in primary.scores)
    result = _ENGINE.build_final_output(
        original, detection, classification, primary, crops, operations
    )
    if result.boxes is None or result.txts is None or result.scores is None:
        return []
    return list(zip(result.boxes, result.txts, result.scores))


def smoke_test() -> None:
    """Exercise every bundled ONNX session without reading a user document."""
    initialize()
    from rapidocr.ch_ppocr_rec.typings import TextRecInput

    blank_page = np.full((64, 128, 3), 255, dtype=np.uint8)
    blank_line = np.full((48, 160, 3), 255, dtype=np.uint8)
    _ENGINE.text_det(blank_page)
    _ENGINE.text_cls([blank_line])
    request = TextRecInput(img=[blank_line], return_word_box=False)
    for recognizer in (_ENGINE.text_rec, _ARABIC):
        recognizer(request)
    if not _FONT.has_glyph(ord("A")) or not _FONT.has_glyph(ord("ه")):
        raise RuntimeError("Bundled OCR font lacks required glyphs")


def exec_ocr(page, dpi=300, pixmap=None, language=None, keep_ocr_text=False):
    """PyMuPDF4LLM OCR callback using its official full-OCR integration."""
    initialize()
    from pymupdf4llm.ocr import exec_ocr_interface

    # The adapter's built-in CJK fallback has no Arabic glyphs. Use the
    # bundled Noto font so the invisible OCR text layer retains Unicode Arabic.
    exec_ocr_interface.FONT = _FONT
    return exec_ocr_interface.exec_ocr_full(
        page,
        full_ocr,
        dpi=dpi,
        language=language,
        keep_ocr_text=keep_ocr_text,
    )
