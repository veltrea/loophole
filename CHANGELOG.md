# Changelog

**Read this in other languages:** [日本語](CHANGELOG.ja.md)

## [0.4.0]

### Added
- `loophole_window` gained `set`: move, resize, minimize/restore, fullscreen, maximize,
  and raise a single window — blind (no screenshot) and without dragging. Works on macOS,
  Windows, and Linux (X11); Windows has no OS fullscreen concept, and Wayland is unsupported.
  Verified end-to-end on real Windows and Linux (X11), including the move/resize geometry
  read-back and that a fullscreen request is never faked on Windows.
- `loophole_mouse` gained a `drag` action (press, move, release) for text selection,
  sliders, and drag-and-drop. Implemented on Windows, Linux, and macOS.
- `loophole_send_keys` gained a `text` mode (wire command `type_text`): type literal text
  character by character. It is an escape hatch for the cases where clipboard paste fails
  (paste-blocking fields like confirm-password / license-key, web forms that only react to
  real key events, terminals and games); clipboard paste stays the default, IME-safe way to
  enter text. Windows injects Unicode directly (KEYEVENTF_UNICODE, bypassing the keyboard
  layout and the IME, so Japanese types too); macOS injects Unicode directly; Linux sends
  real keycodes from the current layout (X11 XTEST / Wayland ydotool), so it is ASCII /
  layout-only — characters not on the layout (e.g. Japanese) are rejected with a hint to use
  clipboard paste. Verified live on Windows (Japanese round-trips byte-identical with the IME
  on) and Linux/X11 (ASCII byte-identical). The goal/scope is documented in docs/architecture.md.
- macOS menu support: `menu_enumerate` and `menu_invoke` now work on macOS as well
  (previously Windows/Linux only), so all three platforms can drive an app's menu bar.
- `hello` now reports, on macOS, the privacy-permission (TCC) state — Accessibility,
  Screen Recording, and Automation — and the display layout (each display's position
  and scale factor), so a client can tell up front what is permitted and how to read
  screen coordinates.

### Changed
- The macOS window backend was rewritten onto stable window identifiers (CGWindowID)
  and the Accessibility API. A window handle now stays valid across focus and z-order
  changes (no re-listing needed), and the fullscreen on→off round trip works. This
  path needs only the Accessibility permission, not Automation.

### Fixed
- macOS `activate_window` and `set_window` could fail to address the target window.

## [0.3.0]

### Added
- `loophole_window`: list and activate windows (blind, by title).
- Multi-machine support: drive several target machines at once. Register named targets
  in a connection registry (`~/.loophole/registry.json`) and pick one per project with
  `LOOPHOLE_TARGET`. Each target's local forward port is auto-assigned while every agent
  stays on 9999 (`LOOPHOLE_REMOTE_PORT` decouples the local and remote tunnel ports).
- Wire-protocol specification `docs/protocol.md`: the client↔agent protocol (transport,
  JSONL framing, message envelope, auth, and every command) in one page.
- Connect-time client/agent version negotiation: `hello` now returns `protocol_version`
  and the agent's command list, so the client detects an older agent at connect time and
  hides the tools that agent does not implement.
- New `loophole_reload` tool: after editing the local client code, reconnect with the
  latest source without reopening the window.

## [0.2.0]

### Added
- Linux target support: the same client now drives a Linux desktop, not just Windows.
  X11 is full-coverage — screenshot, key send, and window control via libX11/libXtst,
  with the clipboard owned in-process (no xclip/xsel). Wayland is partial — screenshot
  (grim), clipboard (wl-clipboard), key send (ydotool), window control (sway/Hyprland IPC
  only). Japanese IME control (fcitx5/ibus) and blind menu-bar enumerate/invoke (AT-SPI)
  work on both. Backends sit behind an OS-neutral dispatcher; the MCP tools and setup
  wizard are OS-neutral.
- `loophole_mouse`: move, click (left/middle/right, double), and scroll by absolute
  coordinates. Windows and Linux.
- Windows menu: a UI Automation fallback so `menu_*` works on modern apps with no classic
  menu bar (WPF/WinForms/UWP), including reading WinForms checked state.
- Initial macOS backend (clipboard/screenshot/keys/mouse/window/IME) as groundwork for a
  future macOS target.
- The README is now a bilingual pair (English + Japanese).

## [0.1.0]

### Added
- Initial release: drive a remote **Windows** desktop from your Mac's Claude Code over SSH.
  A small agent runs inside the logged-in desktop session, so GUI launches, screenshots, and
  the clipboard work despite the SSH "session 0" wall. The agent listens on loopback only and
  is reached through an SSH tunnel (auth delegated to SSH; no LAN port opened).
- Command execution: `run` (argv, no shell) and `shell` (one-liner), returning
  stdout/stderr/exit code with CP932/UTF-8 output decoding.
- `gui`: launch a GUI / long-running program in the interactive desktop (it appears on
  screen, unlike a plain SSH shell).
- `screenshot` of the target desktop.
- Clipboard get/set — a text round-trip that bypasses the IME (no garbling).
- File read/write and `find_files` (search by name).
- `send_keys`: keyboard shortcuts / chords (Ctrl+S, Win+R, ...).
- List windows and activate one by title.
- Japanese IME get/set (on/off and conversion mode).
- `menu`: enumerate and invoke a classic Win32 menu bar blind (no screenshot).
- Live view (opt-in, read-only): watch the target screen as an MJPEG stream, with the
  command history, in a browser.
- Chat-driven setup (`loophole_configure`): give an IP and a username; the SSH tunnel and
  config are handled for you — no need to return to a terminal.
