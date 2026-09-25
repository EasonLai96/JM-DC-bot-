# -*- coding: utf-8 -*-
import os
import json
import time
import datetime
import discord
from discord.ext import commands
from discord import app_commands
from logger_config import log, discord_relay_handler
from config import current_dir

# 🛠️ 修復：原本是 CONFIG_FILE = 'log_channel.json' 純相對路徑，跟專案裡其他所有
# cog（Autorestart.py、feedback.py、waifu.py、profile_store.py 全都用 current_dir）
# 的寫法不一致。相對路徑是相對於「程式啟動當下的工作目錄」，不是這支檔案所在的
# 資料夾——如果啟動方式（面板 / systemd / docker / 手動執行）的工作目錄跟預期不同，
# 這個設定檔可能寫到意料之外的地方，或下次啟動時讀不到之前存的設定，導致轉發頻道
# 設定莫名其妙消失。改用絕對路徑，行為就跟其他 cog 完全一致、不受啟動方式影響。
CONFIG_FILE = os.path.join(current_dir, 'log_channel.json')

def _validate_config(data) -> dict | None:
    """設定檔必須是 dict，且含有可轉成 int 的 guild_id / channel_id。

    🛠️ 修復（L8）：原本 `_get_relay_channel()` 直接 `cfg['channel_id']`，
    只要 JSON 被手動改壞、少了 key、或值不是數字，`on_interaction` 這個
    「每一道指令都會跑」的 listener 就會直接噴 KeyError —— 而且是在攔截器
    安裝之前，等於整個 cog 的轉發功能從此完全失效，log 裡只看得到例外。
    """
    if not isinstance(data, dict):
        return None
    try:
        return {'guild_id': int(data['guild_id']), 'channel_id': int(data['channel_id'])}
    except (KeyError, TypeError, ValueError):
        return None


# 🛠️ 一次性搬遷：如果舊版（相對路徑）留下的設定檔還在目前工作目錄，
# 且新的絕對路徑位置還沒有檔案，就把舊檔案的內容搬過去，
# 避免升級後既有的轉發頻道設定看起來像「憑空消失」。
# 🛠️ 修復（L6）：原本是「看到檔名就搬」。搬過去之後 `load_config()` 只驗證
# 「檔案存在」，所以一個壞掉的舊檔會讓新位置變成壞檔，而且沒有任何訊息 ——
# 轉發設定就真的憑空消失了。改成先驗證內容合法才搬。
_OLD_RELATIVE_CONFIG_FILE = 'log_channel.json'
if os.path.exists(_OLD_RELATIVE_CONFIG_FILE) and not os.path.exists(CONFIG_FILE):
    try:
        with open(_OLD_RELATIVE_CONFIG_FILE, 'r', encoding='utf-8') as _f:
            _migrated = _validate_config(json.load(_f))
        if _migrated:
            import shutil as _shutil
            _shutil.move(_OLD_RELATIVE_CONFIG_FILE, CONFIG_FILE)
            log.info(f"📡 [Log 轉發] 已將舊版相對路徑的設定檔自動搬遷至新位置：{CONFIG_FILE}")
        else:
            log.warning(
                f"⚠️ [Log 轉發] 舊版設定檔 {_OLD_RELATIVE_CONFIG_FILE} 內容不合法，不予搬遷"
                f"（請重新用 /setlogchannel 設定一次）"
            )
    except Exception as _e:
        log.warning(f"⚠️ [Log 轉發] 搬遷舊版設定檔失敗（不影響後續正常運作，只是需要重新 /setlogchannel 一次）: {_e}")

# ── 顏色常數（用於 relay embed 的左側色條）──────────────────────────
COLOR_CMD    = discord.Color.from_rgb(88, 101, 242)   # 紫藍：指令觸發
COLOR_OK     = discord.Color.from_rgb(87, 242, 135)   # 綠：正常公開回應
COLOR_HIDDEN = discord.Color.from_rgb(254, 231, 92)   # 黃：ephemeral（私人回應）
COLOR_ERR    = discord.Color.from_rgb(237, 66, 69)    # 紅：錯誤 / 異常

