"""GhostProvider — cyberpunk 2077 themed TUI application."""

from textual.app import App, Binding

from ghostprovider.screens import (
    MainScreen, AnalysisScreen, GithubScreen,
    ServiceListScreen,
    MatrixRain,
)

CYBERPUNK_CSS = """
Screen {
    background: #000;
}

Vertical {
    align: center middle;
    background: #000;
}

Center {
    align: center middle;
    width: 100%;
    background: #000;
}

Horizontal {
    background: #000;
}

Static {
    color: #cc0000;
    background: #000;
}

Button {
    background: #000;
    color: #ffcc00;
    border: tall #cc0000;
    padding: 1 4;
    min-width: 26;
    text-align: center;
}

Button:hover {
    background: #cc0000;
    color: #000000;
    border: tall #ffcc00;
}

Button:focus {
    background: #cc0000;
    color: #000000;
    border: tall #ffcc00;
}

Button.-primary {
    background: #cc0000;
    color: #ffcc00;
    border: tall #ffcc00;
}

Button.-primary:hover {
    background: #ffcc00;
    color: #000000;
    border: tall #cc0000;
}

Button.-primary:focus {
    background: #ffcc00;
    color: #000000;
    border: tall #cc0000;
}

Input {
    background: #000;
    color: #ff3333;
    border: tall #cc0000;
    padding: 0 2;
}

Input:focus {
    border: tall #ffcc00;
}

RichLog {
    background: #000;
    color: #cc0000;
    border: none;
    padding: 1;
    margin: 0 2;
}

ProgressBar {
    background: #000;
    color: #cc0000;
    border: none;
}

ProgressBar > .bar {
    background: #1a0000;
}

ProgressBar > .bar > .complete {
    background: #cc0000;
}

ProgressBar > .bar > .remaining {
    background: #1a0000;
}

Header {
    background: #000;
    color: #ffcc00;
}

Footer {
    background: #000;
    color: #660000;
}

Scrollbar {
    background: #1a0000;
}

Scrollbar > .thumb {
    background: #cc0000;
}

ListView {
    background: #000;
    border: none;
}

ListView > ListItem {
    background: #000;
    padding: 0 1;
    height: 3;
}

ListView > ListItem.--highlight {
    background: #2a0000;
}

ListView > ListItem > Horizontal {
    width: 100%;
}

#description {
    align: center middle;
    text-align: center;
    padding: 0 2;
}

#hint {
    align: center middle;
    color: #660000;
    margin: 1 0;
}

#btn-analyze {
    align: center middle;
    margin: 1 0;
}

BootSequence {
    width: 100%;
    height: 100%;
}

#github-title,
#result-title,
#host-title,
#services-title {
    align: center top;
    padding: 1 0;
    text-align: center;
}

#result-log,
#host-log {
    height: 14;
}

#host-progress {
    margin: 0 4;
}

#github-desc {
    align: center middle;
    text-align: center;
    padding: 0 2;
}

#github-input {
    margin: 0 4;
}

#github-hint {
    align: center middle;
    color: #660000;
    margin: 1 0;
}

#result-actions {
    align: center middle;
    margin: 1 0;
}

#modal-msg {
    align: center middle;
    text-align: center;
    padding: 2;
    background: #000;
    border: tall #cc0000;
    margin: 2 4;
}

#modal-buttons {
    align: center middle;
    margin: 1 0;
}

#services-hint {
    align: center middle;
    color: #660000;
    margin: 1 0;
}

#services-list {
    margin: 0 2;
    height: 1fr;
}

/* ── Row ── */
.svc-row {
    width: 100%;
    align: left middle;
}

/* ── Name ── */
.svc-name {
    width: 1fr;
    max-width: 40;
    min-width: 16;
    color: #ffcc00;
    padding: 0 1;
}

/* ── Status ── */
.svc-status {
    width: 12;
    text-align: center;
}

.svc-status-running   { color: #00ff00; }
.svc-status-exited    { color: #ff0000; }
.svc-status-paused    { color: #ffcc00; }
.svc-status-dead      { color: #ff0000; }
.svc-status-created   { color: #666666; }
.svc-status-restarting { color: #ffcc00; }
.svc-status-removing  { color: #ff0000; }
.svc-status-pending   { color: #ffcc00; }

/* ── Address / URL ── */
.svc-url {
    width: 2fr;
    min-width: 20;
    color: #660000;
    text-align: left;
    overflow: hidden;
    text-overflow: ellipsis;
    padding: 0 1;
}

/* ── Toggle ── */
Switch.svc-toggle {
    width: auto;
    min-width: 4;
    margin: 0 1;
    background: #000000;
    border: none;
}

Switch.svc-toggle.-on {
    background: #000000;
    border: none;
}

Switch.svc-toggle .switch--slider {
    color: #000000;
    background: #000000;
}

Switch.svc-toggle.-on .switch--slider {
    color: #cc0000;
    background: #000000;
}

Button.svc-rm-btn {
    color: yellow;
    background: #000000;
    border: none;
    padding: 0 1;
    min-width: 2;
}

Button.svc-rm-btn:hover {
    color: ansi_bright_yellow;
}

#main-container,
#github-container,
#result-container,
#hosting-container,
#services-container {
    width: 100%;
    height: 100%;
    background: #000;
}

#services-list {
    background: #000;
}
"""


class GhostProviderApp(App):
    CSS = CYBERPUNK_CSS
    TITLE = "ghostprovider"
    SUB_TITLE = "⎈"

    SCREENS = {
        "main": MainScreen,
        "github": GithubScreen,
    }

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("c", "copy_visible", "Copy all text", show=False),
        Binding("ctrl+shift+c", "copy_visible", "Copy selected text", show=False),
    ]

    def action_copy_visible(self) -> None:
        screen = self.screen
        rain = screen.query(MatrixRain).first()
        if rain is not None:
            text = rain.get_visible_text()
            if text:
                self.copy_to_clipboard(text)
                self._copy_via_wl(text)
            return
        screen.action_copy_text()

    def _copy_via_wl(self, text: str) -> None:
        import subprocess
        try:
            proc = subprocess.Popen(
                ["wl-copy"], stdin=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            proc.communicate(text.encode("utf-8"), timeout=1)
        except FileNotFoundError:
            pass
        except Exception:
            pass

    def on_mount(self) -> None:
        self.push_screen("main")


if __name__ == "__main__":
    app = GhostProviderApp()
    app.run()
