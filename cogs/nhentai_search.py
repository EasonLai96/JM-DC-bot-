# -*- coding: utf-8 -*-
"""Text search and random recommendations for the nHentai v2 API."""
import asyncio
from typing import Any, Dict, List, Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

# 🛠️ 修復：原本是 `from nhentai import ...`（頂層匯入）。因為 help_command.py 會把
# `cogs/` 加進 sys.path，這行實際上會把 **cogs/nhentai.py 再匯入成一個獨立的頂層模組
# `nhentai`**，於是記憶體裡同時存在 `cogs.nhentai`（main.py 載入的正牌 cog）與 `nhentai`
# 兩份 —— 兩份各有自己的 DOWNLOAD_REGISTRY、API_KEY 與模組層狀態，等於：
#   1) 這裡拿到的輔助函式來自「另一份」模組；
#   2) 那一份被匯入時 __package__ 是空的，模組內的相對匯入全部失效
#      （正式環境的 log 就出現了「⚠️ [收藏] 無法載入收藏按鈕，/nhv 將不顯示收藏按鈕:
#        ImportError('attempted relative import with no known parent package')」）。
# 改成匯入正牌的那一份（cogs.nhentai）之後，兩邊共用同一個模組物件，問題根除。
from cogs.nhentai import (
    API_KEY,
    NHENTAI_API_BASE,
    format_values,
    pick_num_pages,
    pick_title,
    pick_upload_time,
    tag_names,
    truncate_text,
)

try:
    from logger_config import log
except ImportError:
    import logging
    log = logging.getLogger(__name__)

from utils import is_nsfw_allowed

try:
    # 🌐 顯示層翻譯（cogs/translate.py）。用 try/except 包住：翻譯是附加功能，
    # 載入失敗也絕不能讓搜尋整支 cog 掛掉；失敗時下面的包裝會直接顯示原文。
    from cogs import translate as _TR
except Exception:
    try:
        import translate as _TR  # type: ignore[no-redef]
    except Exception as _tr_err:  # pragma: no cover
        _TR = None
        log.warning(f"⚠️ [翻譯] 無法載入翻譯模組，將只顯示原文: {_tr_err!r}")


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


RESULTS_PER_EMBED = 10


def is_nsfw_channel(interaction: discord.Interaction) -> bool:
    """判斷這個頻道是否允許成人內容（True = 允許）。

    🛠️ 修復：原本是 `not hasattr(channel, "nsfw") or channel.nsfw`，但 discord.py 的
    Thread 沒有 nsfw 屬性 → 在一般頻道底下開的討論串裡 hasattr 會是 False →
    整段 NSFW 檢查被跳過。現在統一委派給 utils.is_nsfw_allowed()，
    它會改看討論串的 parent 頻道設定（所有 cog 共用同一套邏輯）。
    """
    return is_nsfw_allowed(getattr(interaction, "channel", None))


def search_title(result: Dict[str, Any]) -> str:
    return str(result.get("english_title") or result.get("japanese_title") or "未知標題")


class RandomRecommendationView(discord.ui.View):
    """Lets members replace a random recommendation without issuing another slash command."""
    def __init__(self, cog: "NhentaiSearchCog") -> None:
        super().__init__(timeout=900)
        self.cog = cog

    @discord.ui.button(label="🎲 Random", style=discord.ButtonStyle.primary)
    async def random_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not is_nsfw_channel(interaction):
            await interaction.response.send_message(
                "❌ 此指令包含成人內容，請前往 **開啟年齡限制 (NSFW)** 的頻道中使用！",
                ephemeral=True,
            )
            return

        await interaction.response.defer()
        try:
            async with self.cog.random_lock:
                embed, gallery_id = await self.cog.build_random_embed()
            await interaction.message.edit(embed=embed, view=self)
            log.info(f"✅ [nhentai] 按鈕隨機推薦 user={interaction.user.id} id={gallery_id}")
        except (PermissionError, RuntimeError, ValueError, FileNotFoundError) as error:
            await interaction.followup.send(f"❌ 推薦失敗：{error}", ephemeral=True)
        except Exception:
            log.exception("❌ [nhentai] 按鈕隨機推薦發生未預期錯誤")
            await interaction.followup.send("❌ 推薦失敗，請稍後再試。", ephemeral=True)


class NhentaiSearchCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: Optional[aiohttp.ClientSession] = None
        self.random_lock = asyncio.Lock()

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DiscordBot/1.0",
                    "Accept": "application/json",
                },
                timeout=aiohttp.ClientTimeout(total=20),
            )
        return self.session

    async def _api_get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        session = await self._get_session()
        headers = {"Accept": "application/json"}
        if API_KEY:
            headers["Authorization"] = f"Key {API_KEY}"

        async with session.get(f"{NHENTAI_API_BASE}{endpoint}", params=params, headers=headers) as response:
            if response.status == 401:
                raise PermissionError("API key 無效或未授權。")
            if response.status == 403:
                raise PermissionError("API 拒絕此請求，請稍後再試。")
            if response.status == 404:
                raise FileNotFoundError("找不到作品。")
            if response.status == 429:
                raise RuntimeError("查詢過於頻繁，請稍候再試。")
            if response.status != 200:
                raise RuntimeError(f"API 呼叫失敗（HTTP {response.status}）。")
            data = await response.json()
            if not isinstance(data, dict):
                raise ValueError("API 回傳格式異常。")
            return data

    async def build_random_embed(self) -> tuple[discord.Embed, str]:
        """Fetch one random gallery and render its text-only recommendation card."""
        random_result = await self._api_get("/galleries/random")
        gallery_id = str(random_result.get("id") or "")
        if not gallery_id.isdigit():
            raise ValueError("隨機推薦沒有回傳有效作品 ID。")

        data = await self._api_get(f"/galleries/{gallery_id}")
        title = pick_title(data)
        title_data = data.get("title")
        japanese_title = ""
        if isinstance(title_data, dict):
            title = str(title_data.get("english") or title)
            japanese_title = str(title_data.get("japanese") or "")

        try:
            favorites = f"{int(data.get('num_favorites', 0)):,}"
        except (TypeError, ValueError):
            favorites = "未知"

        # 🌐 隨機推薦卡片：書名翻成「中文（原文）」，標籤走本地字典查表
        title_display = await _tr_title(title, limit=240)
        embed = discord.Embed(
            title=f"🎲 隨機推薦・{truncate_text(title_display, 240)}",
            url=f"https://nhentai.net/g/{gallery_id}/",
            description="隨機選出的作品。",
            color=discord.Color.purple(),
        )
        if japanese_title and japanese_title != title:
            japanese_display = await _tr_title(japanese_title, limit=240)
            embed.add_field(
                name="🇯🇵 日文標題",
                value=truncate_text(japanese_display, 900),
                inline=False,
            )
        embed.add_field(name="🆔 ID", value=f"`{gallery_id}`", inline=True)
        embed.add_field(name="📄 頁數", value=pick_num_pages(data), inline=True)
        embed.add_field(name="❤️ 收藏數", value=favorites, inline=True)
        embed.add_field(name="👤 作者", value=await _tr_tags(tag_names(data, "artist")), inline=False)
        embed.add_field(name="🏷️ 標籤", value=await _tr_tags(tag_names(data, "tag")), inline=False)
        embed.add_field(name="🗣️ 語言", value=await _tr_tags(tag_names(data, "language")), inline=True)
        embed.add_field(name="🕒 上傳時間", value=pick_upload_time(data), inline=True)
        embed.add_field(name="🌐 原站連結", value=f"[點我開啟](https://nhentai.net/g/{gallery_id}/)", inline=False)
        embed.set_footer(text=f"使用 /nhv {gallery_id} 查看完整資訊；使用 /nh {gallery_id} 建立 PDF。")
        return embed, gallery_id

    @app_commands.command(name="nhsearch", description="以關鍵字搜尋 nHentai 作品")
    @app_commands.describe(keyword="標題、角色、作者或標籤關鍵字", page="結果頁碼，預設第 1 頁")
    @app_commands.checks.cooldown(1, 8.0, key=lambda interaction: interaction.user.id)
    async def nh_search(self, interaction: discord.Interaction, keyword: str, page: app_commands.Range[int, 1, 100] = 1):
        if not is_nsfw_channel(interaction):
            await interaction.response.send_message(
                "❌ 此指令包含成人內容，請前往 **開啟年齡限制 (NSFW)** 的頻道中使用！",
                ephemeral=True,
            )
            return

        keyword = keyword.strip()
        if not keyword:
            await interaction.response.send_message("❌ 請輸入搜尋關鍵字。", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        try:
            data = await self._api_get("/search", {"query": keyword, "page": page})
            results = data.get("result", [])
            if not isinstance(results, list) or not results:
                await interaction.followup.send(f"🔎 找不到「{truncate_text(keyword, 100)}」的作品。", ephemeral=True)
                return

            total = data.get("total", len(results))
            total_pages = data.get("num_pages", 1)
            embed = discord.Embed(
                title=f"🔎 搜尋結果：{truncate_text(keyword, 200)}",
                description=f"第 {page}/{total_pages} 頁，共 {total} 筆結果。顯示前 {min(RESULTS_PER_EMBED, len(results))} 筆。",
                color=discord.Color.orange(),
            )

            for result in results[:RESULTS_PER_EMBED]:
                if not isinstance(result, dict) or "id" not in result:
                    continue
                gallery_id = str(result["id"])
                # 🌐 搜尋結果一次最多 10 筆，逐筆翻書名會慢；但這裡是使用者最需要
                # 看懂的地方，所以仍然翻（translate 有快取，重複查詢不會再打 API）。
                title = truncate_text(
                    await _tr_title(search_title(result), limit=220), 220
                )
                pages = result.get("num_pages", "未知")
                embed.add_field(
                    name=f"#{gallery_id}・{title}",
                    value=f"📄 {pages} 頁　[查看作品](https://nhentai.net/g/{gallery_id}/)",
                    inline=False,
                )

            embed.set_footer(text="使用 /nhv <ID> 查看完整資訊；使用 /nh <ID> 建立 PDF。")
            await interaction.followup.send(embed=embed)
            log.info(f"✅ [nhentai] 搜尋 user={interaction.user.id} keyword={keyword!r} page={page}")
        except (PermissionError, RuntimeError, ValueError) as error:
            await interaction.followup.send(f"❌ 搜尋失敗：{error}", ephemeral=True)
        except Exception:
            log.exception("❌ [nhentai] 搜尋發生未預期錯誤")
            await interaction.followup.send("❌ 搜尋失敗，請稍後再試。", ephemeral=True)

    @app_commands.command(name="nhrandom", description="隨機推薦一部 nHentai 作品")
    @app_commands.checks.cooldown(1, 10.0, key=lambda interaction: interaction.user.id)
    async def nh_random(self, interaction: discord.Interaction):
        if not is_nsfw_channel(interaction):
            await interaction.response.send_message(
                "❌ 此指令包含成人內容，請前往 **開啟年齡限制 (NSFW)** 的頻道中使用！",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True)
        try:
            async with self.random_lock:
                embed, gallery_id = await self.build_random_embed()
            await interaction.followup.send(embed=embed, view=RandomRecommendationView(self))
            log.info(f"✅ [nhentai] 隨機推薦 user={interaction.user.id} id={gallery_id}")
        except (PermissionError, RuntimeError, ValueError, FileNotFoundError) as error:
            await interaction.followup.send(f"❌ 推薦失敗：{error}", ephemeral=True)
        except Exception:
            log.exception("❌ [nhentai] 隨機推薦發生未預期錯誤")
            await interaction.followup.send("❌ 推薦失敗，請稍後再試。", ephemeral=True)

    async def cog_unload(self):
        if self.session and not self.session.closed:
            await self.session.close()


async def setup(bot: commands.Bot):
    await bot.add_cog(NhentaiSearchCog(bot))