# 🛠️ 隱私修復：這些指令的私人（ephemeral）回應內容可能包含其他玩家的 UID、
# 資產異動、頻道設定等敏感資訊，即使 relay 頻道只給你自己看，還是別把明細轉發出去，
# 只轉發「這個指令被觸發了、有沒有出錯」這個層級的資訊就好。
SENSITIVE_COMMANDS = {"debug_give", "settings", "set_feedback_channel", "setlogchannel", "announcement", "translate_test"}


# ── 設定檔存取 ────────────────────────────────────────────────────────
def save_config(guild_id: int, channel_id: int):
    """原子寫入設定檔。

    🛠️ 修復（L7）：原本是 `open(CONFIG_FILE,'w')` 直接覆寫 —— 容器在寫入過程中被
    砍（Pterodactyl 重啟、OOM 都很常見）就會留下半截 JSON，下次啟動
    `load_config()` 回 None，轉發設定無聲無息地消失。改用 tempfile + os.replace
    （與 profile_store._save_raw、fixlink.save_settings 相同做法）。
    """
    tmp_path = CONFIG_FILE + '.tmp'
    try:
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump({'guild_id': int(guild_id), 'channel_id': int(channel_id)}, f)
        os.replace(tmp_path, CONFIG_FILE)
    except OSError as e:
        log.error(f"❌ [Log 轉發] 寫入設定檔失敗（本次設定不會保存）: {e!r}")


def clear_config():
    if os.path.exists(CONFIG_FILE):
        try:
            os.remove(CONFIG_FILE)
        except OSError as e:
            log.warning(f"⚠️ [Log 轉發] 刪除設定檔失敗（下次啟動可能還會還原舊頻道）: {e!r}")


def load_config() -> dict | None:
    if not os.path.exists(CONFIG_FILE):
        return None
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            cfg = _validate_config(json.load(f))
    except (OSError, json.JSONDecodeError) as e:
        log.warning(f"⚠️ [Log 轉發] 讀取設定檔失敗，已忽略（請重新 /setlogchannel 設定）：{e!r}")
        return None
    if cfg is None:
        log.warning(f"⚠️ [Log 轉發] 設定檔內容不合法，已忽略（請重新 /setlogchannel 設定）：{CONFIG_FILE}")
    return cfg


# ── Embed 摘要提取（把 bot 的回應 embed 縮成一行給 relay 看）──────────
def _summarise_embed(embed: discord.Embed) -> str:
    parts = []
    if embed.title:
        parts.append(f"**{discord.utils.escape_markdown(embed.title)}**")
    if embed.description:
        desc = discord.utils.escape_markdown(embed.description[:120])
        if len(embed.description) > 120:
            desc += "…"
        parts.append(desc)
    if embed.fields:
        field_names = " / ".join(discord.utils.escape_markdown(f.name) for f in embed.fields[:4])
        if len(embed.fields) > 4:
            field_names += f" …(+{len(embed.fields)-4})"
        parts.append(f"📋 欄位：{field_names}")
    text = "\n".join(parts) if parts else "*(空 embed)*"
    # 🛠️ 修復（M9）：embed 的 field value 上限是 1024 字，一個 256 字的標題
    # + 4 個 256 字的欄位名就會超過 —— 超過時整則 relay 訊息會被 Discord 以
    # HTTP 400 拒絕，而 `_relay()` 是靜默吞掉的，所以你只會覺得「轉發偶爾漏掉」。
    if len(text) > 1000:
        text = text[:999] + "…"
    return text


