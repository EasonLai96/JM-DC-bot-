# -*- coding: utf-8 -*-
"""
互動式 /help 斜線指令模組

🛠️ 重寫重點（相較舊版）：
- 導覽層級從「6 個經濟子頁籤」精簡成 3 個，同性質資訊合併在同一頁，
  減少使用者要點好幾次選單才找得到東西的問題。
- 舊版一旦切進「經濟系統」或選了「賭場」，就沒有路徑能回到最初的首頁，
  只能重新打一次 /help。現在每個子選單都加了「⬅️ 返回主選單」選項。
- 拆成「資料層」(EMBED 建構函式) 與「導覽層」(Dropdown/View) 兩塊，
  之後要調整文案或加減分頁，只需要動其中一邊，不用整支重寫。

⚠️ 本檔案的所有經濟數值皆直接對應 profile_store.py 的 SIGN_LEVEL_MATRIX、
economy.py 的實際指令邏輯。如果之後調整曲線或公式，請同步更新本檔案，
避免 help 文件與實際遊玩體驗再次出現落差。

🆕 指令清單改為「自動生成」（本次改動）：
- 「📜 指令手冊」頁面不再手寫，而是直接走訪 bot.tree 目前實際載入的指令樹。
  舊版手寫清單同時犯了兩個錯——列了不存在的 /set_profession、/set_background 等
  指令，又漏掉 /leaderboard、/nh、/nhsearch 等實際存在的指令。
  改成自動生成後，這種「文件與實作脫節」在結構上不可能再發生。
- 下面的 COMMAND_GROUPS 只是「排序提示」，沒被列到的指令會自動落到「其他指令」，
  永遠不會因為忘記維護而從說明中消失。
"""
import os
import sys
import discord
from discord import app_commands

# 🛠️ help_command.py 與 main.py 同層，但 profile_store.py 實際放在 cogs/ 資料夾內。
#
# ⚠️ 這裡要把 cogs/ 加進 sys.path **不能拿掉**：cogs 內有幾個檔案是用「頂層名稱」
# 互相匯入的（comic.py → `import comic_cache`、nhentai.py → `from nhentai_cache import`），
# 而且 main.py 的 cogs 清單裡有一個 'nhentai_search' 是刻意用頂層名稱載入的。
_COGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cogs")
if _COGS_DIR not in sys.path:
    sys.path.insert(0, _COGS_DIR)

# 🛠️ 修復（與系統載入同一個 profile_store）：原本是 `import profile_store`，因為上面把
# cogs/ 加進了 sys.path，這行會把 cogs/profile_store.py 匯入成**另一個獨立的頂層模組**，
# 而 cogs/economy.py、cogs/profile_card.py、cogs/casino.py 用的是 `from . import profile_store`
# （= cogs.profile_store）。於是記憶體裡有兩份 profile_store，各有自己的 asyncio.Lock 與
# 模組狀態 —— 而 /help 底下會呼叫 `profile_store.get_profile()`，等於用「另一把鎖」對
# profiles.json 做 read-modify-write，與經濟系統的並發寫入完全沒有互斥，可能造成玩家資料
# 被舊值覆蓋。改成匯入同一個套件模組（絕對匯入優先，失敗才退回舊寫法）。
try:
    from cogs import profile_store
except ImportError:  # pragma: no cover - 備援：萬一 cogs 不是可匯入的套件
    import profile_store


# ==================== 🎨 共用小工具 ====================

def _with_nav_footer(embed: discord.Embed, extra: str) -> discord.Embed:
    """統一在 footer 補上導覽提示，避免每個 builder 各寫一次。"""
    base = embed.footer.text if embed.footer and embed.footer.text else ""
    embed.set_footer(text=f"{base} ｜ {extra}" if base else extra)
    return embed


# ==================== 🧭 指令樹自動生成工具 ====================
# 這一區讓 /help 的指令清單「以 bot.tree 實際載入的指令為唯一真相」，
# 而不是維護一份手寫清單——手寫清單一定會腐化（舊版就同時漏列與誤列過）。

