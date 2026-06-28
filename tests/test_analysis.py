"""Comprehensive tests for ghostprovider deep analysis."""
import json
import shutil
import tempfile
import textwrap
from pathlib import Path

import pytest

from ghostprovider.hoster import (
    RepoAnalysis,
    parse_github_url,
    _parse_requirements_txt,
    _parse_pyproject_toml_deps,
    _collect_node_deps,
    _collect_go_deps,
    _collect_rust_deps,
    _scan_python_source,
    _scan_node_source,
    _scan_go_source,
    _scan_rust_source,
    _is_library_project,
    _deep_analyze_project,
    _compute_host_score,
    _can_host_verdict,
    detect_app_category,
    _detect_language,
    find_free_port,
)

from ghostprovider.services import _parse_host_port, container_urls
from ghostprovider.installer import required_tools, missing_tools


# ═══════════════════════════════════════════════════════════
#  Fixtures: mock repository directories
# ═══════════════════════════════════════════════════════════

@pytest.fixture
def mock_repo():
    """Create a temporary directory that looks like a cloned repo."""
    tmpdir = Path(tempfile.mkdtemp(prefix="ghost_test_"))
    yield tmpdir
    shutil.rmtree(str(tmpdir), ignore_errors=True)


def _write(repo: Path, path: str, content: str):
    full = repo / path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)


# ═══════════════════════════════════════════════════════════
#  1. URL parsing
# ═══════════════════════════════════════════════════════════

class TestParseGithubUrl:
    def test_standard(self):
        assert parse_github_url("https://github.com/user/repo") == ("user", "repo")

    def test_with_git_suffix(self):
        assert parse_github_url("https://github.com/user/repo.git") == ("user", "repo")

    def test_with_trailing_slash(self):
        assert parse_github_url("https://github.com/user/repo/") == ("user", "repo")

    def test_invalid(self):
        assert parse_github_url("https://gitlab.com/user/repo") is None

    def test_no_protocol(self):
        assert parse_github_url("github.com/user/repo") is None


# ═══════════════════════════════════════════════════════════
#  2. Dependency parsing
# ═══════════════════════════════════════════════════════════

class TestParseRequirementsTxt:
    def test_simple(self, mock_repo):
        _write(mock_repo, "requirements.txt", "flask\nDjango>=4.0\nfastapi[all]\n")
        deps = _parse_requirements_txt(mock_repo)
        assert "flask" in deps
        assert "django" in deps
        assert "fastapi" in deps

    def test_comments_and_flags(self, mock_repo):
        _write(mock_repo, "requirements.txt", textwrap.dedent("""\
            # comment
            --index-url https://example.com
            -r other.txt
            click==8.0
            requests>=2.0,<3.0
        """))
        deps = _parse_requirements_txt(mock_repo)
        assert "click" in deps
        assert "requests" in deps
        assert "--index-url" not in deps
        assert "-r" not in deps

    def test_empty(self, mock_repo):
        _write(mock_repo, "requirements.txt", "")
        assert _parse_requirements_txt(mock_repo) == set()

    def no_file(self, mock_repo):
        assert _parse_requirements_txt(mock_repo) == set()


class TestParsePyprojectTomlDeps:
    def test_pep621(self, mock_repo):
        _write(mock_repo, "pyproject.toml", textwrap.dedent("""\
            [project]
            name = "myapp"
            dependencies = [
                "flask>=2.0",
                "click",
            ]
        """))
        deps = _parse_pyproject_toml_deps(mock_repo)
        assert "flask" in deps
        assert "click" in deps

    def test_no_deps(self, mock_repo):
        _write(mock_repo, "pyproject.toml", "[project]\nname = 'x'\n")
        assert _parse_pyproject_toml_deps(mock_repo) == set()


class TestCollectNodeDeps:
    def test_standard(self, mock_repo):
        pkg = {"dependencies": {"express": "^4.0", "commander": "^5.0"}}
        _write(mock_repo, "package.json", json.dumps(pkg))
        deps = _collect_node_deps(mock_repo)
        assert deps is not None
        assert "express" in deps
        assert "commander" in deps

    def test_with_dev(self, mock_repo):
        pkg = {
            "dependencies": {"next": "^12.0"},
            "devDependencies": {"electron": "^28.0"},
        }
        _write(mock_repo, "package.json", json.dumps(pkg))
        deps = _collect_node_deps(mock_repo)
        assert "next" in deps
        assert "electron" in deps

    def test_invalid_json(self, mock_repo):
        _write(mock_repo, "package.json", "not json")
        assert _collect_node_deps(mock_repo) is None

    def test_no_file(self, mock_repo):
        assert _collect_node_deps(mock_repo) is None


