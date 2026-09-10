import pytest

from app.conversation.speech import prepare_for_speech


@pytest.mark.parametrize(
    ("raw", "spoken"),
    [
        ('**"Xin chào..."**', "Xin chào."),
        ("[Tài liệu](https://example.com) cho *bạn*", "Tài liệu cho bạn"),
        ("Giá tăng 50% & thêm + 2", "Giá tăng 50 phần trăm và thêm cộng 2"),
        ("Dòng một\n# Dòng hai!!!", "Dòng một. Dòng hai!"),
        ("Robot 🤖 <đang> {nói}", "Robot đang nói"),
    ],
)
def test_prepare_for_speech_removes_markup_and_pronounceable_symbols(
    raw: str,
    spoken: str,
) -> None:
    assert prepare_for_speech(raw) == spoken
