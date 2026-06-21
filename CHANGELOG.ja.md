# 変更履歴

**他の言語で読む:** [English](CHANGELOG.md)

## [0.4.0]

### Added
- `loophole_window` に `set` を追加: 特定のウィンドウ 1 枚を移動・リサイズ・最小化/復元・
  フルスクリーン・最大化・前面化。スクリーンショットもドラッグも要らない。macOS / Windows /
  Linux(X11) で利用可（Windows は OS にフルスクリーンの概念が無いため非対応。Wayland も非対応）。
  Windows・Linux(X11) の実機で end-to-end 検証済み（移動・リサイズ後のジオメトリ読み戻し、
  および Windows でフルスクリーン要求を決して捏造しないことを含む）。
- `loophole_mouse` に `drag` アクション（押す→動かす→離す）を追加。テキスト選択・スライダー・
  ドラッグ&ドロップに使える。Windows / Linux / macOS で実装。
- `loophole_send_keys` に `text` モード（ワイヤーコマンド `type_text`）を追加: 文字列を 1 文字ずつ
  そのまま打ち込む。これは文字入力の本線ではなく、クリップボード貼り付けが効かない場面のための
  逃げ道（貼り付けを弾く入力欄＝確認用パスワード/ライセンスキー、キー入力にしか反応しない Web
  フォーム、ターミナルやゲーム）。文字入力の既定は今もクリップボード貼り付け（全 OS で IME を
  通さず確実）。Windows は Unicode を直接注入（KEYEVENTF_UNICODE。キーボード配列も IME も通さない
  ので日本語も打てる）、macOS も Unicode 直接、Linux は現レイアウトの実キーコードを送る
  （X11=XTEST / Wayland=ydotool）ため ASCII・配列にある文字専用——配列に無い文字（日本語等）は
  クリップボード貼り付けを促すエラーで弾く。Windows（IME ON のまま日本語がバイト一致で往復）と
  Linux/X11（ASCII バイト一致）の実機で検証済み。設計の目標と範囲は docs/architecture.md に明文化。
- macOS のメニュー対応: `menu_enumerate` と `menu_invoke` が macOS でも動くようになった
  （従来は Windows/Linux のみ）。3 プラットフォームすべてでアプリのメニューバーを操作できる。
- `hello` が macOS で、プライバシー許可（TCC）の状態 — アクセシビリティ・画面収録・オートメーション —
  と、ディスプレイ配置（各ディスプレイの位置とスケール係数）を返すようになった。
  クライアントが「何が許可されているか」「画面座標をどう解釈するか」を呼ぶ前に把握できる。

### Changed
- macOS のウィンドウ操作の実装を、安定したウィンドウ識別子（CGWindowID）と macOS のアクセシビリティ
  API へ作り替えた。ウィンドウのハンドルがフォーカスや重なり順（z-order）の変化をまたいでも有効な
  ままになり（再列挙が不要）、フルスクリーンのオン↔オフ往復も通るようになった。この方式は
  アクセシビリティ権限だけでよく、オートメーション権限は不要。

### Fixed
- macOS の `activate_window` と `set_window` が対象ウィンドウを指定できずに失敗することがあった不具合を修正。

## [0.3.0]

### Added
- `loophole_window`: ウィンドウの一覧（list）と、タイトル指定での前面化（activate）。
- マルチマシン対応: 複数の検証マシンを同時に操作できる。接続先レジストリ
  （`~/.loophole/registry.json`）に名前付きターゲットを登録し、プロジェクトごとに
  `LOOPHOLE_TARGET` で選ぶだけ。手元の転送ポートは自動採番、対象 agent は 9999 のまま
  （`LOOPHOLE_REMOTE_PORT` でローカルとリモートのトンネルポートを分離）。
- ワイヤープロトコル仕様書 `docs/protocol.md`: クライアント↔agent の通信仕様
  （トランスポート・JSONL フレーミング・メッセージ封筒・認証・全コマンド）を一枚にまとめた。
- 接続時の client/agent バージョンネゴシエーション: `hello` が `protocol_version` と
  対応コマンド一覧を返し、クライアントが古い agent を接続時に検知して、その agent が実装して
  いないツールを自動で隠す。
- 新ツール `loophole_reload`: ローカルの client コードを編集した後、ウィンドウを開き直さずに
  最新ソースで再接続する。

## [0.2.0]

### Added
- Linux 対象に対応。同じクライアントで Windows だけでなく Linux のデスクトップも操作できる。
  X11 はフル対応——スクショ・キー送出・ウィンドウ操作を libX11/libXtst で直接、クリップボードは
  プロセス内でセレクション所有（xclip/xsel 不要）。Wayland は一部——スクショ（grim）・
  クリップボード（wl-clipboard）・キー送出（ydotool）・ウィンドウ操作（sway/Hyprland の IPC のみ）。
  日本語 IME 制御（fcitx5/ibus）と画面を見ないメニュー列挙・実行（AT-SPI）は X11/Wayland 両対応。
  バックエンドを OS 非依存ディスパッチャで分離し、MCP ツールとセットアップも OS 中立化。
- `loophole_mouse`: 絶対座標でポインタの移動・クリック（左/中/右・ダブル）・スクロール。Windows と Linux。
- Windows メニュー: クラシックなメニューバーを持たないモダンアプリ（WPF/WinForms/UWP）でも `menu_*`
  が効く UI Automation フォールバックを追加（WinForms の checked 状態の読み取りを含む）。
- macOS バックエンドの初期実装（clipboard/screenshot/keys/mouse/window/IME）を将来の macOS 対象対応の土台として追加。
- README を英日のペアにした。

## [0.1.0]

### Added
- 初回リリース。手元 Mac の Claude Code から、リモートの **Windows** デスクトップを SSH 越しに
  操作する。ログイン中のデスクトップセッション内に小さな agent を常駐させることで、SSH の
  「session 0 の壁」を越えて GUI 起動・スクリーンショット・クリップボードが効く。agent は
  ループバックのみで待ち受け、SSH トンネル経由で届く（認証は SSH に委譲・LAN にポートを開かない）。
- コマンド実行: `run`（argv・シェル無し）と `shell`（ワンライナー）。stdout/stderr/終了コードを返し、
  出力は CP932/UTF-8 を復号。
- `gui`: GUI／常駐プログラムを対話デスクトップで起動（素の SSH シェルと違い、実際に画面に出る）。
- 対象デスクトップの `screenshot`。
- クリップボード get/set——IME を通さない文字往復（化けない）。
- ファイル読み書きと `find_files`（名前で検索）。
- `send_keys`: キーボードショートカット（Ctrl+S・Win+R …）。
- ウィンドウの一覧と、タイトル指定での前面化。
- 日本語 IME の get/set（ON/OFF・変換モード）。
- `menu`: クラシックな Win32 メニューバーを画面を見ずに列挙・実行。
- ライブビュー（任意・read-only）: 対象画面を MJPEG ストリームで覗き、コマンド履歴をブラウザで見る。
- チャットからのセットアップ（`loophole_configure`）: IP とユーザー名を渡すだけで、SSH トンネルと
  設定が自動で整う——ターミナルに戻る必要がない。
