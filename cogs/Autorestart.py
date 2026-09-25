# -*- coding: utf-8 -*-
"""
自動重啟系統 Cog (autorestart.py) — v2 重新設計
-----------------------------------------------
功能：
  🕓 定時重啟：每天固定時間（預設台灣時間 04:00）自動重啟一次。
  🛠️ /restart  [僅限開發者] 手動立即重啟。

v2 改動重點：
  1. 【強制結束保險】重啟一開始就啟動一條「非 daemon 的看門狗執行緒」，
     時間到就直接 os._exit(1)（或 exec）。不再依賴 asyncio 任務活到最後一刻：
     - bot.close() 會呼叫 cog_unload -> scheduled_restart.cancel()，
       在「定時重啟」路徑上會把正在執行重啟的那個任務自己取消掉，
       導致後面的 os._exit 永遠跑不到（程序 exit 0，翼龍不會拉起來）。
     - asyncio.run() 收尾時也會取消所有殘留任務，同樣會吃掉 os._exit。
  2. 【重啟鎖】一旦開始重啟，其他所有斜線指令都會收到「重啟中」的友善提示，
     不會再在關閉途中炸出 [指令崩潰]，也避免連按 /restart 重複觸發。
  3. 【CommandNotFound 友善處理】指令找不到時不再當成「指令崩潰」，
     開機/重啟期間會回覆「啟動中請稍後」，並在 log 標明是否為啟動期間。
  4. 【開機保護】剛開機 60 秒內拒絕再次重啟，避免重啟迴圈。
  5. 移除「連續失敗計數」：舊版 _do_restart 內部已吞掉所有例外並直接 os._exit，
     計數邏輯實際上永遠不會被觸發，屬於死程式碼。

重啟機制：
  預設關閉 Discord 連線後以非 0 狀態碼結束，交給翼龍／Docker 的 restart policy。
  只有本機無 process manager 時，才設定 BOT_RESTART_STRATEGY=exec 使用原地 execv。

⚠️ 建議搭配：主程式請在 setup_hook() 載入 cogs（不要在 on_ready 內載入），
   否則開機初期指令樹是空的，這段時間收到的指令一律會 CommandNotFound。
"""
import os
import sys
import json
import time
import glob
import shutil
import tempfile
import asyncio
import logging
import threading
import datetime
import traceback
from typing import List, Optional, Set, Tuple

import discord
from discord.ext import commands, tasks
from discord import app_commands

from config import current_dir
from logger_config import log

BOOT_TIME = time.monotonic()
print(f"[autorestart.py] 模組已載入 (PID={os.getpid()})", flush=True)

# ─────────────────────────────────────────────
# ⚙️ 設定
# ─────────────────────────────────────────────
RESTART_HOUR = 4      # 24 小時制
RESTART_MINUTE = 0
RESTART_TZ = datetime.timezone(datetime.timedelta(hours=8))  # 台灣時間 UTC+8
RESTART_STRATEGY = os.getenv("BOT_RESTART_STRATEGY", "supervisor").strip().lower()
RESTART_EXIT_CODE = 1

CLOSE_TIMEOUT_SECONDS = 6     # 等 bot.close() 的最長時間
RESTART_GRACE_SECONDS = 1     # close 完成後多等一下再結束
WATCHDOG_SECONDS = 8          # 無論如何，開始重啟後這麼久一定強制結束
MIN_UPTIME_SECONDS = 120      # 開機未滿這個秒數，不接受重啟（翼龍 60 秒內連續當機不會自動拉起，留足餘裕）
STARTUP_HINT_SECONDS = 120    # 開機這段時間內的 CommandNotFound 視為「啟動中」

