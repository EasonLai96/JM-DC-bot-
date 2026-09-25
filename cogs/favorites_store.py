# -*- coding: utf-8 -*-
"""⭐ 收藏（我的最愛）資料儲存層。

禁漫（JM）與 nhentai（NH）**共用同一個檔案**，但每一筆都用「來源前綴」區分，
key 長這樣：

    "JM:123456"   → 禁漫天堂本子 123456
    "NH:654321"   → nhentai 作品 654321

為什麼要用前綴而不是分兩個檔案／兩個 dict：
  • 兩個站台的 ID 都是純數字，**一定會撞號**（JM 的 123456 跟 NH 的 123456
    是兩本完全不同的本子）。用前綴當 key 之後，「同一本」的判定是唯一的，
    而且排序、分頁、計數、去重都不用再分開處理兩套邏輯。
  • 之後若要新增第三個來源（例如某個新站），只要加一個前綴，資料結構不用改。

檔案：favorites_data/favorites.json
    {
      "version": 1,
      "users": {
        "<user_id>": {
          "JM:123456": {"title": "...", "note": "...", "added": 1730000000.0}
        }
      }
    }

寫入策略與 profile_store.py 一致：asyncio.Lock 序列化 + tmp/os.replace 原子寫入
+ 每天第一次覆蓋前的快照備份（收藏是玩家自己累積的資料，不該因為一次意外全毀）。
"""
import os
import re
import json
import time
import shutil
import asyncio
import datetime
from typing import Optional, Dict, Any, List, Tuple
from config import current_dir
from logger_config import log

# ==================== 路徑與上限 ====================

DATA_DIR = os.path.join(current_dir, "favorites_data")
FAVORITES_PATH = os.path.join(DATA_DIR, "favorites.json")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
BACKUP_KEEP = 3          # 收藏檔比經濟檔小很多，留 3 份就夠

MAX_FAVORITES_PER_USER = 300   # 每人上限，避免有人用腳本把檔案灌爆
MAX_TITLE_LEN = 120
MAX_NOTE_LEN = 80
MAX_ID_LEN = 20

# 來源定義：前綴 → 顯示名稱 / 網址模板
SOURCES: Dict[str, Dict[str, str]] = {
    "JM": {"label": "禁漫天堂", "url": "https://18comic.vip/album/{id}"},
    "NH": {"label": "nhentai", "url": "https://nhentai.net/g/{id}"},
}

os.makedirs(DATA_DIR, exist_ok=True)

_lock = asyncio.Lock()


# ==================== 純函式：來源與 key ====================

def normalize_source(value) -> Optional[str]:
    """把各種寫法（JM／jm／禁漫／NH／nh／nhentai）正規化成 "JM" / "NH"。"""
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    for prefix, meta in SOURCES.items():
        if text == prefix.lower() or text == meta["label"].lower():
            return prefix
    if text in ("禁漫", "禁漫天堂", "jmcomic", "18comic", "jcomic"):
        return "JM"
    if text in ("nhentai.net", "nh"):
        return "NH"
    return None


def default_source() -> str:
    return "JM"


def make_key(source, book_id) -> Optional[str]:
    """組出 "JM:123456" 這種前綴 key；格式不對回 None。"""
    prefix = normalize_source(source)
    if prefix is None:
        return None
    text = str(book_id or "").strip()
    if not text.isdigit() or not (1 <= len(text) <= MAX_ID_LEN):
        return None
    return f"{prefix}:{text}"


def split_key(key) -> Tuple[Optional[str], Optional[str]]:
    """把 "JM:123456" 拆回 ("JM", "123456")；格式不對回 (None, None)。"""
    if not isinstance(key, str) or ":" not in key:
        return None, None
    prefix, _, book_id = key.partition(":")
    prefix = normalize_source(prefix)
    if prefix is None or not book_id.isdigit():
        return None, None
    return prefix, book_id


