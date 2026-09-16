"""Local PaddleOCR recognition with language routing and no quality ensemble."""
from importlib.resources import files
from concurrent.futures import ThreadPoolExecutor
import logging
import atexit
import os
from pathlib import Path
import threading
import time
import numpy as np

_ENGINE = _ARABIC = _FONT = None
_LOCK = threading.Lock()
LAST_LOW_CONFIDENCE = False
LAST_METRICS = {}

# Review threshold only: a low score must never erase potentially real text.
NOISE_FLOOR = 0.85
OCR_DPI = 200


def asset_path(*parts):
    return Path(str(files("pdf2ai.assets").joinpath(*parts)))


def required_assets():
    import rapidocr
    return (
        Path(rapidocr.__file__).parent / "models" / "PP-OCRv6_rec_small.onnx",
        asset_path("models", "arabic_PP-OCRv5_rec_mobile.onnx"),
        asset_path("models", "PP-OCRv6_det_tiny.onnx"),
        asset_path("fonts", "NotoSansArabic.ttf"),
    )


def runtime_params():
    from pdf2ai.extraction.acceleration import runtime_options
    return {
        "Global.log_level": "critical", "Global.text_score": 0.0,
        "Rec.rec_batch_num": 6,
        # Detection uses a bounded image; recognition uses original pixels.
        "Global.max_side_len": 2000,
        "EngineConfig.onnxruntime.intra_op_num_threads": min(4, os.cpu_count() or 2),
        "EngineConfig.onnxruntime.inter_op_num_threads": 1,
        "EngineConfig.onnxruntime.enable_cpu_mem_arena": True,
        **runtime_options(),
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
        from rapidocr.utils.typings import ModelType, LangRec, OCRVersion
        from pdf2ai.extraction.acceleration import disable_gpu, effective_provider
        cv2.setNumThreads(1)
        logger = logging.getLogger("RapidOCR")
        logger.handlers.clear()
        logger.propagate = False
        _FONT = pymupdf.Font(fontfile=str(required_assets()[3]))
        def create_engine():
            return RapidOCR(params={**runtime_params(),
                "Global.use_cls": False,
                "Det.model_path": str(required_assets()[2]), "Det.model_type": ModelType.TINY,
                "Rec.model_path": str(required_assets()[0]),
                "Rec.model_type": ModelType.SMALL, "Rec.lang_type": LangRec.CH,
                "Rec.ocr_version": OCRVersion.PPOCRV6})
        try:
            _ENGINE = create_engine()
        except Exception as exc:
            if not disable_gpu(exc):
                raise
            _ENGINE = create_engine()
        logging.getLogger("pdf2ai").info("OCR execution provider: %s", effective_provider(_ENGINE))


def recognizer(kind):
    global _ARABIC
    from rapidocr.utils.typings import LangRec, ModelType, OCRVersion
    if kind == "arabic":
        if _ARABIC is None:
            _ARABIC = _make_recognizer(required_assets()[1], LangRec.ARABIC, ModelType.MOBILE, OCRVersion.PPOCRV5)
        return _ARABIC
    raise ValueError("Unsupported OCR language route")


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


def full_ocr(image, progress=None, detail_image=None):
    from pdf2ai.extraction.acceleration import disable_gpu
    try:
        return _full_ocr(image, progress, detail_image)
    except Exception as exc:
        if not disable_gpu(exc):
            raise
        # Nothing has been inserted into the PDF yet. Retry this same page on
        # CPU once if a device/driver failed; subsequent pages stay on CPU.
        shutdown()
        if progress:
            progress("Graphics acceleration unavailable; continuing on CPU")
        return _full_ocr(image, progress, detail_image)


def _full_ocr(image, progress=None, detail_image=None):
    global LAST_LOW_CONFIDENCE, LAST_METRICS
    initialize()
    from pdf2ai.extraction.acceleration import effective_provider
    from rapidocr.ch_ppocr_rec.typings import TextRecInput, TextRecOutput
    from rapidocr.main import RapidOCRError
    from rapidocr.utils.process_img import map_boxes_to_original
    LAST_METRICS = {"provider": effective_provider(_ENGINE), "recognizer": "PP-OCRv6 small"}
    LAST_LOW_CONFIDENCE = False

    def stage(name, function, announce=True):
        if progress and announce:
            progress(name)
        started = time.monotonic()
        result = function()
        LAST_METRICS[name] = round(LAST_METRICS.get(name, 0) + time.monotonic() - started, 3)
        return result

    def read_crops(name, engine, images):
        # Keep memory and time between progress events bounded. Sort globally by
        # aspect ratio so one long line cannot pad a batch of short table cells.
        order = sorted(range(len(images)), key=lambda i: images[i].shape[1] / max(1, images[i].shape[0]))
        texts = [""] * len(images)
        scores = [0.0] * len(images)
        batches = [order[offset:offset + 24] for offset in range(0, len(order), 24)]
        # CPU sessions support concurrent calls; at most two batches share the
        # same model and its four-thread pool. DirectML forbids concurrent Run
        # calls on one session, so its batches must remain strictly sequential.
        workers = 2 if (len(images) >= 48 and (os.cpu_count() or 1) >= 8
                        and effective_provider(engine) == "CPU") else 1
        LAST_METRICS["recognition_workers"] = max(workers, LAST_METRICS.get("recognition_workers", 1))

        def read_batch(indices):
            return engine(TextRecInput(img=[images[i] for i in indices], return_word_box=False))

        def collect(indices, result, completed):
            if len(result.txts or ()) != len(indices) or len(result.scores) != len(indices):
                raise RuntimeError("OCR returned an incomplete recognition batch")
            for i, text, score in zip(indices, result.txts, result.scores):
                texts[i], scores[i] = text, float(score)
            if progress:
                progress(f"{name}: {completed}/{len(order)} text regions")

        if progress:
            progress(f"{name}: 0/{len(order)} text regions")
        started = time.monotonic()
        completed = 0
        if workers == 1:
            for indices in batches:
                result = read_batch(indices)
                completed += len(indices)
                collect(indices, result, completed)
        else:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ocr-batch") as executor:
                for offset in range(0, len(batches), workers):
                    pending = [(indices, executor.submit(read_batch, indices))
                               for indices in batches[offset:offset + workers]]
                    for indices, future in pending:
                        result = future.result()
                        completed += len(indices)
                        collect(indices, result, completed)
        LAST_METRICS[name] = round(LAST_METRICS.get(name, 0) + time.monotonic() - started, 3)
        return TextRecOutput(txts=tuple(texts), scores=scores)

    original = _ENGINE.load_img(image)
    processed, operations = _ENGINE.preprocess_img(original)
    try:
        crops, detection = stage("Finding printed text", lambda: _ENGINE.detect_and_crop(processed, operations))
    except RapidOCRError:
        return []
    # Detect at bounded resolution, then recrop from the original raster so
    # tiny characters are not irreversibly downsampled before recognition.
    original_boxes = map_boxes_to_original(detection.boxes.copy(), operations, original.shape[0], original.shape[1])
    crops = _ENGINE.crop_text_regions(original, original_boxes)
    # Low-DPI detection is sufficient to locate text, but resizing its tiny
    # letter pixels back up for recognition cannot restore lost detail. Read
    # small lines from one lazily rendered detail image instead. This is still
    # one recognition pass, with the same model and the same page coordinates.
    small_lines = [i for i, crop in enumerate(crops) if crop.shape[0] < 32]
    if small_lines and detail_image is not None:
        detail = stage("Reading small print at higher resolution", detail_image)
        if detail is not None:
            boxes = original_boxes[small_lines].copy()
            boxes[:, :, 0] *= detail.shape[1] / original.shape[1]
            boxes[:, :, 1] *= detail.shape[0] / original.shape[0]
            for i, crop in zip(small_lines, _ENGINE.crop_text_regions(detail, boxes)):
                crops[i] = crop
            LAST_METRICS["detail_regions"] = len(small_lines)
    # One primary recognition pass. Arabic routing is needed because the v6
    # Latin/CJK recognizer does not cover Arabic; there are no quality retries.
    primary = read_crops("Reading printed text", _ENGINE.text_rec, crops)
    texts, scores = list(primary.txts or ()), list(primary.scores)
    if not texts:
        return []
    count = len(texts)

    def recognize_indices(kind, indices):
        if not indices:
            return {}
        result = read_crops("Checking " + kind.replace("_", " "), recognizer(kind), [crops[i] for i in indices])
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

    from bidi.algorithm import get_display
    for i in arabic_lines:
        # PyMuPDF reorders the positioned PDF text layer during extraction.
        texts[i], scores[i] = get_display(arabic[i][0]), arabic[i][1]
    LAST_LOW_CONFIDENCE = any(s < .92 for s in scores)
    LAST_METRICS["lines"] = count
    LAST_METRICS["printed_retries"] = 0
    LAST_METRICS["arabic_lines"] = len(arabic_lines)
    return list(zip(original_boxes, texts, scores))


def smoke_test():
    initialize()
    from rapidocr.ch_ppocr_rec.typings import TextRecInput
    _ENGINE.text_det(np.full((64, 128, 3), 255, dtype=np.uint8))
    request = TextRecInput(img=[np.full((48, 160, 3), 255, dtype=np.uint8)], return_word_box=False)
    _ENGINE.text_rec(request)
    recognizer("arabic")(request)
    if not _FONT.has_glyph(ord("ه")):
        raise RuntimeError("Bundled font lacks Arabic glyphs")


def is_ocr_span(span):
    """Identify non-rendering text using the installed engine's definition.

    Invisibility identifies a potential OCR layer, not whether it is accurate.
    """
    from pymupdf4llm.ocr.exec_ocr_interface import ocr_text
    return ocr_text(span)


def needs_scan_ocr(page):
    """Refresh image pages with hidden text, including mixed native headers."""
    if not page.get_images():
        return False
    import pymupdf
    has_visible_text = False
    for block in page.get_text('dict', flags=pymupdf.TEXT_ACCURATE_BBOXES)['blocks']:
        for line in block.get('lines', ()):
            for span in line['spans']:
                if not span['text'].strip():
                    continue
                if is_ocr_span(span):
                    return True
                has_visible_text = True
    return not has_visible_text


def exec_ocr(page, dpi=OCR_DPI, pixmap=None, language=None, keep_ocr_text=False, progress=None):
    global LAST_LOW_CONFIDENCE
    initialize()
    import pymupdf
    from pymupdf4llm.ocr.get_culled_pixmap import get_pixmap
    healthy, replace = [], []
    for block in page.get_text("dict", flags=pymupdf.TEXT_ACCURATE_BBOXES)["blocks"]:
        for line in block.get("lines", ()):
            for span in line["spans"]:
                if is_ocr_span(span):
                    if keep_ocr_text:
                        return
                    replace.append(span["bbox"])
                elif "\ufffd" in span["text"]:
                    replace.append(span["bbox"])
                else:
                    healthy.append(span["bbox"])
    if progress:
        progress("Rendering scanned page")
    # Photographs sometimes have pixel dimensions stored as PDF points. Never
    # expand a 2000px photo into an 8333px raster just because its page is huge.
    dpi = max(36, min(int(dpi), int(72 * 3000 / max(page.rect.width, page.rect.height))))
    if healthy:
        # Mixed pages need native text masked to avoid recognizing it twice.
        pix, empty = get_pixmap(page.get_displaylist(), dpi=dpi, rects=healthy, empty_threshold=250)
    else:
        # Pure scans need no masking or full-page color histogram. Hidden OCR
        # text does not render, so the visible source image is authoritative.
        pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
        empty = pix.is_unicolor and min(pix.pixel(0, 0)) >= 250
    if empty:
        return
    if progress:
        progress(f"Page raster ready: {pix.width} × {pix.height} pixels")
    image = np.frombuffer(pix.samples_mv, dtype=np.uint8).reshape(pix.height, pix.width, 3)
    detail_pix = None
    def detail_image():
        nonlocal detail_pix
        detail_dpi = max(36, min(300, int(72 * 3000 / max(page.rect.width, page.rect.height))))
        if detail_dpi <= dpi:
            return None
        if detail_pix is None:
            if healthy:
                detail_pix, _ = get_pixmap(page.get_displaylist(), dpi=detail_dpi, rects=healthy, empty_threshold=250)
            else:
                detail_pix = page.get_pixmap(dpi=detail_dpi, colorspace=pymupdf.csRGB, alpha=False)
        LAST_METRICS["detail_dpi"] = detail_dpi
        return np.frombuffer(detail_pix.samples_mv, dtype=np.uint8).reshape(detail_pix.height, detail_pix.width, 3)

    result = full_ocr(image, progress, detail_image=detail_image)
    LAST_METRICS.update(dpi=dpi, raster_width=pix.width, raster_height=pix.height)
    matrix = pymupdf.Rect(pix.irect).torect(page.rect)
    for rect in replace:
        page.add_redact_annot(rect)
    if replace:
        page.apply_redactions(images=0, graphics=0)
    page.insert_font(fontname="pdf2aiocr", fontbuffer=_FONT.buffer)
    for box, text, score in result:
        if not text.strip():
            continue
        if score < NOISE_FLOOR:
            LAST_LOW_CONFIDENCE = True
            LAST_METRICS['uncertain_regions'] = LAST_METRICS.get('uncertain_regions', 0) + 1
        rect = pymupdf.Rect(float(np.min(box[:, 0])), float(np.min(box[:, 1])),
                           float(np.max(box[:, 0])), float(np.max(box[:, 1]))) * matrix
        # The official adapter assumes a font with near-unit line height.
        # Noto Arabic's ascender/descender span >2 em: using box height as font
        # size makes adjacent OCR lines overlap and destroys table columns.
        size = rect.height / (_FONT.ascender - _FONT.descender)
        origin = pymupdf.Point(rect.x0, rect.y0 + size * _FONT.ascender)
        width = _FONT.text_length(text, fontsize=size)
        if width > 0:
            # Layout can ignore hidden spans when visible native text is also
            # present. This layer exists only in the in-memory working document;
            # use the official adapter's filled-text mode so both are extracted.
            page.insert_text(origin, text, fontsize=size, fontname="pdf2aiocr", render_mode=0,
                             morph=(origin, pymupdf.Matrix(rect.width / width, 1)))


def shutdown():
    """Release native sessions before Python/native library teardown begins."""
    global _ENGINE, _ARABIC, _FONT
    _ENGINE = _ARABIC = _FONT = None
    import gc
    gc.collect()


atexit.register(shutdown)
