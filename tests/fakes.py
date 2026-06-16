"""fakes.py — handlers.py のインターフェースのフェイク実装（テスト共有用）。

実プロセス・Windows・デスクトップ無しで Handlers / Agent のロジックを検証する。
import しても何も実行しない（test_* と違い副作用なし）。
"""

from handlers import ProcessResult


class FakeRunner:
    def __init__(self):
        self.calls = []
        self.spawned = []
        self.next_result = ProcessResult(0, b"", b"")
        self.next_pid = 4321

    def run(self, argv, cwd, timeout, stdin_text):
        self.calls.append({"argv": argv, "cwd": cwd, "timeout": timeout, "stdin": stdin_text})
        return self.next_result

    def spawn(self, argv, cwd):
        self.spawned.append({"argv": argv, "cwd": cwd})
        return self.next_pid


class FakeClipboard:
    def __init__(self):
        self.value = "initial"

    def get(self):
        return self.value

    def set(self, text):
        self.value = text


class FakeScreenshotter:
    def __init__(self):
        self.captured = False
        self.png = b"\x89PNG\r\nFAKEPNGBYTES"

    def capture(self):
        self.captured = True
        return self.png


class FakeFS:
    def __init__(self):
        self.store = {}
        # walk が返す擬似ツリー: [(dirpath, [dirnames], [filenames]), ...]
        self.tree = []
        # stat が返す値: path -> (size, mtime)。未登録は (0, 0.0)。
        self.stats = {}

    def read_bytes(self, path):
        return self.store[path]

    def write_bytes(self, path, data):
        self.store[path] = data

    def exists(self, path):
        if path in self.store:
            return True
        # find_files の root チェック用に、ツリー上のディレクトリも存在扱いにする。
        return any(dirpath == path for dirpath, _, _ in self.tree)

    def walk(self, root):
        # os.walk と同じく (dirpath, dirnames, filenames) を順に。呼び側が破壊的に
        # dirnames を削っても次の yield に影響しないようコピーを渡す（実 os.walk の
        # 枝刈り最適化は再現しないが、find_files は深さで明示フィルタするので結果は同じ）。
        for dirpath, dirnames, filenames in self.tree:
            yield dirpath, list(dirnames), list(filenames)

    def stat(self, path):
        return self.stats.get(path, (0, 0.0))


class FakeKeyboard:
    def __init__(self):
        # 送られた打鍵を (modifiers, main) のタプルで記録する。
        self.sent = []

    def send_chord(self, modifiers, main):
        self.sent.append((list(modifiers), main))


class FakeEnv:
    def describe(self):
        return {"user": "testuser", "session_id": 2, "interactive": True, "platform": "win32"}


class FakeWindowManager:
    def __init__(self):
        # 列挙して返すウィンドウ一覧（テストが直接セットする）。
        self.windows = []
        # activate に渡された hwnd を記録する。
        self.activated = []
        # activate の戻り値（前面化に成功したか）。テストで切り替える。
        self.activate_result = True

    def list_windows(self, visible_only):
        return [dict(w) for w in self.windows]

    def activate(self, hwnd):
        self.activated.append(hwnd)
        return self.activate_result


class FakeIme:
    def __init__(self):
        # (open, conversion) のタプル。None にすると IME を持たない窓を模せる。
        self.state = (False, 0)
        # set に渡された (open, conversion) を順に記録する。
        self.sets = []

    def get(self):
        return self.state

    def set(self, open, conversion):
        if self.state is None:
            return False
        cur_open, cur_conv = self.state
        if open is not None:
            cur_open = open
        if conversion is not None:
            cur_conv = conversion
        self.state = (cur_open, cur_conv)
        self.sets.append((open, conversion))
        return True


class FakeMenuController:
    def __init__(self, tree=None, supported=True):
        # enumerate が返す生ツリー（テストが直接セットする）。
        self.tree = tree if tree is not None else []
        # False にするとメニューを持たないウィンドウ（enumerate→None）を模せる。
        self.supported = supported
        # invoke に渡された (hwnd, command_id) を順に記録する。
        self.invoked = []
        # invoke の戻り値（発火に成功したか）。テストで切り替える。
        self.invoke_result = True

    def enumerate(self, hwnd):
        return self.tree if self.supported else None

    def invoke(self, hwnd, command_id):
        self.invoked.append((hwnd, command_id))
        return self.invoke_result