def book_url(source, book_id) -> str:
    prefix = normalize_source(source) or default_source()
    return SOURCES[prefix]["url"].format(id=str(book_id))


def source_label(source) -> str:
    prefix = normalize_source(source)
    return SOURCES[prefix]["label"] if prefix else str(source)


# ==================== 純函式：使用者輸入解析 ====================
#
# ⚠️ 這裡的網址規則刻意與 cogs/comic.py 的 _parse_album_id、
#    cogs/nhentai.py 的 parse_book_id 保持一致。之所以不直接 import 那兩支，
#    是因為它們所在的 cog 會連帶載入 jmcomic / discord 等重量級相依，
#    而這一層必須保持「純資料、可單獨測試」。

_PREFIX_INPUT_RE = re.compile(
    r'^\s*(jm|nh|禁漫|禁漫天堂|nhentai)\s*[:：\-_ ]?\s*(\d{1,20})\s*$', re.I
)
_NH_PATH_RE = re.compile(r'/g/(\d{1,20})', re.I)
_JM_PATH_RE = re.compile(r'/(?:album|photos)/(\d{1,20})', re.I)
_BARE_NUMBER_RE = re.compile(r'^\d{1,20}$')
_DIGITS_RE = re.compile(r'\d{1,20}')

_NH_HOST_HINTS = ("nhentai.net", "nhentai.com", "nhentai")
_JM_HOST_HINTS = ("18comic", "jmcomic", "jmtt", "comic18", "jcomic")


def parse_favorite_input(raw, source_hint=None) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """把使用者輸入解析成 `(source, book_id, error)`。

    支援的寫法（大小寫不拘）：
      • `JM:123456` / `NH-123456` / `jm_123456` / `nh123456` / `禁漫:123456`
      • `https://18comic.vip/album/123456/`            → JM
      • `https://nhentai.net/g/654321/`                → NH
      • 純數字 `123456` → 必須搭配 `source_hint`（因為兩站 ID 會撞號）

    成功時回傳 `(source, book_id, None)`；失敗時回傳 `(None, None, 錯誤訊息)`。
    """
    if raw is None or not str(raw).strip():
        return None, None, "請輸入本子 ID 或完整網址。"

    text = str(raw).strip().strip("<>").strip()

    # ① 使用者自己打了前綴（最明確，優先採用）
    match = _PREFIX_INPUT_RE.match(text)
    if match:
        prefix = normalize_source(match.group(1))
        book_id = match.group(2)
        if prefix and book_id:
            return prefix, book_id, None

    # ② 網址：去掉 query / fragment / 結尾斜線
    cleaned = text.split("?")[0].split("#")[0].rstrip("/").strip().lower()

    nh_match = _NH_PATH_RE.search(cleaned)
    jm_match = _JM_PATH_RE.search(cleaned)

    # `/g/<數字>` 是 nhentai 專屬路徑，優先於 host 判斷
    if nh_match:
        return "NH", nh_match.group(1), None
    if jm_match:
        return "JM", jm_match.group(1), None

    if any(hint in cleaned for hint in _NH_HOST_HINTS):
        digits = _DIGITS_RE.search(cleaned)
        if digits:
            return "NH", digits.group(0), None
        return None, None, "這個 nhentai 網址裡找不到作品 ID，請直接輸入數字 ID。"

    if any(hint in cleaned for hint in _JM_HOST_HINTS):
        # 禁漫網址的 ID 幾乎都在最後一段，取最後一串數字最保險
        digits = _DIGITS_RE.findall(cleaned)
        if digits:
            return "JM", digits[-1], None
        return None, None, "這個禁漫網址裡找不到本子 ID，請直接輸入數字 ID。"

    # ③ 純數字：兩站 ID 會撞號，所以一定要有來源提示
    if _BARE_NUMBER_RE.match(text):
        prefix = normalize_source(source_hint)
        if prefix is None:
            return None, None, (
                "純數字 ID 必須指定來源（禁漫與 nhentai 的 ID 會重複）。\n"
                "請用 `/favorite` 的 `來源` 選項選擇，或直接打 `JM:123456` / `NH:654321`。"
            )
        return prefix, text, None

    return None, None, "看不懂這個輸入，請輸入本子 ID（例如 `JM:123456`、`NH:654321`）或完整網址。"