# 只有機器人 Owner 才看得到的指令。這些指令是用「執行時呼叫 is_owner()」判斷的，
# 不是用裝飾器，所以無法從指令物件自動推斷，只能在這裡明確維護。
#
# ⚠️ 必須與 cogs/logrelay.py 的 SENSITIVE_COMMANDS 保持一致（那邊也維護了一份）。
#    logrelay 目前的值：{"debug_give", "settings", "set_feedback_channel",
#                        "setlogchannel", "announcement"}
#    這裡額外加上沒有被 logrelay 收錄的 "restart"。
#    漏掉的話，那些管理指令會出現在一般使用者看到的 /help 裡。
ADMIN_ONLY_COMMANDS = {
    "announcement",
    "restart",
    "set_feedback_channel",
    "setlogchannel",   # cogs/logrelay.py    → is_owner()
    "debug_give",      # cogs/profile_card.py → is_owner()
    "settings",        # cogs/fixlink.py      → has_permissions(manage_guild)
    "translate_test",  # cogs/translate_admin.py → is_owner()
}

# 分組「提示」：用來決定顯示順序與歸類。
# ⚠️ 不在這裡的指令並不會消失，會自動歸到最後的「其他指令」區塊。
COMMAND_GROUPS = [
    ("📕 漫畫與本子", ["jmv", "jmbatch", "jm", "nzip", "queue",
                      "nhv", "nhbatch", "nh", "nhsearch", "nhrandom",
                      "favorite", "unfavorite", "favorites", "favorites_clear"]),
    ("🪐 經濟與簽到", ["hourly", "daily", "reclaim", "level_info", "power_plant", "leaderboard"]),
    ("🎰 時空裂縫賭場", ["dice", "wheel", "blackjack"]),
    ("🎨 個人檔案", ["profile", "set_profession", "set_background", "reset_background"]),
    ("📬 互動與說明", ["feedback", "help"]),
]

# Discord embed 的 field value 上限是 1024 字元，超過會讓整則訊息發送失敗。
# 這裡刻意留一點餘裕。
_FIELD_VALUE_LIMIT = 1020


def _is_slash_command(cmd) -> bool:
    """區分斜線指令與右鍵選單指令：ContextMenu 有 .type，斜線指令沒有。"""
    return getattr(cmd, "type", None) is None and hasattr(cmd, "description")


def _get_command(bot, name: str):
    """從指令樹取出指令；不存在、或不是斜線指令時回傳 None（絕不拋例外）。"""
    if bot is None:
        return None
    try:
        cmd = bot.tree.get_command(name)
    except Exception:
        return None
    return cmd if _is_slash_command(cmd) else None


def _command_exists(bot, name: str) -> bool:
    return _get_command(bot, name) is not None


def _format_params(cmd) -> str:
    """把參數排成 `/dice <數量> [貨幣類型]` 這種可讀形式（<>必填、[]選填）。"""
    parts = []
    for param in getattr(cmd, "parameters", None) or []:
        name = getattr(param, "name", None)
        if not name:
            continue
        parts.append(f"<{name}>" if getattr(param, "required", False) else f"[{name}]")
    return (" " + " ".join(parts)) if parts else ""


def _describe(cmd) -> str:
    """一行指令說明。"""
    return f"• `/{cmd.name}{_format_params(cmd)}` —— {getattr(cmd, 'description', '') or '（無說明）'}"


def _limit_lines(lines, limit: int = _FIELD_VALUE_LIMIT) -> str:
    """把多行文字塞進單一 embed field，**保證不超過 1024 字元上限**。
    超過時保留完整行並在最後標註省略，而不是讓 Discord 直接回 400。"""
    kept, used = [], 0
    for line in lines:
        if used + len(line) + 1 > limit:
            kept.append("　…（其餘省略）")
            break
        kept.append(line)
        used += len(line) + 1
    return "\n".join(kept) if kept else "（無）"


async def _is_owner(interaction: discord.Interaction) -> bool:
    """安全判斷是否為機器人 Owner；判斷失敗一律視為「不是」，避免權限外洩。"""
    try:
        return await interaction.client.is_owner(interaction.user)
    except Exception:
        return False


# ==================== 📕 漫畫下載頁 ====================

