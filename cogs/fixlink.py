# -*- coding: utf-8 -*-
import asyncio
import json
import os
import re
import time
from urllib.parse import urlparse

import discord
from discord import app_commands
from discord.ext import commands
from logger_config import log

SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "fixlink_settings.json")

# 送出修復訊息後，等待這麼多秒讓 Discord 的連結爬蟲把 embed 抓回來
EMBED_CHECK_DELAY = 30

# 預設翻譯目標語言（FixTweetBot 是跟隨互動者的 locale，這裡固定為 zh-TW 繁體中文）
DEFAULT_LANG = "zh-tw"

# 每個平台的顯示名稱 + 對應的 (regex, fixer_domain) 規則
#
# 🆕 translatable：該平台的 fixer 是否支援「在網址尾端接語言碼」來翻譯貼文。
#    這是翻譯功能的核心開關 —— **實測只有 fxtwitter 與 fxbsky 支援**，其餘 fixer
#    接了語言碼會直接壞掉（實測：vxreddit 的 /zh-TW 回 "Invalid post path"、
#    phixiv 回 500、tnktok 回 404）。所以只有這兩個平台會被加上語言碼。
#
# 🆕 translation_fixer：翻譯開啟時要改用的 fixer 網域。目前只有 Bluesky 需要 ——
#    原本用的 bskyx.app（第三方 VixBluesky）不支援語言碼，必須換成 FxEmbed 官方的
#    fxbsky.app 才有翻譯。翻譯關閉時仍走原本的 bskyx.app，維持 121 台伺服器
#    現有的預覽外觀不變。
PLATFORMS = {
    "twitter": {
        "label": "Twitter / X",
        "rules": [
            (r'https?://(?:www\.)?(?:twitter\.com|x\.com)(/.+)', 'https://fxtwitter.com'),
        ],
        "translatable": True,
    },
    "tiktok": {
        "label": "TikTok",
        "rules": [
            (r'https?://(?:www\.)?tiktok\.com(/.+)', 'https://tnktok.com'),
            (r'https?://vm\.tiktok\.com(/.+)', 'https://vm.tnktok.com'),
        ],
    },
    "reddit": {
        "label": "Reddit",
        "rules": [
            (r'https?://(?:www\.)?reddit\.com(/.+)', 'https://vxreddit.com'),
            (r'https?://(?:www\.)?redd\.it(/.+)', 'https://vxreddit.com'),
        ],
    },
    "bilibili": {
        "label": "Bilibili",
        "rules": [
            (r'https?://(?:www\.)?bilibili\.com(/.+)', 'https://vxbilibili.com'),
            (r'https?://b23\.tv(/.+)', 'https://vxb23.tv'),
        ],
    },
    "pixiv": {
        "label": "Pixiv",
        "rules": [
            (r'https?://(?:www\.)?pixiv\.net(/.+)', 'https://phixiv.net'),
        ],
    },
    "bluesky": {
        "label": "Bluesky",
        "rules": [
            (r'https?://bsky\.app(/.+)', 'https://bskyx.app'),
        ],
        "translatable": True,
        "translation_fixer": 'https://fxbsky.app',
    },
    "threads": {
        "label": "Threads",
        "rules": [
            (r'https?://(?:www\.)?(?:threads\.net|threads\.com)(/.+)', 'https://fzthreads.com'),
        ],
    },
}

DEFAULT_ENABLED = {key: True for key in PLATFORMS}

# 支援翻譯的平台（給設定面板用）
TRANSLATABLE_PLATFORMS = [key for key, cfg in PLATFORMS.items() if cfg.get("translatable")]


# ──────────────────────────────────────────────────────────────
# 語言碼處理（與 FxEmbed 的 normalizeLanguage 對齊）
# ──────────────────────────────────────────────────────────────

# 領頭的 ISO 語言 token：`en`、`zh-tw`、`zh-hant`
_LANG_TOKEN_RE = re.compile(r'^[a-z]{2,3}(?:-[a-z]{2,4})?')

# 使用者輸入的別名 → FxEmbed 認得的代碼
_LANG_ALIASES = {
    "zh": "zh-cn",
    "cn": "zh-cn",
    "zh-hans": "zh-cn",
    "tw": "zh-tw",
    "hk": "zh-tw",
    "zh-hk": "zh-tw",
    "zh-mo": "zh-tw",
    "zh-hant": "zh-tw",
    "jp": "ja",
    "kr": "ko",
    "ua": "uk",
}


