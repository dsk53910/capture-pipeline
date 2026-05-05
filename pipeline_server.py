"""
Pipeline server: runs capture + AI, exposes HTTP API for TUI.
Events stored in ring buffer, TUI polls /events?since=X every second.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import yaml
from aiohttp import web
from dotenv import load_dotenv

from main import Pipeline

load_dotenv()

CONFIG_PATH = Path("pipeline_config.yaml")
DEFAULT_CONFIG = {
    "audio_device": None,
    "audio_gain": 1.0,
    "audio_chunk_duration": 15.0,
    "audio_chunk_overlap": 1.0,
    "audio_silence_threshold": 0.01,
    "screen_interval": 5.0,
    "vision_model": "gpt-4o",
    "whisper_model": "whisper-1",
    "summary_model": "gpt-4o",
    "translate": False,
    "translate_to": "Russian",
    "output_dir": "./output",
    "segment_duration": 300.0,
}

MAX_EVENTS = 200


def load_config() -> dict:
    if CONFIG_PATH.exists():
        return yaml.safe_load(CONFIG_PATH.read_text()) or {}
    return {}


def save_config(cfg: dict):
    CONFIG_PATH.write_text(yaml.dump(cfg, allow_unicode=True, default_flow_style=False))


class PipelineServer:
    def __init__(self):
        self._pipeline: Pipeline | None = None
        self._events: list[dict] = []
        self._event_counter = 0
        self._start_time: float = 0
        self._running = False
        self._config: dict = {**DEFAULT_CONFIG, **load_config()}

    def _emit(self, event_type: str, data: dict):
        """Add event to ring buffer."""
        self._event_counter += 1
        self._events.append({
            "id": self._event_counter,
            "type": event_type,
            "data": data,
            "ts": time.time(),
        })
        if len(self._events) > MAX_EVENTS:
            self._events = self._events[-MAX_EVENTS:]

    async def start_pipeline(self, config: dict | None = None):
        if config:
            self._config = {**self._config, **config}
            save_config(self._config)

        if self._pipeline and self._pipeline._running:
            return {"status": "already_running"}

        self._pipeline = Pipeline(
            screen_interval=self._config["screen_interval"],
            audio_chunk_duration=self._config["audio_chunk_duration"],
            audio_silence_threshold=self._config["audio_silence_threshold"],
            audio_device=self._config.get("audio_device"),
            vision_model=self._config["vision_model"],
            whisper_model=self._config["whisper_model"],
            summary_model=self._config["summary_model"],
            output_dir=self._config["output_dir"],
            segment_duration=self._config["segment_duration"],
            translate=self._config.get("translate", False),
            translate_to=self._config.get("translate_to", "Russian"),
        )
        self._pipeline._on_event = self._emit

        self._start_time = time.time()
        self._running = True
        asyncio.create_task(self._run_pipeline())
        return {"status": "started"}

    async def stop_pipeline(self):
        if self._pipeline:
            self._pipeline.stop()
            self._pipeline = None
        self._running = False
        return {"status": "stopped"}

    async def _run_pipeline(self):
        try:
            await self._pipeline.run()
        except Exception as e:
            self._emit("error", {"message": str(e)})
        finally:
            self._running = False

    # ── HTTP handlers ──

    async def handle_status(self, request: web.Request) -> web.Response:
        uptime = time.time() - self._start_time if self._running else 0
        return web.json_response({
            "running": self._running,
            "uptime": round(uptime),
            "config": {k: v for k, v in self._config.items() if "key" not in k.lower()},
        })

    async def handle_config_get(self, request: web.Request) -> web.Response:
        return web.json_response(self._config)

    async def handle_config_put(self, request: web.Request) -> web.Response:
        body = await request.json()
        self._config = {**self._config, **body}
        save_config(self._config)
        return web.json_response({"status": "ok", "config": self._config})

    async def handle_devices(self, request: web.Request) -> web.Response:
        import sounddevice as sd

        devices = []
        try:
            dev_list = sd.query_devices()
            for i, d in enumerate(dev_list):
                if d["max_input_channels"] > 0:
                    devices.append({
                        "index": i,
                        "name": d["name"],
                        "channels_in": d["max_input_channels"],
                        "channels_out": d["max_output_channels"],
                        "default": i == sd.default.device[0],
                    })
        except Exception:
            pass
        return web.json_response(devices)

    async def handle_start(self, request: web.Request) -> web.Response:
        body = await request.json() if request.can_read_body else {}
        result = await self.start_pipeline(body.get("config"))
        return web.json_response(result)

    async def handle_stop(self, request: web.Request) -> web.Response:
        result = await self.stop_pipeline()
        return web.json_response(result)

    async def handle_events(self, request: web.Request) -> web.Response:
        """Return events since given id (polling)."""
        since = int(request.query.get("since", "0"))
        new_events = [e for e in self._events if e["id"] > since]
        return web.json_response({"events": new_events})

    def create_app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/status", self.handle_status)
        app.router.add_get("/config", self.handle_config_get)
        app.router.add_put("/config", self.handle_config_put)
        app.router.add_get("/devices", self.handle_devices)
        app.router.add_post("/start", self.handle_start)
        app.router.add_post("/stop", self.handle_stop)
        app.router.add_get("/events", self.handle_events)
        return app


def main():
    server = PipelineServer()
    app = server.create_app()
    web.run_app(app, host="127.0.0.1", port=8730, access_log=None)


if __name__ == "__main__":
    main()
