# -*- coding: utf-8 -*-
"""
萌力次元經濟與簽到等級系統 Cog 模組
包含 /hourly, /daily, /power_plant, /level_info 以及補簽互動

🔮 次元結晶 (DC) 多元掉落管道（取代原先僅有 /daily 一種、且掉落量遠不及需求的版本）：
  • /daily 簽到：45% 機率掉落 25~50 顆，每 5 天連續簽到額外 +8 顆（封頂 +80）
  • 每日發言：每則訊息 3% 機率掉落 1 顆，不限次數（見 EconomySystem.on_message）
  • 進語音頻道：停留滿 5 分鐘後 55% 機率掉落 5~10 顆（見 EconomySystem.on_voice_state_update）
配合 profile_store.SIGN_LEVEL_MATRIX 重新設計後的 DC 需求曲線（總量約 7,597 顆），
目標讓中等活躍玩家約 6 個月、低活躍玩家約 6~7 個月可練滿 Lv.200。
"""
import os
import time
import random
import datetime
import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional

from . import profile_store  # 修正後的相對路徑引入 # 引入剛剛升級好的儲存層
from logger_config import log

class EconomySystem(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.VOICE_MIN_SECONDS = 5 * 60  # 語音掉落所需的最低停留時間：5分鐘

    # ==================== 🔮 DC 掉落管道一：每日發言 ====================
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """
        每則訊息皆有 3% 機率掉落 1 顆次元結晶 (DC)，不限觸發次數。
        排除 bot 自身與其他 bot 的訊息，避免被其他自動發言機器人異常洗出大量 DC。
        """
        if message.author.bot:
            return
        if random.random() < 0.03:
            await profile_store.add_dc(message.author.id, 1)
            try:
                await message.add_reaction("🔮")  # 輕量提示，不額外發訊息打擾頻道
            except Exception:
                pass  # 缺少權限或訊息已被刪除等情況直接忽略，不影響掉落本身

    # ==================== 🔮 DC 掉落管道二：語音頻道停留 ====================
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before, after):
        """
        玩家進入語音頻道並停留滿 5 分鐘後（以「離開或切換頻道時的時間差」結算），
        有 55% 機率掉落 5~10 顆次元結晶 (DC)。
        進出刻意刷頻道無法投機觸發，因為時間差不滿 5 分鐘時不會給予任何掉落。
        """
        if member.bot:
            return

        was_in_voice = before.channel is not None
        now_in_voice = after.channel is not None

        if not was_in_voice and now_in_voice:
            # 從「不在語音」變成「在語音」：開始計時
            await profile_store.set_voice_join_time(member.id, time.time())
            return

        if was_in_voice and not now_in_voice:
            # 從「在語音」變成「不在語音」（離開所有語音頻道）：結算掉落並清空計時
            join_time = await profile_store.get_voice_join_time(member.id)
            if join_time > 0:
                elapsed = time.time() - join_time
                if elapsed >= self.VOICE_MIN_SECONDS and random.random() < 0.55:
                    dc_dropped = random.randint(5, 10)
                    await profile_store.add_dc(member.id, dc_dropped)
            await profile_store.set_voice_join_time(member.id, 0.0)
            return

        # 在不同語音頻道間切換（before.channel 與 after.channel 皆存在但不同）：
        # 視為延續同一段停留時間，不重新計時、不重複結算。

    # ==================== 🪐 核心指令一：每小時提取 ====================
    @app_commands.command(name="hourly", description="提取時空累積的萌力值 (MP)")
    async def hourly(self, interaction: discord.Interaction):
        await interaction.response.defer()
        user_id = interaction.user.id
        
        # 讀取玩家資料
        p_data = await profile_store.get_profile(user_id)
        level = p_data["sign_level"]
        profession = p_data["profession"]
        last_time = p_data["last_hourly_time"]
        
        # 獲取該等級的上限與倍率
        lvl_info = profile_store.SIGN_LEVEL_MATRIX.get(level, profile_store.SIGN_LEVEL_MATRIX[1])
        base_max_hours = lvl_info["hours"]
        h_mult = lvl_info["h_mult"]
        
        # 🎭 職業天賦判定一：【時空定錨】流派 (儲存時數上限 +2 小時)
        talent = profile_store.get_profession_talent(profession)
        max_hours = base_max_hours
        if talent == "時空定錨":
            max_hours += 2
            
        # 計算過去累積的時間 (小時)
        now_time = time.time()
        elapsed_seconds = now_time - last_time
        elapsed_hours = elapsed_seconds / 3600.0
        
        # 檢查是否滿 1 小時可以領取
        if elapsed_hours < 1.0:
            rem_seconds = int(3600 - elapsed_seconds)
            min_left = rem_seconds // 60
            sec_left = rem_seconds % 60
            await interaction.followup.send(
                f"⏳ 萌力尚未凝聚完畢！請再等待 `{min_left}` 分 `{sec_left}` 秒後方可進行時空提取。",
                ephemeral=True
            )
            return
            
        # 限制不超過最大儲存時數
        actual_hours = min(elapsed_hours, float(max_hours))
        
        # 基礎獎勵每小時隨機 80~120 MP
        base_reward_per_hour = random.randint(80, 120)
        calculated_base = int(base_reward_per_hour * actual_hours)
        
        # 計算全服發電廠 Buff (達標加成 1.2x ~ 1.5x)
        global_buff_active = await profile_store.check_global_buff()
        g_mult = random.uniform(1.2, 1.5) if global_buff_active else 1.0
        
        # 最終收益公式
        final_reward = int(calculated_base * h_mult * g_mult)
        
        # 更新資料庫
        await profile_store.add_mp(user_id, final_reward)
        # 🛠️ Bug 修復：原先只把 last_hourly_time 往前推進「實際被領取的那段時數」
        # (used_seconds = int(actual_hours)*3600)，但當玩家擱置時間超過儲存池上限時，
        # actual_hours 會被夾住變小，導致「超出上限、本來就領不到的那段時間差」永遠卡在
        # last_time 與 now_time 之間消不掉。玩家只要曾經拖超過上限沒領，之後就能在短時間內
        # 連續呼叫 /hourly 把這筆欠帳一次次領完，等於變相可以無限連續領取。
        # 正確做法：只要這次成功領取，就直接把 last_hourly_time 設為現在，清空所有欠帳。
        await profile_store.update_hourly_time(user_id, now_time)
        
        # 建立精美回應面板
        embed = discord.Embed(
            title="🪐 時空萌力提取成功！",
            description=f"恭喜 {interaction.user.mention} 成功提取過往儲存的萌能量！",
            color=discord.Color.from_rgb(0, 210, 255)
        )
        embed.add_field(name="✨ 獲得萌力值", value=f"**+{final_reward:,} MP**", inline=False)
        
        buff_str = f"✕ 階級倍率 {h_mult}倍"
        if global_buff_active:
            buff_str += f" ✕ 🪐全服發電廠超載加成 {g_mult:.2f}倍"
            
        embed.add_field(
            name="📊 算式詳情", 
            value=f"`[ {int(actual_hours)}小時基礎: {calculated_base} MP ] {buff_str} = {final_reward} MP`", 
            inline=False
        )
        embed.add_field(name="🎭 啟動天賦", value=f"`【{talent}】` (目前個人階級: Lv.{level})", inline=True)
        
        if int(elapsed_hours) >= max_hours:
            embed.set_footer(text="⚠️ 提示：你的萌力儲存池先前已達上限滿載，記得定時提領喔！")
            
        await interaction.followup.send(embed=embed)


    # ==================== 🌸 核心指令二：每日簽到 ====================
    @app_commands.command(name="daily", description="每日點擊簽到，獲取大量萌力值與維繫連簽")
    async def daily(self, interaction: discord.Interaction):
        await interaction.response.defer()
        user_id = interaction.user.id
        
        p_data = await profile_store.get_profile(user_id)
        level = p_data["sign_level"]
        profession = p_data["profession"]
        last_daily = p_data["last_daily_time"]
        streak = p_data["streak_days"]
        
        # 判定日期
        today_str = datetime.date.today().strftime("%Y-%m-%d")
        yesterday_str = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        
        if last_daily == today_str:
            await interaction.followup.send("❌ 你今天已經完成簽到了，明天再來吧！", ephemeral=True)
            return
            
        # 計算連續簽到
        if last_daily == yesterday_str:
            new_streak = streak + 1
        elif last_daily == "":
            new_streak = 1
        else:
            # 斷簽了！跳出彈窗提示可以使用補簽
            new_streak = 1
            if streak > 1:
                await interaction.followup.send(
                    f"😭 哎呀！你中斷了先前連續 `{streak}` 天的簽到！\n"
                    f"💡 想要挽回記錄嗎？請輸入 `/reclaim` 扭轉時空進行補簽吧！(今天先不幫你簽到)", 
                    ephemeral=True
                )
                return

        lvl_info = profile_store.SIGN_LEVEL_MATRIX.get(level, profile_store.SIGN_LEVEL_MATRIX[1])
        d_mult = lvl_info["d_mult"]
        
        # 每日基礎發放隨機 1500 ~ 2500 MP，連簽越多下限越高
        base_min = min(1500 + (new_streak * 50), 2400)
        base_reward = random.randint(base_min, 2500)
        
        # 🎭 職業天賦判定二：【萌力暴走】流派 (10% 機率簽到大暴擊，獲得 2 倍萌力)
        talent = profile_store.get_profession_talent(profession)
        is_crit = False
        crit_mult = 1.0
        if talent == "萌力暴走" and random.random() < 0.10:
            is_crit = True
            crit_mult = 2.0
            
        # 全服發電廠加成
        global_buff_active = await profile_store.check_global_buff()
        g_mult = random.uniform(1.2, 1.5) if global_buff_active else 1.0
        
        # 總分發
        final_daily = int(base_reward * d_mult * crit_mult * g_mult)
        
        # 🔮 次元結晶 (DC) 掉落判定（主力管道）：
        # 原始 10%/1~3顆 的版本遠遠跟不上 DC 需求（即使搭配重新設計後總量已大幅壓低的
        # SIGN_LEVEL_MATRIX，練滿仍需數十個月）。提高為 45% 機率、掉落 25~50 顆，
        # 每滿 5 天連續簽到再額外 +8 顆掉落量（封頂 +80），讓長期連簽玩家明顯加速累積。
        dc_dropped = 0
        if random.random() < 0.45:
            bonus_dc = min((new_streak // 5) * 8, 80)
            dc_dropped = random.randint(25, 50) + bonus_dc
            await profile_store.add_dc(user_id, dc_dropped)
        
        # 寫入資料庫
        await profile_store.add_mp(user_id, final_daily)
        await profile_store.save_daily_signin(user_id, today_str, new_streak)
        
        # 渲染精美 Embed
        embed = discord.Embed(
            title="🌸 每日簽到成功！",
            description=f"歡迎回來！你已連續簽到 **{new_streak}** 天！",
            color=discord.Color.from_rgb(255, 105, 180)
        )
        embed.add_field(name="✨ 獲得萌力值", value=f"**+{final_daily:,} MP**", inline=False)
        
        if dc_dropped > 0:
            embed.add_field(name="🔮 裂縫掉落！", value=f"**+{dc_dropped} 次元結晶 (DC)**", inline=False)
        
        calc_str = f"`[ 基礎隨機: {base_reward} MP ] ✕ [ 每日倍率: {d_mult}倍 ]"
        if is_crit:
            calc_str += " ✕ 💥【天賦暴擊 2.0倍】"
        if global_buff_active:
            calc_str += f" ✕ 🪐全服加成 {g_mult:.2f}倍"
        calc_str += f" = {final_daily} MP`"
        
        embed.add_field(name="📊 算式詳情", value=calc_str, inline=False)
        await interaction.followup.send(embed=embed)


    # ==================== ⛩️ 核心指令三：全服發電廠注入 ====================
    @app_commands.command(name="power_plant", description="將自身的萌力值注入到全服發電廠，達標開啟全服簽到狂歡 Buff")
    @app_commands.describe(注入數量="你想奉獻並銷毀的萌力值 (MP) 數量")
    async def power_plant(self, interaction: discord.Interaction, 注入數量: int):
        if 注入數量 <= 0:
            await interaction.response.send_message("❌ 注入數量必須大於 0！", ephemeral=True)
            return
            
        await interaction.response.defer()
        user_id = interaction.user.id
        p_data = await profile_store.get_profile(user_id)
        
        if p_data["moe_point"] < 注入數量:
            await interaction.followup.send(f"❌ 你的萌力值不足！目前僅有: `{p_data['moe_point']}` MP", ephemeral=True)
            return
            
        # 執行發電廠注入 API
        res = await profile_store.contribute_to_power_plant(user_id, 注入數量)
        
        embed = discord.Embed(
            title="⛩️ 萌力注入發電廠成功！",
            description=f"感謝 {interaction.user.mention} 奉獻出的高純度萌能量！這些萌力已被高壓銷毀轉化為全服電力！",
            color=discord.Color.gold()
        )
        embed.add_field(name="⚡ 你本次注入", value=f"**{注入數量:,} MP**", inline=True)
        embed.add_field(name="🔋 目前儲能進度", value=f"**{res['current']:,}** / {res['target']:,} MP", inline=True)
        
        if res["triggered"]:
            embed.add_field(
                name="🎉 💥 發電廠超載大爆發！", 
                value="**全服發電廠已進入狂暴負載狀態！全體玩家在接下來的 24 小時內，簽到提取將獲得隨機 1.2x ~ 1.5x 的瘋狂乘算加成！**", 
                inline=False
            )
            # 頒發全服爆發隱藏大成就
            await profile_store.add_achievement(user_id, "⚡ 穗織首席發電工程師")
            
        await interaction.followup.send(embed=embed)


    # ==================== 🏆 核心指令：全服排行榜 ====================
    @app_commands.command(name="leaderboard", description="查看跨伺服器全服排行榜，看看誰是萌力次元的頂尖玩家")
    @app_commands.describe(排序依據="選擇要依據哪個項目排名")
    @app_commands.choices(排序依據=[
        app_commands.Choice(name="🪐 萌力階級 (等級)", value="level"),
        app_commands.Choice(name="🪙 萌力資產 (MP)", value="mp"),
        app_commands.Choice(name="🔮 次元結晶 (DC)", value="dc"),
        app_commands.Choice(name="🔥 連續簽到天數", value="streak"),
    ])
    async def leaderboard(self, interaction: discord.Interaction, 排序依據: app_commands.Choice[str] = None):
        await interaction.response.defer()

        # 🆕 全新功能：排行榜本身是跨伺服器的，因為 profiles.json 是全域共用儲存，
        # 不分玩家屬於哪個伺服器（與 /profile 改版後「只要知道UID就能查任何人」精神一致）。
        sort_key = 排序依據.value if 排序依據 else "level"
        sort_label = {
            "level": "🪐 萌力階級",
            "mp": "🪙 萌力資產 (MP)",
            "dc": "🔮 次元結晶 (DC)",
            "streak": "🔥 連續簽到天數",
        }[sort_key]

        rankings = await profile_store.get_leaderboard(sort_by=sort_key, limit=10)

        if not rankings:
            await interaction.followup.send("📭 目前還沒有任何玩家資料，排行榜是空的！", ephemeral=True)
            return

        medal = ["🥇", "🥈", "🥉"]
        lines = []
        for idx, (user_id, value) in enumerate(rankings):
            rank_prefix = medal[idx] if idx < 3 else f"`#{idx + 1}`"

            # 🛠️ 防呆：使用者可能已經刪除帳號或被Discord停權，fetch_user 會失敗，
            # 這種情況顯示「未知使用者」並附上UID，而不是讓整個排行榜指令噴錯中斷。
            try:
                user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
                name = user.name
            except Exception:
                name = f"未知使用者 (UID:{user_id})"

            if sort_key == "level":
                value_str = f"Lv.{value}"
            elif sort_key == "mp":
                value_str = f"{profile_store.format_cn_number(int(value))} MP"
            elif sort_key == "dc":
                value_str = f"{int(value):,} DC"
            else:  # streak
                value_str = f"{int(value):,} 天"

            lines.append(f"{rank_prefix} **{name}** — {value_str}")

        embed = discord.Embed(
            title="🏆 萌力次元全服排行榜",
            description=f"排序依據：**{sort_label}** ｜ 跨伺服器統計，前 {len(rankings)} 名",
            color=discord.Color.gold()
        )
        embed.add_field(name="📋 排名", value="\n".join(lines), inline=False)
        embed.set_footer(text="💡 用 /leaderboard 並選擇不同的排序依據，可切換查看其他排名項目")

        await interaction.followup.send(embed=embed)


    # ==================== 📊 核心指令四：查看換算表與升級 ====================
    @app_commands.command(name="level_info", description="查看 1~200 萌力階級換算表，並升級自己的簽到等級")
    async def level_info(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        p_data = await profile_store.get_profile(user_id)
        current_lvl = p_data["sign_level"]
        
        embed = discord.Embed(
            title="📊 萌力階級(簽到等級) 數值矩陣一覽",
            description=f"你目前所在的階級為：**Lv.{current_lvl}**\n點擊下方按鈕可直接申請花費儲備晉升到下一級！",
            color=discord.Color.purple()
        )
        
        # 擷取代表性節點塞入面板，防止文字超過 Discord embed 2048 字元上限。
        # 200 級分為 9 大境界，這裡每個境界區段挑一個代表等級，
        # 並永遠額外附上玩家「目前等級」與「下一級」方便對照升級花費。
        show_lvls = sorted(set([1, 10, 25, 50, 75, 100, 125, 150, 175, 200,
                                 current_lvl, min(current_lvl + 1, profile_store.LEVEL_CAP)]))
        matrix_str = ""
        for l in show_lvls:
            info = profile_store.SIGN_LEVEL_MATRIX[l]
            prefix = "⭐" if l == current_lvl else ("🔜" if l == current_lvl + 1 else "•")
            mp_str = profile_store.format_cn_number(info['mp_cost'])
            matrix_str += (f"{prefix} **Lv.{l}** | 需 {mp_str}MP / {info['dc_cost']:,}DC | "
                            f"存{info['hours']}h | 每時{info['h_mult']}x | 每日{info['d_mult']}x\n")
            
        embed.add_field(name="📈 階級核心換算表 (1 ~ 200，含你的目前/下一級)", value=matrix_str, inline=False)
        embed.set_footer(text="💡 4等以上升級需要消耗次元結晶 (DC) | ⭐ = 目前等級 🔜 = 下一級")
        
        # 建立前台 View 按鈕
        view = UpgradeButtonView(user_id, current_lvl)
        await interaction.response.send_message(embed=embed, view=view)


    # ==================== 🔮 核心指令五：時空扭轉補簽 ====================
    @app_commands.command(name="reclaim", description="扭轉時空進行補簽，挽回斷簽的連續天數")
    async def reclaim(self, interaction: discord.Interaction):
        await interaction.response.defer()
        user_id = interaction.user.id
        
        p_data = await profile_store.get_profile(user_id)
        last_daily = p_data["last_daily_time"]
        profession = p_data["profession"]
        level = p_data["sign_level"]
        streak = p_data["streak_days"]
        N = p_data["total_reclaims"] + 1  # 這是第 N 次補簽
        
        today_str = datetime.date.today().strftime("%Y-%m-%d")
        yesterday_str = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        
        # 檢查是否不需要補簽
        if last_daily == today_str or last_daily == yesterday_str:
            await interaction.followup.send("💡 你的時空定錨很穩固，目前連續簽到並未中斷，不需要補簽喔！", ephemeral=True)
            return
            
        # 🔮 核心公式計算：嚴格對接你的補簽費用截圖
        # 1. 次元結晶 (DC) 扣除 = N 顆
        base_dc_cost = N
        
        # 🎭 職業天賦判定三：【結晶走私】流派 (補簽消耗的 DC 享有 -1 顆優惠，最低扣 1 顆)
        talent = profile_store.get_profession_talent(profession)
        if talent == "結晶走私" and base_dc_cost > 1:
            dc_cost = base_dc_cost - 1
        else:
            dc_cost = base_dc_cost
            
        # 2. 階級折扣係數：指數衰減版，1~200 等皆適用（詳見 profile_store.get_reclaim_discount_factor）
        discount_factor = profile_store.get_reclaim_discount_factor(level)
        
        # 3. 萌力值 (MP) 扣除 = (500 * N) * (擬補簽後的連續天數) * 階級折扣
        target_streak = streak + 1
        mp_cost = int((500 * N) * target_streak * discount_factor)
        
        # 餘額不足判定（金額一律改用中文單位顯示，避免補簽次數N過大時數字過長）
        if p_data["moe_point"] < mp_cost or p_data["dimension_crystal"] < dc_cost:
            mp_cost_str = profile_store.format_cn_number(mp_cost)
            cur_mp_str = profile_store.format_cn_number(p_data['moe_point'])
            await interaction.followup.send(
                f"❌ 補簽所需的時空能量不足！\n"
                f"本次補簽 (第 {N} 次) 費用預估：\n"
                f"• 需消耗: `{mp_cost_str}` MP / `{dc_cost:,}` DC\n"
                f"• 你目前: `{cur_mp_str}` MP / `{p_data['dimension_crystal']:,}` DC",
                ephemeral=True
            )
            return
            
        # 執行扣費並補簽
        await profile_store.execute_reclaim(user_id, mp_cost, dc_cost)
        # 把上一次簽到日期強行扭轉篡改為昨天，讓玩家可以立刻執行當天的 /daily 簽到！
        await profile_store.save_daily_signin(user_id, yesterday_str, target_streak)
        
        mp_cost_str = profile_store.format_cn_number(mp_cost)
        embed = discord.Embed(
            title="🔮 時空扭轉！補簽成功！",
            description=f"成功消耗資源發動時空魔法，強行接回斷掉的因果鏈！",
            color=discord.Color.teal()
        )
        embed.add_field(name="💸 扣除費用", value=f"`-{mp_cost_str} MP` / `-{dc_cost:,} DC`", inline=True)
        embed.add_field(name="🔥 拯救天數", value=f"連續天數已安全恢復至 **{target_streak}** 天！", inline=True)
        embed.add_field(name="💬 算式依據", value=f"`(500 ✕ 補簽次數{N}) ✕ 天數{target_streak} ✕ 階級折扣{discount_factor:.3f} = {mp_cost_str} MP`", inline=False)
        embed.set_footer(text="🎉 補簽完畢！你現在可以立刻輸入 /daily 領取今天的簽到獎勵囉！")
        
        await interaction.followup.send(embed=embed)


# ==================== 🛠️ 升級互動 View 按鈕組件 ====================
class UpgradeButtonView(discord.ui.View):
    def __init__(self, user_id: int, current_lvl: int):
        super().__init__(timeout=60.0)
        self.user_id = user_id
        self.current_lvl = current_lvl
        
        # 如果已經滿級，停用按鈕
        if self.current_lvl >= profile_store.LEVEL_CAP:
            self.children[0].disabled = True
            self.children[0].label = "已達最高極限階級"

    @discord.ui.button(label="🔼 申請晉升下一階級", style=discord.ButtonStyle.success)
    async def upgrade_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 嚴格驗證按按鈕的人是不是指令發起者
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ 你不是這份換算表單的擁有者，無法幫他人代點升級！", ephemeral=True)
            return
            
        target_lvl = self.current_lvl + 1
        if target_lvl > profile_store.LEVEL_CAP:
            await interaction.response.send_message(f"❌ 你已經滿級 (Lv.{profile_store.LEVEL_CAP})，無法再晉升了！", ephemeral=True)
            return
            
        p_data = await profile_store.get_profile(self.user_id)
        profession = p_data["profession"]
        
        # 獲取升級所需消耗
        lvl_info = profile_store.SIGN_LEVEL_MATRIX[target_lvl]
        base_mp_cost = lvl_info["mp_cost"]
        dc_cost = lvl_info["dc_cost"]
        
        # 🎭 職業天賦判定四：【平庸之福】流派 (升級 Lv.2 ~ Lv.3 時費用享有 9 折大恩惠)
        talent = profile_store.get_profession_talent(profession)
        mp_cost = base_mp_cost
        if talent == "平庸之福" and target_lvl in [2, 3]:
            mp_cost = int(base_mp_cost * 0.9)
            
        # 驗證餘額（巨大數字改用中文單位顯示，避免 embed 訊息過長）
        if p_data["moe_point"] < mp_cost or p_data["dimension_crystal"] < dc_cost:
            mp_cost_str = profile_store.format_cn_number(mp_cost)
            cur_mp_str = profile_store.format_cn_number(p_data['moe_point'])
            await interaction.response.send_message(
                f"❌ 晉升失敗！所需能量餘額不足！\n"
                f"升級到 Lv.{target_lvl} 需要: `{mp_cost_str}` MP / `{dc_cost:,}` DC\n"
                f"你目前擁有: `{cur_mp_str}` MP / `{p_data['dimension_crystal']:,}` DC",
                ephemeral=True
            )
            return
            
        # 執行扣費升級
        await profile_store.execute_upgrade(self.user_id, target_lvl, mp_cost, dc_cost)
        
        # 滿級特殊驚喜
        if target_lvl == profile_store.LEVEL_CAP:
            await profile_store.add_achievement(self.user_id, "👑 終焉・大羅法天")
            
        mp_cost_str = profile_store.format_cn_number(mp_cost)
        await interaction.response.send_message(
            f"🎉 🎉 恭喜 {interaction.user.mention} 成功晉升至 **階級 Lv.{target_lvl}**！\n"
            f"💸 扣除費用：`-{mp_cost_str} MP` / `-{dc_cost:,} DC`！未來你的每小時與每日簽到產出將大幅激增！",
            ephemeral=False
        )
        
        # 停用按鈕防重複點擊
        self.children[0].disabled = True
        self.children[0].label = f"已晉升至 Lv.{target_lvl}"
        await interaction.message.edit(view=self)


# 模組 setup 掛載
async def setup(bot: commands.Bot):
    await bot.add_cog(EconomySystem(bot))