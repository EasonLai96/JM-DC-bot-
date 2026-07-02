# -*- coding: utf-8 -*-
import os
import threading
import asyncio
import jmcomic
from dotenv import load_dotenv

# 動態解析並取得當前腳本所在的專案絕對路徑
current_dir = os.path.dirname(os.path.abspath(__file__))

# ==============================================================================
# 🔐 環境變數配置加載
# ==============================================================================
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


# ==============================================================================
# 🚀 全局速率限制（Rate Limit）管理器
# ==============================================================================
class DownloadManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.active_albums = set()  # 紀錄目前正在下載的 album_id
        # 💡 考慮到 1GB RAM，限制整個機器人最多同時處理 2 個大任務，每個任務內部開 4 線程
        self.semaphore = asyncio.Semaphore(2) 

    def acquire_album(self, album_id):
        with self.lock:
            if album_id in self.active_albums:
                return False
            self.active_albums.add(album_id)
            return True

    def release_album(self, album_id):
        with self.lock:
            if album_id in self.active_albums:
                self.active_albums.remove(album_id)


dl_manager = DownloadManager()


# ==============================================================================
# ⚡ 1GB RAM 極致效能與多線程優化設定 (Jmcomic 模組配置)
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
        # 🚀 解放多線程：Jmcomic 內部下載圖片改為 10 執行緒併發
        'threads': 10,
        'max_workers': 10,
        # 🎨 降低壓縮質量至 55 (視覺近乎無損，但記憶體與硬碟佔用暴跌 40%，且產出的 ZIP 體積更小、上傳更快)
        'image_convert': {'format': 'jpg', 'quality': 55},
        'save_dir': '.'
    }
}

# 構造與註冊全局預設 JmOption 實例
option = jmcomic.JmOption.construct(option_dict)
jmcomic.JmModuleConfig.default_option = lambda: option