def build_media_embed(bot=None) -> discord.Embed:
    embed = discord.Embed(
        title="📕 漫畫下載與社群修復指南",
        description="提供快速的漫畫與本子下載，以及全自動的社群預覽修復：",
        color=discord.Color.from_rgb(255, 105, 180)
    )
    embed.add_field(
        name="📕 漫畫 & 本子指令",
        value="• `/jmv [ID或網址]` ：預覽禁漫天堂本子的標題、作者與標籤。\n"
              "• `/jm [ID或網址]` ：自動下載解密禁漫本子，打包轉存至 Pixeldrain 雲端。\n"
              "　　同一本子短時間內重複下載會直接命中快取、秒速送出，不會重複佔用下載通道。\n"
              "• `/nzip [ID或網址]` ：秒級生成 `nhentai.zip` 雲端打包下載頁面。\n"
              "• `/favorite <ID或網址>` ：⭐ 收藏喜歡的本子（禁漫與 nhentai **共用同一個收藏庫**，\n"
              "　　以 `JM:` / `NH:` 前綴區分，兩邊 ID 撞號也不會搞混）。\n"
              "• `/favorites [使用者]` ：📖 查看收藏清單，可依來源篩選、可分頁。",
        inline=False
    )

    # 🆕 自動補上上面文案沒提到的同類指令（例如 /nh、/nhv、/nhsearch、/nhrandom）。
    # 這樣以後新增本子相關指令時，這頁不會再出現「功能存在但說明沒寫」的落差。
    described = {"jmv", "jm", "nzip", "favorite", "favorites"}
    extra_lines = []
    for name in dict(COMMAND_GROUPS).get("📕 漫畫與本子", []):
        if name in described:
            continue
        cmd = _get_command(bot, name)
        if cmd is not None:
            extra_lines.append(_describe(cmd))
    if extra_lines:
        embed.add_field(
            name="➕ 其他本子相關指令（自動偵測）",
            value=_limit_lines(extra_lines),
            inline=False,
        )

    embed.add_field(
        name="🔗 全自動社群預覽修復（無需指令）",
        value="群友發送以下平台的連結時，Bot 會自動修復並貼出完美的嵌入式預覽：\n"
              "Twitter/X、Instagram、TikTok、Reddit、Pixiv、Bluesky、Bilibili。\n"
              "💡 訊息中包含 `fxignore` 即可跳過該則不修復。",
        inline=False
    )
    return embed


# ==================== 🪐 經濟系統：貨幣與升級 ====================

def build_currency_and_level_embed(current_lvl: int = None) -> discord.Embed:
    embed = discord.Embed(
        title="🪙 貨幣與升級系統",
        description="伺服器內共有兩種貨幣，分工明確：日常流通用 MP，稀有突破用 DC。\n"
                    "階級上限為 **200 等**，等級越高、`/hourly` 儲存池容量越大、提取倍率越高。",
        color=discord.Color.gold()
    )
    embed.add_field(
        name="✨ 萌力值（MP）",
        value="日常流通基礎貨幣。透過 `/hourly`（每小時提取儲存池）與 `/daily`（每日簽到）獲得，"
              "用於 `/level_info` 升級、`/reclaim` 補簽、`/power_plant` 發電廠注入。",
        inline=False
    )
    embed.add_field(
        name="🔮 次元結晶（DC）",
        value="稀有核心能源，用於 4 等以上升級與補簽。**唯一取得管道**：`/daily` 簽到時有 **10%** 機率"
              "掉落 **1~3 顆**，每滿 **7 天連續簽到**額外 **+1 顆**（最多 +3 顆）。",
        inline=False
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
        name="📈 等級關鍵節點（完整 1~200 等請用 /level_info 查詢）",
        value="\n".join(lines),
        inline=False
    )
    embed.add_field(
        name="💡 補充",
        value="• 4 等以上升級需消耗次元結晶 (DC)\n• 儲存池上限在 **Lv.15 起封頂於 168 小時（1 週）**",
        inline=False
    )
    return embed


# ==================== 🪐 經濟系統：天賦與特殊機制 ====================