# ==================== 檔案存取 ====================

def _empty_store() -> dict:
    return {"version": 1, "users": {}}


def _load_raw() -> dict:
    if not os.path.exists(FAVORITES_PATH):
        return _empty_store()
    try:
        with open(FAVORITES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        # 🛠️ 壞檔時「回空集合」等於全部玩家的收藏瞬間歸零，所以一定要留下痕跡，
        # 而且不能順手把壞檔覆蓋掉（下一次存檔前先讓每日快照把原檔留一份）。
        log.error(f"❌ [Favorites] 讀取 favorites.json 失敗（將以空資料繼續）: {e!r}")
        return _empty_store()

    if not isinstance(data, dict):
        log.error("❌ [Favorites] favorites.json 格式不是物件，已忽略內容。")
        return _empty_store()

    users = data.get("users")
    if not isinstance(users, dict):
        # 相容「直接把 users 放在最外層」的舊寫法，或修掉被手動改壞的結構
        users = {k: v for k, v in data.items() if k.isdigit() and isinstance(v, dict)}
    data["users"] = users
    data.setdefault("version", 1)
    return data


_last_backup_date: Optional[str] = None


def _prune_backups() -> None:
    try:
        names = sorted(
            n for n in os.listdir(BACKUP_DIR)
            if n.startswith("favorites-") and n.endswith(".json")
        )
    except OSError:
        return
    for name in names[:-BACKUP_KEEP]:
        try:
            os.remove(os.path.join(BACKUP_DIR, name))
        except OSError:
            pass


def _maybe_daily_backup() -> None:
    """每天第一次覆蓋前，先留一份「變更前」的快照（與 profile_store 相同策略）。"""
    global _last_backup_date
    today = datetime.date.today().isoformat()
    if _last_backup_date == today:
        return
    _last_backup_date = today
    if not os.path.exists(FAVORITES_PATH):
        return
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        snapshot = os.path.join(BACKUP_DIR, f"favorites-{today}.json")
        if not os.path.exists(snapshot):
            shutil.copy2(FAVORITES_PATH, snapshot)
            log.info(f"🗄️ [Favorites] 已建立每日備份快照: {os.path.basename(snapshot)}")
        _prune_backups()
    except OSError as e:
        log.warning(f"⚠️ [Favorites] 建立每日備份快照失敗（不影響本次存檔）: {e!r}")


def _save_raw(data: dict) -> bool:
    try:
        _maybe_daily_backup()
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp_path = FAVORITES_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, FAVORITES_PATH)
        return True
    except OSError as e:
        log.error(f"❌ [Favorites] 寫入 favorites.json 失敗: {e!r}")
        return False


def _clean_text(value, limit: int) -> str:
    if value is None:
        return ""
    text = str(value).replace("\n", " ").replace("\r", " ").strip()
    return text[:limit]


# ==================== 對外 API ====================

