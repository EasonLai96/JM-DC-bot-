# -*- coding: utf-8 -*-
"""⭐ 本子收藏（我的最愛）指令模組。

禁漫（JM）與 nhentai（NH）**共用同一個收藏庫**，每一筆用來源前綴區分：
`JM:123456` / `NH:654321`。詳細的資料格式與存取邏輯都在 favorites_store.py。

指令：
  • `/favorite`        —— 收藏一本（可加備註）
  • `/unfavorite`      —— 取消收藏
  • `/favorites`       —— 查看收藏清單（可分頁、可查別人）
  • `/favorites_clear` —— 清空自己的收藏（需二次確認）
"""
import datetime
from typing import Optional, List

import discord
from discord import app_commands
from discord.ext import commands

from logger_config import log
from utils import is_nsfw_allowed, safe_respond

from . import favorites_store

# 一頁顯示幾筆（embed description 上限 4096 字，8 筆 × 約 170 字最安全）
PAGE_SIZE = 8

# 指令選項用不到的常數集中在這裡，方便日後調整
NOTE_MAX_LEN = 80

SOURCE_CHOICES = [
    app_commands.Choice(name="禁漫天堂 (JM)", value="JM"),
    app_commands.Choice(name="nhentai (NH)", value="NH"),
]


def _safe_label(text: str, limit: int = 60) -> str:
    """把外部標題處理成可以安全放進 markdown 連結標籤的字串。

    ⚠️ 標題來自禁漫／nhentai 的 API（等於第三方可控文字），而我們要把它放進
    `[標題](網址)`。如果標題裡含有 `](`，就能把連結「拆開」插入自己的網址。
    `escape_markdown` 會處理掉 `[..](..)` 這種組合，這裡再保險把殘餘的
    中括號換成全形，確保標籤不可能逃出連結語法。
    """
    if not text:
        return ""
    safe = discord.utils.escape_markdown(str(text))
    safe = safe.replace("[", "［").replace("]", "］")
    return safe[:limit]


def _entry_line(index: int, item: dict) -> str:
    """把一筆收藏排成清單中的一行（含備註與加入日期）。"""
    key = item["key"]
    url = favorites_store.book_url(item["source"], item["book_id"])
    title = item.get("title") or ""

    label = _safe_label(title, 52) if title else f"本子 {item['book_id']}"
    line = f"**{index}.** [{label}]({url})　`{key}`"

    added = item.get("added") or 0
    if added > 0:
        date_text = datetime.datetime.fromtimestamp(added).strftime("%Y-%m-%d")
        line += f"　🕒 {date_text}"

    note = item.get("note")
    if note:
        line += f"\n　　📝 {_safe_label(note, NOTE_MAX_LEN)}"
    return line


def _build_pages(items: List[dict], owner_display: str, source_name: str, is_self: bool) -> List[discord.Embed]:
    """把收藏清單切成多個 embed 頁面。"""
    jm_count = sum(1 for it in items if it["source"] == "JM")
    nh_count = sum(1 for it in items if it["source"] == "NH")

    chunks = [items[i:i + PAGE_SIZE] for i in range(0, len(items), PAGE_SIZE)]
    pages: List[discord.Embed] = []
    for page_index, chunk in enumerate(chunks, start=1):
        embed = discord.Embed(
            title=("⭐ 我的收藏" if is_self else f"⭐ {owner_display} 的收藏") + f"（{source_name}）",
            color=discord.Color.from_rgb(255, 200, 60),
        )
        description = "\n".join(
            _entry_line((page_index - 1) * PAGE_SIZE + offset, item)
            for offset, item in enumerate(chunk, start=1)
        )
        # 防禦性截斷：8 筆 + 備註的理論最大值仍遠低於 4096，但寧可截斷也不要整則送不出去
        embed.description = description[:4000]
        embed.set_footer(
            text=f"共 {len(items)} 筆（JM {jm_count} ／ NH {nh_count}）"
                 f"　｜　第 {page_index}/{len(chunks)} 頁"
                 f"　｜　用 /unfavorite 可移除收藏"
        )
        pages.append(embed)
    return pages


