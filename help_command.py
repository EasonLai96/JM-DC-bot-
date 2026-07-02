# -*- coding: utf-8 -*-
"""
互動式 /help 斜線指令模組
第一層下拉選單：媒體下載功能 / 萌力次元經濟系統
第二層下拉選單（選擇經濟系統後出現）：依主題切換子頁籤，避免單一 embed 塞入過多 1~200 等資訊而超出 Discord 字數限制。

⚠️ 本檔案的所有經濟數值皆直接對應 profile_store.py 的 SIGN_LEVEL_MATRIX、
economy.py 的實際指令邏輯。如果之後調整曲線或公式，請同步更新本檔案，
避免 help 文件與實際遊玩體驗再次出現落差。
"""
import os
import sys
import discord
from discord import app_commands

# 🛠️ help_command.py 與 main.py 同層，但 profile_store.py 實際放在 cogs/ 資料夾內，
# 兩者不屬於同一個套件，無法使用 from . import profile_store 這種相對匯入語法
# （那是給 cogs/ 內部彼此互相 import 用的）。
# 這裡改為將 cogs/ 加入模組搜尋路徑後，直接以一般方式 import。
_COGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cogs")
if _COGS_DIR not in sys.path:
    sys.path.insert(0, _COGS_DIR)

import profile_store


# ==================== 📊 經濟系統子頁籤資料 ====================

def build_currency_embed() -> discord.Embed:
    """🪙 子頁籤一：核心貨幣定義"""
    embed = discord.Embed(
        title="🪙 核心數位貨幣定義",
        description="伺服器內共有兩種貨幣，分工明確：日常流通用 MP，稀有突破用 DC。",
        color=discord.Color.gold()
    )
    embed.add_field(
        name="✨ 萌力值（Moe Point，簡稱 MP）",
        value="日常流通基礎貨幣。\n"
              "• 透過 `/hourly`（每小時提取儲存池）與 `/daily`（每日簽到）獲得\n"
              "• 用於 `/level_info` 升級、`/reclaim` 補簽、`/power_plant` 發電廠注入",
        inline=False
    )
    embed.add_field(
        name="🔮 次元結晶（Dimension Crystal，簡稱 DC）",
        value="稀有核心能源，用於 4 等以上升級與補簽。\n"
              "• **唯一取得管道：** `/daily` 簽到時有 **10%** 機率掘出裂縫，掉落 **1~3 顆**\n"
              "• 每滿 **7 天連續簽到**，掉落量額外 **+1 顆**（最多 +3 顆）\n"
              "• 💡 DC 目前無法透過 `/power_plant` 或其他途徑取得，請靠每日簽到累積",
        inline=False
    )
    return embed


def build_level_embed(current_lvl: int = None) -> discord.Embed:
    """📈 子頁籤二：1~200 等級換算表"""
    embed = discord.Embed(
        title="📈 萌力階級突破系統（1~200 等）",
        description="階級上限為 **200 等**。等級越高，`/hourly` 儲存池容量越大、`/hourly` 與 `/daily` 提取倍率越高。\n"
                    "費用與倍率採**收斂型指數成長曲線**：中段成長快、後段逐漸放緩，不會無限暴衝到天文數字。",
        color=discord.Color.purple()
    )
    rows = [1, 10, 25, 50, 75, 100, 125, 150, 175, 200]
    lines = []
    for lv in rows:
        info = profile_store.SIGN_LEVEL_MATRIX[lv]
        mp_str = profile_store.format_cn_number(info["mp_cost"])
        marker = "⭐" if current_lvl == lv else "•"
        lines.append(
            f"{marker} **Lv.{lv}** | {mp_str}MP / {info['dc_cost']:,}DC | "
            f"池{info['hours']}h | 時{info['h_mult']}x | 日{info['d_mult']}x"
        )
    embed.add_field(
        name="📋 關鍵節點一覽（完整 1~200 等請用 /level_info 查詢）",
        value="\n".join(lines),
        inline=False
    )
    embed.add_field(
        name="💡 補充說明",
        value="• 4 等以上升級需要消耗次元結晶 (DC)\n"
              "• 儲存池上限在 **Lv.15 起封頂於 168 小時（1 週）**，之後不再隨等級增加\n"
              "• 用 `/level_info` 可查看你目前所在等級，並一鍵點擊升級",
        inline=False
    )
    return embed


