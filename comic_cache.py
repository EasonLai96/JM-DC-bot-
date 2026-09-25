# -*- coding: utf-8 -*-
"""
comic_cache.py — /jm 下載結果快取

目的：同一本子被不同使用者（或同一使用者手滑重複點）短時間內重複下載時，
直接把上次上傳到 Pixeldrain 的連結重新送出，省下重新解析、下載、壓縮、
打包 PDF、再上傳一次的頻寬與時間成本。

設計重點：
- Pixeldrain 上的檔案「3 個月內無人下載會自動清除」，所以快取本身的有效期
  要抓得比 3 個月短很多（預設 30 天），降低「快取還在、但檔案其實已經被
  清掉」的機率。
- 即使在 TTL 內，送出前仍會做一次輕量 HEAD 請求驗證連結是否還活著；
  驗證失敗就視同快取未命中，自動改為重新下載，不會讓使用者拿到死連結。
- 純 JSON 檔案儲存，跟專案裡其他設定檔（log_channel.json 等）風格一致，
  用絕對路徑（current_dir）避免相對路徑那個問題重演。
"""
import os
import json
import time
import aiohttp

from config import current_dir
from logger_config import log

CACHE_FILE = os.path.join(current_dir, 'comic_cache.json')

# 快取有效期（秒）。刻意抓得比 Pixeldrain 的「3 個月無人下載自動清除」短很多，
# 降低「快取還在，但檔案其實已經被清掉」的機率。有需要可自行調整。
CACHE_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 天

# 驗證連結存活時的 HEAD 請求逾時秒數，避免驗證卡住整個指令
_VERIFY_TIMEOUT = 5


def _load_raw() -> dict:
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_raw(data: dict):
    try:
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        # 快取寫入失敗不該影響這次下載本身已經成功的結果，只是下次不會命中快取而已
        log.warning(f"⚠️ [漫畫快取] 寫入快取檔失敗（不影響本次下載結果，只是這次不會被快取）: {e}")


def get_cached_album(album_id: str):
    """回傳指定 album_id 的快取內容；不存在或已過期回傳 None（過期的話順便清掉該筆）。"""
    data = _load_raw()
    entry = data.get(str(album_id))
    if not entry:
        return None
    if time.time() - entry.get('cached_at', 0) > CACHE_TTL_SECONDS:
        data.pop(str(album_id), None)
        _save_raw(data)
        return None
    return entry


def save_cached_album(album_id: str, title: str, total_pages: int, volumes: list):
    """
    volumes: [{'vol_idx': 1, 'url': ..., 'size_mb': ..., 'page_start': 1, 'page_end': 500}, ...]
    """
    data = _load_raw()
    data[str(album_id)] = {
        'title': title,
        'total_pages': total_pages,
        'volumes': volumes,
        'cached_at': time.time(),
    }
    _save_raw(data)


def invalidate_cached_album(album_id: str):
    data = _load_raw()
    if str(album_id) in data:
        data.pop(str(album_id), None)
        _save_raw(data)


async def verify_links_alive(urls: list) -> bool:
    """對快取裡的每個下載連結做一次輕量 HEAD 請求，全部存活才算通過。
    任何一個掛掉、逾時、或連線失敗，都保守視為快取失效——寧可重新下載，
    也不要讓使用者點到死連結。"""
    if not urls:
        return False
    try:
        timeout = aiohttp.ClientTimeout(total=_VERIFY_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for url in urls:
                try:
                    async with session.head(url, allow_redirects=True) as resp:
                        if resp.status >= 400:
                            return False
                except Exception:
                    return False
        return True
    except Exception as e:
        log.warning(f"⚠️ [漫畫快取] 驗證連結存活狀態時發生例外，保守視為快取失效: {e}")
        return False