class FavoritesPager(discord.ui.View):
    """收藏清單的分頁按鈕（只有原使用者能按）。"""

    def __init__(self, pages: List[discord.Embed], owner_id: int):
        super().__init__(timeout=180)
        self.pages = pages
        self.owner_id = owner_id
        self.index = 0
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        last = len(self.pages) - 1
        self.first_page.disabled = self.index == 0
        self.prev_page.disabled = self.index == 0
        self.next_page.disabled = self.index >= last
        self.last_page.disabled = self.index >= last
        self.page_counter.label = f"{self.index + 1} / {len(self.pages)}"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "❌ 這不是你的收藏清單，請用 `/favorites` 查詢自己的。", ephemeral=True
            )
            return False
        return True

    async def _goto(self, interaction: discord.Interaction, new_index: int) -> None:
        self.index = max(0, min(new_index, len(self.pages) - 1))
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @discord.ui.button(emoji="⏮️", style=discord.ButtonStyle.secondary)
    async def first_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._goto(interaction, 0)

    @discord.ui.button(emoji="◀️", style=discord.ButtonStyle.primary)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._goto(interaction, self.index - 1)

    @discord.ui.button(label="1 / 1", style=discord.ButtonStyle.secondary, disabled=True)
    async def page_counter(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 純顯示用；理論上按不到（disabled），留著是為了讓使用者一眼看到頁碼
        await interaction.response.defer()

    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.primary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._goto(interaction, self.index + 1)

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary)
    async def last_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._goto(interaction, len(self.pages) - 1)


