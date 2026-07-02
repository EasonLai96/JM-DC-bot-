# -*- coding: utf-8 -*-
import os
import json
import discord
from discord.ext import commands
from discord import app_commands
from logger_config import log, discord_relay_handler

CONFIG_FILE = 'log_channel.json'


def save_config(guild_id: int, channel_id: int):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump({'guild_id': guild_id, 'channel_id': channel_id}, f)


def clear_config():
    if os.path.exists(CONFIG_FILE):
        os.remove(CONFIG_FILE)


def load_config():
    if not os.path.exists(CONFIG_FILE):
        return None
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


class LogRelayCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        # 🔄 bot 重啟後自動還原上次設定的轉發頻道，不用每次重新 /setlogchannel
        cfg = load_config()
        if not cfg:
            return
        try:
            channel = self.bot.get_channel(cfg['channel_id']) or await self.bot.fetch_channel(cfg['channel_id'])
            discord_relay_handler.set_target(self.bot, channel)
            log.info(f"📡 [Log 轉發] 已自動還原轉發頻道：#{channel.name} ({channel.guild.name})")
        except Exception as e:
            log.warning(f"⚠️ [Log 轉發] 還原轉發頻道失敗（頻道可能已被刪除）: {e}")

    @app_commands.command(name='setlogchannel', description='【開發者專用】設定即時 log 轉發到的頻道')
    @app_commands.describe(channel='要接收 log 的頻道（留空則關閉轉發）')
    async def set_log_channel(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        if not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message("❌ 只有機器人開發者（Owner）才能使用此指令！", ephemeral=True)
            return

        if channel is None:
            discord_relay_handler.clear_target()
            clear_config()
            await interaction.response.send_message("🔌 已關閉 log 轉發功能。", ephemeral=True)
            log.info(f"📡 [Log 轉發] 已由 {interaction.user.name} 關閉轉發功能")
            return

        perms = channel.permissions_for(channel.guild.me)
        if not perms.send_messages:
            await interaction.response.send_message(f"❌ 我在 {channel.mention} 沒有發送訊息的權限！", ephemeral=True)
            return

        discord_relay_handler.set_target(self.bot, channel)
        save_config(channel.guild.id, channel.id)

        await interaction.response.send_message(
            f"✅ 已設定 log 即時轉發到 {channel.mention}！\n"
            f"⚠️ 注意：這會把**所有**終端機 log（含下載進度、RAM 狀態等高頻訊息）都同步過來，訊息量會很大。",
            ephemeral=True
        )
        log.info(f"📡 [Log 轉發] 已由 {interaction.user.name} 設定轉發頻道：#{channel.name} ({channel.guild.name})")


async def setup(bot):
    await bot.add_cog(LogRelayCog(bot))