"""Realtime Soniox transcription module for VoiceBoard.

Uses the Soniox Speech-to-Text WebSocket API for streaming transcription.
Audio is sent as PCM16 at 16 kHz mono and tokens arrive in real-time with
``is_final`` flags indicating whether they are provisional or confirmed.

Non-final tokens are typed immediately for instant feedback.  When final
tokens arrive, the previously typed non-final text is corrected (via
backspaces) and replaced with the confirmed text.

Reference: https://soniox.com/docs/stt/rt/real-time-transcription
"""

import asyncio
import datetime
import json
import logging
import re
import threading
from typing import Callable, Optional

import websockets
import websockets.asyncio.client

log = logging.getLogger(__name__)

SONIOX_WEBSOCKET_URL = "wss://stt-rt.soniox.com/transcribe-websocket"


class RealtimeTranscriber:
    """Streams audio to the Soniox STT API and emits transcription events.

    Tokens arrive with ``is_final`` flags:
      - Non-final tokens are provisional — typed immediately but may change.
      - Final tokens are confirmed — they replace the provisional text.

    Callbacks:
      - ``on_text(text, backspace_count)`` — type *text* after deleting
        *backspace_count* characters of previously typed non-final text.
      - ``on_error(msg)`` — an error occurred.
    """

    def __init__(
        self,
        api_key: str,
        language: str = "",
    ):
        self._api_key = api_key
        self._language = language

        # Callbacks — set by the app layer
        self.on_text: Optional[Callable[[str, int], None]] = None
        self.on_error: Optional[Callable[[str], None]] = None

        # Internal state
        self._ws: Optional[websockets.asyncio.client.ClientConnection] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

        # Track what non-final text has been typed so we can correct it.
        # When final tokens arrive, we backspace over the non-final chars
        # and retype the confirmed text.
        self._nonfinal_typed_text: str = ""

    # ── Public API ──────────────────────────────────────────────

    def update_api_key(self, api_key: str) -> None:
        self._api_key = api_key

    def update_language(self, language: str) -> None:
        self._language = language

    @property
    def is_connected(self) -> bool:
        return self._running and self._ws is not None

    def start(self) -> None:
        """Open a Soniox transcription session in a background thread."""
        if self._running:
            return
        if not self._api_key:
            if self.on_error:
                self.on_error("Soniox API key is not set. Please configure it in Settings.")
            return

        # If a previous non-blocking stop() left a thread still winding
        # down, wait briefly for it to finish before starting a new one.
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._thread = None

        self._running = True
        self._nonfinal_typed_text = ""
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self, blocking: bool = True) -> None:
        """Gracefully disconnect from the Soniox API.

        If *blocking* is True (the default), waits up to 8 s for the
        background thread to finish.  Pass ``blocking=False`` when
        calling from the GUI thread to avoid freezing the UI.
        """
        self._running = False

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
        """Send a chunk of raw PCM16 audio (16 kHz, mono, little-endian).

        Safe to call from any thread.
        """
        if not self._running or self._ws is None or self._loop is None:
            return

        try:
            asyncio.run_coroutine_threadsafe(
                self._ws.send(pcm_bytes), self._loop
            )
        except Exception:
            pass  # connection may have closed

    def finalize(self) -> None:
        """Send a finalize message to force all pending tokens to become final.

        Useful when stopping recording — ensures the last words are confirmed.
        """
        if not self._running or self._ws is None or self._loop is None:
            return
        msg = json.dumps({"type": "finalize"})
        try:
            asyncio.run_coroutine_threadsafe(
                self._ws.send(msg), self._loop
            )
        except Exception:
            pass

    def send_eof(self) -> None:
        """Signal end-of-audio to the server (empty string)."""
        if not self._running or self._ws is None or self._loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(
                self._ws.send(""), self._loop
            )
        except Exception:
            pass

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
                log.exception("Soniox session error")
                if self.on_error:
                    self.on_error(str(exc))
        finally:
            self._running = False
            self._ws = None
            try:
                self._loop.close()
            except Exception:
                pass
            self._loop = None
            self._thread = None

    async def _session(self) -> None:
        """Connect, configure, and listen for events."""
        try:
            async with websockets.asyncio.client.connect(
                SONIOX_WEBSOCKET_URL,
                max_size=2**24,
                close_timeout=5,
            ) as ws:
                self._ws = ws
                # Send config as the first message
                await self._send_config(ws)
                # Listen for token responses
                await self._listen(ws)
        except websockets.exceptions.ConnectionClosed as exc:
            if self._running:
                log.warning("WebSocket closed unexpectedly: %s", exc)
                if self.on_error:
                    self.on_error(f"Connection closed: {exc}")
        except Exception as exc:
            if self._running:
                log.exception("WebSocket error")
                if self.on_error:
                    self.on_error(str(exc))

    async def _send_config(self, ws) -> None:
        """Send the initial configuration message to Soniox."""
        config: dict = {
            "api_key": self._api_key,
            "model": "stt-rt-preview",
            "audio_format": "pcm_s16le",
            "sample_rate": 16000,
            "num_channels": 1,
            # Endpoint detection finalises tokens when the speaker pauses,
            # which gives us confirmed text faster.
            "enable_endpoint_detection": True,
        }

        if self._language:
            config["language_hints"] = [self._language]

        await ws.send(json.dumps(config))

    async def _listen(self, ws) -> None:
        """Process incoming server responses (token streams)."""
        async for raw in ws:
            if not self._running:
                break
            try:
                response = json.loads(raw)
            except json.JSONDecodeError:
                continue

            # Error from server
            if response.get("error_code"):
                msg = f"{response['error_code']} - {response.get('error_message', '')}"
                log.error("Soniox API error: %s", msg)
                if self.on_error:
                    self.on_error(msg)
                continue

            tokens = response.get("tokens")
            if tokens:
                self._process_tokens(tokens)

            # Session finished (server signals end of stream)
            if response.get("finished"):
                log.info("Soniox session finished")
                break

    def _process_tokens(self, tokens: list[dict]) -> None:
        """Handle a batch of tokens from the server.

        Strategy:
        1. Collect final tokens and non-final tokens from this response.
        2. Final tokens: backspace over previously typed non-final text,
           then type the final text.  This "corrects" the provisional text.
        3. Non-final tokens: type them as provisional feedback, remembering
           what was typed so we can backspace later.
        """
        final_text_parts: list[str] = []
        nonfinal_text_parts: list[str] = []

        for token in tokens:
            text = token.get("text", "")
            if not text:
                continue
            # Strip Soniox control tokens (e.g. <end>, <fin>)
            text = re.sub(r"<\w+>", "", text)
            if not text:
                continue
            if token.get("is_final"):
                final_text_parts.append(text)
            else:
                nonfinal_text_parts.append(text)

        final_text = "".join(final_text_parts)
        nonfinal_text = "".join(nonfinal_text_parts)

        if not final_text and not nonfinal_text:
            return

        now = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]

        # How many non-final characters we previously typed that need correction
        backspace_count = len(self._nonfinal_typed_text)

        # The new text to type: confirmed final text + new provisional text
        new_text = final_text + nonfinal_text

        if backspace_count > 0 or new_text:
            print(f"[{now}] bs={backspace_count} final='{final_text}' nonfinal='{nonfinal_text}'")

            if self.on_text:
                self.on_text(new_text, backspace_count)

        # Update tracking: only the non-final portion remains "provisional"
        self._nonfinal_typed_text = nonfinal_text