# ── 🆕 下載保護：定時重啟遇到有人在download時，先等他們完成 ──────────────
# 為什麼需要：`_do_restart()` 最後會用看門狗在 WATCHDOG_SECONDS（8 秒）
# 內強制 os._exit()。若 04:00 當下有下載在跑，那個下載**一定**被打斷。
# 而且不只使用者白等 —— 下載中的暫存資料夾因為是被強殺，`finally` 的
# 清理不會執行，會在 VPS 上留下垃圾（1GB 硬碟會被慢慢吃光）。
DOWNLOAD_GRACE_SECONDS = int(os.getenv("RESTART_DOWNLOAD_GRACE_SECONDS", str(2 * 3600)))
DOWNLOAD_POLL_SECONDS = 30        # 檢查間隔
DOWNLOAD_NOTIFY_INTERVAL = 900    # 每隔這麼久回報一次「還在等」的狀態（15 分鐘）
SCRATCH_MAX_AGE_SECONDS = 6 * 3600   # 開機清理：只刪超過這麼舊的暫存資料夾
SCRATCH_MIN_FREE_CHECK = True        # 清理前先確認路徑真的是暫存區

RESTART_FLAG_PATH = os.path.join(current_dir, "restart_flag.json")


def _uptime() -> float:
    return time.monotonic() - BOOT_TIME


# ─────────────────────────────────────────────
# 🔎 有沒有下載在跑？
# ─────────────────────────────────────────────
def _pending_downloads() -> Tuple[List[str], Optional[str]]:
    """回傳 (正在下載／排隊中的項目描述, 無法判斷的錯誤訊息)。

    • 第一個值非空 → 有下載在進行，重啟應該延後
    • 第二個值非 None → 檢查失敗（例如模組還沒載入或狀態異常）。
      ⚠️ 這種情況**一律當成「有下載」處理**（保守），因為「誤判成沒下載」
      會直接砍掉使用者正在下載的本子，代價遠高於多等一輪。
    """
    pending: List[str] = []
    error: Optional[str] = None

    # ── 禁漫（JM）──
    try:
        from config import dl_manager

        snap = dl_manager.snapshot()
        for task in snap.get("running", []):
            album = getattr(task, "album_id", "?")
            title = (getattr(task, "title", "") or "解析中")[:40]
            user = getattr(task, "user_name", "?")
            pending.append(f"禁漫 `{album}`（{title}｜{user}）")
        waiting = snap.get("waiting", [])
        if waiting:
            pending.append(f"另有 {len(waiting)} 個禁漫任務在排隊")
    except Exception as e:
        error = f"無法讀取禁漫下載狀態：{type(e).__name__}: {e}"

    # ── nhentai（NH）──
    try:
        from cogs.nhentai import DOWNLOAD_REGISTRY

        count = DOWNLOAD_REGISTRY.active_count()
        if count:
            ids = sorted(DOWNLOAD_REGISTRY.active_ids())[:5]
            listed = "、".join(f"`{i}`" for i in ids)
            extra = f"（＋{count - len(ids)}）" if count > len(ids) else ""
            pending.append(f"nhentai {count} 個作品（{listed}{extra}）")
    except Exception as e:
        error = f"無法讀取 nhentai 下載狀態：{type(e).__name__}: {e}"

    return pending, error


# ─────────────────────────────────────────────
# 🧹 開機清理：強殺造成的暫存垃圾
# ─────────────────────────────────────────────
def _cleanup_orphaned_scratch() -> int:
    """刪除上次程序被強制結束時留下的下載暫存資料夾，回傳刪除數量。

    背景：`comic.py` 用 `tempfile.mkdtemp(prefix=f"jm_{album_id}_")`、
    `nhentai.py` 用 `tempfile.mkdtemp(prefix=f"nhentai_{book_id}_")` 建暫存區，
    正常路徑下 `finally` 會 `shutil.rmtree()` 清掉。但只要程序是被
    `os._exit()` 強殺（自動重啟、OOM、面板強制停止…），`finally` 就不會執行，
    整個資料夾（可能上百 MB 的圖片）就永久留在 /tmp。

    安全設計（避免誤刪）：
      • 只掃系統暫存目錄，而且會用 `tempfile.gettempdir()` 動態取得
      • 只刪前綴完全符合 `jm_<數字>_` / `nhentai_<數字>_` 的**目錄**
      • 只刪「最後修改時間」超過 SCRATCH_MAX_AGE_SECONDS 的項目
        → 即使同一台機器同時跑了別份程式（不同 PID），也幾乎不可能誤刪它正在用的
      • 單一項目的刪除失敗只記錄、不中斷啟動
    """
    removed = 0
    try:
        scratch_root = tempfile.gettempdir()
    except Exception as e:
        log.warning(f"⚠️ [自動重啟] 無法取得暫存目錄，略過清理：{e!r}")
        return 0

    if SCRATCH_MIN_FREE_CHECK and (not scratch_root or not os.path.isdir(scratch_root)):
        return 0

    now = time.time()
    patterns = ("jm_[0-9]*_*", "nhentai_[0-9]*_*")

    for pattern in patterns:
        try:
            matches = glob.glob(os.path.join(scratch_root, pattern))
        except Exception:
            continue
        for path in matches:
            try:
                if not os.path.isdir(path):
                    continue
                # 年齡保護：太新的可能是別的行程正在使用
                if (now - os.path.getmtime(path)) < SCRATCH_MAX_AGE_SECONDS:
                    continue
                shutil.rmtree(path, ignore_errors=True)
                if not os.path.exists(path):
                    removed += 1
                    log.info(f"🧹 [自動重啟] 已清除中斷殘留的暫存資料夾：{path}")
            except Exception as e:
                log.warning(f"⚠️ [自動重啟] 清除暫存資料夾失敗 {path}: {e!r}")

    return removed


