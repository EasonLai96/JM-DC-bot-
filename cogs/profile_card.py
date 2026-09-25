# -*- coding: utf-8 -*-
"""
個人檔案卡 Cog 模組 (包含 /profile, /set_profession, /set_background, /reset_background, /debug_give)
- 卡片稱號演算法 (get_title_info_by_level) 支援 Lv.1~200，共 9 大動態境界頭銜
- 玩家實際可升級範圍為 1~200 等，由 profile_store.SIGN_LEVEL_MATRIX 完整定義；
  debug_give 的等級調整範圍已同步對應為 1~LEVEL_CAP。
- 🛠️ 修復：卡片讀取等級/結晶欄位已統一改為 sign_level / dimension_crystal，
  解決原先讀取 level/crystal 造成升級不會反映在卡片上的問題。
- 直接渲染無損大尺寸 Emoji，解決職業膠囊內部 Emoji 太小、未置中的問題
- 四大固定職業下拉選單轉職
- 一鍵恢復預設背景指令
- 管理員專用 Debug 指令（已與 profile_store 正式 API 同步，範圍 1~200 等）
"""
import os
import io
import asyncio
import aiohttp
import discord
import random
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont
from typing import Optional

from config import current_dir
from logger_config import log
from utils import safe_respond
from . import profile_store  # 引入儲存層

# 🛡️ 防解壓縮炸彈（DoS）——這是全專案唯一「單一使用者、單次請求就能讓整個 bot 下線」的點。
#
# Pillow 預設的 MAX_IMAGE_PIXELS 是 89,478,485（= 1024*1024*1024 // 4 // 3），而且
# `_decompression_bomb_check()` 只有在超過「2 倍」（178,956,970 px）時才 raise；
# 89.4M～178.9M 之間只發一個沒人處理的 warning 就照樣解碼。
#
# 一張 13000x13000 的單色 PNG 只有約 200KB（遠低於 8MB 的「檔案大小」上限），
# 但 169M px 落在 warning 區間不會 raise → `convert("RGBA")` 會一次配置
# 169M x 4B ≈ 676MB → 1GB 的容器直接被 OOM killer 殺掉，121 個伺服器全部下線。
#
# 這裡把它收緊到本檔真正允許的上限，讓超標的圖在 `Image.open()` 讀取尺寸時就 raise。
MAX_BG_PIXELS = 40_000_000          # 約 6300x6300；單獨定義以便與下面的檢查共用同一來源
Image.MAX_IMAGE_PIXELS = MAX_BG_PIXELS

# 🛡️ 字型快取：每張名片要載入 5 個中文字型檔（每個 7~12MB）+ 1 個 emoji 字型，
# 原本每次 render 都重新載入，是純粹的浪費。用 dict 手動快取（不用 lru_cache 是因為
# 簽名含 ImageFont.truetype 的參數與 fallback 邏輯）。
_FONT_CACHE: dict = {}

# 名片實際渲染出來的尺寸（_composite_card 內是 1000x560 * SCALE 2）——存檔時直接壓到
# 這個大小，存原始尺寸對畫質沒有任何幫助，只會把磁碟吃光。
CARD_STORE_W, CARD_STORE_H = 2000, 1120

# 🆕 同時最多幾張名片在背景渲染（避免佔滿 default executor、拖垮 comic / nhentai）
_PROFILE_RENDER_SEMAPHORE = asyncio.Semaphore(4)


class _BackgroundRejected(Exception):
    """預期內的拒絕（不是有效圖片／解析度過高／存檔過大）。

    訊息設計成可以直接顯示給使用者看，呼叫端不必再包一層文案。
    """

def generate_fallback_bg() -> Image.Image:
    """動態生成極光星空原層，作為無自訂背景時的預設基底"""
    bg = Image.new('RGBA', (2000, 1120), (15, 15, 25, 255))
    draw = ImageDraw.Draw(bg)
    for y in range(1120):
        r = int(15 + (y / 1120) * 20)
        g = int(20 + (y / 1120) * 45)
        b = int(45 + (y / 1120) * 40)
        draw.line([(0, y), (2000, y)], fill=(r, g, b, 255))
    for _ in range(80):
        x = random.randint(0, 2000)
        y = random.randint(0, 1120)
        radius = random.randint(2, 6)
        draw.ellipse([x-radius, y-radius, x+radius, y+radius], fill=(0, 210, 255, random.randint(50, 150)))
    return bg


_FALLBACK_BG_CACHE: Optional[Image.Image] = None


def get_fallback_bg() -> Image.Image:
    """取得預設背景（模組層級只生成一次）。

    🛠️ 修復：原本每次 render 都呼叫 generate_fallback_bg() —— 新建 2000x1120 RGBA
    + 1120 次 draw.line + 80 個 ellipse。而 `assets/backgrounds/default_bg.png`
    目前不存在（assets/ 底下只有 fonts/），所以幾乎每一次 /profile 都會走這條路，
    讓正常的瀏覽行為變成 CPU／記憶體放大器。
    這裡只生成一次並重複使用（後續都是 .resize() 產生新物件，不會改到這份快取）。
    """
    global _FALLBACK_BG_CACHE
    if _FALLBACK_BG_CACHE is None:
        _FALLBACK_BG_CACHE = generate_fallback_bg()
    return _FALLBACK_BG_CACHE