async def add_favorite(user_id: int, source, book_id, title=None, note=None) -> Tuple[str, str]:
    """新增收藏。

    回傳 `(status, key)`：
      "added"   —— 新增成功
      "updated" —— 已經收藏過，這次補上了標題／備註
      "exists"  —— 已經收藏過，且沒有新資訊可補
      "limit"   —— 超過每人上限
      "invalid" —— 來源或 ID 格式錯誤
    """
    key = make_key(source, book_id)
    if key is None:
        return "invalid", ""

    title = _clean_text(title, MAX_TITLE_LEN)
    note = _clean_text(note, MAX_NOTE_LEN)

    async with _lock:
        data = _load_raw()
        users = data["users"]
        uid = str(user_id)
        bucket = users.get(uid)
        if not isinstance(bucket, dict):
            bucket = {}

        existing = bucket.get(key)
        if isinstance(existing, dict):
            changed = False
            # 先前是用純 ID 收藏的（還不知道標題），這次補上標題
            if title and not existing.get("title"):
                existing["title"] = title
                changed = True
            if note and existing.get("note") != note:
                existing["note"] = note
                changed = True
            if changed:
                users[uid] = bucket
                _save_raw(data)
                return "updated", key
            return "exists", key

        if len(bucket) >= MAX_FAVORITES_PER_USER:
            return "limit", key

        bucket[key] = {"title": title, "note": note, "added": time.time()}
        users[uid] = bucket
        if not _save_raw(data):
            return "invalid", key
        return "added", key


async def remove_favorite(user_id: int, source, book_id) -> bool:
    """取消收藏；回傳「原本是否存在」（不存在就沒必要存檔）。"""
    key = make_key(source, book_id)
    if key is None:
        return False

    async with _lock:
        data = _load_raw()
        users = data["users"]
        uid = str(user_id)
        bucket = users.get(uid)
        if not isinstance(bucket, dict) or key not in bucket:
            return False
        bucket.pop(key, None)
        if not bucket:
            users.pop(uid, None)   # 空殼不留，避免檔案被「已清空的使用者」撐大
        _save_raw(data)
        return True


async def clear_favorites(user_id: int) -> int:
    """清空某人的收藏，回傳被刪除的筆數。"""
    async with _lock:
        data = _load_raw()
        users = data["users"]
        uid = str(user_id)
        bucket = users.pop(uid, None)
        if not isinstance(bucket, dict) or not bucket:
            return 0
        _save_raw(data)
        return len(bucket)


async def is_favorite(user_id: int, source, book_id) -> bool:
    key = make_key(source, book_id)
    if key is None:
        return False
    async with _lock:
        data = _load_raw()
    bucket = data["users"].get(str(user_id))
    return isinstance(bucket, dict) and key in bucket


async def list_favorites(user_id: int, source=None) -> List[Dict[str, Any]]:
    """回傳某人的收藏清單（新加入的排在前面）。

    每一筆是 dict：`{"key","source","book_id","title","note","added"}`。
    """
    prefix = normalize_source(source) if source is not None else None
    async with _lock:
        data = _load_raw()
    bucket = data["users"].get(str(user_id))
    if not isinstance(bucket, dict):
        return []

    items: List[Dict[str, Any]] = []
    for key, entry in bucket.items():
        item_source, book_id = split_key(key)
        if item_source is None:
            continue
        if prefix is not None and item_source != prefix:
            continue
        entry = entry if isinstance(entry, dict) else {}
        items.append({
            "key": key,
            "source": item_source,
            "book_id": book_id,
            "title": _clean_text(entry.get("title"), MAX_TITLE_LEN),
            "note": _clean_text(entry.get("note"), MAX_NOTE_LEN),
            "added": float(entry.get("added") or 0.0),
        })
    items.sort(key=lambda it: (it["added"], it["key"]), reverse=True)
    return items


async def count_favorites(user_id: int) -> Dict[str, int]:
    """回傳 `{"total": n, "JM": a, "NH": b}`。"""
    items = await list_favorites(user_id)
    result = {"total": len(items)}
    for prefix in SOURCES:
        result[prefix] = sum(1 for it in items if it["source"] == prefix)
    return result


async def global_stats() -> Dict[str, int]:
    """全站統計（給 Owner 除錯用）。"""
    async with _lock:
        data = _load_raw()
    users = data["users"]
    total = sum(len(b) for b in users.values() if isinstance(b, dict))
    return {"users": len(users), "favorites": total, "limit_per_user": MAX_FAVORITES_PER_USER}
