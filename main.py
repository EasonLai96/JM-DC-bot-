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
async def on_error(event_method: str, *args, **kwargs):
    """
    🛠️ 修復：discord.py 預設只會把「非斜線指令」的事件監聽器例外
    （例如某個 cog 的 on_message、on_voice_state_update，或是背景 tasks.loop
    本身出錯）印到 stderr，完全不會經過我們的 log 系統。如果 log 檔案只收集
    log.info/log.error 這種明確呼叫，這類例外就會完全「隱形」，什麼紀錄都留不下。
    這裡補上這個全域 handler，確保任何事件監聽器炸掉都會被完整記錄下來，
    不會再無聲無息地消失。
    """
    exc_text = traceback.format_exc()
    log.error(f"☠️ [未預期的事件例外] 事件: {event_method} | args: {args}\n{exc_text}")


# 🛠️ 隱私修復（H2，與 cogs/logrelay.py 的 SENSITIVE_COMMANDS 一致）：
# 這幾道指令的參數本身就是敏感資訊 —— /debug_give 是「給哪個 UID 多少錢」、
# /announcement 是還沒發布的公告全文、/setlogchannel 與 /set_feedback_channel 是
# 頻道設定。原本的完成訊息是 `opt['value']` 全數原文照印，等於把明細直接寫進
# bot.log（而 bot.log 又會被轉發到 Discord 頻道），隱私保護只做了一半。
SENSITIVE_COMMANDS = {"debug_give", "settings", "set_feedback_channel", "setlogchannel", "announcement", "translate_test"}


@bot.event
async def on_app_command_completion(interaction: discord.Interaction, command: app_commands.AppCommand):
    guild_name = interaction.guild.name if interaction.guild else "私訊"
    user_info = f"{interaction.user.name}({interaction.user.id})"

    options = interaction.data.get('options', []) if interaction.data else []
    if command.name in SENSITIVE_COMMANDS:
        # 只留參數名稱，不留值
        args = ", ".join(f"{opt.get('name', '?')}=<已隱藏>" for opt in options)
    else:
        args = ", ".join(
            f"{opt.get('name', '?')}='{str(opt.get('value', ''))[:100]}'" for opt in options
        )

    ram_info = BotMonitor._get_ram_usage()
    log.info(f"🟢 [指令成功] 伺服器: [{guild_name}] | 使用者: {user_info} | 呼叫: /{command.name} ({args}) {ram_info}")


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    # 🛠️ 修復（重複回覆 + 把冷卻誤報成崩潰）：
    # discord.py 的流程是「先呼叫指令自己的錯誤處理器（包含 cog_app_command_error），
    # 再**無條件**呼叫這個 tree.on_error」（見 app_commands/tree.py 的 _call）。
    # 也就是說只要 cog 已經回應過（例如把「冷卻」轉成「請等 N 秒」的友善提示），
    # 這裡就會變成第二次回覆 —— 使用者會多收到一則誤導的「未知的內部錯誤」，
    # 而且單純的指令冷卻還會被記成 ERROR 等級的「指令崩潰」，把真正的錯誤洗掉。
    #
    # discord.py 內建的 CommandTree.on_error 會用 command._has_any_error_handlers()
    # 提早 return 來避免這件事。這裡比照同樣的判斷，但更保守：
    # 只有在「cog 確實已經回應過」時才跳過；若它其實沒回應，仍然要讓使用者知道。
    command = interaction.command
    if command is not None and interaction.response.is_done():
        _has_handler = getattr(command, "_has_any_error_handlers", None)
        if _has_handler is not None:
            try:
                if _has_handler():
                    guild_name = interaction.guild.name if interaction.guild else "私訊"
                    log.warning(
                        f"⚠️ [指令未執行] /{command.name} | 伺服器: [{guild_name}] "
                        f"| 使用者: {interaction.user}({interaction.user.id}) "
                        f"| 已由該指令的錯誤處理器回應 | {error!r}"
                    )
                    return
            except Exception:
                pass

    cmd_name = BotMonitor.log_command_error(interaction, error)
    
    reply_msg = f"❌ 執行指令 `/{cmd_name}` 時發生未知的內部錯誤！Bug 已回報給後台開發者。"
    try:
        if interaction.response.is_done():
            await interaction.followup.send(reply_msg, ephemeral=True)
        else:
            await interaction.response.send_message(reply_msg, ephemeral=True)
    except Exception:
        # 🛠️ 原本是裸 except：會把 KeyboardInterrupt / asyncio.CancelledError
        # 這類 BaseException 一起吞掉，關機流程中可能讓 task 無法正常結束。
        pass

@bot.event
async def on_ready():
    # 🛠️ 修復：Discord 的 Gateway 只要重新連線（不只是你主動重啟，網路短暫斷線、
    # Discord 端要求重新 Identify 等情況都算），就有機會讓 on_ready 在「同一個
    # 程序」裡再次觸發。如果沒有防呆，就會想再載入一次所有已載入的 cogs（噴一堆
    # ExtensionAlreadyLoaded）、還會再 sync 一次指令樹，白白浪費資源也徒增噪音。
    # 用一個旗標確保「載入 cogs + 同步指令」這件事，同一個程序只做一次。
    if getattr(bot, "_startup_done", False):
        log.info("🔁 [重新連線] Gateway 重新連線（可能只是短暫斷線後自動恢復），略過重複載入 cogs。")
        return
    bot._startup_done = True

    log.info(f'=================================')
    log.info(f' 機器人已成功上線！')
    log.info(f' 機器人名稱: {bot.user.name} (ID: {bot.user.id})')
    log.info(f' 目前連線到 {len(bot.guilds)} 個伺服器')
    log.info(f'=================================')
    
    # ⚠️ 載入順序有意義：'cogs.nhentai' 必須排在 'nhentai_search' 前面。
    # nhentai_search.py 是 `from cogs.nhentai import ...`（共用同一個模組物件），
    # 而 discord.py 的 load_extension 會 `del sys.modules[name]` 後重新匯入 ——
    # 如果 nhentai_search 先載入，它會先建立一份 cogs.nhentai，之後 load_extension
    # 又換成另一份，兩邊就各自持有不同模組（等於兩份 DOWNLOAD_REGISTRY）。
    cogs_to_load = ['cogs.comic', 'cogs.admin', 'cogs.fixlink', 'cogs.logrelay', 'cogs.feedback', 'cogs.profile_card', 'cogs.economy', 'cogs.casino', 'cogs.Autorestart', 'cogs.nhentai','nhentai_search','cogs.favorites','cogs.translate_admin',]
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
        try:
            bot.run(TOKEN)
        except Exception:
            # 🛠️ 修復：如果連 bot.run() 這層都炸掉（例如登入失敗、Token 被撤銷、
            # 網路層級的例外），這是最後一道防線，確保就算程序即將整個死掉，
            # 至少完整的錯誤內容有被寫進我們自己的 log，而不是只留在 stderr
            # （很多主機面板的 stderr 不會保留在你查得到的 log 檔案裡）。
            log.error(f"☠️☠️☠️ [致命錯誤] bot.run() 意外終止：\n{traceback.format_exc()}")
            raise