def _cancel_pending_restart_task(task: Optional[asyncio.Task]) -> None:
    """取消「等待下載完成後才重啟」的背景任務（0 個 await，不會有競態）。

    `DLManager.snapshot()` 內部用的是 `threading.Lock`、`DownloadRegistry`
    讀的是 `set`，兩者都不需要 await，所以這裡是安全的同步取消。
    """
    if task is not None and not task.done():
        task.cancel()


# ─────────────────────────────────────────────
# 🔚 結束程序（不依賴 asyncio）
# ─────────────────────────────────────────────
def _terminate_process(flush: bool = True):
    """立即結束（或 exec 取代）目前程序。可在任何執行緒呼叫。

    ⚠️ 刻意不呼叫 logging.shutdown()：若 logger_config 有自訂 handler 在
    關閉時卡住，程序會既沒離線也沒重開，而且看門狗也會跟著卡死。
    """
    if flush:
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass

    if RESTART_STRATEGY == "exec":
        try:
            os.execv(sys.executable, [sys.executable] + sys.argv)
        except Exception:
            pass  # exec 失敗就退回 _exit，讓外部管理器接手
    os._exit(RESTART_EXIT_CODE)


def _start_watchdog(delay: float):
    """啟動非 daemon 看門狗：主執行緒結束了它也會讓程序撐到時間到再強制結束。"""
    def _run():
        time.sleep(delay)
        print("[自動重啟] ⑤ 看門狗觸發（前面某一步卡住了），強制結束程序。", flush=True)
        _terminate_process(flush=False)

    threading.Thread(target=_run, name="restart-watchdog", daemon=False).start()


# ─────────────────────────────────────────────
# 📝 重啟標記檔
# ─────────────────────────────────────────────
def _save_restart_flag(channel_id: Optional[int], reason: str):
    """重啟前寫入標記檔（不管有沒有頻道都寫，這樣定時重啟也能在 log 留下紀錄）"""
    try:
        with open(RESTART_FLAG_PATH, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "channel_id": channel_id,
                    "reason": reason,
                    "time": datetime.datetime.now(RESTART_TZ).isoformat(),
                },
                f, ensure_ascii=False,
            )
    except Exception as e:
        log.warning(f"⚠️ [自動重啟] 寫入重啟標記檔失敗: {e}")


