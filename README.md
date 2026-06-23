<div align="center">

# GHOST PROVIDER

**TUI for self-hosting & localhost management**

<br>

[![Python](https://img.shields.io/badge/Python-3.11+-ff9900?style=for-the-badge&logo=python&logoColor=white&labelColor=111)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-ff9900?style=for-the-badge&logo=open-source-initiative&logoColor=white&labelColor=111)](LICENSE)
[![AUR](https://img.shields.io/badge/AUR-ghostprovider-ff9900?style=for-the-badge&logo=arch-linux&logoColor=white&labelColor=111)](https://aur.archlinux.org)
[![Docker](https://img.shields.io/badge/Docker-ready-ff9900?style=for-the-badge&logo=docker&logoColor=white&labelColor=111)](https://docker.com)
[![Textual](https://img.shields.io/badge/Built%20with-Textual-ff9900?style=for-the-badge&logo=python&logoColor=white&labelColor=111)](https://textual.textualize.io)

<img src="https://img.shields.io/github/stars/iamnetuseroagent/ghostprovider?style=flat-square&color=ff9900&logo=github" alt="stars">
<img src="https://img.shields.io/github/last-commit/iamnetuseroagent/ghostprovider?style=flat-square&color=ff9900&label=updated" alt="last commit">

<br>
<br>

**GHOST PROVIDER** automates and simplifies working with localhost.  
Analyze your system, discover services, deploy GitHub repos as Docker containers, and manage everything from a beautiful terminal interface.

<br>

---

<br>

</div>

## Features

- **System Analysis** &mdash; Scan your environment for Docker, Git, Python, Node.js, network interfaces, listening ports, and running services.
- **Service Fingerprinting** &mdash; Detect 45+ self-hosted services (Jellyfin, SearXNG, Grafana, Home Assistant, Pi-hole, and more) by fingerprinting HTTP responses.
- **GitHub Repo Analysis** &mdash; Paste any GitHub URL. GhostProvider checks for Dockerfile, docker-compose, dependency files, scans source code for HTTP servers, and computes a host score (0&ndash;100).
- **One-Click Deploy** &mdash; Deploy compatible repos as Docker containers. Supports Python, Node.js, Go, Rust, static HTML, and Docker Compose projects with automatic port remapping.
- **AUR Package** &mdash; Native Arch Linux package available on AUR.
- **Container Management** &mdash; Start, stop, restart, and remove containers from the TUI.

<br>

## Installation

### AUR (Arch Linux)

```bash
yay -S ghostprovider
```

### Manual

```bash
git clone https://github.com/iamnetuseroagent/ghostprovider.git
cd ghostprovider
python -m venv .venv
source .venv/bin/activate
pip install -e .
./ghostprovider.sh
```

### Dependencies

- **Python** 3.11+
- **Docker** &mdash; for container deployment
- **Git** &mdash; for cloning repositories

<br>

## Usage

```bash
ghostprovider
```

### Main Menu

| Action | Description |
|---|---|
| **INITIALIZE SYSTEM SCAN** | Scan your local environment for tools, ports, and running services |
| **MANAGE ACTIVE SERVICES** | View, start, stop, restart, and remove running Docker containers |
| **Enter GitHub URL** | Paste a repo URL to analyze and deploy |

### Host Score

GhostProvider analyzes each GitHub repo and returns a score:

| Score | Meaning |
|---|---|
| 80&ndash;100 | High &mdash; ready to deploy |
| 50&ndash;79 | Medium &mdash; likely deployable |
| 20&ndash;49 | Low &mdash; may work, but not guaranteed |
| 0&ndash;19 | Unsuitable &mdash; CLI, library, or desktop app |

<br>

---

<br>

<div align="center">

**GHOST PROVIDER**

</div>
