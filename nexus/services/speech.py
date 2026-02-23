"""
Azure Speech Services integration for NEXUS.

Receives raw PCM audio chunks from the browser WebSocket, feeds them into
an Azure Speech SDK PushAudioInputStream, and emits transcribed text via
an async queue consumed by MODE 1 (CAPTURE).

Audio format expected from browser (MediaRecorder):
  - PCM 16-bit LE, mono, 16000 Hz
  - Sent as raw binary frames over WebSocket

If the browser sends WebM/Opus (MediaRecorder default), the frontend must
either:
  a) resample/convert to PCM before sending, or
  b) signal NEXUS to use a compressed audio stream format.
  We default to PCM and note this in the frontend.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import azure.cognitiveservices.speech as speechsdk
import structlog

from nexus.config import settings

log = structlog.get_logger(__name__)


class TranscriptEvent:
    __slots__ = ("text", "is_final", "speaker")

    def __init__(self, text: str, is_final: bool, speaker: str | None = None):
        self.text = text
        self.is_final = is_final
        self.speaker = speaker


class SpeechSession:
    """
    One SpeechSession per active NEXUS session WebSocket connection.
    Lifecycle: create → feed audio chunks → close.
    """

    def __init__(self, session_id: str):
        self.session_id = session_id
        self._queue: asyncio.Queue[TranscriptEvent | None] = asyncio.Queue()
        self._loop = asyncio.get_event_loop()

        # Audio stream config: PCM 16-bit, 16kHz, mono
        audio_format = speechsdk.audio.AudioStreamFormat(
            samples_per_second=16000,
            bits_per_sample=16,
            channels=1,
        )
        self._push_stream = speechsdk.audio.PushAudioInputStream(stream_format=audio_format)
        audio_config = speechsdk.audio.AudioConfig(stream=self._push_stream)

        speech_config = speechsdk.SpeechConfig(
            subscription=settings.azure_speech_key,
            region=settings.azure_speech_region,
        )
        speech_config.speech_recognition_language = settings.azure_speech_language
        # Enable diarization (speaker separation) — best effort, 2 speakers default
        speech_config.set_property(
            speechsdk.PropertyId.SpeechServiceConnection_EnableAudioLogging, "false"
        )

        self._recognizer = speechsdk.SpeechRecognizer(
            speech_config=speech_config,
            audio_config=audio_config,
        )

        # Wire up callbacks
        self._recognizer.recognizing.connect(self._on_recognizing)
        self._recognizer.recognized.connect(self._on_recognized)
        self._recognizer.session_stopped.connect(self._on_stopped)
        self._recognizer.canceled.connect(self._on_canceled)

        self._recognizer.start_continuous_recognition()
        log.info("speech_session_started", session_id=session_id)

    # ── Azure SDK callbacks (run on SDK thread, not event loop) ──────────────

    def _put(self, event: TranscriptEvent | None) -> None:
        self._loop.call_soon_threadsafe(self._queue.put_nowait, event)

    def _on_recognizing(self, evt: speechsdk.SpeechRecognitionEventArgs) -> None:
        """Interim result — useful for live display on tablet."""
        if evt.result.text:
            self._put(TranscriptEvent(text=evt.result.text, is_final=False))

    def _on_recognized(self, evt: speechsdk.SpeechRecognitionEventArgs) -> None:
        """Final result — this is what CAPTURE processes."""
        if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech and evt.result.text:
            self._put(TranscriptEvent(text=evt.result.text, is_final=True))

    def _on_stopped(self, evt) -> None:
        log.info("speech_session_stopped", session_id=self.session_id)
        self._put(None)  # sentinel — signal stream end

    def _on_canceled(self, evt: speechsdk.SpeechRecognitionCanceledEventArgs) -> None:
        log.error(
            "speech_recognition_canceled",
            session_id=self.session_id,
            reason=str(evt.cancellation_details.reason),
            error_details=evt.cancellation_details.error_details,
        )
        self._put(None)

    # ── Public API ────────────────────────────────────────────────────────────

    def feed(self, audio_chunk: bytes) -> None:
        """Push raw PCM bytes into the Azure Speech push stream."""
        self._push_stream.write(audio_chunk)

    async def events(self) -> AsyncIterator[TranscriptEvent]:
        """Async generator — yields TranscriptEvents until the session ends."""
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event

    async def close(self) -> None:
        self._push_stream.close()
        await asyncio.get_event_loop().run_in_executor(
            None, self._recognizer.stop_continuous_recognition
        )
        log.info("speech_session_closed", session_id=self.session_id)
