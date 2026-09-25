# -*- coding: utf-8 -*-
import os
import time
import threading
import asyncio
import jmcomic
from typing import Optional
from dotenv import load_dotenv

current_dir = os.path.dirname(os.path.abspath(__file__))

# 載入環境變數
env_loaded = False
for env_name in ['token.env', 'token.env.txt', '.env']:
    env_path = os.path.join(current_dir, env_name)
    if os.path.exists(env_path):
        load_dotenv(env_path)
        print(f"✓ 成功讀取環境設定檔：{env_name}")
        env_loaded = True
        break
if not env_loaded:
    load_dotenv()

# 全局速率限制（Rate Limit）管理器
class DownloadTask:
    """單一下載任務的狀態（同時涵蓋「排隊中」與「執行中」兩個階段）。"""

    __slots__ = ("album_id", "user_id", "user_name", "guild_name",
                 "title", "enqueued_at", "started_at", "current", "total")

    # 樣本時間太短時速率會被嚴重高估（例如剛開始就回報 42/100 頁，算出來的
    # 剩餘時間會變成 0 秒這種荒謬數字）。至少要跑滿這個秒數才開始提供估計。
    MIN_RATE_SAMPLE_SECONDS = 3.0

    def __init__(self, album_id: str, user_id=None, user_name: str = "", guild_name: str = ""):
        self.album_id = album_id
        self.user_id = user_id
        self.user_name = user_name or "未知使用者"
        self.guild_name = guild_name or "私訊"
        self.title = "解析中..."
        self.enqueued_at = time.time()
        self.started_at: Optional[float] = None   # None = 還在排隊
        self.current = 0
        self.total = 0

    @property
    def running(self) -> bool:
        return self.started_at is not None

    def page_rate(self) -> float:
        """每秒完成頁數；樣本不足或還沒有資料時回傳 0（代表「無法估計」）。"""
        if not self.running or self.current <= 0 or self.started_at is None:
            return 0.0
        elapsed = time.time() - self.started_at
        if elapsed < self.MIN_RATE_SAMPLE_SECONDS:
            return 0.0
        return self.current / elapsed

    def eta_seconds(self) -> Optional[float]:
        """預估剩餘秒數；無法估計時回傳 None（不亂猜）。"""
        rate = self.page_rate()
        if rate <= 0 or self.total <= self.current:
            return None
        return (self.total - self.current) / rate


