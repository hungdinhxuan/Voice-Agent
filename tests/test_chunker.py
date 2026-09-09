from app.config import ChunkerConfig
from app.conversation.chunker import StreamingTextChunker


def test_streams_complete_sentences_without_tiny_fragments() -> None:
    chunker = StreamingTextChunker(ChunkerConfig(min_chars=15, preferred_chars=60, max_chars=120))
    assert chunker.push("Được rồi. ") == []
    assert chunker.push("Tôi sẽ kiểm tra giúp bạn. Cứ chờ một chút nhé.") == [
        "Được rồi. Tôi sẽ kiểm tra giúp bạn.",
        "Cứ chờ một chút nhé.",
    ]
    assert chunker.flush() == []


def test_hard_limit_uses_word_boundary() -> None:
    chunker = StreamingTextChunker(ChunkerConfig(min_chars=5, preferred_chars=10, max_chars=20))
    chunks = chunker.push("một hai ba bốn năm sáu bảy")
    assert chunks == ["một hai ba bốn năm"]
    assert chunker.flush() == ["sáu bảy"]
