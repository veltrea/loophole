#!/bin/bash
# mac-install-agent.sh — テスト Mac に loophole エージェントを LaunchAgent としてインストール。
#
# 何をするか:
#   1. loophole の server/ ディレクトリを ~/loophole-agent/ にコピー
#   2. ~/Library/LaunchAgents/com.loophole.agent.plist を生成
#   3. launchctl bootstrap gui/$UID で登録（次回ログインから自動起動）
#   4. launchctl kickstart で即起動
#
# なぜ LaunchAgent か:
#   SSH ターミナルから直接 agent を起動すると、TCC（Accessibility / Screen Recording）が
#   sshd セッション側を許可候補にして混乱する。LaunchAgent (gui/$UID ドメイン) 経由なら
#   Aqua セッション内で動くので、TCC のダイアログが期待通りに出る。
#
# 使い方:
#   ./scripts/mac-install-agent.sh             # デフォルトの ~/loophole-agent/ にインストール
#   ./scripts/mac-install-agent.sh /custom/path
#
#   アンインストール:
#   ./scripts/mac-install-agent.sh --uninstall
#
# 失敗時の典型:
#   - python3 が PATH に無い → Homebrew Python が PATH に入っていない（plist で PATH を明示するので
#     plist 生成時の python3 の場所だけ確実なら OK）。
#   - TCC 拒否 → 初回起動後に Screen Recording / Accessibility を System Settings で許可し、
#     `launchctl kickstart -k gui/$UID/com.loophole.agent` で再起動する。

set -euo pipefail

readonly LABEL="com.loophole.agent"
readonly PLIST_PATH="$HOME/Library/LaunchAgents/${LABEL}.plist"
readonly LOG_DIR="$HOME/Library/Logs"
readonly STDOUT_LOG="${LOG_DIR}/loophole-agent.out"
readonly STDERR_LOG="${LOG_DIR}/loophole-agent.log"

PORT="${LOOPHOLE_PORT:-9999}"
VIEW_PORT="${LOOPHOLE_VIEW_PORT:-9998}"


die() {
    echo "error: $*" >&2
    exit 1
}


find_python3() {
    # Homebrew (Apple Silicon) > Homebrew (Intel) > system > PATH の順に探す。
    local candidates=(
        "/opt/homebrew/bin/python3"
        "/usr/local/bin/python3"
        "/usr/bin/python3"
    )
    for p in "${candidates[@]}"; do
        if [[ -x "$p" ]]; then
            echo "$p"
            return
        fi
    done
    if command -v python3 >/dev/null 2>&1; then
        command -v python3
        return
    fi
    die "python3 not found (tried /opt/homebrew, /usr/local, /usr/bin, PATH)"
}


uninstall() {
    if [[ -f "$PLIST_PATH" ]]; then
        echo "stopping LaunchAgent..."
        launchctl bootout "gui/$UID/${LABEL}" 2>/dev/null || true
        echo "removing plist: $PLIST_PATH"
        rm -f "$PLIST_PATH"
    fi
    echo "uninstalled. (agent files in your install dir are kept; remove them manually if desired)"
}


install_agent() {
    local install_dir="${1:-$HOME/loophole-agent}"

    # 1) 入力検証 ---------------------------------------------------------
    local repo_root
    repo_root="$(cd "$(dirname "$0")/.." && pwd)"
    if [[ ! -d "${repo_root}/server" ]]; then
        die "could not find server/ next to scripts/ (looked in ${repo_root})"
    fi

    local py
    py="$(find_python3)"
    echo "python3: $py"

    # 2) ファイル配置 ----------------------------------------------------
    mkdir -p "$install_dir"
    echo "syncing server/ -> $install_dir/server/"
    # rsync は macOS 既定で入っている。--delete で旧ファイルを掃除する。
    rsync -a --delete --exclude='__pycache__' \
        "${repo_root}/server/" "${install_dir}/server/"

    # 3) ログディレクトリ
    mkdir -p "$LOG_DIR"

    # 4) plist 生成 -------------------------------------------------------
    mkdir -p "$(dirname "$PLIST_PATH")"
    echo "writing plist: $PLIST_PATH"
    cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>

  <key>ProgramArguments</key>
  <array>
    <string>${py}</string>
    <string>${install_dir}/server/agent.py</string>
    <string>--port</string>
    <string>${PORT}</string>
    <string>--view-port</string>
    <string>${VIEW_PORT}</string>
  </array>

  <key>WorkingDirectory</key>
  <string>${install_dir}</string>

  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>

  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    <key>LC_ALL</key>
    <string>en_US.UTF-8</string>
    <key>LANG</key>
    <string>en_US.UTF-8</string>
  </dict>

  <key>StandardOutPath</key>
  <string>${STDOUT_LOG}</string>
  <key>StandardErrorPath</key>
  <string>${STDERR_LOG}</string>

  <key>ProcessType</key>
  <string>Interactive</string>
</dict>
</plist>
PLIST

    # 5) 既存登録を一旦解除してから bootstrap -------------------------------
    echo "registering with launchd (gui/$UID)..."
    launchctl bootout "gui/$UID/${LABEL}" 2>/dev/null || true
    launchctl bootstrap "gui/$UID" "$PLIST_PATH"
    launchctl kickstart -k "gui/$UID/${LABEL}"

    echo ""
    echo "installed. agent should be running now."
    echo "  log:    $STDERR_LOG"
    echo "  stdout: $STDOUT_LOG"
    echo ""
    echo "verify:"
    echo "  launchctl print gui/$UID/${LABEL} | grep -E 'state|last exit'"
    echo "  nc -zv 127.0.0.1 ${PORT}"
    echo ""
    echo "first run: macOS will ask for Screen Recording / Accessibility / Automation"
    echo "permissions on first use. Approve them, then:"
    echo "  launchctl kickstart -k gui/$UID/${LABEL}"
    echo ""
}


case "${1:-}" in
    --uninstall|-u)
        uninstall
        ;;
    --help|-h)
        sed -n '2,30p' "$0" | sed 's/^# \?//'
        ;;
    *)
        install_agent "${1:-}"
        ;;
esac
