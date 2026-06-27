"""Docker service discovery and management for ghostprovider."""

import json
import os
import re
import socket
import subprocess
import time
from dataclasses import dataclass


@dataclass
class ContainerInfo:
    name: str
    container_id: str
    image: str
    status: str
    state: str
    ports: str
    labels: dict[str, str] | None = None


def _parse_host_port(left: str) -> str | None:
    """Extract host port from a Docker port mapping left-hand side.

    Handles:
      - ``0.0.0.0:3000``         → ``3000``
      - ``[::1]:3000``           → ``3000``
      - ``:::3000``              → ``3000``
      - ``3000`` (no bind IP)    → ``3000``
    """
    # IPv6: [::1]:port  or  [xxxx:xxxx::xxxx]:port
    ipv6_match = re.search(r"\[.*\]:(\d+)$", left)
    if ipv6_match:
        return ipv6_match.group(1)
    # IPv4 or bare: 0.0.0.0:port  or  :::port  or  just port
    parts = left.rsplit(":", 1)
    candidate = parts[-1].strip()
    if candidate.isdigit():
        return candidate
    return None


def container_urls(ports: str) -> list[str]:
    """Extract http://localhost:PORT URLs from a Docker ports string.

    Example input:  '0.0.0.0:3000->3000/tcp, 0.0.0.0:8080->80/tcp'
    """
    urls: list[str] = []
    if not ports:
        return urls
    for part in ports.split(","):
        part = part.strip()
        if "->" not in part:
            continue
        left = part.split("->")[0].strip()
        host_port = _parse_host_port(left)
        if host_port:
            urls.append(f"http://localhost:{host_port}")
    return urls


def list_containers(all_containers: bool = False) -> list[ContainerInfo]:
    try:
        cmd = ["docker", "ps"]
        if all_containers:
            cmd.append("-a")
        cmd.extend(["--format", "{{json .}}"])
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            return []
        containers = []
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                names_raw = data.get("Names", [])
                if isinstance(names_raw, str):
                    name_str = names_raw.lstrip("/")
                else:
                    name_str = names_raw[0].lstrip("/") if names_raw else ""
                labels_raw = data.get("Labels", "")
                labels_dict = None
                if labels_raw:
                    labels_dict = {}
                    for part in labels_raw.split(","):
                        if "=" in part:
                            k, v = part.split("=", 1)
                            labels_dict[k.strip()] = v.strip()
                containers.append(ContainerInfo(
                    name=name_str,
                    container_id=data.get("ID", "")[:12],
                    image=data.get("Image", ""),
                    status=data.get("Status", ""),
                    state=data.get("State", ""),
                    ports=data.get("Ports", ""),
                    labels=labels_dict,
                ))
            except (json.JSONDecodeError, KeyError):
                continue
        from ghostprovider.state import migrate_old_containers
        migrate_old_containers(containers)
        return containers
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


