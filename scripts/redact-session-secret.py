#!/usr/bin/env python3
"""Claude Code のセッション JSONL ログから、誤って記録された秘密文字列
（VNC パスワード等）をプレースホルダへ一括置換する。

安全設計:
  * 秘密の値はコマンドライン引数では受け取らない（履歴・ps・画面に残るため）。
    環境変数 REDACT_SECRETS か、対話入力（getpass / 画面非表示）で渡す。
  * 対話入力は既定で「1 つだけ」受け取って即終了（無限に聞かない）。
    複数を一度に消すときだけ --multi（空行で終了）。
  * 生の値と JSON エスケープ版の両方を置換対象にする。
  * --dry-run で件数のみ。書き換えはしない。
  * --inspect で、各ヒットを「直前ラベル」で 本物 / 誤爆候補 に自動分類し、
    周辺文脈を“秘密を伏字にしたまま”表示する（値は一切晒さない）。
      - 直前に「パスワード:」「Password:」等があるヒット → 本物（漏洩）
      - 直前が無関係な文章のヒット → 誤爆候補（消すと会話文が壊れる）
  * 置換が起きたファイルだけタイムスタンプ付き .bak を残す。
  * 置換後の各行が依然として有効な JSON であることを検証してから書き込む。

典型的な使い方:
    python3 scripts/redact-session-secret.py --dry-run --inspect   # 確認
    python3 scripts/redact-session-secret.py                       # 本実行
    python3 scripts/redact-session-secret.py --only-labeled        # 本物だけ消す
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_PLACEHOLDER = "[REDACTED]"

# ヒットの直前にこれらがあれば「パスワードのラベル付き＝本物の漏洩」と判定する。
LABEL_KEYWORDS = (
    "password", "passwd", "pass:", "pass ", "pwd", "pw:",
    "パスワード", "ぱすわーど", "認証", "credential", "secret",
    "vnc", "rdp", "login", "ログイン",
)
LABEL_WINDOW = 28  # ヒット直前の何文字を見てラベル判定するか


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for s in items:
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def load_secrets(multi: bool) -> list[str]:
    """秘密文字列を環境変数または対話入力から取得（引数からは取らない）。"""
    env = os.environ.get("REDACT_SECRETS")
    if env is not None and env != "":
        return _dedupe([s for s in env.split("\n") if s != ""])

    import getpass

    if not multi:
        s = getpass.getpass("秘密文字列（パスワード）を貼り付けて Enter> ")
        return _dedupe([s]) if s else []

    print("複数モード: 1 行 1 つ。空行で終了（画面非表示）。", file=sys.stderr)
    secrets: list[str] = []
    while True:
        try:
            s = getpass.getpass("secret> ")
        except (EOFError, KeyboardInterrupt):
            print("", file=sys.stderr)
            break
        if s == "":
            break
        secrets.append(s)
    return _dedupe(secrets)


def build_targets(secret: str) -> list[str]:
    """生の値と JSON エスケープ版（重複は除く）。"""
    targets = [secret]
    json_escaped = json.dumps(secret)[1:-1]
    if json_escaped != secret:
        targets.append(json_escaped)
    return targets


def find_matches(text: str, secrets: list[str], min_length: int) -> list[tuple[int, int]]:
    """全ヒット位置 (start, end) を集めて返す（位置順、重複除去）。"""
    positions: set[tuple[int, int]] = set()
    for secret in secrets:
        if len(secret) < min_length:
            continue
        for target in build_targets(secret):
            if not target:
                continue
            start = 0
            while True:
                idx = text.find(target, start)
                if idx < 0:
                    break
                positions.add((idx, idx + len(target)))
                start = idx + len(target)
    return sorted(positions)


def is_labeled(text: str, start: int) -> bool:
    """ヒット直前 LABEL_WINDOW 文字にパスワード系ラベルがあるか。"""
    before = text[max(0, start - LABEL_WINDOW):start].lower()
    return any(kw in before for kw in LABEL_KEYWORDS)


def scrub(snippet: str, secrets: list[str], placeholder: str) -> str:
    """文字列中の全秘密を伏字化し、改行を可視化する。"""
    for secret in secrets:
        for target in build_targets(secret):
            if target:
                snippet = snippet.replace(target, placeholder)
    return snippet.replace("\n", " ⏎ ").replace("\r", "")


def redact_text(
    text: str, secrets: list[str], placeholder: str, min_length: int, only_labeled: bool
) -> tuple[str, int, list[int]]:
    """置換後 text, 総数, 秘密ごとの件数。only_labeled なら本物（ラベル付き）だけ置換。"""
    matches = find_matches(text, secrets, min_length)
    if not matches:
        return text, 0, [0] * len(secrets)

    # どの位置を置換するか決める。
    targets_pos = [(s, e) for (s, e) in matches if (not only_labeled or is_labeled(text, s))]
    if not targets_pos:
        return text, 0, [0] * len(secrets)

    # 後ろから置換して位置ズレを防ぐ。
    chars = text
    for s, e in sorted(targets_pos, reverse=True):
        chars = chars[:s] + placeholder + chars[e:]

    # 秘密ごとの件数（元 text に対して数える）。
    per_secret = [0] * len(secrets)
    for i, secret in enumerate(secrets):
        if len(secret) < min_length:
            continue
        for target in build_targets(secret):
            if target:
                per_secret[i] += text.count(target)

    return chars, len(targets_pos), per_secret


def inspect_file(
    text: str, secrets: list[str], placeholder: str, min_length: int, max_hits: int = 4
) -> tuple[int, int, list[str]]:
    """(本物件数, 誤爆候補件数, 表示スニペット) を返す。"""
    matches = find_matches(text, secrets, min_length)
    labeled = bare = 0
    snippets: list[str] = []
    shown_bare = shown_labeled = 0
    for s, e in matches:
        lab = is_labeled(text, s)
        if lab:
            labeled += 1
        else:
            bare += 1
        # 本物・誤爆候補を最大 max_hits 件ずつ表示。
        if (lab and shown_labeled < max_hits) or ((not lab) and shown_bare < max_hits):
            a = max(0, s - 55)
            b = min(len(text), e + 25)
            tag = "本物 " if lab else "誤爆?"
            snip = scrub(text[a:b], secrets, placeholder)
            prefix = "…" if a > 0 else ""
            suffix = "…" if b < len(text) else ""
            snippets.append(f"[{tag}] {prefix}{snip}{suffix}")
            if lab:
                shown_labeled += 1
            else:
                shown_bare += 1
    return labeled, bare, snippets


def lines_still_valid_json(original: str, redacted: str) -> bool:
    old_lines = original.split("\n")
    new_lines = redacted.split("\n")
    if len(old_lines) != len(new_lines):
        return False
    for old, new in zip(old_lines, new_lines):
        if old == new:
            continue
        stripped = new.strip()
        if stripped == "":
            continue
        try:
            json.loads(stripped)
        except json.JSONDecodeError:
            try:
                json.loads(old.strip())
            except json.JSONDecodeError:
                continue
            return False
    return True


def process_file(
    path: Path, secrets: list[str], placeholder: str, min_length: int,
    dry_run: bool, backup: bool, inspect: bool, only_labeled: bool,
    timestamp: str, grand_per_secret: list[int], totals: dict[str, int],
) -> tuple[int, list[str]]:
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as exc:
        print(f"  ! 読み込みスキップ {path.name}: {exc}", file=sys.stderr)
        return 0, []

    if inspect and dry_run:
        labeled, bare, snippets = inspect_file(text, secrets, placeholder, min_length)
        totals["labeled"] += labeled
        totals["bare"] += bare
        return labeled + bare, snippets

    redacted, count, per_secret = redact_text(text, secrets, placeholder, min_length, only_labeled)
    if count == 0:
        return 0, []
    for i, n in enumerate(per_secret):
        grand_per_secret[i] += n

    if dry_run:
        return count, []

    if not lines_still_valid_json(text, redacted):
        print(f"  ! {path.name}: 置換すると JSON が壊れるため中止", file=sys.stderr)
        return 0, []

    if backup:
        bak = path.with_name(path.name + f".bak-{timestamp}")
        bak.write_bytes(path.read_bytes())
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(redacted)
    return count, []


def main() -> int:
    default_dir = Path.home() / ".claude" / "projects"
    p = argparse.ArgumentParser(description="Claude Code セッション JSONL から秘密を伏字化")
    p.add_argument("--dir", type=Path, default=default_dir, help=f"走査ディレクトリ（既定: {default_dir}）")
    p.add_argument("--placeholder", default=DEFAULT_PLACEHOLDER, help=f"置換後の文字列（既定: {DEFAULT_PLACEHOLDER}）")
    p.add_argument("--min-length", type=int, default=6, help="この長さ未満の秘密は無視（既定: 6）")
    p.add_argument("--dry-run", action="store_true", help="書き換えず件数だけ")
    p.add_argument("--inspect", action="store_true", help="本物/誤爆候補に分類し文脈を伏字表示（--dry-run 併用）")
    p.add_argument("--only-labeled", action="store_true", help="直前にパスワード系ラベルがある本物だけ置換（誤爆を避ける）")
    p.add_argument("--multi", action="store_true", help="対話で複数の秘密を受け取る（既定は 1 つ）")
    p.add_argument("--no-backup", action="store_true", help=".bak を作らない（既定は作る）")
    p.add_argument("--exclude", action="append", default=["blog"],
                   help="パスにこの文字列を含むファイルを除外（既定: blog。複数指定可）")
    args = p.parse_args()

    if "\n" in args.placeholder:
        print("placeholder に改行は含められません。", file=sys.stderr)
        return 2

    secrets = load_secrets(args.multi)
    if not secrets:
        print("秘密文字列が指定されませんでした。中止。", file=sys.stderr)
        return 1

    too_short = [s for s in secrets if len(s) < args.min_length]
    if too_short:
        print(f"注意: {len(too_short)} 個が {args.min_length} 文字未満のため無視されます。", file=sys.stderr)

    if not args.dir.exists():
        print(f"ディレクトリが存在しません: {args.dir}", file=sys.stderr)
        return 1
    files = sorted(
        f for f in args.dir.rglob("*.jsonl")
        if f.is_file() and not any(ex in str(f) for ex in args.exclude)
    )
    if not files:
        print(f"対象 .jsonl が見つかりません: {args.dir}", file=sys.stderr)
        return 1

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = not args.no_backup
    grand_per_secret = [0] * len(secrets)
    totals = {"labeled": 0, "bare": 0}

    mode = "DRY-RUN（書き換えなし）" if args.dry_run else "本実行"
    if args.only_labeled:
        mode += " / 本物（ラベル付き）のみ"
    print(f"== {mode} / 対象 {len(files)} ファイル / ルート {args.dir} ==", file=sys.stderr)

    grand_total = 0
    affected = 0
    for f in files:
        n, snippets = process_file(
            f, secrets, args.placeholder, args.min_length, args.dry_run,
            backup, args.inspect, args.only_labeled, timestamp, grand_per_secret, totals,
        )
        if n:
            affected += 1
            grand_total += n
            print(f"  {f.relative_to(args.dir)}: {n} 箇所")
            for sn in snippets:
                print(f"      | {sn}")

    print("", file=sys.stderr)
    if args.inspect and args.dry_run:
        print("=== 分類結果（パスワードの値は非表示）===", file=sys.stderr)
        print(f"  本物（直前にパスワード系ラベル）: {totals['labeled']} 箇所", file=sys.stderr)
        print(f"  誤爆候補（無関係な文脈）         : {totals['bare']} 箇所", file=sys.stderr)
        print(
            "\n→ 誤爆候補が 0 なら全消しで安全。--dry-run を外して本実行してください。"
            "\n→ 誤爆候補が多いなら、本物だけ消す --only-labeled を使ってください。",
            file=sys.stderr,
        )
    elif len(secrets) > 1:
        print("秘密ごとの内訳（値は非表示・長さのみ）:", file=sys.stderr)
        for i, s in enumerate(secrets):
            note = "  ← 無視（短すぎ）" if len(s) < args.min_length else ""
            print(f"  秘密#{i + 1}（{len(s)} 文字）: {grand_per_secret[i]} 箇所{note}", file=sys.stderr)

    print("", file=sys.stderr)
    if grand_total == 0:
        print("該当なし（既に伏字化済みか対象外）。", file=sys.stderr)
    elif args.dry_run:
        print(f"DRY-RUN: 合計 {grand_total} 箇所 / {affected} ファイル該当。", file=sys.stderr)
    else:
        note = "（.bak 付き）" if backup else "（バックアップなし）"
        print(f"完了: 合計 {grand_total} 箇所 / {affected} ファイルを伏字化 {note}。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
