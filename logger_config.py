# -*- coding: utf-8 -*-
import logging
from logging.handlers import RotatingFileHandler
import os
import asyncio
import threading


class DiscordRelayHandler(logging.Handler):
    """
    ⚡ 把 logger 收到的每一筆訊息緩衝起來，每隔固定秒數打包成一則訊息
    批量發送到指定的 Discord 頻道，避免高頻 log（下載進度、RAM 狀態等）
    直接觸發 Discord 速率限制。

    使用方式：
        from logger_config import discord_relay_handler
        discord_relay_handler.set_target(bot, channel)   # 開始轉發
        discord_relay_handler.clear_target()             # 停止轉發
    """

    FLUSH_INTERVAL = 2.5  # 秒
    MAX_CHUNK_LEN = 1900  # 留一點餘裕給 ```code block``` 包裝

    def __init__(self):
        super().__init__()
        self.bot = None
        self.channel = None
        self._buffer = []
        self._lock = threading.Lock()
        self._flush_task = None

    def emit(self, record):
        # ⚠️ emit() 可能從任何執行緒呼叫（例如 ThreadPoolExecutor 下載執行緒），
        # 這裡只做單純的記憶體操作，絕對不能在這裡做任何 I/O 或 await。
        try:
            msg = self.format(record)
        except Exception:
            return

        # 避免訊息中含有 ``` 把外層的 code block 弄壞
        # 🛠️ 優化：先判斷是否真的含有反引號才做替換，多數一般訊息不含 ```，
        # 可以省下不必要的字串掃描與重新配置記憶體。
        if '```' in msg:
            msg = msg.replace('```', '`\u200b``')

        with self._lock:
            self._buffer.append(msg)

    def set_target(self, bot, channel):
        """指定要轉發到的 bot 與頻道，並啟動背景批次發送任務（若尚未啟動）"""
        # 🛠️ 優化：原本只在「任務不存在或已結束」時才建立新任務，但如果中途換成
        # 不同的 bot 實例（例如重新登入產生新的 bot.loop），舊任務仍綁在舊的
        # event loop 上繼續跑，新舊任務並存會造成訊息被重複或錯誤地發送兩次。
        # 現在改為：偵測到 bot 物件本身換了，就先取消舊任務再重建。
        bot_changed = self.bot is not None and self.bot is not bot
        if bot_changed and self._flush_task is not None and not self._flush_task.done():
            self._flush_task.cancel()
            self._flush_task = None

        self.bot = bot
        self.channel = channel
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = bot.loop.create_task(self._flush_loop())

    def clear_target(self):
        """關閉轉發（背景任務會繼續跑但不再發送，只清空緩衝避免佔記憶體）"""
        # 🛠️ 優化：原本只清空 self.channel，self.bot 仍殘留舊引用，
        # 物件狀態不夠乾淨（雖然目前不影響行為，但容易在之後維護時造成誤判）。
        # 一併清空，明確表示「目前沒有任何轉發目標」。
        self.bot = None
        self.channel = None

    async def _flush_loop(self):
        while True:
            await asyncio.sleep(self.FLUSH_INTERVAL)

            # 🛠️ 優化：原本迴圈主體只有「發送到 channel.send 失敗」這一處有 try/except，
            # 但如果 self._chunk_lines(lines) 本身、或讀取 self.channel 屬性時意外噴出
            # 其他未預期的例外（例如 self.channel 在多執行緒情境下被清空到一半），
            # 整個 while True 迴圈會直接終止，_flush_task 變成 done()，
            # 之後即使重新呼叫 set_target() 也只是建立新任務，但這段時間的轉發
            # 完全停止且沒有任何提示。現在用外層 try/except 包住整個迴圈主體，
            # 任何例外都印出來但繼續下一輪，確保轉發功能不會無聲無息地永久停止。
            try:
                if self.channel is None:
                    with self._lock:
                        self._buffer.clear()
                    continue

                with self._lock:
                    lines = self._buffer[:]
                    self._buffer.clear()

                if not lines:
                    continue

                for chunk in self._chunk_lines(lines):
                    try:
                        await self.channel.send(f"```\n{chunk}\n```")
                        await asyncio.sleep(0.3)  # 避免連續發送多則訊息時撞到速率限制
                    except Exception as e:
                        # 故意用 print 而不是 log.error，避免送失敗又被記錄、又再轉發一輪造成迴圈
                        print(f"[LogRelay] 轉發 log 到 Discord 頻道失敗: {e}")

            except asyncio.CancelledError:
                # set_target() 偵測到 bot 換了時會主動 cancel 這個任務，
                # 這是正常的關閉流程，直接往外拋出讓任務乾淨結束，不要被下面的 except Exception 吞掉。
                raise
            except Exception as e:
                print(f"[LogRelay] 背景轉發迴圈發生未預期錯誤（已自動恢復，不影響下一輪）: {e}")

    @classmethod
    def _chunk_lines(cls, lines):
        """把多行 log 依長度切成多個不超過 MAX_CHUNK_LEN 的區塊"""
        chunks = []
        current = []
        current_len = 0

        for line in lines:
            line_len = len(line) + 1  # +1 換行符

            # 單行就超長，直接硬切
            if line_len > cls.MAX_CHUNK_LEN:
                if current:
                    chunks.append("\n".join(current))
                    current, current_len = [], 0
                for i in range(0, len(line), cls.MAX_CHUNK_LEN):
                    chunks.append(line[i:i + cls.MAX_CHUNK_LEN])
                continue

            if current_len + line_len > cls.MAX_CHUNK_LEN and current:
                chunks.append("\n".join(current))
                current, current_len = [], 0

            current.append(line)
            current_len += line_len

        if current:
            chunks.append("\n".join(current))

        return chunks


# 全域唯一的轉發 handler 實例，main.py / cogs 都透過 import 拿到同一個物件
discord_relay_handler = DiscordRelayHandler()


def setup_logger():
    # 建立日誌紀錄器
    logger = logging.getLogger('DiscordBot')

    # 🛠️ 優化：日誌等級可透過環境變數 LOG_LEVEL 調整（例如 DEBUG/WARNING/ERROR），
    # 預設仍是 INFO，不影響現有行為。方便除錯時臨時調高/調低詳細程度，不需要改程式碼。
    level_name = os.environ.get('LOG_LEVEL', 'INFO').upper()
    level = getattr(logging, level_name, logging.INFO)
    logger.setLevel(level)

    # 避免重複添加 Handler
    if logger.handlers:
        return logger

    # 設定輸出排版格式： [時間] [訊息層級] 內容
    formatter = logging.Formatter(
        fmt='[%(asctime)s] [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # 1. 輸出到控制台 (讓你開著黑視窗能即時看到)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 2. 輸出到檔案 (儲存成 bot.log，萬一關掉視窗還能查)
    # 🛠️ 優化：原本的 FileHandler 沒有任何輪替機制，bot.log 會無限制長大，
    # 跑久了可能變成幾百 MB 甚至更大。改用 RotatingFileHandler：
    # 單檔超過 10MB 就輪替，最多保留 5 份歷史備份（bot.log.1 ~ bot.log.5），
    # 總佔用空間控制在約 60MB 內，舊的自動被覆蓋，不需要手動清理。
    file_handler = RotatingFileHandler(
        'bot.log', maxBytes=10 * 1024 * 1024, backupCount=5, encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # 3. 輸出到 Discord 指定頻道（預設沒有設定頻道，不會發送任何東西）
    discord_relay_handler.setFormatter(formatter)
    logger.addHandler(discord_relay_handler)

    return logger

# 全局調用物件
log = setup_logger()