def normalize_lang(value) -> str:
    """把使用者輸入的語言碼正規化成 fixer 認得的形式。

    與 FxEmbed 的 normalizeLanguage 行為一致（含 Discord 暴雷標記 `||` 會黏在網址
    尾巴的情形），並額外對映 tw/hk → zh-tw、jp → ja 這類常見寫法。無法解析時
    回傳空字串，交由呼叫端決定 fallback。
    """
    if not isinstance(value, str):
        return ""
    text = value.strip().lower().replace('|', '')
    token = _LANG_TOKEN_RE.match(text)
    if not token:
        return ""
    code = token.group(0)
    return _LANG_ALIASES.get(code, code)


def is_valid_lang(value) -> bool:
    """使用者輸入的語言碼是否可用（2 字母 ISO 639-1，可帶地區碼）。"""
    return bool(normalize_lang(value))


def _append_lang(url: str, lang: str) -> str:
    """把語言碼接到網址路徑尾端，且**必須在 query / fragment 之前**。

    FxEmbed 的路由是 `/:handle/status/:id/:language`，語言碼是路徑的最後一段；
    若直接接在 `?s=20` 之後（`.../status/1?s=20/zh-tw`）會被當成 query 值，
    翻譯不會生效。所以要先切開 query / fragment 再插。
    """
    for sep in ('?', '#'):
        idx = url.find(sep)
        if idx != -1:
            return url[:idx].rstrip('/') + '/' + lang + url[idx:]
    return url.rstrip('/') + '/' + lang


def load_settings() -> dict:
    if not os.path.exists(SETTINGS_PATH):
        return {}
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as e:
        # 🛠️ 修復：原本壞檔只回 {} —— 那等於「121 台伺服器的設定被靜默重設為全開」，
        # 而且 bot.log 不會留下任何一行，維運端完全不知道發生過什麼事。
        log.error(f"❌ [修復連結] 設定檔損毀，將以預設值（全開）繼續運作：{SETTINGS_PATH} | {e!r}")
        return {}


def save_settings(data: dict) -> None:
    """原子寫入設定檔。

    🛠️ 修復：原本是 `open(...,'w')` 直接覆寫 —— 容器在寫入過程中被砍（Pterodactyl 很常
    發生）就會留下半截 JSON，下次啟動 `load_settings()` 回 {} → 121 台伺服器的平台設定
    被靜默重設為全開。改用 tempfile + os.replace（與 profile_store._save_raw 相同做法）。
    """
    tmp_path = SETTINGS_PATH + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, SETTINGS_PATH)
    except OSError as e:
        log.error(f"❌ [修復連結] 寫入設定檔失敗（本次變更不會保存）：{e!r}")


def get_guild_settings(all_settings: dict, guild_id: int) -> dict:
    """回傳該伺服器的平台開關設定，沒設定過的平台預設為開啟"""
    guild_conf = all_settings.get(str(guild_id), {})
    return {key: guild_conf.get(key, True) for key in PLATFORMS}


def get_translation_settings(all_settings: dict, guild_id: int) -> tuple:
    """回傳 (各平台翻譯開關, 目標語言)。

    🆕 預設全部開啟（使用者指定）：121 台伺服器升級後立刻就有翻譯，不想用的伺服器
    自己打 `/settings` 關掉。已存在 fixlink_settings.json 的舊設定不受影響 ——
    翻譯是**新增的 key**（`twitter_tr` / `bluesky_tr` / `lang`），舊檔裡沒有這些 key
    就套用預設值。
    """
    guild_conf = all_settings.get(str(guild_id), {})
    translation = {key: bool(guild_conf.get(f"{key}_tr", True)) for key in TRANSLATABLE_PLATFORMS}
    lang = normalize_lang(guild_conf.get("lang")) or DEFAULT_LANG
    return translation, lang


def label_from_url(url: str) -> str:
    host = urlparse(url).netloc.split(':')[0]
    parts = host.split('.')
    if parts and parts[0] == 'www':
        parts = parts[1:]
    name = parts[0] if parts else host
    return name.capitalize()


# 緊貼在網址尾巴、但不屬於網址的字元（中英標點都要處理）
_TRAILING_JUNK = "。，、；：！？）」』】》〉….,;:!?)]}>'\""