def _load_and_clear_restart_flag() -> Optional[dict]:
    """開機時讀取並刪除標記檔，避免下次開機重複回報"""
    if not os.path.exists(RESTART_FLAG_PATH):
        return None
    try:
        with open(RESTART_FLAG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        os.remove(RESTART_FLAG_PATH)
        return data
    except Exception as e:
        log.warning(f"⚠️ [自動重啟] 讀取重啟標記檔失敗: {e}")
        return None


class AutoRestartCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._restarting = False
        # 🆕 是否正處於「等待下載完成」的階段。
        # ⚠️ 這個旗標非常重要：等待期間 `_restarting` 已經是 True（重啟鎖已取得），
        #    但機器人**其實完全正常運作中**（下載還在跑、指令都還能用）。
        #    如果照舊用 `_restarting` 去擋指令，使用者會在長達數小時的等待期間
        #    一直被回「機器人正在重啟中」，等於整個機器人變半殘。
        #    所以等待期間只擋 `/restart` 本身，其餘指令一律放行。
        self._deferring = False
        self._orig_on_error = None
        self._orig_interaction_check = None
        self._report_task: Optional[asyncio.Task] = None
        # 🆕 「等所有下載完成後才重啟」的背景任務（定時重啟專用）
        self._pending_restart_task: Optional[asyncio.Task] = None
        self._last_defer_notify: float = 0.0
        # 🆕 上次成功回報過的頻道（延後通知用）。
        # ⚠️ 不能只靠讀 restart_flag.json —— 那個檔是 `_do_restart()` 才寫入的，
        #    而延後通知發生在「決定重啟之前」，那時檔案還不存在（或還是上一次的舊資料），
        #    結果通知永遠送不出去。這裡記在記憶體，開機時再從標記檔補回來。
        self._notify_channel_id: Optional[int] = None

    # ── 生命週期 ────────────────────────────────────────────────
    async def cog_load(self):
        self._install_tree_hooks()

        # 🆕 開機先清掉上次被強殺留下的下載暫存資料夾（不然會慢慢吃掉硬碟）
        try:
            removed = _cleanup_orphaned_scratch()
            if removed:
                log.info(f"🧹 [自動重啟] 開機清理完成，共移除 {removed} 個中斷殘留的暫存資料夾")
            else:
                print("[自動重啟] 開機清理：沒有發現殘留的暫存資料夾", flush=True)
        except Exception:
            log.warning(f"⚠️ [自動重啟] 開機清理暫存失敗（不影響啟動）：\n{traceback.format_exc()}")

        flag = _load_and_clear_restart_flag()
        print(f"[自動重啟] 開機檢查：{'找到重啟標記 → ' + str(flag.get('reason')) if flag else '沒有重啟標記（冷開機）'}", flush=True)
        if flag:
            # 記住上次用的頻道，之後「定時重啟延後」的通知才有地方送
            try:
                channel_id = flag.get("channel_id")
                self._notify_channel_id = int(channel_id) if channel_id else None
            except (TypeError, ValueError):
                self._notify_channel_id = None
            # 開背景任務等 ready 再回報，不阻塞 cog 載入
            self._report_task = asyncio.create_task(self._report_restart_result(flag))

        self.scheduled_restart.start()

    def cog_unload(self):
        # ⚠️ 根因修復：定時重啟是在 scheduled_restart 這個任務「裡面」呼叫 bot.close()，
        # 而 bot.close() 會卸載本 cog -> 這裡若 cancel() 就等於自己取消自己，
        # bot.close() 被腰斬在「指令已卸載、Discord 連線還沒關」的殭屍狀態，
        # 之後的結束程序邏輯也跑不到。重啟期間絕對不能取消這個任務。
        if not self._restarting:
            self.scheduled_restart.cancel()
        # 🆕 收掉「等待下載完成」的背景任務（重啟中不需要，程序馬上要結束了）
        _cancel_pending_restart_task(self._pending_restart_task)
        self._remove_tree_hooks()

    # ── 指令樹掛鉤：重啟中攔截 + CommandNotFound 友善處理 ─────────
    def _install_tree_hooks(self):
        tree = self.bot.tree
        self._orig_on_error = tree.on_error
        self._orig_interaction_check = tree.interaction_check
        tree.on_error = self._tree_on_error
        tree.interaction_check = self._tree_interaction_check

    def _remove_tree_hooks(self):
        if self._restarting:
            return  # 重啟關閉途中保留攔截，直到程序結束
        tree = self.bot.tree
        if tree.on_error == self._tree_on_error and self._orig_on_error:
            tree.on_error = self._orig_on_error
        if tree.interaction_check == self._tree_interaction_check and self._orig_interaction_check:
            tree.interaction_check = self._orig_interaction_check

    async def _tree_interaction_check(self, interaction: discord.Interaction) -> bool:
        if self._restarting:
            # 🆕 等待下載完成的階段：機器人其實是健康的，只有 /restart 該被擋。
            # 其餘指令照常放行，否則使用者會在數小時的等待期間以為機器人掛了。
            if self._deferring:
                is_restart_cmd = getattr(interaction.command, "name", None) == "restart"
                if not is_restart_cmd:
                    return await self._orig_interaction_check(interaction)
                try:
                    await interaction.response.send_message(
                        "⏳ 已排定「等目前的下載完成後」自動重啟，不需要再手動重啟。\n"
                        "（若你要**立刻**重啟、不等下載，請使用 `/restart force:True`）",
                        ephemeral=True,
                    )
                except Exception:
                    pass
                return False

            try:
                await interaction.response.send_message(
                    "🔄 機器人正在重啟中，請約 10~30 秒後再試（請勿重複操作）。", ephemeral=True
                )
            except Exception:
                pass
            return False
        return await self._orig_interaction_check(interaction)

    async def _tree_on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CommandNotFound):
            await self._handle_command_not_found(interaction, error)
            return
        await self._orig_on_error(interaction, error)

    async def _handle_command_not_found(self, interaction: discord.Interaction, error: app_commands.CommandNotFound):
        booting = self._restarting or _uptime() < STARTUP_HINT_SECONDS
        user = interaction.user
        if booting:
            log.warning(
                f"⏳ [指令未載入] /{error.name} | 使用者: {user}({user.id}) | "
                f"已開機 {int(_uptime())} 秒（啟動/重啟期間，屬正常現象）"
            )
            text = "⏳ 機器人剛啟動，指令還在載入中，請等 10~30 秒後再試一次。"
        else:
            log.error(
                f"❓ [指令不存在] /{error.name} | 使用者: {user}({user.id}) | 已開機 {int(_uptime())} 秒 | "
                "非啟動期間仍找不到：請檢查該 cog 是否載入失敗、或 Discord 上殘留舊的指令（需要 tree.sync）"
            )
            text = "❓ 這個指令目前無法使用（可能已被移除或尚未載入完成），請稍後再試。"
        try:
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)
        except Exception:
            pass

    # ── 開機回報 ────────────────────────────────────────────────
    async def _report_restart_result(self, flag: dict):
        try:
            await self.bot.wait_until_ready()

            channel_id = flag.get("channel_id")
            if channel_id:
                channel = self.bot.get_channel(channel_id)
                if channel is None:
                    try:
                        channel = await self.bot.fetch_channel(channel_id)
                    except Exception:
                        channel = None

                if channel is None:
                    log.warning(f"⚠️ [自動重啟] 找不到要回報的頻道 {channel_id}（沒權限或已刪除），略過頻道通知")
                if channel:
                    # 記住這個頻道，之後「定時重啟延後」的通知才知道要送哪裡
                    self._notify_channel_id = channel_id
                    elapsed = ""
                    try:
                        requested_at = datetime.datetime.fromisoformat(flag["time"])
                        seconds = max(0, round((datetime.datetime.now(RESTART_TZ) - requested_at).total_seconds()))
                        elapsed = f"（耗時約 {seconds} 秒）"
                    except (KeyError, TypeError, ValueError):
                        pass
                    await channel.send(
                        f"✅ 機器人已成功重新啟動完畢{elapsed}！（原因：{flag.get('reason', '未知')}）"
                    )
            log.info(f"🔄 [自動重啟] 重啟完成，原因：{flag.get('reason', '未知')}")
        except Exception:
            log.warning(f"⚠️ [自動重啟] 回報重啟結果失敗：\n{traceback.format_exc()}")

    # ── 核心：實際執行重啟 ──────────────────────────────────────
    def _acquire(self) -> bool:
        """同步上鎖（中間沒有 await，不會有競態）"""
        if self._restarting:
            return False
        self._restarting = True
        return True

    async def _do_restart(self, reason: str, notify_channel_id: Optional[int] = None):
        """呼叫前必須先 _acquire() 成功。這個函式最後一定會結束程序。"""
        print(f"[自動重啟] ① 開始重啟，原因：{reason}（策略={RESTART_STRATEGY}）", flush=True)
        log.info(f"🔄 [自動重啟] 即將重啟，原因：{reason}")
        # 記住這次用的頻道，下次「定時重啟延後」才有地方可以通知
        if notify_channel_id:
            self._notify_channel_id = notify_channel_id
        _save_restart_flag(notify_channel_id, reason)

        # 先上保險：就算下面任何一步被取消、卡住、丟例外，時間到程序一定會結束
        _start_watchdog(WATCHDOG_SECONDS)
        print(f"[自動重啟] ② 看門狗已啟動（{WATCHDOG_SECONDS} 秒後強制結束），開始關閉 Discord 連線...", flush=True)

        try:
            try:
                await asyncio.wait_for(self.bot.close(), timeout=CLOSE_TIMEOUT_SECONDS)
            except Exception as e:
                log.warning(f"⚠️ [自動重啟] 關閉 Discord 連線時發生問題（繼續重啟）: {type(e).__name__}: {e}")

            print(f"[自動重啟] 連線已處理完畢，{RESTART_GRACE_SECONDS} 秒後結束程序...", flush=True)
            await asyncio.sleep(RESTART_GRACE_SECONDS)
        except BaseException:
            # 含 CancelledError：被取消也沒關係，直接結束
            log.error(f"☠️ [自動重啟] 重啟流程被中斷，直接強制結束：\n{traceback.format_exc()}")

        print(f"[自動重啟] ④ 呼叫結束程序（exit code={RESTART_EXIT_CODE}），若之後沒有新程序啟動，代表翼龍沒有自動拉起", flush=True)
        _terminate_process()

    # ── 🕓 每日定時重啟 ────────────────────────────────────────
    @tasks.loop(time=datetime.time(hour=RESTART_HOUR, minute=RESTART_MINUTE, tzinfo=RESTART_TZ))
    async def scheduled_restart(self):
        try:
            if _uptime() < MIN_UPTIME_SECONDS:
                log.warning(f"⚠️ [自動重啟] 開機才 {int(_uptime())} 秒，跳過這次定時重啟以避免重啟迴圈")
                return
            if not self._acquire():
                return

            # 🆕 先看有沒有下載在跑。有的話把「真正重啟」丟到背景任務去等，
            # 讓這個定時任務立刻結束（它不該被長時間佔住）。
            reason = f"每日定時重啟（{RESTART_HOUR:02d}:{RESTART_MINUTE:02d} 台灣時間）"
            pending, probe_error = _pending_downloads()
            if pending or probe_error:
                self._pending_restart_task = asyncio.create_task(
                    self._deferred_restart_guarded(reason, pending, probe_error)
                )
                return

            await self._do_restart(reason=reason)
        except Exception:
            # 讓迴圈本身永遠不會因為例外而死掉
            self._restarting = False
            log.error(f"☠️ [自動重啟] 定時重啟發生例外，明天會再嘗試：\n{traceback.format_exc()}")

    async def _deferred_restart_guarded(self, reason: str, pending: List[str],
                                        probe_error: Optional[str]) -> None:
        """`_deferred_restart()` 的安全外殼。

        兩個必須處理的邊界：
          1. 被取消（例如使用者手動 /restart 不想再等）→ 必須把 `_restarting`
             解鎖，否則之後所有重啟都會被「已經在重啟中」永久擋掉。
          2. 發生未預期例外 → 同上解鎖，並記錄下來，不能讓這個背景任務
             默默死掉而 bot 從此不再定時重啟。
        """
        try:
            self._deferring = True
            await self._deferred_restart(reason, pending, probe_error)
        except asyncio.CancelledError:
            self._restarting = False
            log.warning("⚠️ [自動重啟] 等待下載的延後重啟已被取消，重啟鎖已解除")
            raise
        except Exception:
            self._restarting = False
            log.error(f"☠️ [自動重啟] 延後重啟發生例外，重啟鎖已解除：\n{traceback.format_exc()}")
        finally:
            # 一定要收掉這個旗標：它決定 `_tree_interaction_check` 要不要擋指令。
            # 留在 True 的話，之後真正的重啟期間反而擋不住指令（失去原本的保護）。
            self._deferring = False

    @scheduled_restart.before_loop
    async def before_scheduled_restart(self):
        await self.bot.wait_until_ready()

    @scheduled_restart.error
    async def scheduled_restart_error(self, error: BaseException):
        # 最後防線（正常情況下上面的 try/except 已經接住了）
        log.error(f"☠️☠️ [自動重啟] 定時重啟任務發生未攔截的例外：{error!r}")

    # ── 🆕 等下載完成再重啟 ────────────────────────────────────
    async def _notify_restart_channel(self, text: str, force: bool = False) -> None:
        """把重啟相關訊息送到「上次重啟回報用的頻道」。

        ⚠️ 這段刻意包了兩層 try/except：通知失敗**絕對不能**影響重啟流程
        （例如頻道被刪、權限被拔、網路暫時不通）。
        `force=False` 時會做節流，避免等待期間每 30 秒洗一次頻道。
        """
        now = time.monotonic()
        if not force and (now - self._last_defer_notify) < DOWNLOAD_NOTIFY_INTERVAL:
            return
        self._last_defer_notify = now

        try:
            # 優先使用記憶體中的頻道；沒有的話才回頭讀標記檔（並補進記憶體）
            channel_id = self._notify_channel_id
            if not channel_id:
                try:
                    if os.path.exists(RESTART_FLAG_PATH):
                        with open(RESTART_FLAG_PATH, "r", encoding="utf-8") as f:
                            raw = (json.load(f) or {}).get("channel_id")
                        channel_id = int(raw) if raw else None
                        self._notify_channel_id = channel_id
                except Exception:
                    channel_id = None

            if not channel_id:
                log.info("[自動重啟] 沒有可通知的頻道（尚無重啟紀錄），略過頻道通知")
                return
            channel = self.bot.get_channel(channel_id)
            if channel is None:
                channel = await self.bot.fetch_channel(channel_id)
            if channel is None:
                return
            await channel.send(text)
        except Exception as e:
            log.warning(f"⚠️ [自動重啟] 無法送出延後通知（不影響重啟）: {type(e).__name__}: {e}")

    async def _deferred_restart(self, reason: str, pending: List[str],
                                probe_error: Optional[str]) -> None:
        """等所有下載結束後才真正重啟；超過 DOWNLOAD_GRACE_SECONDS 則放棄等待。

        為什麼要有「放棄等待」的上限：如果某個下載卡死（例如遠端主機不回應、
        狀態沒被正確釋放），無限等待就等於**永遠不再重啟**，
        那比打斷一次下載更糟。
        """
        if probe_error:
            log.warning(
                f"⚠️ [自動重啟] {probe_error} → 保守起見視為「有下載」，先延後重啟"
            )
        log.info(
            f"⏳ [自動重啟] {reason} 延後執行：偵測到 {len(pending)} 項下載工作進行中"
        )
        for item in pending:
            log.info(f"     • {item}")

        summary = "\n".join(f"　• {item}" for item in pending) or "　• （狀態無法確認）"
        await self._notify_restart_channel(
            f"⏳ **今日定時重啟已延後**：目前還有下載正在進行，為避免中斷使用者的下載，"
            f"機器人會等它們完成後才重啟（最多等 {DOWNLOAD_GRACE_SECONDS // 60} 分鐘）。\n"
            f"進行中的工作：\n{summary}",
            force=True,
        )

        deadline = time.monotonic() + DOWNLOAD_GRACE_SECONDS
        forced = False
        waited = 0.0

        while True:
            await asyncio.sleep(DOWNLOAD_POLL_SECONDS)
            waited += DOWNLOAD_POLL_SECONDS

            try:
                pending, probe_error = _pending_downloads()
            except Exception:
                log.warning(f"⚠️ [自動重啟] 檢查下載狀態時發生例外：\n{traceback.format_exc()}")
                pending, probe_error = [], None

            if not pending and not probe_error:
                log.info(f"✅ [自動重啟] 下載已全部完成（等待約 {int(waited)} 秒），繼續執行重啟")
                break

            if time.monotonic() >= deadline:
                forced = True
                log.warning(
                    f"⚠️ [自動重啟] 已等待 {int(waited)} 秒（上限 {DOWNLOAD_GRACE_SECONDS} 秒）"
                    f"仍有下載未完成，仍執行重啟（避免永遠不重啟）"
                )
                break

            # 節流後回報進度，讓使用者知道「不是當掉，是在等」
            await self._notify_restart_channel(
                f"⏳ 定時重啟仍在等待下載完成（已等約 {int(waited // 60)} 分鐘）…\n"
                f"剩餘 {max(0, int((deadline - time.monotonic()) // 60))} 分鐘後無論如何都會重啟。"
            )

        if forced:
            await self._notify_restart_channel(
                "⚠️ **等待逾時，即將重啟**。若有下載正在進行將會被中斷；"
                "殘留的暫存檔案會在下次開機時自動清除，下次請稍候再試。",
                force=True,
            )

        await self._do_restart(reason=reason)

    # ── 🛠️ 手動重啟指令 ───────────────────────────────────────
    @app_commands.command(name="restart", description="【開發者專用】立即手動重啟機器人")
    @app_commands.describe(
        force="跳過「等待下載完成」直接重啟（會中斷正在進行的下載，預設否）"
    )
    async def restart_command(self, interaction: discord.Interaction, force: bool = False):
        if not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message("❌ 只有機器人開發者（Owner）才能使用此指令！", ephemeral=True)
            return

        if _uptime() < MIN_UPTIME_SECONDS:
            wait = int(MIN_UPTIME_SECONDS - _uptime())
            await interaction.response.send_message(
                f"⏳ 機器人剛開機不久，請再等約 {wait} 秒再重啟（避免重啟迴圈）。", ephemeral=True
            )
            return

        # 同步上鎖（tree.interaction_check 會擋掉之後的所有指令，包含重複的 /restart）
        if not self._acquire():
            # 已經在重啟中。唯一需要特別處理的情況：目前是「正在等所有下載完成」
            # 的延後重啟，而使用者不想等了 → 取消等待、立刻重啟。
            deferring = self._pending_restart_task is not None and not self._pending_restart_task.done()
            if deferring and force:
                log.warning("⚠️ [自動重啟] 手動 /restart force:True 覆蓋了等待中的定時重啟，改為立即執行")
                _cancel_pending_restart_task(self._pending_restart_task)
                self._deferring = False
                try:
                    await interaction.response.send_message(
                        "🔄 已取消「等待下載完成」的延後重啟，改為**立即**重新啟動。\n"
                        "⚠️ 正在進行的下載會被中斷（殘留暫存檔會在下次開機自動清除）。",
                        ephemeral=True,
                    )
                except Exception:
                    pass
                await asyncio.sleep(1)
                await self._do_restart(
                    reason=f"由 {interaction.user} 手動觸發（強制覆蓋延後中的定時重啟）",
                    notify_channel_id=interaction.channel_id,
                )
                return

            if deferring:
                await interaction.response.send_message(
                    "⏳ 目前已排定「等正在進行的下載完成後」自動重啟，不需要再手動重啟。\n"
                    "若你要**立刻**重啟（會中斷下載），請用 `/restart force:True`。",
                    ephemeral=True,
                )
                return

            await interaction.response.send_message("🔄 已經在重啟中了，請稍候。", ephemeral=True)
            return

        try:
            await interaction.response.send_message(
                "🔄 收到！機器人即將重新啟動，約 10~30 秒後會自動回報。**請先不要再輸入指令**，"
                "開機初期指令還在載入。"
            )
        except Exception:
            self._restarting = False  # 連回覆都失敗就不重啟，解鎖
            raise

        log.info(f"🔄 [自動重啟] 由 {interaction.user} ({interaction.user.id}) 手動觸發重啟")
        await asyncio.sleep(1)  # 讓上面的訊息有時間送達
        await self._do_restart(
            reason=f"由 {interaction.user} 手動觸發",
            notify_channel_id=interaction.channel_id,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoRestartCog(bot))
