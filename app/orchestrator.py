from __future__ import annotations

import asyncio
import contextlib
import re
import time
import unicodedata
from collections.abc import Callable
from typing import Any

import numpy as np

from app.audio.input import MicrophoneInput
from app.audio.output import AudioOutput, SpeakerOutput
from app.cancellation import TurnCancellation
from app.config import AppConfig
from app.conversation.chunker import StreamingTextChunker
from app.conversation.history import ConversationHistory
from app.conversation.speech import prepare_for_speech
from app.runtime import ModelRuntime
from app.state import ConversationState, StateMachine
from app.tools import (
    ToolCall,
    ToolProvider,
    ToolSpec,
    assistant_tool_message,
    tool_result_message,
)
from app.utils.timing import TurnTiming
from app.vad.silero import SileroVADSegmenter, VADEventType


EventHandler = Callable[[dict[str, Any]], None]

# A stalled audio consumer must not block a push-to-talk release forever.
DRAIN_TIMEOUT_SECONDS = 1.0


class VoiceOrchestrator:
    def __init__(
        self,
        config: AppConfig,
        event_handler: EventHandler | None = None,
        *,
        speaker: AudioOutput | None = None,
        use_local_microphone: bool = True,
        runtime: ModelRuntime | None = None,
        language: str | None = None,
        tools: ToolProvider | None = None,
        max_tool_rounds: int = 3,
    ) -> None:
        self.config = config
        self._event_handler = event_handler
        self.tools = tools
        self.max_tool_rounds = max_tool_rounds
        self.state = StateMachine()
        self.history = ConversationHistory(
            config.conversation.system_prompt,
            config.conversation.max_history_turns,
        )
        self.vad: SileroVADSegmenter | None = None
        self.runtime = runtime or ModelRuntime(config)
        self._owns_runtime = runtime is None
        self.language = language or config.asr.language or self.runtime.default_language
        self.asr = self.runtime.asr_for(self.language)
        self.llm = self.runtime.llm
        self.tts = self.runtime.tts_for(self.language)
        self.speaker = speaker or SpeakerOutput(config.audio)
        self._audio_frames: asyncio.Queue[np.ndarray] | None = (
            None if use_local_microphone else asyncio.Queue(maxsize=64)
        )
        self._turn_task: asyncio.Task[None] | None = None
        self._cancellation: TurnCancellation | None = None
        self._turn_id = 0
        self._listen_after = 0.0
        self._interrupt_candidate = False

    @property
    def turn_id(self) -> int:
        return self._turn_id

    async def run(self) -> None:
        speaker_started = False
        try:
            self._log("[APP] Đang load Silero VAD...")
            self.vad = await asyncio.to_thread(
                SileroVADSegmenter,
                self.config.vad,
                self.config.audio.sample_rate,
                self.config.audio.block_size,
            )
            if self._owns_runtime:
                self._log("[APP] Đang load model runtime...")
                await self.runtime.load_language(self.language)
            elif not self.runtime.is_language_loaded(self.language):
                raise RuntimeError(f"Model runtime cho {self.language} chưa được load.")
            await self.speaker.start()
            speaker_started = True
            self._log("[APP] Sẵn sàng. Hãy nói tiếng Việt. Nhấn Ctrl+C để dừng.")
            self._emit(
                "ready",
                backend=self.config.llm.backend,
                model=self.config.llm.model,
                asr_backend=self.config.asr.backend,
                asr_model=self.config.asr.model,
                language=self.language,
            )
            await self._listen_forever()
        finally:
            await self._cancel_turn()
            if speaker_started:
                await self.speaker.close()
            if self._owns_runtime:
                await self.runtime.close()

    async def _listen_forever(self) -> None:
        if self._audio_frames is not None:
            await self._listen_to_browser()
            return
        while True:
            try:
                async with MicrophoneInput(self.config.audio) as microphone:
                    async for frame in microphone.frames():
                        await self._process_audio_frame(frame)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._log(f"[AUDIO] input error: {type(exc).__name__}: {exc}", level="error")
                await self._cancel_turn()
                assert self.vad is not None
                self.vad.reset()
                if self.state.state is not ConversationState.IDLE:
                    self.state = StateMachine()
                    self._emit("state", state=self.state.state.value)
                self._log("[AUDIO] Thử kết nối lại sau 1 giây...")
                await asyncio.sleep(1)

    async def _listen_to_browser(self) -> None:
        assert self._audio_frames is not None
        while True:
            frame = await self._audio_frames.get()
            try:
                await self._process_audio_frame(frame)
            finally:
                self._audio_frames.task_done()

    async def _process_audio_frame(self, frame: np.ndarray) -> None:
        assert self.vad is not None
        if not self._microphone_is_open():
            self.vad.reset()
            return
        for event in self.vad.process(frame):
            if event.kind is VADEventType.SPEECH_START:
                await self._on_speech_start()
            elif event.audio is not None:
                await self._on_speech_end(event.audio)

    def feed_audio(self, frame: np.ndarray) -> None:
        if self._audio_frames is None:
            raise RuntimeError("Orchestrator đang dùng microphone cục bộ.")
        audio = np.asarray(frame, dtype=np.float32).reshape(-1)
        if audio.size != self.config.audio.block_size:
            raise ValueError(
                f"Browser audio cần {self.config.audio.block_size} samples, nhận {audio.size}."
            )
        if self._audio_frames.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                self._audio_frames.get_nowait()
                self._audio_frames.task_done()
        self._audio_frames.put_nowait(audio.copy())

    async def end_utterance(self) -> bool:
        """Finish the utterance in progress immediately.

        For clients that signal end-of-speech themselves (push-to-talk) instead
        of relying on the server's silence detector. Returns False when there is
        nothing buffered.
        """

        if self.vad is None:
            return False
        turn_before = self._turn_id
        await self._drain_pending_audio()
        event = self.vad.flush()
        if event is not None and event.audio is not None:
            await self._on_speech_end(event.audio)
        return self._turn_id != turn_before

    async def _drain_pending_audio(self) -> None:
        """Let queued audio reach the VAD before the utterance is closed.

        `feed_audio` only enqueues; the frames are consumed by another task. A
        push-to-talk release arrives right behind the last audio frame, so
        flushing immediately would cut the utterance short or lose it entirely.
        """

        if self._audio_frames is None:
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._audio_frames.join(), DRAIN_TIMEOUT_SECONDS)

    async def disconnect_audio_client(self) -> None:
        if self.state.state in {ConversationState.PROCESSING, ConversationState.SPEAKING}:
            await self.interrupt(source="browser disconnect")
        elif self.state.state is ConversationState.LISTENING:
            self._transition(ConversationState.IDLE)
        self._interrupt_candidate = False
        if self.vad is not None:
            self.vad.reset()
        if self._audio_frames is not None:
            while not self._audio_frames.empty():
                with contextlib.suppress(asyncio.QueueEmpty):
                    self._audio_frames.get_nowait()
                    self._audio_frames.task_done()

    async def _on_speech_start(self) -> None:
        if not self._microphone_is_open():
            return
        self._log("[VAD] speech started")
        if self._turn_task is not None and not self._turn_task.done():
            self._interrupt_candidate = True
            self._log("[APP] checking voice interrupt")
            return
        self._transition(ConversationState.LISTENING)

    async def _on_speech_end(self, audio: np.ndarray) -> None:
        duration = audio.size / self.config.audio.sample_rate
        self._log(f"[VAD] speech ended: {duration:.2f} s")
        self._emit("metric", name="utterance", value=round(duration * 1000), unit="ms")
        if self._interrupt_candidate:
            self._interrupt_candidate = False
            await self._handle_interrupt_candidate(audio)
            return
        if self.state.state is ConversationState.IDLE:
            # Speech that began while the previous turn was still running, and
            # that turn ended before the user stopped talking. Open the turn now
            # instead of raising on an IDLE -> PROCESSING transition.
            self._transition(ConversationState.LISTENING)
        self._transition(ConversationState.PROCESSING)
        self._turn_id += 1
        cancellation = TurnCancellation()
        self._cancellation = cancellation
        self.speaker.begin_turn(self._turn_id)
        self._turn_task = asyncio.create_task(
            self._process_turn(self._turn_id, audio, cancellation),
            name=f"turn-{self._turn_id}",
        )

    async def _cancel_turn(self) -> None:
        self._interrupt_candidate = False
        if self._cancellation is not None:
            self._cancellation.cancel()
        await self.speaker.clear()
        if self._turn_task is not None and not self._turn_task.done():
            self._turn_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._turn_task
        self._turn_task = None
        self._cancellation = None

    async def _handle_interrupt_candidate(self, audio: np.ndarray) -> None:
        cancellation = TurnCancellation()
        try:
            text = await self.runtime.transcribe(
                audio,
                self.config.audio.sample_rate,
                cancellation,
                language=self.language,
            )
        except Exception as exc:
            self._log(
                f"[ASR] interrupt detection error: {type(exc).__name__}: {exc}",
                level="error",
            )
            return
        self._log(f"[ASR] interrupt candidate: {text}")
        if not _matches_interrupt_phrase(text, self.config.audio.interrupt_phrases):
            self._log("[APP] voice interrupt ignored")
            return
        self._emit("transcript", text=text)
        await self.interrupt(source="voice command")

    async def _process_turn(
        self,
        turn_id: int,
        audio: np.ndarray,
        cancellation: TurnCancellation,
    ) -> None:
        timing = TurnTiming()
        try:
            timing.asr_started = time.perf_counter()
            text = await self.runtime.transcribe(
                audio,
                self.config.audio.sample_rate,
                cancellation,
                language=self.language,
            )
            timing.asr_finished = time.perf_counter()
            self._log(f"[ASR] {text}")
            self._emit("transcript", text=text)
            asr_ms = timing.milliseconds(timing.asr_started, timing.asr_finished)
            self._log(f"[ASR] latency: {asr_ms:.0f} ms")
            self._emit("metric", name="asr", value=round(asr_ms), unit="ms")
            if not _usable_transcript(text):
                self._log("[ASR] Bỏ qua transcript rỗng hoặc không hợp lệ.")
                self._transition(ConversationState.IDLE)
                return

            messages = self.history.messages_with_user(text)
            text_queue: asyncio.Queue[str | None] = asyncio.Queue()
            full_response: list[str] = []
            first_played_task = asyncio.create_task(
                self._record_first_played(turn_id, timing),
                name=f"first-played-{turn_id}",
            )
            timing.llm_started = time.perf_counter()

            llm_task = asyncio.create_task(
                self._produce_llm(messages, text_queue, full_response, timing, cancellation),
                name=f"llm-{turn_id}",
            )
            tts_task = asyncio.create_task(
                self._consume_tts(turn_id, text_queue, timing, cancellation),
                name=f"tts-{turn_id}",
            )
            try:
                await asyncio.gather(llm_task, tts_task)
            except BaseException:
                cancellation.cancel()
                for task in (llm_task, tts_task):
                    task.cancel()
                await asyncio.gather(llm_task, tts_task, return_exceptions=True)
                first_played_task.cancel()
                await asyncio.gather(first_played_task, return_exceptions=True)
                raise
            if timing.first_audio is not None:
                await first_played_task
            else:
                first_played_task.cancel()
                await asyncio.gather(first_played_task, return_exceptions=True)
            await self.speaker.wait_drained(turn_id)
            cancellation.raise_if_cancelled()

            assistant_text = "".join(full_response).strip()
            if assistant_text:
                self.history.commit(text, assistant_text)
                self._emit("assistant_done", text=assistant_text)
            if timing.first_token is not None:
                first_token_ms = timing.milliseconds(timing.llm_started, timing.first_token)
                self._log(f"[LLM] first token: {first_token_ms:.0f} ms")
                self._emit("metric", name="first_token", value=round(first_token_ms), unit="ms")
            generation_ms = timing.milliseconds(timing.llm_started, timing.llm_finished)
            self._log(f"[LLM] generation: {generation_ms:.0f} ms")
            self._emit("metric", name="generation", value=round(generation_ms), unit="ms")
            if timing.first_audio is not None and timing.tts_started is not None:
                first_audio_ms = timing.milliseconds(timing.tts_started, timing.first_audio)
                self._log(f"[TTS] first audio: {first_audio_ms:.0f} ms")
                self._emit("metric", name="first_audio", value=round(first_audio_ms), unit="ms")
            if timing.first_played is not None:
                total_ms = timing.milliseconds(timing.speech_end, timing.first_played)
                self._log(f"[TOTAL] speech-end to first played audio: {total_ms:.0f} ms")
                self._emit("metric", name="total", value=round(total_ms), unit="ms")
            self._transition(ConversationState.IDLE)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            cancellation.cancel()
            await self.speaker.clear()
            self._log(f"[TURN] error: {type(exc).__name__}: {exc}", level="error")
            if self.state.state in {ConversationState.PROCESSING, ConversationState.SPEAKING}:
                self._transition(ConversationState.IDLE)

    async def _record_first_played(self, turn_id: int, timing: TurnTiming) -> None:
        await self.speaker.wait_first_played(turn_id)
        timing.first_played = time.perf_counter()

    async def _produce_llm(
        self,
        messages: list[dict[str, str]],
        text_queue: asyncio.Queue[str | None],
        full_response: list[str],
        timing: TurnTiming,
        cancellation: TurnCancellation,
    ) -> None:
        chunker = StreamingTextChunker(self.config.tts_chunker)
        conversation: list[dict] = list(messages)
        rounds_left = self.max_tool_rounds
        try:
            while True:
                tools = self._tool_specs() if rounds_left > 0 else None
                pending: list[ToolCall] = []
                async for delta in self.runtime.generate_turn(
                    conversation,
                    cancellation,
                    tools,
                ):
                    if delta.text:
                        if timing.first_token is None:
                            timing.first_token = time.perf_counter()
                        full_response.append(delta.text)
                        self._emit("assistant_delta", text=delta.text)
                        for chunk in chunker.push(delta.text):
                            await text_queue.put(chunk)
                    if delta.tool_calls:
                        pending.extend(delta.tool_calls)
                if not pending or rounds_left == 0:
                    break
                rounds_left -= 1
                conversation = conversation + [assistant_tool_message(tuple(pending))]
                for call in pending:
                    conversation.append(
                        tool_result_message(call, await self._run_tool(call, cancellation))
                    )
            for chunk in chunker.flush():
                await text_queue.put(chunk)
        finally:
            timing.llm_finished = time.perf_counter()
            await text_queue.put(None)

    def _tool_specs(self) -> list[ToolSpec] | None:
        if self.tools is None or not self.runtime.supports_tools:
            return None
        return self.tools.specs() or None

    async def _run_tool(self, call: ToolCall, cancellation: TurnCancellation) -> str:
        assert self.tools is not None
        self._log(f"[TOOL] {call.name} {call.arguments}")
        self._emit("tool_call", name=call.name, arguments=call.arguments)
        started = time.perf_counter()
        try:
            result = await self.tools.call(call, cancellation)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            self._log(f"[TOOL] {call.name} error: {message}", level="error")
            self._emit("tool_result", name=call.name, error=message)
            return f"Tool error: {message}"
        latency_ms = (time.perf_counter() - started) * 1000
        self._emit("metric", name="tool_call", value=round(latency_ms), unit="ms")
        self._emit("tool_result", name=call.name, result=result)
        return result

    async def _consume_tts(
        self,
        turn_id: int,
        text_queue: asyncio.Queue[str | None],
        timing: TurnTiming,
        cancellation: TurnCancellation,
    ) -> None:
        while True:
            text = await text_queue.get()
            if text is None:
                return
            text = prepare_for_speech(text, language=self.language)
            if not text:
                continue
            if timing.tts_started is None:
                timing.tts_started = time.perf_counter()
            self._emit("tts_sentence", text=text)
            async for audio in self.runtime.synthesize_stream(
                text,
                cancellation,
                language=self.language,
            ):
                if timing.first_audio is None:
                    timing.first_audio = time.perf_counter()
                    self._transition(ConversationState.SPEAKING)
                await self.speaker.enqueue(turn_id, audio)

    async def interrupt(self, source: str = "web UI") -> None:
        if self.state.state not in {ConversationState.PROCESSING, ConversationState.SPEAKING}:
            return
        self._transition(ConversationState.INTERRUPTED)
        await self._cancel_turn()
        self._transition(ConversationState.IDLE)
        self._log(f"[APP] interrupted from {source}")

    def clear_history(self) -> None:
        self.history.clear()
        self._emit("history_cleared")

    def _transition(self, target: ConversationState) -> None:
        previous = self.state.state
        self.state.transition(target)
        if target is ConversationState.IDLE and previous in {
            ConversationState.PROCESSING,
            ConversationState.SPEAKING,
            ConversationState.INTERRUPTED,
        }:
            self._listen_after = time.monotonic() + self.config.audio.echo_guard_ms / 1000
        self._emit("state", state=target.value)

    def _microphone_is_open(self) -> bool:
        if time.monotonic() < self._listen_after:
            return False
        if not self.config.audio.allow_barge_in and self.state.state in {
            ConversationState.PROCESSING,
            ConversationState.SPEAKING,
            ConversationState.INTERRUPTED,
        }:
            return False
        return True

    def _log(self, message: str, level: str = "info") -> None:
        print(message)
        self._emit("log", message=message, level=level)

    def _emit(self, event_type: str, **data: Any) -> None:
        if self._event_handler is not None:
            self._event_handler({"type": event_type, **data})