class TestCollectGoDeps:
    def test_standard(self, mock_repo):
        _write(mock_repo, "go.mod", textwrap.dedent("""\
            module example.com/myapp
            go 1.21
            require (
                github.com/gin-gonic/gin v1.9.0
                github.com/spf13/cobra v1.7.0
            )
        """))
        deps = _collect_go_deps(mock_repo)
        assert "github.com/gin-gonic/gin" in deps
        assert "github.com/spf13/cobra" in deps

    def test_single_require(self, mock_repo):
        _write(mock_repo, "go.mod", textwrap.dedent("""\
            module example.com/myapp
            go 1.21
            require github.com/gofiber/fiber/v2 v2.50.0
        """))
        deps = _collect_go_deps(mock_repo)
        assert "github.com/gofiber/fiber/v2" in deps


class TestCollectRustDeps:
    def test_standard(self, mock_repo):
        _write(mock_repo, "Cargo.toml", textwrap.dedent("""\
            [package]
            name = "myapp"
            version = "0.1.0"

            [dependencies]
            actix-web = "4"
            clap = { version = "4", features = ["derive"] }
            serde = "1"
        """))
        deps = _collect_rust_deps(mock_repo)
        assert "actix-web" in deps
        assert "clap" in deps
        assert "serde" in deps

    def test_no_deps(self, mock_repo):
        _write(mock_repo, "Cargo.toml", "[package]\nname = 'x'\n")
        assert _collect_rust_deps(mock_repo) == set()


# ═══════════════════════════════════════════════════════════
#  3. Source code scanning
# ═══════════════════════════════════════════════════════════

class TestScanPythonSource:
    def test_flask_app(self, mock_repo):
        _write(mock_repo, "app.py", textwrap.dedent("""\
            from flask import Flask
            app = Flask(__name__)
            @app.route("/")
            def hello():
                return "hello"
            if __name__ == "__main__":
                app.run()
        """))
        info = _scan_python_source(mock_repo)
        assert info["has_http_server"] is True

    def test_cli_click(self, mock_repo):
        _write(mock_repo, "cli.py", textwrap.dedent("""\
            import click
            @click.command()
            def main():
                print("hello")
            if __name__ == "__main__":
                main()
        """))
        info = _scan_python_source(mock_repo)
        assert info["has_cli"] is True
        assert info["has_http_server"] is False

    def test_argparse(self, mock_repo):
        _write(mock_repo, "main.py", textwrap.dedent("""\
            import argparse
            parser = argparse.ArgumentParser()
            parser.parse_args()
        """))
        info = _scan_python_source(mock_repo)
        assert info["has_cli"] is True

    def test_pygame_gui(self, mock_repo):
        _write(mock_repo, "game.py", "import pygame; pygame.init()")
        info = _scan_python_source(mock_repo)
        assert info["has_desktop_gui"] is True

    def test_no_signals(self, mock_repo):
        _write(mock_repo, "math_util.py", "def add(a, b): return a + b")
        info = _scan_python_source(mock_repo)
        assert info["has_http_server"] is False
        assert info["has_cli"] is False
        assert info["has_desktop_gui"] is False
        assert info["is_library"] is False

    def test_django_source(self, mock_repo):
        _write(mock_repo, "manage.py", textwrap.dedent("""\
            os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myapp.settings')
            from django.core.management import execute_from_command_line
        """))
        info = _scan_python_source(mock_repo)
        assert info["has_http_server"] is True

    def test_fastapi_uvicorn(self, mock_repo):
        _write(mock_repo, "main.py", textwrap.dedent("""\
            from fastapi import FastAPI
            app = FastAPI()
            if __name__ == "__main__":
                import uvicorn
                uvicorn.run(app)
        """))
        info = _scan_python_source(mock_repo)
        assert info["has_http_server"] is True


class TestScanNodeSource:
    def test_express(self, mock_repo):
        _write(mock_repo, "server.js", textwrap.dedent("""\
            const express = require('express');
            const app = express();
            app.listen(3000);
        """))
        info = _scan_node_source(mock_repo)
        assert info["has_http_server"] is True

    def test_cli_commander(self, mock_repo):
        _write(mock_repo, "cli.js", textwrap.dedent("""\
            #!/usr/bin/env node
            const { program } = require('commander');
            program.parse(process.argv);
        """))
        info = _scan_node_source(mock_repo)
        assert info["has_cli"] is True
        assert info["has_http_server"] is False

    def test_electron(self, mock_repo):
        _write(mock_repo, "main.js", textwrap.dedent("""\
            const { app, BrowserWindow } = require('electron');
        """))
        info = _scan_node_source(mock_repo)
        assert info["has_desktop_gui"] is True

    def test_http_create_server(self, mock_repo):
        _write(mock_repo, "server.js", textwrap.dedent("""\
            const http = require('http');
            http.createServer((req, res) => res.end('ok')).listen(3000);
        """))
        info = _scan_node_source(mock_repo)
        assert info["has_http_server"] is True


