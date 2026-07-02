# -*- coding: utf-8 -*-
import sys
import io
import os
import discord
from discord.ext import commands
from discord import app_commands
import traceback

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

import config
from logger_config import log  
from bot_monitor import BotMonitor
from help_command import setup_help
setup_help(bot)

@bot.event
async def on_app_command_completion(interaction: discord.Interaction, command: app_commands.AppCommand):
    guild_name = interaction.guild.name if interaction.guild else "私訊"
    user_info = f"{interaction.user.name}({interaction.user.id})"
    
    options = interaction.data.get('options', [])
    args = ", ".join([f"{opt['name']}='{opt['value']}'" for opt in options])
    
    ram_info = BotMonitor._get_ram_usage()
    log.info(f"🟢 [指令成功] 伺服器: [{guild_name}] | 使用者: {user_info} | 呼叫: /{command.name} ({args}) {ram_info}")


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    cmd_name = BotMonitor.log_command_error(interaction, error)
    
    reply_msg = f"❌ 執行指令 `/{cmd_name}` 時發生未知的內部錯誤！Bug 已回報給後台開發者。"
    try:
        if interaction.response.is_done():
            await interaction.followup.send(reply_msg, ephemeral=True)
        else:
            await interaction.response.send_message(reply_msg, ephemeral=True)
    except:
        pass

@bot.event
async def on_ready():
    log.info(f'=================================')
    log.info(f' 機器人已成功上線！')
    log.info(f' 機器人名稱: {bot.user.name} (ID: {bot.user.id})')
    log.info(f' 目前連線到 {len(bot.guilds)} 個伺服器')
    log.info(f'=================================')
    
    cogs_to_load = ['cogs.comic', 'cogs.admin', 'cogs.fixlink', 'cogs.logrelay', 'cogs.nzip', 'cogs.casino', 'cogs.feedback', 'cogs.profile_card', 'cogs.economy']  # 將 feedback_cog 加入到要載入的模組列
    for cog in cogs_to_load:
        try:
            await bot.load_extension(cog)
            log.info(f"✓ 成功載入功能模組: {cog}")
        except Exception as ce:
            log.error(f"❌ 模組 {cog} 載入失敗: {ce}")

    try:
        await bot.tree.sync()
        log.info(f"✓ 成功同步所有斜線指令！")
    except Exception as e:
        log.error(f"❌ 指令同步失敗: {e}")

if __name__ == '__main__':
    TOKEN = os.getenv('DISCORD_TOKEN')
    if not TOKEN:
        log.error("❌ 錯誤：找不到 DISCORD_TOKEN，請檢查設定檔。")
    else:
        bot.run(TOKEN)