def build_features_embed(bot=None) -> discord.Embed:
    # 🆕 舊版無條件寫「使用 /set_profession 選擇職業流派」，但那個指令不一定有載入
    # （它不在本資料夾的任何 cog 裡）。改成先確認指令真的存在，再決定要顯示哪段文案。
    if _command_exists(bot, "set_profession"):
        description = "使用 `/set_profession` 選擇職業流派，立即套用對應的經濟被動技能："
    else:
        description = ("⚠️ 本伺服器目前**未載入** `/set_profession`（負責個人檔案的模組不在線），"
                       "以下為天賦規則參考；實際職業仍沿用玩家資料中既有的設定。")
    embed = discord.Embed(
        title="⚔️ 職業天賦與特殊機制",
        description=description,
        color=discord.Color.blue()
    )
    embed.add_field(
        name="⚔️ 四大職業天賦",
        value="⏰ **時空定錨者**：`/hourly` 儲存池上限永久 +2 小時\n"
              "💥 **萌力暴走者**：`/daily` 有 10% 機率觸發暴擊，收益翻倍 (2.0x)\n"
              "🕵️ **結晶走私客**：`/reclaim` 消耗的結晶永遠 -1 顆（最低仍需 1 顆）\n"
              "🌱 **平民無業遊民**：升級至 Lv.2~3 時，MP 費用享 9 折",
        inline=False
    )
    embed.add_field(
        name="⚡ 萌力發電廠",
        value="`/power_plant [數量]` 奉獻並**銷毀** MP，全服累計達 **500,000 MP** 時超載觸發，"
              "之後 **24 小時內**全服 `/hourly`、`/daily` 額外乘上隨機 **1.2x~1.5x**。",
        inline=False
    )
    embed.add_field(
        name="🔮 補簽機制",
        value="忘記簽到？`/reclaim` 可扭轉因果：DC 消耗 = 第 N 次補簽扣 N 顆；"
              "MP 消耗 = `(500 × N) × 補簽後天數 × 階級折扣`，等級越高折扣越多（Lv.1 不打折 → Lv.100+ 趨近下限 0.02）。",
        inline=False
    )
    return embed


def build_commands_embed(bot=None, is_owner: bool = False) -> discord.Embed:
    """📜 指令手冊——完全由 bot.tree 目前實際載入的指令生成。

    設計原則：**指令樹是唯一真相**，COMMAND_GROUPS 只是排序提示。
      • 不存在的指令不會被列出（舊版列了 /set_profession、/set_background 等不存在的東西）
      • 存在的指令不會被漏掉（舊版漏了 /leaderboard、/nh、/nhsearch…），
        沒被分組表提到的會自動落到最後的「其他指令」區塊
      • ADMIN_ONLY_COMMANDS 只有 Owner 才看得到
    """
    try:
        tree_commands = list(bot.tree.get_commands()) if bot is not None else []
    except Exception:
        tree_commands = []

    available = {}
    for cmd in tree_commands:
        if not _is_slash_command(cmd):
            continue
        if cmd.name in ADMIN_ONLY_COMMANDS and not is_owner:
            continue
        available[cmd.name] = cmd

    embed = discord.Embed(
        title="📜 指令手冊",
        description=(
            "以下清單由機器人**目前的指令樹自動生成**，只會列出真正載入成功的指令。"
            f"\n目前可用：**{len(available)}** 個。"
        ),
        color=discord.Color.from_rgb(255, 105, 180),
    )

    if not available:
        embed.add_field(
            name="⚠️ 沒有偵測到任何指令",
            value="指令樹目前是空的，可能是 cog 還沒載入完成，請稍後再試。",
            inline=False,
        )
        return embed

    placed = set()
    for group_title, names in COMMAND_GROUPS:
        lines = []
        for name in names:
            cmd = available.get(name)
            if cmd is None:
                continue  # 這個指令不存在 → 略過，不再列出不存在的功能
            placed.add(name)
            lines.append(_describe(cmd))
        if lines:
            embed.add_field(name=group_title, value=_limit_lines(lines), inline=False)

    # 開發者專用指令單獨一區（只有 Owner 的 available 裡才會包含它們）
    admin_names = sorted(n for n in available if n in ADMIN_ONLY_COMMANDS)
    if admin_names:
        placed.update(admin_names)
        embed.add_field(
            name="🛡️ 開發者專用（僅 Owner 可見）",
            value=_limit_lines([_describe(available[n]) for n in admin_names]),
            inline=False,
        )

    # 沒有被分組表提到的指令一律補在這裡，確保「有這個功能就一定有說明」
    leftover = sorted(name for name in available if name not in placed)
    if leftover:
        embed.add_field(
            name="🔧 其他指令",
            value=_limit_lines([_describe(available[n]) for n in leftover]),
            inline=False,
        )

    embed.set_footer(text="⚙️ 自動生成 ｜ 新增或移除指令後會即時反映，不需要手動維護這份清單")
    return embed


