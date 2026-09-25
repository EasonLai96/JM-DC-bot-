# -*- coding: utf-8 -*-
import os
import re
import asyncio
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Any, Dict, Awaitable, Callable

import aiohttp
import discord
from discord.ext import commands
from discord import app_commands

try:
    from logger_config import log
except ImportError:
    import logging
    log = logging.getLogger(__name__)

from nhentai_cache import (
    DownloadRegistry,
    get_cached_download,
    invalidate_cached_download,
    save_cached_download,
    verify_link_alive,
)
from utils import upload_to_pixeldrain, is_nsfw_allowed, split_id_tokens, pack_embed_fields, safe_respond

try:
    # 🆕 預覽結果底下的「⭐ 收藏這本」按鈕（cogs/favorites.py）。
    # ⚠️ 這裡刻意用 try/except 包住：收藏只是附加功能，萬一收藏模組載入失敗，
    # 也絕對不能連帶讓 nhentai 查詢／下載整支 cog 載入失敗。
    #
    # 🛠️ 修復：改成「絕對匯入優先、相對匯入備援」。原因是 cogs/ 被加進了 sys.path，
    # 所以這個檔案有可能被別的模組用頂層名稱 `nhentai` 匯入（歷史上的
    # nhentai_search.py 就是這樣寫的），那種情況下 __package__ 是空的，
    # `from .favorites import ...` 一定會失敗（正式環境 log 已出現過這個警告）。
    # 絕對匯入不論被載成 `cogs.nhentai` 還是 `nhentai` 都能指向同一個收藏模組；
    # 而且兩種寫法都只會拿到同一個 cogs.favorites_store（同一把鎖），不會產生兩份資料。
    from cogs.favorites import FavoriteButtonView
except Exception:
    try:
        from .favorites import FavoriteButtonView
    except Exception as _fav_err:  # pragma: no cover
        FavoriteButtonView = None
        log.warning(f"⚠️ [收藏] 無法載入收藏按鈕，/nhv 將不顯示收藏按鈕: {_fav_err!r}")

try:
    # 🌐 顯示層翻譯（cogs/translate.py）。同樣刻意用 try/except 包住：
    # 翻譯只是附加功能，萬一 translate 模組載入失敗（依賴缺失、語法問題…），
    # 也絕對不能讓 nhentai 查詢／下載整支 cog 載入失敗。
    # 失敗時下面所有呼叫都會被 `_TR` 的 fallback 包裝擋掉，直接顯示原文。
    from cogs import translate as _TR
except Exception:
    try:
        import translate as _TR  # type: ignore[no-redef]
    except Exception as _tr_err:  # pragma: no cover
        _TR = None
        log.warning(f"⚠️ [翻譯] 無法載入翻譯模組，將只顯示原文: {_tr_err!r}")

# ── 翻譯安全包裝 ──────────────────────────────────────────────
# 這幾個小函式確保「翻譯模組不存在 / 翻譯壞掉 / 逾時」時，一律安靜地退回原文，
# 呼叫端不必到處寫 try/except。
async def _tr_title(title: str, limit: int = 240) -> str:
    """回傳「中文（原文）」或原文。永遠不會拋例外。"""
    text = str(title or "")
    if _TR is None or not text:
        return text
    try:
        return await _TR.translate_display(text, limit=limit)
    except Exception as error:  # pragma: no cover
        log.warning(f"⚠️ [翻譯] 標題翻譯失敗，改用原文: {error!r}")
        return text


async def _tr_tags(names, limit: int = 900, show_original: bool = True) -> str:
    """把標籤清單轉成中文顯示字串。永遠不會拋例外。"""
    values = [str(n) for n in (names or []) if n]
    if _TR is None:
        return format_values(values, limit)
    try:
        return await _TR.translate_tag_line(values, show_original=show_original, budget=limit)
    except Exception as error:  # pragma: no cover
        log.warning(f"⚠️ [翻譯] 標籤翻譯失敗，改用原文: {error!r}")
        return format_values(values, limit)

NHENTAI_API_BASE = "https://nhentai.net/api/v2"
IMAGE_CDN_BASE = "https://i.nhentai.net/"
THUMBNAIL_CDN_BASE = "https://t.nhentai.net/"
# The image CDN rate-limits bursty traffic. Keep downloads deliberately gentle.
PAGE_DOWNLOAD_CONCURRENCY = 1
PAGE_REQUEST_INTERVAL_SECONDS = 0.8
PAGE_DOWNLOAD_MAX_RETRIES = 5
CDN_REQUEST_RATE_LOCK = asyncio.Lock()
CDN_NEXT_REQUEST_AT = 0.0


