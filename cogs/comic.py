# -*- coding: utf-8 -*-
import os
import re
import gc
import shutil
import asyncio
import discord
import img2pdf
from discord.ext import commands
from discord import app_commands
from PIL import Image

from bot_monitor import BotMonitor
from config import dl_manager, option, current_dir
from logger_config import log
from utils import upload_to_pixeldrain  # 💡 確保是從你的 utils 引入上傳函數

class ComicCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name='jmv', description='預覽本子的標題、作者、標籤等資訊')
    @app_commands.describe(album_id='請輸入本子 ID 或完整網址')
    async def view_comic(self, interaction: discord.Interaction, album_id: str):
        if hasattr(interaction.channel, 'nsfw') and not interaction.channel.nsfw:
            await interaction.response.send_message("❌ 此指令包含成人內容，請前往 **開啟年齡限制 (NSFW)** 的頻道中使用！", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        album_id = "".join(re.findall(r'\d+', album_id.split('/')[-1]))
        if not album_id.isdigit():
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

            embed = discord.Embed(title=title, url=f"https://18comic.vip/album/{album_id}", color=discord.Color.orange())
            embed.add_field(name="✍️ 作者", value=author, inline=True)
            embed.add_field(name="🏷️ 標籤", value=f"```{tags_str}```", inline=False)
            await interaction.edit_original_response(embed=embed)
            BotMonitor.log_bot_output(
                interaction,
                f"預覽 embed -> 標題: <{title}> | 作者: {author} | 標籤: {tags_str}"
            )
        except Exception as e:
            BotMonitor.log_preview_fail(album_id, e)
            await interaction.followup.send(f"❌ 查詢失敗: {e}")
            BotMonitor.log_bot_output(interaction, f"查詢失敗訊息 -> {e}")

    @app_commands.command(name='jm', description='智慧安全多線程下載本子圖片並託管至免空')
    @app_commands.describe(album_id='請輸入本子 ID 或完整網址')
    async def download_comic(self, interaction: discord.Interaction, album_id: str):
        if hasattr(interaction.channel, 'nsfw') and not interaction.channel.nsfw:
            await interaction.response.send_message("❌ 此指令包含成人內容，請前往 **開啟年齡限制 (NSFW)** 的頻道中使用！", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        album_id = "".join(re.findall(r'\d+', album_id.split('/')[-1]))
        if not album_id.isdigit():
            await interaction.followup.send("❌ 請輸入正確的本子 ID！")
            return

        if not dl_manager.acquire_album(album_id):
            BotMonitor.log_duplicate_prevented(interaction, album_id)
            await interaction.followup.send(f"⚠️ 本子 `{album_id}` 目前正在被其他使用者下載中，請勿重複提交！")
            return

        # 初始化需要跨作用域使用的變數
        output_pdf_paths = []  # 改為清單，支援多冊
        target_folder = None

        try:
            if dl_manager.semaphore.locked():
                BotMonitor.log_queue_entered(interaction, album_id)
                await interaction.edit_original_response(content="⏳ 目前伺服器下載通道已滿，您的指令已進入**排隊佇列**，請稍候...")

            async with dl_manager.semaphore:
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
                    invalid_chars = r'<>:"/\|?*'
                    cleaned_title = "".join([c for c in album_title if c not in invalid_chars]).strip()
                    
                    local_target_folder = os.path.join(current_dir, cleaned_title)
                    os.makedirs(local_target_folder, exist_ok=True)
                    
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
                            try: await interaction.edit_original_response(content=status_msg)
                            except: pass
                                
                        if tot > 0 and curr >= tot: break

                # 1. 執行多線程下載，並等待它完全打包結束
                download_worker = asyncio.to_thread(safe_download_process)
                progress_reporter = asyncio.create_task(report_progress_task())
                
                output_pdf_paths, target_folder = await download_worker
                await progress_reporter  # 確保下載與進度條協程都安全關閉
                
                # 2. ⚡ 核心修復：回到 Discord 安全的主異步線程中進行上傳與發送
                valid_pdfs = [p for p in output_pdf_paths if os.path.exists(p)]
                if valid_pdfs:
                    total_vols = len(valid_pdfs)
                    total_size_mb = sum(os.path.getsize(p) for p in valid_pdfs) / (1024 * 1024)

                    async def safe_edit_status(msg_text):
                        try: await interaction.edit_original_response(content=msg_text)
                        except discord.errors.HTTPException as he:
                            if he.status == 401: await interaction.channel.send(content=f"⚠️ {msg_text}")
                            else: raise he

                    vol_hint = f"共 {total_vols} 冊" if total_vols > 1 else ""
                    await safe_edit_status(
                        f"🎉 PDF 合併完成 (`{total_size_mb:.1f}MB` {vol_hint})！正在逐冊託管至 Pixeldrain 免空..."
                    )

                    upload_errors = []
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

                            title_text = progress_status['title']
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

                        except Exception as ue:
                            BotMonitor.log_upload_fail(album_id, ue)
                            upload_errors.append(f"{vol_label}：{ue}")
                            await interaction.channel.send(
                                f"❌ Pixeldrain 上傳失敗 {vol_label}，原因：\n```{str(ue)}```"
                            )
                            BotMonitor.log_bot_output(interaction, f"上傳失敗訊息 {vol_label} -> {ue}")

                    if not upload_errors:
                        await safe_edit_status("✅ 處理完成！所有 PDF 下載連結已成功發送。")
                    else:
                        await safe_edit_status(f"⚠️ 部分冊次上傳失敗，請查看頻道訊息。")
                else:
                    await interaction.channel.send("❌ 錯誤：找不到生成的 PDF 檔案。")
                    BotMonitor.log_bot_output(interaction, "錯誤訊息 -> 找不到生成的 PDF 檔案")
                    
        except Exception as e:
            try: await interaction.followup.send(f"❌ 錯誤：\n```{str(e)}```")
            except: await interaction.channel.send(f"❌ 錯誤：\n```{str(e)}```")
            BotMonitor.log_bot_output(interaction, f"外層錯誤訊息 -> {e}")
            
        finally:
            # 🧹 最終清理快取，確保 1GB RAM 不留任何殘留物
            for pdf_path in output_pdf_paths:
                if pdf_path and os.path.exists(pdf_path): os.remove(pdf_path)
            if target_folder and os.path.exists(target_folder): shutil.rmtree(target_folder)
            BotMonitor.log_cleanup_success(album_id)
            dl_manager.release_album(album_id)
            gc.collect()

async def setup(bot):
    await bot.add_cog(ComicCog(bot))