def build_talent_embed() -> discord.Embed:
    """⚔️ 子頁籤三：四大職業天賦"""
    embed = discord.Embed(
        title="⚔️ 四大職業天賦流派",
        description="使用 `/set_profession` 選擇職業流派，立即套用對應的經濟被動技能：",
        color=discord.Color.blue()
    )
    embed.add_field(
        name="⏰ 時空定錨者",
        value="`/hourly` 儲存池上限永久 **+2 小時**。",
        inline=False
    )
    embed.add_field(
        name="💥 萌力暴走者",
        value="`/daily` 簽到時有 **10% 機率** 觸發暴擊，當次 MP 收益直接 **翻倍 (2.0x)**。",
        inline=False
    )
    embed.add_field(
        name="🕵️ 結晶走私客",
        value="`/reclaim` 補簽所需的次元結晶消耗永遠 **-1 顆**（最低仍需扣 1 顆）。",
        inline=False
    )
    embed.add_field(
        name="🌱 平民無業遊民",
        value="升級至 **Lv.2 ~ Lv.3** 時，所需 MP 費用享有 **9 折**優惠。",
        inline=False
    )
    embed.set_footer(text="💡 四個流派為固定選項，透過 /set_profession 直接選擇，與 /profile 上顯示的職業文字綁定")
    return embed


def build_power_plant_embed() -> discord.Embed:
    """⚡ 子頁籤四：萌力發電廠"""
    embed = discord.Embed(
        title="⚡ 全服大活動：萌力發電廠",
        description="伺服器全體玩家共同集資的全服 Buff 機制。",
        color=discord.Color.orange()
    )
    embed.add_field(
        name="⛩️ 發電廠注入",
        value="輸入 `/power_plant [數量]`，奉獻並**銷毀**你的 MP（注入後 MP 不會返還）。",
        inline=False
    )
    embed.add_field(
        name="🎉 超載大爆發",
        value="全服累計注入達到 **500,000 MP** 時，發電廠超載觸發！\n"
              "（達標後累計進度會留下餘數延續到下一輪，不會直接清零）",
        inline=False
    )
    embed.add_field(
        name="🪐 全服狂歡 Buff",
        value="超載後的 **24 小時內**，全服所有人執行 `/hourly` 與 `/daily` 皆會額外乘上"
              "**隨機 1.2x ~ 1.5x** 的加成倍率！",
        inline=False
    )
    embed.set_footer(text="💡 目前發電廠超載僅提供全服倍率 Buff，沒有額外的抽獎或 DC 贈送機制")
    return embed


def build_reclaim_embed() -> discord.Embed:
    """🔮 子頁籤五：補簽機制"""
    embed = discord.Embed(
        title="🔮 時空扭轉補簽機制",
        description="忘記每日簽到導致連續天數 (Streak) 中斷？輸入 `/reclaim` 扭轉因果！",
        color=discord.Color.teal()
    )
    embed.add_field(
        name="💰 補簽扣費公式",
        value="• **DC 消耗** = 第 N 次補簽，扣除 N 顆結晶（結晶走私客流派 -1 顆）\n"
              "• **MP 消耗** = `(500 ✕ 補簽次數N) ✕ 擬補簽後天數 ✕ 階級折扣`",
        inline=False
    )
    embed.add_field(
        name="📉 階級折扣曲線",
        value="折扣係數隨等級平滑遞減，永遠不會變成 0：\n"
              "• Lv.1：1.00（不打折）　• Lv.10：0.61　• Lv.20：0.35\n"
              "• Lv.50：0.08　• Lv.100：0.02　• Lv.200：0.02（趨近下限）\n"
              "💡 等級越高，補簽 MP 費用越便宜！",
        inline=False
    )
    embed.set_footer(text="🎉 補簽成功後可立即輸入 /daily 領取當天的簽到獎勵")
    return embed


