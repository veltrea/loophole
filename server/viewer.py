"""viewer.py — 操作対象の画面をブラウザで確認するための read-only ライブビューア。

loophole が対話デスクトップを操作している最中、その画面を人間がブラウザで眺め、
コマンドが実際に画面へ反映されているかを確認するための仕組み。**入力経路は一切
持たない**（read-only）ので VNC/RDP のリモート操作とは別物——「エージェントが今
なにをしているか」を覗く窓に徹する。

仕組みは古典的な MJPEG（multipart/x-mixed-replace）。ブラウザの 1 個の <img> が
ストリーム中の各フレームで自動更新される。JS もクライアントアプリも要らない。

  ブラウザ ──ssh -L <port> ──▶ loophole の viewer
                                 └ screenshotter.capture() を ~fps で multipart 配信

設計方針:
  - **opt-in**。agent.py の --view-port を付けたときだけ起動し、付けなければゼロ負荷。
  - **loopback 限定**。既存の SSH 相乗り（新規ポートを LAN に開けない）の境界を保つ。
  - **見ている人がいる時だけ撮る**。capture は /stream の接続中だけ回り、誰も見て
    いなければ一切走らない。
  - **DI 純粋**。Screenshotter（handlers.Screenshotter Protocol; capture()->bytes）を
    注入で受け取るので、Mac 上でフェイクを差して単体テストできる（tests/test_viewer.py）。

スクリーンショットのバックエンド（BitBlt + GDI を ctypes 直叩き）はプロセス内で
完結するため、PowerShell 版のような毎フレームのプロセス起動コストが無い。既定の
~2fps は画面確認用途に十分で、もっと滑らかにしたくなったら永続キャプチャに差し
替えるだけ——このモジュール側（capture() を呼ぶだけ）は無変更でよい。
"""

from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Iterator, Optional

from handlers import Screenshotter

# multipart の境界文字列。フレームごとにこの行で区切る。
BOUNDARY = "loopholeframe"

# 分割ダッシュボード（/）。左に画面ライブビュー、右にコマンド履歴を縦に並べる。
# 履歴は「hide log」ボタン or L キーでワンクリック非表示にでき、隠すと画面が全幅に
# なる。状態は localStorage に残す（次回も維持）。左は /stream、右は /log.json を
# JS が ~1.5s ごとに取得して描く。値は esc() で HTML エスケープする。
INDEX_HTML = ("""<!doctype html><html><head><meta charset="utf-8">
<title>loophole</title><style>
html,body{margin:0;height:100%;background:#111;color:#ddd;overflow:hidden;
 font:13px/1.4 ui-monospace,Menlo,Consolas,"Hiragino Sans","Yu Gothic UI",Meiryo,"MS Gothic",monospace}
#top{display:flex;align-items:center;justify-content:space-between;
 padding:6px 10px;background:#1b1b1b;border-bottom:1px solid #333}
#top .ttl{color:#9ad}#top .s{color:#8a8a8a;margin-left:8px}
button{font:inherit;color:#9ad;background:#000;border:1px solid #333;
 border-radius:5px;padding:3px 9px;cursor:pointer}
button:hover{background:#222}a.full{color:#6cf;text-decoration:none;margin-left:10px}
#wrap{display:flex;height:calc(100vh - 33px)}
#screen{flex:1.55;display:flex;background:#0d0d0d;border-right:1px solid #333;min-width:0}
#screen img{width:100%;height:100%;object-fit:contain}
#log{flex:1;min-width:0;display:flex;flex-direction:column;overflow:hidden}
#log h3{margin:0;padding:6px 10px;font-size:11px;font-weight:500;color:#9ad;
 border-bottom:1px solid #222;background:#161616}
#rows{overflow:auto}
.e{padding:7px 10px;border-bottom:1px solid #1e1e1e}
.e .r1{display:flex;justify-content:space-between}
.e .r2{margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.t{color:#8a8a8a}.via{color:#6cf}.cmd{color:#dca}.tg{color:#bbb}.ok{color:#7c7}.err{color:#f77}
body.nolog #log{display:none}body.nolog #screen{border-right:none}
</style></head><body>
<div id="top">
 <span><span class="ttl">loophole</span><span class="s" id="st">● live</span></span>
 <span><button id="tog">hide log</button><a class="full" href="/log">/log ↗</a></span>
</div>
<div id="wrap">
 <div id="screen"><img src="/stream" alt="live screen"></div>
 <div id="log"><h3>command log · newest first</h3><div id="rows"></div></div>
</div>
<script>
var HK='loophole_log_hidden';
function apply(){var h=localStorage.getItem(HK)==='1';
 document.body.classList.toggle('nolog',h);
 document.getElementById('tog').textContent=h?'show log':'hide log';}
function toggle(){localStorage.setItem(HK,localStorage.getItem(HK)==='1'?'0':'1');apply();}
document.getElementById('tog').onclick=toggle;
addEventListener('keydown',function(e){if(e.key==='l'||e.key==='L')toggle();});
function esc(s){return String(s).replace(/[&<>]/g,function(c){
 return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c];});}
async function tick(){try{
 var d=await(await fetch('/log.json')).json();
 document.getElementById('st').innerHTML=
  '<span style="color:#7c7">●</span> live · '+d.entries.length+' cmds';
 var r=document.getElementById('rows');r.innerHTML='';
 for(var i=d.entries.length-1;i>=0;i--){var e=d.entries[i];
  var div=document.createElement('div');div.className='e';
  div.innerHTML='<div class="r1"><span><span class="t">'+esc(e.time.slice(11))+
   '</span> <span class="via">'+esc(e.via)+'</span></span>'+
   '<span class="'+(e.ok?'ok':'err')+'">'+(e.ok?'ok':'err')+'</span></div>'+
   '<div class="r2"><span class="cmd">'+esc(e.cmd)+'</span> '+
   '<span class="tg">'+esc(e.target)+'</span></div>';
  r.appendChild(div);}
}catch(_){}}
apply();tick();setInterval(tick,1500);
</script></body></html>""").encode("utf-8")