def load_api_key() -> str:
    """Use an environment variable first, then the local key file if present."""
    key = os.getenv("NHENTAI_API_KEY", "").strip()
    if key:
        return key

    try:
        return Path(__file__).with_name("nhantikey.txt").read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


API_KEY = load_api_key()
DOWNLOAD_REGISTRY = DownloadRegistry(max_concurrent=2)

def parse_book_id(value: str) -> Optional[str]:
    if not value:
        return None

    text = value.strip()
    m = re.search(r"/g/(\d+)/?", text, re.I)
    if m:
        return m.group(1)

    # 若是純數字
    if re.fullmatch(r"\d+", text):
        return text

    # 若是 nhentai.net/g/123456?xxx
    m = re.search(r"(\d+)", text)
    if m:
        # 避免把雜訊數字當 ID，這裡保守處理
        return m.group(1)

    return None


# 🆕 批量查詢一次最多處理幾個（同時也是 nhentai API 的禮貌上限）
MAX_BATCH_IDS = 10

# 🆕 每筆查詢之間的間隔（秒）。API 有速率限制，連續猛打會拿到 429。
BATCH_QUERY_INTERVAL = 0.7


def parse_book_id_list(raw, limit: int = MAX_BATCH_IDS):
    """把使用者貼上的一串 ID／網址解析成去重後的 nhentai ID 清單。

    回傳 (ids, dropped, invalid)：
      ids     —— 解析成功且去重後的 ID（最多 limit 個）
      dropped —— 因為超過 limit 而被忽略的數量
      invalid —— 完全解析不出 ID 的 token 數量（讓使用者知道有東西被略過）
    """
    ids, seen = [], set()
    dropped = invalid = 0
    for token in split_id_tokens(raw):
        parsed = parse_book_id(token)
        if not parsed:
            invalid += 1
            continue
        if parsed in seen:
            continue
        seen.add(parsed)
        if len(ids) >= limit:
            dropped += 1
            continue
        ids.append(parsed)
    return ids, dropped, invalid


def truncate_text(value: str, max_length: int = 180) -> str:
    if not value:
        return "未知"
    text = str(value).strip()
    if len(text) <= max_length:
        return text
    return text[: max_length - 1].rstrip() + "…"


def safe_escape(value: str) -> str:
    if value is None:
        return "未知"
    text = str(value).strip()
    return text.replace("*", "\\*").replace("_", "\\_").replace("`", "\\`")


def normalize_tags(raw_tags: Any) -> List[str]:
    tags: List[str] = []

    if isinstance(raw_tags, list):
        for tag in raw_tags:
            if isinstance(tag, str):
                tags.append(tag)
            elif isinstance(tag, dict):
                name = tag.get("name") or tag.get("tag") or tag.get("title")
                if name:
                    tags.append(str(name))
    elif isinstance(raw_tags, dict):
        # 支援 {"tags":[...]} 形式
        for key in ("tags", "tag_list", "items"):
            if key in raw_tags and isinstance(raw_tags[key], list):
                return normalize_tags(raw_tags[key])

    # 只保留前 20 個，避免 embed 超長
    return tags[:20]


def tag_names(data: Dict[str, Any], tag_type: str) -> List[str]:
    """Return the names for one v2 gallery tag category, preserving API order."""
    names: List[str] = []
    for tag in data.get("tags", []):
        if isinstance(tag, dict) and tag.get("type") == tag_type and tag.get("name"):
            names.append(str(tag["name"]))
    return names


def format_values(values: List[str], max_length: int = 900) -> str:
    """Make a Discord field value without exceeding its size limit."""
    if not values:
        return "無"

    result: List[str] = []
    used = 0
    for value in values:
        escaped = safe_escape(value)
        addition = len(escaped) + (2 if result else 0)
        if used + addition > max_length:
            result.append("…")
            break
        result.append(escaped)
        used += addition
    return ", ".join(result)


def pick_upload_time(data: Dict[str, Any]) -> str:
    value = data.get("upload_date")
    try:
        timestamp = int(value)
        # Discord will render this in each member's local time zone.
        datetime.fromtimestamp(timestamp, tz=timezone.utc)
        return f"<t:{timestamp}:F>"
    except (TypeError, ValueError, OSError):
        return "未知"


