from app.config import AppConfig
from app.web.model_catalog import build_language_catalogs, build_model_catalog


def test_catalog_exposes_every_active_pipeline_model() -> None:
    models = build_model_catalog(AppConfig.load("config.yaml"))

    assert set(models) == {"asr", "llm", "tts", "vad"}
    assert models["asr"]["model"] == "nvidia/parakeet-ctc-0.6b-Vietnamese"
    assert models["asr"]["checkpoint"] == "parakeet-ctc-0.6b-vi.nemo"
    assert models["llm"]["model"] == "qwen3.5:4b"
    assert models["llm"]["api_base"] == "http://127.0.0.1:11434"
    assert models["llm"]["runtime"] == "Ollama local API"
    assert models["llm"]["client"].startswith("httpx ")
    assert models["tts"]["model"] == "VieNeu-TTS v3 Turbo"
    assert models["vad"]["model"] == "Silero VAD"


def test_catalog_omits_qwen_only_fields_for_parakeet() -> None:
    asr = build_model_catalog(AppConfig())["asr"]

    assert "max_new_tokens" not in asr
    assert "dtype" not in asr


def test_catalog_exposes_english_pipeline() -> None:
    catalogs = build_language_catalogs(AppConfig.load("config.yaml"))

    assert catalogs["en"]["asr"]["model"] == "nvidia/parakeet-tdt-0.6b-v3"
    assert "checkpoint" not in catalogs["en"]["asr"]
    assert "max_new_tokens" not in catalogs["en"]["asr"]
    assert catalogs["en"]["tts"]["model"] == "hexgrad/Kokoro-82M"
    assert catalogs["en"]["tts"]["provider"] == "kokoro"
    assert catalogs["en"]["tts"]["audio_output"] == "24000 Hz mono"
