# -*- coding: utf-8 -*-
"""🧪 Autorestart 的「等下載完成再重啟」邏輯測試（不會真的重啟）

測試重點：
  1. `_cleanup_orphaned_scratch()` 只刪「舊的、前綴符合的」暫存資料夾
  2. `_pending_downloads()` 能正確偵測 JM / nhentai 的下載狀態
  3. `_deferred_restart()` 的行為：
       • 下載中途完成 → 立刻重啟（不必等滿）
       • 下載一直沒完成 → 等滿上限後仍然重啟（不會永遠不重啟）
       • 狀態查詢壞掉 → 保守等待，但屆時仍要重啟
  4. `_deferred_restart_guarded()` 在例外時必須解除重啟鎖
     （否則之後所有重啟都會被「已經在重啟中」永久擋掉）

⚠️ 安全性：測試會把 `_do_restart` 換成假的（只記錄呼叫），
   **絕對不會**真的結束程序。所有被改寫的模組層級函式都會在 finally 還原。

用法：
    python tools/test_autorestart.py
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import time
from typing import List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "cogs"))
for _candidate in (
    os.path.join(_ROOT, ".local", "lib", "python3.11", "site-packages"),
    os.path.join(_ROOT, ".local", "lib", "python3.12", "site-packages"),
):
    if os.path.isdir(_candidate) and _candidate not in sys.path:
        sys.path.append(_candidate)

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

_passed = 0
_failed = 0


def _install_audioop_stub_if_needed() -> None:
    """讓測試能在 Python 3.13+ 的本機執行。

    discord.py 2.7 會 `import audioop`，而 `audioop` 在 Python 3.13 已被移除。
    正式主機用的是 Python 3.11（有 audioop），所以這只是本機測試的相容措施：
    只有在真的匯入不到時才塞一個空的 stub —— 本模組的測試完全不會用到音訊功能。
    """
    try:
        import audioop  # noqa: F401

        return
    except ImportError:
        pass
    import types as _types

    stub = _types.ModuleType("audioop")
    for name in ("error", "error_", "AudioopError"):
        setattr(stub, name, Exception)
    for name in ("mul", "ratecv", "tomono", "tostereo", "lin2lin"):
        setattr(stub, name, lambda *a, **k: b"")
    sys.modules.setdefault("audioop", stub)


def _load_autorestart():
    """載入 cogs/Autorestart.py。

    本機（Windows / Python 3.14）載入真正的 `config` 會失敗，因為
    `jmcomic` → `PIL` 是針對 Python 3.11 編譯的。這種情況下注入一個
    最小 stub（只需要 `current_dir`），讓測試專注在 Autorestart 的邏輯上。

    ⚠️ 主機（Python 3.11）會走真實 `config` 路徑，所以 `_pending_downloads()`
    會真的去讀 JM / nhentai 的下載狀態。
    """
    import importlib.util

    used_stub = False
    try:
        import config  # noqa: F401
    except Exception as error:
        print(f"  ℹ️ 本機無法載入真實 config（{type(error).__name__}: {error}）")
        print("     → 改注入最小 stub，下載狀態偵測會以假物件驗證")
        import types as _types

        stub = _types.ModuleType("config")
        stub.current_dir = _ROOT
        stub.option = None
        stub.dl_manager = None
        sys.modules["config"] = stub
        used_stub = True

    path = os.path.join(_ROOT, "cogs", "Autorestart.py")
    spec = importlib.util.spec_from_file_location("Autorestart", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["Autorestart"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module, used_stub


_install_audioop_stub_if_needed()


def check(label: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  ✅ {label}")
    else:
        _failed += 1
        print(f"  ❌ {label}{('  → ' + detail) if detail else ''}")


def section(title: str) -> None:
    print()
    print(f"── {title} ──")


class FakeChannel:
    """假的 Discord 頻道，只記錄送出的訊息。"""

    def __init__(self) -> None:
        self.sent: List[str] = []

    async def send(self, text: str) -> None:
        self.sent.append(text)


class FakeBot:
    """最小可用的假 bot：只提供重啟流程會用到的方法。"""

    def __init__(self) -> None:
        self.channel = FakeChannel()

    def get_channel(self, channel_id):
        return self.channel

    async def fetch_channel(self, channel_id):
        return self.channel


def make_cog(autorestart):
    """建立一個不跑 __init__ 的 cog 實例（避免真的去碰 discord 物件）。"""
    cog = autorestart.AutoRestartCog.__new__(autorestart.AutoRestartCog)
    cog.bot = FakeBot()
    cog._restarting = True          # 模擬「已取得重啟鎖」
    cog._deferring = False
    cog._pending_restart_task = None
    cog._last_defer_notify = 0.0
    cog._notify_channel_id = 123456789   # 模擬「之前重啟成功回報過的頻道」
    return cog


# ──────────────────────────────────────────────────────────────
def test_scratch_cleanup(autorestart) -> None:
    section("1. 開機清理：只刪舊的、前綴符合的暫存資料夾")

    # ⚠️ 測試用的資料夾刻意建在 workspace 內（而不是系統 Temp）：
    #    workspace 保證可寫，避免沙箱或權限設定讓測試因 PermissionError 失敗。
    root = os.path.join(_ROOT, "tools", "_scratch_test_tmp")
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root, exist_ok=True)
    old = time.time() - (autorestart.SCRATCH_MAX_AGE_SECONDS + 600)
    fresh = time.time()

    def make(name: str, mtime: float) -> str:
        path = os.path.join(root, name)
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, "dummy.jpg"), "w", encoding="utf-8") as handle:
            handle.write("x")
        os.utime(path, (mtime, mtime))
        return path

    targets = [
        make("jm_104601_abcdef", old),        # 該刪（中斷殘留）
        make("nhentai_2112_xyz", old),        # 該刪（中斷殘留）
        make("jm_999999_newone", fresh),      # 太新 → 保留（可能是別的行程在用）
        make("jm_notanumber_xx", old),        # 前綴不符 → 保留
        make("some_other_folder", old),       # 無關 → 保留
    ]

    original_root = autorestart.tempfile.gettempdir
    autorestart.tempfile.gettempdir = lambda: root
    try:
        removed = autorestart._cleanup_orphaned_scratch()
    finally:
        autorestart.tempfile.gettempdir = original_root

    # ⚠️ 斷言必須在清理測試目錄「之前」執行 —— 曾經把 rmtree 放在前面，
    #    結果所有「應該被保留」的檢查都因為檔案已被自己刪掉而失敗。
    check("回報刪除數量為 2", removed == 2, f"實際 {removed}")
    check("舊的 jm_ 資料夾被刪除", not os.path.exists(targets[0]))
    check("舊的 nhentai_ 資料夾被刪除", not os.path.exists(targets[1]))
    check("太新的資料夾被保留", os.path.exists(targets[2]))
    check("前綴不符的資料夾被保留", os.path.exists(targets[3]))
    check("無關資料夾被保留", os.path.exists(targets[4]))

    shutil.rmtree(root, ignore_errors=True)


def test_pending_downloads(autorestart, allow_missing_manager: bool = False) -> None:
    section("2. 下載狀態偵測")
    pending, error = autorestart._pending_downloads()
    print(f"     目前偵測結果：pending={pending}  error={error}")
    check("回傳清單型別", isinstance(pending, list), str(type(pending)))

    if allow_missing_manager:
        # 本機用 stub config 時，dl_manager 是 None → 預期會回報「無法讀取」，
        # 而且**必須被視為有下載**（保守），這是安全設計的關鍵。
        check("讀不到 JM 狀態時回報錯誤（而非靜默略過）", error is not None, str(error))
        check("錯誤訊息包含原因", bool(error and "無法讀取" in error), str(error))
    else:
        check("狀態可讀取、沒有錯誤", error is None, str(error))
        check("測試環境沒有下載在跑", pending == [], str(pending))


# ──────────────────────────────────────────────────────────────
async def test_deferred_restart(autorestart) -> None:
    section("3. 等待邏輯")

    original_poll = autorestart.DOWNLOAD_POLL_SECONDS
    original_grace = autorestart.DOWNLOAD_GRACE_SECONDS
    original_probe = autorestart._pending_downloads
    original_do = autorestart.AutoRestartCog._do_restart

    autorestart.DOWNLOAD_POLL_SECONDS = 0.15
    autorestart.DOWNLOAD_GRACE_SECONDS = 1.2

    call_log: List[str] = []

    async def fake_do_restart(self, reason: str, notify_channel_id=None):
        call_log.append(reason)

    try:
        # ── 情境 A：第一次仍在忙、第二次已完成 → 應立刻重啟（不等滿）──
        cog = make_cog(autorestart)
        sequence: List[Tuple[List[str], Optional[str]]] = [
            (["禁漫 `123`（測試）"], None),
            ([], None),
        ]
        calls = {"n": 0}

        def probe_a():
            calls["n"] += 1
            return sequence.pop(0) if sequence else ([], None)

        autorestart._pending_downloads = probe_a
        autorestart.AutoRestartCog._do_restart = fake_do_restart

        started = time.time()
        await cog._deferred_restart("測試A", ["禁漫 `123`（測試）"], None)
        elapsed_a = time.time() - started

        check("A：下載完成後有執行重啟", len(call_log) == 1, str(call_log))
        check("A：只輪詢 2 次（完成就停）", calls["n"] == 2, f"實際 {calls['n']}")
        check("A：沒有等滿上限", elapsed_a < 1.0, f"實際 {elapsed_a:.2f}s")
        check("A：有送出『已延後』通知",
              any("已延後" in t for t in cog.bot.channel.sent),
              str(cog.bot.channel.sent[:1]))

        # ── 情境 B：下載一直不完成 → 等滿上限後**仍必須**重啟 ──
        call_log.clear()
        cog_b = make_cog(autorestart)
        autorestart._pending_downloads = lambda: ([("禁漫 `999`（卡住）")], None)

        started = time.time()
        await cog_b._deferred_restart("測試B", ["禁漫 `999`（卡住）"], None)
        elapsed_b = time.time() - started

        check("B：逾時後仍然有重啟（不會永遠不重啟）", len(call_log) == 1, str(call_log))
        check("B：等待時間有到上限", elapsed_b >= 1.1, f"實際 {elapsed_b:.2f}s")
        check("B：有送出『等待逾時』警告",
              any("逾時" in t for t in cog_b.bot.channel.sent),
              str(cog_b.bot.channel.sent))

        # ── 情境 C：狀態查詢失敗 → 保守處理，但屆時仍要重啟 ──
        call_log.clear()
        cog_c = make_cog(autorestart)
        autorestart._pending_downloads = lambda: ([], None)
        await cog_c._deferred_restart("測試C", [], "無法讀取狀態")
        check("C：probe_error 下仍完成重啟", len(call_log) == 1, str(call_log))

    finally:
        autorestart.DOWNLOAD_POLL_SECONDS = original_poll
        autorestart.DOWNLOAD_GRACE_SECONDS = original_grace
        autorestart._pending_downloads = original_probe
        autorestart.AutoRestartCog._do_restart = original_do


async def test_guarded_unlocks(autorestart) -> None:
    section("4. 安全外殼：例外時必須解除重啟鎖")

    original = autorestart.AutoRestartCog._deferred_restart

    async def boom(self, reason, pending, probe_error):
        raise RuntimeError("模擬內部錯誤")

    autorestart.AutoRestartCog._deferred_restart = boom
    try:
        # 4-1 一般例外
        cog = make_cog(autorestart)
        await cog._deferred_restart_guarded("測試", [], None)
        check("發生例外後重啟鎖被解除", cog._restarting is False, str(cog._restarting))

        # 4-2 被取消（使用者手動 /restart 不想再等）
        async def hang(self, reason, pending, probe_error):
            await asyncio.sleep(60)

        autorestart.AutoRestartCog._deferred_restart = hang
        cog2 = make_cog(autorestart)
        task = asyncio.create_task(cog2._deferred_restart_guarded("測試", [], None))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        check("被取消後重啟鎖也被解除", cog2._restarting is False, str(cog2._restarting))
    finally:
        autorestart.AutoRestartCog._deferred_restart = original


async def test_deferring_flag(autorestart) -> None:
    section("5. `_deferring` 旗標（等待期間不該擋掉所有指令）")

    original = autorestart.AutoRestartCog._deferred_restart
    seen: List[bool] = []

    async def spy(self, reason, pending, probe_error):
        seen.append(self._deferring)   # 等待期間應為 True

    autorestart.AutoRestartCog._deferred_restart = spy
    try:
        cog = make_cog(autorestart)
        await cog._deferred_restart_guarded("測試", [], None)
        check("等待期間 _deferring 為 True", seen == [True], str(seen))
        check("結束後 _deferring 被清除", cog._deferring is False, str(cog._deferring))

        # 例外路徑也必須清除旗標，否則之後真正的重啟期間會失去指令攔截保護
        async def boom(self, reason, pending, probe_error):
            raise RuntimeError("模擬錯誤")

        autorestart.AutoRestartCog._deferred_restart = boom
        cog2 = make_cog(autorestart)
        await cog2._deferred_restart_guarded("測試", [], None)
        check("例外後 _deferring 也被清除", cog2._deferring is False, str(cog2._deferring))
    finally:
        autorestart.AutoRestartCog._deferred_restart = original


async def main() -> int:
    print("=" * 74)
    print("🧪 Autorestart「等下載完成再重啟」測試")
    print("=" * 74)

    _install_audioop_stub_if_needed()
    try:
        autorestart, used_stub = _load_autorestart()
    except Exception as error:
        print(f"❌ 無法載入 cogs/Autorestart.py：{type(error).__name__}: {error}")
        return 2

    if used_stub:
        print("  ⚠️ 本次以 stub config 執行（本機限制）；主機上會用真實 config")

    test_scratch_cleanup(autorestart)
    test_pending_downloads(autorestart, allow_missing_manager=used_stub)
    await test_deferred_restart(autorestart)
    await test_guarded_unlocks(autorestart)
    await test_deferring_flag(autorestart)

    print()
    print("=" * 74)
    print(f"📊 結果：✅ {_passed} 通過　❌ {_failed} 失敗")
    print("=" * 74)
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
