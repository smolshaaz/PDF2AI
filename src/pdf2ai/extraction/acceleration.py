"""Automatic Windows DirectML selection; CPU is always a supported fallback.

Provider availability means that ORT ships DirectML, not that this PC has a
working GPU driver. Callers must retry construction/inference on CPU when
``disable_gpu`` returns True, before making any changes to the PDF page.
"""
import logging
import os
import sys


_GPU_REQUESTED = False
_GPU_DISABLED = False
_SESSION_OPTIONS_PATCHED = False
_LOG = logging.getLogger("pdf2ai")


def _configure_directml_sessions():
    """Supply the two required DML options missing from pinned RapidOCR 3.9.2.

    This changes only RapidOCR's session-options factory, only for sessions
    explicitly requesting DML. CPU settings and OCR/model code stay upstream.
    https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html
    """
    global _SESSION_OPTIONS_PATCHED
    if _SESSION_OPTIONS_PATCHED:
        return
    from onnxruntime import ExecutionMode
    from rapidocr.inference_engine.onnxruntime.main import OrtInferSession

    original = OrtInferSession._init_sess_opts

    def session_options(cfg):
        options = original(cfg)
        if cfg.get("use_dml", False):
            options.enable_mem_pattern = False
            options.execution_mode = ExecutionMode.ORT_SEQUENTIAL
        return options

    OrtInferSession._init_sess_opts = staticmethod(session_options)
    _SESSION_OPTIONS_PATCHED = True


def runtime_options():
    """RapidOCR configuration overrides, with no downloads or driver setup."""
    global _GPU_REQUESTED
    use_dml = False
    if sys.platform == "win32" and not _GPU_DISABLED:
        # Support can be disabled for diagnostics without changing the app UI.
        if os.environ.get("PDF2AI_FORCE_CPU", "").lower() not in {"1", "true", "yes"}:
            import onnxruntime

            use_dml = "DmlExecutionProvider" in onnxruntime.get_available_providers()
            if use_dml:
                _configure_directml_sessions()
    _GPU_REQUESTED = use_dml
    return {
        "EngineConfig.onnxruntime.use_dml": use_dml,
        "EngineConfig.onnxruntime.use_cuda": False,
        "EngineConfig.onnxruntime.use_coreml": False,
        "EngineConfig.onnxruntime.dml_ep_cfg": {"device_id": 0},
    }


def disable_gpu(error):
    """Disable DML for this worker; return whether the caller should retry once."""
    global _GPU_DISABLED
    if not _GPU_REQUESTED or _GPU_DISABLED:
        return False
    _GPU_DISABLED = True
    # Exception messages from OCR can include inputs; log only the error type.
    _LOG.warning("DirectML path failed (%s); retrying on CPU", type(error).__name__)
    return True


def effective_provider(engine):
    """Report registered providers from actual sessions, not GPU availability.

    A DML session may still assign unsupported operations to CPU; this is not a
    promise that every model operation runs on the GPU or is faster there.
    """
    components = [getattr(engine, name, None) for name in ("text_det", "text_rec")]
    if not any(component is not None for component in components):
        components = [engine]
    providers = set()
    for component in components:
        wrapper = getattr(component, "session", None)
        session = getattr(wrapper, "session", wrapper)
        if session is not None and hasattr(session, "get_providers"):
            providers.update(session.get_providers())
    if "DmlExecutionProvider" in providers:
        return "DirectML (with CPU fallback)"
    if "CPUExecutionProvider" in providers:
        return "CPU"
    return "Unknown"
