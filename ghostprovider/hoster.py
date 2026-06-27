"""GitHub repository analysis & hosting logic."""

import json
import os
import random
import re
import socket
import subprocess
import tempfile
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import requests

from ghostprovider.services import _parse_host_port
from ghostprovider.state import register as _register_state


@dataclass
class VolumeHint:
    container_path: str
    description: str
    host_default: str = ""


@dataclass
class HostResult:
    container_ids: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    healthy: bool = False
    errors: list[str] = field(default_factory=list)
    compose_project: str | None = None


GITHUB_URL_RE = re.compile(
    r"^https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$"
)


@dataclass
class RepoAnalysis:
    url: str = ""
    owner: str = ""
    name: str = ""
    exists: bool = False
    has_dockerfile: bool = False
    has_compose: bool = False
    has_package_json: bool = False
    has_requirements: bool = False
    has_go_mod: bool = False
    has_cargo: bool = False
    has_index: bool = False
    language: str = ""
    can_host: bool = False
    reason: str = ""
    clone_path: str | None = None
    errors: list[str] = field(default_factory=list)
    app_category: str = "unknown"
    category_reason: str = ""
    volume_hints: list[VolumeHint] = field(default_factory=list)
    web_app_verified: bool = True
    web_framework: str = ""
    has_http_server: bool = False
    has_cli: bool = False
    is_library: bool = False
    has_desktop_gui: bool = False
    host_score: int = 0
    host_recommendation: str = ""
    deep_analysis: dict[str, Any] = field(default_factory=dict)
    compose_content: str = ""
    compose_images_only: bool = False
    dockerfile_path: str | None = None
    compose_path: str | None = None


def parse_github_url(url: str) -> tuple | None:
    m = GITHUB_URL_RE.match(url.strip())
    if m:
        return m.group(1), m.group(2).rstrip("/")
    return None


def fetch_repo_metadata(owner: str, name: str) -> dict[str, Any] | None:
    try:
        r = requests.get(
            f"https://api.github.com/repos/{owner}/{name}",
            timeout=10,
            headers={"Accept": "application/vnd.github.v3+json"},
        )
        if r.status_code == 200:
            return r.json()
    except requests.RequestException:
        pass
    return None


CATEGORY_KEYWORDS: dict[str, set[str]] = {
    "media_server": {
        "media", "music", "video", "stream", "streaming",
        "podcast", "audio", "photo", "gallery", "player",
        "jellyfin", "plex", "emby", "blackcandy", "navidrome",
        "airsonic", "funkwhale", "koel",
    },
    "web_app": {
        "web", "website", "frontend", "dashboard", "ui", "app",
        "server", "admin", "panel", "cms", "blog", "forum",
        "wiki", "board",
    },
    "api_server": {
        "api", "backend", "graphql", "rest", "grpc",
    },
    "search_engine": {
        "search", "searx", "searxng", "whoogle", "yacy",
        "librey", "shiori", "gigablast", "manticore",
    },
    "desktop_app": {
        "desktop", "electron", "gtk", "qt", "tui", "tauri",
        "nw.js", "react-native",
    },
    "cli": {
        "cli", "command-line", "console", "terminal",
    },
    "library": {
        "library", "sdk", "framework", "client", "sdk-",
        "plugin", "extension", "middleware",
    },
}

MEDIA_SERVER_INDICATORS: set[str] = CATEGORY_KEYWORDS["media_server"]
SEARCH_ENGINE_INDICATORS: set[str] = CATEGORY_KEYWORDS["search_engine"]
NOT_WEB_TOPICS: set[str] = {
    "desktop-app", "library", "cli", "command-line", "sdk",
    "react-native", "electron-app",
}

# ── Deep dependency & source analysis ──────────────────────────────

PYTHON_WEB_DEPS: set[str] = {
    "flask", "django", "fastapi", "aiohttp", "tornado", "bottle",
    "pyramid", "sanic", "falcon", "starlette", "quart", "cherrypy",
    "hug", "masonite", "responder",
    "uvicorn", "gunicorn", "waitress", "daphne", "hypercorn",
    "uvicorn[standard]", "gunicorn[gevent]",
}

PYTHON_CLI_DEPS: set[str] = {
    "click", "typer", "cement", "cliff", "cleo", "invoke",
    "plac", "python-fire",
}

PYTHON_GUI_DEPS: set[str] = {
    "PyQt5", "PyQt6", "PySide2", "PySide6", "wxPython", "PyGTK",
    "Kivy", "DearPyGui", "pygame", "pyglet", "toga",
}

NODE_WEB_DEPS: set[str] = {
    "express", "next", "nuxt", "fastify", "koa", "hapi", "sails",
    "meteor", "restify", "feathers", "adonisjs", "loopback",
    "moleculer", "derby", "total.js",
    "@sveltejs/kit", "@angular/core", "@nestjs/core",
    "gatsby", "remix", "astro", "svelte", "vue", "react",
    "angular", "preact", "solid-js",
    "strapi", "keystone", "ghost", "directus", "payload",
    "next-server", "nuxt3", "vue-router",
}

NODE_CLI_DEPS: set[str] = {
    "commander", "yargs", "meow", "oclif", "vorpal", "ink",
}

NODE_GUI_DEPS: set[str] = {
    "electron", "electron-builder", "nw.js", "proton-native",
}

GO_WEB_DEPS: set[str] = {
    "gin", "fiber", "echo", "chi", "gorilla/mux", "beego",
    "revel", "buffalo", "iris", "httprouter", "negroni",
    "gin-gonic/gin", "gofiber/fiber", "labstack/echo", "go-chi/chi",
    "gorilla/mux",
}

GO_CLI_DEPS: set[str] = {
    "cobra", "urfave/cli", "pflag",
}

RUST_WEB_DEPS: set[str] = {
    "actix-web", "axum", "rocket", "warp", "tide", "salvo",
    "poem", "trillium", "nickel", "iron", "gotham", "tiny_http",
    "actix-rt",
}

RUST_CLI_DEPS: set[str] = {
    "clap", "structopt", "argh", "gumdrop",
}

RUST_GUI_DEPS: set[str] = {
    "tauri", "egui", "iced", "druid", "gtk",
}


def _parse_requirements_txt(project_dir: Path) -> set[str]:
    req_file = project_dir / "requirements.txt"
    if not req_file.exists():
        return set()
    try:
        deps: set[str] = set()
        for line in req_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            m = re.match(r"^([a-zA-Z0-9_.-]+)", line)
            if m:
                deps.add(m.group(1).lower().rstrip(">===<~!@"))
        return deps
    except OSError:
        return set()


def _parse_pyproject_toml_deps(project_dir: Path) -> set[str]:
    pyproj = project_dir / "pyproject.toml"
    if not pyproj.exists():
        return set()
    try:
        deps: set[str] = set()
        content = pyproj.read_text()
        lines = content.splitlines()

        # PEP 621 format: [project] with dependencies = ["pkg", ...]
        # Also support: [tool.poetry.dependencies] and [project.dependencies]
        in_project = False
        in_deps_table = False
        in_deps_list = False
        bracket_depth = 0

        for line in lines:
            stripped = line.strip()

            # Section headers
            if re.match(r"^\[project\]$", stripped):
                in_project = True
                in_deps_table = False
                in_deps_list = False
                continue
            if re.match(r"^\[(project\.dependencies|tool\.poetry\.dependencies)\]$", stripped):
                in_deps_table = True
                in_project = False
                in_deps_list = False
                continue
            if re.match(r"^\[", stripped):
                in_project = False
                in_deps_table = False
                in_deps_list = False
                if not stripped.startswith("[tool."):
                    continue

            # Table dependencies: pkg = "^1.0" or pkg = {version = "^1.0"}
            if in_deps_table:
                m = re.match(r'([a-zA-Z0-9_.-]+)\s*=', stripped)
                if m:
                    pkg = m.group(1).lower().rstrip(">===<~!@")
                    if pkg not in ("python", "python-versions", "python-version"):
                        deps.add(pkg)

            # PEP 621 inline list under [project]
            if in_project:
                if "dependencies" in stripped and "[" in stripped:
                    in_deps_list = True
                    bracket_depth = stripped.count("[") - stripped.count("]")
                    # Extract from same line
                    m = re.findall(r'"([^"]+)"', stripped.split("[", 1)[1])
                    for d in m:
                        pkg = re.match(r"([a-zA-Z0-9_.-]+)", d)
                        if pkg:
                            deps.add(pkg.group(1).lower().rstrip(">===<~!@"))
                    if bracket_depth <= 0:
                        in_deps_list = False
                    continue
                if in_deps_list:
                    bracket_depth += stripped.count("[") - stripped.count("]")
                    m = re.findall(r'"([^"]+)"', stripped)
                    for d in m:
                        pkg = re.match(r"([a-zA-Z0-9_.-]+)", d)
                        if pkg:
                            deps.add(pkg.group(1).lower().rstrip(">===<~!@"))
                    if bracket_depth <= 0:
                        in_deps_list = False

        return deps
    except OSError:
        return set()


def _collect_python_deps(project_dir: Path) -> set[str]:
    return _parse_requirements_txt(project_dir) | _parse_pyproject_toml_deps(project_dir)


def _collect_node_deps(project_dir: Path) -> dict[str, str] | None:
    pkg = project_dir / "package.json"
    if not pkg.exists():
        return None
    try:
        data = json.loads(pkg.read_text())
        all_deps: dict[str, str] = {}
        for section in ("dependencies", "devDependencies", "peerDependencies"):
            all_deps.update(data.get(section, {}))
        return all_deps
    except (json.JSONDecodeError, OSError):
        return None


def _collect_go_deps(project_dir: Path) -> set[str]:
    gomod = project_dir / "go.mod"
    if not gomod.exists():
        return set()
    try:
        deps: set[str] = set()
        for line in gomod.read_text().splitlines():
            # Formats:
            #   "    github.com/gin-gonic/gin v1.9.0"
            #   "require github.com/gin-gonic/gin v1.9.0"
            #   '	github.com/gin-gonic/gin v1.9.0'
            m = re.search(r'\s*([a-zA-Z0-9_.-]+(?:\/[a-zA-Z0-9_.-]+)*)\s+v', line)
            if m:
                full = m.group(1)
                deps.add(full)
                # Also add short form (strip known hosting prefixes)
                short = re.sub(r'^(github\.com|gopkg\.in|gitlab\.com|bitbucket\.org)/', '', full)
                if short != full:
                    deps.add(short)
        return deps
    except OSError:
        return set()


def _collect_rust_deps(project_dir: Path) -> set[str]:
    cargo = project_dir / "Cargo.toml"
    if not cargo.exists():
        return set()
    try:
        deps: set[str] = set()
        content = cargo.read_text()
        in_deps = False
        for line in content.splitlines():
            if re.match(r"^\[dependencies\]", line):
                in_deps = True
                continue
            if in_deps:
                if re.match(r"^\[", line):
                    break
                m = re.match(r'([a-zA-Z0-9_-]+)\s*=', line.strip())
                if m:
                    deps.add(m.group(1).lower().replace("_", "-"))
        return deps
    except OSError:
        return set()


def _scan_python_source(project_dir: Path) -> dict[str, Any]:
    info: dict[str, Any] = {
        "has_http_server": False,
        "has_cli": False,
        "is_library": False,
        "has_desktop_gui": False,
    }
    http_patterns = [
        r"app\.run\s*\(", r"uvicorn\.run\s*\(", r"gunicorn",
        r"make_server\s*\(", r"application\.run\s*\(",
        r"web\.run\s*\(", r"sanic\.run\s*\(", r"aiohttp\.web",
        r"HTTPServer\s*\(", r"ThreadingHTTPServer\s*\(",
        r"django\.core\.management", r"DJANGO_SETTINGS_MODULE",
        r"masonite", r"flask\.Flask\s*\(", r"Flask\s*\(",
        r"FastAPI\s*\(", r"Starlette\s*\(", r"bottle\.run",
        r"tornado\.web\.Application",
    ]
    cli_patterns = [
        r"argparse\s*\.", r"ArgumentParser\s*\(", r"click\.(command|group|option)",
        r"typer\.", r"fire\.Fire\s*\(", r"cement",
    ]
    gui_patterns = [
        r"tkinter", r"PyQt5", r"PyQt6", r"PySide", r"wx\.Frame",
        r"kivy\.app", r"dearpygui", r"pygame",
    ]
    for pyfile in project_dir.rglob("*.py"):
        if pyfile.stat().st_size > 50000:
            continue
        try:
            content = pyfile.read_text(errors="replace")
            for pat in http_patterns:
                if re.search(pat, content):
                    info["has_http_server"] = True
                    break
            for pat in cli_patterns:
                if re.search(pat, content):
                    info["has_cli"] = True
                    break
            for pat in gui_patterns:
                if re.search(pat, content):
                    info["has_desktop_gui"] = True
                    break
        except (OSError, UnicodeDecodeError):
            continue
    return info