# 網址讀到這裡就該停：空白、`<>"`、以及中日韓文字與全形標點。
# 🛠️ 修復：原本的 `[^\s<>"]+` 只排除空白與 `<>"`，所以「看這個 https://x.com/a/b，超好笑」
# 會把「，超好笑」整串一起吃進網址（中文句子不會在標點後加空格！）→ 改寫成死連結
# → 30 秒後偵測不到 embed → bot 自己把訊息撤回，使用者兩邊都看不到東西。
# 這些社群平台的網址本體只會用到 ASCII，所以直接排除 CJK 區段最乾淨。
_URL_CHARS = r'^\s<>"\u3000-\u303f\uff00-\uffef\u4e00-\u9fff\u3400-\u4dbf'
_URL_RE = re.compile(r'https?://[^' + _URL_CHARS + r']+')


def _clean_url(url: str) -> str:
    """去掉 regex 多抓到的尾端標點（英國標點的情形，例如 `https://x.com/a/b).`）。"""
    return url.rstrip(_TRAILING_JUNK)


# markdown 連結目標 `[標籤](目標)` 裡不能出現的字元（會提前關掉連結、讓整段變成純文字）
_MD_TARGET_ESCAPES = {'(': '%28', ')': '%29', ' ': '%20', '<': '%3C', '>': '%3E'}


def _safe_link_target(url: str) -> str:
    """把會破壞 markdown 連結語法的字元做百分比編碼。

    🛠️ 修復：使用者可控的網址會被放進 `[標籤](網址)` 的目標位置。若網址含 `)`，
    例如 `https://x.com/a)b`，Discord 會在第一個 `)` 就把連結收掉，畫面上
    剩下的 `b)` 變成裸露文字 —— 標籤看起來像壞掉。百分比編碼後目標不變（伺服器
    端會解碼回同一個路徑）但語法不會被破壞。
    """
    return ''.join(_MD_TARGET_ESCAPES.get(ch, ch) for ch in url)


def find_and_fix_links(content: str, enabled: dict, translation: dict = None, lang: str = DEFAULT_LANG):
    """找出可修復的連結並產生修復後的網址。

    :param content: 訊息內容
    :param enabled: 各平台是否開啟修復（`get_guild_settings` 的結果）
    :param translation: 🆕 各平台是否開啟翻譯（`get_translation_settings` 的第一項）。
                        預設 None＝全部不翻譯，行為與加入本功能前完全相同。
    :param lang: 🆕 翻譯目標語言（已正規化，例如 `zh-tw`）
    :return: [(原始網址, 修復後網址, 平台 key, 是否翻譯), ...]
    """
    translation = translation or {}
    lang = normalize_lang(lang) or DEFAULT_LANG
    results = []
    urls = _URL_RE.findall(content)
    for raw_url in urls:
        url = _clean_url(raw_url)
        if not url:
            continue
        for key, platform in PLATFORMS.items():
            if not enabled.get(key, True):
                continue
            for pattern, fixer_domain in platform["rules"]:
                match = re.match(pattern, url)
                if match:
                    # 🆕 翻譯：只有 fixer 真的支援語言碼的平台才加（見 PLATFORMS 的說明）
                    translate_this = bool(
                        platform.get("translatable") and translation.get(key, False)
                    )
                    if translate_this:
                        # 翻譯開啟時改用支援翻譯的 fixer（目前只有 Bluesky 需要換）
                        fixer_domain = platform.get("translation_fixer", fixer_domain)
                    fixed = (fixer_domain + match.group(1)).rstrip('&?')
                    if translate_this:
                        fixed = _append_lang(fixed, lang)
                    results.append((url, fixed, key, translate_this))
                    break
            else:
                continue
            break
    return results


