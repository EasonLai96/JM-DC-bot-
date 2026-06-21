# 🤖 JM-DC-bot- (漫畫下載與社群預覽修復機器人)

JM-DC-bot- 是一個多功能的 Discord 機器人，專為提升群組內的多媒體體驗而設計。它不僅能自動修復各種社群平台（Twitter/X, Threads, IG, TikTok）在 Discord 中預覽失敗的網址，更整合了強大的 JM 漫畫下載與分包系統，讓使用者在 Discord 頻道內就能輕鬆預覽並下載本子。

---

## ✨ 功能特色 (Features)

### 1. 📥 JM 漫畫智慧下載系統
- **單線程安全下載**：內建防封鎖機制的智慧下載系統。
- **自動分包壓縮**：當漫畫檔案超過 Discord 上傳限制時，會自動將圖片拆分成多個小於 9.2MB 的 ZIP 壓縮檔並依序傳送。
- **資訊預覽**：提供 `/jmv` 指令，在下載前可預覽漫畫的標題、作者與標籤。

### 2. 🔗 全自動社群網址修復
Discord 經常無法正常顯示部分社群網站的圖片或影片預覽。機器人會自動攔截並轉換為支援完美預覽的連結：
- **Twitter / X** ➡️ `fxtwitter`
- **Threads** ➡️ `fxthreads`
- **Instagram** ➡️ `g.vxtwitter`
- **TikTok** ➡️ `vxtiktok`（影片可直接播放）

---

## 🛠️ 安裝與啟動 (Installation)

### 前置作業
- 安裝 **Python 3.8+**
- 準備好 Discord Bot Token

### 安裝步驟
```bash
# 下載專案
git clone https://github.com/EasonLai96/JM-DC-bot-.git
cd JM-DC-bot-

# 安裝依賴套件
pip install -r requirements.txt

# 設定 Token
echo "DISCORD_TOKEN=你的_DISCORD_BOT_TOKEN" > token.env

# 啟動機器人
python main.py
