# -*- coding: utf-8 -*-
import re
import discord
from discord.ext import commands
from discord import app_commands
from logger_config import log

class Nzip(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="nzip", description="秒級生成 nhentai.zip 雲端 ZIP 打包進度連結點及即可下載")
    @app_commands.describe(url_or_id="輸入本子的 URL 或 ID")
    async def nzip(self, interaction: discord.Interaction, url_or_id: str):
        # 由於純字串拼接極快，直接回應即可（所有人皆可見）
        await interaction.response.defer(thinking=False)

        # 1. 解析 book_id
        cleaned_input = url_or_id.strip()
        book_id_match = re.search(r'\d+', cleaned_input)
        
        if not book_id_match:
            await interaction.followup.send("❌ 無法解析有效的本子 ID，請檢查輸入。", ephemeral=True)
            return
            
        book_id = book_id_match.group(0)
        guild_name = interaction.guild.name if interaction.guild else "私訊"
        log.info(f"🚀 [nzip-lite] 伺服器: [{guild_name}] | 請求 ID: {book_id}")

        # 2. 使用驗證可行的高架構前台網頁網址
        web_url = f"https://nhentai.zip/g/{book_id}"

        # 3. 封裝 Embed 回傳給使用者
        embed = discord.Embed(
            title=f"📦 雲端 ZIP 打包頁面已生成",
            description=f"已成功為本子 **ID: `{book_id}`** 生成專屬轉存轉換分頁。",
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name="🌐 前往雲端網頁進行下載", 
            value=f"[👉 點我進入 nhentai.zip 網頁端]({web_url})\n\n*(進入網頁後，伺服器會自動與後端連線，帶你跑完進度條並下載標準 ZIP 壓縮檔！)*", 
            inline=False
        )
        
        embed.set_footer(text="⚡ 免下載架構：不消耗 Bot 伺服器任何硬碟與流量")

        await interaction.followup.send(embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(Nzip(bot))