def _scan_node_source(project_dir: Path) -> dict[str, Any]:
    info: dict[str, Any] = {
        "has_http_server": False,
        "has_cli": False,
        "is_library": False,
        "has_desktop_gui": False,
    }
    http_patterns = [
        r"app\.listen\s*\(", r"server\.listen\s*\(", r"createServer\s*\(",
        r"http\.createServer", r"express\s*\(", r"Fastify\s*\(",
        r"Koa\s*\(", r"socket\.io",
    ]
    cli_patterns = [
        r"commander", r"yargs", r"argv", r"process\.argv",
        r"meow\s*\(", r"oclif",
    ]
    gui_patterns = [
        r"electron", r"nw\.js", r"BrowserWindow",
    ]
    for jsfile in list(project_dir.rglob("*.js")) + list(project_dir.rglob("*.ts")):
        if jsfile.stat().st_size > 100000:
            continue
        if "node_modules" in str(jsfile):
            continue
        try:
            content = jsfile.read_text(errors="replace")
            for pat in http_patterns:
                if re.search(pat, content):
                    info["has_http_server"] = True
                    break
            for pat in cli_patterns:
                if re.search(pat, content):
                    info["has_cli"] = True
                    break
            for pat in gui_patterns:
                if re.search(pat, content):
                    info["has_desktop_gui"] = True
                    break
        except (OSError, UnicodeDecodeError):
            continue
    return info


def _scan_go_source(project_dir: Path) -> dict[str, Any]:
    info: dict[str, Any] = {
        "has_http_server": False,
        "has_cli": False,
        "is_library": False,
        "has_desktop_gui": False,
    }
    http_patterns = [
        r"http\.ListenAndServe", r"http\.ListenAndServeTLS",
        r"gin\.Default\s*\(", r"gin\.New\s*\(", r"fiber\.New\s*\(",
        r"echo\.New\s*\(", r"chi\.NewRouter", r"mux\.NewRouter",
        r"beego\.Run", r"iris\.New", r"buffalo",
    ]
    cli_patterns = [
        r"cobra\.Command", r"cobra\.Execute", r"flag\.",
        r"pflag\.", r"cli\.App",
    ]
    for gofile in project_dir.rglob("*.go"):
        if gofile.stat().st_size > 100000:
            continue
        try:
            content = gofile.read_text(errors="replace")
            for pat in http_patterns:
                if re.search(pat, content):
                    info["has_http_server"] = True
                    break
            for pat in cli_patterns:
                if re.search(pat, content):
                    info["has_cli"] = True
                    break
        except (OSError, UnicodeDecodeError):
            continue
    return info


def _scan_rust_source(project_dir: Path) -> dict[str, Any]:
    info: dict[str, Any] = {
        "has_http_server": False,
        "has_cli": False,
        "is_library": False,
        "has_desktop_gui": False,
    }
    http_patterns = [
        r"actix_web::", r"axum::", r"rocket::", r"warp::filter",
        r"warp::path", r"Tide::new", r"salvo::",
        r"HttpServer::new", r"Server::bind",
    ]
    cli_patterns = [
        r"clap::", r"StructOpt", r"argh::",
    ]
    for rsfile in project_dir.rglob("*.rs"):
        if rsfile.stat().st_size > 100000:
            continue
        try:
            content = rsfile.read_text(errors="replace")
            for pat in http_patterns:
                if re.search(pat, content):
                    info["has_http_server"] = True
                    break
            for pat in cli_patterns:
                if re.search(pat, content):
                    info["has_cli"] = True
                    break
        except (OSError, UnicodeDecodeError):
            continue
    return info


def _is_library_project(project_dir: Path, analysis: RepoAnalysis) -> bool:
    """Heuristic: project looks like a library (not an application)."""
    if analysis.language == "Python":
        has_setup = (project_dir / "setup.py").exists() or (project_dir / "setup.cfg").exists()
        has_pyproject = (project_dir / "pyproject.toml").exists()
        has_entry = (project_dir / "__main__.py").exists() or any(
            (project_dir / f).exists()
            for f in ("app.py", "main.py", "server.py", "manage.py", "wsgi.py", "asgi.py", "run.py")
        )
        has_src = (project_dir / "src").is_dir()
        if has_setup and not has_entry:
            return True
        if has_pyproject and not has_entry and has_src:
            return True
    if analysis.language == "Node.js":
        pkg_json = project_dir / "package.json"
        if pkg_json.exists():
            try:
                pkg = json.loads(pkg_json.read_text())
                has_main = bool(pkg.get("main"))
                has_bin = bool(pkg.get("bin"))
                has_scripts = bool(pkg.get("scripts"))
                has_web_app_sections = any(
                    s in str(pkg) for s in ("react", "next", "nuxt", "express", "angular")
                )
                is_pure_lib = has_main and not has_bin and not has_scripts
                return is_pure_lib and not has_web_app_sections
            except (json.JSONDecodeError, OSError):
                pass
    if analysis.language == "Rust":
        cargo = project_dir / "Cargo.toml"
        has_main = (project_dir / "src" / "main.rs").exists()
        has_lib = (project_dir / "src" / "lib.rs").exists()
        if cargo.exists():
            try:
                content = cargo.read_text()
                if "[lib]" in content and "[[bin]]" not in content:
                    return True
                # Has lib.rs but no main.rs → library
                if has_lib and not has_main:
                    return True
            except OSError:
                pass
    if analysis.language == "Go":
        main_go = project_dir / "main.go"
        if not main_go.exists():
            has_main_func = False
            for gofile in project_dir.rglob("*.go"):
                if gofile.stat().st_size > 10000:
                    continue
                try:
                    if "func main()" in gofile.read_text(errors="replace"):
                        has_main_func = True
                        break
                except OSError:
                    continue
            if not has_main_func:
                return True
    return False


def detect_app_category(
    analysis: RepoAnalysis,
    metadata: dict[str, Any] | None,
) -> tuple[str, str, bool]:
    description = (metadata or {}).get("description", "") or ""
    topics = set((metadata or {}).get("topics", []) or [])
    name_lower = analysis.name.lower()
    desc_lower = description.lower()
    combined = f"{name_lower} {desc_lower}"

    desc_kws = {"desktop", "electron", "cli", "command line", "terminal",
                "library", "sdk", "framework"}
    media_kws = {"media", "music", "video", "stream", "streaming",
                 "podcast", "audio", "photo", "gallery", "player",
                 "jellyfin", "plex", "emby", "blackcandy", "navidrome",
                 "airsonic", "funkwhale", "koel"}
    search_kws = {"search", "searx", "searxng", "whoogle", "yacy",
                  "librey", "shiori", "gigablast"}
    web_kws = {"web", "website", "frontend", "dashboard", "ui", "app",
               "server", "admin", "panel", "cms", "blog", "forum",
               "wiki", "board", "api", "backend", "graphql", "rest"}

    # ── Phase 1: Deep analysis signals (most reliable) ──
    if analysis.deep_analysis:
        da = analysis.deep_analysis
        # Web signals first (strongest indicators)
        if da.get("web_framework"):
            return "web_app", f"Deep analysis: {da['web_framework']}", True
        if da.get("has_http_server"):
            return "web_app", "Deep analysis: HTTP server code found", True
        # Non-web signals (only if no web signal present)
        if da.get("has_desktop_gui"):
            return "desktop_app", "Deep analysis: GUI framework detected", False
        if da.get("has_cli") and not da.get("has_http_server"):
            return "cli", "Deep analysis: CLI interface detected", False
        if da.get("is_library"):
            return "library", "Deep analysis: project identified as library", False

    # ── Phase 2: GitHub metadata (fast, no clone needed) ──
    for kw in desc_kws:
        if kw in desc_lower or kw in name_lower:
            if kw in ("library", "sdk", "framework"):
                return "library", f"GitHub description/library keyword: {kw}", False
            if kw in ("desktop", "electron"):
                return "desktop_app", f"GitHub description: {kw}", False
            if kw in ("cli", "command line", "terminal"):
                return "cli", f"GitHub description: {kw}", False
    if topics & NOT_WEB_TOPICS:
        topic_str = ", ".join(sorted(topics & NOT_WEB_TOPICS))
        if topics & {"desktop-app", "electron-app"}:
            return "desktop_app", f"GitHub topics: {topic_str}", False
        return "library", f"GitHub topics: {topic_str}", False

    search_topics = {"search-engine", "search", "searx", "searxng", "whoogle", "yacy"}
    if topics & search_topics:
        topic_str = ", ".join(sorted(topics & search_topics))
        return "search_engine", f"GitHub topics: {topic_str}", True

    for kw in search_kws:
        if kw in combined:
            return "search_engine", f"Keyword: {kw}", True
    for kw in media_kws:
        if kw in combined:
            return "media_server", f"Keyword: {kw}", True
    for kw in web_kws:
        if kw in combined:
            return "web_app", f"Keyword: {kw}", True

    # ── Phase 3: File-level fallback ──
    if analysis.has_index:
        return "web_app", "Static site (index.html)", True
    if analysis.has_compose:
        return "web_app", "Docker Compose project", True
    if analysis.has_dockerfile:
        return "web_app", "Dockerfile project", True

    return "unknown", "Could not determine application type from available signals", True


def _parse_compose_volumes(project_dir: Path, compose_subpath: str | None = None) -> list[VolumeHint]:
    hints: list[VolumeHint] = []
    compose_file = None
    if compose_subpath and (project_dir / compose_subpath).is_file():
        compose_file = compose_subpath
    else:
        for f in ("docker-compose.yml", "docker-compose.yaml", "compose.yaml", "compose.yml"):
            if (project_dir / f).exists():
                compose_file = f
                break
        if not compose_file:
            for subdir in _SUBDIRS_TO_SCAN:
                for f in _COMPOSE_NAMES:
                    candidate = f"{subdir}/{f}"
                    if (project_dir / candidate).is_file():
                        compose_file = candidate
                        break
                if compose_file:
                    break
    if not compose_file:
        return hints

    try:
        content = (project_dir / compose_file).read_text()
    except OSError:
        return hints

    # Match volume definitions in YAML that reference host paths or env vars
    patterns = [
        (r'\$\{(.+?):-(\.?/?[^}]*)\}:(/[^"\s]+)', "env_var"),
        (r'"(\.?/[^"]+)":(/\S+)', "quoted_host"),
        (r'"(\.?/[^"]+)":(/[^"]+)', "quoted_host_alt"),
        (r"'(\.?/[^']+)':(/\S+)", "single_quoted"),
        (r"\s+-\s+(\./[\S]+):(/\S+)", "relative"),
        (r"\s+-\s+(\./[\S]+):(/[^\s]+)", "relative_alt"),
    ]

    for pat, kind in patterns:
        for m in re.finditer(pat, content):
            if kind == "env_var":
                var_name = m.group(1)
                default_path = m.group(2)
                container_path = m.group(3)
            else:
                default_path = m.group(1)
                container_path = m.group(2)
                var_name = ""

            # Skip named volumes (no / in container path) and already-found
            if not container_path.startswith("/"):
                continue
            if any(h.container_path == container_path for h in hints):
                continue

            desc = _describe_volume(container_path, var_name)
            hints.append(VolumeHint(
                container_path=container_path,
                description=desc,
                host_default=default_path,
            ))

    return hints


