"""Smart dependency installer for ghostprovider (Arch Linux)."""

import os
import shutil
import subprocess


def required_tools(has_compose: bool, has_dockerfile: bool,
                   has_package_json: bool, has_requirements: bool,
                   has_go_mod: bool, has_cargo: bool,
                   has_index: bool = False) -> list[str]:
    """Return list of required tool names based on repo analysis.

    For Docker-based deployment only git and docker are needed on the host.
    Python, Node.js etc. run inside containers.
    """
    tools = ["git"]
    if has_compose or has_dockerfile or has_index:
        tools.append("docker")
    return tools


def tool_display_name(tool: str) -> str:
    names = {
        "git": "Git",
        "docker": "Docker",
    }
    return names.get(tool, tool)


def tool_description(tool: str) -> str:
    desc = {
        "git": "Git — version control system (cloning repositories)",
        "docker": "Docker — containerization (running isolated services)",
    }
    return desc.get(tool, tool)


def is_installed(tool: str) -> bool:
    return shutil.which(tool) is not None


def missing_tools(tools: list[str]) -> list[str]:
    return [t for t in tools if not is_installed(t)]


def detect_pm() -> str | None:
    """Detect available package manager on Arch Linux.

    AUR helpers (yay, paru) are preferred over plain pacman
    because they handle both official and AUR packages.
    """
    for cmd in ("yay", "paru", "pacman"):
        if shutil.which(cmd):
            return cmd
    return None


_PM_PKGS: dict[str, dict[str, str]] = {
    "pacman": {
        "git": "git",
        "docker": "docker",
    },
    "yay": {
        "git": "git",
        "docker": "docker",
    },
    "paru": {
        "git": "git",
        "docker": "docker",
    },
}

_PM_BASE: dict[str, list[str]] = {
    "pacman": ["pacman", "-S", "--noconfirm"],
    "yay": ["yay", "-S", "--noconfirm", "--needed"],
    "paru": ["paru", "-S", "--noconfirm", "--needed"],
}


def _pm_install_cmd(pm: str, tool: str) -> list[str]:
    pkg = _PM_PKGS.get(pm, {}).get(tool)
    if not pkg:
        return []
    return _PM_BASE.get(pm, []) + [pkg]


def _run_sudo(cmd: list[str], pw_bytes: bytearray | None,
              sudo_path: str | None) -> tuple[int, str]:
    """Run a command with sudo, using password if available.

    Returns (returncode, stderr_output).
    """
    if pw_bytes is not None and sudo_path:
        full_cmd = ["sudo", "-S"] + cmd
        proc = subprocess.Popen(
            full_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        proc.communicate(input=pw_bytes + b"\n", timeout=120)
        return proc.returncode, proc.stderr.decode(errors="replace")
    elif sudo_path:
        result = subprocess.run(
            [sudo_path] + cmd,
            capture_output=True, text=True, timeout=120,
        )
        return result.returncode, result.stderr
    else:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120,
        )
        return result.returncode, result.stderr


def install_tools(tools: list[str], password: str | None = None) -> tuple[list[str], list[str]]:
    """Install missing tools. Returns (failed_tools, warnings).

    If *password* is provided, uses ``sudo -S`` to pass it non-interactively.
    Otherwise the usual ``sudo`` is tried without stdin (will likely fail
    when there is no TTY).

    For security the password is zeroed from memory after use.
    """
    pm = detect_pm()
    if not pm:
        return (tools, [])

    pw_bytes: bytearray | None = None
    if password is not None:
        pw_bytes = bytearray(password, "utf-8")

    sudo_path = shutil.which("sudo")
    failed: list[str] = []
    installed: list[str] = []
    for tool in tools:
        if is_installed(tool):
            continue
        cmd = _pm_install_cmd(pm, tool)
        if not cmd:
            failed.append(tool)
            continue

        try:
            _run_sudo(cmd, pw_bytes, sudo_path)
            if is_installed(tool):
                installed.append(tool)
            else:
                failed.append(tool)
        except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
            failed.append(tool)

    warnings: list[str] = []
    if installed and "docker" in installed:
        warnings.extend(post_install_actions(installed, password))

    if pw_bytes is not None:
        for i in range(len(pw_bytes)):
            pw_bytes[i] = 0

    return (failed, warnings)


def post_install_actions(tools: list[str], password: str | None = None) -> list[str]:
    """Run post-install setup (Docker service, user groups).

    Returns warning messages for the user.
    """
    warnings: list[str] = []
    if "docker" not in tools:
        return warnings

    pw_bytes: bytearray | None = None
    if password is not None:
        pw_bytes = bytearray(password, "utf-8")

    sudo_path = shutil.which("sudo")

    try:
        rc, _ = _run_sudo(
            ["systemctl", "enable", "--now", "docker"],
            pw_bytes, sudo_path,
        )
        if rc != 0:
            warnings.append("Could not start Docker service — run: sudo systemctl enable --now docker")
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
        warnings.append("Could not start Docker service — run: sudo systemctl enable --now docker")

    try:
        username = os.environ.get("USER", "")
        if username:
            rc, _ = _run_sudo(
                ["usermod", "-aG", "docker", username],
                pw_bytes, sudo_path,
            )
            if rc == 0:
                warnings.append("User added to docker group — log out and back in for it to take effect")
            else:
                warnings.append("Could not add user to docker group — run: sudo usermod -aG docker $USER")
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
        warnings.append("Could not add user to docker group — run: sudo usermod -aG docker $USER")

    if pw_bytes is not None:
        for i in range(len(pw_bytes)):
            pw_bytes[i] = 0

    return warnings
