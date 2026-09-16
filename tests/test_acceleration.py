"""GPU routing checks without loading OCR models or requiring Windows hardware."""
from types import SimpleNamespace

import onnxruntime
import pytest

from pdf2ai.extraction import acceleration


@pytest.fixture(autouse=True)
def reset_policy(monkeypatch):
    monkeypatch.setattr(acceleration, "_GPU_REQUESTED", False)
    monkeypatch.setattr(acceleration, "_GPU_DISABLED", False)
    monkeypatch.delenv("PDF2AI_FORCE_CPU", raising=False)


@pytest.mark.parametrize("platform,providers,expected", [
    ("win32", ["DmlExecutionProvider", "CPUExecutionProvider"], True),
    ("win32", ["CPUExecutionProvider"], False),
    ("darwin", ["DmlExecutionProvider", "CPUExecutionProvider"], False),
])
def test_selects_only_available_windows_directml(monkeypatch, platform, providers, expected):
    monkeypatch.setattr(acceleration, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(onnxruntime, "get_available_providers", lambda: providers)
    configured = []
    monkeypatch.setattr(acceleration, "_configure_directml_sessions", lambda: configured.append(True))

    options = acceleration.runtime_options()

    assert options["EngineConfig.onnxruntime.use_dml"] is expected
    assert bool(configured) is expected
    assert options["EngineConfig.onnxruntime.use_cuda"] is False


def test_gpu_failure_retries_once_then_stays_on_cpu(monkeypatch, caplog):
    monkeypatch.setattr(acceleration, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(onnxruntime, "get_available_providers", lambda: ["DmlExecutionProvider", "CPUExecutionProvider"])
    monkeypatch.setattr(acceleration, "_configure_directml_sessions", lambda: None)
    assert acceleration.runtime_options()["EngineConfig.onnxruntime.use_dml"]

    assert acceleration.disable_gpu(RuntimeError("private document content")) is True
    assert acceleration.disable_gpu(RuntimeError()) is False
    assert acceleration.runtime_options()["EngineConfig.onnxruntime.use_dml"] is False
    assert "private document content" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_cpu_failure_does_not_request_gpu_retry():
    assert acceleration.disable_gpu(RuntimeError()) is False


def test_directml_session_options_leave_cpu_settings_intact(monkeypatch):
    from rapidocr.inference_engine.onnxruntime.main import OrtInferSession
    from rapidocr.main import DEFAULT_CFG_PATH
    from rapidocr.utils.parse_parameters import ParseParams

    # Register restoration before the compatibility shim changes the factory.
    original = OrtInferSession.__dict__["_init_sess_opts"]
    monkeypatch.setattr(OrtInferSession, "_init_sess_opts", original)
    monkeypatch.setattr(acceleration, "_SESSION_OPTIONS_PATCHED", False)
    cfg = ParseParams.load(DEFAULT_CFG_PATH).EngineConfig.onnxruntime
    cpu_before = OrtInferSession._init_sess_opts(cfg)
    acceleration._configure_directml_sessions()

    cfg.use_dml = True
    options = OrtInferSession._init_sess_opts(cfg)
    assert options.enable_mem_pattern is False
    assert options.execution_mode == onnxruntime.ExecutionMode.ORT_SEQUENTIAL

    cfg.use_dml = False
    cpu_after = OrtInferSession._init_sess_opts(cfg)
    assert cpu_after.enable_mem_pattern == cpu_before.enable_mem_pattern
    assert cpu_after.execution_mode == cpu_before.execution_mode