ECONOMY_PAGES = {
    "currency_level": build_currency_and_level_embed,
    "features": build_features_embed,
    "commands": build_commands_embed,
}


# ==================== 🎰 賭場系統頁 ====================

def build_casino_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🎰 時空裂縫賭場",
        description="內建 **3 款** 賭場遊戲，皆可使用 MP 或 DC 下注。\n"
                    "💡 **獲勝抽稅：** 每局獲勝抽取 **5% 賭場稅**（MP 限定，DC 不抽稅），"
                    "稅金全自動即時注入全服 `/power_plant` 發電廠！",
        color=discord.Color.from_rgb(218, 41, 28)
    )
    embed.add_field(
        name="🎲 命運極限雙骰　`/dice [數量] [貨幣類型]`",
        value="玩家與莊家各擲兩顆骰子比大小，平手退回本金。\n"
              "💎 以 DC 下注擲出雙六豹子 `(6,6)`，賠率飆升至 **5 倍**！",
        inline=False
    )
    embed.add_field(
        name="🎡 次元幸運大輪盤　`/wheel [下注MP]`",
        value="💀 時空吞噬 40%（沒收）　⚖️ 原物返還 25%（退本金）\n"
              "✨ 萌力翻湧 20%（1.5x）　💥 狂暴突破 10%（3.0x）\n"
              "🔮 結晶裂縫 5%（不返還 MP，改掘出 1~3 顆 DC）",
        inline=False
    )
    embed.add_field(
        name="🃏 萌力 21 點　`/blackjack [下注MP]`",
        value="一般獲勝贏 **2 倍**、開局天選 Blackjack 額外 +1.5 倍（共 2.5 倍）、平手退本金。\n"
              "⏳ 按鈕互動 60 秒未操作自動逾時。",
        inline=False
    )
    embed.add_field(
        name="🌱🕵️ 職業流派特權",
        value="**平庸之福流**：`/dice`、`/wheel` 連輸 3 把自動返還 50% 本金救濟。\n"
              "**結晶走私客**：`/dice` 以 DC 下注失敗時 10% 機率全額保全本金。",
        inline=False
    )
    embed.set_footer(text="🎰 賭場為高風險娛樂功能，請理性下注")
    return embed


# ==================== 🪐 第二層：經濟系統子選單 ====================

