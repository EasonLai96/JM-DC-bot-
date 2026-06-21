# -*- coding: utf-8 -*-
import discord
from discord import app_commands # 💡 引入 app_commands 支援斜線指令

def setup_help(bot):
    """
    將自訂的 /help 斜線指令註冊到機器人的 Command Tree 中
    """
    
    # 💡 註冊為斜線指令 (Slash Command)
    @bot.tree.command(name='help', description='顯示機器人的詳細使用說明與指令清單')
    async def custom_help(interaction: discord.Interaction):
        """
        顯示精美的使用指南
        """
        embed = discord.Embed(
            title="📚 禁漫天堂下載助手 - 使用指南",
            description="歡迎使用禁漫天堂下載助手！本機器人可以幫您快速預覽並直接打包下載本子至 Discord 頻道中。\n下方為目前支援的完整指令清單：",
            color=discord.Color.from_rgb(255, 128, 0) # 禁漫橘色調
        )

        # 1. 指令說明欄位
        embed.add_field(
            name="🔍 1. 預覽本子資訊：`/jmv [本子ID]`",
            value="💡 **說明：** 快速取得該本子的基本資料。\n"
                  "• 顯示包含：標題、作者、標籤分類。\n"
                  "• *不會下載任何檔案，反應極快！*",
            inline=False
        )

        embed.add_field(
            name="📥 2. 打包下載本子：`/jm [本子ID]`",
            value="💡 **說明：** 自動將該本子所有圖片下載、解密並壓縮成 `.zip` 上傳。\n"
                  "• 支援輸入 **純數字 ID** (例如: `666`)。\n"
                  "• 支援直接輸入 **完整網址** (機器人會自動解析並截取 ID)。\n"
                  "• 傳送成功後會 **自動刪除** 伺服器本地暫存，不佔用系統硬碟空間。",
            inline=False
        )

        # 2. 智慧分包與溫馨提示 (配合 main.py 的智慧分包邏輯)
        embed.add_field(
            name="🤖 智慧分包與傳送機制",
            value="• **自動分包功能：** 為了配合 Discord 免費頻道的限制，當本子壓縮檔超過 **9.5MB** 時，機器人會啟動「獨立健全分包」機制，**自動將本子拆分成數個約 9.2MB 的小 ZIP 檔**並依序發送，無需擔心因檔案過大而無法接收！\n"
                  "• **極致省記憶體：** 本系統採用「物理單線程下載」與「動態快取釋放」技術。下載較大本子（如 200 頁以上）通常需要 1~2 分鐘，請耐心等候，切勿重複發送指令。",
            inline=False
        )

        # 3. 頁尾資訊
        embed.set_footer(text="機器人狀態：正常運行中 • 有任何問題請聯絡管理員")
        
        # 💡 使用 response.send_message 回應斜線指令
        await interaction.response.send_message(embed=embed)