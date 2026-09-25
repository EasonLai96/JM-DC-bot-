# -*- coding: utf-8 -*-
import os
import re
import gc
import time
import shutil
import asyncio
import tempfile
import discord
import img2pdf
from discord.ext import commands
from discord import app_commands
from PIL import Image

from bot_monitor import BotMonitor
from config import dl_manager, option, current_dir
from logger_config import log
from utils import (
    upload_to_pixeldrain,
    is_nsfw_allowed,
    split_id_tokens,
    pack_embed_fields,
    safe_respond,
)
import comic_cache  # 🆕 下載結果快取：同一本子重複下載時直接重送上次的連結，省頻寬與時間

try:
    # 🆕 預覽結果底下的「⭐ 收藏這本」按鈕（cogs/favorites.py）。
    # ⚠️ 這裡刻意用 try/except 包住：收藏只是附加功能，萬一收藏模組載入失敗，
    # 也絕對不能連帶讓漫畫查詢／下載整支 cog 載入失敗。
    #
    # 🛠️ 修復：改成「絕對匯入優先、相對匯入備援」。cogs/ 被加進了 sys.path，所以這個
    # 檔案有可能被別的模組用頂層名稱 `comic` 匯入，那種情況下 __package__ 是空的，
    # `from .favorites import ...` 會直接失敗。絕對匯入兩種載入方式都能通，而且都指向
    # 同一個 cogs.favorites_store（同一把鎖），不會產生兩份收藏資料。
    from cogs.favorites import FavoriteButtonView
except Exception:
    try:
        from .favorites import FavoriteButtonView
    except Exception as _fav_err:  # pragma: no cover
        FavoriteButtonView = None
        log.warning(f"⚠️ [收藏] 無法載入收藏按鈕，/jmv 將不顯示收藏按鈕: {_fav_err!r}")

try:
    # 🌐 顯示層翻譯（cogs/translate.py）。同樣刻意用 try/except 包住：
    # 翻譯只是附加功能，載入失敗也絕不能讓整支漫畫 cog 掛掉。
    # 失敗時下面的 `_tr_title()` 會直接回傳原文，顯示層完全不受影響。
    from cogs import translate as _TR
except Exception:
    try:
        import translate as _TR  # type: ignore[no-redef]
    except Exception as _tr_err:  # pragma: no cover
        _TR = None
        log.warning(f"⚠️ [翻譯] 無法載入翻譯模組，將只顯示原文: {_tr_err!r}")


async def _tr_title(title, limit: int = 240) -> str:
    """回傳「中文（原文）」或原文。永遠不會拋例外。

    ⚠️ 禁漫（JM）站方的標題本來就是簡體中文，`translate` 會判定「已是中文」
    而直接回傳原文（不會做簡繁轉換），所以 JM 這邊實際只對少數英文本生效。
    """
    text = str(title or "")
    if _TR is None or not text:
        return text
    try:
        return await _TR.translate_display(text, limit=limit)
    except Exception as error:  # pragma: no cover
        log.warning(f"⚠️ [翻譯] 標題翻譯失敗，改用原文: {error!r}")
        return text


# ==================== 🧰 佇列顯示用小工具 ====================

def _fmt_duration(seconds) -> str:
    """把秒數格式化成「2 分 30 秒」這種人類可讀形式。"""
    if seconds is None:
        return "未知"
    try:
        total = int(max(0, float(seconds)))
    except (TypeError, ValueError):
        return "未知"
    if total < 60:
        return f"{total} 秒"
    minutes, sec = divmod(total, 60)
    if minutes < 60:
        return f"{minutes} 分 {sec} 秒" if sec else f"{minutes} 分"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} 小時 {minutes} 分"


def _safe_title(title, limit: int = 80) -> str:
    """書名來自遠端資料，塞進 embed 前先清掉會破壞 Markdown 格式的字元並截長。"""
    text = str(title or "未知標題")
    for ch in ("`", "*", "_", "|", "~"):
        text = text.replace(ch, "")
    text = text.replace("\n", " ").strip() or "未知標題"
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ⚠️ Discord 單一 embed 最多只能有 25 個 field，/queue 在排隊人潮多時必須截斷顯示，
# 否則整則訊息會直接被 API 拒絕（field 數量超限）。
MAX_QUEUE_ROWS = 8


def _parse_album_id(raw) -> str:
    """從使用者輸入解析出禁漫本子 ID。

    🛠️ 修復：舊版寫 `"".join(re.findall(r'\\d+', album_id.split('/')[-1]))`，
    只要網址帶結尾斜線（https://18comic.vip/album/123456/）或 query string
    （.../123456/?lang=zh），`split('/')[-1]` 就會取到空字串 → 直接回
    「請輸入正確的本子 ID」。這是使用者最常見的貼法之一，等於功能半殘。

    新作法：先去掉 query/fragment 與結尾斜線，優先抓 /album/<數字> 或
    /photos/<數字>，抓不到才退而取最後一串數字。
    """
    if raw is None:
        return ""
    text = str(raw).strip()
    if not text:
        return ""
    # 去掉 query string 與 fragment，再移除結尾斜線
    text = text.split("?")[0].split("#")[0].rstrip("/").strip()
    if not text:
        return ""
    for pattern in (r"/album/(\d+)", r"/photos/(\d+)", r"/g/(\d+)"):
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(1)
    if text.isdigit():
        return text
    numbers = re.findall(r"\d+", text)
    return numbers[-1] if numbers else ""