class EconomySubDropdown(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="🪙 貨幣與升級", description="MP/DC 用途、取得方式與 1~200 等關鍵節點", value="currency_level", emoji="🪙"),
            discord.SelectOption(label="⚔️ 天賦與特殊機制", description="四大職業天賦、發電廠、補簽機制", value="features", emoji="⚔️"),
            discord.SelectOption(label="📜 指令總覽（全功能）", description="由指令樹自動生成，列出目前所有可用指令", value="commands", emoji="📜"),
            discord.SelectOption(label="⬅️ 返回主選單", description="回到 /help 首頁，切換其他章節", value="back_home", emoji="⬅️"),
        ]
        super().__init__(placeholder="選擇要查看的經濟系統子主題...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        page_key = self.values[0]

        if page_key == "back_home":
            embed = build_home_embed(interaction.client)
            await interaction.response.edit_message(embed=embed, view=HelpView())
            return

        builder = ECONOMY_PAGES[page_key]
        if page_key == "currency_level":
            p_data = await profile_store.get_profile(interaction.user.id)
            embed = builder(current_lvl=p_data["sign_level"])
        elif page_key == "commands":
            # 🆕 指令手冊改為從指令樹自動生成，需要 bot 本體與 Owner 身分
            embed = builder(interaction.client, is_owner=await _is_owner(interaction))
        elif page_key == "features":
            # 🆕 這一頁會先確認 /set_profession 是否真的存在，再決定顯示哪段文案
            embed = builder(interaction.client)
        else:
            embed = builder()

        _with_nav_footer(embed, "🪐 經濟系統說明書 ｜ 可重新選擇上方選單切換子主題或返回首頁")
        await interaction.response.edit_message(embed=embed, view=self.view)


class EconomySubView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.add_item(EconomySubDropdown())


# ==================== 📕 第一層：主選單 ====================

class HelpDropdown(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="📕 漫畫下載與社群修復", description="漫畫/本子下載與連結自動預覽修復功能", value="media", emoji="📕"),
            discord.SelectOption(label="🪐 萌力次元經濟系統", description="等級系統、四大天賦、發電廠與補簽機制", value="economy", emoji="🪐"),
            discord.SelectOption(label="🎰 時空裂縫賭場", description="雙骰、大輪盤、21點三款賭場遊戲規則", value="casino", emoji="🎰"),
        ]
        super().__init__(placeholder="選擇要查看的功能章節...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        selection = self.values[0]

        if selection == "media":
            embed = _with_nav_footer(build_media_embed(interaction.client), "⚙️ 下拉選單隨時切換分頁")
            await interaction.response.edit_message(embed=embed, view=self.view)

        elif selection == "economy":
            p_data = await profile_store.get_profile(interaction.user.id)
            embed = build_currency_and_level_embed(current_lvl=p_data["sign_level"])
            _with_nav_footer(embed, "🪐 經濟系統說明書 ｜ 可重新選擇上方選單切換子主題或返回首頁")
            await interaction.response.edit_message(embed=embed, view=EconomySubView())

        elif selection == "casino":
            await interaction.response.edit_message(embed=build_casino_embed(), view=self.view)


class HelpView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)  # 3 分鐘後選單過期
        self.add_item(HelpDropdown())


# ==================== 🏠 首頁 ====================

def build_home_embed(bot=None) -> discord.Embed:
    # 🆕 動態統計目前實際載入的指令數量，讓首頁的「說明」與「實況」永遠一致
    total_commands = 0
    try:
        if bot is not None:
            total_commands = sum(1 for c in bot.tree.get_commands() if _is_slash_command(c))
    except Exception:
        total_commands = 0

    embed = discord.Embed(
        title="✨ 綜合多功能助手 - 使用指南",
        description="本機器人整合了**漫畫下載**、**社群連結自動修復**、**🪐 萌力次元經濟系統**"
                    "與**🎰 時空裂縫賭場**。請用下方下拉選單切換章節查看完整說明：",
        color=discord.Color.from_rgb(255, 105, 180)
    )
    embed.add_field(
        name="📕 漫畫下載",
        value="`/jm`、`/jmv`、`/nzip`，以及 Twitter/IG/TikTok/Pixiv 等連結的自動預覽修復。",
        inline=False
    )
    embed.add_field(
        name="🪐 經濟系統",
        value="1~200 等升級、四大天賦流派、全服發電廠、補簽機制——點選單「🪐 萌力次元經濟系統」查看。",
        inline=False
    )
    embed.add_field(
        name="🎰 賭場系統",
        value="雙骰、大輪盤、21點三款遊戲——點選單「🎰 時空裂縫賭場」查看規則與賠率。",
        inline=False
    )
    if total_commands:
        embed.add_field(
            name="📜 完整指令清單",
            value=f"目前共載入 **{total_commands}** 個指令。"
                  "點「🪐 萌力次元經濟系統」→「📜 指令總覽（全功能）」可看到由指令樹自動生成的完整清單。",
            inline=False
        )
    embed.set_footer(text="⚙️ 下拉選單隨時切換分頁 ｜ 經濟數值與指令清單皆會隨實際設定自動更新")
    return embed


def setup_help(bot):
    """將 /help 斜線指令註冊到機器人的 Command Tree 中"""
    @bot.tree.command(name='help', description='顯示機器人的所有功能與完整指令指南')
    async def custom_help(interaction: discord.Interaction):
        await interaction.response.send_message(
            embed=build_home_embed(interaction.client), view=HelpView()
        )