class TestScanGoSource:
    def test_gin(self, mock_repo):
        _write(mock_repo, "main.go", textwrap.dedent("""\
            package main
            import "github.com/gin-gonic/gin"
            func main() {
                r := gin.Default()
                r.Run()
            }
        """))
        info = _scan_go_source(mock_repo)
        assert info["has_http_server"] is True

    def test_http_listen(self, mock_repo):
        _write(mock_repo, "main.go", textwrap.dedent("""\
            package main
            import "net/http"
            func main() {
                http.ListenAndServe(":8080", nil)
            }
        """))
        info = _scan_go_source(mock_repo)
        assert info["has_http_server"] is True

    def test_cli_cobra(self, mock_repo):
        _write(mock_repo, "main.go", textwrap.dedent("""\
            package main
            import "github.com/spf13/cobra"
            func main() { cobra.Execute() }
        """))
        info = _scan_go_source(mock_repo)
        assert info["has_cli"] is True


class TestScanRustSource:
    def test_actix(self, mock_repo):
        _write(mock_repo, "src/main.rs", textwrap.dedent("""\
            use actix_web::{web, App, HttpServer};
            #[actix_web::main]
            async fn main() {
                HttpServer::new(|| App::new())
                    .bind("127.0.0.1:8080").unwrap()
                    .run().await;
            }
        """))
        info = _scan_rust_source(mock_repo)
        assert info["has_http_server"] is True

    def test_cli_clap(self, mock_repo):
        _write(mock_repo, "src/main.rs", textwrap.dedent("""\
            use clap::Parser;
            #[derive(Parser)]
            struct Args {}
            fn main() { Args::parse(); }
        """))
        info = _scan_rust_source(mock_repo)
        assert info["has_cli"] is True


# ═══════════════════════════════════════════════════════════
#  4. Library detection
# ═══════════════════════════════════════════════════════════

class TestIsLibraryProject:
    def test_python_lib(self, mock_repo):
        _write(mock_repo, "setup.py", "from setuptools import setup; setup()")
        _write(mock_repo, "src/mylib/__init__.py", "")
        analysis = RepoAnalysis(language="Python", clone_path=str(mock_repo))
        assert _is_library_project(mock_repo, analysis) is True

    def test_python_app(self, mock_repo):
        _write(mock_repo, "setup.py", "from setuptools import setup; setup()")
        _write(mock_repo, "app.py", "print('hello')")
        analysis = RepoAnalysis(language="Python", clone_path=str(mock_repo))
        assert _is_library_project(mock_repo, analysis) is False

    def test_rust_lib(self, mock_repo):
        _write(mock_repo, "Cargo.toml", "[package]\nname='mylib'\n\n[lib]\nname='mylib'\n")
        _write(mock_repo, "src/lib.rs", "")
        analysis = RepoAnalysis(language="Rust", clone_path=str(mock_repo))
        assert _is_library_project(mock_repo, analysis) is True

    def test_go_no_main(self, mock_repo):
        _write(mock_repo, "go.mod", "module example.com/mylib\n")
        _write(mock_repo, "utils.go", "package mylib\nfunc Add(a,b int) int { return a+b }\n")
        analysis = RepoAnalysis(language="Go", clone_path=str(mock_repo))
        assert _is_library_project(mock_repo, analysis) is True

    def test_node_library(self, mock_repo):
        pkg = {"name": "mylib", "main": "index.js", "version": "1.0.0"}
        _write(mock_repo, "package.json", json.dumps(pkg))
        _write(mock_repo, "index.js", "module.exports = { foo: 1 }")
        analysis = RepoAnalysis(language="Node.js", clone_path=str(mock_repo))
        assert _is_library_project(mock_repo, analysis) is True


# ═══════════════════════════════════════════════════════════
#  5. Scoring system
# ═══════════════════════════════════════════════════════════

