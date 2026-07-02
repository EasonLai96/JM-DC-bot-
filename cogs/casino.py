# -*- coding: utf-8 -*-
"""
時空裂縫賭場系統 Cog 模組 (casino.py)
- 內建 21點、次元幸運大輪盤、命運極限雙骰
- 獲勝抽取 5% 賭場稅，全自動即時注入全服 /power_plant 發電廠大活動！
- 完美連動四大職業流派被動特權（結晶走私流、平庸之福流）
- 嚴格採用底層金流非同步鎖，杜絕併發洗錢 Bug
"""
import discord
from discord import app_commands
from discord.ext import commands
import random
import asyncio
import time
from typing import Literal

# 引入你的經濟系統儲存層
from . import profile_store 

class Casino(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # 紀錄玩家的連輸場數，用於「平庸之福流」補償判定 {user_id: consecutive_losses}
        self.loss_streaks = {}
        # 🔒 紀錄目前正在進行賭局（已扣款但尚未結算）的玩家，防止旋轉動畫等待期間被重複觸發造成併發扣款漏洞
        self.active_players = set()

    def _check_profession_flow(self, prof_name: str) -> str:
        """根據職業名稱精準分析流派"""
        if not prof_name:
            return "平庸之福流"
        if any(k in prof_name for k in ["科技", "程式", "宅", "潛水", "開發"]):
            return "時空定錨流"
        if any(k in prof_name for k in ["商", "精明", "錢", "走私", "粗心"]):
            return "結晶走私流"
        if any(k in prof_name for k in ["無業", "遊民", "新手", "平民"]):
            return "平庸之福流"
        return "萌力暴走流"

    def _calc_tax(self, amount: int, rate: float = 0.05) -> int:
        """統一計算賭場稅。只要有應稅金額（amount > 0）就至少抽 1，避免小額下注因無條件捨去而完全免稅。"""
        if amount <= 0:
            return 0
        tax = int(amount * rate)
        if tax <= 0:
            tax = 1
        return min(tax, amount)

    async def _handle_casino_tax(self, tax_amount: int):
        """將抽稅的 MP 自動注入到全服發電廠中"""
        if tax_amount <= 0:
            return
        async with profile_store._lock:
            g_data = profile_store._load_global()
            g_data["total_contributed"] += tax_amount
            # 檢查是否觸發超載 (PP_TARGET，預設 500,000 MP)
            if g_data["total_contributed"] >= profile_store.PP_TARGET:
                g_data["total_contributed"] = g_data["total_contributed"] % profile_store.PP_TARGET  # 餘額留到下一輪
                g_data["buff_end_time"] = time.time() + 86400  # 觸發全服 24 小時倍率狂歡
            profile_store._save_global(g_data)

    # ==========================================
    # 🎲 遊戲一：命運極限雙骰 (/dice)
    # ==========================================
    @app_commands.command(name="dice", description="🪐 時空裂縫賭場：命運雙骰對決，支援結晶豪賭，勝負一瞬間！")
    @app_commands.describe(
        數量="你要下注的貨幣數量",
        貨幣類型="請選擇下注 MP 還是稀有的 DC 結晶"
    )
    async def casino_dice(self, interaction: discord.Interaction, 數量: int, 貨幣類型: Literal["萌力值 (MP)", "次元結晶 (DC)"]):
        if 數量 <= 0:
            await interaction.response.send_message("❌ 下注數量必須大於 0！", ephemeral=True)
            return

        await interaction.response.defer()
        uid = interaction.user.id

        if uid in self.active_players:
            await interaction.followup.send("⏳ 你目前還有一局賭局正在結算中，請稍候再試！", ephemeral=True)
            return
        self.active_players.add(uid)

        try:
            p = await profile_store.get_profile(uid)
            flow = self._check_profession_flow(p["profession"])

            # 1. 檢查資產
            if 貨幣類型 == "萌力值 (MP)":
                if p["deposit"] < 數量:
                    await interaction.followup.send(f"❌ 你的萌力值 (MP) 不足！當前餘額：`{p['deposit']:,}` MP")
                    return
            else:
                if p["dimension_crystal"] < 數量:
                    await interaction.followup.send(f"❌ 你的次元結晶 (DC) 不足！當前餘額：`{p['dimension_crystal']:,}` DC")
                    return

            # 2. 擲骰子判定
            player_d1, player_d2 = random.randint(1, 6), random.randint(1, 6)
            bot_d1, bot_d2 = random.randint(1, 6), random.randint(1, 6)
            p_total = player_d1 + player_d2
            b_total = bot_d1 + bot_d2

            embed = discord.Embed(title="🎲 命運極限雙骰 🪐", color=discord.Color.purple())
            embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
            embed.add_field(name="🔮 玩家擲出", value=f"⚁ `[{player_d1}]` + ⚄ `[{player_d2}]` = **{p_total} 點**", inline=True)
            embed.add_field(name="🤖 莊家擲出", value=f"⚃ `[{bot_d1}]` + ⚃ `[{bot_d2}]` = **{b_total} 點**", inline=True)

            # 3. 結算邏輯
            if p_total > b_total:
                # 玩家獲勝：抽稅 5%
                tax = self._calc_tax(數量) if 貨幣類型 == "萌力值 (MP)" else 0  # 結晶不抽稅
                win_net = 數量 - tax
            
                if 貨幣類型 == "萌力值 (MP)":
                    await profile_store.add_mp(uid, win_net)
                    await self._handle_casino_tax(tax)
                    tax_text = f" (已扣除 5% 賭場稅 `{tax:,}` MP 注入發電廠)" if tax > 0 else ""
                    embed.description = f"🎉 **恭喜獲勝！** 淨賺了 **`{win_net:,}` MP**！{tax_text}"
                else:
                    # 驚喜彩蛋：若玩家擲出雙六豹子，賠率飆升 5x
                    if player_d1 == 6 and player_d2 == 6:
                        win_net = 數量 * 4 # 拿回本金外加 4 倍
                        embed.description = f"🔥 **【主宰天罰】觸發！** 你擲出了極致豹子 `(6, 6)`！獲取 **5 倍終極大暴擊**，贏得 **`{win_net:,}` DC**！"
                    else:
                        embed.description = f"🎉 **恭喜獲勝！** 贏得了 **`{win_net:,}` 次元結晶 (DC)**！"
                    await profile_store.add_dc(uid, win_net)
                
                self.loss_streaks[uid] = 0
                embed.color = discord.Color.green()

            elif p_total < b_total:
                # 玩家失敗
                if 貨幣類型 == "萌力值 (MP)":
                    await profile_store.add_mp(uid, -數量)
                    embed.description = f" 📉 **大失敗！** 損失了 **`{數量:,}` MP**。輸掉的資金已化為虛無..."
                
                    # 平庸之福流 連輸安慰補償判定
                    if flow == "平庸之福流":
                        self.loss_streaks[uid] = self.loss_streaks.get(uid, 0) + 1
                        if self.loss_streaks[uid] >= 3:
                            refund = int(數量 * 0.5)
                            await profile_store.add_mp(uid, refund)
                            embed.description += f"\n🌱 **【平庸之福】特權發動：** 偵測到連續慘輸 {self.loss_streaks[uid]} 把，命運時空同情補償返還 **`{refund:,}` MP** 救濟金！"
                            self.loss_streaks[uid] = 0
                    else:
                        self.loss_streaks[uid] = 0
                else:
                    # 結晶走私流 特權判定
                    if flow == "結晶走私流" and random.random() < 0.10:
                        embed.description = f"🕵️ **【結晶走私】特權大成功！** 雖然點數輸了，但你利用暗影網路走私技術，成功保全了本金 **`{數量:,}` DC** 免遭扣除！"
                    else:
                        await profile_store.add_dc(uid, -數量)
                        embed.description = f"💀 **時空崩塌！** 損失了 **`{數量:,}` 次元結晶 (DC)**。"
                embed.color = discord.Color.red()
            
            else:
                # 平手
                embed.description = "🤝 **因果重疊！雙方平手**。下注本金全額退回。"
                embed.color = discord.Color.blue()

            embed.set_footer(text=f"✨ 目前特權流派屬性：【{flow}】")
            await interaction.followup.send(embed=embed)
        finally:
            # 🔒 無論成功、失敗或提早 return，都務必釋放鎖，避免玩家被永久卡住
            self.active_players.discard(uid)


    # ==========================================
    # 🎡 遊戲二：次元幸運大輪盤 (/wheel)
    # ==========================================
    @app_commands.command(name="wheel", description="🪐 時空裂縫賭場：轉動次元命運大輪盤，機率直接摸出極稀有結晶！")
    @app_commands.describe(下注mp="你要注入大輪盤的 MP 數量")
    async def casino_wheel(self, interaction: discord.Interaction, 下注mp: int):
        if 下注mp <= 0:
            await interaction.response.send_message("❌ 下注數量必須大於 0！", ephemeral=True)
            return

        await interaction.response.defer()
        uid = interaction.user.id

        # 🔒 防止玩家在旋轉動畫等待期間重複觸發本指令造成併發扣款漏洞
        if uid in self.active_players:
            await interaction.followup.send("⏳ 你目前還有一局賭局正在結算中，請稍候再試！", ephemeral=True)
            return
        self.active_players.add(uid)

        try:
            p = await profile_store.get_profile(uid)
            flow = self._check_profession_flow(p["profession"])

            if p["deposit"] < 下注mp:
                await interaction.followup.send(f"❌ 你的萌力值 (MP) 不足！當前餘額：`{p['deposit']:,}` MP")
                return

            # 先扣除本金
            await profile_store.add_mp(uid, -下注mp)

            # 動態旋轉效果
            spin_embed = discord.Embed(title="🎡 次元幸運大輪盤 旋轉中...", description="🌀 命運法陣正在高速旋轉變幻... ⌛ `[ 3 ]`", color=discord.Color.gold())
            spin_msg = await interaction.followup.send(embed=spin_embed)
            await asyncio.sleep(0.8)
            spin_embed.description = "🌀 命運法陣正在高速旋轉變幻... ⌛ `[ 2 ]`"
            await spin_msg.edit(embed=spin_embed)
            await asyncio.sleep(0.8)
            spin_embed.description = "🌀 命運法陣正在高速旋轉變幻... ⌛ `[ 1 ]`"
            await spin_msg.edit(embed=spin_embed)
            await asyncio.sleep(0.6)

            # 機率判定
            rand = random.random()
            res_embed = discord.Embed(title="🎡 次元幸運大輪盤 開獎結果")
            res_embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)

            if rand < 0.40:
                # 1. 時空吞噬 (40%)
                res_embed.description = f"💀 **【時空吞噬】(機率 40%)**\n不幸落入黑洞裂縫！下注的 **`{下注mp:,}` MP** 全額被空間風暴沒收！"
                res_embed.color = discord.Color.from_rgb(40, 40, 40)
                
                # 平庸之福連輸補償
                if flow == "平庸之福流":
                    self.loss_streaks[uid] = self.loss_streaks.get(uid, 0) + 1
                    if self.loss_streaks[uid] >= 3:
                        refund = int(下注mp * 0.5)
                        await profile_store.add_mp(uid, refund)
                        res_embed.description += f"\n\n🌱 **【平庸之福】天賦觸發：** 連輸 3 把大輪盤/骰子，守護因果線，緊急補償你 **`{refund:,}` MP**！"
                        self.loss_streaks[uid] = 0
                else:
                    self.loss_streaks[uid] = 0

            elif rand < 0.65:
                # 2. 原物返還 (25%)
                await profile_store.add_mp(uid, 下注mp)
                res_embed.description = f"⚖️ **【原物返還】(機率 25%)**\n法陣軌道完美重合！退回全額本金 **`{下注mp:,}` MP**。"
                res_embed.color = discord.Color.blue()
                self.loss_streaks[uid] = 0

            elif rand < 0.85:
                # 3. 萌力翻湧 1.5倍 (20%)
                win_total = int(下注mp * 1.5)
                tax = self._calc_tax(win_total - 下注mp)
                net_win = win_total - tax
                
                await profile_store.add_mp(uid, net_win)
                await self._handle_casino_tax(tax)
                
                res_embed.description = f"✨ **【萌力翻湧】(機率 20%)**\n法陣金光大作！斬獲 **1.5 倍** 收益！\n獲得共計：**`{net_win:,}` MP** *(扣除稅金 `{tax:,}` MP 已注入發電廠)*。"
                res_embed.color = discord.Color.green()
                self.loss_streaks[uid] = 0

            elif rand < 0.95:
                # 4. 狂暴突破 3倍暴擊 (10%)
                win_total = int(下注mp * 3.0)
                tax = self._calc_tax(win_total - 下注mp)
                net_win = win_total - tax
                
                await profile_store.add_mp(uid, net_win)
                await self._handle_casino_tax(tax)
                
                res_embed.description = f"💥 **【狂暴突破】(機率 10%)**\n核心代碼超載！觸發 **3.0 倍超級暴擊**！\n直接獲得高達：**`{net_win:,}` MP** *(扣除稅金 `{tax:,}` MP 已注入發電廠)*！"
                res_embed.color = discord.Color.orange()
                self.loss_streaks[uid] = 0

            else:
                # 5. 結晶裂縫 (5%) -> 轉換為 1~3 顆結晶
                dc_reward = random.randint(1, 3)
                await profile_store.add_dc(uid, dc_reward)
                res_embed.description = f"🔮 **【結晶裂縫】(極致機率 5%)**\n大輪盤核心破碎，時空裂縫深處掉落高階能源！\n不返還 MP，但你直接挖出了稀有物資： **`{dc_reward}` 顆 【次元結晶 (DC)】**！"
                res_embed.color = discord.Color.gold()
                self.loss_streaks[uid] = 0

            res_embed.set_footer(text=f"✨ 目前特權流派屬性：【{flow}】")
            await spin_msg.edit(embed=res_embed)
        finally:
            # 🔒 無論成功、失敗或提早 return，都務必釋放鎖，避免玩家被永久卡住
            self.active_players.discard(uid)


    # ==========================================
    # 🃏 遊戲三：萌力 21 點 (/blackjack)
    # ==========================================
    @app_commands.command(name="blackjack", description="🃏 時空裂縫賭場：挑戰萌力 21 點！靠智商、策略與運氣擊敗機器人莊家！")
    @app_commands.describe(下注mp="你要下注的 MP 數量")
    async def casino_blackjack(self, interaction: discord.Interaction, 下注mp: int):
        if 下注mp <= 0:
            await interaction.response.send_message("❌ 下注數量必須大於 0！", ephemeral=True)
            return

        uid = interaction.user.id

        if uid in self.active_players:
            await interaction.response.send_message("⏳ 你目前還有一局賭局正在結算中，請稍候再試！", ephemeral=True)
            return
        self.active_players.add(uid)

        p = await profile_store.get_profile(uid)
        flow = self._check_profession_flow(p["profession"])

        if p["deposit"] < 下注mp:
            await interaction.response.send_message(f"❌ 你的萌力值 (MP) 不足！當前餘額：`{p['deposit']:,}` MP", ephemeral=True)
            self.active_players.discard(uid)
            return

        # 扣除本金
        await profile_store.add_mp(uid, -下注mp)

        # 21點撲克牌庫與發牌邏輯
        suits = ['♠', '♥', '♦', '♣']
        ranks = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']
        deck = [{'rank': r, 'suit': s} for r in ranks for s in suits]
        random.shuffle(deck)

        def calc_score(hand):
            score = 0
            aces = 0
            for card in hand:
                if card['rank'] in ['J', 'Q', 'K']:
                    score += 10
                elif card['rank'] == 'A':
                    aces += 1
                    score += 11
                else:
                    score += int(card['rank'])
            while score > 21 and aces > 0:
                score -= 10
                aces -= 1
            return score

        player_hand = [deck.pop(), deck.pop()]
        bot_hand = [deck.pop(), deck.pop()]

        # 內建互動按鈕 View
        class BlackjackView(discord.ui.View):
            def __init__(self, cog_instance, user_id, p_hand, b_hand, current_deck, bet):
                super().__init__(timeout=60)
                self.cog = cog_instance
                self.user_id = user_id
                self.player_hand = p_hand
                self.bot_hand = b_hand
                self.deck = current_deck
                self.bet = bet
                self.flow = flow
                self._settled = False
                self._settle_lock = asyncio.Lock()

            def get_cards_str(self, hand, hide_first=False):
                if hide_first:
                    return f"🃏 `[隱藏卡]` " + " ".join([f"`{c['suit']}{c['rank']}`" for c in hand[1:]])
                return " ".join([f"`{c['suit']}{c['rank']}`" for c in hand])

            async def interaction_check(self, inter: discord.Interaction) -> bool:
                if inter.user.id != self.user_id:
                    await inter.response.send_message("❌ 這是別人的賭局，請自己開一局！", ephemeral=True)
                    return False
                return True

            async def end_game(self, inter, status, msg_text, color):
                async with self._settle_lock:
                    if self._settled:
                        return
                    self._settled = True

                    # 停用按鈕
                    for item in self.children:
                        item.disabled = True

                    # 金流結算
                    if status == "win":
                        tax = self.cog._calc_tax(self.bet)
                        net_win = self.bet * 2 - tax
                        await profile_store.add_mp(self.user_id, net_win)
                        await self.cog._handle_casino_tax(tax)
                        msg_text += f"\n💰 淨獲得 **`{net_win - self.bet:,}` MP** *(已扣除稅金 `{tax:,}` 注入發電廠)*"
                        self.cog.loss_streaks[self.user_id] = 0
                    elif status == "blackjack":
                        tax = self.cog._calc_tax(int(self.bet * 1.5))
                        net_win = int(self.bet * 2.5) - tax
                        await profile_store.add_mp(self.user_id, net_win)
                        await self.cog._handle_casino_tax(tax)
                        msg_text += f"\n👑 淨獲得 **`{net_win - self.bet:,}` MP** *(已扣除稅金 `{tax:,}` 注入發電廠)*"
                        self.cog.loss_streaks[self.user_id] = 0
                    elif status == "push":
                        await profile_store.add_mp(self.user_id, self.bet)
                        self.cog.loss_streaks[self.user_id] = 0
                    else:
                        # 玩家輸了
                        if self.flow == "平庸之福流":
                            self.cog.loss_streaks[self.user_id] = self.cog.loss_streaks.get(self.user_id, 0) + 1
                            if self.cog.loss_streaks[self.user_id] >= 3:
                                refund = int(self.bet * 0.5)
                                await profile_store.add_mp(self.user_id, refund)
                                msg_text += f"\n\n🌱 **【平庸之福】連輸慰問發動：** 返還補償 **`{refund:,}` MP** 救濟金！"
                                self.cog.loss_streaks[self.user_id] = 0
                        else:
                            self.cog.loss_streaks[self.user_id] = 0

                    emb = discord.Embed(title="🃏 萌力 21 點：對局結束 🪐", description=msg_text, color=color)
                    emb.set_author(name=inter.user.display_name, icon_url=inter.user.display_avatar.url)
                    emb.add_field(name="👤 你的手牌", value=f"{self.get_cards_str(self.player_hand)}\n(點數: **{calc_score(self.player_hand)}**)", inline=True)
                    emb.add_field(name="🤖 莊的手牌", value=f"{self.get_cards_str(self.bot_hand)}\n(點數: **{calc_score(self.bot_hand)}**)", inline=True)
                    emb.set_footer(text=f"✨ 目前特權流派屬性：【{self.flow}】")
                    await inter.response.edit_message(embed=emb, view=self)
                    self.cog.active_players.discard(self.user_id)
                    self.stop()

            @discord.ui.button(label="🃏 加牌 (Hit)", style=discord.ButtonStyle.green)
            async def hit_button(self, inter: discord.Interaction, button: discord.ui.Button):
                async with self._settle_lock:
                    if self._settled:
                        return
                    self.player_hand.append(self.deck.pop())
                    p_score = calc_score(self.player_hand)
                    is_bust = p_score > 21
                    if not is_bust:
                        # 更新當前 Embed 狀態
                        emb = discord.Embed(title="🃏 萌力 21 點正在進行中... 🪐", description=f"下注金額：`{self.bet:,}` MP\n請決定是否繼續加牌！", color=discord.Color.gold())
                        emb.set_author(name=inter.user.display_name, icon_url=inter.user.display_avatar.url)
                        emb.add_field(name="👤 你的手牌", value=f"{self.get_cards_str(self.player_hand)}\n(當前點數: **{p_score}**)", inline=True)
                        emb.add_field(name="🤖 莊的手牌", value=f"{self.get_cards_str(self.bot_hand, hide_first=True)}\n(首牌隱藏)", inline=True)
                        emb.set_footer(text=f"✨ 目前特權流派屬性：【{self.flow}】")
                        await inter.response.edit_message(embed=emb, view=self)

                if is_bust:
                    await self.end_game(inter, "lose", "💀 **玩家爆牌 (Bust)！** 點數超過 21 點，莊家直接獲勝！", discord.Color.red())

            @discord.ui.button(label="🛑 停牌 (Stand)", style=discord.ButtonStyle.red)
            async def stand_button(self, inter: discord.Interaction, button: discord.ui.Button):
                p_score = calc_score(self.player_hand)
                b_score = calc_score(self.bot_hand)

                # 莊家 AI：小於 17 點必須強迫持續抽牌
                while b_score < 17:
                    self.bot_hand.append(self.deck.pop())
                    b_score = calc_score(self.bot_hand)

                if b_score > 21:
                    await self.end_game(inter, "win", "🎉 **莊家爆牌 (Bust)！** 莊家點數破產，玩家無條件勝利！", discord.Color.green())
                elif p_score > b_score:
                    await self.end_game(inter, "win", f"🎉 **玩家勝出！** 你的點數大於莊家點數！ (`{p_score}` vs `{b_score}`)", discord.Color.green())
                elif p_score < b_score:
                    await self.end_game(inter, "lose", f"💀 **莊家勝出！** 你的點數小於莊家點數！ (`{p_score}` vs `{b_score}`)", discord.Color.red())
                else:
                    await self.end_game(inter, "push", f"🤝 **因果同頻 (平手)！** 點數完全一樣 (`{p_score}` 點)！本金退回。", discord.Color.blue())

            async def on_timeout(self):
                async with self._settle_lock:
                    if self._settled:
                        return
                    self._settled = True

                    # ⏳ 超時自動視為「停牌」進行結算，避免本金憑空消失且不結算
                    p_score = calc_score(self.player_hand)
                    b_score = calc_score(self.bot_hand)
                    while b_score < 17:
                        self.bot_hand.append(self.deck.pop())
                        b_score = calc_score(self.bot_hand)

                    for item in self.children:
                        item.disabled = True

                    if b_score > 21 or p_score > b_score:
                        status = "win"
                    elif p_score < b_score:
                        status = "lose"
                    else:
                        status = "push"

                    # 金流結算（與按鈕結算共用同一套邏輯）
                    if status == "win":
                        tax = self.cog._calc_tax(self.bet)
                        net_win = self.bet * 2 - tax
                        await profile_store.add_mp(self.user_id, net_win)
                        await self.cog._handle_casino_tax(tax)
                    elif status == "push":
                        await profile_store.add_mp(self.user_id, self.bet)
                    # status == "lose" 不需額外動作，本金已在開局時扣除

                    self.cog.loss_streaks[self.user_id] = 0
                    self.cog.active_players.discard(self.user_id)
                    # 注意：逾時後 interaction 已失效，無法再 edit_message，僅能靜默完成結算

        # 初始局直接檢查有無天生天選天神 Blackjack (21點)
        p_init = calc_score(player_hand)
        if p_init == 21:
            tax = self._calc_tax(int(下注mp * 1.5))
            net_win = int(下注mp * 2.5) - tax
            await profile_store.add_mp(uid, net_win)
            await self._handle_casino_tax(tax)
            
            emb = discord.Embed(title="🃏 萌力 21 點：天選開局！ 👑", description=f"🔥 **【天選・大羅神眼】！** 開局直接拿到 **Blackjack (21點)**！\n獲得 **1.5倍 特殊獲勝獎勵**，斬獲 **`{net_win - 下注mp:,}` MP** *(已扣稅注入發電廠)*！", color=discord.Color.gold())
            emb.add_field(name="👤 你的手牌", value=" ".join([f"`{c['suit']}{c['rank']}`" for c in player_hand]), inline=True)
            emb.add_field(name="🤖 莊的手牌", value=" ".join([f"`{c['suit']}{c['rank']}`" for c in bot_hand]), inline=True)
            await interaction.response.send_message(embed=emb)
            self.active_players.discard(uid)
            return

        # 正常開盤互動
        view = BlackjackView(self, uid, player_hand, bot_hand, deck, 下注mp)
        init_embed = discord.Embed(title="🃏 萌力 21 點：正式開盤 🪐", description=f"成功下注：`{下注mp:,}` MP\n請點擊下方按鈕執行你的時空決策：", color=discord.Color.gold())
        init_embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        init_embed.add_field(name="👤 你的手牌", value=f"{view.get_cards_str(player_hand)}\n(當前點數: **{p_init}**)", inline=True)
        init_embed.add_field(name="🤖 莊的手牌", value=f"{view.get_cards_str(bot_hand, hide_first=True)}\n(首牌已封印)", inline=True)
        init_embed.set_footer(text=f"✨ 目前特權流派屬性：【{flow}】")
        
        await interaction.response.send_message(embed=init_embed, view=view)

async def setup(bot: commands.Bot):
    """加載 Casino Cog 至 Bot"""
    await bot.add_cog(Casino(bot))