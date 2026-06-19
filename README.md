# loophole

**Read this in other languages:** [日本語](README.ja.md)

**A testing tool for cross-platform development.** When you build software on your Mac and want to
verify it on a remote **Windows or Linux** machine, loophole lets you test without sitting at the
other OS's physical machine. It has two parts: a **server that listens on the target PC (Windows or
Linux)**, and a **client called from Claude Code on your local machine**. When you automate testing of
the target's GUI apps with Claude's computer use, loophole **takes over all the work that doesn't need
pixel coordinates**.

It's not a replacement for computer use but a **companion** to it: it cuts the number of slow
"screenshot → image recognition → click-by-coordinate" round trips and lowers token usage.

> **Support (current):** local machine = Mac. Target = **Windows** and **Linux**.
> Linux is **fully supported on X11** (screenshot, key sending, and window control via direct
> libX11/libXtst ctypes; the clipboard is owned in-process, so no external tool is needed).
> **Wayland is partial** (screenshot via grim, clipboard via wl-clipboard, key sending via ydotool,
> window control via sway/Hyprland IPC; window control on GNOME/KDE is out of scope).
> IME (fcitx5/ibus) and menus (AT-SPI) work on both X11 and Wayland.

## What it can do

From your local machine (a Mac, etc.), against the target PC (Windows or Linux):

- **Launch apps / processes** (GUIs actually appear on screen)
- **Run commands** and get the result (stdout, stderr, exit code)
- **Take screenshots**
- **Pass / retrieve text via the clipboard** (bypasses the Japanese IME)
- **Read / write files**, and **search** for them by name
- **Send keyboard shortcuts** (modifier + key chords like `ctrl+s`, `win+r`)
- **List windows** and **bring one to the front** by title
- **Get / toggle the Japanese IME** on/off and conversion mode (Windows = IMM32, Linux = fcitx5/ibus)
- **Enumerate a menu bar without looking at the screen** and invoke an item (Windows = classic Win32
  menus, Linux = the AT-SPI accessibility tree)

You can also **watch** what's happening from a browser on your local machine (read-only, started only
with `--view-port`):

- **Peek at the target's screen live** (MJPEG stream)
- **See the history of executed commands** (newest first)

## Architecture

```
local machine (Claude Code + loophole client)  ──ssh -L tunnel──▶  target PC (server/agent.py resident)
```

The server (`server/agent.py`) listens only on the target's `127.0.0.1` and is reachable from your
local machine only through an SSH tunnel (no port opened to the LAN; authentication is left to SSH).
**This is why you start the server on the target PC before using it.** For the tunnel itself, you ask
Claude once to "set up loophole" and answer the destination a single time; after that the MCP client
opens the tunnel automatically on startup, so no manual step is needed each time
([client-setup.md](docs/client-setup.md)).

## Install

Install on **both** the **local machine (a Mac, etc.)** you operate from and the **target PC** you
operate. The steps are in separate manuals:

- **① Target PC (server side)**
  - Windows — install OpenSSH, Python, and loophole, and keep it resident on the desktop → [docs/windows-setup.md](docs/windows-setup.md)
  - Linux — install OpenSSH, Python, and the per-capability packages, and keep it resident → [docs/linux-setup.md](docs/linux-setup.md)
- **② Local machine (client side)** — install with `uv` and run the interactive setup once (it asks for
  the destination and handles both the config and registering with Claude) → [docs/client-setup.md](docs/client-setup.md)

## Usage

The main use is **testing GUI apps on the remote target PC** from Claude Code. The typical flow is:
**place and launch** the thing under test (your own `.exe` / executable, etc.) → **run commands and
actions** → **check state with a screenshot** → **collect output and logs**. Of these, only the part
where you look at the screen and click by coordinate is left to computer use; loophole does the rest at
roughly the cost of an SSH command. The result is fewer computer-use round trips (screenshot → image
recognition → click), which lowers both latency and token usage.

## Documentation

**Setup & operation**

| What you want | Document |
|---|---|
| Install (target Windows, server side) | [windows-setup.md](docs/windows-setup.md) |
| Install (target Linux, server side) | [linux-setup.md](docs/linux-setup.md) |
| Install (local machine, client side) | [client-setup.md](docs/client-setup.md) |
| Setting up the OpenSSH server | [windows-openssh-server.md](docs/windows-openssh-server.md) |
| Auto-start the server at logon (Task Scheduler) | [agent-autostart.md](docs/agent-autostart.md) |
| Deploying for another user / headless operation (advanced) | [operator-runbook.md](docs/operator-runbook.md) |
| Uninstall | [uninstall.md](docs/uninstall.md) |

**How it works & development**

| What you want | Document |
|---|---|
| How it works / design (why SSH-resident / the session 0 problem / Linux support) | [architecture.md](docs/architecture.md) |
| Live view (watch the screen during operation, read-only) | [architecture.md](docs/architecture.md) |
| The CLI (`loophole-cli`) and all its commands | [cli.md](docs/cli.md) |
| The screenshot backend (ddagrab / VNC & RDP notes) | [vnc-for-computer-use-testing.md](docs/vnc-for-computer-use-testing.md) |
| Modification & testing policy | [dev-notes.md](docs/dev-notes.md) |

## Security

`run` / `shell` / `gui` are equivalent to arbitrary code execution. They assume a **local test machine
only**, with the reach path limited to the inside of SSH (loopback + port forwarding). See
[architecture.md](docs/architecture.md) for details.
