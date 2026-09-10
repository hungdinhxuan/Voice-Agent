from pathlib import Path

import pytest

from app.config import AppConfig, ConfigError


def test_loads_repository_config() -> None:
    config = AppConfig.load(Path("config.yaml"))
    assert config.audio.sample_rate == 16000
    assert config.asr.language == "vi"
    assert config.asr.backend == "parakeet"
    assert config.asr.model == "nvidia/parakeet-ctc-0.6b-Vietnamese"
    assert config.audio.allow_barge_in
    assert "dừng lại" in config.audio.interrupt_phrases
    assert config.runtime.max_concurrent_asr == 1
    assert config.web.default_language == "vi"
    assert config.web.max_sessions == 4
    assert config.english.asr.model == "nvidia/parakeet-tdt-0.6b-v3"
    assert config.english.tts.model == "hexgrad/Kokoro-82M"
    assert config.for_language("en").audio.output_sample_rate == 24000


def test_rejects_unknown_keys(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("audio:\n  mystery: true\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="mystery"):
        AppConfig.load(path)


def test_rejects_empty_interrupt_phrases(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("audio:\n  interrupt_phrases: []\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="interrupt_phrases"):
        AppConfig.load(path)


def test_lan_web_requires_token_tls_and_origin(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("web:\n  host: 0.0.0.0\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="access_token"):
        AppConfig.load(path)


def test_lan_web_accepts_complete_security_config(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        "web:\n"
        "  host: 0.0.0.0\n"
        "  access_token: 0123456789abcdef\n"
        "  tls_certfile: cert.pem\n"
        "  tls_keyfile: key.pem\n"
        "  allowed_origins: [https://voice.local]\n",
        encoding="utf-8",
    )

    config = AppConfig.load(path)

    assert config.web.host == "0.0.0.0"


def test_rejects_non_positive_max_sessions(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("web:\n  max_sessions: 0\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="max_sessions"):
        AppConfig.load(path)