# 🆕 批量查詢一次最多處理幾本（避免一次打太多 API、也避免回覆超過 Discord 限制）
MAX_BATCH_IDS = 10

# 🆕 每筆查詢之間的禮貌間隔（秒）。禁漫站台不喜歡突發流量，
# 一次 10 本連續猛打容易觸發限流甚至暫時封鎖。
BATCH_QUERY_INTERVAL = 0.6


def _parse_album_id_list(raw, limit: int = MAX_BATCH_IDS):
    """把使用者貼上的一串 ID／網址解析成去重後的 ID 清單。

    回傳 (ids, dropped, invalid)：
      ids     —— 解析成功且去重後的 ID（最多 limit 個）
      dropped —— 因為超過 limit 而被忽略的數量
      invalid —— 完全解析不出數字的 token 數量（讓使用者知道有東西被略過）
    """
    ids, seen = [], set()
    dropped = invalid = 0
    for token in split_id_tokens(raw):
        parsed = _parse_album_id(token)
        if not parsed:
            invalid += 1
            continue
        if parsed in seen:
            continue
        seen.add(parsed)
        if len(ids) >= limit:
            dropped += 1
            continue
        ids.append(parsed)
    return ids, dropped, invalid


class ComicCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name='jmv', description='預覽本子的標題、作者、標籤等資訊')
    @app_commands.describe(album_id='請輸入本子 ID 或完整網址')
    async def view_comic(self, interaction: discord.Interaction, album_id: str):
        if not is_nsfw_allowed(interaction.channel):
            await interaction.response.send_message("❌ 此指令包含成人內容，請前往 **開啟年齡限制 (NSFW)** 的頻道中使用！", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        album_id = _parse_album_id(album_id)
        if not album_id:
            await interaction.followup.send("❌ 請輸入正確的本子 ID！")
            return

        try:
            BotMonitor.log_preview_request(interaction, album_id)
            client = option.new_jm_client()
            album = await asyncio.to_thread(client.get_album_detail, album_id)
            
            title = getattr(album, 'title', '無標題')
            author = getattr(album, 'author', '未知')
            if not author or str(author).strip() == "": author = '未知'
            
            tags_list = getattr(album, 'tags', [])
            if not tags_list and hasattr(album, 'tag_list'):
                tags_list = album.tag_list
            tags_str = ", ".join(tags_list) if tags_list else "無標籤"

            # 🌐 少數禁漫本子的標題是英文（例：High School Legend : Red Dragon），
            # 這裡翻成「中文（原文）」。中文標題不會被動到。
            # ⚠️ `title` 本身保持原文，收藏庫與 log 用的都是它。
            title_display = await _tr_title(title, limit=240)
            embed = discord.Embed(title=title_display, url=f"https://18comic.vip/album/{album_id}", color=discord.Color.orange())
            embed.add_field(name="✍️ 作者", value=author, inline=True)
            embed.add_field(name="🏷️ 標籤", value=f"```{tags_str}```", inline=False)
            # 🆕 預覽結果底下附一顆「⭐ 收藏這本」按鈕：書名現成的，順手按一下就能收進
            # 共用收藏庫（JM 前綴），不用再自己抄 ID 去打 /favorite。
            # FavoriteButtonView.create() 保證不拋例外，收藏功能的任何問題都不會讓查詢失敗。
            favorite_view = None
            if FavoriteButtonView is not None:
                favorite_view = await FavoriteButtonView.create(
                    interaction.user.id, "JM", album_id, str(title or "")
                )
            await interaction.edit_original_response(embed=embed, view=favorite_view)
            BotMonitor.log_bot_output(
                interaction,
                f"預覽 embed -> 標題: <{title}> | 作者: {author} | 標籤: {tags_str}"
            )
        except Exception as e:
            # 依你的要求改回顯示完整訊息：jmcomic 函式庫丟出的錯誤（例如「本子不存在，
            # 原因可能為：id有誤 / 該漫畫只對登入用戶可見」）本身就是設計給使用者看的
            # 友善提示，不是原始 Python traceback 或內部路徑，所以直接顯示對使用者有幫助。
            BotMonitor.log_preview_fail(album_id, e)
            await interaction.followup.send(f"❌ 查詢失敗: {e}")
            BotMonitor.log_bot_output(interaction, f"查詢失敗訊息 -> {e}")

    # ==========================================
    # 📚 批量查詢（一次查多本）
    # ==========================================
    @app_commands.command(name='jmbatch', description=f'批量查詢多本禁漫本子的資訊（一次最多 {MAX_BATCH_IDS} 本）')
    @app_commands.describe(ids='多個本子 ID 或網址，用空白、逗號或換行分隔（例：123456 234567 345678）')
    @app_commands.checks.cooldown(1, 20.0, key=lambda i: i.user.id)
    async def batch_view_comic(self, interaction: discord.Interaction, ids: str):
        if not is_nsfw_allowed(interaction.channel):
            await interaction.response.send_message("❌ 此指令包含成人內容，請前往 **開啟年齡限制 (NSFW)** 的頻道中使用！", ephemeral=True)
            return

        album_ids, dropped, invalid = _parse_album_id_list(ids)
        if not album_ids:
            await interaction.response.send_message(
                "❌ 沒有解析到任何有效的本子 ID。\n"
                "請用空白、逗號或換行分隔，例如 `123456 234567` 或 `https://18comic.vip/album/123456/`",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True)
        await interaction.edit_original_response(
            content=f"📚 開始批量查詢 **{len(album_ids)}** 本，請稍候…"
                    f"（每本間隔 {BATCH_QUERY_INTERVAL} 秒，避免被站台限流）"
        )

        client = option.new_jm_client()
        fields = []
        success = failed = 0

        for index, album_id in enumerate(album_ids, start=1):
            try:
                album = await asyncio.to_thread(client.get_album_detail, album_id)
                title = getattr(album, 'title', '無標題') or '無標題'
                author = getattr(album, 'author', '未知') or '未知'
                if str(author).strip() == "":
                    author = '未知'
                tags_list = getattr(album, 'tags', []) or getattr(album, 'tag_list', []) or []
                tags_str = "、".join(str(t) for t in tags_list) if tags_list else "無標籤"

                # 🌐 批量查詢是每本一行的緊湊格式，逐本翻書名（英文標題的本子很少）。
                # translate 有快取，使用者重複查同一批不會再打 API。
                title_display = await _tr_title(title, limit=60)

                fields.append((
                    f"#{album_id} · {_safe_title(title_display, 60)}",
                    f"✍️ {_safe_title(author, 40)}\n"
                    f"🏷️ {tags_str[:700]}\n"
                    f"🔗 [開啟原站](https://18comic.vip/album/{album_id})",
                ))
                success += 1
            except Exception as e:
                fields.append((f"❌ #{album_id}", f"查詢失敗：{_safe_title(e, 200)}"))
                failed += 1
                BotMonitor.log_preview_fail(album_id, e)

            if index < len(album_ids):   # 最後一筆不用再多等
                await asyncio.sleep(BATCH_QUERY_INTERVAL)

        # 依 Discord 的 field 數量／長度限制自動分頁
        summary = f"共查詢 **{success + failed}** 本　｜　✅ 成功 {success}　❌ 失敗 {failed}"
        notes = []
        if dropped:
            notes.append(f"⚠️ 另有 {dropped} 個超出單次上限（{MAX_BATCH_IDS} 本），已忽略")
        if invalid:
            notes.append(f"⚠️ {invalid} 個項目無法解析成 ID，已略過")
        if notes:
            summary += "\n" + "\n".join(notes)

        groups = pack_embed_fields(fields)
        embeds = []
        for index, group in enumerate(groups[:10], start=1):
            embed = discord.Embed(
                title="📚 禁漫批量查詢結果",
                description=summary if index == 1 else None,
                color=discord.Color.orange(),
            )
            for name, value in group:
                embed.add_field(name=name, value=value, inline=False)
            if len(groups) > 1:
                embed.set_footer(text=f"第 {index} / {len(groups)} 頁")
            embeds.append(embed)

        await interaction.edit_original_response(content=None, embeds=embeds)
        BotMonitor.log_bot_output(
            interaction,
            f"批量查詢完成 -> 成功 {success} / 失敗 {failed}（共 {len(album_ids)} 本）",
        )

    @app_commands.command(name='jm', description='智慧安全多線程下載本子圖片並託管至免空')
    @app_commands.describe(album_id='請輸入本子 ID 或完整網址')
    async def download_comic(self, interaction: discord.Interaction, album_id: str):
        if not is_nsfw_allowed(interaction.channel):
            await interaction.response.send_message("❌ 此指令包含成人內容，請前往 **開啟年齡限制 (NSFW)** 的頻道中使用！", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        album_id = _parse_album_id(album_id)
        if not album_id:
            await interaction.followup.send("❌ 請輸入正確的本子 ID！")
            return

        # 🆕 快取檢查：這本之前有人成功下載過、且還在有效期內的話，
        # 先驗證上次的 Pixeldrain 連結是否還活著，活著就直接重送，完全跳過
        # 解析/下載/壓縮/打包/上傳，秒速回應且不佔用下載通道。
        cached = comic_cache.get_cached_album(album_id)
        if cached:
            alive = await comic_cache.verify_links_alive([v['url'] for v in cached['volumes']])
            if alive:
                BotMonitor.log_bot_output(interaction, f"快取命中 -> 本子 {album_id}，直接重送連結，未重新下載")
                await interaction.edit_original_response(
                    content=f"⚡ 本子 `{album_id}` 先前已下載過，直接使用快取結果，秒速送出！"
                )
                cached_total_vols = len(cached['volumes'])
                for vol in cached['volumes']:
                    vol_label = f"第 {vol['vol_idx']}/{cached_total_vols} 冊" if cached_total_vols > 1 else ""
                    # 🌐 快取裡存的是原文書名（cached['title']），翻譯只在顯示時做，
                    # 所以 comic_cache.json 的內容完全不會被本功能改動。
                    title_text = await _tr_title(cached['title'], limit=200)
                    if cached_total_vols > 1:
                        title_text += f"（{vol_label}，頁 {vol['page_start']}～{vol['page_end']}）"

                    embed = discord.Embed(
                        title=f"📄 {title_text}",
                        description="本子已成功轉換為 PDF 並託管至外部雲端，方便手機直接閱讀。本通道符合安全環境與社群防護規範。\n（⚡ 此結果來自快取，未重新下載）",
                        color=discord.Color.green()
                    )
                    embed.add_field(name="🗂️ 檔案大小", value=f"`{vol['size_mb']:.1f} MB`", inline=True)
                    embed.add_field(name="⏳ 有效期限", value="`3 個月內無人下載將自動清除`", inline=True)
                    if cached_total_vols > 1:
                        embed.add_field(name="📚 分冊資訊", value=f"`{vol_label} / 共 {cached_total_vols} 冊`", inline=True)
                    embed.add_field(name="🔗 下載連結", value=f"[點我直接下載 PDF]({vol['url']})", inline=False)

                    view = discord.ui.View()
                    btn_label = f"下載 {vol_label} PDF" if cached_total_vols > 1 else "點此安全下載本子 PDF"
                    view.add_item(discord.ui.Button(
                        label=btn_label, url=vol['url'],
                        style=discord.ButtonStyle.link, emoji="📥"
                    ))
                    await interaction.channel.send(embed=embed, view=view)

                await interaction.edit_original_response(content="✅ 處理完成（快取命中，未重新下載）！所有 PDF 下載連結已成功發送。")
                BotMonitor.log_bot_output(interaction, f"快取命中送出完成 -> {album_id}")
                return
            else:
                # 連結已經失效（可能被 Pixeldrain 清除），快取沒有意義了，清掉並照常重新下載
                comic_cache.invalidate_cached_album(album_id)
                BotMonitor.log_bot_output(interaction, f"快取連結已失效，改為重新下載 -> {album_id}")

        # 🆕 登記任務時一併記下「誰在排隊」，/queue 才能顯示出有意義的資訊
        guild_name = interaction.guild.name if interaction.guild else "私訊"
        if not dl_manager.acquire_album(
            album_id,
            user_id=interaction.user.id,
            user_name=interaction.user.display_name,
            guild_name=guild_name,
        ):
            BotMonitor.log_duplicate_prevented(interaction, album_id)
            await interaction.followup.send(f"⚠️ 本子 `{album_id}` 目前正在被其他使用者下載中，請勿重複提交！")
            return

        # 初始化需要跨作用域使用的變數
        output_pdf_paths = []  # 改為清單，支援多冊
        target_folder = None

        try:
            if dl_manager.semaphore.locked():
                BotMonitor.log_queue_entered(interaction, album_id)
                # 🆕 明確告訴使用者排在第幾位、大概要等多久（原本只說「已進入排隊佇列」）
                position = dl_manager.queue_position(album_id)
                if position:
                    ahead, waiting_total = position
                    eta = dl_manager.estimate_wait_seconds(ahead)
                    eta_text = f"，預估還要等約 **{_fmt_duration(eta)}**" if eta is not None else ""
                    queue_text = (
                        f"⏳ 目前 {dl_manager.max_concurrent} 個下載通道已滿，"
                        f"您排在第 **{ahead + 1}** 位（共 {waiting_total} 個等待中）{eta_text}。\n"
                        f"💡 隨時可用 `/queue` 查看即時佇列狀態。"
                    )
                else:
                    queue_text = (
                        "⏳ 目前伺服器下載通道已滿，您的指令已進入**排隊佇列**，請稍候..."
                        "（可用 `/queue` 查看狀態）"
                    )
                await interaction.edit_original_response(content=queue_text)

            async with dl_manager.semaphore:
                dl_manager.mark_started(album_id)   # 🆕 正式從「排隊」轉為「執行中」
                BotMonitor.log_task_start(interaction, album_id)
                await interaction.edit_original_response(content=f"📥 成功獲取通道！正在解析本子 `{album_id}` 結構...")

                progress_status = {"current": 0, "total": 0, "title": "獲取中..."}

                # 💡 子執行緒：只做「下載、壓縮、打包 ZIP」
                def safe_download_process():
                    gc.collect()
                    client = option.new_jm_client()
                    
                    album = client.get_album_detail(album_id)
                    if not album:
                        raise ValueError("無法解析該本子，可能不存在或伺服器斷線。")
                        
                    album_title = album.title
                    progress_status["title"] = album_title
                    dl_manager.update_progress(album_id, title=album_title)   # 🆕 讓 /queue 能顯示書名

                    # 🛠️ 修復（可能刪光整個程式目錄）：舊版把下載資料夾建在
                    # `os.path.join(current_dir, 書名)`，最後再無條件 shutil.rmtree。
                    # 這有兩個嚴重風險：
                    #   1) 書名把 < > : " / \ | ? * 清掉後若變成空字串 →
                    #      target_folder == current_dir → 整個程式目錄（含 profiles.json、
                    #      token.env）被刪掉。在雲伺服器上 current_dir 就是 bot 根目錄。
                    #   2) 書名剛好等於既有資料夾名（例如 profile_data）→ 沿用既有目錄後
                    #      連帶把玩家經濟資料整包刪除。
                    # 改用 tempfile.mkdtemp() 建立專屬暫存目錄（nhentai.py 已是正確示範），
                    # 路徑由系統管理，從根本上不可能指到程式目錄。
                    local_target_folder = tempfile.mkdtemp(prefix=f"jm_{album_id}_")
                    
                    photos = getattr(album, 'photo_list', []) or list(getattr(album, 'photo_dict', {}).values())
                    if not photos: photos = list(album)

                    image_details = []
                    for photo in photos:
                        photo_detail = client.get_photo_detail(photo.photo_id)
                        img_list = getattr(photo_detail, 'image_list', []) or list(getattr(photo_detail, 'image_dict', {}).values())
                        if not img_list: img_list = list(photo_detail)
                        image_details.extend(img_list)
                            
                    total_pages = len(image_details)
                    progress_status["total"] = total_pages
                    dl_manager.update_progress(album_id, total=total_pages)   # 🆕 讓 /queue 能推算 ETA
                    if total_pages == 0:
                        raise ValueError("未能解析到任何圖片。")

                    BotMonitor.log_structure_success(album_id, album_title, total_pages)

                    # 內建多執行緒下載單張圖片與即時微調
                    def download_single_image(args):
                        idx, img_detail = args
                        page_num = idx + 1
                        filename = f"{page_num:05d}.jpg"
                        save_path = os.path.join(local_target_folder, filename)

                        try:
                            client.download_by_image_detail(img_detail, save_path)
                        except Exception as de:
                            BotMonitor.log_compress_skip(album_id, page_num, de)
                            return page_num, None

                        # 確認檔案真的存在且不是空檔，避免之後合併 PDF 時抓到壞檔
                        if not os.path.exists(save_path) or os.path.getsize(save_path) == 0:
                            BotMonitor.log_compress_skip(album_id, page_num, "下載後檔案不存在或大小為 0")
                            return page_num, None

                        try:
                            with Image.open(save_path) as img:
                                if img.mode != 'RGB': img = img.convert('RGB')
                                img.save(save_path, 'JPEG', quality=55, optimize=True)
                        except Exception as ce:
                            # 壓縮失敗不代表下載失敗，原始檔案還在，仍可正常使用
                            BotMonitor.log_compress_skip(album_id, page_num, ce)

                        return page_num, save_path

                    from concurrent.futures import ThreadPoolExecutor
                    download_results = []
                    with ThreadPoolExecutor(max_workers=4) as executor:
                        tasks = [(i, img) for i, img in enumerate(image_details)]
                        for page_num, saved_path in executor.map(download_single_image, tasks):
                            progress_status["current"] = page_num
                            dl_manager.update_progress(album_id, current=page_num)   # 🆕 即時回報佇列管理器
                            BotMonitor.log_download_progress(album_id, page_num, total_pages)
                            download_results.append((page_num, saved_path))
                            if page_num % 4 == 0: gc.collect()

                    # 💡 直接用下載迴圈回報的實際路徑，依頁碼排序，
                    # 不再靠事後 glob 猜檔名/副檔名，徹底避免抓到空清單。
                    image_files = [path for _, path in sorted(download_results) if path]
                    failed_count = total_pages - len(image_files)

                    if not image_files:
                        raise ValueError("下載失敗：所有頁面都無法成功下載，請確認本子 ID 是否正確，或稍後再試一次。")

                    if failed_count > 0:
                        log.warning(f"⚠️ [部分頁面缺失] ID: {album_id} | 共 {failed_count}/{total_pages} 頁下載失敗，PDF 將略過這些頁面")

                    BotMonitor.log_compress_start(album_id)

                    # ── 切割參數 ────────────────────────────────────────────────
                    # 每份 PDF 最多包含幾頁；超過此數量自動分冊。
                    # 500 頁 × 平均壓縮後約 0.3MB = ~150MB / 份，對 Pixeldrain 友好。
                    PAGES_PER_CHUNK = 500

                    # ── 尺寸安全函式（保留上一版修正）───────────────────────────
                    IMG2PDF_MAX_PT = 14400  # img2pdf 上限：14400 pt = 200 inch
                    IMG2PDF_MIN_PT = 3

                    def safe_img2pdf_layout(img_path):
                        """若圖片尺寸換算後超出 PDF 規格，回傳對應 layout_fun；否則回傳 None。"""
                        try:
                            with Image.open(img_path) as im:
                                w_px, h_px = im.size
                        except Exception:
                            return None
                        max_px = max(w_px, h_px)
                        if max_px > IMG2PDF_MAX_PT:
                            safe_dpi = int(max_px / 200.0) + 1
                            return img2pdf.get_layout_fun((
                                img2pdf.in_to_pt(w_px / safe_dpi),
                                img2pdf.in_to_pt(h_px / safe_dpi)
                            ))
                        min_px = min(w_px, h_px)
                        if min_px < IMG2PDF_MIN_PT:
                            return img2pdf.get_layout_fun((
                                img2pdf.in_to_pt(max(w_px / 72.0, 1.0)),
                                img2pdf.in_to_pt(max(h_px / 72.0, 1.0))
                            ))
                        return None

                    def build_chunk_pdf(chunk_files, out_path):
                        """將一批圖片路徑合併為單一 PDF 檔案，自動處理尺寸超限。"""
                        has_custom = any(safe_img2pdf_layout(p) is not None for p in chunk_files)

                        if not has_custom:
                            # 快速路徑：全部尺寸正常，直接批次轉換
                            with open(out_path, 'wb') as f:
                                f.write(img2pdf.convert(chunk_files))
                        else:
                            # 降級路徑：用 Pillow 逐張讀取後輸出 PDF（無尺寸限制）
                            pil_imgs = []
                            for p in chunk_files:
                                try:
                                    pil_imgs.append(Image.open(p).convert('RGB'))
                                except Exception as pe:
                                    log.warning(f"⚠️ [PIL chunk] 無法開啟圖片 {p}: {pe}")
                            if not pil_imgs:
                                raise ValueError(f"無法讀取任何圖片以產生 PDF：{out_path}")
                            pil_imgs[0].save(
                                out_path, format='PDF', save_all=True,
                                append_images=pil_imgs[1:], resolution=150
                            )
                            for im in pil_imgs:
                                im.close()
                        gc.collect()

                    # ── 按頁數切割，產生一或多個 PDF ────────────────────────────
                    total_images = len(image_files)
                    chunks = [
                        image_files[i:i + PAGES_PER_CHUNK]
                        for i in range(0, total_images, PAGES_PER_CHUNK)
                    ]
                    total_chunks = len(chunks)

                    output_pdf_paths = []  # 最終回傳所有 PDF 路徑
                    for idx, chunk in enumerate(chunks, start=1):
                        if total_chunks == 1:
                            chunk_name = f"comic_{album_id}.pdf"
                        else:
                            chunk_name = f"comic_{album_id}_vol{idx:02d}of{total_chunks:02d}.pdf"
                        chunk_path = os.path.join(current_dir, chunk_name)
                        if os.path.exists(chunk_path): os.remove(chunk_path)

                        page_start = (idx - 1) * PAGES_PER_CHUNK + 1
                        page_end   = min(idx * PAGES_PER_CHUNK, total_images)
                        log.info(f"📄 [PDF切割] {album_id} → 第 {idx}/{total_chunks} 冊 "
                                 f"(頁 {page_start}~{page_end})，共 {len(chunk)} 頁")
                        build_chunk_pdf(chunk, chunk_path)
                        output_pdf_paths.append(chunk_path)

                    return output_pdf_paths, local_target_folder

                # 前台 Discord 進度條刷新協程
                async def report_progress_task():
                    last_reported = -1
                    while True:
                        await asyncio.sleep(2.5)
                        curr = progress_status["current"]
                        tot = progress_status["total"]
                        
                        if tot > 0 and curr != last_reported:
                            last_reported = curr
                            percent = (curr / tot) * 100
                            bar_length = 12
                            filled_length = int(round(bar_length * curr / tot))
                            bar = '🟩' * filled_length + '⬜' * (bar_length - filled_length)
                            
                            status_msg = (
                                f"📥 **正在安全多線下載本子中...**\n"
                                f"📖 標題：*{progress_status['title']}*\n"
                                f"📊 進度：`[{bar}]` **{percent:.1f}%** ({curr} / {tot} 頁)"
                            )
                            # 🛠️ 修復（L2）：進度訊息的標題來自本子資料（等於第三方可控文字），
                            # 而「編輯訊息」跟「送出訊息」一樣會解析提及 —— 標題裡若有
                            # @everyone 就會真的 ping 整個頻道。關閉提及解析。
                            try: await interaction.edit_original_response(
                                content=status_msg, allowed_mentions=discord.AllowedMentions.none()
                            )
                            except Exception: pass
                                
                        if tot > 0 and curr >= tot: break

                # 1. 執行多線程下載，並等待它完全打包結束
                download_worker = asyncio.to_thread(safe_download_process)
                progress_reporter = asyncio.create_task(report_progress_task())

                # 🛠️ 修復（殭屍任務）：舊版是 `await download_worker` 之後才
                # `await progress_reporter`。只要 download_worker 丟例外，這個協程就
                # 永遠不會被 await 或 cancel；而 report_progress_task 唯一的結束條件是
                # `curr >= tot`，下載失敗時永遠不成立（total 甚至可能還是 0）→
                # 它會變成一個每 2.5 秒無限呼叫 edit_original_response 的洩漏任務，
                # 例外全被 `except: pass` 吞掉，永遠不會停。
                # 這裡保證無論成功或失敗，這個協程都會被取消並確實回收。
                try:
                    output_pdf_paths, target_folder = await download_worker
                finally:
                    if not progress_reporter.done():
                        progress_reporter.cancel()
                    try:
                        await progress_reporter
                    except asyncio.CancelledError:
                        pass  # 這是我們自己發出的取消，屬正常結束路徑
                
                # 2. ⚡ 核心修復：回到 Discord 安全的主異步線程中進行上傳與發送
                valid_pdfs = [p for p in output_pdf_paths if os.path.exists(p)]
                if valid_pdfs:
                    total_vols = len(valid_pdfs)
                    total_size_mb = sum(os.path.getsize(p) for p in valid_pdfs) / (1024 * 1024)

                    async def safe_edit_status(msg_text):
                        # 🛠️ 修復（L2）：同上，訊息含本子標題等第三方文字，關閉提及解析。
                        try: await interaction.edit_original_response(
                            content=msg_text, allowed_mentions=discord.AllowedMentions.none()
                        )
                        except discord.errors.HTTPException as he:
                            if he.status == 401: await interaction.channel.send(content=f"⚠️ {msg_text}")
                            else: raise he

                    vol_hint = f"共 {total_vols} 冊" if total_vols > 1 else ""
                    await safe_edit_status(
                        f"🎉 PDF 合併完成 (`{total_size_mb:.1f}MB` {vol_hint})！正在逐冊託管至 Pixeldrain 免空..."
                    )

                    upload_errors = []
                    cached_volumes = []  # 🆕 收集每冊的連結/大小/頁碼範圍，全部成功後寫入快取
                    for vol_idx, pdf_path in enumerate(valid_pdfs, start=1):
                        file_size_mb = os.path.getsize(pdf_path) / (1024 * 1024)
                        vol_label = f"第 {vol_idx}/{total_vols} 冊" if total_vols > 1 else ""

                        try:
                            if total_vols > 1:
                                await safe_edit_status(
                                    f"📤 正在上傳 {vol_label}（`{file_size_mb:.1f}MB`）..."
                                )
                            BotMonitor.log_upload_start(album_id, file_size_mb)
                            download_url = await upload_to_pixeldrain(pdf_path)
                            BotMonitor.log_upload_success(album_id, download_url)

                            # 🌐 顯示層翻譯：progress_status['title'] 是原文，
                            # 翻的只有要放進 embed 的顯示字串，/queue 仍顯示原文。
                            title_text = await _tr_title(progress_status['title'], limit=200)
                            if total_vols > 1:
                                title_text += f"（{vol_label}，頁 {(vol_idx-1)*500+1}～{min(vol_idx*500, progress_status['total'])}）"

                            embed = discord.Embed(
                                title=f"📄 {title_text}",
                                description="本子已成功轉換為 PDF 並託管至外部雲端，方便手機直接閱讀。本通道符合安全環境與社群防護規範。",
                                color=discord.Color.green()
                            )
                            embed.add_field(name="🗂️ 檔案大小", value=f"`{file_size_mb:.1f} MB`", inline=True)
                            embed.add_field(name="⏳ 有效期限", value="`3 個月內無人下載將自動清除`", inline=True)
                            if total_vols > 1:
                                embed.add_field(name="📚 分冊資訊", value=f"`{vol_label} / 共 {total_vols} 冊`", inline=True)
                            embed.add_field(name="🔗 下載連結", value=f"[點我直接下載 PDF]({download_url})", inline=False)

                            view = discord.ui.View()
                            btn_label = f"下載 {vol_label} PDF" if total_vols > 1 else "點此安全下載本子 PDF"
                            view.add_item(discord.ui.Button(
                                label=btn_label, url=download_url,
                                style=discord.ButtonStyle.link, emoji="📥"
                            ))

                            await interaction.channel.send(embed=embed, view=view)
                            BotMonitor.log_bot_output(
                                interaction,
                                f"下載完成 {vol_label} -> 標題: <{progress_status['title']}> | "
                                f"大小: {file_size_mb:.1f}MB | 連結: {download_url}"
                            )

                            cached_volumes.append({
                                'vol_idx': vol_idx,
                                'url': download_url,
                                'size_mb': file_size_mb,
                                'page_start': (vol_idx - 1) * 500 + 1,
                                'page_end': min(vol_idx * 500, progress_status['total']),
                            })

                        except Exception as ue:
                            BotMonitor.log_upload_fail(album_id, ue)
                            upload_errors.append(f"{vol_label}：{ue}")
                            await interaction.channel.send(
                                f"❌ Pixeldrain 上傳失敗 {vol_label}，原因：\n```{str(ue)}```"
                            )
                            BotMonitor.log_bot_output(interaction, f"上傳失敗訊息 {vol_label} -> {ue}")

                    if not upload_errors:
                        # 🆕 全部冊次都成功上傳才寫入快取——只要有任何一冊失敗，
                        # 就不快取這本，避免下次快取命中卻只拿到殘缺的冊數。
                        comic_cache.save_cached_album(
                            album_id, progress_status['title'], progress_status['total'], cached_volumes
                        )
                        await safe_edit_status("✅ 處理完成！所有 PDF 下載連結已成功發送。")
                    else:
                        await safe_edit_status(f"⚠️ 部分冊次上傳失敗，請查看頻道訊息。")
                else:
                    await interaction.channel.send("❌ 錯誤：找不到生成的 PDF 檔案。")
                    BotMonitor.log_bot_output(interaction, "錯誤訊息 -> 找不到生成的 PDF 檔案")
                    
        except Exception as e:
            # 依你的要求改回顯示完整訊息（同上，jmcomic 的錯誤內容本身是給使用者看的）。
            # 🛠️ 修復：① 訊息上限 2000 字，jmcomic 的例外內容常常超過 → 送出時被 400 拒絕，
            # 使用者什麼都看不到；② 原本的 except: 是裸 except，且 fallback 的
            # channel.send 失敗時會直接把例外再往上丟（在 except 區塊裡爆炸最難查）。
            err_text = str(e)[:1500]
            try:
                await interaction.followup.send(f"❌ 錯誤：\n```{err_text}```")
            except Exception:
                try:
                    await interaction.channel.send(f"❌ 錯誤：\n```{err_text}```")
                except Exception:
                    pass
            BotMonitor.log_bot_output(interaction, f"外層錯誤訊息 -> {e}")
            
        finally:
            # 🧹 最終清理快取，確保 1GB RAM 不留任何殘留物
            for pdf_path in output_pdf_paths:
                if pdf_path and os.path.exists(pdf_path): os.remove(pdf_path)
            # 🛠️ target_folder 現在一定是 tempfile.mkdtemp() 建的，但這裡仍多加一道
            # 保險：萬一它真的指到程式目錄，寧可不刪也不要刪錯。
            if target_folder and os.path.exists(target_folder):
                if os.path.abspath(target_folder) != os.path.abspath(current_dir):
                    shutil.rmtree(target_folder, ignore_errors=True)
            BotMonitor.log_cleanup_success(album_id)
            dl_manager.release_album(album_id)
            gc.collect()

    # ==========================================
    # 📋 佇列狀態查詢
    # ==========================================
    @app_commands.command(name='queue', description='查看目前的本子下載佇列狀態與預估等待時間')
    async def queue_status(self, interaction: discord.Interaction):
        snap = dl_manager.snapshot()
        running = snap["running"]
        waiting = snap["waiting"]

        if not running and not waiting:
            await interaction.response.send_message(
                f"✅ 目前沒有下載任務，**{snap['max_concurrent']} 個通道全部閒置中**，可以直接使用 `/jm`。"
            )
            return

        embed = discord.Embed(
            title="📋 本子下載佇列",
            description=(
                f"通道使用：**{len(running)} / {snap['max_concurrent']}**"
                f"（空閒 {snap['free_slots']} 個）　｜　排隊等待：**{len(waiting)}** 個"
            ),
            color=discord.Color.blurple(),
        )

        now = time.time()

        # ── 執行中的任務：顯示書名、進度條與預估剩餘時間 ──
        for idx, task in enumerate(running, start=1):
            if task.total > 0:
                filled = max(0, min(12, int(round(12 * task.current / task.total))))
                bar = "🟩" * filled + "⬜" * (12 - filled)
                progress = f"`[{bar}]` {task.current}/{task.total} 頁（{task.current / task.total * 100:.0f}%）"
            else:
                progress = "🔍 正在解析本子結構…"
            eta = task.eta_seconds()
            eta_text = f"　｜　預估剩餘 **{_fmt_duration(eta)}**" if eta is not None else ""
            embed.add_field(
                name=f"🟢 下載中 #{idx}　ID `{task.album_id}`",
                value=(
                    f"📖 {_safe_title(task.title)}\n"
                    f"👤 {task.user_name}　｜　⏱️ 已執行 {_fmt_duration(now - (task.started_at or now))}\n"
                    f"📊 {progress}{eta_text}"
                ),
                inline=False,
            )

        # ── 等待中的任務：顯示排隊位置與已等待時間（超過上限則截斷） ──
        shown = waiting[:MAX_QUEUE_ROWS]
        for pos, task in enumerate(shown, start=1):
            wait_est = dl_manager.estimate_wait_seconds(pos - 1)
            est_text = f"　｜　預估還要等 **{_fmt_duration(wait_est)}**" if wait_est is not None else ""
            embed.add_field(
                name=f"⏳ 排隊 #{pos}　ID `{task.album_id}`",
                value=f"👤 {task.user_name}　｜　⏱️ 已等待 {_fmt_duration(now - task.enqueued_at)}{est_text}",
                inline=False,
            )
        if len(waiting) > len(shown):
            embed.add_field(
                name="…",
                value=f"另外還有 **{len(waiting) - len(shown)}** 個任務在排隊（僅顯示前 {MAX_QUEUE_ROWS} 位）",
                inline=False,
            )

        embed.set_footer(text="💡 預估值依目前下載速度推算，僅供參考　｜　新增下載任務請使用 /jm")
        await interaction.response.send_message(embed=embed)

    # ==========================================
    # ⚠️ 統一錯誤處理
    # ==========================================
    async def cog_app_command_error(self, interaction: discord.Interaction,
                                    error: app_commands.AppCommandError):
        """🆕 之前這個 cog 完全沒有錯誤處理，而 /jmbatch 有加冷卻。

        沒有這個 handler 的話，冷卻觸發時例外會往上丟到 tree.on_error，
        使用者看到的是 Discord 的「應用程式未回應」，完全不知道發生什麼事。
        """
        if isinstance(error, app_commands.CommandOnCooldown):
            await safe_respond(
                interaction,
                f"⏳ 指令冷卻中，請於 **{error.retry_after:.1f}** 秒後再試一次。",
            )
            return
        log.error(f"❌ [漫畫指令] 未預期的指令錯誤：{error!r}")
        await safe_respond(interaction, "❌ 指令執行時發生未預期錯誤，請稍後再試。")


async def setup(bot):
    await bot.add_cog(ComicCog(bot))