# -*- coding: utf-8 -*-
import discord
from discord.ext import commands, tasks
from discord import app_commands
from logger_config import log

class AdminCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.update_status.start()

    def cog_unload(self):
        self.update_status.cancel()

    @tasks.loop(minutes=10)
    async def update_status(self):
        total_guilds = len(self.bot.guilds)
        total_members = sum(guild.member_count for guild in self.bot.guilds)

        status_text = f"{total_guilds:,}個伺服器 ｜ {total_members:,}位用戶"

        activity = discord.Game(name=status_text)
        await self.bot.change_presence(status=discord.Status.online, activity=activity)
        log.info(f"📊 [狀態同步] 已更新狀態：{status_text}")

    @update_status.before_loop
    async def before_update_status(self):
        await self.bot.wait_until_ready()


    @app_commands.command(name='announcement', description='【開發者專用】向機器人所在的所有伺服器發送更新公告')
    @app_commands.describe(title='公告標題', content='公告詳細內容（支援 \\\\n 換行）')
    async def system_announcement(self, interaction: discord.Interaction, title: str, content: str):
        if not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message("❌ 只有機器人開發者（Owner）才能使用此全域公告指令！", ephemeral=True)
            return

        await interaction.response.defer(thinking=True, ephemeral=True)
        formatted_content = content.replace("\\n", "\n")

        embed = discord.Embed(title=f"📢 {title}", description=formatted_content, color=discord.Color.red())
        embed.set_author(name=self.bot.user.name, icon_url=self.bot.user.avatar.url if self.bot.user.avatar else None)
        embed.set_footer(text=f"來自系統官方的全域通知 • 2026-06-24")

        success_count = 0
        fail_count = 0
        total_guilds = len(self.bot.guilds)

        for guild in self.bot.guilds:
            sent = False
            if guild.system_channel and guild.system_channel.permissions_for(guild.me).send_messages:
                try:
                    await guild.system_channel.send(embed=embed)
                    success_count += 1
                    sent = True
                except: pass

            if not sent:
                def channel_score(ch):
                    name = ch.name.lower()
                    if '公告' in name or 'announcement' in name: return 0
                    if 'general' in name or '日常' in name: return 1
                    return 2

                text_channels = [
                    ch for ch in guild.text_channels \
                    if ch.permissions_for(guild.me).send_messages and not ch.is_nsfw()
                ]
                text_channels.sort(key=channel_score)

                if text_channels:
                    try:
                        await text_channels[0].send(embed=embed)
                        success_count += 1
                        sent = True
                    except: pass

            if not sent: fail_count += 1

        await interaction.edit_original_response(content=f"✅ 公告發送完畢！發送給 {total_guilds} 個伺服器中的 {success_count} 個伺服器（失敗：{fail_count}）")

async def setup(bot):
    await bot.add_cog(AdminCog(bot))