def build_commands_embed() -> discord.Embed:
    """📜 子頁籤六：玩家指令手冊"""
    embed = discord.Embed(
        title="📜 玩家專用指令手冊",
        description="所有經濟系統相關指令一覽：",
        color=discord.Color.from_rgb(255, 105, 180)
    )
    embed.add_field(
        name="🪐 經濟與簽到",
        value="• `/hourly` —— 提領儲存池累積的 MP（滿 1 小時即可提領）\n"
              "• `/daily` —— 每日簽到，維繫連簽，有機率掉落次元結晶 (DC)\n"
              "• `/reclaim` —— 消耗 MP/DC 扭轉時空進行補簽，接回連簽紀錄\n"
              "• `/level_info` —— 查看 1~200 等換算表，並一鍵點擊升級\n"
              "• `/power_plant [數量]` —— 奉獻 MP 參與全服集資，達標啟動全服狂歡 Buff",
        inline=False
    )
    embed.add_field(
        name="🎨 個人檔案卡",
        value="• `/profile [擁有者]` —— 展示 16:9 磨砂玻璃名片卡（可選查看他人）\n"
              "• `/set_profession` —— 選擇四大天賦流派職業\n"
              "• `/set_background [圖片]` —— 上傳自訂卡片背景（自動裁切 16:9）\n"
              "• `/reset_background` —— 恢復為預設的次元星空背景",
        inline=False
    )
    return embed


# ==================== 🎰 賭場系統獨立頁籤 ====================

def build_casino_embed() -> discord.Embed:
    """🎰 第一層主選單獨立頁面：時空裂縫賭場"""
    embed = discord.Embed(
        title="🎰 時空裂縫賭場",
        description="內建 **3 款** 賭場遊戲，皆可使用萌力值 (MP) 或次元結晶 (DC) 下注豪賭！\n"
                    "💡 **獲勝抽稅：** 每局獲勝皆抽取 **5% 賭場稅**（MP 限定，DC 不抽稅），稅金會全自動即時注入全服 `/power_plant` 發電廠！",
        color=discord.Color.from_rgb(218, 41, 28)
    )
    embed.add_field(
        name="🎲 命運極限雙骰　`/dice [數量] [貨幣類型]`",
        value="玩家與機器人莊家各擲兩顆骰子比點數大小，支援 MP 或 DC 結晶雙幣下注。\n"
              "• 點數較高者獲勝；平手退回本金\n"
              "• 💎 隱藏彩蛋：以 DC 下注時擲出雙六豹子 `(6, 6)`，賠率直接飆升至 **5 倍**！",
        inline=False
    )
    embed.add_field(
        name="🎡 次元幸運大輪盤　`/wheel [下注MP]`",
        value="轉動命運法陣，5 個機率區間決定結果：\n"
              "• 💀 時空吞噬（40%）：本金全沒收\n"
              "• ⚖️ 原物返還（25%）：退回全額本金\n"
              "• ✨ 萌力翻湧（20%）：獲得 **1.5 倍** 收益\n"
              "• 💥 狂暴突破（10%）：獲得 **3.0 倍** 超級暴擊收益\n"
              "• 🔮 結晶裂縫（5%）：不返還 MP，改掘出 **1~3 顆**次元結晶 (DC)",
        inline=False
    )
    embed.add_field(
        name="🃏 萌力 21 點　`/blackjack [下注MP]`",
        value="經典 21 點玩法，與機器人莊家比點數，使用按鈕互動操作：\n"
              "• 一般獲勝：贏得 **2 倍**本金（扣稅後淨賺）\n"
              "• 開局天選 Blackjack：贈送 **1.5 倍** 特殊獲勝獎勵（共 2.5 倍）\n"
              "• 平手 (Push)：退回全額本金\n"
              "• ⏳ 互動按鈕 **60 秒**未操作將自動逾時",
        inline=False
    )
    embed.add_field(
        name="🌱 「平庸之福流」連輸補償",
        value="此職業流派玩家若在 `/dice` 或 `/wheel` 連續輸 **3 把**，會自動觸發天賦補償，"
              "返還 **50%** 本金救濟金，且連輸次數會重新計算。",
        inline=False
    )
    embed.add_field(
        name="🕵️ 「結晶走私客」豁免特權",
        value="此職業流派玩家在 `/dice` 以 DC 下注失敗時，有 **10% 機率**觸發走私技術，"
              "全額保全本金不被扣除。",
        inline=False
    )
    embed.set_footer(text="🎰 賭場為高風險娛樂功能，請理性下注 ｜ 獲勝稅金將全數挹注全服發電廠 Buff 活動")
    return embed