class TestComputeHostScore:
    def test_docker_compose_high(self):
        a = RepoAnalysis(has_compose=True)
        a.deep_analysis = {}
        score, _ = _compute_host_score(a)
        assert score >= 80

    def test_dockerfile_good(self):
        a = RepoAnalysis(has_dockerfile=True)
        a.deep_analysis = {}
        score, _ = _compute_host_score(a)
        assert score >= 60

    def test_web_framework_high(self):
        a = RepoAnalysis(has_requirements=True, language="Python", clone_path="/tmp/_")
        a.deep_analysis = {"web_framework": "flask", "has_http_server": True}
        score, _ = _compute_host_score(a)
        assert score >= 90

    def test_cli_tool_low(self):
        a = RepoAnalysis(has_requirements=True, language="Python", clone_path="/tmp/_")
        a.deep_analysis = {"has_cli": True, "has_http_server": False}
        score, _ = _compute_host_score(a)
        assert score < 0

    def test_desktop_gui_negative(self):
        a = RepoAnalysis(has_package_json=True, language="Node.js", clone_path="/tmp/_")
        a.deep_analysis = {"has_desktop_gui": True, "gui_dep": True}
        score, _ = _compute_host_score(a)
        assert score < 0

    def test_library_negative(self):
        a = RepoAnalysis(language="Python", clone_path="/tmp/_")
        a.deep_analysis = {"is_library": True}
        score, _ = _compute_host_score(a)
        assert score < 0

    def test_static_site_medium(self):
        a = RepoAnalysis(has_index=True, language="Static HTML", clone_path="/tmp/_")
        a.deep_analysis = {}
        score, _ = _compute_host_score(a)
        assert 20 <= score < 80

    def test_empty_no_score(self):
        a = RepoAnalysis()
        a.deep_analysis = {}
        score, _ = _compute_host_score(a)
        assert score < 20

    def test_can_host_thresholds(self):
        # Docker compose → always can_host
        assert _can_host_verdict(RepoAnalysis(has_compose=True))[0] is True
        # Dockerfile → always can_host
        assert _can_host_verdict(RepoAnalysis(has_dockerfile=True))[0] is True
        # Web framework → can_host
        a = RepoAnalysis()
        a.deep_analysis = {"web_framework": "flask", "has_http_server": True}
        assert _can_host_verdict(a)[0] is True
        # CLI only → cannot_host
        a2 = RepoAnalysis()
        a2.deep_analysis = {"has_cli": True}
        assert _can_host_verdict(a2)[0] is False


# ═══════════════════════════════════════════════════════════
#  6. Category detection
# ═══════════════════════════════════════════════════════════

class TestDetectAppCategory:
    def test_web_framework_deep(self):
        a = RepoAnalysis(name="myapp")
        a.deep_analysis = {"web_framework": "flask"}
        cat, reason, is_web = detect_app_category(a, None)
        assert cat == "web_app"
        assert is_web is True

    def test_desktop_gui_deep(self):
        a = RepoAnalysis(name="myapp")
        a.deep_analysis = {"has_desktop_gui": True}
        cat, reason, is_web = detect_app_category(a, None)
        assert cat == "desktop_app"
        assert is_web is False

    def test_cli_deep(self):
        a = RepoAnalysis(name="myapp")
        a.deep_analysis = {"has_cli": True, "has_http_server": False}
        cat, reason, is_web = detect_app_category(a, None)
        assert cat == "cli"
        assert is_web is False

    def test_library_deep(self):
        a = RepoAnalysis(name="myapp")
        a.deep_analysis = {"is_library": True}
        cat, reason, is_web = detect_app_category(a, None)
        assert cat == "library"
        assert is_web is False

    def test_static_fallback(self):
        a = RepoAnalysis(name="myapp", has_index=True)
        a.deep_analysis = {}
        cat, _, is_web = detect_app_category(a, None)
        assert cat == "web_app"
        assert is_web is True

    def test_github_metadata(self):
        metadata = {"description": "A desktop application for editing photos", "topics": []}
        a = RepoAnalysis(name="myapp")
        a.deep_analysis = {}
        cat, _, is_web = detect_app_category(a, metadata)
        assert is_web is False

    def test_github_topics_library(self):
        metadata = {"description": "", "topics": ["library", "sdk"]}
        a = RepoAnalysis(name="myapp")
        a.deep_analysis = {}
        cat, _, is_web = detect_app_category(a, metadata)
        assert is_web is False


# ═══════════════════════════════════════════════════════════
#  7. Language detection
# ═══════════════════════════════════════════════════════════

class TestDetectLanguage:
    def test_docker_compose(self):
        assert _detect_language(RepoAnalysis(has_compose=True)) == "Container (Docker)"

    def test_dockerfile(self):
        assert _detect_language(RepoAnalysis(has_dockerfile=True)) == "Container (Docker)"

    def test_python(self):
        assert _detect_language(RepoAnalysis(has_requirements=True)) == "Python"

    def test_node(self):
        assert _detect_language(RepoAnalysis(has_package_json=True)) == "Node.js"

    def test_go(self):
        assert _detect_language(RepoAnalysis(has_go_mod=True)) == "Go"

    def test_rust(self):
        assert _detect_language(RepoAnalysis(has_cargo=True)) == "Rust"

    def test_html(self):
        assert _detect_language(RepoAnalysis(has_index=True)) == "Static HTML"

    def test_unknown(self):
        assert _detect_language(RepoAnalysis()) == "Unknown"


# ═══════════════════════════════════════════════════════════
#  8. End-to-end deep analysis with mock repos
# ═══════════════════════════════════════════════════════════

