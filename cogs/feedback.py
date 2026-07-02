# -*- coding: utf-8 -*-
"""
直通總設計者 / 使用者全域回饋模組 (獨立頻道分流版)
------------------------------------------------
功能：
  /feedback               [所有人可用] 彈出表單填寫意見，跨服空投至設計者的獨立回饋頻道。
  /set_feedback_channel   [僅限開發者] 隨時更改或設定接收回饋的獨立專屬頻道。
"""
import os
import json
from typing import Optional

import discord
from discord.ext import commands
from discord import app_commands

from config import current_dir
from logger_config import log

# ─────────────────────────────────────────────
# 分類與獨立設定檔路徑
# ─────────────────────────────────────────────
CATEGORY_STYLES = {
    "bug": {"label": "🐛 Bug 回報", "color": discord.Color.red()},
    "suggestion": {"label": "💡 功能建議", "color": discord.Color.blue()},
    "other": {"label": "📨 其他意見", "color": discord.Color.greyple()},
}

# 💥 獨立的儲存設定檔，徹底與普通的 log_channel.json 分開
FEEDBACK_CONFIG_FILE = os.path.join(current_dir, 'global_feedback_channel.json')

def save_feedback_config(channel_id: int):
    """儲存全域回饋頻道 ID"""
    with open(FEEDBACK_CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump({'channel_id': channel_id}, f)

def load_feedback_config() -> Optional[int]:
    """讀取全域回饋頻道 ID"""
    if os.path.exists(FEEDBACK_CONFIG_FILE):
        try:
            with open(FEEDBACK_CONFIG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get('channel_id')
        except Exception as e:
            log.error(f"❌ [直通回饋] 讀取回饋頻道設定失敗: {e}")
    return None


class FeedbackModal(discord.ui.Modal):
    def __init__(self, category: str, bot: commands.Bot, attachment_url: Optional[str] = None):
        style = CATEGORY_STYLES.get(category, {"label": "📨 意見反應", "color": discord.Color.blue()})
        super().__init__(title=f"提交 - {style['label']}")
        self.category = category
        self.bot = bot
        self.attachment_url = attachment_url

        self.fb_content = discord.ui.TextInput(
            label="請輸入您的詳細意見或回報內容：",
            style=discord.TextStyle.paragraph,
            placeholder="請詳細說明，以便總設計者為您處理...",
            required=True,
            max_length=1500
        )
        self.add_item(self.fb_content)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        # 1. 讀取獨立的回饋專用頻道
        target_channel_id = load_feedback_config()
        if not target_channel_id:
            await interaction.followup.send("❌ 系統發送失敗：總設計者尚未設定「回饋接收頻道」！請先使用 `/set_feedback_channel` 設定。", ephemeral=True)
            return

        try:
            channel = self.bot.get_channel(target_channel_id) or await self.bot.fetch_channel(target_channel_id)
        except Exception:
            channel = None

        if not channel:
            await interaction.followup.send("❌ 系統發送失敗：找不到設定的獨立回饋頻道，可能已被刪除或權限不足。", ephemeral=True)
            return

        style = CATEGORY_STYLES.get(self.category, {"label": "📨 意見反應", "color": discord.Color.blue()})
        guild_name = interaction.guild.name if interaction.guild else "🔒 使用者私訊 DM"
        
        # 2. 建立送往獨立頻道的精美大 Embed 看板（格式跟普通 Log 區開）
        embed = discord.Embed(
            title=f"📬 【跨服直通】收到使用者意見回饋",
            color=style["color"],
            timestamp=interaction.created_at
        )
        embed.add_field(name="📌 回饋分類", value=style["label"], inline=True)
        embed.add_field(name="👤 提交使用者", value=f"{interaction.user.mention} (`{interaction.user.name}`)", inline=True)
        embed.add_field(name="🏰 來源伺服器", value=guild_name, inline=True)
        embed.add_field(name="💬 詳細內容", value=f"```\n{self.fb_content.value}\n```", inline=False)

        if self.attachment_url:
            embed.set_image(url=self.attachment_url)
            embed.add_field(name="📎 附加檔案", value=f"[點我查看原檔]({self.attachment_url})", inline=False)

        embed.set_footer(text=f"User ID: {interaction.user.id}")

        try:
            await channel.send(embed=embed)
            await interaction.followup.send("✨ 謝謝你的回饋！您的意見已直接直通發送給總設計者。", ephemeral=True)
            log.info(f"📨 [直通回饋] 成功將 {interaction.user.name} 的回饋空投至獨立接收頻道。")
        except Exception as e:
            log.error(f"❌ [直通回饋] 轉發至獨立頻道失敗: {e}")
            await interaction.followup.send(f"❌ 發送失敗，技術錯誤：`{e}`", ephemeral=True)


class FeedbackCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ─────────────────────────────────────────────
    # 1. 所有人皆可用的回饋指令 (支援私訊與任何伺服器)
    # ─────────────────────────────────────────────
    @app_commands.command(name="feedback", description="向總設計者回報 Bug、提交功能建議或提出其他意見")
    @app_commands.describe(
        category="請選擇回饋的分類",
        attachment="可選擇性附加一張螢幕截圖或相關檔案"
    )
    @app_commands.choices(category=[
        app_commands.Choice(name="🐛 Bug 回報 (功能故障/噴錯誤碼)", value="bug"),
        app_commands.Choice(name="💡 功能建議 (希望加入新功能或優化)", value="suggestion"),
        app_commands.Choice(name="📨 其他意見 (合作、留言或其他)", value="other")
    ])
    @app_commands.checks.cooldown(1, 30.0, key=lambda i: (i.user.id))
    async def feedback(
        self,
        interaction: discord.Interaction,
        category: app_commands.Choice[str],
        attachment: Optional[discord.Attachment] = None
    ):
        modal = FeedbackModal(category=category.value, bot=self.bot, attachment_url=attachment.url if attachment else None)
        await interaction.response.send_modal(modal)

    # ─────────────────────────────────────────────
    # 2. 💥 【全新加入】開發者專用：隨時更改獨立回饋頻道的指令
    # ─────────────────────────────────────────────
    @app_commands.command(name="set_feedback_channel", description="【總設計者專用】設定或隨時變更接收使用者回饋的獨立頻道")
    @app_commands.describe(channel="要將使用者回饋發送到的專屬頻道")
    async def set_feedback_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        # 嚴格驗證是否為機器人擁有者（你）
        if not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message("❌ 只有機器人總設計者（Owner）才能使用此管理指令！", ephemeral=True)
            return

        # 檢查權限
        perms = channel.permissions_for(channel.guild.me)
        if not perms.send_messages or not perms.embed_links:
            await interaction.response.send_message(f"❌ 我在 {channel.mention} 沒有「發送訊息」或「嵌入連結」的權限！", ephemeral=True)
            return

        # 儲存到獨立的設定檔
        save_feedback_config(channel.id)
        await interaction.response.send_message(f"🎯 成功綁定！全域使用者回饋管道已切換至獨立頻道：{channel.mention}\n*（此頻道將專門接收回饋，不會與普通的控制台 Log 混在一起！）*", ephemeral=True)
        log.info(f"⚙️ [直通回饋] 總設計者已將回饋接收頻道變更為: {channel.name} ({channel.id})")

    # ───────────────────────────────
    # 統一錯誤處理
    # ───────────────────────────────
    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CommandOnCooldown):
            await interaction.response.send_message(f"⏳ 回饋冷卻中，請於 {error.retry_after:.1f} 秒後再試一次，請勿頻繁發送唷！", ephemeral=True)
        else:
            log.error(f"❌ [直通回饋] 未預期的指令錯誤：{error}")
            try:
                await interaction.response.send_message(f"❌ 發生未知錯誤：{error}", ephemeral=True)
            except:
                pass

async def setup(bot: commands.Bot):
    await bot.add_cog(FeedbackCog(bot))