def _describe_volume(container_path: str, var_name: str = "") -> str:
    known: dict[str, str] = {
        "/music": "Music directory",
        "/media": "Media files",
        "/downloads": "Downloads directory",
        "/data": "Application data",
        "/config": "Configuration",
        "/app/data": "Application data",
        "/app/config": "Configuration",
        "/app/media": "Media files",
        "/app/music": "Music directory",
        "/var/lib/postgresql/data": "PostgreSQL data",
        "/var/lib/mysql": "MySQL data",
        "/var/lib/redis": "Redis data",
        "/etc/nginx/conf.d": "Nginx configuration",
        "/storage": "Data storage",
        "/backups": "Backups",
    }

    if var_name:
        var_lower = var_name.lower()
        var_desc = {
            "music_dir": "Music directory",
            "media_dir": "Media files",
            "data_dir": "Application data",
            "config_dir": "Configuration",
            "download_dir": "Downloads directory",
            "library_path": "Library path",
            "content_dir": "Content directory",
        }
        if var_lower in var_desc:
            return var_desc[var_lower]

    for path_prefix, desc in known.items():
        if container_path == path_prefix or container_path.startswith(path_prefix + "/"):
            return desc

    base = container_path.rsplit("/", 1)[-1]
    return f"Directory: {base}"


def _parse_dockerfile_volumes(project_dir: Path, dockerfile_subpath: str | None = None) -> list[VolumeHint]:
    hints: list[VolumeHint] = []
    dockerfiles: list[Path] = []
    if dockerfile_subpath and (project_dir / dockerfile_subpath).is_file():
        dockerfiles.append(project_dir / dockerfile_subpath)
    else:
        for df_name in ("Dockerfile", "Dockerfile.ghost"):
            df = project_dir / df_name
            if df.is_file():
                dockerfiles.append(df)
        if not dockerfiles:
            for subdir in _SUBDIRS_TO_SCAN:
                for df_name in _DOCKERFILE_NAMES:
                    df = project_dir / subdir / df_name
                    if df.is_file():
                        dockerfiles.append(df)
                if dockerfiles:
                    break
    for df in dockerfiles:
        try:
            for line in df.read_text().splitlines():
                m = re.search(r"VOLUME\s+\[?\"?\'?(/[^\]\"\']+)", line)
                if m:
                    path = m.group(1)
                    if not any(h.container_path == path for h in hints):
                        hints.append(VolumeHint(
                            container_path=path,
                            description=_describe_volume(path),
                            host_default=f".{path}",
                        ))
        except OSError:
            pass
    return hints


def detect_volume_hints(analysis: RepoAnalysis) -> list[VolumeHint]:
    if not analysis.clone_path:
        return []
    project_dir = Path(analysis.clone_path)
    hints = _parse_compose_volumes(project_dir, analysis.compose_path)
    hints.extend(_parse_dockerfile_volumes(project_dir, analysis.dockerfile_path))
    return hints


def _deep_analyze_project(analysis: RepoAnalysis) -> RepoAnalysis:
    """Run deep, dependency & source-code-level analysis on a cloned project.

    Examines dependency files (requirements.txt, package.json, go.mod,
    Cargo.toml) and scans source code for HTTP servers, CLI interfaces,
    GUI frameworks, and library patterns.

    Performance note: when docker-compose or Dockerfile is present the
    expensive per-file source scanning is skipped — the compose/Dockerfile
    strategy handles deployment regardless.
    """
    if not analysis.clone_path:
        return analysis

    project_dir = Path(analysis.clone_path)
    da: dict[str, Any] = {
        "web_framework": "",
        "has_http_server": False,
        "has_cli": False,
        "is_library": False,
        "has_desktop_gui": False,
        "gui_dep": False,
        "gh_description_web": False,
        "gh_topics_media": False,
        "github_not_web": False,
    }

    # ── 1. Dependency analysis ──
    if analysis.has_requirements:
        py_deps = _collect_python_deps(project_dir)
        web_dep = (py_deps & PYTHON_WEB_DEPS)
        cli_dep = (py_deps & PYTHON_CLI_DEPS)
        gui_dep = (py_deps & PYTHON_GUI_DEPS)
        if web_dep:
            da["web_framework"] = next(iter(web_dep))
        if cli_dep:
            da["has_cli"] = True
        if gui_dep:
            da["has_desktop_gui"] = True
            da["gui_dep"] = True
        da["_py_deps"] = py_deps

    if analysis.has_package_json:
        nd = _collect_node_deps(project_dir)
        if nd:
            all_dep_names = set(nd.keys())
            web_dep = all_dep_names & NODE_WEB_DEPS
            cli_dep = all_dep_names & NODE_CLI_DEPS
            gui_dep = all_dep_names & NODE_GUI_DEPS
            if web_dep:
                wf = next(iter(web_dep))
                da["web_framework"] = wf
                if "/" in wf:
                    da["web_framework"] = wf.split("/")[-1]
            if cli_dep:
                da["has_cli"] = True
            if gui_dep:
                da["has_desktop_gui"] = True
                da["gui_dep"] = True
        da["_node_deps"] = nd

    if analysis.has_go_mod:
        go_deps = _collect_go_deps(project_dir)
        web_dep = go_deps & GO_WEB_DEPS
        cli_dep = go_deps & GO_CLI_DEPS
        if web_dep:
            wf = next(iter(web_dep))
            short = wf.split("/")[-1] if "/" in wf else wf
            da["web_framework"] = short
        if cli_dep:
            da["has_cli"] = True
        da["_go_deps"] = go_deps

    if analysis.has_cargo:
        rs_deps = _collect_rust_deps(project_dir)
        web_dep = rs_deps & RUST_WEB_DEPS
        cli_dep = rs_deps & RUST_CLI_DEPS
        gui_dep = rs_deps & RUST_GUI_DEPS
        if web_dep:
            da["web_framework"] = next(iter(web_dep))
        if cli_dep:
            da["has_cli"] = True
        if gui_dep:
            da["has_desktop_gui"] = True
            da["gui_dep"] = True
        da["_rs_deps"] = rs_deps

    # ── 2. Source code scanning (skip if compose/Dockerfile handles deployment) ──
    if not analysis.has_compose and not analysis.has_dockerfile:
        if analysis.language in ("Python", "Container (Docker)"):
            src_info = _scan_python_source(project_dir)
        elif analysis.language == "Node.js":
            src_info = _scan_node_source(project_dir)
        elif analysis.language == "Go":
            src_info = _scan_go_source(project_dir)
        elif analysis.language == "Rust":
            src_info = _scan_rust_source(project_dir)
        else:
            src_info = {}

        for key in ("has_http_server", "has_cli", "has_desktop_gui"):
            if src_info.get(key):
                da[key] = True

    # ── 3. Library detection (skip when compose/Dockerfile present) ──
    if not analysis.has_compose and not analysis.has_dockerfile:
        da["is_library"] = _is_library_project(project_dir, analysis)

    # ── 4. Store in analysis ──
    analysis.deep_analysis = da
    analysis.web_framework = da.get("web_framework", "")
    analysis.has_http_server = da.get("has_http_server", False)
    analysis.has_cli = da.get("has_cli", False)
    analysis.is_library = da.get("is_library", False)
    analysis.has_desktop_gui = da.get("has_desktop_gui", False)

    return analysis


def _strip_compose_builds(compose_content: str) -> str:
    """Remove ``build:`` blocks from services that also have ``image:``.

    This lets us deploy using pre-built images without cloning the repo.
    """
    lines = compose_content.split("\n")
    result: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^(\s+)build\s*:", line)
        if m:
            indent = m.group(1)
            # Check if this service also has an `image:` key — look backwards first
            has_image = False
            for j in range(len(result) - 1, -1, -1):
                prev = result[j]
                if re.match(r"^" + indent + r"\S", prev):
                    if re.match(r"^" + indent + r"image\s*:", prev):
                        has_image = True
                    break
            # Also look forward (image: may appear after build:)
            if not has_image:
                for j in range(i + 1, len(lines)):
                    nxt = lines[j]
                    if re.match(r"^" + indent + r"\S", nxt):
                        if re.match(r"^" + indent + r"image\s*:", nxt):
                            has_image = True
                        break
                    if nxt.strip() == "" or nxt.startswith(indent + " "):
                        continue
                    break
            if has_image:
                build_indent = indent + "  "
                i += 1
                while i < len(lines) and (
                    lines[i].startswith(build_indent) or lines[i].strip() == ""
                ):
                    i += 1
                continue
        result.append(line)
        i += 1
    return "\n".join(result)


def _fetch_compose_content(result: RepoAnalysis) -> None:
    """Fetch docker-compose content via API and detect if it uses only images."""
    candidates = ["docker-compose.yml", "docker-compose.yaml"]
    if result.compose_path:
        candidates.insert(0, result.compose_path)
    for fname in candidates:
        try:
            r = requests.get(
                f"https://raw.githubusercontent.com/{result.owner}/{result.name}/main/{fname}",
                timeout=10,
            )
            if r.status_code == 200:
                raw_content = r.text
                # Strip `build:` blocks from services that also have `image:`
                result.compose_content = _strip_compose_builds(raw_content)
                # Check if any `build:` sections remain after stripping
                if not re.search(r"^\s+build\s*:", result.compose_content, re.MULTILINE):
                    result.compose_images_only = True
                return
        except requests.RequestException:
            continue


def _check_root_files_via_api(owner: str, name: str) -> set[str] | None:
    """Fetch root directory listing via GitHub Contents API (no clone needed)."""
    try:
        r = requests.get(
            f"https://api.github.com/repos/{owner}/{name}/contents/",
            timeout=10,
            headers={"Accept": "application/vnd.github.v3+json"},
        )
        if r.status_code != 200:
            return None
        return {item["name"] for item in r.json() if isinstance(item, dict)}
    except (requests.RequestException, ValueError, TypeError):
        return None


_SUBDIRS_TO_SCAN = ("scripts", ".docker", "deploy", "build", "docker")
_DOCKERFILE_NAMES = ("Dockerfile",)
_COMPOSE_NAMES = ("docker-compose.yml", "docker-compose.yaml", "compose.yaml", "compose.yml")


def _check_subdirs_via_api(owner: str, name: str) -> tuple[str | None, str | None]:
    """Probe common subdirectories for Dockerfile and compose files via GitHub API."""
    dockerfile_path: str | None = None
    compose_path: str | None = None

    for subdir in _SUBDIRS_TO_SCAN:
        if dockerfile_path and compose_path:
            break
        try:
            r = requests.get(
                f"https://api.github.com/repos/{owner}/{name}/contents/{subdir}",
                timeout=10,
                headers={"Accept": "application/vnd.github.v3+json"},
            )
            if r.status_code != 200:
                continue
            files = {item["name"] for item in r.json() if isinstance(item, dict)}
            if not dockerfile_path:
                for name_ in _DOCKERFILE_NAMES:
                    if name_ in files:
                        dockerfile_path = f"{subdir}/{name_}"
                        break
            if not compose_path:
                for name_ in _COMPOSE_NAMES:
                    if name_ in files:
                        compose_path = f"{subdir}/{name_}"
                        break
        except (requests.RequestException, ValueError, TypeError):
            continue

    return dockerfile_path, compose_path


def _scan_clone_subdirs(project_dir: Path) -> tuple[str | None, str | None]:
    """Scan cloned repo subdirectories for Dockerfile and compose files."""
    dockerfile_path: str | None = None
    compose_path: str | None = None

    for subdir in _SUBDIRS_TO_SCAN:
        if dockerfile_path and compose_path:
            break
        sub = project_dir / subdir
        if not sub.is_dir():
            continue
        if not dockerfile_path:
            for name_ in _DOCKERFILE_NAMES:
                if (sub / name_).is_file():
                    dockerfile_path = f"{subdir}/{name_}"
                    break
        if not compose_path:
            for name_ in _COMPOSE_NAMES:
                if (sub / name_).is_file():
                    compose_path = f"{subdir}/{name_}"
                    break

    return dockerfile_path, compose_path


