"""Realtime OpenAI transcription module for VoiceBoard.

Uses the OpenAI Realtime API via WebSockets for streaming transcription.
Audio is sent as PCM16 at 24 kHz and transcription deltas arrive in real-time.
"""

import asyncio
import base64
import json
import logging
import threading
from typing import Callable, Optional

import websockets
import websockets.asyncio.client

log = logging.getLogger(__name__)

REALTIME_URL = "wss://api.openai.com/v1/realtime"

# The Realtime API WebSocket URL requires a realtime-capable session model.
# The transcription model (e.g. gpt-4o-mini-transcribe) is configured
# separately inside session.update, not in the URL.
REALTIME_SESSION_MODEL = "gpt-4o-mini-realtime-preview"


class RealtimeTranscriber:
    """Streams audio to the OpenAI Realtime API and emits transcription events."""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini-transcribe",
        language: str = "",
    ):
        self._api_key = api_key
        self._model = model  # transcription model for session.update
        self._language = language

        # Callbacks – set by the app layer
        self.on_delta: Optional[Callable[[str], None]] = None
        self.on_completed: Optional[Callable[[str], None]] = None
        self.on_error: Optional[Callable[[str], None]] = None
        self.on_turn_started: Optional[Callable[[], None]] = None

        # Internal state
        self._ws: Optional[websockets.asyncio.client.ClientConnection] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

        # Accumulate deltas per item_id for the completed transcript
        self._transcripts: dict[str, str] = {}

    # ── Public API ──────────────────────────────────────────────

    def update_api_key(self, api_key: str) -> None:
        self._api_key = api_key

    def update_language(self, language: str) -> None:
        self._language = language

    @property
    def is_connected(self) -> bool:
        return self._running and self._ws is not None

    def start(self) -> None:
        """Open a Realtime transcription session in a background thread."""
        if self._running:
            return
        if not self._api_key:
            if self.on_error:
                self.on_error("OpenAI API key is not set. Please configure it in Settings.")
            return

        # If a previous non-blocking stop() left a thread still winding
        # down, wait briefly for it to finish before starting a new one.
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._thread = None

        self._running = True
        self._transcripts.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self, blocking: bool = True) -> None:
        """Gracefully disconnect from the Realtime API.

        If *blocking* is True (the default), waits up to 8 s for the
        background thread to finish.  Pass ``blocking=False`` when
        calling from the GUI thread to avoid freezing the UI — the
        background thread will clean up on its own.
        """

        self._running = False

        # Close the WebSocket gracefully from the event-loop thread;
        # this causes _listen() to exit and the `async with` block to
        # clean up, so the session coroutine finishes naturally.
        ws = self._ws
        loop = self._loop
        if ws is not None and loop is not None and loop.is_running():
            asyncio.run_coroutine_threadsafe(self._close_ws(ws), loop)
        if blocking and self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
            self._ws = None
            self._loop = None

    def send_audio(self, pcm_bytes: bytes) -> None:
        """Send a chunk of raw PCM16 audio (24 kHz, mono, little-endian).

        Safe to call from any thread.
        """
        if not self._running or self._ws is None or self._loop is None:
            return

        b64 = base64.b64encode(pcm_bytes).decode("ascii")
        event = {
            "type": "input_audio_buffer.append",
            "audio": b64,
        }
        try:
            asyncio.run_coroutine_threadsafe(
                self._ws.send(json.dumps(event)), self._loop
            )
        except Exception:
            pass  # connection may have closed

    # ── Internal ────────────────────────────────────────────────

    @staticmethod
    async def _close_ws(ws) -> None:
        """Close the WebSocket connection gracefully."""
        try:
            await ws.close()
        except Exception:
            pass

    def _run_loop(self) -> None:
        """Entry point for the background event-loop thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._session())
        except Exception as exc:
            if self._running:
                log.exception("Realtime session error")
                if self.on_error:
                    self.on_error(str(exc))
        finally:
            self._running = False
            self._ws = None
            # Clean up the event loop so a future start() gets a fresh one
            try:
                self._loop.close()
            except Exception:
                pass
            self._loop = None
            self._thread = None

    async def _session(self) -> None:
        """Connect, configure, and listen for events."""
        url = f"{REALTIME_URL}?model={REALTIME_SESSION_MODEL}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "OpenAI-Beta": "realtime=v1",
        }

        try:
            async with websockets.asyncio.client.connect(
                url,
                additional_headers=headers,
                max_size=2**24,
                close_timeout=5,
            ) as ws:
                self._ws = ws
                # Send session.update to configure transcription
                await self._configure_session(ws)
                # Listen for server events
                await self._listen(ws)
        except websockets.exceptions.ConnectionClosed as exc:
            if self._running:
                log.warning("WebSocket closed unexpectedly: %s", exc)
                if self.on_error:
                    self.on_error(f"Connection closed: {exc}")
            # Otherwise this is an intentional close from stop() — ignore
        except Exception as exc:
            if self._running:
                log.exception("WebSocket error")
                if self.on_error:
                    self.on_error(str(exc))

    async def _configure_session(self, ws) -> None:
        """Send session.update to configure transcription-only mode."""
        transcription_cfg: dict = {
            "model": self._model,
        }
        if self._language:
            transcription_cfg["language"] = self._language

        session_update = {
            "type": "session.update",
            "session": {
                "modalities": ["text"],
                "input_audio_format": "pcm16",
                "input_audio_transcription": transcription_cfg,
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": 0.5,
                    "prefix_padding_ms": 300,
                    "silence_duration_ms": 500,
                    "create_response": False,
                },
            },
        }
        await ws.send(json.dumps(session_update))

    async def _listen(self, ws) -> None:
        """Process incoming server events."""
        async for raw in ws:
            if not self._running:
                break
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue

            etype = event.get("type", "")

            if etype == "conversation.item.input_audio_transcription.delta":
                delta = event.get("delta", "")
                item_id = event.get("item_id", "")
                if delta:
                    self._transcripts.setdefault(item_id, "")
                    self._transcripts[item_id] += delta
                    if self.on_delta:
                        self.on_delta(delta)

            elif etype == "conversation.item.input_audio_transcription.completed":
                transcript = event.get("transcript", "")
                if transcript and self.on_completed:
                    self.on_completed(transcript.strip())

            elif etype == "input_audio_buffer.speech_started":
                if self.on_turn_started:
                    self.on_turn_started()

            elif etype == "error":
                err = event.get("error", {})
                msg = err.get("message", str(err))
                log.error("Realtime API error: %s", msg)
                if self.on_error:
                    self.on_error(msg)

            elif etype == "session.created":
                log.info("Realtime session created: %s", event.get("session", {}).get("id"))

            elif etype == "session.updated":
                log.info("Realtime session configured")