ECONOMY_PAGES = {
    "currency": build_currency_embed,
    "level": build_level_embed,
    "talent": build_talent_embed,
    "power_plant": build_power_plant_embed,
    "reclaim": build_reclaim_embed,
    "commands": build_commands_embed,
}


# ==================== 🪐 第二層：經濟系統子選單 ====================

class EconomySubDropdown(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="🪙 核心貨幣定義", description="萌力值(MP)與次元結晶(DC)的用途與取得方式", value="currency", emoji="🪙"),
            discord.SelectOption(label="📈 1~200 等級換算表", description="查看階級費用、儲存池上限與提取倍率", value="level", emoji="📈"),
            discord.SelectOption(label="⚔️ 四大職業天賦", description="四種職業流派的被動技能效果", value="talent", emoji="⚔️"),
            discord.SelectOption(label="⚡ 萌力發電廠", description="全服集資與狂歡 Buff 機制", value="power_plant", emoji="⚡"),
            discord.SelectOption(label="🔮 補簽機制", description="時空扭轉補簽的扣費公式與折扣曲線", value="reclaim", emoji="🔮"),
            discord.SelectOption(label="📜 玩家指令手冊", description="所有經濟系統相關指令一覽", value="commands", emoji="📜"),
        ]
        super().__init__(placeholder="選擇要查看的經濟系統子主題...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        page_key = self.values[0]
        builder = ECONOMY_PAGES[page_key]

        if page_key == "level":
            p_data = await profile_store.get_profile(interaction.user.id)
            embed = builder(current_lvl=p_data["sign_level"])
        else:
            embed = builder()

        suffix = "🪐 經濟系統說明書 ｜ 可重新選擇上方選單切換子主題"
        if embed.footer and embed.footer.text:
            embed.set_footer(text=f"{embed.footer.text} ｜ {suffix}")
        else:
            embed.set_footer(text=suffix)

        await interaction.response.edit_message(embed=embed, view=self.view)


class EconomySubView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.add_item(EconomySubDropdown())


# ==================== 📕 第一層：主選單 ====================

class HelpDropdown(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label="📕 漫畫下載與社群修復",
                description="查看漫畫/本子下載與連結自動預覽修復功能",
                value="media",
                emoji="📕"
            ),
            discord.SelectOption(
                label="🪐 萌力次元經濟系統",
                description="查看 1~200 等級系統、四大天賦、發電廠與補簽機制",
                value="economy",
                emoji="🪐"
            ),
            discord.SelectOption(
                label="🎰 時空裂縫賭場",
                description="查看雙骰、大輪盤、21點三款賭場遊戲規則與賠率",
                value="casino",
                emoji="🎰"
            )
        ]
        super().__init__(placeholder="選擇要查看的功能章節...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        selection = self.values[0]

        if selection == "media":
            embed = discord.Embed(
                title="📕 漫畫下載與社群修復指南",
                description="提供快速的漫畫與本子下載，以及全自動的社群預覽修復：",
                color=discord.Color.from_rgb(255, 105, 180)
            )
            embed.add_field(
                name="📕 漫畫 & 本子指令",
                value="• `/jmv [ID或網址]` ：預覽禁漫天堂本子的標題、作者與標籤。\n"
                      "• `/jm [ID或網址]` ：自動下載解密禁漫本子，打包轉存至 Pixeldrain 雲端。\n"
                      "• `/nzip [ID或網址]` ：秒級生成 `nhentai.zip` 雲端打包下載頁面。",
                inline=False
            )
            embed.add_field(
                name="🔗 全自動社群預覽修復 (無需指令)",
                value="💡 **說明：** 當群友發送以下特定網址時，Bot 會自動修復並貼出完美的嵌入式預覽：\n"
                      "• **支援平台：** Twitter/X (`fxtwitter`)、Instagram (`fxstagram`)、TikTok、Reddit、Pixiv (`phixiv`)、Bluesky、Bilibili (`vxbilibili`)。\n"
                      "• **小提示：** 如果該則訊息不想被修復，只要在訊息中包含 `fxignore` 即可跳過。",
                inline=False
            )
            embed.set_footer(text="⚙️ 下拉選單隨時切換分頁")
            await interaction.response.edit_message(embed=embed, view=self.view)

        elif selection == "economy":
            # 切換到第二層子選單，預設顯示「核心貨幣定義」
            embed = build_currency_embed()
            embed.set_footer(text="🪐 經濟系統說明書 ｜ 可重新選擇上方選單切換子主題")
            await interaction.response.edit_message(embed=embed, view=EconomySubView())

        elif selection == "casino":
            # 賭場系統為獨立單頁，不展開第二層子選單
            embed = build_casino_embed()
            await interaction.response.edit_message(embed=embed, view=self.view)


class HelpView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)  # 3分鐘後選單過期
        self.add_item(HelpDropdown())