def analyze_repo(url: str, work_dir: str | None = None) -> RepoAnalysis:
    result = RepoAnalysis(url=url)

    parsed = parse_github_url(url)
    if not parsed:
        result.errors.append("Invalid GitHub URL format")
        result.reason = "Invalid GitHub URL"
        return result

    result.owner, result.name = parsed

    # Single API call for repo metadata (existence check + description/topics)
    metadata = fetch_repo_metadata(result.owner, result.name)
    if metadata is None:
        result.errors.append("Repository does not exist or is private")
        result.reason = "Repository not found"
        return result
    result.exists = True

    # Quick API-based root file check (avoids slow clone for Docker/compose repos)
    root_files = _check_root_files_via_api(result.owner, result.name)
    if root_files is not None:
        result.has_dockerfile = "Dockerfile" in root_files
        result.has_compose = (
            "docker-compose.yml" in root_files or "docker-compose.yaml" in root_files
            or "compose.yaml" in root_files or "compose.yml" in root_files
        )
        result.has_package_json = "package.json" in root_files
        result.has_requirements = "requirements.txt" in root_files
        result.has_go_mod = "go.mod" in root_files
        result.has_cargo = "Cargo.toml" in root_files
        result.has_index = any(f in root_files for f in ("index.html", "index.htm", "index.php"))
        # When Dockerfile/compose are detected via API we can skip cloning entirely
        # for the analysis phase — clone happens later at deployment time.
        if result.has_compose or result.has_dockerfile:
            result.language = _detect_language(result)
            result.can_host = True
            result.host_score = 90
            result.host_recommendation = "Docker project (API scan)"
            cat, cat_reason, is_web = detect_app_category(result, metadata)
            result.app_category = cat
            result.category_reason = cat_reason
            result.web_app_verified = is_web

            # Fetch compose content to check if it uses only pre-built images (no build:)
            if result.has_compose:
                _fetch_compose_content(result)

            return result

        # No Dockerfile/compose in root — probe common subdirectories
        sub_dockerfile, sub_compose = _check_subdirs_via_api(result.owner, result.name)
        if sub_dockerfile or sub_compose:
            if sub_dockerfile:
                result.has_dockerfile = True
                result.dockerfile_path = sub_dockerfile
            if sub_compose:
                result.has_compose = True
                result.compose_path = sub_compose
            result.language = _detect_language(result)
            result.can_host = True
            result.host_score = 90
            result.host_recommendation = "Docker project (subdir scan)"
            cat, cat_reason, is_web = detect_app_category(result, metadata)
            result.app_category = cat
            result.category_reason = cat_reason
            result.web_app_verified = is_web

            if result.has_compose:
                _fetch_compose_content(result)

            return result

    # ── Fallback: clone for deeper analysis (non-Docker repos) ──
    if work_dir:
        base = os.path.abspath(os.path.expanduser(work_dir))
        os.makedirs(base, exist_ok=True)
        clone_dir = os.path.join(base, result.name)
        if os.path.isdir(clone_dir):
            shutil.rmtree(clone_dir, ignore_errors=True)
    else:
        clone_dir = tempfile.mkdtemp(prefix="ghost_")
    try:
        git_url = f"https://github.com/{result.owner}/{result.name}.git"
        proc = subprocess.run(
            ["git", "clone", "--depth", "1", "--single-branch", "--no-tags", git_url, clone_dir],
            capture_output=True, timeout=120,
        )
        if proc.returncode != 0:
            result.errors.append(f"git clone failed: {proc.stderr.decode(errors='replace')[:200]}")
            result.reason = "Cannot clone repository"
            shutil.rmtree(clone_dir, ignore_errors=True)
            return result

        result.clone_path = clone_dir
        items = os.listdir(clone_dir)

        result.has_dockerfile = "Dockerfile" in items
        result.has_compose = (
            "docker-compose.yml" in items or "docker-compose.yaml" in items
            or "compose.yaml" in items or "compose.yml" in items
        )
        result.has_package_json = "package.json" in items
        result.has_requirements = "requirements.txt" in items
        result.has_go_mod = "go.mod" in items
        result.has_cargo = "Cargo.toml" in items
        result.has_index = any(f in items for f in ("index.html", "index.htm", "index.php"))

        # If no root Dockerfile/compose, scan subdirectories
        if not result.has_dockerfile and not result.has_compose:
            sub_dockerfile, sub_compose = _scan_clone_subdirs(Path(clone_dir))
            if sub_dockerfile:
                result.has_dockerfile = True
                result.dockerfile_path = sub_dockerfile
            if sub_compose:
                result.has_compose = True
                result.compose_path = sub_compose

        result.language = _detect_language(result)

        # Deep analysis: dependency scanning + source code analysis
        result = _deep_analyze_project(result)

        # Compute hosting score and verdict
        result.can_host, result.reason = _can_host_verdict(result)

    except subprocess.TimeoutExpired:
        result.errors.append("git clone timed out")
        result.reason = "Clone timed out"
        shutil.rmtree(clone_dir, ignore_errors=True)
        result.clone_path = None
    except Exception as e:
        result.errors.append(str(e))
        result.reason = "Unexpected error during analysis"
        shutil.rmtree(clone_dir, ignore_errors=True)
        result.clone_path = None

    # Category detection from metadata (works with or without clone)
    if metadata:
        desc = (metadata.get("description") or "").lower()
        topics = set(metadata.get("topics", []) or [])
        not_web_kws = {"desktop", "electron", "cli", "command line", "terminal", "library", "sdk", "framework"}
        if any(kw in desc for kw in not_web_kws):
            if not result.deep_analysis:
                result.deep_analysis = {}
            result.deep_analysis["github_not_web"] = True
        web_kws = {"web", "website", "frontend", "dashboard", "api", "server", "backend"}
        if any(kw in desc for kw in web_kws):
            if not result.deep_analysis:
                result.deep_analysis = {}
            result.deep_analysis["gh_description_web"] = True
        media_topics = {"media-server", "music", "streaming", "jellyfin", "plex"}
        if topics & media_topics:
            if not result.deep_analysis:
                result.deep_analysis = {}
            result.deep_analysis["gh_topics_media"] = True
        search_topics = {"search-engine", "searx", "searxng", "whoogle", "yacy", "search"}
        if topics & search_topics:
            if not result.deep_analysis:
                result.deep_analysis = {}
            result.deep_analysis["gh_topics_search"] = True
    cat, cat_reason, is_web = detect_app_category(result, metadata)
    result.app_category = cat
    result.category_reason = cat_reason
    result.web_app_verified = is_web
    if result.clone_path:
        result.volume_hints = detect_volume_hints(result)

    return result


def ensure_cloned(analysis: RepoAnalysis, work_dir: str | None = None) -> None:
    """Clone the repo if not already cloned (deferred from quick analysis).

    When the compose file only uses pre-built images (no ``build:`` section)
    we skip cloning entirely and just write the compose file to disk.
    """
    if analysis.clone_path is not None:
        return
    if not analysis.exists or not analysis.owner or not analysis.name:
        return

    if work_dir:
        base = os.path.abspath(os.path.expanduser(work_dir))
        os.makedirs(base, exist_ok=True)
        clone_dir = os.path.join(base, analysis.name)
        if os.path.isdir(clone_dir):
            shutil.rmtree(clone_dir, ignore_errors=True)
    else:
        clone_dir = tempfile.mkdtemp(prefix="ghost_")

    # When compose uses only pre-built images, skip git clone entirely
    if analysis.compose_images_only and analysis.compose_content:
        os.makedirs(clone_dir, exist_ok=True)
        # Write the compose file into the directory — preserve subdir path if known
        if analysis.compose_path:
            target_dir = os.path.join(clone_dir, os.path.dirname(analysis.compose_path))
            os.makedirs(target_dir, exist_ok=True)
            compose_file_path = os.path.join(clone_dir, analysis.compose_path)
        else:
            compose_file_path = os.path.join(clone_dir, "docker-compose.yml")
        with open(compose_file_path, "w") as f:
            f.write(analysis.compose_content)
        analysis.clone_path = clone_dir
        analysis.has_compose = True
        analysis.has_dockerfile = False
        return

    git_url = f"https://github.com/{analysis.owner}/{analysis.name}.git"
    proc = subprocess.run(
        ["git", "clone", "--depth", "1", "--single-branch", "--no-tags", git_url, clone_dir],
        capture_output=True, timeout=120,
    )
    if proc.returncode == 0:
        analysis.clone_path = clone_dir
        items = os.listdir(clone_dir)
        analysis.has_dockerfile = "Dockerfile" in items
        analysis.has_compose = (
            "docker-compose.yml" in items or "docker-compose.yaml" in items
            or "compose.yaml" in items or "compose.yml" in items
        )
        analysis.has_package_json = "package.json" in items
        analysis.has_requirements = "requirements.txt" in items
        analysis.has_go_mod = "go.mod" in items
        analysis.has_cargo = "Cargo.toml" in items
        analysis.has_index = any(f in items for f in ("index.html", "index.htm", "index.php"))

        # If no root Dockerfile/compose, scan subdirectories
        if not analysis.has_dockerfile and not analysis.has_compose:
            project_dir = Path(clone_dir)
            sub_dockerfile, sub_compose = _scan_clone_subdirs(project_dir)
            if sub_dockerfile:
                analysis.has_dockerfile = True
                analysis.dockerfile_path = sub_dockerfile
            if sub_compose:
                analysis.has_compose = True
                analysis.compose_path = sub_compose
    else:
        raise RuntimeError(f"git clone failed: {proc.stderr.decode(errors='replace')[:200]}")