def _usable_transcript(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 2:
        return False
    letters = [character for character in stripped if character.isalpha()]
    if not letters:
        return any(character.isdigit() for character in stripped)
    latin_letters = sum(
        ord(character) <= 0x024F or 0x1E00 <= ord(character) <= 0x1EFF
        for character in letters
    )
    return latin_letters / len(letters) >= 0.8


def _matches_interrupt_phrase(text: str, phrases: list[str]) -> bool:
    normalized_text = _normalize_phrase(text)
    prefixes = ("", "hay ", "lam on ", "vui long ", "please ")
    suffixes = {
        "",
        "di",
        "nhe",
        "voi",
        "ngay",
        "duoc roi",
        "giup toi",
        "please",
        "now",
    }
    for prefix in prefixes:
        if prefix and not normalized_text.startswith(prefix):
            continue
        candidate = normalized_text[len(prefix) :]
        for phrase in phrases:
            normalized_phrase = _normalize_phrase(phrase)
            if candidate == normalized_phrase:
                return True
            if candidate.startswith(f"{normalized_phrase} "):
                if candidate[len(normalized_phrase) + 1 :] in suffixes:
                    return True
    return False


def _normalize_phrase(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text).casefold().replace("đ", "d")
    without_marks = "".join(
        character for character in normalized if unicodedata.category(character) != "Mn"
    )
    return " ".join(re.sub(r"[^\w\s]", " ", without_marks, flags=re.UNICODE).split())