def setup_help(bot):
    """
    將 /help 斜線指令註冊到機器人的 Command Tree 中
    """
    @bot.tree.command(name='help', description='顯示機器人的所有功能與完整指令指南')
    async def custom_help(interaction: discord.Interaction):
        # 初始首頁
        embed = discord.Embed(
            title="✨ 綜合多功能助手 - 使用指南",
            description="歡迎使用本助手！本機器人已全面整合漫畫下載、社群預覽修復、**🪐 萌力次元經濟系統（1~200 等）**以及**🎰 時空裂縫賭場**。\n\n"
                        "請使用下方表格的**「下拉選單」**切換不同章節查看完整指令說明：",
            color=discord.Color.from_rgb(255, 105, 180)
        )

        embed.add_field(
            name="🎬 預設首頁功能摘要",
            value="• 漫畫與本子下載 (`/jm`, `/jmv`, `/nzip`)\n"
                  "• 社群連結自動預覽修復 (Twitter, IG, TikTok, Pixiv, Bilibili等)",
            inline=False
        )

        embed.add_field(
            name="🪐 想看經濟系統說明書？",
            value="請點擊下方的下拉選單，選擇 **「🪐 萌力次元經濟系統」**，即可進入第二層選單"
                  "切換查看貨幣定義、1~200 等級換算表、四大天賦流派、發電廠與補簽機制等子主題！",
            inline=False
        )

        embed.add_field(
            name="🎰 想看賭場遊戲規則？",
            value="請點擊下方的下拉選單，選擇 **「🎰 時空裂縫賭場」**，即可查看雙骰、大輪盤、21點"
                  "三款遊戲的完整規則、賠率與職業天賦特權說明！",
            inline=False
        )

        embed.set_footer(text="⚙️ 功能已同步更新 ｜ 下拉選單隨時切換分頁")

        view = HelpView()
        await interaction.response.send_message(embed=embed, view=view)