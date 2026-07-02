# -*- coding: utf-8 -*-
import re
import discord
from discord.ext import commands
from logger_config import log

FIXERS = [
    # Twitter / X
    (r'https?://(?:www\.)?(?:twitter\.com|x\.com)(/.+)', 'https://fxtwitter.com'),
    # Instagram
    (r'https?://(?:www\.)?instagram\.com(/.+)', 'https://fxstagram.com'),
    # TikTok
    (r'https?://(?:www\.)?tiktok\.com(/.+)', 'https://tnktok.com'),
    (r'https?://vm\.tiktok\.com(/.+)', 'https://vm.tnktok.com'),
    # Reddit
    (r'https?://(?:www\.)?reddit\.com(/.+)', 'https://vxreddit.com'),
    (r'https?://(?:www\.)?redd\.it(/.+)', 'https://vxreddit.com'),
    # Bilibili
    (r'https?://(?:www\.)?bilibili\.com(/.+)', 'https://vxbilibili.com'),
    (r'https?://b23\.tv(/.+)', 'https://b23.vxbilibili.com'),
    # Pixiv
    (r'https?://(?:www\.)?pixiv\.net(/.+)', 'https://phixiv.net'),
    # Bluesky
    (r'https?://bsky\.app(/.+)', 'https://bskyx.app'),
]

def find_and_fix_links(content: str):
    results = []
    urls = re.findall(r'https?://[^\s<>"]+', content)
    for url in urls:
        for pattern, fixer_domain in FIXERS:
            match = re.match(pattern, url)
            if match:
                fixed = fixer_domain + match.group(1)
                results.append((url, fixed))
                break
    return results


class FixLinkCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        if not message.content:
            return

        if 'fxignore' in message.content.lower():
            return

        fixes = find_and_fix_links(message.content)
        if not fixes:
            return

        fixed_links = '\n'.join(fixed for _, fixed in fixes)
        reply = f"🔗 **修復連結：**\n{fixed_links}"

        try:
            await message.edit(suppress=True)
        except (discord.Forbidden, discord.HTTPException):
            pass

        await message.reply(reply, mention_author=False)

        for original, fixed in fixes:
            log.info(
                f"🔗 [修復連結] 伺服器: [{message.guild.name if message.guild else '私訊'}] "
                f"| 使用者: {message.author.name}({message.author.id}) "
                f"| 原始: {original} → 修復: {fixed}"
            )


async def setup(bot):
    await bot.add_cog(FixLinkCog(bot))