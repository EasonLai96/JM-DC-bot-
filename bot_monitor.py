# -*- coding: utf-8 -*-
import traceback
import discord
import os
import sys
from logger_config import log

# 自動檢查並安裝 psutil 硬體監控套件
try:
    import psutil
except ImportError:
    import subprocess
    log.info("[SYSTEM] 正在安裝 psutil 硬體監控套件...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "psutil"])
    import psutil

class BotMonitor:
    @staticmethod
    def _get_ram_usage():
        """⚡ 核心：獲取當前 1GB RAM 的系統與機器人記憶體狀態"""
        try:
            process = psutil.Process(os.getpid())
            bot_ram = process.memory_info().rss / (1024 * 1024) # 機器人吃了多少 MB
            sys_ram = psutil.virtual_memory() # 系統整體記憶體
            return f"[RAM 狀態 - 機器人: {bot_ram:.1f}MB | 系統空閒: {sys_ram.available / (1024 * 1024):.1f}MB ({sys_ram.percent}%已用)]"
        except:
            return "[RAM 狀態 - 無法獲取]"

    @staticmethod
    def _get_context(interaction: discord.Interaction):
        guild_name = interaction.guild.name if interaction.guild else "私訊"
        user_info = f"{interaction.user.name}({interaction.user.id})"
        return guild_name, user_info

    @classmethod
    def log_preview_request(cls, interaction: discord.Interaction, album_id: str):
        guild_name, user_info = cls._get_context(interaction)
        log.info(f"🔍 [預覽請求] 伺服器: [{guild_name}] | 使用者: {user_info} | ID: {album_id}")

    @classmethod
    def log_preview_fail(cls, album_id: str, error: Exception):
        log.warning(f"⚠️ [預覽失敗] 本子 ID: {album_id} | 原因: {error}")

    @classmethod
    def log_bot_output(cls, interaction: discord.Interaction, content: str):
        """⚡ 記錄機器人實際發送給使用者的輸出內容（embed 標題、連結、錯誤訊息等）"""
        guild_name, user_info = cls._get_context(interaction)
        log.info(f"🟢 [機器人輸出] 伺服器: [{guild_name}] | 使用者: {user_info} | 內容: {content}")

    @classmethod
    def log_duplicate_prevented(cls, interaction: discord.Interaction, album_id: str):
        guild_name, user_info = cls._get_context(interaction)
        log.warning(f"🔒 [阻擋重複提交] 伺服器: [{guild_name}] | 使用者: {user_info} | ID: {album_id}")

    @classmethod
    def log_queue_entered(cls, interaction: discord.Interaction, album_id: str):
        guild_name, _ = cls._get_context(interaction)
        ram_info = cls._get_ram_usage()
        log.info(f"⏳ [進入排隊] 伺服器: [{guild_name}] | 本子 ID {album_id} 進入佇列。{ram_info}")

    @classmethod
    def log_task_start(cls, interaction: discord.Interaction, album_id: str):
        guild_name, user_info = cls._get_context(interaction)
        ram_info = cls._get_ram_usage()
        log.info(f"🚀 [開始下載] 伺服器: [{guild_name}] | 使用者: {user_info} | 本子 ID: {album_id} {ram_info}")

    @classmethod
    def log_structure_success(cls, album_id: str, title: str, total_pages: int):
        ram_info = cls._get_ram_usage()
        log.info(f"📖 [解析成功] ID: {album_id} | 標題: <{title}> | 總共 {total_pages} 頁 {ram_info}")

    @staticmethod
    def log_compress_skip(album_id: str, page_num: int, error: Exception):
        log.warning(f"⚠️ [圖片微調跳過] ID: {album_id} | 頁數: {page_num} | 原因: {error}")

    @classmethod
    def log_download_progress(cls, album_id: str, current: int, total: int):
        # 多線程加速時，改為每 30 頁輸出一次，將 Log 的硬碟寫入干擾降到最低
        if current % 30 == 0 or current == total:
            ram_info = cls._get_ram_usage()
            log.info(f"🖨️ [下載進度] ID: {album_id} -> 已完成: {current}/{total} 頁 {ram_info}")

    @classmethod
    def log_compress_start(cls, album_id: str):
        ram_info = cls._get_ram_usage()
        log.info(f"📄 [PDF 合併中] 正在將 ID {album_id} 的圖片合併為 PDF... {ram_info}")

    @classmethod
    def log_upload_start(cls, album_id: str, file_size_mb: float):
        ram_info = cls._get_ram_usage()
        log.info(f"☁️ [雲端託管開始] ID: {album_id} ({file_size_mb:.1f}MB) -> 正在推送... {ram_info}")

    @staticmethod
    def log_upload_success(album_id: str, url: str):
        log.info(f"✅ [外部託管成功] ID: {album_id} -> 雲端網址: {url}")

    @staticmethod
    def log_upload_fail(album_id: str, error: Exception):
        log.error(f"❌ [雲端上傳崩潰] ID: {album_id} 上傳 Pixeldrain 失敗: {error}")

    @classmethod
    def log_cleanup_success(cls, album_id: str):
        ram_info = cls._get_ram_usage()
        log.info(f"🧹 [暫存環境清理] ID: {album_id} 的本機殘留快取已徹底銷毀。{ram_info}")

    @classmethod
    def log_command_error(cls, interaction: discord.Interaction, error: Exception):
        guild_name, user_info = cls._get_context(interaction)
        cmd_name = interaction.command.name if interaction.command else "未知指令"
        
        orig_error = getattr(error, 'original', error)
        error_msg = "".join(traceback.format_exception(type(orig_error), orig_error, orig_error.__traceback__))
        
        ram_info = cls._get_ram_usage()
        log.error(f"☠️ [指令崩潰] 伺服器: [{guild_name}] | 使用者: {user_info} | 指令: /{cmd_name} {ram_info}")
        log.error(f"詳細錯誤追蹤資訊:\n{error_msg}")
        return cmd_name