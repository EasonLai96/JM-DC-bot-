# -*- coding: utf-8 -*-
import sys
import io

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

import os
import shutil
import asyncio
import zipfile
import gc  # 💡 512MB 記憶體救星
import re
import threading  # 💡 新增：用於跨線程安全鎖
import discord
from discord.ext import commands
from discord import app_commands 
import jmcomic
from dotenv import load_dotenv

try:
    from PIL import Image
except ImportError:
    import subprocess
    print("[SYSTEM] 正在自動安裝 Pillow 圖片處理套件...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "Pillow"])
    from PIL import Image

from help_command import setup_help

current_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(current_dir)

env_loaded = False
for env_name in ['token.env', 'token.env.txt', '.env']:
    env_path = os.path.join(current_dir, env_name)
    if os.path.exists(env_path):
        load_dotenv(env_path)
        print(f"✓ 成功讀取環境設定檔：{env_name}")
        env_loaded = True
        break
if not env_loaded:
    load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)
setup_help(bot)

# ==============================================================================
# 💡 全局速率限制（Rate Limit）管理器
# ==============================================================================
class DownloadManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.active_albums = set()  # 紀錄目前正在下載的 album_id
        self.semaphore = asyncio.Semaphore(2)  # 🔒 限制整個機器人最多只能同時有 2 個下載任務

    def acquire_album(self, album_id):
        """嘗試鎖定本子 ID，如果已經在下載中則返回 False"""
        with self.lock:
            if album_id in self.active_albums:
                return False
            self.active_albums.add(album_id)
            return True

    def release_album(self, album_id):
        """下載完成或失敗後，釋放本子 ID"""
        with self.lock:
            if album_id in self.active_albums:
                self.active_albums.remove(album_id)

dl_manager = DownloadManager()
# ==============================================================================

# ==============================================================================
# 💡 初始化動態全局設定 (免去 option.yml 依賴)
# ==============================================================================
option_dict = {
    'version': '2.0',
    'client': {
        'domain': ['www.cdnhjk.net', 'www.cdngwc.cc', 'www.cdngwc.net', 'www.cdngwc.club'],
        'post_with_common_headers': True,
        'retry_times': 5,
        'client_config': {'meta_data': {'verify': False}}
    },
    'download': {
        'threads': 1,
        'max_workers': 1,
        'image_convert': {'format': 'jpg', 'quality': 60},
        'save_dir': '.'
    }
}
option = jmcomic.JmOption.construct(option_dict)
jmcomic.JmModuleConfig.default_option = lambda: option
# ==============================================================================

