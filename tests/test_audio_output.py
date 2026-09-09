from app.audio.output import SpeakerOutput
from app.config import AudioConfig


class FakeStream:
    active = True
    stopped = False

    def __init__(self) -> None:
        self.abort_calls = 0
        self.start_calls = 0

    def abort(self) -> None:
        self.abort_calls += 1
        self.active = False
        self.stopped = True

    def start(self) -> None:
        self.start_calls += 1


async def test_clear_aborts_without_immediate_mme_restart() -> None:
    speaker = SpeakerOutput(AudioConfig())
    stream = FakeStream()
    speaker._stream = stream  # type: ignore[assignment]
    speaker.begin_turn(1)
    await speaker.clear()
    assert stream.abort_calls == 1
    assert stream.start_calls == 0