def _exec_docker_action(action: str, name: str) -> str:
    try:
        result = subprocess.run(
            ["docker", action, name],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return f"Container '{name}' {action}ed successfully"
        error = result.stderr.strip() or "unknown error"
        return f"Failed to {action} '{name}': {error}"
    except subprocess.TimeoutExpired:
        return f"Timeout during '{action}' for container '{name}'"
    except FileNotFoundError:
        return "Docker is not available on this system"


def stop_container(name: str) -> str:
    return _exec_docker_action("stop", name)


def start_container(name: str) -> str:
    return _exec_docker_action("start", name)


def _get_host_ports(name: str) -> list[tuple[str, int]]:
    try:
        result = subprocess.run(
            ["docker", "port", name],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return []
        ports: list[tuple[str, int]] = []
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            parts = line.split(" -> ")
            if len(parts) == 2:
                host_part = parts[1].strip()
                port_str = _parse_host_port(host_part)
                if not port_str:
                    continue
                try:
                    port = int(port_str)
                except ValueError:
                    continue
                if host_part.startswith("["):
                    host = host_part.split("]")[0][1:]
                elif ":" in host_part:
                    host = host_part.rsplit(":", 1)[0].strip()
                    if not host or host in ("::", "0.0.0.0"):
                        host = "127.0.0.1"
                else:
                    host = "127.0.0.1"
                ports.append((host, port))
        return ports
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []


def _get_container_health(name: str) -> str | None:
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{json .State.Health}}", name],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        data = json.loads(result.stdout)
        return data.get("Status") if isinstance(data, dict) else None
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        return None


def wait_container_ready(name: str, timeout: int = 60) -> bool:
    """Wait until a container is fully ready (healthy or port-responsive).
    
    Guarantees at least MIN_VISIBLE seconds of pending state so the UI
    has time to show feedback before the state flips to 'running'.
    """
    MIN_VISIBLE = 3.0
    deadline = time.time() + timeout
    start = time.time()

    while time.time() < deadline:
        # Check Docker-level state
        try:
            result = subprocess.run(
                ["docker", "inspect", "--format", "{{.State.Status}}", name],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                return False
            status = result.stdout.strip()
            # Retry on transitional states, only bail on definitive failure
            if status in ("exited", "dead"):
                return False
            if status == "running":
                pass  # proceed to health/port checks
            else:
                time.sleep(2)
                continue
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

        # Healthcheck
        health = _get_container_health(name)
        has_healthcheck = health is not None

        if health == "healthy":
            return True
        if health == "unhealthy":
            return False

        # Port check — try to connect to exposed ports
        host_ports = _get_host_ports(name)
        port_ok = False
        if host_ports:
            for host, port in host_ports:
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                        s.settimeout(2)
                        if s.connect_ex((host, port)) == 0:
                            port_ok = True
                            break
                except OSError:
                    pass

        elapsed = time.time() - start
        if elapsed >= MIN_VISIBLE:
            if port_ok:
                return True
            # No healthcheck and no ports — running is our best signal
            if not has_healthcheck and not host_ports:
                return True

        time.sleep(1)
    return False


def _container_name_from_id(container_id: str) -> str | None:
    try:
        r = subprocess.run(
            ["docker", "inspect", "--format", "{{.Name}}", container_id],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            return r.stdout.strip().lstrip("/")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def _find_clone_on_disk(repo_url: str) -> str | None:
    """Scan /tmp/ghost_* directories for a git repo matching the URL."""
    import glob
    for d in glob.glob("/tmp/ghost_*"):
        git_config = os.path.join(d, ".git", "config")
        if os.path.isfile(git_config):
            try:
                with open(git_config, encoding="utf-8", errors="replace") as f:
                    for line in f:
                        if repo_url in line:
                            return d
            except OSError:
                pass
    return None


def _remove_clone_dir(clone_path: str) -> None:
    """Remove cloned repository directory from disk."""
    import shutil
    if not clone_path or not os.path.isdir(clone_path):
        return
    try:
        shutil.rmtree(clone_path, ignore_errors=True)
    except Exception:
        pass


def _get_container_networks(name: str) -> list[str]:
    """Return network names a container is connected to (excluding built-in ones)."""
    built_in = {"bridge", "host", "none", "ingress"}
    try:
        r = subprocess.run(
            ["docker", "inspect", "--format", "{{json .NetworkSettings.Networks}}", name],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0:
            return []
        import json
        networks = json.loads(r.stdout.strip())
        return [n for n in networks if n not in built_in]
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError, ValueError):
        return []


def _cleanup_networks(networks: list[str]) -> None:
    """Remove orphaned Docker networks (silently ignores errors)."""
    for net in networks:
        try:
            subprocess.run(
                ["docker", "network", "rm", net],
                capture_output=True, text=True, timeout=5,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass


def remove_container(name: str) -> str:
    """Force-remove a container by name, clean up its network, and delete cloned repo."""
    from ghostprovider.state import get_clone_path, _find_by_repo_url, unregister as _unregister_state

    networks = _get_container_networks(name)

    clone_path = get_container_label(name, "ghostprovider.clone_path")
    if not clone_path:
        clone_path = get_clone_path(name)
    if not clone_path:
        repo_url = get_container_label(name, "ghostprovider.repo") or ""
        if repo_url:
            clone_path = _find_by_repo_url(repo_url)
    if not clone_path:
        repo_url = get_container_label(name, "ghostprovider.repo") or ""
        if repo_url:
            clone_path = _find_clone_on_disk(repo_url)

    try:
        result = subprocess.run(
            ["docker", "rm", "-f", name],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            _unregister_state(name)
            if clone_path:
                _remove_clone_dir(clone_path)
            if networks:
                _cleanup_networks(networks)
            return f"Container '{name}' removed successfully"
        error = result.stderr.strip() or "unknown error"
        return f"Failed to remove '{name}': {error}"
    except subprocess.TimeoutExpired:
        return f"Timeout while removing container '{name}'"
    except FileNotFoundError:
        return "Docker is not available on this system"


def get_container_label(container_name: str, key: str) -> str | None:
    """Read a single Docker label from a container."""
    try:
        r = subprocess.run(
            ["docker", "inspect", "--format", "{{index .Config.Labels \"" + key + "\"}}", container_name],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            val = r.stdout.strip()
            if val and val != "<no value>":
                return val
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def restart_container(name: str) -> str:
    return _exec_docker_action("restart", name)
