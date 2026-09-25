# -*- coding: utf-8 -*-
"""🔬 翻譯功能診斷指令（Owner 專用）

這支 cog 只提供一個指令：

    /translate_test text:<要翻譯的字串> [show_tags:<是否列出字典涵蓋率>]

用途是在**真正的部署環境**（機器人自己所在的主機／IP）驗證翻譯是否可用，
因為免費機翻端點會針對 IP 限流 —— 實測同一組端點在不同機器上結果完全不同
（本機 `google_gtx` 一律 429，主機上卻 16/16 成功）。

所以「在 Discord 裡打一次指令」得到的結果，比在任何其他機器上跑腳本都準確，
因為走的網路路徑與正式翻譯完全相同。

🔒 安全性：僅限機器人 Owner（`bot.is_owner`），並且已登記進
`SENSITIVE_COMMANDS`（main.py 與 cogs/logrelay.py）與 `ADMIN_ONLY_COMMANDS`
（help_command.py），確保一般使用者在 /help 看不到、也不會被 relay 記錄內容。
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

try:
    from logger_config import log
except ImportError:  # pragma: no cover
    import logging

    log = logging.getLogger(__name__)

try:
    from cogs import translate as _TR
except Exception:
    try:
        import translate as _TR  # type: ignore[no-redef]
    except Exception as _error:  # pragma: no cover
        _TR = None
        log.warning(f"⚠️ [翻譯] 診斷指令無法載入翻譯模組: {_error!r}")


class TranslateAdminCog(commands.Cog):
    """提供 /translate_test 讓 Owner 在正式環境驗證翻譯。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="translate_test",
        description="🔬 [開發者專用] 測試翻譯功能（端點、延遲、字典涵蓋率、快取狀態）",
    )
    @app_commands.describe(
        text="要翻譯的字串（英／日文書名或標籤，例如 nakadashi 或 如月ちゃんの受難）",
        show_tags="是否列出前 20 個標籤的字典對照（預設否）",
    )
    async def translate_test(
        self,
        interaction: discord.Interaction,
        text: str,
        show_tags: bool = False,
    ):
        # 與 /debug_give 相同的權限模型：只有 Token 綁定的 Owner 本人可用。
        # is_owner() 是非同步方法，無法用裝飾器表達，所以寫在函式內。
        if not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message(
                "❌ 此指令僅限機器人開發者使用。", ephemeral=True
            )
            return

        if _TR is None:
            await interaction.response.send_message(
                "❌ 翻譯模組（cogs/translate.py）載入失敗，請查看 bot.log。", ephemeral=True
            )
            return

        sample = (text or "").strip()
        if not sample:
            await interaction.response.send_message("❌ 請輸入要測試的字串。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            info = await _TR.diagnostics(sample)
        except Exception as error:
            log.exception("❌ [translate_test] 診斷失敗")
            await interaction.followup.send(f"❌ 診斷失敗：{error}", ephemeral=True)
            return

        # ── 標題翻譯（走機翻）──
        try:
            title_result = await _TR.translate_title(sample)
        except Exception:
            title_result = None

        status_icon = "🟢" if info["endpoint"] not in ("", "(全部端點失敗)") else "🔴"
        embed = discord.Embed(
            title="🔬 翻譯功能診斷",
            description=(
                f"**開關狀態**：{'✅ 已啟用' if info['enabled'] else '⛔ 已停用（TRANSLATE_ENABLED=0）'}\n"
                f"**偵測語言**：`{info['source_lang']}`\n"
                f"**逾時設定**：`{info['timeout_seconds']}s`"
            ),
            color=discord.Color.green() if info["enabled"] else discord.Color.dark_grey(),
        )

        embed.add_field(
            name="📡 端點測試",
            value=(
                f"{status_icon} 使用端點：`{info['endpoint']}`\n"
                f"⏱️ 延遲：`{info['latency_ms']} ms`\n"
                f"📥 原文：`{sample[:200]}`\n"
                f"📤 譯文：`{str(info['translated'])[:200]}`"
            ),
            inline=False,
        )

        embed.add_field(
            name="🌐 標題翻譯結果（含括號保護）",
            value=f"`{title_result[:400]}`" if title_result else "（無譯文，顯示原文即可）",
            inline=False,
        )

        embed.add_field(
            name="📚 標籤字典",
            value=(
                f"總收錄：`{info['glossary_size']}` 筆"
                f"（標籤 {info['tag_glossary_size']}＋分類／語言／角色／系列）\n"
                f"查表結果：`{_TR.tag_zh(sample) or '（此字串不在字典中）'}`"
            ),
            inline=False,
        )

        embed.add_field(
            name="💾 標題快取",
            value=(
                f"筆數：`{info['cache']['entries']}`　"
                f"檔案存在：`{'是' if info['cache']['path_ok'] else '否'}`"
            ),
            inline=False,
        )

        if show_tags:
            sample_tags = list(_TR.NH_TAG_ZH.items())[:20]
            lines = [f"`{en}` → {zh}" for en, zh in sample_tags]
            embed.add_field(
                name="🏷️ 字典樣本（前 20 筆）",
                value="\n".join(lines)[:1024],
                inline=False,
            )

        embed.set_footer(text="此指令僅 Owner 可見；結果來自機器人所在主機的實際網路")
        await interaction.followup.send(embed=embed, ephemeral=True)
        log.info(
            f"🔬 [translate_test] user={interaction.user.id} "
            f"端點={info['endpoint']} 延遲={info['latency_ms']}ms 字串={sample[:60]!r}"
        )

    async def cog_unload(self):
        """卸載時關閉翻譯模組的共用 aiohttp session。

        ⚠️ 為什麼放在這支 cog：`translate.py` 不是 cog，沒有自己的卸載時機，
        而它內部為了避免每次重付連線成本，共用了一個模組層級的 ClientSession。
        這支 cog 是翻譯功能唯一被載入的 cog，掛在這裡就能確保
        `bot.close()`（含 Autorestart 的定時重啟）會把連線釋放掉，
        不會留下「未關閉的 session」警告或連線洩漏。
        """
        if _TR is not None:
            try:
                await _TR.close_session()
            except Exception as error:  # pragma: no cover
                log.warning(f"⚠️ [翻譯] 關閉 session 時發生問題: {error!r}")


async def setup(bot: commands.Bot):
    await bot.add_cog(TranslateAdminCog(bot))