# コマンド履歴ページ。/log.json を ~1.5s ごとに取得して表を作り直す（新しい順）。
# 値は esc() で HTML エスケープしてから挿入する（ログ経由のインジェクション防止）。
LOG_HTML = (
    "<!doctype html><html><head><meta charset=\"utf-8\">"
    "<title>loophole command log</title><style>"
    "body{margin:0;background:#111;color:#ddd;"
    "font:13px/1.5 ui-monospace,Menlo,Consolas,"
    "'Hiragino Sans','Yu Gothic UI',Meiryo,'MS Gothic',monospace}"
    "header{padding:8px 12px;background:#1b1b1b;border-bottom:1px solid #333}"
    "a{color:#6cf;text-decoration:none}"
    "table{width:100%;border-collapse:collapse}"
    "th,td{padding:4px 10px;text-align:left;border-bottom:1px solid #222;vertical-align:top}"
    "th{position:sticky;top:0;background:#1b1b1b;color:#9ad}"
    "td.t{white-space:nowrap;color:#888}td.via{white-space:nowrap;color:#6cf}"
    "td.cmd{white-space:nowrap;color:#dca}.err{color:#f77}.ok{color:#7c7}"
    "</style></head><body>"
    "<header>loophole command log &nbsp;·&nbsp; <a href=\"/\">↩ live view</a>"
    " &nbsp;·&nbsp; <span id=\"n\">0</span> entries</header>"
    "<table><thead><tr><th>time</th><th>via</th><th>cmd</th>"
    "<th>target</th><th></th></tr></thead><tbody id=\"b\"></tbody></table>"
    "<script>"
    "function esc(s){return String(s).replace(/[&<>]/g,"
    "c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}"
    "async function tick(){try{"
    "const d=await(await fetch('/log.json')).json();"
    "document.getElementById('n').textContent=d.entries.length;"
    "const b=document.getElementById('b');b.innerHTML='';"
    "for(const e of d.entries.slice().reverse()){"
    "const tr=document.createElement('tr');"
    "tr.innerHTML=`<td class=\"t\">${esc(e.time)}</td>`+"
    "`<td class=\"via\">${esc(e.via)}</td>`+"
    "`<td class=\"cmd\">${esc(e.cmd)}</td>`+"
    "`<td>${esc(e.target)}</td>`+"
    "`<td class=\"${e.ok?'ok':'err'}\">${e.ok?'ok':'err'}</td>`;"
    "b.appendChild(tr);}}catch(_){}}"
    "tick();setInterval(tick,1500);"
    "</script></body></html>"
).encode("utf-8")