class PlatformSelect(discord.ui.Select):
    def __init__(self, guild_id: int, all_settings: dict):
        self.guild_id = guild_id
        self.all_settings = all_settings
        enabled = get_guild_settings(all_settings, guild_id)

        options = [
            discord.SelectOption(
                label=platform["label"],
                value=key,
                default=enabled[key],
            )
            for key, platform in PLATFORMS.items()
        ]

        super().__init__(
            placeholder="選擇要開啟修復的平台",
            min_values=0,
            max_values=len(options),
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        selected = set(self.values)
        guild_conf = self.all_settings.setdefault(str(self.guild_id), {})
        for key in PLATFORMS:
            guild_conf[key] = key in selected
        save_settings(self.all_settings)

        # 重新整理選項的預設勾選狀態，讓面板顯示最新結果
        for option in self.options:
            option.default = option.value in selected

        enabled_labels = [PLATFORMS[k]["label"] for k in PLATFORMS if k in selected]
        desc = "、".join(enabled_labels) if enabled_labels else "（全部關閉）"
        translation, lang = get_translation_settings(self.all_settings, self.guild_id)
        tr_labels = [PLATFORMS[k]["label"] for k in TRANSLATABLE_PLATFORMS if translation[k]]

        await interaction.response.edit_message(
            content=(
                f"✅ 已更新本伺服器的修復連結設定！\n目前開啟：{desc}\n"
                f"翻譯：{'、'.join(tr_labels) + f'（→ {lang}）' if tr_labels else '（全部關閉）'}"
            ),
            view=self.view,
        )


class TranslationSelect(discord.ui.Select):
    """🆕 選擇要開啟翻譯的平台（只列出 fixer 真的支援翻譯的平台）。"""

    def __init__(self, guild_id: int, all_settings: dict):
        self.guild_id = guild_id
        self.all_settings = all_settings
        translation, _lang = get_translation_settings(all_settings, guild_id)

        options = [
            discord.SelectOption(
                label=PLATFORMS[key]["label"],
                value=key,
                default=translation[key],
                description="翻譯成繁體中文（zh-tw）",
            )
            for key in TRANSLATABLE_PLATFORMS
        ]

        super().__init__(
            placeholder="選擇要開啟翻譯的平台",
            min_values=0,
            max_values=len(options),
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        selected = set(self.values)
        guild_conf = self.all_settings.setdefault(str(self.guild_id), {})
        for key in TRANSLATABLE_PLATFORMS:
            guild_conf[f"{key}_tr"] = key in selected
        save_settings(self.all_settings)

        for option in self.options:
            option.default = option.value in selected

        _translation, lang = get_translation_settings(self.all_settings, self.guild_id)
        labels = [PLATFORMS[k]["label"] for k in TRANSLATABLE_PLATFORMS if k in selected]
        desc = "、".join(labels) if labels else "（全部關閉）"

        await interaction.response.edit_message(
            content=f"✅ 已更新翻譯設定！\n翻譯平台：{desc}\n目標語言：{lang}",
            view=self.view,
        )


class LangModal(discord.ui.Modal):
    """🆕 編輯翻譯目標語言。

    與 FixTweetBot 的 TranslationModal 相同設計：輸入 2 字母 ISO 639-1 語言代碼。
    本專案額外接受 `zh-TW` 這種帶地區碼的寫法（FxEmbed 支援）。
    """

    def __init__(self, guild_id: int, all_settings: dict):
        super().__init__(title="編輯翻譯語言", timeout=180)
        self.guild_id = guild_id
        self.all_settings = all_settings
        _translation, lang = get_translation_settings(all_settings, guild_id)
        self.add_item(discord.ui.TextInput(
            label="翻譯語言",
            placeholder="輸入 2 字母 ISO 語言代碼（例：zh-TW、ja、en）",
            custom_id="lang",
            default=lang,
            max_length=16,
        ))

    async def on_submit(self, interaction: discord.Interaction):
        raw = str(self.children[0]).strip()
        code = normalize_lang(raw)
        if not code:
            await interaction.response.send_message(
                f"❌ `{discord.utils.escape_markdown(raw)}` 不是有效的語言代碼。\n"
                "請輸入 2 字母 ISO 639-1 代碼（例：`zh-TW`、`zh-CN`、`ja`、`en`、`ko`）。\n"
                "語言代碼列表：<https://en.wikipedia.org/wiki/List_of_ISO_639_language_codes#Table>",
                ephemeral=True,
                delete_after=15,
            )
            return

        guild_conf = self.all_settings.setdefault(str(self.guild_id), {})
        guild_conf["lang"] = code
        save_settings(self.all_settings)

        await interaction.response.send_message(
            f"✅ 翻譯目標語言已設為 `{code}`。\n"
            "（下一次張貼連結時生效；已貼出的舊訊息不會變更）",
            ephemeral=True,
            delete_after=15,
        )


class LangButton(discord.ui.Button):
    def __init__(self, guild_id: int, all_settings: dict):
        translation, lang = get_translation_settings(all_settings, guild_id)
        enabled_any = any(translation.values())
        super().__init__(
            label=f"編輯翻譯語言（目前 {lang}）",
            style=discord.ButtonStyle.secondary,
            disabled=not enabled_any,
        )
        self.guild_id = guild_id
        self.all_settings = all_settings

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(LangModal(self.guild_id, self.all_settings))


class SettingsView(discord.ui.View):
    def __init__(self, guild_id: int, all_settings: dict):
        super().__init__(timeout=180)
        self.add_item(PlatformSelect(guild_id, all_settings))
        # 🆕 翻譯相關控制項（只有 fixer 支援翻譯的平台會出現）
        if TRANSLATABLE_PLATFORMS:
            self.add_item(TranslationSelect(guild_id, all_settings))
            self.add_item(LangButton(guild_id, all_settings))


class FixLinkCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.settings = load_settings()
        # 🆕 節流與背景任務管理（原本完全沒有，見 on_message 的說明）
        self._last_channel_handled: dict = {}
        self._verify_tasks: set = set()
        self._channel_cooldown = 5.0     # 同一頻道幾秒內只處理一次

    def _allow_channel(self, channel_id: int) -> bool:
        """同一頻道在 _channel_cooldown 秒內只處理一次。

        🛠️ 修復（DoS）：原本每則含連結的訊息都會做 1 次 edit + 1 次 reply，30 秒後再
        1 次 fetch（+delete+edit），完全沒有節流。121 個伺服器規模下，一波洗頻
        （20 msg/s）就是 100+ req/s，超過 bot 的全域 50 req/s；discord.py 遇到全域 429
        會用 _global_over 擋住**所有**請求，其他 cog 的指令會一起被延遲。
        """
        now = time.monotonic()
        last = self._last_channel_handled.get(channel_id, 0.0)
        if now - last < self._channel_cooldown:
            return False
        self._last_channel_handled[channel_id] = now
        # 防呆：這個 dict 不該無上限成長
        if len(self._last_channel_handled) > 5000:
            cutoff = now - self._channel_cooldown
            for cid in [c for c, t in self._last_channel_handled.items() if t < cutoff]:
                self._last_channel_handled.pop(cid, None)
        return True

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        if not message.content:
            return

        # 🛠️ 修復：原本是子字串比對 `'fxignore' in content.lower()`，任何人只要在訊息
        # 任何位置打出這串字（包含引用別人的字、或單字裡意外包含）就能讓訊息不被修復。
        # 改成獨立的詞判定（前後不能緊接英數字）。
        if re.search(r'(?<![A-Za-z0-9_])fxignore(?![A-Za-z0-9_])', message.content, re.I):
            return

        if message.guild:
            enabled = get_guild_settings(self.settings, message.guild.id)
            # 🆕 翻譯設定（預設全開，見 get_translation_settings 的說明）
            translation, lang = get_translation_settings(self.settings, message.guild.id)
        else:
            enabled = DEFAULT_ENABLED
            translation, lang = {}, DEFAULT_LANG

        fixes = find_and_fix_links(message.content, enabled, translation, lang)
        if not fixes:
            return

        # 🆕 節流（見 _allow_channel 的說明）
        if not self._allow_channel(message.channel.id):
            return

        # 🛠️ 修復（markdown 注入）：URL 是使用者可控的字串，直接串進訊息可以注入
        # **粗體**、||暴雷||、``` 等 markdown（例如網址結尾是 ``` 會把整則回覆變成
        # code block → embed 生不出來 → 30 秒後 bot 又自己把訊息刪掉）。
        lines = [
            f"[{discord.utils.escape_markdown(label_from_url(original))}](<{original}>) • "
            f"[{discord.utils.escape_markdown(label_from_url(fixed))}]({_safe_link_target(fixed)})"
            for original, fixed, _key, _tr in fixes
        ]
        reply = '\n'.join(lines)

        # 🛠️ 修復（H1 的第二條觸發路徑）：超過 2000 字會讓 reply 被 HTTP 400 拒絕。
        # 實測 679 字含 40 個短連結就會組出 2559 字（換成真實推文網址約 15 個連結就爆）。
        if len(reply) > 1900:
            reply = reply[:1899] + "…"

        suppressed = False
        try:
            if message.channel.permissions_for(message.guild.me).manage_messages:
                await message.edit(suppress=True)
                suppressed = True
        except (AttributeError, discord.Forbidden, discord.HTTPException):
            pass  # 沒有 Manage Messages 權限（或非伺服器訊息）→ 不壓抑預覽，但仍可貼修復連結

        # 🛠️ 修復（H1，已在正式環境炸過 3 次）：這是全檔唯一沒有 try/except 的 Discord API
        # 呼叫，而 suppress 已經先執行了、清理任務還沒建立 —— 一旦它拋 Forbidden（bot.log
        # 的 403 / 50013，堆疊都停在原本的下一行），使用者的訊息就會**永久**停在「embed
        # 被隱藏且不會還原」的狀態。這裡失敗時立刻把 suppress 還原。
        try:
            reply_msg = await message.reply(
                reply,
                mention_author=False,
                # 🛡️ 不解析任何提及：回覆內容是由使用者可控的 URL 組出來的
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException) as e:
            log.warning(
                f"⚠️ [修復連結] 無法在 #{getattr(message.channel, 'name', '?')} 回覆修復訊息"
                f"（已自動撤回壓抑）: {e!r}"
            )
            if suppressed:
                try:
                    await message.edit(suppress=False)
                except (discord.Forbidden, discord.HTTPException):
                    pass
            return

        for original, fixed, _key, translated in fixes:
            log.info(
                f"🔗 [修復連結] 伺服器: [{message.guild.name if message.guild else '私訊'}] "
                f"| 使用者: {message.author.name}({message.author.id}) "
                f"| 原始: {original} → 修復: {fixed}"
                + (f" | 🌐 翻譯: {lang}" if translated else "")
            )

        # 🆕 集中保存背景任務的參照（原本是射後不理，無法取消也無法觀察）
        task = asyncio.create_task(self._verify_embed_or_cleanup(message, reply_msg))
        self._verify_tasks.add(task)
        task.add_done_callback(self._verify_tasks.discard)

    async def _verify_embed_or_cleanup(self, original_message: discord.Message, reply_message: discord.Message):
        """等一下，確認修復訊息真的有生出 embed；沒有的話代表修復服務掛了，
        就把自己發的訊息刪掉，並把原始訊息的預覽還原回來，避免使用者兩邊都看不到東西。"""
        await asyncio.sleep(EMBED_CHECK_DELAY)

        try:
            fresh_reply = await reply_message.channel.fetch_message(reply_message.id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return

        if fresh_reply.embeds:
            return  # 有預覽，修復成功，不用管它

        # 沒有任何 embed 冒出來，判定修復失敗
        try:
            await fresh_reply.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

        try:
            await original_message.edit(suppress=False)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

        log.info(
            f"🔗 [修復連結] 偵測不到 embed，判定修復失敗並自動撤回 | 訊息: {original_message.jump_url}"
        )

    @app_commands.command(name="settings", description="設定要修復的連結平台")
    # 讓一般使用者看不到這個指令（122 個伺服器的 / 列表都能少一項）。
    # ⚠️ 這只是「預設隱藏」的提示，伺服器管理員仍可自行開放；真正擋住執行的
    #    是下面那個 has_permissions 檢查，兩者都要留（縱深防禦）。
    # ⚠️ 必須用裝飾器形式 —— app_commands.command() 不接受 default_permissions 參數
    #    （實測會噴 TypeError: command() got an unexpected keyword argument）。
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def settings_command(self, interaction: discord.Interaction):
        enabled = get_guild_settings(self.settings, interaction.guild_id)
        enabled_labels = [platform["label"] for key, platform in PLATFORMS.items() if enabled[key]]
        desc = "、".join(enabled_labels) if enabled_labels else "（全部關閉）"

        translation, lang = get_translation_settings(self.settings, interaction.guild_id)
        tr_labels = [PLATFORMS[k]["label"] for k in TRANSLATABLE_PLATFORMS if translation[k]]
        tr_desc = f"{'、'.join(tr_labels)}（→ {lang}）" if tr_labels else "（全部關閉）"

        view = SettingsView(interaction.guild_id, self.settings)
        await interaction.response.send_message(
            content=(
                f"目前開啟修復：{desc}\n"
                f"目前開啟翻譯：{tr_desc}\n\n"
                "・**修復平台**：選擇哪些平台的連結要改寫成可正常顯示預覽的版本\n"
                "・**翻譯平台**：選擇哪些平台的貼文要自動翻譯（只支援 Twitter/X 與 Bluesky）\n"
                "・**編輯翻譯語言**：修改翻譯的目標語言（預設 zh-TW 繁體中文）"
            ),
            view=view,
            ephemeral=True,
        )

    @settings_command.error
    async def settings_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "❌ 你需要「管理伺服器」權限才能使用這個指令。", ephemeral=True
            )
        else:
            log.error(f"/settings 指令發生錯誤: {error}")
            if interaction.response.is_done():
                await interaction.followup.send("❌ 發生未預期的錯誤。", ephemeral=True)
            else:
                await interaction.response.send_message("❌ 發生未預期的錯誤。", ephemeral=True)


async def setup(bot):
    await bot.add_cog(FixLinkCog(bot))
