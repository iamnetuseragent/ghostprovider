"""Cyberpunk-themed screens for ghostprovider."""

import asyncio
import random
import time

from rich.style import Style
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, Center
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import (
    Button, Input, Static, ProgressBar,
    RichLog, ListView, ListItem, Switch,
)

from ghostprovider.analyzer import run_analysis, AnalysisResult, fingerprint_port
from ghostprovider.hoster import (
    analyze_repo, host_project, cleanup, preflight_check,
    verify_deployment, RepoAnalysis, VolumeHint,
)
from ghostprovider.services import (
    list_containers, start_container, stop_container, restart_container,
    remove_container,
    wait_container_ready, container_urls,
)
from ghostprovider.installer import (
    required_tools, missing_tools, install_tools, tool_description,
)


def _hex() -> str:
    return f"0x{random.randint(0x1000, 0xFFFF):04x}"


def _safe_task(coro) -> asyncio.Task:
    """Create a background task and attach an error handler so exceptions
    are not silently swallowed."""
    task = asyncio.create_task(coro)

    def _done_cb(t: asyncio.Task) -> None:
        try:
            t.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    task.add_done_callback(_done_cb)
    return task

class MatrixRain(Widget):
    """Full-screen Matrix-style digital rain animation."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._messages: list[tuple[str, str, bool]] = []
        self._typing = ""
        self._progress = (0, 0)
        self._status = ""

    # ── Public API (matches BootSequence) ────────────────────────────

    def on_mount(self) -> None:
        pass

    def set_progress(self, current: int, total: int) -> None:
        self._progress = (current, total)
        self.refresh()

    def set_status(self, text: str) -> None:
        self._status = text
        self.refresh()

    def reset(self) -> None:
        self._messages.clear()
        self._typing = ""
        self._progress = (0, 0)
        self._status = ""

    def write_ok(self, label: str, addr: str = "") -> None:
        self._messages.append((label, addr, True))
        self._typing = ""
        self.refresh()

    def write_fail(self, label: str, detail: str = "") -> None:
        self._messages.append((label, detail, False))
        self._typing = ""
        self.refresh()

    def write_msg(self, label: str) -> None:
        self._messages.append((label, "", None))
        self._typing = ""
        self.refresh()

    async def typewrite_ok(self, label: str, addr: str = "", speed: float = 0.02) -> None:
        text = f"{addr}  {label}" if addr else label
        self._typing = ""
        for ch in text:
            self._typing += ch
            self.refresh()
            await asyncio.sleep(speed)
        self._messages.append((label, addr, True))
        self._typing = ""
        self.refresh()

    async def typewrite_msg(self, label: str, speed: float = 0.02) -> None:
        self._typing = ""
        for ch in label:
            self._typing += ch
            self.refresh()
            await asyncio.sleep(speed)
        self._messages.append((label, "", None))
        self._typing = ""
        self.refresh()

    async def typewrite_status(self, text: str, speed: float = 0.03) -> None:
        self._typing = ""
        for ch in text:
            self._typing += ch
            self.refresh()
            await asyncio.sleep(speed)
        self._typing = text
        self.refresh()

    def get_visible_text(self) -> str:
        lines: list[str] = []
        for label, extra, ok in self._messages:
            status = "[ OK ]" if ok else "[FAIL]"
            if extra:
                lines.append(f"{status}  {extra}  {label}")
            else:
                lines.append(f"{status}  {label}")
        if self._typing:
            lines.append(f">>> {self._typing}")
        if self._status:
            lines.append(f"  {self._status}")
        return "\n".join(lines)

    # ── Render ──────────────────────────────────────────────────────

    def render(self) -> Text:
        w = self.size.width
        h = self.size.height
        if w <= 0 or h <= 0:
            return Text()

        rows = [Text(" " * w) for _ in range(h)]

        overlay: list[tuple[str, object]] = []
        if self._progress[1] > 0:
            overlay.append(("progress", self._progress))
        for msg in self._messages:
            overlay.append(("msg", msg))
        if self._typing:
            overlay.append(("typing", self._typing))

        if overlay:
            lines: list[Text] = []
            for kind, data in overlay:
                if kind == "progress":
                    cur, tot = data  # type: ignore[misc]
                    pct = f" {cur}/{tot} "
                    bar_w = min(40, w - 10)
                    filled = int(bar_w * cur / tot)
                    bar = "█" * filled + "░" * (bar_w - filled)
                    t = Text()
                    t.append(f"[{bar}]", Style(bold=True, color="#00ff00"))
                    t.append(pct, Style(bold=True, color="#00ff00"))
                    lines.append(t)
                elif kind == "msg":
                    label, extra, ok = data  # type: ignore[misc]
                    if ok is None:
                        color = "#00ff00"
                        text = label
                    else:
                        status = "[  OK  ]" if ok else "[FAILED]"
                        color = "#00ff00" if ok else "#ff0000"
                        text = f"{status}"
                        if extra:
                            text += f"  {extra}"
                        text += f"  {label}"
                    lines.append(Text(text, Style(bold=True, color=color)))
                elif kind == "typing":
                    text = data  # type: ignore[assignment]
                    lines.append(Text(f">>> {text}", Style(color="#00cc00")))

            max_w = max(t.cell_len for t in lines) if lines else 0
            pad = max(0, (w - max_w) // 2)
            mid = max(1, h // 2 - len(lines) // 2)
            for i, t in enumerate(lines):
                r = mid + i
                if 0 <= r < h:
                    rows[r] = Text(" " * pad) + t

        if self._status and h > 1:
            sr = h - 2
            if 0 <= sr < h:
                sep = "─" * (w - 4)
                t = Text(f"  {sep}", Style(color="#004400"))
                rows[sr] = t
            sr = h - 1
            if 0 <= sr < h:
                t = Text(f"  {self._status}  ", Style(bold=True, color="#00ff00"))
                rows[sr] = t

        result = Text()
        for i, row in enumerate(rows):
            if i > 0:
                result.append("\n")
            result.append(row)
        return result


# ── Main Menu Screen ───────────────────────────────────────────────

class MainScreen(Screen):
    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(
                "[bold yellow]⎈ SYSTEM READY ⎈[/bold yellow]\n\n"
                "[red]Your data is your life.\n"
                "Fail to protect it, and you fail to protect your future.\n"
                "Only you decide what that future will be.[/red]",
                id="description",
            ),
            Center(
                Button("▶  INITIALIZE SYSTEM SCAN  ◀", id="btn-analyze", variant="primary"),
            ),
            Center(
                Button("☰  MANAGE ACTIVE SERVICES  ☰", id="btn-services", variant="default"),
            ),
            Static(
                "[dim red]────────────────────────────────[/dim red]\n"
                "[dim red]↑↓[/dim red] [dim]navigate  |  [/dim]"
                "[dim red]Enter[/dim red] [dim]select  |  [/dim]"
                "[dim red]← Esc[/dim red] [dim]exit  |  [/dim]"
                "[dim red]Ctrl+Shift+C[/dim red] [dim]copy[/dim]",
                id="hint",
            ),
            id="main-container",
        )

    def on_mount(self) -> None:
        self.query_one("#btn-analyze", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-analyze":
            self.app.push_screen(AnalysisScreen())
        elif event.button.id == "btn-services":
            self.app.push_screen(ServiceListScreen())

    def on_key(self, event) -> None:
        if event.key in ("escape", "left"):
            self.app.exit()
        elif event.key == "enter":
            focused = self.focused
            if focused and focused.id == "btn-analyze":
                self.app.push_screen(AnalysisScreen())
            elif focused and focused.id == "btn-services":
                self.app.push_screen(ServiceListScreen())
        elif event.key == "down":
            self.query_one("#btn-services", Button).focus()
        elif event.key == "up":
            self.query_one("#btn-analyze", Button).focus()


# ── Analysis Screen (Matrix rain) ─────────────────────────────────────

class AnalysisScreen(Screen):
    BINDINGS = [
        ("escape", "pop_screen"),
        ("left", "pop_screen"),
    ]

    def action_pop_screen(self) -> None:
        self.app.pop_screen()

    def compose(self) -> ComposeResult:
        yield MatrixRain(id="matrix-rain")

    def on_mount(self) -> None:
        rain = self.query_one(MatrixRain)
        _safe_task(self._run_scan(rain))

    async def _animate_dots(self, rain: MatrixRain, base: str, duration: float = 2.0, speed: float = 0.3) -> None:
        end = time.monotonic() + duration
        while time.monotonic() < end:
            for dots in [".", "..", "..."]:
                rain.set_status(f"{base}{dots}")
                await asyncio.sleep(speed)
                if time.monotonic() >= end:
                    break

    async def _run_scan(self, rain: MatrixRain) -> None:
        TOTAL = 8
        rain.set_progress(0, TOTAL)

        await rain.typewrite_status("initializing localhost connection...", speed=0.04)
        await asyncio.sleep(0.5)
        rain.set_progress(1, TOTAL)

        await rain.typewrite_status("authenticating kernel access...", speed=0.04)
        await asyncio.sleep(0.3)
        rain.set_progress(2, TOTAL)

        await rain.typewrite_status("scanning environment...", speed=0.04)
        await asyncio.sleep(0.3)
        rain.set_progress(3, TOTAL)

        rain.set_progress(4, TOTAL)
        await self._animate_dots(rain, "network analysis")
        rain.write_msg("network analysis")

        rain.set_progress(5, TOTAL)
        await self._animate_dots(rain, "port analysis")
        rain.write_msg("port analysis")

        rain.set_progress(6, TOTAL)
        rain.set_status("scanning localhost...")
        await asyncio.sleep(0.3)

        rain.set_progress(7, TOTAL)
        rain.set_status("analyzing system...")
        result = await self._run_analysis_thread()

        rain.set_progress(0, len(result.summary_items))
        for i, (label, ok) in enumerate(result.summary_items):
            if ok:
                await rain.typewrite_msg(label, speed=0.015)
            else:
                rain.write_msg(f"✗ {label}")
            rain.set_progress(i + 1, len(result.summary_items))
            await asyncio.sleep(0.1)

        if result.all_ok:
            rain.set_status("ALL GATEWAYS NOMINAL — Enter to proceed")
        else:
            rain.set_status("SYSTEM COMPROMISED — Enter to continue")

        self._result = result

    async def _run_analysis_thread(self) -> AnalysisResult:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, run_analysis)

    def on_key(self, event) -> None:
        if event.key == "enter" and hasattr(self, "_result"):
            self.app.push_screen("github")


# ── GitHub Input Screen ────────────────────────────────────────────

class GithubScreen(Screen):
    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("[bold red]╔══ ENTER THE ABYSS ══╗[/bold red]", id="github-title"),
            Center(
                Static(
                    "[yellow]Paste a GitHub repository URL below.\n"
                    "Ghostprovider will analyse whether it can be hosted.[/yellow]",
                    id="github-desc",
                ),
            ),
            Input(
                placeholder="https://github.com/user/repository",
                id="github-input",
            ),
            Center(
                Static(
                    "[dim red]Enter[/dim red] [dim]analyse  |  [/dim]"
                    "[dim red]← Esc[/dim red] [dim]return[/dim]",
                    id="github-hint",
                ),
            ),
            id="github-container",
        )

    def on_mount(self) -> None:
        self.query_one("#github-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        url = event.value.strip()
        if url:
            self.app.push_screen(WorkDirPromptScreen(url=url))

    def on_key(self, event) -> None:
        if event.key in ("escape", "left"):
            self.app.pop_screen()


# ── Work Directory Prompt ──────────────────────────────────────────

class WorkDirPromptScreen(Screen):
    BINDINGS = [
        ("escape", "pop_screen"),
        ("left", "pop_screen"),
    ]

    DEFAULT_CSS = """
    WorkDirPromptScreen {
        background: #000;
    }
    #wd-container {
        width: 100%;
        height: 100%;
        background: #000;
    }
    #wd-title {
        align: center top;
        padding: 1 0;
        text-align: center;
    }
    #wd-desc {
        align: center middle;
        text-align: center;
        padding: 0 2;
    }
    #wd-input {
        margin: 0 4;
    }
    #wd-hint {
        align: center middle;
        color: #660000;
        margin: 1 0;
    }
    """

    def __init__(self, url: str):
        self._url = url
        super().__init__()

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(
                "[bold red]╔══ WORK DIRECTORY ══╗[/bold red]",
                id="wd-title",
            ),
            Center(
                Static(
                    "[yellow]Which directory to clone the repository into?\n"
                    "Leave empty for a temporary folder.[/yellow]",
                    id="wd-desc",
                ),
            ),
            Input(
                placeholder="~/ghostprovider (Enter — confirm, Esc — back)",
                id="wd-input",
            ),
            Center(
                Static(
                    "[dim red]Enter[/dim red] [dim]continue  |  [/dim]"
                    "[dim red]Esc[/dim red] [dim]back[/dim]",
                    id="wd-hint",
                ),
            ),
            id="wd-container",
        )

    def on_mount(self) -> None:
        self.call_after_refresh(self._focus_input)

    def _focus_input(self) -> None:
        self.query_one("#wd-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        val = event.value.strip()
        work_dir = val if val else None
        self.app.push_screen(RepoResultScreen(url=self._url, work_dir=work_dir))

    def on_key(self, event) -> None:
        if event.key in ("escape", "left"):
            self.app.pop_screen()

    def _on_paste(self, event) -> None:
        try:
            input_widget = self.query_one("#wd-input", Input)
            text = event.text.splitlines()[0] if event.text else ""
            if text:
                input_widget.insert_text_at_cursor(text)
        except Exception:
            pass
        event.stop()


# ── Result Screen ───────────────────────────────────────────────────

class RepoResultScreen(Screen):
    BINDINGS = [
        ("escape", "pop_screen"),
        ("left", "pop_screen"),
    ]

    def action_pop_screen(self) -> None:
        self.app.pop_screen()

    def __init__(self, url: str, work_dir: str | None = None):
        self._url = url
        self._work_dir = work_dir
        self._volume_mounts: list[tuple[str, str]] | None = None
        super().__init__()

    def compose(self) -> ComposeResult:
        yield MatrixRain(id="matrix-rain")

    def on_mount(self) -> None:
        rain = self.query_one(MatrixRain)
        _safe_task(self._animate_result(rain))

    async def _animate_result(self, rain: MatrixRain) -> None:
        await rain.typewrite_status("initializing target acquisition...", speed=0.04)
        await asyncio.sleep(0.3)
        await rain.typewrite_status("cloning repository...", speed=0.04)
        await asyncio.sleep(0.2)

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, lambda: analyze_repo(self._url, work_dir=self._work_dir),
        )

        await rain.typewrite_status("decompiling structure...", speed=0.04)
        await asyncio.sleep(0.2)

        rain.set_progress(0, 5)
        await rain.typewrite_ok(result.url, addr="TARGET", speed=0.02)
        await rain.typewrite_ok(result.owner or "?", addr="OWNER", speed=0.02)
        await rain.typewrite_ok(result.name or "?", addr="REPO", speed=0.02)
        rain.set_progress(1, 5)

        exists_str = "VERIFIED" if result.exists else "NOT FOUND"
        await rain.typewrite_ok(exists_str, addr="STATUS", speed=0.02)
        await rain.typewrite_ok(result.language, addr="LANG", speed=0.02)
        rain.set_progress(2, 5)

        if result.errors:
            for err in result.errors:
                rain.write_fail(err, detail="ERROR")
            await asyncio.sleep(0.2)

        has_any_file_info = result.has_dockerfile or result.has_compose or result.has_package_json or result.has_requirements
        if has_any_file_info:
            checks = [
                ("DOCKER", result.has_dockerfile),
                ("COMPOSE", result.has_compose),
                ("REQS", result.has_requirements),
                ("GO", result.has_go_mod),
                ("RUST", result.has_cargo),
                ("INDEX", result.has_index),
            ]
            for name, ok in checks:
                mark = "✓" if ok else "✗"
                if ok:
                    await rain.typewrite_ok(f"{name} {mark}", addr="", speed=0.01)
                else:
                    rain.write_fail(f"{name} {mark}", detail="")
        rain.set_progress(3, 5)

        # Show app category
        cat_labels = {
            "media_server": "MEDIA SERVER",
            "web_app": "WEB APP",
            "api_server": "API SERVER",
            "search_engine": "SEARCH ENGINE",
            "desktop_app": "DESKTOP APP",
            "cli": "CLI TOOL",
            "library": "LIBRARY",
            "unknown": "UNKNOWN",
        }
        cat_label = cat_labels.get(result.app_category, result.app_category.upper())
        await rain.typewrite_ok(cat_label, addr="TYPE", speed=0.02)

        if result.app_category == "search_engine":
            await rain.typewrite_ok(
                "🔍 Search engine — serves HTML, works in browser",
                addr="INFO", speed=0.01,
            )
        elif not result.web_app_verified:
            await rain.typewrite_ok(
                result.category_reason or "⚠ May not work in browser",
                addr="WARN", speed=0.01,
            )

        # Show deep analysis
        if result.web_framework:
            await rain.typewrite_ok(
                f"web: {result.web_framework}",
                addr="FRAME", speed=0.02,
            )
        if result.has_http_server:
            await rain.typewrite_ok("HTTP server detected in source", addr="SERVE", speed=0.02)
        if result.has_cli and not result.has_http_server:
            await rain.typewrite_ok("CLI tool (no HTTP server)", addr="CLI", speed=0.02)
        if result.has_desktop_gui:
            await rain.typewrite_ok("Desktop/GUI application", addr="GUI", speed=0.02)
        if result.is_library:
            await rain.typewrite_ok("Library-type project", addr="LIB", speed=0.02)

        # Show volume hints
        if result.volume_hints:
            for hint in result.volume_hints:
                await rain.typewrite_ok(
                    f"{hint.description} → {hint.container_path}",
                    addr="MOUNT", speed=0.01,
                )

        rain.set_progress(4, 5)
        if result.can_host:
            score_str = f"SCORE {result.host_score}/100"
            label = "✓ TARGET COMPATIBLE" if result.host_score >= 50 else "⚠ LOW CONFIDENCE"
            await rain.typewrite_ok(f"{label}  {score_str}", addr="", speed=0.02)
            await rain.typewrite_ok(result.host_recommendation, addr="", speed=0.02)
            rain.set_progress(5, 5)

            need = required_tools(
                result.has_compose, result.has_dockerfile,
                result.has_package_json, result.has_requirements,
                result.has_go_mod, result.has_cargo,
                result.has_index,
            )
            miss = missing_tools(need)

            if miss:
                await rain.typewrite_status("detecting missing dependencies...", speed=0.03)
                for t in miss:
                    rain.write_fail(tool_description(t), detail="MISS")
                rain.set_status(
                    "══ ENTER — INSTALL AND LAUNCH ══   (Esc — cancel)"
                )
                self._install_tools = miss
            else:
                rain.set_status(
                    "══ ENTER — LAUNCH ══   (Esc — back)"
                )
                self._install_tools = []
        else:
            rain.write_fail("✗ TARGET INCOMPATIBLE", detail="")
            rain.write_fail(result.reason, detail="")
            rain.set_status("Enter — return")
            self._install_tools = []

        self._repo_result = result

    async def _do_install_and_deploy(self, result: RepoAnalysis, password: str | None = None) -> None:
        rain = self.query_one(MatrixRain)
        rain.set_status("══ INSTALLING DEPENDENCIES... ══")
        await rain.typewrite_ok("installing dependencies...", addr="INST", speed=0.02)
        for t in self._install_tools:
            await rain.typewrite_ok(tool_description(t), addr="INST", speed=0.01)

        loop = asyncio.get_event_loop()
        failed = await loop.run_in_executor(
            None, lambda: install_tools(self._install_tools, password=password)
        )

        if failed:
            rain.write_fail("FAILED TO INSTALL:", detail="ERR")
            for t in failed:
                rain.write_fail(tool_description(t), detail="ERR")
            rain.write_fail("install manually and try again", detail="ERR")
            rain.set_status("Enter — return")
            self._install_tools = []
            return

        await rain.typewrite_ok("dependencies installed", addr="DONE", speed=0.02)
        await asyncio.sleep(0.3)
        self._start_hosting(result)

    def _start_hosting(self, result: RepoAnalysis) -> None:
        mounts = getattr(self, "_volume_mounts", None)
        wd = getattr(self, "_work_dir", None)
        self.app.push_screen(HostingScreen(result=result, volume_mounts=mounts, work_dir=wd))

    def confirm_and_deploy(self, result: RepoAnalysis) -> None:
        if self._install_tools:
            lines = "\n".join(f"• {tool_description(t)}" for t in self._install_tools)
            msg = f"[yellow]Ghostprovider will install:[/yellow]\n{lines}\n\n[dim](requires sudo)[/dim]"
            self.app.push_screen(ConfirmModal(msg), self._on_install_confirm)
        else:
            self._start_hosting(result)

    def _on_install_confirm(self, confirmed: bool) -> None:
        if confirmed and hasattr(self, "_repo_result"):
            self.app.push_screen(SudoPrompt(), self._on_sudo_password)
        elif hasattr(self, "_repo_result"):
            self._install_tools = []
            rain = self.query_one(MatrixRain)
            rain.set_status("══ INSTALLATION CANCELLED ══   (Esc — back)")

    def _on_sudo_password(self, password: str | None) -> None:
        if password is not None and hasattr(self, "_repo_result"):
            _safe_task(self._do_install_and_deploy(self._repo_result, password))
        elif hasattr(self, "_repo_result"):
            self._install_tools = []
            rain = self.query_one(MatrixRain)
            rain.set_status("══ INSTALLATION CANCELLED ══   (Esc — back)")

    def _on_volume_mounts(self, mounts: list[tuple[str, str]] | None) -> None:
        if mounts is None:
            self._install_tools = []
            rain = self.query_one(MatrixRain)
            rain.set_status("══ MOUNTING CANCELLED ══   (Esc — back)")
            return
        self._volume_mounts = mounts
        if not getattr(self, "_repo_result", None):
            return
        result = self._repo_result
        if result.host_score < 50:
            msg = (
                f"[yellow]Low hosting confidence ({result.host_score}/100)[/yellow]\n\n"
                f"{result.category_reason}\n\n"
                f"{result.host_recommendation}\n\n"
                "[red]This may not be a web application.\n"
                "Browser may show an empty page.\n\n"
                "Still launch it?[/red]"
            )
            self.app.push_screen(ConfirmModal(msg), self._on_nonweb_confirm)
        else:
            self.confirm_and_deploy(result)

    def _on_nonweb_confirm(self, confirmed: bool) -> None:
        if confirmed and hasattr(self, "_repo_result"):
            self.confirm_and_deploy(self._repo_result)
        elif hasattr(self, "_repo_result"):
            self._install_tools = []
            rain = self.query_one(MatrixRain)
            rain.set_status("══ LAUNCH CANCELLED ══   (Esc — back)")

    def on_key(self, event) -> None:
        if event.key == "enter" and hasattr(self, "_repo_result"):
            result = self._repo_result
            if result.can_host:
                if result.volume_hints and getattr(self, "_volume_mounts", None) is None:
                    self.app.push_screen(
                        VolumePromptScreen(result.volume_hints, result.name),
                        self._on_volume_mounts,
                    )
                elif result.host_score < 50:
                    msg = (
                        f"[yellow]Low hosting confidence ({result.host_score}/100)[/yellow]\n\n"
                        f"{result.category_reason}\n\n"
                        f"{result.host_recommendation}\n\n"
                        "[red]This may not be a web application.\n"
                        "Browser may show an empty page.\n\n"
                        "Still launch it?[/red]"
                    )
                    self.app.push_screen(ConfirmModal(msg), self._on_nonweb_confirm)
                else:
                    self.confirm_and_deploy(result)
            else:
                cleanup(result)
                main = self.app.get_screen("main")
                main.query_one("#btn-analyze", Button).focus()
                self.app.switch_screen("main")


# ── Hosting Screen ──────────────────────────────────────────────────

class HostingScreen(Screen):
    BINDINGS = [
        ("escape", "pop_screen"),
        ("left", "pop_screen"),
    ]

    DEFAULT_CSS = """
    HostingScreen {
        background: #000;
    }
    #hosting-container {
        background: #000;
    }
    RichLog {
        background: #000;
    }
    ProgressBar {
        background: #000;
    }
    """

    def action_pop_screen(self) -> None:
        self.app.pop_screen()

    def __init__(self, result: RepoAnalysis, volume_mounts: list[tuple[str, str]] | None = None,
                 work_dir: str | None = None):
        self._result = result
        self._volume_mounts = volume_mounts
        self._work_dir = work_dir
        super().__init__()

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("[bold red]╔══ DEPLOY SEQUENCE ══╗[/bold red]", id="host-title"),
            RichLog(id="host-log", highlight=True, markup=True),
            Center(
                ProgressBar(total=4, id="host-progress", show_eta=False),
            ),
            id="hosting-container",
        )

    def on_mount(self) -> None:
        _safe_task(self._animate_hosting())

    async def _typewrite(self, widget: RichLog, text: str, speed: float = 0.015) -> None:
        widget.write(text)
        await asyncio.sleep(speed)

    async def _animate_hosting(self) -> None:
        log = self.query_one("#host-log", RichLog)
        prog = self.query_one("#host-progress", ProgressBar)

        await self._typewrite(log, f"  [yellow]{_hex()}[/yellow] [dim]initializing deployment...[/dim]")
        await asyncio.sleep(0.3)
        prog.update(progress=1)

        loop = asyncio.get_event_loop()

        try:
            # Pre-flight checks
            await self._typewrite(log, f"  [yellow]{_hex()}[/yellow] [dim]pre-flight checks...[/dim]")
            issues = await loop.run_in_executor(None, preflight_check)
            if issues:
                for iss in issues:
                    await self._typewrite(log, f"  [yellow]  ⚠ {iss}[/yellow]")
                await self._typewrite(log, "  [red]→ pre-flight checks failed, aborting[/red]")
                self._done = True
                await self._typewrite(log, "  [dim yellow]Enter to return[/dim yellow]")
                return

            await self._typewrite(log, f"  [yellow]{_hex()}[/yellow] [dim]breaching target firewall...[/dim]")
            prog.update(progress=2)

            await self._typewrite(log, f"  [yellow]{_hex()}[/yellow] [dim]pulling images & deploying...[/dim]")
            prog.update(progress=2)

            def _on_status(line: str) -> None:
                if line.strip():
                    self.app.call_from_thread(
                        lambda: log.write(f"  [dim]{line[:120]}[/dim]")
                    )

            host_result = await loop.run_in_executor(
                None, lambda: host_project(
                    self._result, 0, volume_mounts=self._volume_mounts,
                    verify=False, work_dir=self._work_dir,
                    on_status=_on_status,
                ),
            )

            prog.update(progress=3)

            await self._typewrite(log, f"  [yellow]{_hex()}[/yellow] [dim]verifying deployment...[/dim]")
            host_result = await loop.run_in_executor(
                None, lambda: verify_deployment(host_result, 300, on_status=_on_status),
            )
            prog.update(progress=4)

            await self._typewrite(log, "")
            if host_result.healthy:
                for url in host_result.urls:
                    await self._typewrite(log, f"  [bold green]  ✓ DEPLOYED AT {url}[/bold green]")
                await self._typewrite(log, "  [dim green]target is live[/dim green]")
            elif host_result.urls:
                for url in host_result.urls:
                    await self._typewrite(log, f"  [bold yellow]  ? CONTAINER RUNNING AT {url}[/bold yellow]")
                await self._typewrite(log, "  [dim yellow]not ready yet (models may still be downloading).[/dim yellow]")
                await self._typewrite(log, "  [dim yellow]container will keep running — check back later.[/dim yellow]")
                if host_result.errors:
                    for err in host_result.errors:
                        await self._typewrite(log, f"  [red]    {err[:200]}[/red]")
            else:
                await self._typewrite(log, "  [bold red]  ✗ DEPLOYMENT FAILED[/bold red]")
                await self._typewrite(log, "  [red]  No accessible URLs found[/red]")
                if host_result.errors:
                    for err in host_result.errors:
                        await self._typewrite(log, f"  [red]    {err[:200]}[/red]")

            self._host_result = host_result

        except Exception as e:
            await self._typewrite(log, "")
            await self._typewrite(log, "  [bold red]  ✗ DEPLOYMENT FAILED[/bold red]")
            await self._typewrite(log, f"  [red]  {e}[/red]")

        await self._typewrite(log, "  [dim yellow]Enter to return[/dim yellow]")
        self._done = True

    def on_key(self, event) -> None:
        if event.key == "enter" and getattr(self, "_done", False):
            while not isinstance(self.app.screen, MainScreen):
                self.app.pop_screen()
            event.stop()


# ── Modals ──────────────────────────────────────────────────────────

class SudoPrompt(Screen):
    """Modal that asks for the sudo password."""

    def compose(self) -> ComposeResult:
        yield Center(
            Vertical(
                Static("[bold red]╔══ SUDO AUTHENTICATION ══╗[/bold red]"),
                Static(
                    "[yellow]Ghostprovider needs sudo privileges\n"
                    "to install missing dependencies.[/yellow]",
                    id="sudo-desc",
                ),
                Input(
                    placeholder="sudo password",
                    password=True,
                    id="sudo-password",
                ),
                Center(
                    Static(
                        "[dim red]Enter[/dim red] [dim]confirm  |  [/dim]"
                        "[dim red]Esc[/dim red] [dim]cancel[/dim]",
                        id="sudo-hint",
                    ),
                ),
                id="sudo-container",
            ),
        )

    def on_mount(self) -> None:
        self.query_one("#sudo-password", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())

    def on_key(self, event) -> None:
        if event.key in ("escape", "left"):
            self.dismiss(None)


class ConfirmModal(Screen):
    def __init__(self, message: str, yes_action: str = ""):
        self._message = message
        self._yes_action = yes_action
        super().__init__()

    def on_mount(self) -> None:
        self.query_one("#modal-yes", Button).focus()

    def compose(self) -> ComposeResult:
        yield Center(
            Static(self._message, id="modal-msg"),
        )
        yield Center(
            Horizontal(
                Button("  YES  ", id="modal-yes", variant="primary"),
                Button("  NO   ", id="modal-no", variant="default"),
                id="modal-buttons",
            ),
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "modal-yes":
            self.dismiss(True)
        else:
            self.dismiss(False)

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(False)
        elif event.key == "enter":
            focused = self.focused
            if focused and focused.id == "modal-yes":
                self.dismiss(True)
            elif focused and focused.id == "modal-no":
                self.dismiss(False)
        elif event.key == "right":
            self.query_one("#modal-no", Button).focus()
        elif event.key == "left":
            self.query_one("#modal-yes", Button).focus()


# ── Volume Prompt Screen ───────────────────────────────────────────

class VolumePromptScreen(Screen):
    BINDINGS = [
        ("escape", "dismiss_cancel"),
        ("left", "dismiss_cancel"),
    ]

    DEFAULT_CSS = """
    VolumePromptScreen {
        background: #000;
    }
    #vp-container {
        width: 100%;
        height: 100%;
        background: #000;
    }
    #vp-title {
        align: center top;
        padding: 1 0;
        text-align: center;
    }
    .vp-hint {
        align: center middle;
        text-align: center;
        color: #660000;
        margin: 1 0;
    }
    .vp-field {
        margin: 0 4;
    }
    .vp-row {
        margin: 1 0;
    }
    .vp-label {
        color: #ffcc00;
        margin: 0 4;
    }
    .vp-desc {
        color: #660000;
        margin: 0 4;
    }
    """

    def __init__(self, hints: list[VolumeHint], repo_name: str):
        self._hints = hints
        self._repo_name = repo_name
        self._inputs: list[Input] = []
        super().__init__()

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(
                f"[bold red]╔══ MOUNT DIRECTORIES: {self._repo_name} ══╗[/bold red]",
                id="vp-title",
            ),
            Center(
                Static(
                    "[yellow]This application requires access to directories on disk.\n"
                    "Specify paths to the relevant folders.[/yellow]",
                    classes="vp-hint",
                ),
            ),
            id="vp-container",
        )
        container = self.query_one("#vp-container", Vertical)
        for hint in self._hints:
            container.mount(
                Vertical(
                    Static(f"  {hint.description}", classes="vp-label"),
                    Static(f"  [dim]container: {hint.container_path}[/dim]", classes="vp-desc"),
                    Input(
                        placeholder=f"path on host (default: {hint.host_default})",
                        classes="vp-field",
                    ),
                    classes="vp-row",
                )
            )
        container.mount(
            Center(
                Horizontal(
                    Button("  DEPLOY  ", id="vp-deploy", variant="primary"),
                    Button("  SKIP  ", id="vp-skip", variant="default"),
                    id="vp-buttons",
                ),
            ),
        )
        container.mount(
            Center(
                Static(
                    "[dim red]Enter[/dim red] [dim]deploy  |  [/dim]"
                    "[dim red]Esc[/dim red] [dim]back[/dim]",
                    classes="vp-hint",
                ),
            ),
        )
        yield from ()

    def on_mount(self) -> None:
        self._inputs = list(self.query(Input))
        if self._inputs:
            self._inputs[0].focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        idx = self._inputs.index(event.input) if event.input in self._inputs else -1
        if 0 <= idx < len(self._inputs) - 1:
            self._inputs[idx + 1].focus()
        else:
            self._do_deploy()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "vp-deploy":
            self._do_deploy()
        elif event.button.id == "vp-skip":
            self.dismiss([])

    def _do_deploy(self) -> None:
        mounts: list[tuple[str, str]] = []
        for i, hint in enumerate(self._hints):
            val = ""
            if i < len(self._inputs):
                val = self._inputs[i].value.strip()
            if not val:
                val = hint.host_default
            if val:
                mounts.append((val, hint.container_path))
        self.dismiss(mounts)

    def dismiss_cancel(self) -> None:
        self.dismiss(None)

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss_cancel()
        elif event.key == "enter" and self.focused:
            if self.focused.id == "vp-deploy":
                self._do_deploy()
            elif self.focused.id == "vp-skip":
                self.dismiss([])


# ── Service Management Screen ──────────────────────────────────────

class ServiceListScreen(Screen):
    BINDINGS = [
        ("escape", "pop_screen"),
        ("left", "pop_screen"),
        ("enter", "toggle_selected"),
        ("r", "restart_selected"),
        ("a", "toggle_all"),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._pending: dict[str, str] = {}
        self._removed_urls: dict[str, str] = {}
        self._show_all: bool = True
        self._refresh_lock = asyncio.Lock()
        self._fingerprints: dict[str, str] = {}
        self._fingerprint_cache_ports: set[int] = set()

    def action_pop_screen(self) -> None:
        self.app.pop_screen()

    def action_toggle_all(self) -> None:
        _safe_task(self._refresh(show_all=not self._show_all))

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("[bold red]══ ACTIVE SERVICES ══[/bold red]", id="services-title"),
            ListView(id="services-list"),
            Static(
                "[dim red]↑↓[/dim red] [dim]navigate  |  [/dim]"
                "[dim red]Enter[/dim red] [dim]toggle  |  [/dim]"
                "[dim red]R[/dim red] [dim]restart  |  [/dim]"
                "[dim red]A[/dim red] [dim]filter  |  [/dim]"
                "[dim red]← Esc[/dim red] [dim]back[/dim]",
                id="services-hint",
            ),
            id="services-container",
        )

    def on_mount(self) -> None:
        _safe_task(self._refresh())

    async def _refresh(self, show_all: bool | None = None) -> None:
        async with self._refresh_lock:
            if show_all is None:
                show_all = getattr(self, "_show_all", False)
            self._show_all = show_all
            self._containers = await asyncio.get_event_loop().run_in_executor(
                None, list_containers, show_all
            )
            self._pending.clear()
            self._rebuild_rows()

    def _rebuild_rows(self) -> None:
        list_view = self.query_one("#services-list", ListView)
        list_view.clear()

        if not self._containers:
            list_view.append(
                ListItem(Static("  No containers or Docker unavailable"))
            )
            return

        for i, c in enumerate(self._containers, 1):
            is_pending = c.name in self._pending
            state_text = self._pending.get(c.name, c.state)
            state_cls = "svc-status-pending" if is_pending else f"svc-status-{c.state}"
            if is_pending:
                switch_value = self._pending[c.name] == "starting"
            else:
                switch_value = c.state == "running"

            urls = container_urls(c.ports)
            url_text = urls[0] if urls else ""
            port = int(urls[0].rsplit(":", 1)[-1]) if urls else 0

            # HTTP-fingerprint the container to get the real service name.
            # Only accept body-matched signatures (confidence >= 75).
            # Server-header fallbacks (Python HTTP Server, Nginx as proxy, etc.)
            # are misleading — fall back to image name instead.
            svc_name = self._fingerprints.get(c.name) or self._fingerprints.get(str(port))
            if not svc_name and port and port not in self._fingerprint_cache_ports:
                self._fingerprint_cache_ports.add(port)
                fp = fingerprint_port(port)
                if fp and fp.confidence >= 75:
                    svc_name = fp.service_name
                    self._fingerprints[c.name] = svc_name
                    self._fingerprints[str(port)] = svc_name

            if svc_name:
                display_name = svc_name
            elif c.image:
                display_name = c.image.split("/")[-1].split(":")[0]
            else:
                display_name = c.name

            buttons = [Button("██", id=f"svc-rm-{c.name}", classes="svc-rm-btn")]

            item = ListItem(
                Horizontal(
                    Static(display_name, classes="svc-name"),
                    Static(state_text, classes=f"svc-status {state_cls}"),
                    Static(url_text, classes="svc-url"),
                    Switch(value=switch_value, classes="svc-toggle"),
                    *buttons,
                    classes="svc-row",
                ),
            )
            list_view.append(item)

        if self._containers:
            list_view.index = 0

    def _toggle_at_index(self, idx: int) -> None:
        if idx < 0 or idx >= len(self._containers):
            return
        container = self._containers[idx]
        action = "stop" if container.state == "running" else "start"
        label = "stopping" if action == "stop" else "starting"
        self._pending[container.name] = label
        self._rebuild_rows()
        _safe_task(self._exec_action(action, container.name))

    def action_toggle_selected(self) -> None:
        list_view = self.query_one("#services-list", ListView)
        if list_view.index is None:
            return
        self._toggle_at_index(list_view.index)

    def action_restart_selected(self) -> None:
        list_view = self.query_one("#services-list", ListView)
        if list_view.index is None:
            return
        idx = list_view.index
        if 0 <= idx < len(self._containers):
            container = self._containers[idx]
            self._pending[container.name] = "restarting"
            self._rebuild_rows()
            _safe_task(self._exec_restart(container.name))

    def on_switch_changed(self, event: Switch.Changed) -> None:
        list_view = self.query_one("#services-list", ListView)
        for idx, child in enumerate(list_view.children):
            try:
                if child.query_one(Switch) is event.switch:
                    if 0 <= idx < len(self._containers):
                        name = self._containers[idx].name
                        if name not in self._pending:
                            self._toggle_at_index(idx)
                    return
            except Exception:
                pass

    async def _exec_action(self, action: str, name: str) -> None:
        loop = asyncio.get_event_loop()
        try:
            if action == "start":
                msg = await loop.run_in_executor(None, start_container, name)
                await loop.run_in_executor(None, wait_container_ready, name)
            elif action == "stop":
                msg = await loop.run_in_executor(None, stop_container, name)
            if not msg.startswith("Failed") and not msg.startswith("Timeout") and "not available" not in msg:
                if hasattr(self, "app") and self.app:
                    self.app.notify(msg, timeout=3)
            else:
                if hasattr(self, "app") and self.app:
                    self.app.notify(msg, severity="error", timeout=5)
        except Exception as e:
            if hasattr(self, "app") and self.app:
                self.app.notify(f"Action error: {e}", severity="error", timeout=5)
        await self._refresh()

    async def _exec_restart(self, name: str) -> None:
        loop = asyncio.get_event_loop()
        try:
            msg = await loop.run_in_executor(None, restart_container, name)
            await loop.run_in_executor(None, wait_container_ready, name)
            if not msg.startswith("Failed") and "not available" not in msg:
                if hasattr(self, "app") and self.app:
                    self.app.notify(msg, timeout=3)
            else:
                if hasattr(self, "app") and self.app:
                    self.app.notify(msg, severity="error", timeout=5)
        except Exception as e:
            if hasattr(self, "app") and self.app:
                self.app.notify(f"Restart error: {e}", severity="error", timeout=5)
        await self._refresh()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if btn_id.startswith("svc-rm-"):
            name = btn_id[len("svc-rm-"):]
            _safe_task(self._exec_remove(name))

    async def _exec_remove(self, name: str) -> None:
        loop = asyncio.get_event_loop()
        try:
            for c in (getattr(self, "_containers", None) or []):
                if c.name == name:
                    repo_url = (c.labels or {}).get("ghostprovider.repo", "")
                    if repo_url:
                        self._removed_urls[name] = repo_url
                    break
            msg = await loop.run_in_executor(None, remove_container, name)
            if "error" in msg.lower() or "failed" in msg.lower():
                if hasattr(self, "app") and self.app:
                    self.app.notify(msg, severity="error", timeout=5)
            else:
                if hasattr(self, "app") and self.app:
                    self.app.notify(msg, timeout=3)
        except Exception as e:
            if hasattr(self, "app") and self.app:
                self.app.notify(f"Remove error: {e}", severity="error", timeout=5)
        await self._refresh()

    async def _exec_reinstall(self, name: str, repo_url: str) -> None:
        if hasattr(self, "app") and self.app:
            self.app.notify(f"Reinstalling {name} from {repo_url}...", timeout=5)
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, remove_container, name)
        except Exception:
            pass
        try:
            from ghostprovider.hoster import analyze_repo, host_project
            result = await loop.run_in_executor(None, analyze_repo, repo_url)
            if not result.can_host:
                if hasattr(self, "app") and self.app:
                    self.app.notify(f"Cannot reinstall: {result.reason}", severity="error", timeout=5)
                return
            host = await loop.run_in_executor(None, host_project, result, 0)
            if host.healthy:
                for url in host.urls:
                    if hasattr(self, "app") and self.app:
                        self.app.notify(f"Reinstalled at {url}", timeout=5)
            else:
                if hasattr(self, "app") and self.app:
                    self.app.notify("Reinstall failed, check containers", severity="error", timeout=5)
            self._removed_urls.pop(name, None)
        except Exception as e:
            if hasattr(self, "app") and self.app:
                self.app.notify(f"Reinstall error: {e}", severity="error", timeout=5)
        await self._refresh()