class TestDeepAnalyzeProject:
    def test_python_flask_app(self, mock_repo):
        _write(mock_repo, "requirements.txt", "flask\n")
        _write(mock_repo, "app.py", "from flask import Flask\napp = Flask(__name__)\napp.run()\n")
        a = RepoAnalysis(
            has_requirements=True,
            language="Python",
            clone_path=str(mock_repo),
        )
        a = _deep_analyze_project(a)
        assert a.web_framework == "flask"
        assert a.has_http_server is True
        assert a.has_cli is False
        assert a.is_library is False

    def test_node_express(self, mock_repo):
        pkg = {"dependencies": {"express": "^4.0"}}
        _write(mock_repo, "package.json", json.dumps(pkg))
        _write(mock_repo, "server.js", "const app = require('express')(); app.listen(3000);")
        a = RepoAnalysis(
            has_package_json=True,
            language="Node.js",
            clone_path=str(mock_repo),
        )
        a = _deep_analyze_project(a)
        assert a.web_framework
        assert a.has_http_server is True

    def test_go_gin(self, mock_repo):
        _write(mock_repo, "go.mod", "module example.com/myapp\nrequire github.com/gin-gonic/gin v1.9.0\n")
        _write(mock_repo, "main.go", 'package main\nimport "github.com/gin-gonic/gin"\nfunc main() { r := gin.Default(); r.Run() }\n')
        a = RepoAnalysis(
            has_go_mod=True,
            language="Go",
            clone_path=str(mock_repo),
        )
        a = _deep_analyze_project(a)
        assert "gin" in a.web_framework
        assert a.has_http_server is True

    def test_python_cli_tool(self, mock_repo):
        _write(mock_repo, "requirements.txt", "click\n")
        _write(mock_repo, "cli.py", "import click\n@click.command()\ndef main(): pass\n")
        a = RepoAnalysis(
            has_requirements=True,
            language="Python",
            clone_path=str(mock_repo),
        )
        a = _deep_analyze_project(a)
        assert a.has_cli is True
        assert a.has_http_server is False

    def test_empty_project(self, mock_repo):
        _write(mock_repo, "README.md", "# empty")
        a = RepoAnalysis(
            language="Unknown",
            clone_path=str(mock_repo),
        )
        a = _deep_analyze_project(a)
        assert a.web_framework == ""
        assert a.has_http_server is False
        assert a.has_cli is False

    def test_malformed_package_json(self, mock_repo):
        _write(mock_repo, "package.json", "not json {{{")
        a = RepoAnalysis(
            has_package_json=True,
            language="Node.js",
            clone_path=str(mock_repo),
        )
        a = _deep_analyze_project(a)
        # Should not crash

    def test_malformed_requirements(self, mock_repo):
        _write(mock_repo, "requirements.txt", "\x00\x00\x00\x00")
        a = RepoAnalysis(
            has_requirements=True,
            language="Python",
            clone_path=str(mock_repo),
        )
        a = _deep_analyze_project(a)
        # Should not crash

    def test_rust_actix(self, mock_repo):
        _write(mock_repo, "Cargo.toml", "[dependencies]\nactix-web = \"4\"\n")
        _write(mock_repo, "src/main.rs", "use actix_web::*;\n")
        a = RepoAnalysis(
            has_cargo=True,
            language="Rust",
            clone_path=str(mock_repo),
        )
        a = _deep_analyze_project(a)
        assert "actix" in a.web_framework
        assert a.has_http_server is True


# ═══════════════════════════════════════════════════════════
#  9. Port utilities
# ═══════════════════════════════════════════════════════════

class TestPortUtils:
    def test_find_free_port(self):
        port = find_free_port()
        assert 8000 <= port <= 32768

    def test_find_free_port_specific(self):
        # Find a port starting from a specific number
        port = find_free_port(start=30000)
        assert 30000 <= port

    def test_parse_host_port(self):
        assert _parse_host_port("0.0.0.0:3000") == "3000"
        assert _parse_host_port(":::3000") == "3000"
        assert _parse_host_port("[::1]:3000") == "3000"
        assert _parse_host_port("3000") == "3000"
        assert _parse_host_port("") is None

    def test_container_urls(self):
        ports = "0.0.0.0:3000->3000/tcp, 0.0.0.0:8080->80/tcp"
        urls = container_urls(ports)
        assert "http://localhost:3000" in urls
        assert "http://localhost:8080" in urls


# ═══════════════════════════════════════════════════════════
#  10. Installer
# ═══════════════════════════════════════════════════════════

