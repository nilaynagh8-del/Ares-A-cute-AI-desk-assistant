"""Gemini Live (gemini-3.1-flash-live-preview) realtime voice session.

Runs its own asyncio loop on a background thread:
  mic (16 kHz PCM)  ->  Gemini Live  ->  speaker (24 kHz PCM)

The Live API does server-side voice-activity-detection and barge-in, so we just
stream the mic continuously and play whatever audio comes back, flushing on an
`interrupted` signal. State changes (idle / listening / speaking) and log lines
are reported through callbacks so the UI (and the robot's eyes) can react.
"""
import asyncio
import queue
import threading
import time

from google import genai
from google.genai import types

import audio_io
import pc_tools


class GeminiVoiceSession:
    def __init__(self, cfg, mic_queue, on_state=None, on_log=None):
        self.cfg = cfg
        self.mic_queue = mic_queue          # bytes of 16 kHz int16 mono PCM
        self.on_state = on_state or (lambda s: None)
        self.on_log = on_log or (lambda m: None)
        self._stop = threading.Event()
        self._thread = None
        self._loop = None
        self._main_task = None
        self._state = "idle"
        self.player = None
        self._last_audio_ts = 0.0     # when we last received reply audio

    # ---- lifecycle -------------------------------------------------------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._thread_main, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        loop, task = self._loop, self._main_task
        if loop and task and not task.done():
            loop.call_soon_threadsafe(task.cancel)

    def _set_state(self, s):
        if s != self._state:
            self._state = s
            self.on_state(s)

    def _build_system_instruction(self):
        import pathlib
        d = pathlib.Path(__file__).resolve().parent

        def read(name):
            f = d / name
            try:
                return f.read_text(encoding="utf-8").strip() if f.exists() else ""
            except Exception:
                return ""

        persona = read("system.md") or self.cfg.get("system_prompt", "")
        mem = read("memory.md")
        out = persona
        if mem:
            out += "\n\n# What you know about your person\n" + mem
        import datetime
        now = datetime.datetime.now()
        out += ("\n\n# Right now\nThe current local time is "
                + now.strftime("%A, %B %d, %Y, %I:%M %p")
                + ". Call get_current_time for the exact time later in the chat.")
        return out.strip() or "You are a friendly robot companion named Ares."

    # ---- background thread ----------------------------------------------
    def _thread_main(self):
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._main_task = self._loop.create_task(self._run())
            self._loop.run_until_complete(self._main_task)
        except asyncio.CancelledError:
            pass
        except Exception as e:  # noqa: BLE001
            self.on_log(f"Gemini error: {e!r}")
        finally:
            self._set_state("idle")
            if self._loop:
                try:
                    self._loop.close()
                except Exception:
                    pass

    async def _run(self):
        key = self.cfg.get("gemini_api_key", "").strip()
        if not key:
            self.on_log("No Gemini API key set - add one in the app or .env.")
            return

        client = genai.Client(
            api_key=key,
            http_options=types.HttpOptions(api_version=self.cfg["api_version"]),
        )
        live_config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=self._build_system_instruction(),
            tools=[
                types.Tool(google_search=types.GoogleSearch()),        # web search
                types.Tool(function_declarations=pc_tools.DECLARATIONS),  # PC control
            ],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=self.cfg.get("voice", "Puck")
                    )
                )
            ),
        )

        player = audio_io.SpeakerPlayer(device=self.cfg.get("output_device"))
        self.player = player
        audio_io.voice_volume = float(self.cfg.get("voice_volume", 1.0))

        self.on_log(f"Connecting to {self.cfg['model']} ...")
        async with client.aio.live.connect(
            model=self.cfg["model"], config=live_config
        ) as session:
            self.on_log("Connected. Listening - just talk.")
            player.start()
            self._set_state("listening")
            sender = asyncio.create_task(self._sender(session, self.mic_queue))
            try:
                await self._receiver(session, player)
            finally:
                sender.cancel()
                player.stop()

    async def _sender(self, session, mic_q):
        while not self._stop.is_set():
            chunk = await asyncio.to_thread(self._next_chunk, mic_q)
            if chunk is None:
                continue
            # half-duplex: while the reply is playing (or just finished), drop
            # mic audio so the speaker echo isn't heard as an interruption.
            if self.player and (self.player.pending_bytes() > 0
                                or time.monotonic() - self._last_audio_ts < 0.4):
                continue
            await session.send_realtime_input(
                audio=types.Blob(data=chunk, mime_type="audio/pcm;rate=16000")
            )

    @staticmethod
    def _next_chunk(mic_q):
        try:
            return mic_q.get(timeout=0.1)
        except queue.Empty:
            return None

    async def _handle_tool_call(self, session, tc):
        responses = []
        for fc in tc.function_calls:
            args = dict(fc.args) if fc.args else {}
            result = await asyncio.to_thread(pc_tools.execute, fc.name, args)
            self.on_log(f"[action] {fc.name} {args} -> {result.get('result', result)}")
            responses.append(types.FunctionResponse(
                id=fc.id, name=fc.name, response=result))
        try:
            await session.send_tool_response(function_responses=responses)
        except Exception as e:  # noqa: BLE001
            self.on_log(f"tool response failed: {e}")

    async def _receiver(self, session, player):
        # receive() yields one turn then completes - loop it so the
        # conversation continues across multiple questions.
        while not self._stop.is_set():
            try:
                async for response in session.receive():
                    if self._stop.is_set():
                        return
                    sc = getattr(response, "server_content", None)
                    if sc is not None and getattr(sc, "interrupted", False):
                        player.flush()
                        self._set_state("listening")
                    tc = getattr(response, "tool_call", None)
                    if tc is not None and getattr(tc, "function_calls", None):
                        await self._handle_tool_call(session, tc)
                    data = getattr(response, "data", None)
                    if data:
                        self._set_state("speaking")
                        self._last_audio_ts = time.monotonic()
                        player.write(data)
                    if sc is not None and getattr(sc, "turn_complete", False):
                        self._set_state("listening")
            except Exception as e:  # noqa: BLE001
                self.on_log(f"Conversation ended: {e!r}")
                return
