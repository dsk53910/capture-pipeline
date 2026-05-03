"""
Textual TUI: connects to pipeline_server, shows settings + live monitor.
"""
from __future__ import annotations

import asyncio
from datetime import datetime

import aiohttp
from textual import on, work
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
    .event-error { color: red; }
    """

    def __init__(self):
        super().__init__()
        self._client = ServerClient()
        self._running = False
        self._config: dict = {}
        self._devices: list[dict] = []
        self._last_event_id = 0

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main-area"):
            with Vertical(id="settings"):
                yield Label("⚙ Аудио", classes="section")
                yield Select([], id="sel-device", prompt="Загрузка...")
                yield Select(
                    [(f"{v}x", v) for v in [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]],
                    id="sel-gain", prompt="Gain 1.0x",
                )
                yield Select(
                    [(f"{v}s", v) for v in [0.0, 0.5, 1.0, 1.5, 2.0]],
                    id="sel-overlap", prompt="Overlap 1.0s",
                )
                yield Label("Модели", classes="section")
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
                yield Label("Перевод", classes="section")
                yield Switch(id="sw-translate", value=False)
                yield Select(
                    [("Russian", "Russian"), ("English", "English")],
                    id="sel-translate-to", prompt="→ Russian",
                )
                yield Label("Интервалы", classes="section")
                yield Select(
                    [(f"{v}с", v) for v in [2.0, 3.0, 5.0, 10.0, 15.0, 30.0]],
                    id="sel-screen", prompt="Скрин 5с",
                )
                yield Select(
                    [(f"{v//60}м", v) for v in [60, 120, 180, 300, 600, 900]],
                    id="sel-segment", prompt="Сегмент 5м",
                )
                yield Button("▶ Старт", id="btn-start", variant="success")
                yield Button("⏹ Стоп", id="btn-stop", variant="error", disabled=True)
                yield Button("💾 Сохранить", id="btn-save")
            with Vertical(id="log-panel"):
                yield Label("Лог событий", classes="section")
                yield RichLog(id="log", markup=True)
        yield Static("Статус: ● Отключён", id="status-bar")

    async def on_mount(self):
        self.title = "Capture Pipeline"

        try:
            await self._client.connect()
            self._log("○ Сервер подключён", "event-summary")
        except Exception as e:
            self._log(f"✕ Сервер недоступен: {e}", "event-error")
            self.query_one("#status-bar", Static).update("Статус: ✕ Нет соединения")
            return

        await self._reload()

        # Poll events every second
        self.set_interval(1.0, self._poll_events)

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
                self._log("▶ Захват запущен", "event-summary")
                self.query_one("#btn-start", Button).disabled = True
                self.query_one("#btn-stop", Button).disabled = False
                self._update_timer()
            else:
                self._log(f"⚠ {result.get('status', 'ошибка')}", "event-tip")
        except Exception as e:
            self._log(f"✕ Ошибка запуска: {e}", "event-error")

    @on(Button.Pressed, "#btn-stop")
    def on_stop(self):
        self._running = False
        self._log("⏹ Остановка...", "event-summary")
        self.query_one("#btn-start", Button).disabled = False
        self.query_one("#btn-stop", Button).disabled = True
        self.query_one("#status-bar", Static).update("Статус: ● Остановлен")
        asyncio.create_task(self._client.stop())

    @on(Button.Pressed, "#btn-save")
    async def on_save(self):
        config = self._collect_config()
        try:
            await self._client.save_config(config)
            self._config = config
            self._log("💾 Конфигурация сохранена", "event-summary")
        except Exception as e:
            self._log(f"✕ Ошибка сохранения: {e}", "event-error")

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

    @work(exclusive=False)
    async def _update_timer(self):
        start = __import__("time").time()
        while self._running:
            elapsed = int(__import__("time").time() - start)
            h, m, s = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
            self.query_one("#status-bar", Static).update(
                f"Статус: ● Запись ({h:02d}:{m:02d}:{s:02d})"
            )
            await asyncio.sleep(1)

    async def _poll_events(self):
        """Fetch new events from server every second."""
        if not self._running:
            # Still check server status for remote stop
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
            self._log(f"📋 Сводка #{num} готова", "event-summary")
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
