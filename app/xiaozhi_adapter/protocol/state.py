from __future__ import annotations

from enum import Enum


class DeviceState(str, Enum):
    CONNECTING = "connecting"
    IDLE = "idle"
    LISTENING = "listening"
    SPEAKING = "speaking"


class ListenMode(str, Enum):
    AUTO = "auto"
    MANUAL = "manual"
    REALTIME = "realtime"


class DeviceStateTracker:
    """Shadow of the firmware's state machine.

    The device owns its own state machine; running a second authoritative one
    here would let the two disagree. This only mirrors what the firmware does in
    response to the messages that cross the socket, and exists for exactly one
    decision: the device discards downlink audio unless it is Speaking, so the
    adapter must know when it may send.
    """

    def __init__(self) -> None:
        self._state = DeviceState.CONNECTING
        self._mode = ListenMode.AUTO

    @property
    def state(self) -> DeviceState:
        return self._state

    @property
    def mode(self) -> ListenMode:
        return self._mode

    @property
    def accepts_audio(self) -> bool:
        """`Application::OnIncomingAudio` only queues packets while Speaking."""

        return self._state is DeviceState.SPEAKING

    def on_handshake_complete(self) -> None:
        """`on_audio_channel_opened_` moves the device from Connecting to Listening."""

        self._state = DeviceState.LISTENING

    def on_listen(self, state: str, mode: str | None) -> None:
        if mode is not None:
            self._mode = ListenMode(mode)
        if state == "stop":
            self._state = DeviceState.IDLE
        else:
            self._state = DeviceState.LISTENING

    def on_tts_start(self) -> None:
        self._state = DeviceState.SPEAKING

    def on_tts_stop(self) -> None:
        """Manual mode returns to Idle; auto and realtime go back to Listening."""

        if self._state is not DeviceState.SPEAKING:
            return
        self._state = (
            DeviceState.IDLE
            if self._mode is ListenMode.MANUAL
            else DeviceState.LISTENING
        )