def get_title_info_by_level(lvl: int) -> tuple:
    """🔮 核心動態稱號演算法：回傳 (Emoji符號, 境界文字稱號)"""
    if lvl <= 19:
        return ("🌱", "初誕之萌")
    elif lvl <= 39:
        return ("✨", "萌力覺醒")
    elif lvl <= 59:
        return ("🚀", "次元先鋒")
    elif lvl <= 79:
        return ("🪐", "星河主宰")
    elif lvl <= 99:
        return ("⚡", "萬物至尊")
    elif lvl == 100:
        return ("⭐", "凡塵極境")
    elif lvl <= 149:
        return ("✨", "聖域·超凡入聖")
    elif lvl <= 199:
        return ("🔥", "神域·不朽天尊")
    else:
        return ("👑", "終焉·大羅法天")

class ProfileCard(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        
        # 1. 偵測現有的主體文字字型
        noto_vf = os.path.join(current_dir, "assets", "fonts", "NotoSansTC-VariableFont_wght.ttf")
        noto_reg = os.path.join(current_dir, "assets", "fonts", "NotoSansTC-Regular.ttf")
        win_jh = "C:\\Windows\\Fonts\\msjh.ttc"
        
        if os.path.exists(noto_vf):
            self.font_main = noto_vf
        elif os.path.exists(noto_reg):
            self.font_main = noto_reg
        elif os.name == 'nt' and os.path.exists(win_jh):
            self.font_main = win_jh
        else:
            self.font_main = None

        # 2. Emoji 字型
        noto_emoji = os.path.join(current_dir, "assets", "fonts", "NotoColorEmoji.ttf")
        win_emoji = "C:\\Windows\\Fonts\\seguiemj.ttf"
        linux_emoji = "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"

        if os.path.exists(noto_emoji):
            self.font_emoji = noto_emoji
        elif os.name == 'nt' and os.path.exists(win_emoji):
            self.font_emoji = win_emoji
        elif os.path.exists(linux_emoji):
            self.font_emoji = linux_emoji
        else:
            self.font_emoji = None
            log.warning("⚠️ 找不到任何可用的 Emoji 字型檔案，貼圖將不會顯示！")
        
        self.default_bg_path = os.path.join(current_dir, "assets", "backgrounds", "default_bg.png")

    def _get_font(self, size: int):
        """安全取得字型物件（有快取，避免每次 render 都重新載入 7~12MB 的字型檔）"""
        key = ("main", self.font_main, size)
        cached = _FONT_CACHE.get(key)
        if cached is not None:
            return cached
        font = None
        if self.font_main and os.path.exists(self.font_main):
            try:
                font = ImageFont.truetype(self.font_main, size)
            except Exception:
                font = None
        if font is None:
            font = ImageFont.load_default()
        _FONT_CACHE[key] = font
        return font

    def _get_emoji_font(self):
        """Emoji 字型（一樣有快取）"""
        key = ("emoji", self.font_emoji)
        if key in _FONT_CACHE:
            return _FONT_CACHE[key]
        font = None
        try:
            em_size = 72 if os.name == 'nt' else 109
            if self.font_emoji and os.path.exists(self.font_emoji):
                font = ImageFont.truetype(self.font_emoji, em_size)
        except Exception as e:
            log.warning(f"⚠️ Emoji 字型載入失敗：{e}")
            font = None
        _FONT_CACHE[key] = font
        return font

    def _composite_card(self, avatar_bytes: bytes, username: str, user_tag: str, p_data: dict, custom_bg_path: Optional[str]) -> io.BytesIO:
        """核心 Pillow 圖像合成運算"""
        SCALE = 2  
        CARD_W, CARD_H = 1000 * SCALE, 560 * SCALE
        
        # 1. 載入背景
        if custom_bg_path and os.path.exists(custom_bg_path):
            try:
                base_img = Image.open(custom_bg_path).convert('RGBA').resize((CARD_W, CARD_H), Image.Resampling.LANCZOS)
            except Exception:
                base_img = generate_fallback_bg()
        else:
            if os.path.exists(self.default_bg_path):
                try:
                    base_img = Image.open(self.default_bg_path).convert('RGBA').resize((CARD_W, CARD_H), Image.Resampling.LANCZOS)
                except Exception:
                    base_img = get_fallback_bg()
            else:
                base_img = get_fallback_bg()

        # 2. 初始化字型大小
        f_name = self._get_font(48 * SCALE)
        f_tag = self._get_font(24 * SCALE)
        f_prof = self._get_font(18 * SCALE) 
        f_stat_lbl = self._get_font(22 * SCALE)
        f_stat_val = self._get_font(36 * SCALE)
        
        # 3. 建立彩色 Emoji 字型物件（有快取，不再每次重載 10MB 字型檔）
        f_em = self._get_emoji_font()

        # 4. 處理大頭貼與白圈保護環
        av_size = 160 * SCALE
        try:
            av_img = Image.open(io.BytesIO(avatar_bytes)).convert('RGBA').resize((av_size, av_size), Image.Resampling.LANCZOS)
        except Exception:
            av_img = Image.new('RGBA', (av_size, av_size), (100, 110, 120, 255))
        
        av_mask = Image.new('L', (av_size, av_size), 0)
        ImageDraw.Draw(av_mask).ellipse([0, 0, av_size, av_size], fill=255)
        
        av_final = Image.new('RGBA', (av_size, av_size), (0,0,0,0))
        av_final.paste(av_img, (0, 0), av_mask)
        
        av_x, av_y = 60 * SCALE, 70 * SCALE
        base_img.alpha_composite(av_final, (av_x, av_y))
        
        gd_main = ImageDraw.Draw(base_img, 'RGBA')
        gd_main.ellipse([av_x, av_y, av_x + av_size, av_y + av_size], outline=(255, 255, 255, 255), width=3 * SCALE)

        # 5. 繪製玩家名稱與 Tag
        text_start_x = av_x + av_size + 30 * SCALE
        text_start_y = av_y + 15 * SCALE
        
        for dx in range(-4, 5):
            for dy in range(-4, 5):
                gd_main.text((text_start_x + dx, text_start_y + dy), username, font=f_name, fill=(0, 0, 0, 220))
        
        gd_main.text((text_start_x, text_start_y), username, font=f_name, fill=(255, 255, 255, 255), stroke_width=2*SCALE, stroke_fill=(255,255,255,255))

        try:
            name_w = gd_main.textlength(username, font=f_name)
        except Exception:
            name_w = len(username) * 44 * SCALE
            
        tag_x = text_start_x + name_w + 12 * SCALE
        tag_y = av_y + 36 * SCALE
        
        gd_main.text((tag_x + 2, tag_y + 2), f"@{user_tag}", font=f_tag, fill=(0, 0, 0, 180))
        gd_main.text((tag_x, tag_y), f"@{user_tag}", font=f_tag, fill=(205, 215, 240, 255))

        # 6. 職業勳章外框與文字（🛠️ 終極修復：放大並完美置中 Emoji）
        # 🛠️ Bug 修復：原先讀取的 'level' 欄位與 profile_store 實際維護的 'sign_level' 不一致，
        # 導致玩家透過 /hourly /daily /level_info 升級後，卡片永遠停留在 Lv.1。改為讀取真實欄位。
        user_lvl = p_data.get('sign_level', 1)
        title_emoji, title_name = get_title_info_by_level(user_lvl)
        base_profession = p_data.get("profession", "無業遊民")
        
        pure_text = f"[{title_name}] {base_profession}"
        
        try:
            text_w = gd_main.textlength(pure_text, font=f_prof)
        except Exception:
            text_w = len(pure_text) * 18 * SCALE
            
        # 計算膠囊大小，保留足夠空間給放大後的 Emoji
        emoji_space_w = 34 * SCALE if f_em else 0
        prof_box_w = (14 * SCALE) + emoji_space_w + (6 * SCALE if f_em else 0) + text_w + (16 * SCALE)
        prof_box_h = 38 * SCALE           
        prof_x = text_start_x
        prof_y = av_y + 95 * SCALE        
        
        border_color = (255, 215, 0, 255) if user_lvl > 100 else (0, 210, 255, 255)
        fill_color = (40, 30, 0, 220) if user_lvl > 100 else (0, 40, 65, 220)
        text_color = (255, 225, 100, 255) if user_lvl > 100 else (0, 210, 255, 255)
        
        gd_main.rounded_rectangle(
            [prof_x, prof_y, prof_x + prof_box_w, prof_y + prof_box_h],
            radius=8 * SCALE, fill=fill_color, outline=border_color, width=2 * SCALE
        )
        
        content_y = prof_y + (prof_box_h // 2)
        current_draw_x = prof_x + 14 * SCALE
        
        # A. 獨立渲染最前方的稱號 Emoji（解決過小與偏心問題）
        if f_em and title_emoji:
            # 建立一個與膠囊等高的正方形畫布來承載 Emoji
            em_canvas_size = prof_box_h + 10 * SCALE
            em_img = Image.new('RGBA', (em_canvas_size, em_canvas_size), (0,0,0,0))
            em_draw = ImageDraw.Draw(em_img)
            
            # 使用 anchor="mm" 讓 Emoji 完美在專屬畫布的正中心渲染
            em_draw.text(
                (em_canvas_size // 2, em_canvas_size // 2), 
                title_emoji, 
                font=f_em, 
                embedded_color=True, 
                anchor="mm"
            )
            
            # 等比縮放成飽滿的大小 (34x34 像素等比放大)
            target_em_h = int(32 * SCALE)
            em_img = em_img.resize((target_em_h, target_em_h), Image.Resampling.LANCZOS)
            
            # 進行圖層合併，垂直 Y 軸使用中心線減去半高，實現絕對致中
            em_paste_y = int(content_y - (target_em_h // 2))
            base_img.alpha_composite(em_img, (int(current_draw_x), em_paste_y))
            
            # 推進接下來文字的起繪點
            current_draw_x += target_em_h + (2 * SCALE)

        # B. 渲染主體文字部分
        gd_main.text(
            (current_draw_x, content_y - (1 * SCALE)), # 微調 1 像素補正視覺字型中線
            pure_text, 
            font=f_prof, 
            fill=text_color, 
            stroke_width=1, 
            stroke_fill=text_color,
            anchor="lm" 
        )

        # 7. 下方磨砂玻璃數據欄與 Emoji 貼圖
        stats = [
            {
                "emoji": "🪐", 
                "label": "萌力階級", 
                "value": f"Lv.{user_lvl}"
            },
            {
                "emoji": "🪙", 
                "label": "萌力資產", 
                "value": f"{p_data.get('deposit', 100):,} MP"
            },
            {
                "emoji": "🔮", 
                "label": "時空結晶", 
                # 🛠️ Bug 修復：原先讀取的 'crystal' 欄位 profile_store 從未寫入，
                # 實際次元結晶資料存在 'dimension_crystal'（與 sha_coin 雙向同步）。
                "value": f"{p_data.get('dimension_crystal', 0):,} DC"
            }
        ]
        
        pad = 60 * SCALE
        spacing = 24 * SCALE
        stat_w = (CARD_W - (pad * 2) - (spacing * 2)) // 3
        stat_h = 112 * SCALE
        stat_top = CARD_H - 168 * SCALE

        for i, st in enumerate(stats):
            x0 = pad + i * (stat_w + spacing)
            y0 = stat_top
            x1 = x0 + stat_w
            y1 = y0 + stat_h
            
            glass_mask = Image.new('L', (x1 - x0, y1 - y0), 0)
            ImageDraw.Draw(glass_mask).rounded_rectangle([0, 0, x1 - x0, y1 - y0], radius=16 * SCALE, fill=45)
            
            glass_layer = Image.new('RGBA', (x1 - x0, y1 - y0), (15, 20, 35, 0))
            glass_layer.putalpha(glass_mask)
            base_img.alpha_composite(glass_layer, (x0, y0))
            
            gd_main.rounded_rectangle([x0, y0, x1, y1], radius=16 * SCALE, outline=(255, 255, 255, 60), width=2 * SCALE)
            
            if f_em:
                em_img = Image.new('RGBA', (140, 140), (0,0,0,0))
                ImageDraw.Draw(em_img).text((10, 10), st["emoji"], font=f_em, embedded_color=True)
                em_img = em_img.resize((36 * SCALE, 36 * SCALE), Image.Resampling.LANCZOS)
                base_img.alpha_composite(em_img, (x0 + 20 * SCALE, y0 + 18 * SCALE))
            
            text_offset_x = x0 + 66 * SCALE
            lbl_y = y0 + 20 * SCALE
            val_y = y0 + 52 * SCALE
            
            gd_main.text((text_offset_x + 1, lbl_y + 1), st["label"], font=f_stat_lbl, fill=(0, 0, 0, 180))
            gd_main.text((text_offset_x, lbl_y), st["label"], font=f_stat_lbl, fill=(215, 225, 245, 255))
            
            for dx in range(-2, 3):
                for dy in range(-2, 3):
                    gd_main.text((text_offset_x + dx, val_y + dy), st["value"], font=f_stat_val, fill=(0, 0, 0, 220))
            gd_main.text((text_offset_x, val_y), st["value"], font=f_stat_val, fill=(255, 255, 255, 255), stroke_width=1*SCALE, stroke_fill=(255,255,255,255))

        # 8. 降採樣輸出
        final_img = base_img.resize((1000, 560), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        final_img.save(output, format='PNG', optimize=True)
        output.seek(0)
        return output

    @app_commands.command(name="profile", description="查看並展示你的個人生涯檔案卡（可輸入任何人的UID跨伺服器查詢）")
    @app_commands.describe(uid="要查詢的玩家 Discord UID（純數字，留空則查詢自己）。右鍵點選使用者「複製使用者ID」即可取得")
    # 🆕 防狂刷：這張卡每次都要下載頭像 + 跑一整套 Pillow 合成（fallback 底圖、
    #    81 次文字描邊…），是這個 bot 最貴的指令之一，原本完全沒有節流。
    @app_commands.checks.cooldown(1, 10.0, key=lambda i: i.user.id)
    async def profile(self, interaction: discord.Interaction, uid: Optional[str] = None):
        await interaction.response.defer()

        # 🆕 功能擴充：原先用 discord.User 型別的「擁有者」參數，下拉選單只會顯示
        # 跟呼叫者「同在至少一個伺服器」的成員，無法查詢完全不同伺服器的人。
        # 改為純文字 UID 輸入 + bot.fetch_user()，這是全域 API 呼叫，不受伺服器邊界限制，
        # 只要知道對方的 UID（不論是否同伺服器、甚至對方有沒有跟這個 Bot 共同伺服器）都能查到。
        if uid is None:
            target_user = interaction.user
        else:
            uid_clean = uid.strip()
            if not uid_clean.isdigit():
                await interaction.followup.send(
                    "❌ UID 格式錯誤！請輸入純數字的 Discord UID"
                    "（在 Discord 設定開啟「開發者模式」後，右鍵點選使用者選擇「複製使用者ID」即可取得）。",
                    ephemeral=True
                )
                return
            try:
                target_user = self.bot.get_user(int(uid_clean)) or await self.bot.fetch_user(int(uid_clean))
            except discord.NotFound:
                await interaction.followup.send(f"❌ 找不到 UID 為 `{uid_clean}` 的 Discord 使用者，請確認輸入是否正確。", ephemeral=True)
                return
            except Exception as e:
                log.error(f"❌ 查詢UID {uid_clean} 時發生錯誤: {e}")
                await interaction.followup.send("⚠️ 查詢使用者時發生未知錯誤，請稍後再試。", ephemeral=True)
                return

        # 🛠️ 修復（垃圾記錄／資源放大）：get_profile() 在找不到資料時會「順手建立」一筆
        # 預設記錄。原本任何人只要枚舉 snowflake 就能替不存在的使用者灌入大量垃圾記錄，
        # 而 profiles.json 每次經濟操作都是整檔 load+dump → 會拖慢所有指令。
        # 這裡在「明確指定 UID」時先做唯讀檢查，不存在就直接回覆，不建立記錄。
        if uid is not None and not await profile_store.profile_exists(target_user.id):
            await interaction.followup.send(
                f"📭 `{target_user.name}` 目前還沒有任何萌力次元資料"
                f"（對方可能還沒用過 `/daily` 或 `/hourly`）。",
                ephemeral=True,
            )
            return

        p_data = await profile_store.get_profile(target_user.id)
        custom_bg = profile_store.get_custom_bg_path(target_user.id)

        avatar_url = target_user.display_avatar.with_format("png").with_size(256).url
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(avatar_url) as resp:
                    if resp.status == 200:
                        avatar_bytes = await resp.read()
                    else:
                        raise Exception()
        except Exception:
            fallback_av = Image.new('RGBA', (256, 256), (100, 110, 120, 255))
            av_io = io.BytesIO()
            fallback_av.save(av_io, format='PNG')
            avatar_bytes = av_io.getvalue()

        # 🛠️ display_name 只存在於 discord.Member（伺服器內成員，含暱稱）；
        # 透過 fetch_user() 取得的是 discord.User（全域使用者），沒有伺服器暱稱，
        # 用 getattr 安全取值，沒有 display_name 時 fallback 回 name（全域使用者名稱）。
        display_name = getattr(target_user, "display_name", None) or target_user.name
        # 🛠️ 修復（排版溢出）：名字以 96px 直接繪製且完全沒有截斷，16 字以上的中文暱稱
        # 會被畫布右緣切掉，而且因為 tag_x / 職業膠囊都是以實測寬度往右推，會整個被
        # 推出畫布外消失。
        if len(display_name) > 14:
            display_name = display_name[:13] + "…"

        loop = self.bot.loop
        try:
            # 🆕 限制同時渲染數：名片合成是純 CPU 的 Pillow 運算，而且 default executor
            #    是與 comic / nhentai 共用的。不限流的話單一使用者狂刷就能佔滿執行緒池。
            async with _PROFILE_RENDER_SEMAPHORE:
                card_stream = await loop.run_in_executor(
                    None, self._composite_card, avatar_bytes, display_name, target_user.name, p_data, custom_bg
                )
            file = discord.File(fp=card_stream, filename=f"profile_{target_user.id}.png")
            await interaction.followup.send(file=file)
        except Exception as e:
            log.error(f"❌ 個人卡渲染失敗: {e}")
            await interaction.followup.send("⚠️ 產生檔案卡時發生未知圖像錯誤！", ephemeral=True)

    @app_commands.command(name="set_profession", description="選擇你在這個數位次元的轉職流派與稱號")
    @app_commands.describe(流派="請從四大天賦流派中選擇一個職業")
    @app_commands.choices(流派=[
        app_commands.Choice(name="⏰ 時空定錨者 (科技/程式/潛水黨)", value="時空定錨者"),
        app_commands.Choice(name="💥 萌力暴走者 (劍客/戰士/冒險者)", value="萌力暴走者"),
        app_commands.Choice(name="🕵️ 結晶走私客 (商人/精明/粗心鬼)", value="結晶走私客"),
        app_commands.Choice(name="🌱 平民無業遊民 (新手/平庸之福)", value="無業遊民")
    ])
    async def set_profession(self, interaction: discord.Interaction, 流派: app_commands.Choice[str]):
        # 🛠️ 修復：update_profession 是整檔讀寫（每天第一次存檔還會 shutil.copy2 備份），
        # 原本先做這件事才回應 —— 遇到 _lock 排隊或磁碟較慢時會超過 Discord 的 3 秒
        # 首回應期限 → 使用者看到「應用程式未回應」，但職業其實已經改了（重試又再寫一次）。
        await interaction.response.defer(ephemeral=True)
        await profile_store.update_profession(interaction.user.id, 流派.value)
        
        response_msg = {
            "時空定錨者": "⏰ 轉職成功！你已與時間同步，`/hourly` 儲存上限永久增加 2 小時！",
            "萌力暴走者": "💥 轉職成功！體內萌力開始暴走，`/daily` 簽到有 10% 機率直接翻倍！",
            "結晶走私客": "🕵️ 轉職成功！暗影網路已連接，`/reclaim` 補簽所需次元結晶永遠減免 1 顆！",
            "無業遊民": "🌱 選擇了平庸之福！新手期（Lv.2 ~ Lv.10）的階級升級費用全部享有 9 折優惠！"
        }
        
        await interaction.followup.send(
            f"🎉 轉職完成！你的職業已變更為 **【{流派.value}】**！\n{response_msg.get(流派.value, '')}", 
            ephemeral=True
        )

    # 🛠️ 原本只檢查 content_type 標頭（可以被偽造），完全沒有限制檔案大小或像素尺寸。
    MAX_BG_FILE_BYTES = 8 * 1024 * 1024      # 檔案大小上限：8MB
    MAX_BG_SIDE = 4000                       # 邊長超過這個值就先等比縮小再存
    # ⚠️ MAX_BG_PIXELS 已提升到模組層級（見檔頭）：Image.MAX_IMAGE_PIXELS 必須在模組
    #    載入時就設好，Pillow 才能在 Image.open() 階段就擋掉超標的圖。

    @app_commands.command(name="set_background", description="上傳自訂圖片，更換你名片卡的背景底圖")
    @app_commands.describe(圖片檔案="請附加一張名片比例的自訂背景圖 (將自動裁切成 16:9)")
    @app_commands.checks.cooldown(1, 20.0, key=lambda i: i.user.id)   # 🆕 防連續上傳拖垮 bot
    async def set_background(self, interaction: discord.Interaction, 圖片檔案: discord.Attachment):
        if not 圖片檔案.content_type or not 圖片檔案.content_type.startswith("image/"):
            await interaction.response.send_message("❌ 請上傳正確的圖檔格式 (PNG/JPG)！", ephemeral=True)
            return

        if 圖片檔案.size > self.MAX_BG_FILE_BYTES:
            size_mb = 圖片檔案.size / (1024 * 1024)
            await interaction.response.send_message(
                f"❌ 圖片檔案太大了！你上傳的檔案為 `{size_mb:.1f}MB`，上限是 `{self.MAX_BG_FILE_BYTES // (1024*1024)}MB`，請壓縮後再試一次。",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        save_path = os.path.join(profile_store.BACKGROUND_DIR, f"{interaction.user.id}.png")

        try:
            raw_bytes = await 圖片檔案.read()
        except Exception as e:
            log.warning(f"⚠️ [名片背景] 讀取上傳檔案失敗 user={interaction.user.id}: {e!r}")
            await interaction.followup.send("❌ 讀取上傳檔案失敗，請再試一次！", ephemeral=True)
            return

        # 🛠️ 修復（DoS）：整條「解碼 → LANCZOS 縮放 → PNG 寫檔」是純 CPU 重運算，原本
        # 同步跑在事件迴圈上、也沒有 cooldown —— 單一使用者連續上傳就能阻塞整個 bot
        # （10 個併發足以讓 gateway heartbeat 逾時、所有指令停擺）。這裡搬進執行緒池，
        # 與同專案 comic.py / nhentai.py 的做法一致。
        try:
            width, height, png_bytes = await asyncio.to_thread(self._prepare_background, raw_bytes)
        except _BackgroundRejected as rejected:
            # 預期內的拒絕（不是有效圖片／解析度過高／存檔過大）→ WARNING 即可，不是崩潰
            log.warning(f"⚠️ [名片背景] 拒絕 user={interaction.user.id}: {rejected}")
            await interaction.followup.send(f"❌ {rejected}", ephemeral=True)
            return
        except Exception as e:
            log.error(f"❌ [名片背景] 處理失敗 user={interaction.user.id}: {e!r}")
            await interaction.followup.send("⚠️ 背景處理失敗，請換一張圖片再試一次！", ephemeral=True)
            return

        try:
            with open(save_path, "wb") as f:
                f.write(png_bytes)
        except Exception as e:
            log.error(f"❌ [名片背景] 寫入磁碟失敗 user={interaction.user.id}: {e!r}")
            await interaction.followup.send("⚠️ 背景檔案寫入伺服器磁碟時失敗！", ephemeral=True)
            return

        await interaction.followup.send(
            f"🎉 自訂背景名片底圖更換成功！（已壓縮為 `{width}x{height}`、"
            f"`{len(png_bytes) / 1024 / 1024:.1f}MB`）輸入 `/profile` 即可查看新外觀。",
            ephemeral=True,
        )

    def _prepare_background(self, raw_bytes: bytes):
        """在**工作執行緒**中把上傳的圖處理成要存檔的 PNG bytes（純 CPU，不碰 event loop）。

        回傳 (width, height, png_bytes)；任何「該拒絕」的情況都丟 _BackgroundRejected。
        """
        # 1) 🛡️ 先只讀「尺寸」就把解壓縮炸彈擋在 convert() 之前 —— Image.open() 是
        #    lazy 的，讀 .size 不會解碼像素；而 convert("RGBA") 才是真正一次配置
        #    數百 MB 的那一步（169M px 會配置約 676MB，足以 OOM 掉 1GB 的容器）。
        try:
            probe = Image.open(io.BytesIO(raw_bytes))
            width, height = probe.size
        except Exception as e:
            raise _BackgroundRejected("這個檔案不是有效的圖片，請換一張再試一次！") from e

        if width * height > MAX_BG_PIXELS:
            raise _BackgroundRejected(
                f"圖片解析度太高了（`{width}x{height}`，{width * height:,} 像素，"
                f"上限 {MAX_BG_PIXELS:,}），請換一張小一點的圖片！"
            )

        # 2) 完整性驗證 + 真正解碼（RGB 就夠了，名片不需要 alpha）
        try:
            probe.verify()  # verify 後物件不能再用，需重新開一次
            img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
        except Exception as e:
            raise _BackgroundRejected(
                "這個檔案不是有效的圖片，或圖片檔案已損毀，請換一張再試一次！"
            ) from e

        # 3) 邊長過大先等比縮小
        if max(width, height) > self.MAX_BG_SIDE:
            scale = self.MAX_BG_SIDE / max(width, height)
            img = img.resize(
                (max(1, int(width * scale)), max(1, int(height * scale))),
                Image.Resampling.LANCZOS,
            )

        # 4) 🛠️ 壓到名片實際需要的尺寸再存。名片渲染出來就是 2000x1120，存原始尺寸對
        #    畫質沒有任何幫助，只會吃光磁碟（實測：8MB 的 JPEG 上傳存成 PNG 會變成
        #    12MB+，目前 custom_backgrounds/ 平均 3.93MB，外推到 10% 使用者約 1.16GB）。
        if (img.width, img.height) != (CARD_STORE_W, CARD_STORE_H):
            img = img.resize((CARD_STORE_W, CARD_STORE_H), Image.Resampling.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        png_bytes = buf.getvalue()

        # 5) 存檔前再確認一次位元組大小
        if len(png_bytes) > self.MAX_BG_FILE_BYTES:
            raise _BackgroundRejected(
                f"這張圖片壓縮後仍達 `{len(png_bytes) / 1024 / 1024:.1f}MB`（上限 "
                f"{self.MAX_BG_FILE_BYTES // (1024 * 1024)}MB），請換一張簡單一點的圖片！"
            )

        return img.width, img.height, png_bytes

    @app_commands.command(name="reset_background", description="🌌 將你名片卡的自訂背景圖清除，恢復為預設星空底圖")
    async def reset_background(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        save_path = os.path.join(profile_store.BACKGROUND_DIR, f"{interaction.user.id}.png")
        
        if os.path.exists(save_path):
            try:
                os.remove(save_path)
                await interaction.followup.send("🌌 已成功移除你的自訂圖片，名片背景已恢復為預設的次元星空！", ephemeral=True)
            except Exception as e:
                log.error(f"❌ 刪除使用者自訂背景失敗: {e}")
                await interaction.followup.send("⚠️ 移除背景檔案時伺服器發生未知錯誤！", ephemeral=True)
        else:
            await interaction.followup.send("💡 你的名片目前本來就是預設背景，不需外部重設喔！", ephemeral=True)

    @app_commands.command(name="debug_give", description="🛠️ [開發者專用] 自由調整自己或目標玩家的等級與貨幣資產")
    @app_commands.describe(
        項目="請選擇要調整的測試核心項目",
        數量="要給予的數量（輸入負數代表扣除）",
        目標="要測試的目標對象（預設為自己）"
    )
    @app_commands.choices(項目=[
        app_commands.Choice(name="🪐 萌力階級 (sign_level 1~200)", value="sign_level"),
        app_commands.Choice(name="🪙 萌力資產 (MP / moe_point)", value="moe_point"),
        app_commands.Choice(name="🔮 時空結晶 (DC / dimension_crystal)", value="dimension_crystal")
    ])
    async def debug_give(self, interaction: discord.Interaction, 項目: app_commands.Choice[str], 數量: int, 目標: Optional[discord.User] = None):
        # 🛠️ 安全性修復：原先用 @app_commands.checks.has_permissions(administrator=True)，
        # 代表「任何伺服器的任何管理員」都能呼叫，包括其他人邀請這個 Bot 進自己伺服器後
        # 自行賦予的管理員身分組。這個指令可以無限調整等級/MP/DC，等同一個測試後門，
        # 一旦 Bot 進入多個伺服器就有被濫用的風險。改為與 /announcement、/setlogchannel
        # 一致的 is_owner 檢查，僅限機器人開發者（Token 綁定的唯一帳號）本人可用。
        # is_owner() 是 bot 物件的非同步方法，無法用裝飾器表達，故改在函式內部判斷。
        if not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message(
                "❌ 這個指令是機器人開發者專用的測試工具，只有 Bot Owner 本人才能使用！",
                ephemeral=True
            )
            return

        # 🛠️ 修復（數量無上限）：原本 `數量` 完全沒有範圍檢查，打錯位數（例如多打 6 個 0）
        # 會永久汙染 3025 人的經濟與排行榜，而且沒有任何還原機制。
        MAX_DEBUG_AMOUNT = 10 ** 9
        if 數量 == 0:
            await interaction.response.send_message("❌ 數量為 0 不會有任何變化。", ephemeral=True)
            return
        if abs(數量) > MAX_DEBUG_AMOUNT:
            await interaction.response.send_message(
                f"❌ 數量超出允許範圍（絕對值上限 `{MAX_DEBUG_AMOUNT:,}`），請確認是不是多打了幾個 0。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        target_user = 目標 or interaction.user

        # 🛠️ Bug 修復：原先寫入 level/deposit/crystal 三個獨立欄位，
        # 與 profile_store.py 實際維護的 sign_level/moe_point/dimension_crystal 完全脫節，
        # 導致 /debug_give 改動的數值不會反映在 /hourly /daily /level_info /profile 上（也不會反過來）。
        # 現在統一改為直接呼叫 profile_store 既有 API，確保新舊欄位（deposit/sha_coin）同步鏡像。
        key_name = 項目.value

        # 🛠️ 修復：整段原本沒有 try/except，defer 之後若拋例外（例如存檔曾靜默失敗導致
        # KeyError），使用者只會看到「應用程式未回應」，完全查不到原因。
        try:
            if key_name == "sign_level":
                # 🛠️ 修復（lost update）：原本是「鎖外用 get_profile 讀 old_val → 鎖外用
                # old_val + 數量 算 new_val → 再進鎖寫入」。owner 執行時若同時有玩家升級，
                # 玩家的變動會被這個舊值覆蓋掉，而且兩邊都看不到任何錯誤。
                # 改用原子 API，把「讀、算、寫」全包進同一次鎖；上限收斂改用 LEVEL_CAP 常數
                # （先前寫死 20，SIGN_LEVEL_MATRIX 擴充到 200 後忘了同步，加再多都卡在 Lv.20）。
                old_val, new_val = await profile_store.admin_set_level(
                    target_user.id, 數量, profile_store.LEVEL_CAP
                )
                if old_val is None:
                    await interaction.followup.send(
                        "❌ 找不到該玩家的資料，無法調整等級（不會為不存在的 UID 建立記錄）。",
                        ephemeral=True,
                    )
                    return
            elif key_name == "moe_point":
                old_val = (await profile_store.get_profile(target_user.id))["moe_point"]
                new_val = await profile_store.add_mp(target_user.id, 數量)
            else:  # dimension_crystal
                old_val = (await profile_store.get_profile(target_user.id))["dimension_crystal"]
                new_val = await profile_store.add_dc(target_user.id, 數量)
        except Exception as e:
            log.error(f"❌ [debug_give] 執行失敗 項目={key_name} 目標={target_user.id}: {e!r}")
            await interaction.followup.send("❌ 執行失敗，請查看 bot log 了解詳情。", ephemeral=True)
            return

        item_names = {
            "sign_level": "🪐 萌力階級",
            "moe_point": "🪙 萌力資產 (MP)",
            "dimension_crystal": "🔮 時空結晶 (DC)"
        }
        
        action_text = "給予" if 數量 >= 0 else "扣除"
        abs_amount = abs(數量)
        display_lvl = new_val if key_name == "sign_level" else (await profile_store.get_profile(target_user.id))["sign_level"]
        title_em, title_nm = get_title_info_by_level(display_lvl)
        
        await interaction.followup.send(
            f"🛠️ **[Debug 測試模式公權力發動]**\n"
            f"👤 測試目標：{target_user.mention}\n"
            f"⚙️ 變更項目：{item_names[key_name]}\n"
            f"📊 數值異動：`{old_val}` ➔ `{new_val}` ({action_text}了 {abs_amount:,})\n"
            f"👑 當前進化頭銜：**{title_em} 【{title_nm}】**\n"
            f"✅ 請輸入 `/profile` 檢查圖像卡片與外框是否正確渲染！",
            ephemeral=True
        )

    # 🛠️ 移除已失效的 @debug_give.error 處理器：原本攔截 MissingPermissions 例外，
    # 但權限檢查已改為函式內部的 is_owner 判斷（不通過時直接回覆並 return），
    # 不會再經過裝飾器層級丟出 MissingPermissions，這個錯誤處理器已是死碼。

    # ==========================================
    # ⚠️ 統一錯誤處理
    # ==========================================
    async def cog_app_command_error(self, interaction: discord.Interaction,
                                    error: app_commands.AppCommandError):
        """🆕 /profile 與 /set_background 這輪加上了 cooldown，必須有這個 handler。

        沒有它的話，冷卻觸發時例外會往上丟到 main.py 的全域 handler，
        使用者會看到誤導的「未知的內部錯誤」而不是「請等 N 秒」。
        """
        if isinstance(error, app_commands.CommandOnCooldown):
            await safe_respond(
                interaction,
                f"⏳ 這個指令比較耗資源，請於 **{error.retry_after:.1f}** 秒後再試一次。",
            )
            return
        log.error(f"❌ [個人檔案] 未預期的指令錯誤：{error!r}")
        await safe_respond(interaction, "❌ 指令執行時發生未預期錯誤，請稍後再試。")

async def setup(bot: commands.Bot):
    await bot.add_cog(ProfileCard(bot))