def encode_frame(png: bytes, boundary: str = BOUNDARY) -> bytes:
    """PNG 1 枚を multipart/x-mixed-replace の 1 パートにする（純粋関数）。

    `--<boundary>\r\nContent-Type: image/png\r\nContent-Length: N\r\n\r\n<png>\r\n`
    PNG をそのまま流す（JPEG 再エンコード不要＝追加依存ゼロ）。ブラウザは各パートの
    Content-Type を見るので image/png でも問題なく差し替え表示する。
    """
    head = (
        f"--{boundary}\r\n"
        f"Content-Type: image/png\r\n"
        f"Content-Length: {len(png)}\r\n\r\n"
    ).encode("ascii")
    return head + png + b"\r\n"


def iter_frames(screenshotter: Screenshotter, boundary: str = BOUNDARY,
                interval: float = 0.5, *, sleep: Callable[[float], None] = time.sleep,
                max_frames: Optional[int] = None) -> Iterator[bytes]:
    """capture → encode を繰り返し、multipart パートを 1 つずつ yield する。

    sleep を注入できるので、テストでは実時間を待たずに回せる。max_frames で停止条件を
    与えられる（本番は None ＝接続が切れるまで無限）。capture が失敗したらストリームを
    静かに終了する（テスト未起動などで撮れない状況でハングさせない）。
    """
    count = 0
    while True:
        try:
            png = screenshotter.capture()
        except Exception:
            return
        yield encode_frame(png, boundary)
        count += 1
        if max_frames is not None and count >= max_frames:
            return
        sleep(interval)


class _ViewHandler(BaseHTTPRequestHandler):
    # server に積んだ screenshotter / interval を使う（serve_view で設定）。

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler の API 名)
        if self.path in ("/", "/index.html"):
            self._serve_html(INDEX_HTML)
        elif self.path == "/stream":
            self._serve_stream()
        elif self.path == "/log":
            self._serve_html(LOG_HTML)
        elif self.path == "/log.json":
            self._serve_log_json()
        else:
            self.send_error(404, "not found")

    def _serve_html(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_log_json(self) -> None:
        history = getattr(self.server, "history", None)
        entries = history.as_display() if history is not None else []
        body = json.dumps({"entries": entries}, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_stream(self) -> None:
        self.send_response(200)
        self.send_header(
            "Content-Type", f"multipart/x-mixed-replace; boundary={BOUNDARY}")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        screenshotter = self.server.screenshotter  # type: ignore[attr-defined]
        interval = self.server.interval            # type: ignore[attr-defined]
        try:
            for part in iter_frames(screenshotter, BOUNDARY, interval):
                self.wfile.write(part)
        except (BrokenPipeError, ConnectionResetError, OSError):
            # 視聴者がタブを閉じた = 正常終了。capture ループもここで止まる。
            pass

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        # 既定の stderr アクセスログは煩いので黙らせる。
        pass


class _ViewServer(ThreadingHTTPServer):
    daemon_threads = True       # プロセス終了でスレッドを道連れにする
    allow_reuse_address = True

    def __init__(self, addr, screenshotter: Screenshotter, interval: float, history=None):
        super().__init__(addr, _ViewHandler)
        self.screenshotter = screenshotter
        self.interval = interval
        self.history = history  # /log.json が読む実行履歴（None 可）


def make_server(screenshotter: Screenshotter, host: str = "127.0.0.1",
                port: int = 9998, fps: float = 2.0, history=None) -> _ViewServer:
    """ビューア HTTP サーバーを生成して返す（serve_forever は呼び側で）。

    テストではこれで生成し、別スレッドで serve_forever して GET を投げて検証する。
    """
    interval = (1.0 / fps) if fps and fps > 0 else 0.5
    return _ViewServer((host, port), screenshotter, interval, history)


def serve_view(screenshotter: Screenshotter, host: str = "127.0.0.1",
               port: int = 9998, fps: float = 2.0, history=None) -> None:
    """ビューアを起動して走らせ続ける（agent.py からデーモンスレッドで呼ぶ）。"""
    make_server(screenshotter, host, port, fps, history).serve_forever()
