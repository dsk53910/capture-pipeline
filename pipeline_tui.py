"""
Textual TUI: connects to pipeline_server, shows settings + live monitor.
"""
from __future__ import annotations

import asyncio
from datetime import datetime

import aiohttp
from textual import on
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import (
    Button,
    Footer,
    Header,
    Label,
    RichLog,
    Select,
    Static,
    Switch,
)


class ServerClient:
    """Async HTTP client to pipeline_server."""

    def __init__(self, base_url: str = "http://127.0.0.1:8730"):
        self._base = base_url
        self._session: aiohttp.ClientSession | None = None

    async def connect(self):
        self._session = aiohttp.ClientSession()

    async def disconnect(self):
        if self._session:
            await self._session.close()
            self._session = None

    async def _get(self, path: str) -> dict:
        to = aiohttp.ClientTimeout(total=10)
        async with self._session.get(f"{self._base}{path}", timeout=to) as r:
            return await r.json()

    async def _post(self, path: str, body: dict | None = None) -> dict:
        to = aiohttp.ClientTimeout(total=10)
        async with self._session.post(f"{self._base}{path}", json=body, timeout=to) as r:
            return await r.json()

    async def _put(self, path: str, body: dict) -> dict:
        to = aiohttp.ClientTimeout(total=10)
        async with self._session.put(f"{self._base}{path}", json=body, timeout=to) as r:
            return await r.json()

    async def get_status(self) -> dict:
        return await self._get("/status")

    async def get_config(self) -> dict:
        return await self._get("/config")

    async def get_devices(self) -> list[dict]:
        return await self._get("/devices")

    async def get_events(self, since: int = 0) -> list[dict]:
        data = await self._get(f"/events?since={since}")
        return data.get("events", [])

    async def save_config(self, config: dict) -> dict:
        return await self._put("/config", config)

    async def start(self, config: dict | None = None) -> dict:
        return await self._post("/start", {"config": config} if config else None)

    async def stop(self) -> dict:
        return await self._post("/stop")