class TestInstaller:
    def test_required_tools(self):
        assert "git" in required_tools(False, False, False, False, False, False)
        assert "docker" in required_tools(True, False, False, False, False, False)
        assert "docker" in required_tools(False, True, False, False, False, False)
        assert "docker" in required_tools(False, False, False, False, False, False, has_index=True)
        # git is always required, docker only for compose/dockerfile/index
        tools = required_tools(False, False, True, False, False, False)
        assert "git" in tools
        assert "docker" not in tools

    def test_missing_tools(self):
        missing = missing_tools(["python3"])
        assert isinstance(missing, list)


# ═══════════════════════════════════════════════════════════
#  11. Edge cases & malformed inputs
# ═══════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_requirements_with_unicode(self, mock_repo):
        _write(mock_repo, "requirements.txt", "flask\n# комментарий\nclick\n")
        deps = _parse_requirements_txt(mock_repo)
        assert "flask" in deps
        assert "click" in deps

    def test_requirements_editable(self, mock_repo):
        _write(mock_repo, "requirements.txt", textwrap.dedent("""\
            -e git+https://example.com/repo.git#egg=myapp
            requests
        """))
        deps = _parse_requirements_txt(mock_repo)
        assert "requests" in deps
        assert "-e" not in deps

    def test_pyproject_poetry_format(self, mock_repo):
        """[tool.poetry.dependencies] format."""
        _write(mock_repo, "pyproject.toml", textwrap.dedent("""\
            [tool.poetry]
            name = "myapp"
            [tool.poetry.dependencies]
            python = "^3.10"
            flask = "^2.0"
        """))
        deps = _parse_pyproject_toml_deps(mock_repo)
        assert "flask" in deps
        assert "python" not in deps  # python version constraint, not a dep

    def test_go_mod_with_tabs(self, mock_repo):
        _write(mock_repo, "go.mod", "module x\nrequire (\n\tgithub.com/gin-gonic/gin v1.9.0\n)\n")
        deps = _collect_go_deps(mock_repo)
        assert "gin-gonic/gin" in deps or "github.com/gin-gonic/gin" in deps

    def test_rust_deps_with_features(self, mock_repo):
        _write(mock_repo, "Cargo.toml", textwrap.dedent("""\
            [dependencies]
            actix-web = { version = "4", features = ["ssl"] }
            tokio = { version = "1", features = ["full"] }
        """))
        deps = _collect_rust_deps(mock_repo)
        assert "actix-web" in deps
        assert "tokio" in deps

    def test_binary_files_in_repo(self, mock_repo):
        _write(mock_repo, "requirements.txt", "flask\n")
        _write(mock_repo, "app.py", "from flask import Flask\napp = Flask(__name__)\napp.run()\n")
        _write(mock_repo, "image.png", "\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00")
        a = RepoAnalysis(
            has_requirements=True,
            language="Python",
            clone_path=str(mock_repo),
        )
        a = _deep_analyze_project(a)
        assert a.has_http_server is True
        assert not a.has_cli
        # Binary files should not crash the scan

    def test_large_file_skipped(self, mock_repo):
        _write(mock_repo, "huge.py", "x = 1\n" * 100000)
        _write(mock_repo, "app.py", "from flask import Flask\napp = Flask(__name__)\napp.run()\n")
        info = _scan_python_source(mock_repo)
        assert info["has_http_server"] is True

    def test_no_clone_path(self):
        a = RepoAnalysis()
        a = _deep_analyze_project(a)
        assert a.deep_analysis == {}

    def test_empty_rust_project(self, mock_repo):
        _write(mock_repo, "Cargo.toml", "[package]\nname='x'\nversion='0.1.0'\n")
        _write(mock_repo, "src/lib.rs", "")
        a = RepoAnalysis(
            has_cargo=True,
            language="Rust",
            clone_path=str(mock_repo),
        )
        a = _deep_analyze_project(a)
        assert a.web_framework == ""
        assert a.has_http_server is False
        assert a.is_library is True

    def test_detect_app_category_deep_overrides_keyword(self):
        """Name contains 'app' (web keyword) but deep says GUI."""
        a = RepoAnalysis(name="myapp")
        a.deep_analysis = {"has_desktop_gui": True}
        cat, _, is_web = detect_app_category(a, None)
        assert cat == "desktop_app"
        assert is_web is False

    def test_scoring_no_clone_path_doesnt_crash(self):
        a = RepoAnalysis()
        score, rec = _compute_host_score(a)
        assert isinstance(score, int)
        assert isinstance(rec, str)


# ═══════════════════════════════════════════════════════════
#  12. Service fingerprinting (analyzer)
# ═══════════════════════════════════════════════════════════