def zip_folder_smart_split(source_folder, output_base_path, max_size_mb=9.2):
    img_files = []
    for root, dirs, files in os.walk(source_folder):
        for file in files:
            if file.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
                img_files.append(os.path.join(root, file))
    
    img_files.sort()
    max_size_bytes = max_size_mb * 1024 * 1024
    zip_paths = []
    current_batch = []
    current_batch_size = 0
    part_num = 1
    
    def create_valid_zip(images, part):
        part_zip_path = f"{output_base_path}_part{part}.zip"
        with zipfile.ZipFile(part_zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for img in images:
                arcname = os.path.relpath(img, source_folder)
                zf.write(img, arcname)
        return part_zip_path

    for img in img_files:
        img_size = os.path.getsize(img)
        if current_batch_size + img_size > max_size_bytes and current_batch:
            part_path = create_valid_zip(current_batch, part_num)
            zip_paths.append(part_path)
            current_batch = [img]
            current_batch_size = img_size
            part_num += 1
        else:
            current_batch.append(img)
            current_batch_size += img_size
            
    if current_batch:
        part_path = create_valid_zip(current_batch, part_num)
        zip_paths.append(part_path)
    return zip_paths

@bot.event
async def on_ready():
    print(f'=================================')
    print(f' 機器人已成功上線！')
    print(f' 機器人名稱: {bot.user.name}')
    try:
        await bot.tree.sync()
        print(f"✓ 成功同步斜線指令！")
    except Exception as e:
        print(f"❌ 指令同步失敗: {e}")
    print(f'=================================')

@bot.tree.command(name='jmv', description='預覽本子的標題、作者、標籤等資訊')
@app_commands.describe(album_id='請輸入本子 ID 或完整網址')
async def view_comic(interaction: discord.Interaction, album_id: str):
    if hasattr(interaction.channel, 'nsfw') and not interaction.channel.nsfw:
        await interaction.response.send_message("❌ 此指令包含成人內容，請前往 **開啟年齡限制 (NSFW)** 的頻道中使用！", ephemeral=True)
        return

    await interaction.response.defer(thinking=True)
    album_id = "".join(re.findall(r'\d+', album_id.split('/')[-1]))
    if not album_id.isdigit():
        await interaction.followup.send("❌ 請輸入正確的本子 ID！")
        return

    try:
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
    except Exception as e:
        await interaction.followup.send(f"❌ 查詢失敗: {e}")

@bot.tree.command(name='jm', description='智慧單線程下載本子圖片並打包')
@app_commands.describe(album_id='請輸入本子 ID 或完整網址')
async def download_comic(interaction: discord.Interaction, album_id: str):
    if hasattr(interaction.channel, 'nsfw') and not interaction.channel.nsfw:
        await interaction.response.send_message("❌ 此指令包含成人內容，請前往 **開啟年齡限制 (NSFW)** 的頻道中使用！", ephemeral=True)
        return

    await interaction.response.defer(thinking=True)
    album_id = "".join(re.findall(r'\d+', album_id.split('/')[-1]))
    if not album_id.isdigit():
        await interaction.followup.send("❌ 請輸入正確的本子 ID！")
        return

    # 1. 🛑 檢查「同本子重複下載限制」
    if not dl_manager.acquire_album(album_id):
        await interaction.followup.send(f"⚠️ 本子 `{album_id}` 目前正在被其他使用者下載中，請勿重複提交！")
        return

    # 使用 try...finally 結構，確保不論成功、失敗或中途出錯，都能正確釋放本子鎖與 Semaphore 額度
    try:
        # 2. 🛑 檢查「全局 2 個同時下載限制」（若額度滿了會在此原地排隊，直到前面的人下載完）
        if dl_manager.semaphore.locked():
            await interaction.edit_original_response(content="⏳ 目前伺服器下載通道已滿（最高 2 個任務），您的指令已進入**排隊佇列**，請稍候...")

        async with dl_manager.semaphore:
            await interaction.edit_original_response(content=f"📥 成功獲取通道！正在智慧相容解析本子 `{album_id}` 結構...")

            # 建立一個進度共享字典，方便跨執行緒同步最新的下載進度
            progress_status = {"current": 0, "total": 0, "title": "獲取中..."}

            def safe_download_process():
                gc.collect()
                client = option.new_jm_client()
                
                # 🚨 核心防護：直接物理閹割套件內建的單圖多執行緒 Pool。這會強迫 client 乖乖地純單線程下載，不再偷開執行緒榨乾 RAM
                if hasattr(client, 'executor'):
                    client.executor = None
                
                album = client.get_album_detail(album_id)
                if not album:
                    raise ValueError("無法解析該本子，可能不存在或伺服器斷線。")
                    
                album_title = album.title
                progress_status["title"] = album_title
                invalid_chars = r'<>:"/\|?*'
                cleaned_title = "".join([c for c in album_title if c not in invalid_chars]).strip()
                
                target_folder = os.path.join(current_dir, cleaned_title)
                os.makedirs(target_folder, exist_ok=True)
                
                # 1. 終極相容相簿章節獲取
                photos = []
                if hasattr(album, 'photo_list'):
                    photos = album.photo_list
                elif hasattr(album, 'photo_dict'):
                    photos = list(album.photo_dict.values())
                elif hasattr(album, 'photo_iter') and callable(getattr(album, 'photo_iter')):
                    photos = list(album.photo_iter())
                else:
                    for attr in dir(album):
                        if 'photo' in attr.lower() and not attr.startswith('__'):
                            val = getattr(album, attr)
                            if isinstance(val, (list, tuple)):
                                photos = val
                                break
                            elif isinstance(val, dict):
                                photos = list(val.values())
                                break
                    if not photos:
                        try: photos = list(album)
                        except: pass

                if not photos:
                    raise ValueError(f"無法從 JmAlbumDetail 物件中找到章節資料，可用屬性有: {dir(album)}")

                image_details = []
                for photo in photos:
                    photo_detail = client.get_photo_detail(photo.photo_id)
                    
                    # 2. 終極相容圖片列表獲取
                    img_list = []
                    if hasattr(photo_detail, 'image_list'):
                        img_list = photo_detail.image_list
                    elif hasattr(photo_detail, 'image_dict'):
                        img_list = list(photo_detail.image_dict.values())
                    elif hasattr(photo_detail, 'image_iter') and callable(getattr(photo_detail, 'image_iter')):
                        img_list = list(photo_detail.image_iter())
                    else:
                        for attr in dir(photo_detail):
                            if 'image' in attr.lower() and not attr.startswith('__'):
                                val = getattr(photo_detail, attr)
                                if isinstance(val, (list, tuple)):
                                    img_list = val
                                    break
                                elif isinstance(val, dict):
                                    img_list = list(val.values())
                                    break
                        if not img_list:
                            try: img_list = list(photo_detail)
                            except: pass
                    
                    for image in img_list:
                        image_details.append(image)
                        
                total_pages = len(image_details)
                progress_status["total"] = total_pages
                if total_pages == 0:
                    raise ValueError("未能解析到任何圖片，該本子可能已被封鎖或受限。")

                print(f"[SYSTEM] 解析成功！總共 {total_pages} 頁。開始純單線程下載...")
                
                # 3. 🚨 物理單線程：一次只下載解密一張圖片，杜絕多線程 Killed
                for idx, img_detail in enumerate(image_details):
                    page_num = idx + 1
                    filename = f"{page_num:05d}.jpg"
                    save_path = os.path.join(target_folder, filename)
                    
                    print(f" -> [單線程] 正在處理第 {page_num}/{total_pages} 頁...")
                    
                    client.download_by_image_detail(img_detail, save_path)
                    
                    try:
                        with Image.open(save_path) as img:
                            if img.mode != 'RGB':
                                img = img.convert('RGB')
                            img.save(save_path, 'JPEG', quality=60, optimize=True)
                        del img
                    except Exception as ce:
                        print(f"   [WARN] 第 {page_num} 頁壓縮跳過: {ce}")
                        
                    # 隨時更新最新頁數，讓背景監聽任務能抓到
                    progress_status["current"] = page_num
                        
                    if page_num % 2 == 0:
                        gc.collect()  # 每 2 張圖強制釋放一次記憶體
                        
                # 4. 打包 ZIP
                zip_file_name = f"comic_{album_id}"
                zip_file_path = os.path.join(current_dir, f"{zip_file_name}.zip")
                if os.path.exists(zip_file_path): os.remove(zip_file_path)
                
                shutil.make_archive(os.path.join(current_dir, zip_file_name), 'zip', target_folder)
                return zip_file_path, target_folder

            # 💡 核心非同步回報監聽：原地 edit 刷新同一個訊息，防刷屏
            async def report_progress_task():
                last_reported = -1
                while True:
                    await asyncio.sleep(2.5)  # 每 2.5 秒刷新一次（防止觸發 Discord API 的 Rate Limit 速率限制）
                    curr = progress_status["current"]
                    tot = progress_status["total"]
                    
                    if tot > 0 and curr != last_reported:
                        last_reported = curr
                        percent = (curr / tot) * 100
                        
                        # 🛠️ 繪製精美進度條 [🟩🟩⬜⬜⬜⬜⬜⬜⬜⬜]
                        bar_length = 12
                        filled_length = int(round(bar_length * curr / tot))
                        bar = '🟩' * filled_length + '⬜' * (bar_length - filled_length)
                        
                        status_msg = (
                            f"📥 **正在下載本子中，請稍候...**\n"
                            f"📖 標題：*{progress_status['title']}*\n"
                            f"📊 進度：`[{bar}]` **{percent:.1f}%** ({curr} / {tot} 頁)"
                        )
                        try:
                            # 🎯 原地編輯原始回應，絕對不會洗版
                            await interaction.edit_original_response(content=status_msg)
                        except discord.errors.HTTPException:
                            # 💡 核心防護：如果進度更新時發現 401 憑證過期了，就靜默跳過更新，不打斷主下載程序
                            pass
                        except:
                            pass
                            
                    if tot > 0 and curr >= tot:
                        break

            # 同步啟動「安全下載線程」與「原地更新進度條任務」
            download_worker = asyncio.to_thread(safe_download_process)
            progress_reporter = asyncio.create_task(report_progress_task())
            
            zip_file_path, target_folder = await download_worker
            await progress_reporter  # 下載完成後確保進度條協程也順利關閉
            
            if os.path.exists(zip_file_path):
                file_size_mb = os.path.getsize(zip_file_path) / (1024 * 1024)
                
                # 💡 建立一個共用的狀態編輯函數，用來智慧阻斷 401 Unauthorized 錯誤
                async def safe_edit_status(msg_text):
                    try:
                        await interaction.edit_original_response(content=msg_text)
                    except discord.errors.HTTPException as he:
                        if he.status == 401:
                            # 🛡️ 憑證過期防護線：改用普通訊息直接發送到頻道中！
                            await interaction.channel.send(content=f"⚠️ (下載耗時較長，原指令已過期) {msg_text}")
                        else:
                            raise he

                if file_size_mb <= 9.5:
                    await safe_edit_status(f"🎉 本子下載並打包成功 (共 `{file_size_mb:.1f}MB`)！正在上傳...")
                    await interaction.channel.send(file=discord.File(zip_file_path))
                    if os.path.exists(zip_file_path): os.remove(zip_file_path)
                else:
                    if os.path.exists(zip_file_path): os.remove(zip_file_path)
                    await safe_edit_status(f"📦 漫畫檔案較大 (`{file_size_mb:.1f}MB`)，啟動獨立健全分包...")
                    
                    output_base_path = os.path.join(current_dir, f"comic_{album_id}")
                    part_paths = await asyncio.to_thread(zip_folder_smart_split, target_folder, output_base_path, 9.2)
                    
                    await interaction.channel.send(content=f"⚠️ 本子已被自動拆分為 **{len(part_paths)} 個健全的 ZIP 分包**，可直接閱讀！")
                    for idx, part_path in enumerate(part_paths):
                        await interaction.channel.send(content=f"📤 傳送分包 Part {idx + 1} / {len(part_paths)}", file=discord.File(part_path))
                        try: os.remove(part_path)
                        except: pass
                
                if os.path.exists(target_folder):
                    shutil.rmtree(target_folder)
            else:
                await interaction.channel.send("❌ 錯誤：找不到生成的壓縮檔。")
    except Exception as e:
        try:
            await interaction.followup.send(f"❌ 下載或處理過程中發生錯誤：\n```{str(e)}```")
        except:
            await interaction.channel.send(f"❌ 下載或處理過程中發生錯誤：\n```{str(e)}```")
    finally:
        # 3. 🔓 最終釋放解鎖：無論下載成功或出錯，都必定釋放本子 ID 限制
        dl_manager.release_album(album_id)
        gc.collect()

TOKEN = os.getenv('DISCORD_TOKEN')
if not TOKEN:
    print("❌ 錯誤：找不到 DISCORD_TOKEN！")
else:
    bot.run(TOKEN)