class DownloadManager:
    """下載排程管理器。

    同時負責兩件事：
      1. 防重複——同一本子（album_id）在完成前不接受第二次提交
      2. 佇列狀態追蹤——記錄誰在排隊、誰在下載、進度到哪，供 /queue 查詢

    ⚠️ 這裡刻意沿用 threading.Lock 而非 asyncio.Lock：下載進度是從
    ThreadPoolExecutor 的 worker 執行緒回報的，而且所有操作都只是單純的記憶體
    讀寫、完全不含 await，用同步鎖最單純也最不會出錯（不會有跨執行緒的
    「Future attached to a different loop」問題）。
    """

    def __init__(self, max_concurrent: int = 2):
        self.lock = threading.Lock()
        self.max_concurrent = max_concurrent
        self._tasks = {}   # album_id -> DownloadTask
        # 💡 考慮到 1GB RAM，限制整個機器人最多同時處理 2 個大任務，每個任務內部開 4 線程
        self.semaphore = asyncio.Semaphore(max_concurrent)

    # ── 相容舊介面 ──────────────────────────────────────────────
    @property
    def active_albums(self) -> set:
        """目前所有已登記的 album_id（含排隊中與執行中）。"""
        with self.lock:
            return set(self._tasks)

    # ── 生命週期 ────────────────────────────────────────────────
    def acquire_album(self, album_id: str, user_id=None,
                      user_name: str = "", guild_name: str = "") -> bool:
        """登記一個任務（先排隊，之後才真正開始下載）。已在處理中則回傳 False。"""
        with self.lock:
            if album_id in self._tasks:
                return False
            self._tasks[album_id] = DownloadTask(album_id, user_id, user_name, guild_name)
            return True

    def release_album(self, album_id: str) -> None:
        with self.lock:
            self._tasks.pop(album_id, None)

    def mark_started(self, album_id: str) -> None:
        """真正取得 semaphore 通道、開始下載時呼叫，用來把任務從「排隊」轉為「執行中」。"""
        with self.lock:
            task = self._tasks.get(album_id)
            if task is not None and task.started_at is None:
                task.started_at = time.time()

    def update_progress(self, album_id: str, current: Optional[int] = None,
                        total: Optional[int] = None, title: Optional[str] = None) -> None:
        """更新進度。⚠️ 可能從 worker 執行緒呼叫，務必保持快速、不做任何 I/O。"""
        with self.lock:
            task = self._tasks.get(album_id)
            if task is None:
                return
            if current is not None:
                task.current = current
            if total is not None:
                task.total = total
            if title:
                task.title = title

    # ── 查詢 ────────────────────────────────────────────────────
    def queue_position(self, album_id: str):
        """回傳 (前面還有幾個人在排隊, 等待佇列總長)；不在等待佇列中則回傳 None。

        asyncio.Semaphore 的喚醒順序是先進先出，所以依 enqueued_at 排序後的位置
        與實際被喚醒的順序一致。
        """
        with self.lock:
            waiting = [t for t in self._tasks.values() if not t.running]
        waiting.sort(key=lambda t: t.enqueued_at)
        for idx, task in enumerate(waiting):
            if task.album_id == album_id:
                return idx, len(waiting)
        return None

    def snapshot(self) -> dict:
        """取得完整佇列狀態（已排序，可直接餵給 /queue 顯示）。"""
        with self.lock:
            tasks = list(self._tasks.values())
        running = sorted((t for t in tasks if t.running), key=lambda t: t.started_at or 0)
        waiting = sorted((t for t in tasks if not t.running), key=lambda t: t.enqueued_at)
        return {
            "running": running,
            "waiting": waiting,
            "free_slots": max(0, self.max_concurrent - len(running)),
            "max_concurrent": self.max_concurrent,
        }

    def estimate_wait_seconds(self, position: int) -> Optional[float]:
        """粗略估計排在第 position 位（0 起算）還要等多久。

        作法：取目前執行中任務的平均預估剩餘時間，再依同時可跑幾個通道推估。
        若沒有任何任務有可用的速率資料，回傳 None——誠實地不亂給數字。
        """
        snap = self.snapshot()
        etas = [e for e in (t.eta_seconds() for t in snap["running"]) if e is not None]
        if not etas:
            return None
        average = sum(etas) / len(etas)
        return average * ((position // max(1, snap["max_concurrent"])) + 1)


dl_manager = DownloadManager()

# ==============================================================================
# ⚡ 1GB RAM 極致多線程優化設定
# ==============================================================================
option_dict = {
    'version': '2.0',
    'client': {
        'domain': ['www.cdnhjk.net', 'www.cdngwc.cc', 'www.cdngwc.net', 'www.cdngwc.club'],
        'post_with_common_headers': True,
        'retry_times': 5,
        'client_config': {'meta_data': {'verify': False}}
    },
    'download': {
        # 🚀 解放多線程：Jmcomic 內部下載圖片改為 4 執行緒併發
        'threads': 4,
        'max_workers': 4,
        # 🎨 降低壓縮質量至 55 (視覺無損，但記憶體佔用暴跌 40%，產出的 ZIP 體積更小、上傳更快)
        'image_convert': {'format': 'jpg', 'quality': 55},
        'save_dir': '.'
    }
}
option = jmcomic.JmOption.construct(option_dict)
jmcomic.JmModuleConfig.default_option = lambda: option