class TestServiceFingerprint:
    def test_service_fingerprint_dataclass(self):
        from ghostprovider.analyzer import ServiceFingerprint
        fp = ServiceFingerprint(port=8080, proto="tcp", service_type="web_app",
                                 service_name="Test", confidence=80)
        assert fp.port == 8080
        assert fp.can_host is True

    def test_service_fingerprint_can_host_true(self):
        from ghostprovider.analyzer import ServiceFingerprint
        for svc_type in ("web_app", "api_server", "media_server", "search_engine",
                         "dashboard", "dev_server", "proxy", "file_server"):
            fp = ServiceFingerprint(port=1, proto="tcp", service_type=svc_type,
                                     service_name="x", confidence=50)
            assert fp.can_host, f"{svc_type} should be hostable"

    def test_service_fingerprint_can_host_false(self):
        from ghostprovider.analyzer import ServiceFingerprint
        for svc_type in ("system_service", "desktop_app", "game_server",
                         "database", "message_broker", "vpn", "unknown"):
            fp = ServiceFingerprint(port=1, proto="tcp", service_type=svc_type,
                                     service_name="x", confidence=50)
            assert not fp.can_host, f"{svc_type} should NOT be hostable"

    def test_service_signatures_search_engine(self):
        from ghostprovider.analyzer import SERVICE_SIGNATURES
        searx_body = b"<html><head><title>SearXNG</title></head><body>search</body></html>"
        whoogle_body = b"<title>Whoogle Search</title>"
        yacy_body = b"<title>YaCy Search</title>"
        shiori_body = b"<title>Shiori</title>"
        librey_body = b"<html>libreYou</html>"

        found_searx = found_whoogle = found_yacy = found_shiori = found_librey = False
        for pattern, svc_type, svc_name, confidence in SERVICE_SIGNATURES:
            if svc_type == "search_engine":
                if pattern.search(searx_body):
                    found_searx = True
                if pattern.search(whoogle_body):
                    found_whoogle = True
                if pattern.search(yacy_body):
                    found_yacy = True
                if pattern.search(shiori_body):
                    found_shiori = True
                if pattern.search(librey_body):
                    found_librey = True

        assert found_searx, "SearXNG body should match search_engine signature"
        assert found_whoogle, "Whoogle body should match search_engine signature"
        assert found_yacy, "YaCy body should match search_engine signature"
        assert found_shiori, "Shiori body should match search_engine signature"
        assert found_librey, "LibreY body should match search_engine signature"

    def test_service_signatures_media_server(self):
        from ghostprovider.analyzer import SERVICE_SIGNATURES
        samples = {
            "Jellyfin": b"<title>Jellyfin</title>",
            "Plex": b"<title>Plex Media Server</title>",
            "Navidrome": b"<title>Navidrome</title>",
        }
        for name, body in samples.items():
            matched = any(
                pat.search(body) for pat, st, sn, conf in SERVICE_SIGNATURES
                if st == "media_server"
            )
            assert matched, f"{name} body should match media_server signature"

    def test_service_signatures_dashboard(self):
        from ghostprovider.analyzer import SERVICE_SIGNATURES
        samples = {
            "Home Assistant": b"<title>Home Assistant</title>",
            "Grafana": b"<title>Grafana</title>",
            "Portainer": b"<title>Portainer</title>",
            "Netdata": b"<title>Netdata</title>",
            "Prometheus": b"<title>Prometheus</title>",
        }
        for name, body in samples.items():
            matched = any(
                pat.search(body) for pat, st, sn, conf in SERVICE_SIGNATURES
            )
            assert matched, f"{name} body should match a service signature"

    def test_service_signatures_generic_nginx(self):
        from ghostprovider.analyzer import SERVICE_SIGNATURES
        # nginx is detected via server header, not body pattern
        nginx_body = b"<html><body>Welcome to nginx!</body></html>"
        matched_nginx = any(
            pat.search(nginx_body) for pat, st, sn, conf in SERVICE_SIGNATURES
            if "nginx" in sn.lower() or st == "proxy"
        )
        # nginx is detected via Server: header, not body; just check no crash
        assert isinstance(matched_nginx, bool)

    def test_service_signatures_unknown(self):
        from ghostprovider.analyzer import SERVICE_SIGNATURES
        unknown_body = b"<html><body>Hello World</body></html>"
        matched = any(
            pat.search(unknown_body) for pat, st, sn, conf in SERVICE_SIGNATURES
        )
        assert not matched, "Generic body should not match any signature"

    def test_analysis_result_hostable_services(self):
        from ghostprovider.analyzer import AnalysisResult, NetworkInfo, ServiceFingerprint
        services = [
            ServiceFingerprint(port=80, proto="tcp", service_type="web_app",
                               service_name="Nginx", confidence=80),
            ServiceFingerprint(port=8080, proto="tcp", service_type="search_engine",
                               service_name="SearXNG", confidence=95),
            ServiceFingerprint(port=3000, proto="tcp", service_type="system_service",
                               service_name="Some system", confidence=50),
        ]
        info = NetworkInfo(services=services)
        result = AnalysisResult(network_info=info)
        assert len(result.hostable_services) == 2
        assert len(result.non_hostable_services) == 1
        assert result.hostable_services[0].service_name == "Nginx"
        assert result.hostable_services[1].service_name == "SearXNG"
        assert result.non_hostable_services[0].service_name == "Some system"

    def test_analysis_result_no_services(self):
        from ghostprovider.analyzer import AnalysisResult, NetworkInfo
        result = AnalysisResult(network_info=NetworkInfo())
        assert result.hostable_services == []
        assert result.non_hostable_services == []


