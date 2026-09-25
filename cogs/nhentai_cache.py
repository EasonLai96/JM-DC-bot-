# -*- coding: utf-8 -*-
"""Small persistent cache for completed nHentai uploads."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import aiohttp


_CACHE_PATH = Path(__file__).with_name("nhentai_download_cache.json")
_CACHE_TTL = timedelta(days=90)


def _read_cache() -> Dict[str, Dict[str, Any]]:
    try:
        data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _write_cache(data: Dict[str, Dict[str, Any]]) -> None:
    temporary_path = _CACHE_PATH.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary_path.replace(_CACHE_PATH)


def get_cached_download(gallery_id: str) -> Optional[Dict[str, Any]]:
    entry = _read_cache().get(str(gallery_id))
    if not entry:
        return None
    try:
        created_at = datetime.fromisoformat(entry["created_at"])
        if datetime.now(timezone.utc) - created_at > _CACHE_TTL:
            invalidate_cached_download(gallery_id)
            return None
    except (KeyError, TypeError, ValueError):
        invalidate_cached_download(gallery_id)
        return None
    return entry


def save_cached_download(gallery_id: str, title: str, url: str, size_mb: float) -> None:
    cache = _read_cache()
    cache[str(gallery_id)] = {
        "title": title,
        "url": url,
        "size_mb": round(size_mb, 1),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_cache(cache)


def invalidate_cached_download(gallery_id: str) -> None:
    cache = _read_cache()
    if cache.pop(str(gallery_id), None) is not None:
        _write_cache(cache)


async def verify_link_alive(url: str) -> bool:
    """Check a stored Pixeldrain link before returning it to a user.

    🛠️ 修復（使用者看到「應用程式未回應」）：
    舊版只 catch `aiohttp.ClientError`，但 ClientTimeout 逾時丟出的是
    `asyncio.TimeoutError`（不是 ClientError 的子類），例外會直接往外拋。
    這裡改成攔截所有例外並保守回傳 False（＝視為快取失效，改走重新下載）。

    另外有些主機不支援 HEAD（回 405/501），舊版會把「其實活著」的連結誤判成
    失效而白白重新下載一次；這裡改用 Range GET 只抓 1 byte 再確認一次。
    """
    timeout = aiohttp.ClientTimeout(total=15)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            head_unsupported = False
            try:
                async with session.head(url, allow_redirects=True) as response:
                    if 200 <= response.status < 400:
                        return True
                    if response.status in (405, 501):
                        head_unsupported = True
                    else:
                        return False
            except Exception:
                head_unsupported = True

            if not head_unsupported:
                return False

            # 主機不支援 HEAD → 用 Range GET 只抓第一個 byte 再判斷
            try:
                async with session.get(
                    url, allow_redirects=True, headers={"Range": "bytes=0-0"}
                ) as response:
                    return 200 <= response.status < 400
            except Exception:
                return False
    except Exception:
        # 任何未預期狀況（含逾時、DNS、TLS）一律保守視為失效
        return False


class DownloadRegistry:
    """Coordinates duplicate requests across concurrent Discord interactions."""
    def __init__(self, max_concurrent: int = 2) -> None:
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self._active: set[str] = set()
        self._lock = asyncio.Lock()

    async def acquire(self, gallery_id: str) -> bool:
        async with self._lock:
            if gallery_id in self._active:
                return False
            self._active.add(gallery_id)
            return True

    async def release(self, gallery_id: str) -> None:
        async with self._lock:
            self._active.discard(gallery_id)

    def active_count(self) -> int:
        """目前正在處理中的作品數量（給自動重啟判斷「有沒有下載在跑」）。

        ⚠️ 刻意做成**同步**方法：`_active` 是 `set`，讀取是原子操作，不需要
        取得 `asyncio.Lock`（那個鎖只能在事件迴圈裡 await）。自動重啟的
        定時任務本身就在事件迴圈中執行，讀取這個集合不會有競態問題。
        """
        try:
            return len(self._active)
        except Exception:
            return 0

    def active_ids(self) -> set[str]:
        """目前處理中的作品 ID 快照（複製一份，避免呼叫端改到內部狀態）。"""
        try:
            return set(self._active)
        except Exception:
            return set()