def preflight_check() -> list[str]:
    """Run pre-flight checks before deployment. Returns list of issues."""
    issues: list[str] = []

    # Docker daemon running
    try:
        r = subprocess.run(
            ["docker", "info"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0:
            issues.append("Docker daemon not running (try: sudo systemctl start docker)")
    except FileNotFoundError:
        issues.append("Docker not installed")
    except subprocess.TimeoutExpired:
        issues.append("Docker daemon not responding")

    # Network
    try:
        r = subprocess.run(
            ["ping", "-c", "1", "-W", "2", "8.8.8.8"],
            capture_output=True, timeout=5,
        )
        if r.returncode != 0:
            issues.append("No network connectivity")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        issues.append("No network connectivity")

    return issues


def _detect_language(analysis: RepoAnalysis) -> str:
    if analysis.has_compose or analysis.has_dockerfile:
        return "Container (Docker)"
    if analysis.has_requirements:
        return "Python"
    if analysis.has_package_json:
        return "Node.js"
    if analysis.has_go_mod:
        return "Go"
    if analysis.has_cargo:
        return "Rust"
    if analysis.has_index:
        return "Static HTML"
    return "Unknown"


def _compute_host_score(analysis: RepoAnalysis) -> tuple[int, str]:
    """Compute a confidence score (0-100) and recommendation for hosting.

    Positive signals (web app indicators):
      +80  Docker Compose (designed to be deployed)
      +60  Dockerfile (designed to be containerised)
      +50  Web framework detected in dependencies
      +40  HTTP server code found in source
      +30  Static site (index.html)
      +20  index.html present
      +15  has_package_json with web deps
      +10  Python with web deps
      +10  GitHub description/web keywords
      +10  media_server keywords
      +10  search_engine keywords
      +25  searx (special known service)

    Negative signals (non-web indicators):
      -40  CLI dependency without web dep
      -50  Library structure (no entry point)
      -50  Desktop/GUI dependency
      -40  CLI source code without HTTP server
      -30  GitHub desktop/CLI keywords
    """
    score = 0
    reasons: list[str] = []
    da = analysis.deep_analysis or {}

    # ── Strong positive signals ──
    if analysis.has_compose:
        score += 80
        reasons.append("docker-compose.yml (+80)")
    elif analysis.has_dockerfile:
        score += 60
        reasons.append("Dockerfile (+60)")

    # ── Dependency-level signals ──
    wf = da.get("web_framework", "")
    if wf:
        score += 50
        reasons.append(f"web framework: {wf} (+50)")

    # ── Source code signals ──
    if da.get("has_http_server"):
        score += 40
        reasons.append("HTTP server in source (+40)")

    # Skip HTML file scanning when compose/dockerfile is present for speed
    if analysis.has_compose or analysis.has_dockerfile:
        html_count = 0
    elif analysis.clone_path:
        html_count = len(list(Path(analysis.clone_path).rglob("*.html")))
    else:
        html_count = 0

    if analysis.has_index:
        score += 30
        reasons.append("static index.html (+30)")
    elif html_count > 0:
        score += min(15 + html_count, 30)
        reasons.append(f"HTML files ({html_count}) (+{min(15 + html_count, 30)})")

    # ── Language-specific dep signals (use cached data if available) ──
    if analysis.has_package_json:
        nd = da.get("_node_deps") if da.get("_node_deps") is not None else (
            _collect_node_deps(Path(analysis.clone_path)) if analysis.clone_path else None
        )
        if nd:
            web_in_node = set(nd.keys()) & NODE_WEB_DEPS
            if web_in_node:
                score += 15
                reasons.append(f"Node.js web deps: {', '.join(web_in_node)} (+15)")
    if analysis.has_requirements or (analysis.clone_path and (Path(analysis.clone_path) / "requirements.txt").exists()):
        pd = da.get("_py_deps") if da.get("_py_deps") is not None else (
            _collect_python_deps(Path(analysis.clone_path)) if analysis.clone_path else set()
        )
        web_in_py = pd & PYTHON_WEB_DEPS
        if web_in_py:
            score += 10
            reasons.append(f"Python web deps: {', '.join(web_in_py)} (+10)")

    # ── Known service signals ──
    if analysis.name and "searx" in analysis.name.lower():
        score += 25
        reasons.append("SearXNG search engine (+25)")
    if analysis.name and any(kw in analysis.name.lower() for kw in ("whoogle", "yacy", "librey")):
        score += 20
        reasons.append("search engine detected (+20)")

    # ── GitHub metadata ──
    if da.get("gh_description_web"):
        score += 10
        reasons.append("GitHub description suggests web app (+10)")
    if da.get("gh_topics_media"):
        score += 10
        reasons.append("GitHub topics suggest media server (+10)")
    if da.get("gh_topics_search"):
        score += 10
        reasons.append("GitHub topics suggest search engine (+10)")

    # ── Negative signals (only if no strong web presence) ──
    has_strong_web = bool(wf) or da.get("has_http_server") or analysis.has_compose

    if not has_strong_web:
        if da.get("has_desktop_gui") or (da.get("gui_dep")):
            score -= 50
            reasons.append("desktop/GUI detected (-50)")
        if da.get("is_library"):
            score -= 50
            reasons.append("project structure is a library (-50)")
        if da.get("has_cli") and not da.get("has_http_server"):
            score -= 40
            reasons.append("CLI interface without HTTP server (-40)")
        if da.get("github_not_web"):
            score -= 30
            reasons.append("GitHub metadata suggests non-web (-30)")

        # ── CLI deps without web deps ──
        if analysis.clone_path:
            if analysis.has_package_json:
                nd = da.get("_node_deps") if da.get("_node_deps") is not None else _collect_node_deps(Path(analysis.clone_path))
                if nd:
                    has_web = bool(set(nd.keys()) & NODE_WEB_DEPS)
                    has_cli = bool(set(nd.keys()) & NODE_CLI_DEPS)
                    if has_cli and not has_web:
                        score -= 40
                        reasons.append("Node CLI deps without web deps (-40)")
            pd = da.get("_py_deps") if da.get("_py_deps") is not None else (
                _collect_python_deps(Path(analysis.clone_path)) if analysis.clone_path else set()
            )
            has_py_cli = bool(pd & PYTHON_CLI_DEPS)
            has_py_web = bool(pd & PYTHON_WEB_DEPS)
            if has_py_cli and not has_py_web:
                score -= 40
                reasons.append("Python CLI deps without web deps (-40)")
            has_py_gui = bool(pd & PYTHON_GUI_DEPS)
            if has_py_gui:
                score -= 50
                reasons.append("Python GUI deps (-50)")

    # ── Score-based verdict ──
    if score >= 50:
        return score, f"high confidence ({score}/100): " + "; ".join(reasons[:3])
    elif score >= 20:
        return score, f"low confidence ({score}/100): " + "; ".join(reasons[:3])
    else:
        return score, f"unsuitable ({score}/100): " + "; ".join(reasons[:3] if reasons else ["no web indicators found"])


def _can_host_verdict(analysis: RepoAnalysis) -> tuple[bool, str]:
    score, rec = _compute_host_score(analysis)
    analysis.host_score = score
    analysis.host_recommendation = rec
    if analysis.has_compose or analysis.has_dockerfile:
        return True, rec
    if score >= 50:
        return True, rec
    if score >= 20:
        return True, f"LOW CONFIDENCE — {rec}"
    return False, rec


def find_free_port(start: int = 0, max_tries: int = 50) -> int:
    """Find the first available port starting from `start`.
    
    If start is 0, picks a random port in [8000, 32768) to reduce
    collisions with commonly-used ports like 3000 or 8080.
    """
    if start == 0:
        start = random.randint(8000, 30000)
    for port in range(start, start + max_tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError(f"No free port found in range {start}-{start + max_tries}")


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


_RE_NAMED_VOLUME = re.compile(r'^\s+-\s+(?P<name>[a-z_][a-z0-9_-]*):(?P<container>/\S+)', re.MULTILINE)

_AI_VOLUME_HOSTS: dict[str, str] = {
    "ollama": os.path.expanduser("~/.ollama"),
    "open-webui": os.path.expanduser("~/.local/share/open-webui"),
    "huggingface": os.path.expanduser("~/.cache/huggingface"),
}


def _resolve_ai_host_path(vol_name: str) -> str:
    """Return the best host path for a known AI named volume.

    For ``ollama``, checks several locations, preferring one with existing
    model blobs so already-pulled models are visible in the container.
    """
    if vol_name == "ollama":
        candidates = [
            os.path.expanduser("~/.ollama"),
            "/var/lib/ollama",
            "/usr/share/ollama",
        ]
        for p in candidates:
            if not os.path.isdir(p):
                continue
            # Check for actual model data (blobs directory with files)
            for sub in ("blobs", "models/blobs"):
                bp = os.path.join(p, sub)
                if os.path.isdir(bp):
                    try:
                        if os.listdir(bp):
                            return p
                    except OSError:
                        pass
        return candidates[0]
    return _AI_VOLUME_HOSTS.get(vol_name, os.path.expanduser(f"~/.{vol_name}"))


def _replace_ai_volumes_in_compose(project_dir: Path,
                                   target_path: Path | None = None,
                                   compose_subpath: str | None = None,
                                   ) -> Path | None:
    """Replace named AI volumes in docker-compose with host-mounted bind mounts.

    If *target_path* is given, read content from that file instead of the
    default compose file (used to chain modifications). Writes to a new
    ``.ghost-`` file so the original is never touched.
    """
    compose_file = None
    if compose_subpath and (project_dir / compose_subpath).is_file():
        compose_file = compose_subpath
    else:
        for f in ("docker-compose.yml", "docker-compose.yaml", "compose.yaml", "compose.yml"):
            if (project_dir / f).exists():
                compose_file = f
                break
    if not compose_file:
        return None

    try:
        content = (target_path or project_dir / compose_file).read_text()
    except OSError:
        return None

    to_replace: set[str] = set()
    for m in _RE_NAMED_VOLUME.finditer(content):
        vol_name = m.group("name")
        if vol_name in _AI_VOLUME_HOSTS:
            to_replace.add(vol_name)

    if not to_replace:
        return None

    for name in to_replace:
        Path(_resolve_ai_host_path(name)).mkdir(parents=True, exist_ok=True)

    def _replace_named(m: re.Match) -> str:
        name = m.group("name")
        if name in to_replace:
            container_path = m.group("container")
            host_path = _resolve_ai_host_path(name)
            # The container ollama expects models under /root/.ollama/models/
            # but native host ollama stores them at /var/lib/ollama/ directly.
            # Adjust the container mount target so the paths line up.
            if name == "ollama" and container_path in ("/root/.ollama", "/home/ollama/.ollama"):
                container_path = "/root/.ollama/models"
            return f"      - {host_path}:{container_path}"
        return m.group(0)

    new_content = _RE_NAMED_VOLUME.sub(_replace_named, content)

    # Remove the replaced named volumes from the top-level volumes: section
    lines = new_content.split("\n")
    in_volumes_top = False
    result: list[str] = []
    for line in lines:
        stripped = line.strip()
        if re.match(r"^volumes\s*:", line) and not stripped.startswith("-"):
            in_volumes_top = True
            remaining_named = [l for l in lines if re.match(r"^\s+[a-z_][a-z0-9_-]*\s*:", l)
                               and l.strip() not in to_replace
                               and not l.strip().startswith("-")]
            if not remaining_named:
                continue
            result.append(line)
            continue
        if in_volumes_top:
            if stripped == "" or stripped.startswith("#"):
                result.append(line)
                continue
            m = re.match(r"^\s+([a-z_][a-z0-9_-]*)\s*:", line)
            if m:
                name = m.group(1)
                if name in to_replace:
                    continue
                result.append(line)
                continue
            in_volumes_top = False
            result.append(line)
            continue
        result.append(line)
    new_content = "\n".join(result)

    # Remove empty volumes: block at the end
    new_content = re.sub(r'\n+volumes:\s*\n(?=\S|\Z)', '', new_content)

    modified = project_dir / f".ghost-{compose_file}"
    modified.parent.mkdir(parents=True, exist_ok=True)
    modified.write_text(new_content)
    return modified


def _remap_compose_ports(project_dir: Path, compose_subpath: str | None = None) -> Path | None:
    """Rewrite docker-compose ports and strip fixed container_name to avoid conflicts.
    
    Replaces busy host ports with free ones and removes hardcoded container_name
    so multiple deployments don't clash.
    
    Returns path to modified compose file, or None if no changes needed.
    """
    compose_file = None
    if compose_subpath and (project_dir / compose_subpath).is_file():
        compose_file = compose_subpath
    else:
        for f in ("docker-compose.yml", "docker-compose.yaml", "compose.yaml", "compose.yml"):
            if (project_dir / f).exists():
                compose_file = f
                break
    if not compose_file:
        return None

    try:
        content = (project_dir / compose_file).read_text()
    except OSError:
        return None

    # Strip container_name — Docker Compose with -p will auto-prefix names
    stripped = re.sub(r"^\s*container_name\s*:.*$", "", content, flags=re.MULTILINE)

    # Match port mappings. Four forms:
    #   1) literal: "3000:80"  or  3000:80
    #   2) env-var w/ default: "${PORT:-3000}:80"
    #   3) env-var w/o default: "${PORT}:80"
    #   4) range: "8000-8010:8000-8010"
    literal_pat = re.compile(
        r'(?P<q>["\']?)(?P<host>\d+):(?P<cont>\d+)(?P<tail>/[a-z]+)?(?P=q)'
    )
    range_pat = re.compile(
        r'(?P<q>["\']?)(?P<host_start>\d+)-(?P<host_end>\d+):(?P<cont_start>\d+)-(?P<cont_end>\d+)(?P<tail>/[a-z]+)?(?P=q)'
    )
    envvar_def_pat = re.compile(
        r'(?P<q>["\']?)\$\{(?P<var>[^}:]+)(?P<sep>:-|-)(?P<def>\d+)\}:(?P<cont>\d+)(?P<tail>/[a-z]+)?(?P=q)'
    )
    envvar_nodef_pat = re.compile(
        r'(?P<q>["\']?)\$\{(?P<var>[^}]+)\}:(?P<cont>\d+)(?P<tail>/[a-z]+)?(?P=q)'
    )

    def _replace_literal(m: re.Match) -> str:
        host_port = int(m.group("host"))
        if _port_free(host_port):
            return m.group(0)
        try:
            new_port = find_free_port(host_port + 1)
        except RuntimeError:
            return m.group(0)
        return f'{m.group("q")}{new_port}:{m.group("cont")}{m.group("tail") or ""}{m.group("q")}'

    def _replace_envvar_def(m: re.Match) -> str:
        host_port = int(m.group("def"))
        if _port_free(host_port):
            return m.group(0)
        try:
            new_port = find_free_port(host_port + 1)
        except RuntimeError:
            return m.group(0)
        return f'{m.group("q")}${{{m.group("var")}{m.group("sep")}{new_port}}}:{m.group("cont")}{m.group("tail") or ""}{m.group("q")}'

    def _replace_envvar_nodef(m: re.Match) -> str:
        return m.group(0)

    def _replace_range(m: re.Match) -> str:
        host_start = int(m.group("host_start"))
        if _port_free(host_start):
            return m.group(0)
        try:
            new_port = find_free_port(host_start + 1)
        except RuntimeError:
            return m.group(0)
        offset = new_port - host_start
        new_host_end = int(m.group("host_end")) + offset
        new_cont_start = int(m.group("cont_start")) + offset
        new_cont_end = int(m.group("cont_end")) + offset
        return f'{m.group("q")}{host_start + offset}-{new_host_end}:{new_cont_start}-{new_cont_end}{m.group("tail") or ""}{m.group("q")}'

    # Apply substitutions only within YAML `ports:` list items
    lines = stripped.split("\n")
    in_ports = False
    changed = False

    for i, line in enumerate(lines):
        stripped_line = line.strip()

        if re.match(r"^\s*ports\s*:", line):
            in_ports = True
            continue

        if in_ports:
            if stripped_line and not stripped_line.startswith("#") and not stripped_line.startswith("-"):
                in_ports = False
                continue

            if stripped_line.startswith("-"):
                new_line = range_pat.sub(_replace_range, line)
                new_line = envvar_def_pat.sub(_replace_envvar_def, new_line)
                new_line = envvar_nodef_pat.sub(_replace_envvar_nodef, new_line)
                new_line = literal_pat.sub(_replace_literal, new_line)
                if new_line != line:
                    lines[i] = new_line
                    changed = True

    new_content = "\n".join(lines)
    # Always write if container_name was stripped or ports changed
    if new_content != content or changed:
        modified = project_dir / f".ghost-{compose_file}"
        modified.parent.mkdir(parents=True, exist_ok=True)
        modified.write_text(new_content)
        return modified
    return None


def _discover_container_urls(cid: str) -> list[str]:
    """Discover HTTP URLs for a container via docker port + inspect fallback."""
    urls: list[str] = []

    # Try docker port first
    try:
        r = subprocess.run(
            ["docker", "port", cid],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            for line in r.stdout.strip().split("\n"):
                line = line.strip()
                if not line or " -> " not in line:
                    continue
                host_part = line.split(" -> ")[1]
                host_port = _parse_host_port(host_part)
                if host_port:
                    urls.append(f"http://localhost:{host_port}")
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    if urls:
        return urls

    # Fallback: parse port bindings from docker inspect
    try:
        r = subprocess.run(
            ["docker", "inspect", "--format", "{{json .NetworkSettings.Ports}}", cid],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            import json as _json
            ports = _json.loads(r.stdout.strip())
            for container_port, bindings in (ports or {}).items():
                if not bindings:
                    continue
                for b in bindings:
                    host_ip = b.get("HostIp", "0.0.0.0")
                    host_port = b.get("HostPort", "")
                    if host_port and host_port.isdigit():
                        if host_ip in ("0.0.0.0", "::", ""):
                            host_ip = "127.0.0.1"
                        urls.append(f"http://{host_ip}:{host_port}")
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, _json.JSONDecodeError):
        pass

    return urls


def verify_deployment(result: HostResult, timeout: int = 300,
                      on_status: Callable[[str], None] | None = None) -> HostResult:
    done_callback = on_status or (lambda _: None)

    if not result.container_ids:
        result.errors.append("No containers to verify")
        return result

    # Wait for containers to be running
    deadline = time.time() + timeout
    for cid in result.container_ids:
        while time.time() < deadline:
            try:
                r = subprocess.run(
                    ["docker", "inspect", "--format", "{{.State.Status}}", cid],
                    capture_output=True, text=True, timeout=5,
                )
                status = r.stdout.strip()
                if status == "running":
                    done_callback(f"container {cid[:12]} is running")
                    break
                if status in ("exited", "dead"):
                    logs = container_logs(cid, 20)
                    result.errors.append(f"Container {cid[:12]} exited: {logs[:200]}")
                    break
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass
            time.sleep(2)
        else:
            result.errors.append(f"Container {cid[:12]} not running after {timeout}s")

    # If no URLs discovered yet, try to find them from containers
    if not result.urls:
        for cid in result.container_ids:
            result.urls.extend(_discover_container_urls(cid))

    if not result.urls:
        result.errors.append("No exposed ports found — container may not be a web service")
        for cid in result.container_ids:
            logs = container_logs(cid)
            if logs:
                result.errors.append(f"Container {cid[:12]} logs:\n{logs[:300]}")
                break
        return result

    # Verify URLs with adaptive retries (exponential backoff, up to timeout)
    for url in result.urls:
        ok = False
        detail = ""
        retries = 0
        while time.time() < deadline and retries < 30:
            done_callback(f"checking {url} (attempt {retries + 1})...")
            ok, detail = verify_url(url, timeout=10)
            if ok:
                result.healthy = True
                done_callback(f"{url} is responding")
                break
            retries += 1
            sleep_time = min(5 * retries, 30)
            time.sleep(sleep_time)
        if not ok:
            result.healthy = False
            done_callback(f"health check failed for {url}")
            for cid in result.container_ids:
                logs = container_logs(cid)
                if logs:
                    result.errors.append(f"Container {cid[:12]} logs:\n{logs[:500]}")
                    break
            result.errors.append(f"Health check failed for {url}: {detail}")
            break

    return result


def _build_volume_args(volume_mounts: list[tuple[str, str]] | None) -> list[str]:
    if not volume_mounts:
        return []
    args = []
    for host_path, container_path in volume_mounts:
        host_path = os.path.abspath(os.path.expanduser(host_path))
        args.extend(["-v", f"{host_path}:{container_path}"])
    return args


def _strategy_priority(analysis: RepoAnalysis) -> list[str]:
    """Return strategy names ordered by priority for the given analysis."""
    entries = [
        ("docker-compose", analysis.has_compose),
        ("Dockerfile", analysis.has_dockerfile),
        ("Python", analysis.has_requirements),
        ("Node.js", analysis.has_package_json),
        ("Go", analysis.has_go_mod),
        ("Rust", analysis.has_cargo),
        ("Static", analysis.has_index),
    ]

    lang_map: dict[str, str] = {
        "Container (Docker)": "docker-compose",
        "Python": "Python",
        "Node.js": "Node.js",
        "Go": "Go",
        "Rust": "Rust",
        "Static HTML": "Static",
    }

    primary = lang_map.get(_detect_language(analysis))
    priority_order = [e[0] for e in entries]

    def sort_key(e):
        name, _ = e
        if name in ("docker-compose", "Dockerfile"):
            return 0
        if primary and name == primary:
            return 1
        if name == "Static":
            return 99
        idx = priority_order.index(name)
        return idx + 2

    ordered = sorted(entries, key=sort_key)
    return [name for name, active in ordered if active]


def host_project(analysis: RepoAnalysis, port: int = 0,
                 volume_mounts: list[tuple[str, str]] | None = None,
                 verify: bool = True, work_dir: str | None = None,
                 on_status: callable = None) -> HostResult:
    """Run the project and return container IDs and URLs.

    If *on_status* is given, it is called with each line of Docker output
    so a TUI can show real-time progress.
    """
    # Clone first if deferred from quick analysis
    if analysis.clone_path is None:
        ensure_cloned(analysis, work_dir=work_dir)
    if not analysis.clone_path:
        return HostResult()

    if port == 0:
        port = find_free_port()

    project_dir = Path(analysis.clone_path)
    mounts = volume_mounts or []
    repo_url = analysis.url

    has_compose = "docker-compose" in _strategy_priority(analysis)

    # Patch compose file: remap ports → inject AI volume host-mounts
    prepared = None
    if has_compose:
        modified = _remap_compose_ports(project_dir, analysis.compose_path)
        ai_modified = _replace_ai_volumes_in_compose(project_dir, modified, analysis.compose_path)
        prepared = ai_modified or modified

    fn_map = {
        "docker-compose": lambda: _run_compose(
            project_dir, prepared, analysis.name, mounts, on_status=on_status,
            compose_subpath=analysis.compose_path,
        ),
        "Dockerfile": lambda: _build_and_run_docker(project_dir, port, repo_url, mounts,
                                                     dockerfile_path=analysis.dockerfile_path),
        "Python": lambda: _host_python(project_dir, port, repo_url, mounts),
        "Node.js": lambda: _host_node(project_dir, port, repo_url, mounts),
        "Go": lambda: _host_go(project_dir, port, repo_url, mounts),
        "Rust": lambda: _host_rust(project_dir, port, repo_url, mounts),
        "Static": lambda: _host_static(project_dir, port, repo_url, mounts),
    }

    strategies = [(name, fn_map[name]) for name in _strategy_priority(analysis)]

    if not strategies:
        raise RuntimeError("No hosting strategy available for this project")

    errors: list[str] = []
    for name, fn in strategies:
        strategy_result = HostResult()
        should_cleanup = False
        try:
            if name == "docker-compose":
                strategy_result = fn()
            else:
                cid = fn()
                strategy_result.container_ids = [cid]
                strategy_result.urls = [f"http://localhost:{port}"]
            if verify:
                strategy_result = verify_deployment(strategy_result)
            if strategy_result.healthy or (strategy_result.urls and strategy_result.container_ids):
                from ghostprovider.services import _container_name_from_id
                for cid in strategy_result.container_ids:
                    name = _container_name_from_id(cid) or cid
                    _register_state(name, str(project_dir), repo_url)
                return strategy_result
            should_cleanup = True
            msg = strategy_result.errors[0] if strategy_result.errors else "unknown error"
            errors.append(f"[{name}] {msg}")
        except Exception as e:
            should_cleanup = True
            errors.append(f"[{name}] {e}")
        finally:
            if should_cleanup:
                _cleanup_strategy(strategy_result)

    raise RuntimeError("All strategies failed:\n" + "\n".join(errors))


def _docker_compose_cmd() -> list[str]:
    """Return the available docker compose command."""
    for candidate in (["docker", "compose"], ["docker-compose"]):
        try:
            subprocess.run(
                candidate + ["version"],
                capture_output=True, timeout=5,
            )
            return candidate
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    raise RuntimeError("Neither `docker compose` nor `docker-compose` found")


def _build_compose_env(project_dir: Path,
                       volume_mounts: list[tuple[str, str]] | None) -> dict[str, str]:
    """Build environment variable overrides for docker-compose from volume_mounts.
    
    Maps user-provided host paths to compose env vars like ``MUSIC_DIR``.
    """
    env: dict[str, str] = {}
    if not volume_mounts:
        return env

    compose_file = None
    for f in ("docker-compose.yml", "docker-compose.yaml", "compose.yaml", "compose.yml"):
        if (project_dir / f).exists():
            compose_file = f
            break
    if not compose_file:
        return env

    try:
        content = (project_dir / compose_file).read_text()
    except OSError:
        return env

    # Build a map: container_path -> var_name from ${VAR:-default} patterns
    var_map: dict[str, str] = {}
    for m in re.finditer(r'\$\{([^}:]+):-([^}]*)\}:(/[^"\s,\]]+)', content):
        var_name = m.group(1)
        container_path = m.group(3)
        var_map[container_path] = var_name

    for host_path, container_path in volume_mounts:
        var_name = var_map.get(container_path)
        if var_name:
            env[var_name] = os.path.abspath(os.path.expanduser(host_path))

    return env


def _stream_subprocess(cmd: list[str], env: dict | None,
                       on_status: callable | None) -> int:
    """Run a subprocess and stream stdout/stderr line by line to *on_status*."""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, env=env,
    )
    if on_status:
        for line in iter(proc.stdout.readline, ""):
            on_status(line.rstrip())
            if proc.poll() is not None:
                break
        # Read any remaining lines
        for line in proc.stdout:
            on_status(line.rstrip())
    proc.wait()
    return proc.returncode


def _run_compose(project_dir: Path, modified: Path | None, repo_name: str,
                 volume_mounts: list[tuple[str, str]] | None = None,
                 on_status: callable | None = None,
                 compose_subpath: str | None = None) -> HostResult:
    compose_file = None
    if compose_subpath and (project_dir / compose_subpath).is_file():
        compose_file = compose_subpath
    else:
        for f in ("docker-compose.yml", "docker-compose.yaml", "compose.yaml", "compose.yml"):
            if (project_dir / f).exists():
                compose_file = f
                break
    if not compose_file:
        return HostResult()

    compose_path = modified if modified and modified.exists() else project_dir / compose_file
    cmd = _docker_compose_cmd()
    safe_name = re.sub(r"[^a-z0-9]", "", (repo_name or "repo").lower()) or "repo"
    project_name = f"ghost-{safe_name[:16]}-{random.randint(1000, 9999)}"

    compose_env = _build_compose_env(project_dir, volume_mounts)
    merged_env = {**os.environ, **compose_env} if compose_env else None

    # Step 1: Pull images (streams progress to the UI in real-time)
    pull_args = cmd + [
        "-f", str(compose_path),
        "-p", project_name,
        "pull",
    ]
    rc = _stream_subprocess(pull_args, merged_env, on_status)
    if rc != 0:
        if on_status:
            on_status("pull failed — continuing anyway")

    # Step 2: Up containers (streams output to the UI in real-time)
    up_args = cmd + [
        "-f", str(compose_path),
        "-p", project_name,
        "up", "-d",
    ]
    rc = _stream_subprocess(up_args, merged_env, on_status)
    if rc != 0:
        raise RuntimeError("docker compose up failed")

    # Discover container IDs
    ps_result = subprocess.run(
        cmd + ["-f", str(compose_path), "-p", project_name, "ps", "-q"],
        capture_output=True, text=True, timeout=10,
        env=merged_env,
    )
    cids = [cid.strip() for cid in ps_result.stdout.strip().split("\n") if cid.strip()]

    # Discover port mappings from running containers
    urls: list[str] = []
    for cid in cids:
        urls.extend(_discover_container_urls(cid))

    if modified and modified.exists():
        modified.unlink()

    return HostResult(container_ids=cids, urls=urls, compose_project=project_name)


def _build_and_run_docker(project_dir: Path, port: int, repo_url: str = "",
                           volume_mounts: list[tuple[str, str]] | None = None,
                           dockerfile_path: str | None = None) -> str:
    import uuid
    tag = f"ghost-{uuid.uuid4().hex[:8]}"

    build_args = ["docker", "build", "-t", tag]
    if dockerfile_path and (project_dir / dockerfile_path).is_file():
        build_args += ["-f", str(project_dir / dockerfile_path)]
        build_args.append(str(project_dir))
    else:
        build_args.append(str(project_dir))
    subprocess.run(
        build_args,
        capture_output=True, text=True,
        check=True, timeout=600,
    )

    # Detect internal port from Dockerfile — check the specific path first
    internal_port = None
    if dockerfile_path and (project_dir / dockerfile_path).is_file():
        internal_port = _detect_dockerfile_port_at(project_dir / dockerfile_path)
    if not internal_port:
        internal_port = _detect_dockerfile_port(project_dir) or 80
    run_args = ["docker", "run", "-d", "-p", f"{port}:{internal_port}"]
    run_args.extend(_build_volume_args(volume_mounts))
    if repo_url:
        run_args += ["--label", f"ghostprovider.repo={repo_url}"]
        run_args += ["--label", f"ghostprovider.clone_path={project_dir}"]
    run_args.append(tag)
    result = subprocess.run(run_args, capture_output=True, text=True, check=True, timeout=30)
    return result.stdout.strip()[:12]


def _detect_dockerfile_port(project_dir: Path) -> int | None:
    """Scan Dockerfile for EXPOSE directive and return the first port found."""
    dockerfile = project_dir / "Dockerfile"
    if not dockerfile.exists():
        return None
    return _detect_dockerfile_port_at(dockerfile)


def _detect_dockerfile_port_at(dockerfile: Path) -> int | None:
    """Scan a specific Dockerfile for EXPOSE directive."""
    if not dockerfile.is_file():
        return None
    try:
        content = dockerfile.read_text()
        for line in content.splitlines():
            m = re.search(r"EXPOSE\s+(\d+)", line)
            if m:
                return int(m.group(1))
    except OSError:
        pass
    return None


def _detect_wsgi_module(project_dir: Path) -> str | None:
    """Try to detect the WSGI/ASGI module from common project structures."""
    manage_py = project_dir / "manage.py"
    if manage_py.exists():
        try:
            for line in manage_py.read_text().splitlines():
                m = re.search(
                    r"setdefault\(\s*['\"]DJANGO_SETTINGS_MODULE['\"]\s*,\s*['\"](.+?)['\"]\s*\)",
                    line,
                )
                if m:
                    settings = m.group(1)
                    return settings.rsplit(".", 1)[0] + ".wsgi:application"
        except OSError:
            pass
        return None

    for candidate in ("app.py", "main.py"):
        f = project_dir / candidate
        if f.exists():
            try:
                content = f.read_text()
                if "FastAPI" in content:
                    return f"{candidate[:-3]}:app"
                if "Starlette" in content:
                    return f"{candidate[:-3]}:app"
            except OSError:
                pass

    for candidate in ("app.py", "main.py"):
        f = project_dir / candidate
        if f.exists():
            try:
                content = f.read_text()
                if "Flask" in content:
                    return f"{candidate[:-3]}:app"
            except OSError:
                pass

    return None


def _detect_python_entry(project_dir: Path) -> str | None:
    for entry in ("run.py", "server.py", "webapp.py", "wsgi.py", "asgi.py", "application.py"):
        f = project_dir / entry
        if f.exists():
            return entry[:-3]

    for pyfile in project_dir.iterdir():
        if pyfile.suffix == ".py" and pyfile.stem not in ("setup", "conf", "test", "tests", "conftest", "__init__"):
            try:
                content = pyfile.read_text()
                if any(x in content for x in ("app.run", "uvicorn.run", "gunicorn", "web.run", "make_server", "application.run")):
                    return pyfile.stem
            except OSError:
                pass

    for subdir in project_dir.iterdir():
        if subdir.is_dir() and (subdir / "__init__.py").exists() and subdir.name != "__pycache__":
            for entry in ("webapp", "server", "wsgi", "asgi", "app", "application"):
                candidate = subdir / f"{entry}.py"
                if candidate.exists():
                    return f"{subdir.name}.{entry}"

    return None


def _detect_python_port(project_dir: Path) -> int:
    for yml in ("settings.yml", "settings.yaml", "config.yml", "config.yaml"):
        f = project_dir / yml
        if f.exists():
            try:
                for line in f.read_text().splitlines():
                    m = re.search(r"port\s*[:=]\s*(\d+)", line, re.IGNORECASE)
                    if m:
                        p = int(m.group(1))
                        if 1024 < p < 65536:
                            return p
            except OSError:
                pass
    for pyfile_name in ("settings.py", "config.py", "app.py", "main.py", "webapp.py"):
        pyfile = project_dir / pyfile_name
        if pyfile.exists():
            try:
                for line in pyfile.read_text().splitlines():
                    m = re.search(r"(?:port|PORT)\s*[=:]\s*(\d+)", line)
                    if m:
                        p = int(m.group(1))
                        if 1024 < p < 65536:
                            return p
            except OSError:
                pass
    for subdir in ("src", project_dir.name):
        for pyfile_name in ("settings.py", "config.py", "app.py", "main.py", "webapp.py"):
            pyfile = project_dir / subdir / pyfile_name
            if pyfile.exists():
                try:
                    for line in pyfile.read_text().splitlines():
                        m = re.search(r"(?:port|PORT)\s*[=:]\s*(\d+)", line)
                        if m:
                            p = int(m.group(1))
                            if 1024 < p < 65536:
                                return p
                except OSError:
                    pass
    return 8000


def _host_python(project_dir: Path, port: int, repo_url: str = "",
                 volume_mounts: list[tuple[str, str]] | None = None) -> str:
    import uuid
    tag = f"ghost-py-{uuid.uuid4().hex[:8]}"
    has_manage = (project_dir / "manage.py").exists()
    has_app = (project_dir / "app.py").exists() or (project_dir / "main.py").exists()
    wsgi_module = _detect_wsgi_module(project_dir)
    py_entry = _detect_python_entry(project_dir)
    container_port = 8000
    env_vars: list[str] = []

    if has_manage and wsgi_module:
        cmd = f"gunicorn --bind 0.0.0.0:8000 {wsgi_module} || uvicorn --host 0.0.0.0 --port 8000 {wsgi_module}"
    elif has_manage:
        cmd = "python3 manage.py runserver 0.0.0.0:8000"
    elif wsgi_module:
        cmd = f"gunicorn --bind 0.0.0.0:8000 {wsgi_module} || uvicorn --host 0.0.0.0 --port 8000 {wsgi_module}"
    elif has_app:
        if (project_dir / "main.py").exists() and not (project_dir / "app.py").exists():
            cmd = "python3 main.py"
        else:
            cmd = "python3 app.py"
    elif py_entry:
        container_port = _detect_python_port(project_dir)
        if "." in py_entry:
            cmd = f"python3 -m {py_entry}"
        else:
            cmd = f"python3 {py_entry}.py"
        import secrets
        secret_key = secrets.token_hex(32)
        env_vars = [
            "-e", f"PORT={container_port}",
            "-e", f"SEARXNG_PORT={container_port}",
            "-e", "SEARXNG_BIND_ADDRESS=0.0.0.0",
            "-e", f"SEARXNG_SECRET={secret_key}",
        ]
    else:
        container_port = port
        cmd = f"python3 -m http.server {container_port}"

    if (project_dir / "searx" / "version.py").exists() and not (project_dir / "searx" / "version_frozen.py").exists():
        (project_dir / "searx" / "version_frozen.py").write_text(
            "VERSION_STRING = '0.0.0'\nVERSION_TAG = '0.0.0'\n"
            "DOCKER_TAG = 'latest'\nGIT_URL = ''\nGIT_BRANCH = 'main'\n"
        )
    if (project_dir / "setup.py").exists() or (project_dir / "pyproject.toml").exists():
        pkg_install = "pip install --no-cache-dir -e . --no-build-isolation 2>/dev/null || true"
    else:
        pkg_install = "true"

    dockerfile = f"""FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -r requirements.txt 2>/dev/null; {pkg_install}
EXPOSE {container_port}
CMD ["sh", "-c", "{cmd}"]
"""
    (project_dir / "Dockerfile.ghost").write_text(dockerfile)
    subprocess.run(
        ["docker", "build", "-t", tag, "-f", str(project_dir / "Dockerfile.ghost"), str(project_dir)],
        capture_output=True, text=True,
        check=True, timeout=600,
    )
    run_args = ["docker", "run", "-d", "-p", f"{port}:{container_port}"]
    run_args.extend(env_vars)
    run_args.extend(_build_volume_args(volume_mounts))
    if repo_url:
        run_args += ["--label", f"ghostprovider.repo={repo_url}"]
        run_args += ["--label", f"ghostprovider.clone_path={project_dir}"]
    run_args.append(tag)
    result = subprocess.run(run_args, capture_output=True, text=True, check=True, timeout=30)
    return result.stdout.strip()[:12]


def _host_node(project_dir: Path, port: int, repo_url: str = "",
               volume_mounts: list[tuple[str, str]] | None = None) -> str:
    """Host a Node.js project with smart package-manager and Electron detection."""
    import uuid
    tag = f"ghost-js-{uuid.uuid4().hex[:8]}"

    pkg = _read_package_json(project_dir)

    # Detect package manager
    has_bun_lock = (project_dir / "bun.lock").exists() or (project_dir / "bun.lockb").exists()
    has_pnpm_lock = (project_dir / "pnpm-lock.yaml").exists()

    if has_bun_lock:
        run_cmd = "bun"
        install_cmd = "bun install 2>/dev/null || true"
        serve_cmd = "bunx serve"
    elif has_pnpm_lock:
        # pnpm v11 supply-chain check fails headless on ignored builds.
        # --ignore-scripts skips running build scripts (no native deps needed).
        run_cmd = "pnpm"
        install_cmd = "npm install -g pnpm 2>/dev/null; pnpm install --ignore-scripts --no-frozen-lockfile 2>/dev/null || true"
        serve_cmd = "pnpm dlx serve"
    else:
        run_cmd = "npm"
        install_cmd = "npm install 2>/dev/null || true"
        serve_cmd = "npx serve"

    # Detect Electron app
    all_deps = {}
    if pkg:
        if "dependencies" in pkg:
            all_deps.update(pkg["dependencies"])
        if "devDependencies" in pkg:
            all_deps.update(pkg["devDependencies"])
    is_electron = "electron" in all_deps

    has_build_script = bool(pkg and "scripts" in pkg and "build" in pkg["scripts"])
    has_start_script = bool(pkg and "scripts" in pkg and "start" in pkg["scripts"])
    has_dev_script = bool(pkg and "scripts" in pkg and "dev" in pkg["scripts"])

    # Determine build and serve commands
    # container_port = what the app listens on inside the container
    if is_electron and has_build_script:
        build_layer = f"{run_cmd} run build 2>/dev/null || true"
        serve_cmd_full = f"{serve_cmd} -s dist/renderer -l {port} 2>/dev/null || {serve_cmd} -s . -l {port}"
        container_port = port
    elif has_build_script and has_start_script:
        build_layer = f"{run_cmd} run build 2>/dev/null || true"
        serve_cmd_full = f"{run_cmd} run start"
        container_port = _detect_node_port(pkg)
    elif has_build_script:
        build_layer = f"{run_cmd} run build 2>/dev/null || true"
        serve_cmd_full = f"{serve_cmd} -s build -l {port}"
        container_port = port
    elif has_dev_script:
        build_layer = ""
        serve_cmd_full = f"{run_cmd} run dev -- --host 0.0.0.0 -p {port}"
        container_port = port
    else:
        build_layer = ""
        serve_cmd_full = f"{serve_cmd} -s . -l {port}"
        container_port = port

    # For electron: pin node version to avoid native module issues
    base_image = "node:20-slim" if not is_electron else "node:22-slim"

    build_run = f"\nRUN {build_layer}" if build_layer else ""

    dockerfile = f"""FROM {base_image}
WORKDIR /app
COPY package.json ./
RUN {install_cmd}
COPY . .
RUN {install_cmd}{build_run}
EXPOSE {container_port}
CMD ["sh", "-c", "{serve_cmd_full}"]
"""
    (project_dir / "Dockerfile.ghost").write_text(dockerfile)
    subprocess.run(
        ["docker", "build", "-t", tag, "-f", str(project_dir / "Dockerfile.ghost"), str(project_dir)],
        capture_output=True, text=True,
        check=True, timeout=600,
    )
    run_args = ["docker", "run", "-d", "-p", f"{port}:{container_port}"]
    run_args.extend(_build_volume_args(volume_mounts))
    if repo_url:
        run_args += ["--label", f"ghostprovider.repo={repo_url}"]
        run_args += ["--label", f"ghostprovider.clone_path={project_dir}"]
    run_args.append(tag)
    result = subprocess.run(run_args, capture_output=True, text=True, check=True, timeout=30)
    return result.stdout.strip()[:12]


def _host_static(project_dir: Path, port: int, repo_url: str = "",
                 volume_mounts: list[tuple[str, str]] | None = None) -> str:
    """Host a static site with nginx."""
    import uuid
    tag = f"ghost-static-{uuid.uuid4().hex[:8]}"

    nginx_conf = """server {
    listen 80;
    root /usr/share/nginx/html;
    index index.html index.htm;
    location / {
        try_files $uri $uri/ =404;
    }
}
"""
    (project_dir / "nginx.ghost.conf").write_text(nginx_conf)

    dockerfile = """FROM nginx:stable-alpine
COPY nginx.ghost.conf /etc/nginx/conf.d/default.conf
COPY . /usr/share/nginx/html
EXPOSE 80
"""
    (project_dir / "Dockerfile.ghost").write_text(dockerfile)
    subprocess.run(
        ["docker", "build", "-t", tag, "-f", str(project_dir / "Dockerfile.ghost"), str(project_dir)],
        capture_output=True, text=True,
        check=True, timeout=600,
    )
    run_args = ["docker", "run", "-d", "-p", f"{port}:80"]
    run_args.extend(_build_volume_args(volume_mounts))
    if repo_url:
        run_args += ["--label", f"ghostprovider.repo={repo_url}"]
    run_args.append(tag)
    result = subprocess.run(run_args, capture_output=True, text=True, check=True, timeout=30)
    return result.stdout.strip()[:12]


def _host_go(project_dir: Path, port: int, repo_url: str = "",
             volume_mounts: list[tuple[str, str]] | None = None) -> str:
    """Host a Go project."""
    import uuid
    tag = f"ghost-go-{uuid.uuid4().hex[:8]}"
    internal_port = 8080

    main_go = project_dir / "main.go"
    if main_go.exists():
        cmd = "go build -o /server . && /server"
    else:
        cmd = "go run ."

    dockerfile = f"""FROM golang:1.23-alpine AS builder
WORKDIR /build
COPY go.mod go.sum* ./
RUN go mod download 2>/dev/null || true
COPY . .
RUN go build -o /server . 2>/dev/null || true

FROM alpine:latest
WORKDIR /app
COPY --from=builder /server /server 2>/dev/null || true
COPY . .
EXPOSE {internal_port}
CMD ["sh", "-c", "{cmd}"]
"""
    (project_dir / "Dockerfile.ghost").write_text(dockerfile)
    subprocess.run(
        ["docker", "build", "-t", tag, "-f", str(project_dir / "Dockerfile.ghost"), str(project_dir)],
        capture_output=True, text=True,
        check=True, timeout=600,
    )
    run_args = ["docker", "run", "-d", "-p", f"{port}:{internal_port}"]
    run_args.extend(_build_volume_args(volume_mounts))
    if repo_url:
        run_args += ["--label", f"ghostprovider.repo={repo_url}"]
        run_args += ["--label", f"ghostprovider.clone_path={project_dir}"]
    run_args.append(tag)
    result = subprocess.run(run_args, capture_output=True, text=True, check=True, timeout=30)
    return result.stdout.strip()[:12]


def _detect_rust_binary(project_dir: Path) -> str | None:
    """Extract binary/package name from Cargo.toml."""
    cargo_toml = project_dir / "Cargo.toml"
    if not cargo_toml.exists():
        return None
    try:
        content = cargo_toml.read_text()
        m = re.search(r'\[\[bin\]\][^[]*name\s*=\s*"(.+?)"', content, re.DOTALL)
        if m:
            return m.group(1)
        m = re.search(r'\[package\][^[]*name\s*=\s*"(.+?)"', content, re.DOTALL)
        if m:
            return m.group(1)
    except OSError:
        pass
    return None


def _host_rust(project_dir: Path, port: int, repo_url: str = "",
               volume_mounts: list[tuple[str, str]] | None = None) -> str:
    """Host a Rust project."""
    import uuid
    tag = f"ghost-rust-{uuid.uuid4().hex[:8]}"

    bin_name = _detect_rust_binary(project_dir) or "app"

    RELEASE_DIR = "/build/target/release"

    dockerfile = f"""FROM rust:1.85-slim AS builder
WORKDIR /build
COPY Cargo.toml Cargo.lock* ./
RUN mkdir src && echo "fn main() {{}}" > src/main.rs
RUN cargo build --release 2>/dev/null || true
COPY src ./src
RUN cargo build --release --bin {bin_name} 2>/dev/null || \\
    cargo build --release 2>/dev/null || true

FROM debian:bookworm-slim
WORKDIR /app
COPY --from=builder {RELEASE_DIR}/{bin_name} /app/server 2>/dev/null || true
EXPOSE 8080
CMD ["/app/server"]
"""
    (project_dir / "Dockerfile.ghost").write_text(dockerfile)
    subprocess.run(
        ["docker", "build", "-t", tag, "-f", str(project_dir / "Dockerfile.ghost"), str(project_dir)],
        capture_output=True, text=True,
        check=True, timeout=600,
    )
    run_args = ["docker", "run", "-d", "-p", f"{port}:8080"]
    run_args.extend(_build_volume_args(volume_mounts))
    if repo_url:
        run_args += ["--label", f"ghostprovider.repo={repo_url}"]
        run_args += ["--label", f"ghostprovider.clone_path={project_dir}"]
    run_args.append(tag)
    result = subprocess.run(run_args, capture_output=True, text=True, check=True, timeout=30)
    return result.stdout.strip()[:12]


def _detect_node_port(pkg: dict | None) -> int:
    """Try to detect the port a Node.js app listens on from package.json scripts."""
    if not pkg:
        return 3000
    scripts = pkg.get("scripts", {})
    for script_name in ("start", "dev", "serve"):
        script = scripts.get(script_name, "")
        if not script:
            continue
        m = re.search(r'(?:-p|--port)(?:\s+|=|:)\s*(\d+)', script)
        if m:
            return int(m.group(1))
        m = re.search(r'(?:PORT)=(\d+)', script)
        if m:
            return int(m.group(1))
    return 3000


def _read_package_json(project_dir: Path) -> dict | None:
    pkg_file = project_dir / "package.json"
    if not pkg_file.exists():
        return None
    try:
        return json.loads(pkg_file.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def container_logs(container_id: str, tail: int = 50) -> str:
    """Get last N lines of container logs."""
    try:
        r = subprocess.run(
            ["docker", "logs", "--tail", str(tail), container_id],
            capture_output=True, text=True, timeout=5,
        )
        out = r.stdout.strip()
        err = r.stderr.strip()
        result = ""
        if out:
            result += out
        if err:
            if result:
                result += "\n"
            result += err
        return result[:2000]  # limit length
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return "(cannot retrieve logs)"


def verify_url(url: str, timeout: int = 15) -> tuple[bool, str]:
    """Check if a URL responds with HTTP 200. Returns (ok, detail)."""
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "ghostprovider/1.0"})
        if r.status_code == 200:
            return True, "HTTP 200 OK"
        return False, f"HTTP {r.status_code}"
    except requests.ConnectionError:
        return False, "Connection refused"
    except requests.Timeout:
        return False, "Timed out"
    except Exception as e:
        return False, str(e)


def _is_ghost_container(container_id: str) -> bool:
    """Check if a container was created by ghostprovider (has ghostprovider label)."""
    try:
        r = subprocess.run(
            ["docker", "inspect", "--format", "{{index .Config.Labels \"ghostprovider.repo\"}}", container_id],
            capture_output=True, text=True, timeout=5,
        )
        return r.returncode == 0 and bool(r.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _cleanup_strategy(result: HostResult) -> None:
    """Remove containers started by a failed strategy attempt and their networks.

    Only removes containers that were created by ghostprovider (have ghostprovider label).
    """
    if result.compose_project:
        try:
            compose_cmd = _docker_compose_cmd()
            subprocess.run(
                compose_cmd + ["-p", result.compose_project, "down", "--remove-orphans"],
                capture_output=True, text=True, timeout=30,
            )
        except (RuntimeError, subprocess.TimeoutExpired, FileNotFoundError):
            pass
    elif result.container_ids:
        for cid in result.container_ids:
            if not _is_ghost_container(cid):
                continue
            try:
                from ghostprovider.services import _get_container_networks, _cleanup_networks
                networks = _get_container_networks(cid)
                subprocess.run(
                    ["docker", "rm", "-f", cid],
                    capture_output=True, text=True, timeout=10,
                )
                if networks:
                    _cleanup_networks(networks)
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass


def cleanup(analysis: RepoAnalysis, container_ids: list[str] | None = None, compose_project: str | None = None) -> None:
    # Remove the specific compose project if we know it
    if compose_project:
        try:
            compose_cmd = _docker_compose_cmd()
            subprocess.run(
                compose_cmd + ["-p", compose_project, "down", "--remove-orphans"],
                capture_output=True, text=True, timeout=30,
            )
        except (RuntimeError, subprocess.TimeoutExpired, FileNotFoundError):
            pass
    if container_ids:
        for cid in container_ids:
            if not _is_ghost_container(cid):
                continue
            try:
                from ghostprovider.services import _get_container_networks, _cleanup_networks
                networks = _get_container_networks(cid)
                subprocess.run(
                    ["docker", "rm", "-f", cid],
                    capture_output=True, text=True, timeout=10,
                )
                if networks:
                    _cleanup_networks(networks)
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass
    if analysis.clone_path and os.path.isdir(analysis.clone_path):
        shutil.rmtree(analysis.clone_path, ignore_errors=True)