# ═══════════════════════════════════════════════════════════
#  13. Search engine category & scoring (hoster)
# ═══════════════════════════════════════════════════════════

class TestSearchEngineCategory:
    def test_search_keyword_in_name(self):
        a = RepoAnalysis(name="searxng-docker")
        a.deep_analysis = {}
        cat, reason, is_web = detect_app_category(a, None)
        assert cat == "search_engine", f"Expected search_engine, got {cat}"
        assert is_web is True

    def test_search_keyword_whoogle(self):
        a = RepoAnalysis(name="whoogle-search")
        a.deep_analysis = {}
        cat, _, _ = detect_app_category(a, None)
        assert cat == "search_engine"

    def test_search_keyword_in_description(self):
        metadata = {"description": "A meta search engine", "topics": []}
        a = RepoAnalysis(name="myapp")
        a.deep_analysis = {}
        cat, _, _ = detect_app_category(a, metadata)
        assert cat == "search_engine"

    def test_search_not_confused_with_web(self):
        """'search' should go to search_engine, not generic web_app"""
        a = RepoAnalysis(name="awesome-search")
        a.deep_analysis = {}
        cat, _, _ = detect_app_category(a, None)
        assert cat == "search_engine"

    def test_search_engine_scoring_with_searx_in_name(self):
        a = RepoAnalysis(name="searxng")
        a.deep_analysis = {}
        score, rec = _compute_host_score(a)
        assert score >= 25, f"searx in name should add +25, got {score}"

    def test_search_engine_high_score_with_web_framework(self):
        a = RepoAnalysis(name="searxng", has_requirements=True, language="Python",
                         clone_path="/tmp/_")
        a.deep_analysis = {"web_framework": "flask", "has_http_server": True}
        score, rec = _compute_host_score(a)
        # Flask (+50) + HTTP server (+40) + searx name (+25) = 115
        assert score >= 100, f"Expected high score for searx+web, got {score}"

    def test_search_engine_can_host(self):
        a = RepoAnalysis(name="searxng", has_requirements=True, language="Python")
        a.deep_analysis = {"web_framework": "flask", "has_http_server": True}
        can_host, _ = _can_host_verdict(a)
        assert can_host is True

    def test_gh_topics_search_scoring(self):
        a = RepoAnalysis(name="myapp")
        a.deep_analysis = {"gh_topics_search": True}
        score, rec = _compute_host_score(a)
        assert score >= 10, f"Expected +10 for search topics, got {score}"

    def test_app_category_detection_with_search_topics(self):
        metadata = {"description": "", "topics": ["search-engine", "docker"]}
        a = RepoAnalysis(name="myapp")
        a.deep_analysis = {}
        cat, _, _ = detect_app_category(a, metadata)
        assert cat == "search_engine"

    def test_search_engine_detected_in_phase1_deep(self):
        """Deep analysis web_framework should take priority over search keyword."""
        a = RepoAnalysis(name="searxng")
        a.deep_analysis = {"web_framework": "flask"}
        cat, _, _ = detect_app_category(a, None)
        # Phase 1 (deep analysis) takes priority → web_app
        assert cat == "web_app"

    def test_search_engine_no_cli_confusion(self):
        """Search engines should NOT be classified as CLI."""
        a = RepoAnalysis(name="searxng")
        a.deep_analysis = {}
        cat, _, _ = detect_app_category(a, None)
        assert cat == "search_engine"
        assert cat != "cli"


# ═══════════════════════════════════════════════════════════
#  14. SEARCH_ENGINE_INDICATORS
# ═══════════════════════════════════════════════════════════

class TestSearchEngineIndicators:
    def test_search_engine_indicators_defined(self):
        from ghostprovider.hoster import SEARCH_ENGINE_INDICATORS
        assert "search" in SEARCH_ENGINE_INDICATORS
        assert "searx" in SEARCH_ENGINE_INDICATORS
        assert "whoogle" in SEARCH_ENGINE_INDICATORS
        assert "yacy" in SEARCH_ENGINE_INDICATORS

    def test_search_engine_indicators_in_category_keywords(self):
        from ghostprovider.hoster import CATEGORY_KEYWORDS
        assert "search_engine" in CATEGORY_KEYWORDS
        assert "searxng" in CATEGORY_KEYWORDS["search_engine"]