def pick_title(data: Dict[str, Any]) -> str:
    if not data:
        return "未知標題"

    # 常見 nhentai API 形態
    if "title" in data:
        title = data["title"]
        if isinstance(title, dict):
            for key in ("pretty", "english", "japanese", "native"):
                value = title.get(key)
                if value:
                    return str(value)
        elif isinstance(title, str):
            return title

    for key in ("title_pretty", "title_english", "title_japanese", "name"):
        value = data.get(key)
        if value:
            return str(value)

    return "未知標題"


def pick_author(data: Dict[str, Any]) -> str:
    if not data:
        return "未知"

    author = data.get("author")
    if isinstance(author, dict):
        for key in ("name", "username", "display_name"):
            value = author.get(key)
            if value:
                return str(value)
    elif isinstance(author, str):
        return author

    for key in ("artist", "creator", "author_name"):
        value = data.get(key)
        if value:
            return str(value)

    artists = tag_names(data, "artist")
    if artists:
        return ", ".join(artists)

    return "未知"


def pick_num_pages(data: Dict[str, Any]) -> str:
    for key in ("num_pages", "pages", "page_count"):
        value = data.get(key)
        if value is not None:
            try:
                return str(int(value))
            except Exception:
                pass

    if isinstance(data.get("images"), dict):
        pages = data["images"].get("pages")
        if isinstance(pages, list):
            return str(len(pages))

    return "未知"


def pick_cover_url(data: Dict[str, Any]) -> Optional[str]:
    if not data:
        return None

    # 1) 直接型態
    for key in ("cover", "cover_url", "thumbnail", "img", "image"):
        value = data.get(key)
        if isinstance(value, str) and value.startswith("http"):
            return value
        if isinstance(value, dict):
            url = value.get("url") or value.get("src")
            if isinstance(url, str) and url.startswith("http"):
                return url
            path = value.get("path")
            if isinstance(path, str) and path:
                return THUMBNAIL_CDN_BASE + path.lstrip("/")

    # 2) images.cover
    images = data.get("images")
    if isinstance(images, dict):
        cover = images.get("cover")
        if isinstance(cover, dict):
            for key in ("url", "src", "image", "base_url"):
                val = cover.get(key)
                if isinstance(val, str) and val.startswith("http"):
                    return val

        # 一些 API 會用 'pages' 自己就有 object
        pages = images.get("pages")
        if isinstance(pages, list) and pages:
            first = pages[0]
            if isinstance(first, dict):
                url = first.get("url") or first.get("src")
                if isinstance(url, str) and url.startswith("http"):
                    return url

    # 3) 傳統 media_id / image_url 形式
    media_id = data.get("media_id")
    if media_id:
        # 這裡保守處理，不直接拼接不確定域名；若 API 字段實際可用，再回傳
        # 若能從 API 補到真正 url，會在下方塞回資料
        pass

    # 4) 其他可能欄位
    for key in ("cover_image", "cover_url", "thumbnail_url"):
        value = data.get(key)
        if isinstance(value, str) and value.startswith("http"):
            return value

    return None


def pick_preview_url(data: Dict[str, Any], page_index: int = 0) -> Optional[str]:
    if not data:
        return None

    images = data.get("images")
    if isinstance(images, dict):
        pages = images.get("pages") or images.get("images")
        if isinstance(pages, list) and len(pages) > page_index:
            page = pages[page_index]
            if isinstance(page, dict):
                for key in ("url", "src", "image", "img"):
                    value = page.get(key)
                    if isinstance(value, str) and value.startswith("http"):
                        return value
                # 有些 API 只有一個 `t` 表示類型，不一定有 true url
                # 這時不硬猜，直接 return None
                return None

    # v2 gallery responses expose pages directly as {path, ...}.
    pages = data.get("pages")
    if isinstance(pages, list) and len(pages) > page_index:
        page = pages[page_index]
        if isinstance(page, dict):
            path = page.get("path")
            if isinstance(path, str) and path:
                return IMAGE_CDN_BASE + path.lstrip("/")

    # fallback: 僅回原站
    return None


def page_urls(data: Dict[str, Any]) -> List[str]:
    """Build direct image URLs from the documented v2 page paths."""
    urls: List[str] = []
    for page in data.get("pages", []):
        if isinstance(page, dict) and isinstance(page.get("path"), str):
            urls.append(IMAGE_CDN_BASE + page["path"].lstrip("/"))
    return urls


