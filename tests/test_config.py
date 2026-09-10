from pathlib import Path

import pytest

from app.config import AppConfig, ConfigError


def test_loads_repository_config() -> None:
    config = AppConfig.load(Path("config.yaml"))
    assert config.audio.sample_rate == 16000
    assert config.asr.language == "vi"
    assert config.asr.backend == "parakeet"
    assert config.asr.model == "nvidia/parakeet-ctc-0.6b-Vietnamese"


def test_rejects_unknown_keys(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("audio:\n  mystery: true\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="mystery"):
        AppConfig.load(path)
