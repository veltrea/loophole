"""registry.py — 複数ターゲット（接続先）のレジストリ。手元機の ~/.loophole/registry.json。

各ターゲットに「手元の転送ポート（local_port）」を 1 つずつ固定割当する。対象 agent は 9999 の
まま（remote_port 既定 9999）で、手元のどのポートがどのホストの 9999 に転送されるかだけが違う。
保存は JSON（stdlib のみ・無依存）。書き込みは atomic（tmp + rename）。

これは純粋なデータ層: socket も ssh も触らない。すべての関数は path 引数でファイルを差し替えられる
ので、本物の ~/.loophole/registry.json を汚さずに単体テストできる（tests/test_registry.py）。

レジストリ構造:
    {
      "default_target": "winpc" | null,
      "targets": {
        "winpc":  {"ssh": "user@host", "local_port": 9999,  "remote_port": 9999, ...},
        "linux1": {"ssh": "user@host", "local_port": 10000, "remote_port": 9999, "ssh_opts": "..."}
      }
    }
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Dict, Optional, Set

REGISTRY_PATH = os.path.expanduser(
    os.environ.get("LOOPHOLE_REGISTRY", "~/.loophole/registry.json"))
DEFAULT_REMOTE_PORT = 9999   # 対象 agent の既定待受ポート
FIRST_PORT = 9999            # 最初のターゲットが取る手元ポート（＝従来の既定）
NEXT_PORT_BASE = 10000       # 2 個目以降の自動採番の起点


def load(path: str = REGISTRY_PATH) -> Dict[str, Any]:
    """レジストリを読む。無い/壊れていれば空のレジストリを返す。"""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("default_target", None)
    data.setdefault("targets", {})
    return data


def save(reg: Dict[str, Any], path: str = REGISTRY_PATH) -> None:
    """atomic 書き込み（tmp + rename）。親ディレクトリは作る。"""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".registry-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(reg, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def used_ports(reg: Dict[str, Any]) -> Set[int]:
    """登録済みの local_port / view_port を全部集める（採番の衝突回避用）。"""
    ports: Set[int] = set()
    for t in reg.get("targets", {}).values():
        for k in ("local_port", "view_port"):
            v = t.get(k)
            if isinstance(v, int) and not isinstance(v, bool):
                ports.add(v)
    return ports


def allocate_local_port(reg: Dict[str, Any]) -> int:
    """空きの手元ポートを返す。1 個目は FIRST_PORT(9999)、以降は NEXT_PORT_BASE(10000) から空きを。"""
    if not reg.get("targets"):
        return FIRST_PORT
    used = used_ports(reg)
    p = NEXT_PORT_BASE
    while p in used:
        p += 1
    return p


def get_target(reg: Dict[str, Any], name: Optional[str]) -> Optional[Dict[str, Any]]:
    """name（None なら default_target）でターゲットを引く。無ければ None。

    返り値には name を足し、remote_port を既定で補完する（呼び元が素直に使えるように）。
    """
    if name is None:
        name = reg.get("default_target")
    if not name:
        return None
    t = reg.get("targets", {}).get(name)
    if not isinstance(t, dict):
        return None
    out = dict(t)
    out["name"] = name
    out.setdefault("remote_port", DEFAULT_REMOTE_PORT)
    return out


def add_target(reg: Dict[str, Any], name: str, ssh: str, *,
               ssh_key: str = "", ssh_opts: str = "",
               local_port: Optional[int] = None,
               remote_port: int = DEFAULT_REMOTE_PORT,
               make_default: Optional[bool] = None) -> Dict[str, Any]:
    """ターゲットを追加/更新して reg を返す（保存は呼び元の責務）。

    local_port 未指定なら既存値を保つか自動採番する（一度割り当てたポートは不変）。
    最初のターゲット、または make_default=True なら default_target にする。
    """
    if not name:
        raise ValueError("target name is required")
    if not ssh:
        raise ValueError("ssh (user@host) is required")
    targets = reg.setdefault("targets", {})
    existing = targets.get(name) or {}
    if local_port is None:
        local_port = existing.get("local_port") or allocate_local_port(reg)
    entry: Dict[str, Any] = {
        "ssh": ssh,
        "local_port": local_port,
        "remote_port": remote_port,
    }
    if ssh_key:
        entry["ssh_key"] = ssh_key
    if ssh_opts:
        entry["ssh_opts"] = ssh_opts
    if existing.get("view_port"):
        entry["view_port"] = existing["view_port"]  # ライブビュー設定は保つ
    targets[name] = entry
    if make_default or reg.get("default_target") is None:
        reg["default_target"] = name
    return reg