def build_pdf(image_paths: List[str], output_path: str) -> None:
    """Convert downloaded page images to one PDF without loading them all into RAM."""
    try:
        import img2pdf
    except ImportError as error:
        raise RuntimeError("缺少 img2pdf 套件，無法建立 PDF。") from error

    with open(output_path, "wb") as pdf_file:
        pdf_file.write(img2pdf.convert(image_paths))


class NhentaiCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DiscordBot/1.0",
                    "Accept": "application/json",
                },
                timeout=aiohttp.ClientTimeout(total=15),
            )
        return self.session

    async def _api_get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        session = await self._get_session()

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DiscordBot/1.0",
            "Accept": "application/json",
        }

        if API_KEY:
            headers["Authorization"] = f"Key {API_KEY}"

        url = f"{NHENTAI_API_BASE}{endpoint}"

        async with session.get(url, params=params, headers=headers) as resp:
            text = await resp.text()

            if resp.status == 401:
                raise PermissionError("API key 無效或未授權。")
            if resp.status == 403:
                raise PermissionError("API 访问被拒絕，請確認權限或 IP 限制。")
            if resp.status == 404:
                raise FileNotFoundError("找不到該作品。")
            if resp.status == 429:
                raise RuntimeError("API 擁擠中，請稍後再試。")
            if resp.status >= 500:
                raise RuntimeError(f"API 伺服器暫時錯誤（{resp.status}）")

            if resp.status != 200:
                raise RuntimeError(f"API 呼叫失敗（{resp.status}）：{text[:200]}")

            try:
                data = await resp.json()
            except Exception:
                raise ValueError(f"API 回傳不是合法 JSON：{text[:200]}")

            if isinstance(data, dict) and data.get("error"):
                raise RuntimeError(str(data["error"]))

            return data

    async def _download_pdf(
        self,
        data: Dict[str, Any],
        book_id: str,
        on_progress: Optional[Callable[[int, int, str], Awaitable[None]]] = None,
    ) -> tuple[str, float]:
        """Download a gallery's pages, create a PDF, upload it, then clean up."""
        urls = page_urls(data)
        if not urls:
            raise ValueError("API 沒有回傳可下載的頁面。")

        work_dir = tempfile.mkdtemp(prefix=f"nhentai_{book_id}_")
        pdf_path = os.path.join(work_dir, f"nhentai_{book_id}.pdf")
        image_paths = []
        for index, url in enumerate(urls, start=1):
            extension = os.path.splitext(url.split("?", 1)[0])[1] or ".jpg"
            image_paths.append(os.path.join(work_dir, f"{index:05d}{extension}"))
        session = await self._get_session()
        page_semaphore = asyncio.Semaphore(PAGE_DOWNLOAD_CONCURRENCY)
        progress_lock = asyncio.Lock()
        completed_pages = 0
        last_reported_pages = 0
        last_reported_at = 0.0

        async def wait_for_download_slot() -> None:
            """Space CDN requests across all concurrent gallery jobs."""
            global CDN_NEXT_REQUEST_AT
            async with CDN_REQUEST_RATE_LOCK:
                now = time.monotonic()
                delay = max(0.0, CDN_NEXT_REQUEST_AT - now)
                CDN_NEXT_REQUEST_AT = max(now, CDN_NEXT_REQUEST_AT) + PAGE_REQUEST_INTERVAL_SECONDS
            if delay:
                await asyncio.sleep(delay)

        async def download_page(url: str, destination: str) -> None:
            nonlocal completed_pages, last_reported_pages, last_reported_at
            async with page_semaphore:
                for attempt in range(PAGE_DOWNLOAD_MAX_RETRIES):
                    await wait_for_download_slot()
                    async with session.get(url) as response:
                        if response.status == 200:
                            with open(destination, "wb") as image_file:
                                async for chunk in response.content.iter_chunked(64 * 1024):
                                    image_file.write(chunk)
                            break

                        if response.status != 429 or attempt == PAGE_DOWNLOAD_MAX_RETRIES - 1:
                            raise RuntimeError(
                                f"下載第 {os.path.basename(destination)} 頁失敗（HTTP {response.status}）。"
                            )

                        retry_after = response.headers.get("Retry-After", "")
                        try:
                            delay = max(float(retry_after), 2 ** attempt)
                        except ValueError:
                            delay = 2 ** attempt
                        delay = min(delay, 30.0)
                        log.warning(
                            f"⚠️ [nhentai] CDN 限流：{os.path.basename(destination)} "
                            f"第 {attempt + 1} 次重試前等待 {delay:.1f} 秒"
                        )
                        if on_progress:
                            await on_progress(completed_pages, len(urls), f"CDN 限流，等待 {delay:.0f} 秒後重試")
                        await asyncio.sleep(delay)

            if not on_progress:
                return

            async with progress_lock:
                completed_pages += 1
                now = time.monotonic()
                should_report = (
                    completed_pages == len(urls)
                    or completed_pages - last_reported_pages >= 4
                    or now - last_reported_at >= 2.0
                )
                if should_report:
                    last_reported_pages = completed_pages
                    last_reported_at = now
                    current_page = completed_pages
                else:
                    current_page = 0

            if current_page:
                await on_progress(current_page, len(urls), "下載圖片中")

        try:
            await asyncio.gather(*(download_page(url, path) for url, path in zip(urls, image_paths)))
            if on_progress:
                await on_progress(len(urls), len(urls), "正在合併 PDF")
            await asyncio.to_thread(build_pdf, image_paths, pdf_path)
            size_mb = os.path.getsize(pdf_path) / (1024 * 1024)
            if on_progress:
                await on_progress(len(urls), len(urls), "正在上傳至雲端")
            cloud_url = await upload_to_pixeldrain(pdf_path)
            return cloud_url, size_mb
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    @app_commands.command(name="nhv", description="查詢 nHentai 作品資訊（僅查詢/預覽，不下載）")
    @app_commands.describe(id_or_url="輸入 nHentai 本子 ID 或完整網址")
    @app_commands.checks.cooldown(1, 10.0, key=lambda i: i.user.id)
    async def nhv(self, interaction: discord.Interaction, id_or_url: str):
        if not is_nsfw_allowed(interaction.channel):
            await interaction.response.send_message(
                "❌ 此指令包含成人內容，請前往 **開啟年齡限制 (NSFW)** 的頻道中使用！",
                ephemeral=True,
            )
            return

        book_id = parse_book_id(id_or_url)
        if not book_id:
            await interaction.response.send_message(
                "❌ 無法解析本子 ID，請輸入 nHentai 本子 ID 或完整網址。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True)

        try:
            data = await self._api_get(f"/galleries/{book_id}")

            if not isinstance(data, dict):
                raise ValueError("API 回傳格式異常。")

            title = pick_title(data)
            english_title = ""
            japanese_title = ""
            title_data = data.get("title")
            if isinstance(title_data, dict):
                english_title = str(title_data.get("english") or "")
                japanese_title = str(title_data.get("japanese") or "")
            if english_title:
                title = english_title

            artists = tag_names(data, "artist")
            parodies = tag_names(data, "parody")
            characters = tag_names(data, "character")
            tags = tag_names(data, "tag")
            groups = tag_names(data, "group")
            languages = tag_names(data, "language")
            categories = tag_names(data, "category")
            scanlator = str(data.get("scanlator") or "").strip()
            try:
                favorites = f"{int(data.get('num_favorites', 0)):,}"
            except (TypeError, ValueError):
                favorites = "未知"

            source_url = f"https://nhentai.net/g/{book_id}/"

            title_text = truncate_text(title, 120)
            # 🌐 書名翻譯：英文／日文標題會顯示成「中文（原文）」；已是中文則原樣顯示。
            # ⚠️ `title`（原文）之後仍要用在收藏庫與 log，所以絕不覆蓋它。
            display_title = await _tr_title(title, limit=240)
            embed = discord.Embed(
                title=truncate_text(display_title, 256),
                url=source_url,
                description="🔎 nHentai 作品預覽（僅查詢/展示，不下載、不分發）",
                color=discord.Color.orange(),
            )

            if japanese_title and japanese_title != title:
                # 日文原名同樣翻成中文（顯示成「中文（原文）」），
                # 這樣英文書名 + 日文原名的作品兩種讀者都看得懂。
                japanese_display = await _tr_title(japanese_title, limit=240)
                embed.add_field(
                    name="🇯🇵 日文標題",
                    value=truncate_text(japanese_display, 900),
                    inline=False,
                )

            # 🏷️ 標籤一律走本地字典查表（0ms、零 API），查不到的顯示原文。
            # 這裡刻意不做機翻 —— 實測機翻術語會產出「摩洛伊斯蘭解放陣線」這種災難。
            embed.add_field(name="👤 作者", value=await _tr_tags(artists), inline=True)
            embed.add_field(name="📄 頁數", value=pick_num_pages(data), inline=True)
            embed.add_field(name="❤️ 收藏數", value=favorites, inline=True)
            embed.add_field(name="🎭 衍生同人誌", value=await _tr_tags(parodies), inline=False)
            embed.add_field(name="👥 登場角色", value=await _tr_tags(characters), inline=False)
            embed.add_field(name="🏷️ 標籤", value=await _tr_tags(tags), inline=False)
            embed.add_field(name="👨‍👩‍👧‍👦 創作團隊", value=await _tr_tags(groups), inline=False)
            embed.add_field(name="🗣️ 語言", value=await _tr_tags(languages), inline=True)
            embed.add_field(name="📚 作品分類", value=await _tr_tags(categories), inline=True)
            if scanlator:
                embed.add_field(name="📝 翻譯團體", value=truncate_text(scanlator, 900), inline=False)
            embed.add_field(name="🕒 上傳時間", value=pick_upload_time(data), inline=False)

            embed.add_field(name="🌐 原站連結", value=f"[點我開啟]({source_url})", inline=False)

            # 🆕 預覽結果底下附一顆「⭐ 收藏這本」按鈕（NH 前綴），書名會一起存進收藏庫。
            # ⚠️ 這裡傳的是**原文 title**，不是翻譯後的顯示字串 —— 收藏庫存原文才不會
            # 因為日後調整譯名而對不上，也不會把機翻結果永久寫進資料庫。
            # create() 保證不拋例外，收藏功能不會影響查詢成功率。
            favorite_view = None
            if FavoriteButtonView is not None:
                favorite_view = await FavoriteButtonView.create(
                    interaction.user.id, "NH", book_id, title_text
                )
            await interaction.followup.send(embed=embed, view=favorite_view)

            log.info(
                f"✅ [nhentai] 伺服器={interaction.guild.name if interaction.guild else 'DM'} "
                f"| 使用者={interaction.user.id} | 本子={book_id} | 標題={title_text}"
            )

        except PermissionError as e:
            await interaction.followup.send(f"❌ 權限錯誤：{e}", ephemeral=True)
        except FileNotFoundError:
            await interaction.followup.send("❌ 找不到這個本子，請確認 ID 是否正確。", ephemeral=True)
        except RuntimeError as e:
            await interaction.followup.send(f"❌ API 目前不可用：{e}", ephemeral=True)
        except Exception as e:
            log.error(f"❌ [nhentai] 查詢失敗 book_id={book_id} error={e}")
            await interaction.followup.send(
                "❌ 查詢失敗，請確認本子 ID 是否正確，或稍後再試。",
                ephemeral=True,
            )

    @app_commands.command(name="nh", description="下載 nHentai 作品為 PDF 並上傳至雲端")
    @app_commands.describe(id_or_url="輸入 nHentai 本子 ID 或完整網址")
    @app_commands.checks.cooldown(1, 30.0, key=lambda i: i.user.id)
    async def nh_download(self, interaction: discord.Interaction, id_or_url: str):
        if not is_nsfw_allowed(interaction.channel):
            await interaction.response.send_message(
                "❌ 此指令包含成人內容，請前往 **開啟年齡限制 (NSFW)** 的頻道中使用！",
                ephemeral=True,
            )
            return

        book_id = parse_book_id(id_or_url)
        if not book_id:
            await interaction.response.send_message("❌ 請輸入正確的本子 ID 或網址。", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        # 🛠️ 修復：快取查詢與連結驗證原本完全在 try 區塊「之外」，只要
        # verify_link_alive() 或 get_cached_download() 丟出任何未預期例外
        # （逾時、DNS、TLS、JSON 損毀…），使用者就會直接收到「應用程式未回應」，
        # 而不是自動改成重新下載。這裡整段包起來：任何快取問題都只當成
        # 「快取未命中」，繼續往下走正常的重新下載流程。
        try:
            cached = get_cached_download(book_id)
            if cached and await verify_link_alive(cached["url"]):
                # 🌐 快取存的是原文書名，翻譯只在顯示時做（不動快取檔）
                cached_display = await _tr_title(str(cached["title"]), limit=240)
                embed = discord.Embed(
                    title=f"📄 {truncate_text(cached_display, 240)}",
                    description="⚡ 這本作品已有有效快取，直接提供既有雲端連結，未重新下載。",
                    color=discord.Color.green(),
                )
                embed.add_field(name="🗂️ 檔案大小", value=f"`{cached['size_mb']} MB`", inline=True)
                embed.add_field(name="🔗 下載連結", value=f"[點我下載 PDF]({cached['url']})", inline=False)
                await interaction.followup.send(embed=embed)
                return
            if cached:
                invalidate_cached_download(book_id)
        except Exception as e:
            log.warning(f"⚠️ [nhentai] 快取查驗失敗，改為重新下載 book_id={book_id}: {e}")

        if not await DOWNLOAD_REGISTRY.acquire(book_id):
            await interaction.followup.send("⏳ 這本作品正在處理中，請勿重複提交。", ephemeral=True)
            return

        try:
            async with DOWNLOAD_REGISTRY.semaphore:
                # 🛠️ 修復（L2）：`book_id` 是使用者輸入的字串，而「編輯訊息」同樣會解析
                # 提及 —— 使用者只要輸入 `@everyone` 就可能讓 bot 幫忙 ping 整個頻道。
                await interaction.edit_original_response(
                    content=f"📥 正在取得作品 `{book_id}` 的頁面資料…",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                data = await self._api_get(f"/galleries/{book_id}")
                title = pick_title(data)
                title_data = data.get("title")
                if isinstance(title_data, dict) and title_data.get("english"):
                    title = str(title_data["english"])

                total_pages = len(page_urls(data))

                async def update_progress(current: int, total: int, stage: str) -> None:
                    safe_total = max(total, 1)
                    percent = current / safe_total * 100
                    bar_length = 12
                    filled = min(bar_length, int(percent / 100 * bar_length))
                    bar = "🟩" * filled + "⬜" * (bar_length - filled)
                    await interaction.edit_original_response(
                        content=(
                            f"📄 **{stage}**\n"
                            f"標題：{truncate_text(title, 100)}\n"
                            f"進度：`[{bar}]` **{percent:.1f}%** ({current}/{total_pages} 頁)"
                        )
                    )

                await update_progress(0, total_pages, "準備下載")
                cloud_url, size_mb = await self._download_pdf(data, book_id, update_progress)
                save_cached_download(book_id, title, cloud_url, size_mb)

                # 🌐 顯示層翻譯：快取寫入的仍是原文書名（上一行），這裡只翻顯示用的字串
                title_display = await _tr_title(title, limit=240)
                embed = discord.Embed(
                    title=f"📄 {truncate_text(title_display, 240)}",
                    description="✅ PDF 已建立並上傳至雲端。",
                    color=discord.Color.green(),
                )
                embed.add_field(name="🗂️ 檔案大小", value=f"`{size_mb:.1f} MB`", inline=True)
                embed.add_field(name="📄 頁數", value=pick_num_pages(data), inline=True)
                embed.add_field(name="🔗 下載連結", value=f"[點我下載 PDF]({cloud_url})", inline=False)
                await interaction.followup.send(embed=embed)
                await interaction.edit_original_response(content="✅ 處理完成。")
                log.info(f"✅ [nhentai] 下載完成 book_id={book_id} size_mb={size_mb:.1f}")
        except Exception as error:
            log.exception(f"❌ [nhentai] 下載失敗 book_id={book_id}: {error}")
            await interaction.edit_original_response(content="❌ 處理失敗，請稍後再試。")
        finally:
            await DOWNLOAD_REGISTRY.release(book_id)

    # ==========================================
    # 📚 批量查詢（一次查多個作品）
    # ==========================================
    @app_commands.command(name="nhbatch", description=f"批量查詢多個 nHentai 作品的資訊（一次最多 {MAX_BATCH_IDS} 本）")
    @app_commands.describe(ids_or_urls="多個本子 ID 或網址，用空白、逗號或換行分隔")
    @app_commands.checks.cooldown(1, 30.0, key=lambda i: i.user.id)
    async def nh_batch(self, interaction: discord.Interaction, ids_or_urls: str):
        if not is_nsfw_allowed(interaction.channel):
            await interaction.response.send_message(
                "❌ 此指令包含成人內容，請前往 **開啟年齡限制 (NSFW)** 的頻道中使用！",
                ephemeral=True,
            )
            return

        book_ids, dropped, invalid = parse_book_id_list(ids_or_urls)
        if not book_ids:
            await interaction.response.send_message(
                "❌ 沒有解析到任何有效的本子 ID。\n"
                "請用空白、逗號或換行分隔，例如 `123456 234567` 或 `https://nhentai.net/g/123456/`",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True)
        await interaction.edit_original_response(
            content=f"📚 開始批量查詢 **{len(book_ids)}** 本，請稍候…"
                    f"（每本間隔 {BATCH_QUERY_INTERVAL} 秒，避免觸發 API 限流）"
        )

        fields = []
        success = failed = 0

        for index, book_id in enumerate(book_ids, start=1):
            try:
                data = await self._api_get(f"/galleries/{book_id}")
                title = pick_title(data)
                title_data = data.get("title")
                if isinstance(title_data, dict) and title_data.get("english"):
                    title = str(title_data["english"])
                try:
                    favorites = f"{int(data.get('num_favorites', 0)):,}"
                except (TypeError, ValueError):
                    favorites = "未知"

                # 🌐 批量查詢是每本一行的緊湊格式，這裡只翻書名，
                # 標籤仍走本地字典（0ms、不打 API），避免 10 本 × 多欄位把 API 打爆。
                title_display = await _tr_title(title, limit=60)
                fields.append((
                    f"#{book_id} · {truncate_text(title_display, 60)}",
                    f"👤 {await _tr_tags(tag_names(data, 'artist'), 200)}\n"
                    f"📄 {pick_num_pages(data)} 頁　❤️ {favorites}\n"
                    f"🏷️ {await _tr_tags(tag_names(data, 'tag'), 500)}\n"
                    f"🔗 [開啟原站](https://nhentai.net/g/{book_id}/)",
                ))
                success += 1
            except Exception as e:
                fields.append((f"❌ #{book_id}", f"查詢失敗：{truncate_text(str(e), 200)}"))
                failed += 1
                log.warning(f"⚠️ [nhentai] 批量查詢單筆失敗 book_id={book_id}: {e}")

            if index < len(book_ids):   # 最後一筆不用再多等
                await asyncio.sleep(BATCH_QUERY_INTERVAL)

        # 依 Discord 的 field 數量／長度限制自動分頁
        summary = f"共查詢 **{success + failed}** 本　｜　✅ 成功 {success}　❌ 失敗 {failed}"
        notes = []
        if dropped:
            notes.append(f"⚠️ 另有 {dropped} 個超出單次上限（{MAX_BATCH_IDS} 本），已忽略")
        if invalid:
            notes.append(f"⚠️ {invalid} 個項目無法解析成 ID，已略過")
        if notes:
            summary += "\n" + "\n".join(notes)

        groups = pack_embed_fields(fields)
        embeds = []
        for index, group in enumerate(groups[:10], start=1):
            embed = discord.Embed(
                title="📚 nHentai 批量查詢結果",
                description=summary if index == 1 else None,
                color=discord.Color.purple(),
            )
            for name, value in group:
                embed.add_field(name=name, value=value, inline=False)
            if len(groups) > 1:
                embed.set_footer(text=f"第 {index} / {len(groups)} 頁")
            embeds.append(embed)

        await interaction.edit_original_response(content=None, embeds=embeds)
        log.info(f"✅ [nhentai] 批量查詢完成 成功={success} 失敗={failed} user={interaction.user.id}")

    # ==========================================
    # ⚠️ 統一錯誤處理
    # ==========================================
    async def cog_app_command_error(self, interaction: discord.Interaction,
                                    error: app_commands.AppCommandError):
        """🆕 原本這個 cog 沒有錯誤處理，冷卻觸發時例外會往上丟到 tree.on_error，
        使用者只會看到 Discord 的「應用程式未回應」，完全不知道發生什麼事。"""
        if isinstance(error, app_commands.CommandOnCooldown):
            # 這則訊息刻意保持通用：本 cog 有冷卻的不只 /nhbatch，
            # /nhv（10 秒）與 /nh（30 秒）也有，寫死「批次查詢」會誤導。
            await safe_respond(
                interaction,
                f"⏳ 指令冷卻中，請於 **{error.retry_after:.1f}** 秒後再試一次。",
            )
            return
        log.error(f"❌ [nhentai 指令] 未預期的指令錯誤：{error!r}")
        await safe_respond(interaction, "❌ 指令執行時發生未預期錯誤，請稍後再試。")

    async def cog_unload(self):
        if self.session and not self.session.closed:
            await self.session.close()

async def setup(bot: commands.Bot):
    await bot.add_cog(NhentaiCog(bot))