class LogRelayCog(commands.Cog):
    # 🆕 轉發用的令牌桶（見 _relay 的說明）
    RELAY_RATE = 5.0     # 每秒補充幾個令牌
    RELAY_BURST = 20.0   # 最多累積幾個

    def __init__(self, bot):
        self.bot = bot
        # 快取轉發頻道，省去每次都 get_channel
        self._relay_channel: discord.TextChannel | None = None
        # 🆕 relay 自己出錯時只在「第一次」與「每 N 次」留訊息，避免失敗迴圈把 log 洗爆
        self._relay_error_count = 0
        self._relay_dropped = 0
        self._relay_tokens = float(self.RELAY_BURST)
        self._relay_last_refill = time.monotonic()
        # 🆕 tree.interaction_check 的包裝（見 _install_tree_hook）
        self._orig_interaction_check = None

    # ── 取得目前的轉發頻道 ─────────────────────────────────────────────
    def _get_relay_channel(self) -> discord.TextChannel | None:
        cfg = load_config()
        if not cfg:
            return None
        return self.bot.get_channel(cfg['channel_id'])

    async def cog_load(self):
        # 🆕 這兩個掛鉤必須在「任何指令被執行之前」裝好，所以放在 cog_load 而不是
        # 第一次 on_interaction（見 _install_tree_hook 的說明）。
        self._install_tree_hook()
        cfg = load_config()
        if not cfg:
            return
        try:
            channel = self.bot.get_channel(cfg['channel_id']) or await self.bot.fetch_channel(cfg['channel_id'])
            self._relay_channel = channel
            discord_relay_handler.set_target(self.bot, channel)
            log.info(f"📡 [Log 轉發] 已自動還原轉發頻道：#{channel.name} ({channel.guild.name})")
        except Exception as e:
            log.warning(f"⚠️ [Log 轉發] 還原轉發頻道失敗（頻道可能已被刪除）: {e}")

    def cog_unload(self):
        tree = self.bot.tree
        # 只有在「最外層還是我們自己」時才還原；如果別人（例如 Autorestart）在我們
        # 之後又包了一層，硬還原會把對方的掛鉤一起拆掉。維持與 Autorestart.py 相同的
        # 守衛寫法，兩邊互相包裝時不會打架。
        if tree.interaction_check == self._tree_interaction_check and self._orig_interaction_check is not None:
            tree.interaction_check = self._orig_interaction_check

    # ── 攔截器安裝時機修復 ────────────────────────────────────────────
    def _install_tree_hook(self):
        """把攔截用的互動標記掛進 `tree.interaction_check`。

        🛠️ 修復（M7，原本的轉發其實只攔得到 followup）：
        Discord 收到 interaction 時，`state.parse_interaction_create()` 會先
        `loop.create_task(wrapper())`（跑指令本身），**之後**才 `dispatch('interaction')`
        把事件排給 cog 的 listener。asyncio 是 FIFO，所以指令的 callback 一定先跑：
        絕大多數指令的第一個 await 就是 `interaction.response.defer()` /
        `send_message(...)`，等到我們的 listener 終於被排到、把
        `interaction.response.__class__` 換掉時，那次回應早就送出去了 ——
        也就是說「② 回應內容」幾乎永遠攔不到，只有之後的 followup 攔得到。

        `interaction_check` 是 CommandTree 在 `_call()` 最開頭 await 的公開擴充點，
        而且**與指令本體在同一個 task**，所以在這裡掛保證「指令還沒開始跑就已經裝好」。
        """
        tree = self.bot.tree
        if getattr(tree.interaction_check, '__self__', None) is self:
            return  # 已經裝過（例如 cog 被 reload）
        self._orig_interaction_check = tree.interaction_check
        tree.interaction_check = self._tree_interaction_check

    async def _tree_interaction_check(self, interaction: discord.Interaction) -> bool:
        try:
            self._patch_interaction(interaction)
        except TypeError as e:
            # __class__ 賦值失敗（discord.py 換版本、slots 佈局改變）
            log.warning(
                f"⚠️ [Log 轉發] 攔截器套用失敗（discord.py 版本可能有變動，本次不會轉發到 log 頻道）: {e}"
            )
        except Exception as e:
            log.warning(f"⚠️ [Log 轉發] 攔截器發生未預期錯誤（已忽略，不影響指令）: {e!r}")
        if self._orig_interaction_check is None:
            return True
        return await self._orig_interaction_check(interaction)

    def _patch_interaction(self, interaction: discord.Interaction) -> None:
        """把這個 interaction 的 response / followup 換成會順便轉發的版本。"""
        cog_self = self

        class _RelayInteractionResponse(discord.InteractionResponse):
            __slots__ = ()

            async def send_message(self, content=None, **kwargs):
                result = await discord.InteractionResponse.send_message(self, content, **kwargs)
                # 🛠️ 修復（H2 附帶）：原本這裡沒有任何 try/except。`_build_response_embed()`
                # 只要噴一次例外（例如某個 embed 的欄位超長），例外就會穿出
                # `send_message()` —— 使用者的訊息已經送出去了，卻同時看到「指令執行
                # 發生錯誤」，而真正的兇手是 log 轉發。轉發永遠不該讓指令失敗。
                try:
                    relay_emb = cog_self._build_response_embed(
                        interaction, content, kwargs.get('embed'), kwargs.get('embeds'),
                        kwargs.get('ephemeral', False),
                    )
                    await cog_self._relay(relay_emb)
                except Exception as e:
                    cog_self._log_relay_error("response.send_message", e)
                return result

        class _RelayWebhookFollowup(discord.Webhook):
            __slots__ = ()

            async def send(self, content=None, **kwargs):
                result = await discord.Webhook.send(self, content, **kwargs)
                try:
                    relay_emb = cog_self._build_response_embed(
                        interaction, content, kwargs.get('embed'), kwargs.get('embeds'),
                        kwargs.get('ephemeral', False), tag="Followup",
                    )
                    await cog_self._relay(relay_emb)
                except Exception as e:
                    cog_self._log_relay_error("followup.send", e)
                return result

        interaction.response.__class__ = _RelayInteractionResponse
        interaction.followup.__class__ = _RelayWebhookFollowup

    def _log_relay_error(self, where: str, error: Exception) -> None:
        self._relay_error_count += 1
        if self._relay_error_count == 1 or self._relay_error_count % 50 == 0:
            log.warning(f"⚠️ [Log 轉發] 產生摘要失敗（{where}，累計 {self._relay_error_count} 次）: {error!r}")

    # ── 核心：把一筆訊息送到 relay 頻道 ──────────────────────────────
    async def _relay(self, embed: discord.Embed):
        ch = self._relay_channel or self._get_relay_channel()
        if ch is None:
            return

        # 🛠️ 修復（M10）：每道指令最多會產生 3 則 relay 訊息（觸發 / 回應 / followup），
        # 再加上 discord_relay_handler 自己的批次訊息，全都算進 discord.py 的全域
        # 50 req/s 額度；一旦撞到全域 429，被延遲的是**所有** cog 的指令。加一個寬鬆的
        # 令牌桶（突發 20、每秒回補 5）擋掉異常爆量，正常使用完全感覺不到。
        now = time.monotonic()
        self._relay_tokens = min(
            float(self.RELAY_BURST),
            self._relay_tokens + (now - self._relay_last_refill) * self.RELAY_RATE,
        )
        self._relay_last_refill = now
        if self._relay_tokens < 1.0:
            self._relay_dropped += 1
            if self._relay_dropped == 1 or self._relay_dropped % 20 == 0:
                log.warning(f"⚠️ [Log 轉發] 短時間內訊息量過大，已丟棄 {self._relay_dropped} 則 relay 訊息")
            return
        self._relay_tokens -= 1.0

        try:
            await ch.send(embed=embed)
        except Exception as e:
            # relay 本身出錯不能影響主要指令 —— 但也不能完全靜默（原本是 pass），
            # 否則頻道被刪、權限被拔你永遠不會知道。用 print 而非 log，避免
            # 「轉發失敗 → 記 log → 又觸發轉發 → 又失敗」的迴圈。
            self._relay_error_count += 1
            if self._relay_error_count == 1 or self._relay_error_count % 50 == 0:
                print(f"[LogRelay] 送出 relay 訊息失敗（累計 {self._relay_error_count} 次）: {e}")

    # ── 把 bot 回應（或 followup）的內容格式化成 relay embed ─────────
    def _build_response_embed(
        self,
        interaction: discord.Interaction,
        content: str | None,
        embed: discord.Embed | None,
        embeds: list[discord.Embed] | None,
        ephemeral: bool,
        tag: str = "回應",
    ) -> discord.Embed:
        color = COLOR_HIDDEN if ephemeral else COLOR_OK

        # 判斷是否為錯誤回應（含 ❌ 或 ephemeral 的短訊息）
        is_error = bool(content and ("❌" in content or "⚠️" in content))
        if is_error:
            color = COLOR_ERR

        cmd_name = interaction.command.qualified_name if interaction.command else "?"

        relay = discord.Embed(
            color=color,
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        relay.set_author(
            name=f"{'🔒 私人' if ephemeral else '📢 公開'} {tag}｜/{cmd_name}",
        )

        # 🛠️ 隱私修復：敏感指令（如 /debug_give）的私人回應不轉發明細，
        # 只有出錯（❌/⚠️）時才照常顯示，因為錯誤訊息本身通常不含其他人的私密資料，
        # 而且錯誤內容正是你要 debug 時最需要看到的東西。
        if ephemeral and cmd_name in SENSITIVE_COMMANDS and not is_error:
            relay.description = "🔒 *(此為敏感指令的私人回應，內容基於隱私保護已隱藏，未出錯)*"
            relay.set_footer(text=f"👤 {interaction.user} ({interaction.user.id})  |  #{getattr(interaction.channel, 'name', '?')}  |  {getattr(interaction.guild, 'name', 'DM')}")
            return relay

        # 文字內容
        if content:
            relay.add_field(name="💬 文字內容", value=content[:512] + ("…" if len(content) > 512 else ""), inline=False)

        # embed 摘要
        all_embeds = []
        if embed:
            all_embeds.append(embed)
        if embeds:
            all_embeds.extend(embeds)
        for i, e in enumerate(all_embeds[:3]):
            relay.add_field(
                name=f"🖼 Embed {'#'+str(i+1) if len(all_embeds)>1 else ''}",
                value=_summarise_embed(e),
                inline=False,
            )
        if len(all_embeds) > 3:
            relay.add_field(name="…", value=f"共 {len(all_embeds)} 個 embed，僅顯示前 3 個", inline=False)

        if not relay.fields:
            relay.description = "*(無文字內容也無 embed)*"

        relay.set_footer(text=f"👤 {interaction.user} ({interaction.user.id})  |  #{getattr(interaction.channel, 'name', '?')}  |  {getattr(interaction.guild, 'name', 'DM')}")
        return relay

    # ── 全域指令攔截器：監聽每一次 slash command 被呼叫 ──────────────
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        # 只處理應用程式指令（slash command）
        if interaction.type != discord.InteractionType.application_command:
            return
        # relay 頻道不存在就不做任何事
        if not (self._relay_channel or self._get_relay_channel()):
            return

        cmd_name = interaction.command.qualified_name if interaction.command else "?"
        user = interaction.user
        guild = getattr(interaction.guild, 'name', 'DM')
        channel_name = getattr(interaction.channel, 'name', '?')

        # ① 先發一張「指令觸發」的 relay embed
        #
        # 🛠️ 修復（H2）：原本不管什麼指令都把所有參數值原文貼出來 —— 而
        # `/debug_give`（金額、目標使用者）、`/announcement`（公告全文，可能還沒發布）
        # 這種指令的參數本身就是敏感資訊。`_build_response_embed()` 已經有
        # SENSITIVE_COMMANDS 的保護，但**觸發**這一張完全沒有，等於隱私修復只做了一半
        # （而且觸發 embed 是無條件送出的，連「不是錯誤」的判斷都被繞過）。
        #
        # 🛠️ 修復（M9）：參數值是使用者輸入，直接串進 embed description 時沒有長度上限
        # （單一 value 可達 6000 字，description 上限 4096）→ 被 Discord 以 HTTP 400
        # 拒絕 → 整張觸發 embed 靜默消失。這裡逐項截斷並做 markdown 轉義。
        is_sensitive_cmd = cmd_name in SENSITIVE_COMMANDS
        if is_sensitive_cmd:
            options_text = "🔒 *(敏感指令，參數內容基於隱私保護不轉發)*"
        else:
            parts = []
            if interaction.data and 'options' in interaction.data:
                for opt in interaction.data.get('options') or []:
                    name = str(opt.get('name', '?'))[:32]
                    value = str(opt.get('value', '…'))[:80]
                    parts.append(f"`{name}`=`{discord.utils.escape_markdown(value)}`")
            options_text = "  ".join(parts)
            if len(options_text) > 900:
                options_text = options_text[:899] + "…"

        trigger_embed = discord.Embed(
            description=options_text or '*(無參數)*',
            color=COLOR_CMD,
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        trigger_embed.set_author(name=f"⚡ 指令觸發：/{cmd_name}")
        trigger_embed.set_footer(
            text=f"👤 {user} ({user.id})  |  #{channel_name}  |  {guild}",
            icon_url=user.display_avatar.url,
        )
        await self._relay(trigger_embed)

        # ② 攔截 interaction.response 的 send_message，以及 followup.send
        # 正常情況下 `_tree_interaction_check()` 已經在指令開始執行前就裝好了（見
        # _install_tree_hook 的說明）；這裡再補一次純粹是防禦性 —— 例如 interaction
        # 沒有走 tree._call（未來的 interaction 型別），patch 仍然是 idempotent 的。
        try:
            self._patch_interaction(interaction)
        except Exception:
            pass

    # ── /setlogchannel 指令 ───────────────────────────────────────────
    @app_commands.command(name='setlogchannel', description='【開發者專用】設定即時 log 轉發到的頻道')
    @app_commands.describe(channel='要接收 log 的頻道（留空則關閉轉發）')
    async def set_log_channel(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        if not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message("❌ 只有機器人開發者（Owner）才能使用此指令！", ephemeral=True)
            return

        if channel is None:
            discord_relay_handler.clear_target()
            self._relay_channel = None
            clear_config()
            await interaction.response.send_message("🔌 已關閉 log 轉發功能。", ephemeral=True)
            log.info(f"📡 [Log 轉發] 已由 {interaction.user.name} 關閉轉發功能")
            return

        try:
            perms = channel.permissions_for(channel.guild.me)
        except Exception as e:
            await interaction.response.send_message(
                f"❌ 無法確認我在 {channel.mention} 的權限（請確認這是伺服器頻道）：{e}", ephemeral=True
            )
            return
        # 🛠️ 修復（M8）：log 轉發用的是 embed，只檢查 send_messages 是不夠的 ——
        # 少了「嵌入連結」權限時每一次 send 都會被 403 拒絕，而 `_relay()` 是靜默吞掉的
        # （現在會 print，但你不會立刻聯想到是權限問題）。設定時就先擋下來。
        if not perms.send_messages:
            await interaction.response.send_message(f"❌ 我在 {channel.mention} 沒有發送訊息的權限！", ephemeral=True)
            return
        if not perms.embed_links:
            await interaction.response.send_message(
                f"❌ 我在 {channel.mention} 沒有「嵌入連結（Embed Links）」權限，"
                f"log 轉發是用 embed 送的，沒有這個權限會全部失敗。請先開啟權限再設定。",
                ephemeral=True,
            )
            return
        if not perms.view_channel:
            await interaction.response.send_message(
                f"❌ 我在 {channel.mention} 沒有「檢視頻道」權限，請先開啟權限再設定。", ephemeral=True
            )
            return

        discord_relay_handler.set_target(self.bot, channel)
        self._relay_channel = channel
        save_config(channel.guild.id, channel.id)

        await interaction.response.send_message(
            f"✅ 已設定 log 即時轉發到 {channel.mention}！\n"
            f"⚠️ 注意：這會把**所有**終端機 log（含下載進度、RAM 狀態等高頻訊息）都同步過來，訊息量會很大。",
            ephemeral=True,
        )
        log.info(f"📡 [Log 轉發] 已由 {interaction.user.name} 設定轉發頻道：#{channel.name} ({channel.guild.name})")


async def setup(bot):
    await bot.add_cog(LogRelayCog(bot))