class ClearConfirmView(discord.ui.View):
    """清空收藏前的二次確認（刪除是不可逆動作，不該一個指令就清掉）。"""

    def __init__(self, owner_id: int):
        super().__init__(timeout=30)
        self.owner_id = owner_id
        self.confirmed = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("❌ 這不是你的收藏，無法代替他人操作。", ephemeral=True)
            return False
        return True

    async def _finish(self, interaction: discord.Interaction, content: str) -> None:
        for child in self.children:
            child.disabled = True
        try:
            await interaction.response.edit_message(content=content, view=self)
        except Exception:
            await interaction.followup.send(content, ephemeral=True)

    @discord.ui.button(label="🗑️ 確認清空", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = True
        removed = await favorites_store.clear_favorites(interaction.user.id)
        log.info(f"⭐ [收藏] {interaction.user} ({interaction.user.id}) 清空了 {removed} 筆收藏")
        await self._finish(interaction, f"🗑️ 已清空你的收藏（共移除 **{removed}** 筆）。")
        self.stop()

    @discord.ui.button(label="❌ 取消", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._finish(interaction, "✅ 已取消，你的收藏完全沒有變動。")
        self.stop()


class FavoriteButtonView(discord.ui.View):
    """查詢結果底下的「⭐ 收藏這本」按鈕（`/jmv`、`/nhv` 共用）。

    為什麼要做成按鈕：查詢當下書名已經在手上，順手按一下就存進收藏（連標題一起），
    不用再自己把 ID 抄去打一次 `/favorite`。

    設計要點：
      • 只有「執行查詢的那個人」能按（別人在公開頻道看到同一則訊息也不能代按）。
      • 已經收藏過的話，按鈕一開始就是反灰的「✅ 已收藏」，不會讓人白按。
      • 不需要 custom_id 解析：View 是每次查詢現建的，直接用閉包把來源／ID／書名帶進去。
      • timeout 過後按鈕失效（Discord 會顯示互動失敗），此時仍可用 `/favorite` 收藏。
    """

    def __init__(self, user_id: int, source: str, book_id: str, title: str = "",
                 *, timeout: float = 600.0):
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.source = source
        self.book_id = book_id
        self.title = title
        self._busy = False

    @classmethod
    async def create(cls, user_id: int, source: str, book_id: str, title: str = "") -> "FavoriteButtonView":
        """建立按鈕；已經收藏過就直接顯示成已完成狀態。

        ⚠️ 這裡刻意「絕不拋例外」——它被放在 `/jmv`、`/nhv` 的查詢成功路徑上，
        收藏功能的任何問題都不該讓一次成功的查詢變成「查詢失敗」。
        """
        view = cls(user_id, source, book_id, title)
        try:
            if await favorites_store.is_favorite(user_id, source, book_id):
                view.add_to_favorites.label = "✅ 已在收藏中"
                view.add_to_favorites.disabled = True
        except Exception as e:
            log.warning(f"⚠️ [收藏] 檢查收藏狀態失敗（按鈕仍可使用）: {e!r}")
        return view

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "❌ 這是別人的查詢結果，請自己用 `/jmv` 或 `/nhv` 查詢後再收藏。", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="⭐ 收藏這本", style=discord.ButtonStyle.success)
    async def add_to_favorites(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self._busy:
            await interaction.response.send_message("⏳ 正在處理中，請稍候…", ephemeral=True)
            return
        self._busy = True
        try:
            status, key = await favorites_store.add_favorite(
                self.user_id, self.source, self.book_id, title=self.title
            )
        finally:
            self._busy = False

        if status in ("added", "updated", "exists"):
            button.label = "✅ 已在收藏中"
            button.disabled = True
            if status == "exists":
                detail = f"ℹ️ 這本本來就在你的收藏裡：`{key}`"
            else:
                detail = f"⭐ 已收藏 **{favorites_store.source_label(self.source)}** 的 `{key}`！"
            try:
                await interaction.response.edit_message(view=self)
            except discord.HTTPException:
                pass  # 訊息被刪或權限不足都不影響「已經收藏成功」這件事
            counts = await favorites_store.count_favorites(self.user_id)
            summary = (
                f"{detail}\n📚 目前共有 **{counts['total']}** 筆收藏"
                f"（JM {counts['JM']} ／ NH {counts['NH']}）　—— 用 `/favorites` 查看清單。"
            )
            # edit_message 已經用掉這次互動的回應額度，細節只能走 followup
            try:
                await interaction.followup.send(summary, ephemeral=True)
            except discord.HTTPException:
                pass
            return

        if status == "limit":
            summary = (
                f"❌ 你的收藏已達上限 **{favorites_store.MAX_FAVORITES_PER_USER}** 筆，"
                f"請先用 `/unfavorite` 移除一些再收藏。"
            )
        else:
            summary = "❌ 收藏時發生問題（可能是寫入檔案失敗），請稍後再試。"

        # ⚠️ 失敗路徑「還沒有回應過」這次互動，必須用 response 而不是 followup，
        # 否則 Discord 會因為互動沒有被 ACK 而顯示「應用程式未回應」。
        try:
            await interaction.response.send_message(summary, ephemeral=True)
        except discord.HTTPException:
            pass


class FavoritesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _nsfw_guard(self, interaction: discord.Interaction) -> bool:
        """成人指令一律只在 NSFW 頻道可用（與 /jm、/nh 相同規則）。"""
        if is_nsfw_allowed(interaction.channel):
            return True
        await safe_respond(interaction, "🔞 這個指令只能在 **年齡限制（NSFW）** 頻道使用！")
        return False

    # ==========================================
    # ⭐ 收藏
    # ==========================================
    @app_commands.command(name="favorite", description="⭐ 收藏一本本子（禁漫 / nhentai 共用收藏庫）")
    @app_commands.describe(
        本子="本子 ID 或完整網址，例如 JM:123456、NH:654321、https://nhentai.net/g/654321/",
        來源="純數字 ID 時必須指定來源（兩站 ID 會重複）",
        備註="給自己的提醒，例如「畫風超讚」",
    )
    @app_commands.choices(來源=SOURCE_CHOICES)
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: i.user.id)
    async def favorite(
        self,
        interaction: discord.Interaction,
        本子: str,
        來源: Optional[str] = None,
        備註: Optional[str] = None,
    ):
        if not await self._nsfw_guard(interaction):
            return

        source, book_id, error = favorites_store.parse_favorite_input(本子, 來源)
        if error:
            await safe_respond(interaction, f"❌ {error}")
            return

        status, key = await favorites_store.add_favorite(
            interaction.user.id, source, book_id, note=備註
        )
        url = favorites_store.book_url(source, book_id)
        label = favorites_store.source_label(source)

        if status == "added":
            counts = await favorites_store.count_favorites(interaction.user.id)
            await safe_respond(
                interaction,
                f"⭐ 已收藏 **{label}** 的本子 `{book_id}`！\n"
                f"🔗 {url}\n"
                f"📚 你目前共有 **{counts['total']}** 筆收藏"
                f"（JM {counts['JM']} ／ NH {counts['NH']}）　—— 用 `/favorites` 查看清單。",
            )
        elif status == "updated":
            await safe_respond(interaction, f"✅ 這本已經在收藏裡了，已幫你更新備註／標題：`{key}`")
        elif status == "exists":
            await safe_respond(interaction, f"ℹ️ 這本已經在你的收藏裡了：`{key}`")
        elif status == "limit":
            await safe_respond(
                interaction,
                f"❌ 你的收藏已達上限 **{favorites_store.MAX_FAVORITES_PER_USER}** 筆，"
                f"請先用 `/unfavorite` 移除一些再收藏。",
            )
        else:
            await safe_respond(interaction, "❌ 收藏時發生問題（可能是寫入檔案失敗），請稍後再試。")
            log.error(f"❌ [收藏] add_favorite 回傳 invalid：user={interaction.user.id} input={本子!r}")

    # ==========================================
    # 🗑️ 取消收藏
    # ==========================================
    @app_commands.command(name="unfavorite", description="🗑️ 取消收藏一本本子")
    @app_commands.describe(
        本子="本子 ID 或完整網址（也接受 JM:123456 / NH:654321）",
        來源="純數字 ID 時必須指定來源",
    )
    @app_commands.choices(來源=SOURCE_CHOICES)
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: i.user.id)
    async def unfavorite(
        self,
        interaction: discord.Interaction,
        本子: str,
        來源: Optional[str] = None,
    ):
        if not await self._nsfw_guard(interaction):
            return

        source, book_id, error = favorites_store.parse_favorite_input(本子, 來源)
        if error:
            await safe_respond(interaction, f"❌ {error}")
            return

        removed = await favorites_store.remove_favorite(interaction.user.id, source, book_id)
        if removed:
            counts = await favorites_store.count_favorites(interaction.user.id)
            await safe_respond(
                interaction,
                f"🗑️ 已從收藏移除 `{source}:{book_id}`。\n"
                f"📚 目前還有 **{counts['total']}** 筆收藏。",
            )
        else:
            await safe_respond(interaction, f"ℹ️ 這本本來就不在你的收藏裡：`{source}:{book_id}`")

    # ==========================================
    # 📖 收藏清單
    # ==========================================
    @app_commands.command(name="favorites", description="📖 查看收藏清單（可查自己或其他人的）")
    @app_commands.describe(
        使用者="要查看誰的收藏（留空 = 自己）",
        來源="只看某個站台的收藏（留空 = 全部）",
    )
    @app_commands.choices(來源=SOURCE_CHOICES)
    async def favorites(
        self,
        interaction: discord.Interaction,
        使用者: Optional[discord.User] = None,
        來源: Optional[str] = None,
    ):
        if not await self._nsfw_guard(interaction):
            return

        target = 使用者 or interaction.user
        items = await favorites_store.list_favorites(target.id, 來源)

        if not items:
            who = "你" if target.id == interaction.user.id else f"{target.display_name} "
            extra = f"（{favorites_store.source_label(來源)}）" if 來源 else ""
            await safe_respond(
                interaction,
                f"📭 {who}還沒有任何收藏{extra}。\n"
                f"用 `/favorite 本子:<ID或網址>` 就能把喜歡的本子加進來！",
            )
            return

        source_name = favorites_store.source_label(來源) if 來源 else "全部來源"
        pages = _build_pages(items, target.display_name, source_name, target.id == interaction.user.id)

        if len(pages) == 1:
            await safe_respond(interaction, embed=pages[0])
            return

        view = FavoritesPager(pages, interaction.user.id)
        try:
            await interaction.response.send_message(embed=pages[0], view=view, ephemeral=True)
        except discord.HTTPException as e:
            # 分頁按鈕送不出去（例如互動已逾時）時，至少把第一頁內容給使用者
            log.warning(f"⚠️ [收藏] 分頁訊息送出失敗，改為單頁顯示: {e!r}")
            await safe_respond(interaction, embed=pages[0])

    # ==========================================
    # 🧹 清空收藏
    # ==========================================
    @app_commands.command(name="favorites_clear", description="🧹 清空自己的所有收藏（會先請你確認）")
    async def favorites_clear(self, interaction: discord.Interaction):
        if not await self._nsfw_guard(interaction):
            return

        counts = await favorites_store.count_favorites(interaction.user.id)
        if counts["total"] == 0:
            await safe_respond(interaction, "📭 你目前沒有任何收藏，不需要清空。")
            return

        view = ClearConfirmView(interaction.user.id)
        await interaction.response.send_message(
            f"⚠️ 你確定要清空全部 **{counts['total']}** 筆收藏嗎"
            f"（JM {counts['JM']} ／ NH {counts['NH']}）？\n"
            f"這個動作**無法復原**，請在 30 秒內按下方按鈕確認。",
            view=view,
            ephemeral=True,
        )

    # ==========================================
    # ⚠️ 統一錯誤處理
    # ==========================================
    async def cog_app_command_error(self, interaction: discord.Interaction,
                                    error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CommandOnCooldown):
            await safe_respond(
                interaction,
                f"⏳ 指令冷卻中，請於 **{error.retry_after:.1f}** 秒後再試一次。",
            )
            return
        log.error(f"❌ [收藏指令] 未預期的指令錯誤：{error!r}")
        await safe_respond(interaction, "❌ 指令執行時發生未預期錯誤，請稍後再試。")


async def setup(bot):
    await bot.add_cog(FavoritesCog(bot))