class PipelinesTUI(App):
    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("ctrl+q", "quit", "Quit"),
    ]

    CSS = """
    Screen {
    }
    #main-area {
        layout: horizontal;
        height: 1fr;
    }
    #settings {
        width: 30;
        max-width: 40;
        border: solid $primary;
        padding: 1;
        overflow-y: auto;
    }
    #settings Label {
        margin-top: 1;
    }
    #log-panel {
        width: 1fr;
        border: solid $primary;
        padding: 1;
    }
    #log {
        height: 1fr;
        min-height: 5;
    }
    #status-bar {
        height: 1;
        border: solid $primary;
        padding: 0 1;
    }
    #status-bar Static {
        width: 1fr;
    }
    Button {
        margin: 1 0;
        width: 100%;
    }
    Select {
        width: 100%;
    }
    .section {
        text-style: bold underline;
        color: $accent;
        margin-top: 1;
    }
    .event-vision { color: green; }
    .event-audio { color: white; }
    .event-trans { color: grey; }
    .event-summary { color: blue; }
    .event-tip { color: yellow; }
    .event-error { color: #cc4444; }
    """

    def __init__(self):
        super().__init__()
        self._client = ServerClient()
        self._running = False
        self._config: dict = {}
        self._devices: list[dict] = []
        self._last_event_id = 0
        self._timer_start = 0.0
        self._poll_timer = None
        self._events_timer = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main-area"):
            with Vertical(id="settings"):
                yield Label("⚙ Audio", classes="section")
                yield Select([], id="sel-device", prompt="Loading...")
                yield Select(
                    [(f"{v}x", v) for v in [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]],
                    id="sel-gain", prompt="Gain 1.0x",
                )
                yield Select(
                    [(f"{v}s", v) for v in [0.0, 0.5, 1.0, 1.5, 2.0]],
                    id="sel-overlap", prompt="Overlap 1.0s",
                )
                yield Label("Models", classes="section")
                yield Select(
                    [("gpt-4o", "gpt-4o"), ("gpt-4o-mini", "gpt-4o-mini")],
                    id="sel-vision", prompt="Vision gpt-4o",
                )
                yield Select(
                    [("whisper-1", "whisper-1")],
                    id="sel-whisper", prompt="Whisper-1",
                )
                yield Select(
                    [("gpt-4o", "gpt-4o"), ("gpt-4o-mini", "gpt-4o-mini")],
                    id="sel-summary", prompt="Summary gpt-4o",
                )
                yield Label("Translation", classes="section")
                yield Switch(id="sw-translate", value=False)
                yield Select(
                    [("Russian", "Russian"), ("English", "English")],
                    id="sel-translate-to", prompt="→ Russian",
                )
                yield Label("Intervals", classes="section")
                yield Select(
                    [(f"{v}s", v) for v in [2.0, 3.0, 5.0, 10.0, 15.0, 30.0]],
                    id="sel-screen", prompt="Screen 5s",
                )
                yield Select(
                    [(f"{v//60}m", v) for v in [60, 120, 180, 300, 600, 900]],
                    id="sel-segment", prompt="Segment 5m",
                )
                yield Button("▶ Start", id="btn-start", variant="success")
                yield Button("⏹ Stop", id="btn-stop", variant="error", disabled=True)
                yield Button("💾 Save", id="btn-save")
            with Vertical(id="log-panel"):
                yield Label("Event Log", classes="section")
                yield RichLog(id="log", markup=True)
        yield Static("Status: ● Disconnected", id="status-bar")

    async def on_mount(self):
        self.title = "Capture Pipeline"

        try:
            await self._client.connect()
            self._log("○ Server connected", "event-summary")
        except Exception as e:
            self._log(f"✕ Server unavailable: {e}", "event-error")
            self.query_one("#status-bar", Static).update("Status: ✕ No connection")
            return

        await self._reload()

        # Poll events every second
        self._events_timer = self.set_interval(1.0, self._poll_events)

    async def on_unmount(self):
        if self._events_timer:
            self._events_timer.stop()
        await self._client.disconnect()

    async def _reload(self):
        try:
            self._config = await self._client.get_config()
            self._devices = await self._client.get_devices()
            self._update_settings()
        except Exception:
            pass

    def _update_settings(self):
        sel_dev = self.query_one("#sel-device", Select)
        dev_options = [(d["name"], d["name"]) for d in self._devices]
        sel_dev.set_options(dev_options)
        if self._config.get("audio_device"):
            try:
                sel_dev.value = self._config["audio_device"]
            except Exception:
                pass

        self._set_select("sel-vision", self._config.get("vision_model", "gpt-4o"))
        self._set_select("sel-whisper", self._config.get("whisper_model", "whisper-1"))
        self._set_select("sel-summary", self._config.get("summary_model", "gpt-4o"))
        self.query_one("#sw-translate", Switch).value = self._config.get("translate", False)
        self._set_select("sel-translate-to", self._config.get("translate_to", "Russian"))
        self._set_select("sel-screen", self._config.get("screen_interval", 5.0))
        self._set_select("sel-segment", self._config.get("segment_duration", 300.0))

    def _set_select(self, widget_id: str, value):
        sel = self.query_one(f"#{widget_id}", Select)
        try:
            sel.value = value
        except Exception:
            pass

    @on(Button.Pressed, "#btn-start")
    async def on_start(self):
        config = self._collect_config()
        try:
            await self._client.save_config(config)
        except Exception:
            pass

        try:
            result = await self._client.start(config)
            if result.get("status") == "started":
                self._running = True
                self._log("▶ Capture started", "event-summary")
                start_btn = self.query_one("#btn-start", Button)
                stop_btn = self.query_one("#btn-stop", Button)
                start_btn.disabled = True
                start_btn.variant = "default"
                stop_btn.disabled = False
                stop_btn.variant = "error"
                start_btn.refresh()
                stop_btn.refresh()
                self._timer_start = __import__("time").time()
                self._poll_timer = self.set_interval(1, self._tick_timer)
            else:
                self._log(f"⚠ {result.get('status', 'error')}", "event-tip")
        except Exception as e:
            self._log(f"✕ Start error: {e}", "event-error")

    @on(Button.Pressed, "#btn-stop")
    async def on_stop(self):
        self._running = False
        if self._poll_timer:
            self._poll_timer.stop()
            self._poll_timer = None
        if self._events_timer:
            self._events_timer.stop()
            self._events_timer = None
        self._log("⏹ Stopping...", "event-summary")
        start_btn = self.query_one("#btn-start", Button)
        stop_btn = self.query_one("#btn-stop", Button)
        start_btn.disabled = False
        start_btn.variant = "success"
        stop_btn.disabled = True
        stop_btn.variant = "default"
        start_btn.refresh()
        stop_btn.refresh()
        self.query_one("#status-bar", Static).update("Status: ● Stopped")
        try:
            result = await self._client.stop()
        except Exception:
            pass
        self._events_timer = self.set_interval(1.0, self._poll_events)

    @on(Button.Pressed, "#btn-save")
    async def on_save(self):
        config = self._collect_config()
        try:
            await self._client.save_config(config)
            self._config = config
            self._log("💾 Config saved", "event-summary")
        except Exception as e:
            self._log(f"✕ Save error: {e}", "event-error")

    def _tick_timer(self):
        """Sync timer callback — fast, no blocking."""
        if not self._running:
            return
        elapsed = int(__import__("time").time() - self._timer_start)
        h, m, s = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
        self.query_one("#status-bar", Static).update(
            f"Status: ● Recording ({h:02d}:{m:02d}:{s:02d})"
        )

    def _collect_config(self) -> dict:
        return {
            "audio_device": self.query_one("#sel-device", Select).value,
            "vision_model": self.query_one("#sel-vision", Select).value,
            "whisper_model": self.query_one("#sel-whisper", Select).value,
            "summary_model": self.query_one("#sel-summary", Select).value,
            "translate": self.query_one("#sw-translate", Switch).value,
            "translate_to": self.query_one("#sel-translate-to", Select).value,
            "screen_interval": self.query_one("#sel-screen", Select).value,
            "segment_duration": self.query_one("#sel-segment", Select).value,
        }

    async def _poll_events(self):
        """Fetch new events from server every second."""
        if not self._running:
            return

        try:
            events = await self._client.get_events(self._last_event_id)
            for event in events:
                self._last_event_id = max(self._last_event_id, event["id"])
                self._render_event(event)
        except Exception:
            pass

    def _render_event(self, event: dict):
        etype = event.get("type", "")
        data = event.get("data", {})
        ts = datetime.fromtimestamp(event.get("ts", 0)).strftime("%H:%M:%S")

        if etype == "vision":
            desc = data.get("description", "")[:100]
            self._log(f"🎥 {ts} {desc}...", "event-vision")
        elif etype == "audio":
            text = data.get("text", "")[:100]
            lang = data.get("language", "?")
            self._log(f"🎤 {ts} [{lang}] \"{text}...\"", "event-audio")
            if data.get("translation"):
                trans = data["translation"][:100]
                self._log(f"🌐 {ts} \"{trans}...\"", "event-trans")
        elif etype == "summary":
            num = data.get("number", 0)
            self._log(f"📋 Summary #{num} ready", "event-summary")
        elif etype == "tip":
            self._log(f"💡 {data.get('message', '')}", "event-tip")
        elif etype == "error":
            self._log(f"✕ {data.get('message', '')}", "event-error")

    def _log(self, text: str, css_class: str = ""):
        log = self.query_one("#log", RichLog)
        ts = datetime.now().strftime("%H:%M:%S")
        if css_class:
            log.write(f"[{css_class}][{ts}] {text}[/{css_class}]")
        else:
            log.write(f"[{ts}] {text}")


def main():
    app = PipelinesTUI()
    app.run()


if __name__ == "__main__":
    main()
