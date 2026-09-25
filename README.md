# 🤖 JM-DC-bot-

一個多功能的 Discord 機器人：整合**禁漫（JM）漫畫下載**、**nhentai 查詢**、**社群網址預覽修復**，並內建收藏庫、萌力經濟系統、個人檔案卡與**顯示層翻譯**。

> ⚠️ 本專案包含成人向內容（NSFW）相關功能，僅供個人／私人社群使用。

---

## ✨ 功能總覽

### 🌐 顯示層翻譯（v2 新增）
英文與日文的作品資訊會自動翻成**台灣繁體中文**，並保留原文對照：

- **標籤字典查表**：nhentai 的受控標籤（如 `nakadashi` → 中出、`ahegao` → 阿黑顏、`netorare` → NTR）
  以本地字典翻譯，**0 延遲、零 API 呼叫、術語 100% 一致**
- **書名機翻**：日文／英文書名翻成「中文（原文）」
- **括號保護**：`[社團名]`、`(C108)`、`[DL版]` 等專有名詞**原樣保留不翻譯**，避免人名被翻爛
- **機翻修正表**：修正免費機翻的常見錯誤（實測 94 個日文詞只有 32 個正確，
  例如 `おっぱい` 會被翻成「山雀」、`寝取られ` 被翻成「烏龜」）
- **絕不影響資料**：翻譯只作用在顯示層，不快取回寫、不動收藏庫
- 可用環境變數 `TRANSLATE_ENABLED=0` 一鍵完全停用

### 📥 禁漫（JM）下載系統
- **多線程安全下載**：內建防封鎖機制與速率限制
- **自動轉 PDF 並分冊**：每 500 頁自動切成一個 PDF，避免單檔過大
- **雲端託管**：上傳至 Pixeldrain 免空，回傳可直接下載的連結
- **下載快取**：同一本重複下載時直接重送上次連結，並自動驗證連結是否還活著
- **佇列管理**：`/queue` 可看即時進度、預估剩餘時間與排隊順位

### 📗 nhentai 整合
- 關鍵字搜尋、隨機推薦、詳細資訊預覽
- 下載為 PDF 並上傳雲端

### 🔗 社群網址預覽修復
Discord 無法正常顯示預覽的社群連結，會自動轉換為可正常預覽的網域：

| 平台 | 轉換為 |
|---|---|
| Twitter / X | `fxtwitter` |
| Threads | `fxthreads` |
| Instagram | `g.vxtwitter` |
| TikTok | `vxtiktok`（可直接播放） |

可用 `/settings` 逐平台開關。

### 🪐 萌力經濟與賭場
- 簽到、補簽、發電廠全服 Buff、跨伺服器排行榜、200 級階級換算
- 賭場：雙骰、命運輪盤、21 點

### 🎨 個人檔案卡
- 產生個人名片卡（可跨伺服器查詢）、自訂背景圖、職業流派稱號

### ⭐ 收藏庫
- 禁漫與 nhentai **共用同一個收藏庫**，可用 `/favorites` 分頁瀏覽

### 🔄 自動重啟保護（v2 新增）
- 每日定時重啟**前會先偵測下載狀態**，有人在下载就等到完成後才重啟
  （避免中斷使用者的下載）
- 等待期間只擋 `/restart`，其餘指令照常運作
- 開機自動清除上次被強制結束時殘留的暫存資料夾（避免 1GB 硬碟被慢慢吃光）

---

## 📋 指令一覽

<details>
<summary><b>📕 漫畫與本子</b></summary>

| 指令 | 說明 |
|---|---|
| `/jmv <ID或網址>` | 預覽禁漫本子的標題、作者、標籤 |
| `/jmbatch <多個ID>` | 批量查詢（一次最多 10 本） |
| `/jm <ID或網址>` | 下載本子並轉為 PDF 上傳雲端 |
| `/queue` | 查看下載佇列與預估等待時間 |
| `/nhv <ID或網址>` | 查詢 nhentai 作品資訊 |
| `/nh <ID或網址>` | 下載 nhentai 作品為 PDF |
| `/nhbatch <多個ID>` | 批量查詢 nhentai 作品 |
| `/nhsearch <關鍵字>` | 以關鍵字搜尋 nhentai |
| `/nhrandom` | 隨機推薦一部 nhentai 作品 |
| `/favorite` `/unfavorite` `/favorites` `/favorites_clear` | 收藏庫管理 |

</details>

<details>
<summary><b>🪐 經濟與賭場</b></summary>

| 指令 | 說明 |
|---|---|
| `/hourly` `/daily` | 領取萌力值與每日簽到 |
| `/reclaim` | 補簽，挽回斷簽天數 |
| `/power_plant` | 注入萌力值開啟全服 Buff |
| `/leaderboard` `/level_info` | 排行榜與階級換算 |
| `/dice` `/wheel` `/blackjack` | 賭場 |

</details>

<details>
<summary><b>🎨 個人檔案與其他</b></summary>

| 指令 | 說明 |
|---|---|
| `/profile [使用者]` | 查看個人檔案卡 |
| `/set_profession` `/set_background` `/reset_background` | 自訂檔案卡 |
| `/settings` | 設定要修復的社群平台（需管理權限） |
| `/feedback` | 回報 Bug 或建議 |
| `/help` | 顯示所有可用指令 |

</details>

<details>
<summary><b>🛠️ 開發者專用（僅 Owner 可見）</b></summary>

| 指令 | 說明 |
|---|---|
| `/restart [force]` | 手動重啟（`force:True` 可跳過等待下載立即重啟） |
| `/translate_test <文字>` | 測試翻譯功能（端點、延遲、字典涵蓋率） |
| `/announcement` | 向所有伺服器發送公告 |
| `/setlogchannel` | 設定 log 轉發頻道 |
| `/set_feedback_channel` | 設定回饋接收頻道 |
| `/debug_give` | 調整玩家等級與貨幣 |

</details>

---

## 🛠️ 安裝與啟動

### 前置作業
- **Python 3.9+**（開發環境使用 3.11）
- 一個 Discord Bot Token（[Discord Developer Portal](https://discord.com/developers/applications)）
- ⚠️ 需要在 Developer Portal 開啟 **Message Content Intent**（網址修復功能必需）

### 安裝步驟

```bash
# 1. 下載專案
git clone https://github.com/EasonLai96/JM-DC-bot-.git
cd JM-DC-bot-

# 2. 安裝依賴
pip install -r requirements.txt

# 3. 建立憑證檔（⚠️ 這個檔案絕對不要提交到 git）
```

建立 `token.env`：

```env
DISCORD_TOKEN=你的_DISCORD_BOT_TOKEN
PIXELDRAIN_API_KEY=你的_PIXELDRAIN_API_KEY
NHENTAI_API_KEY=你的_NHENTAI_API_KEY
```

```bash
# 4. 啟動
python main.py
```

---

## ⚙️ 環境變數

完整清單見 `token.env` 與各模組的預設值。以下是常用的：

| 變數 | 預設 | 說明 |
|---|---|---|
| `DISCORD_TOKEN` | — | **必填**，機器人 Token |
| `PIXELDRAIN_API_KEY` | — | 選填。沒有也能用，但上傳速率較低 |
| `NHENTAI_API_KEY` | — | 選填。沒有時 nhentai 查詢會受較嚴格的速率限制 |
| `TRANSLATE_ENABLED` | `1` | 設 `0` 可完全停用翻譯功能 |
| `TRANSLATE_TIMEOUT` | `3.0` | 單次機翻逾時秒數 |
| `RESTART_DOWNLOAD_GRACE_SECONDS` | `7200` | 定時重啟最多等下載完成幾秒 |
| `BOT_RESTART_STRATEGY` | `supervisor` | 重啟方式；本機無 process manager 時設 `exec` |

---

## 📁 專案結構

```
.
├── main.py                  # 進入點：載入 cogs、註冊指令樹
├── config.py                # 全域設定、下載佇列管理器（DownloadManager）
├── utils.py                 # 共用工具（Pixeldrain 上傳、NSFW 檢查…）
├── logger_config.py         # log 設定
├── bot_monitor.py           # 統一的 log 輸出格式（含 RAM 監控）
├── help_command.py          # /help 與指令分類
├── comic_cache.py           # 禁漫下載結果快取
├── cogs/
│   ├── comic.py             # /jmv /jmbatch /jm /queue（禁漫，含 PDF 分冊）
│   ├── nhentai.py           # /nhv /nh /nhbatch
│   ├── nhentai_search.py    # /nhsearch /nhrandom
│   ├── nhentai_cache.py     # nhentai 快取與下載註冊表
│   ├── translate.py         # 🌐 顯示層翻譯引擎（字典＋機翻＋快取）
│   ├── glossary_zh.py       # 🌐 中文對照表與機翻修正表
│   ├── translate_admin.py   # 🌐 /translate_test 診斷指令
│   ├── favorites.py         # /favorite 系列（JM/NH 共用收藏庫）
│   ├── favorites_store.py   # 收藏資料存取
│   ├── fixlink.py           # 社群網址預覽修復 + /settings
│   ├── economy.py           # 萌力經濟系統
│   ├── casino.py            # 賭場
│   ├── profile_card.py      # 個人檔案卡
│   ├── feedback.py          # /feedback 回饋系統
│   ├── logrelay.py          # log 即時轉發
│   ├── admin.py             # /announcement
│   └── Autorestart.py       # 定時／手動重啟（含下載保護）
├── assets/fonts/            # 檔案卡用字型（Noto Sans TC 等）
└── tools/                   # 開發與驗證工具（見下方）
```

---

## 🧪 開發工具（`tools/`）

這些工具**獨立執行，不會影響機器人**，也不需要啟動 bot：

| 工具 | 用途 |
|---|---|
| `test_translate.py` | 翻譯引擎自我測試（完整 103 項；`--offline` 可跳過連網部分，剩 77 項） |
| `test_autorestart.py` | 重啟保護邏輯測試（22 項：等待、逾時、取消、暫存清理） |
| `probe_translate.py` | 探測免費機翻端點是否可用（**換主機時建議先跑這個**） |
| `audit_corrections.py` | 稽核機翻修正表的覆蓋率，找出還沒修好的詞 |
| `preview_translation.py` | 用真實資料預覽翻譯效果（不啟動機器人） |

```bash
python tools/test_translate.py          # 翻譯引擎測試
python tools/test_autorestart.py        # 重啟保護測試
python tools/probe_translate.py         # 機翻端點探測
```

---

## 🚀 部署（Pterodactyl 面板）

1. 上傳專案檔案（**不要上傳 `token.env`、`profile_data/`、`.cache/`、`.local/`**）
2. 建立 `token.env` 並填入憑證
3. 啟動指令：`python main.py`
4. 首次啟動後，指令需要幾秒同步到 Discord（`tree.sync()`）

⚠️ **`git pull` 部署注意**：執行期資料（`comic_cache.json`、`cogs/nhentai_download_cache.json`、
`profile_data/` 等）已列在 `.gitignore`，避免 `git pull` 因「本地檔案已修改」而失敗。

---

## 🔒 安全性

本專案**不會**將任何憑證寫入程式碼，所有金鑰都從 `token.env` 讀取。以下檔案已列入 `.gitignore`，**請勿提交**：

- `token.env` / `.env`（憑證）
- `profile_data/`、`favorites_data/`（玩家資料，含 Discord ID）
- `.cache/`、`.local/`（pip 快取與虛擬環境）
- `bot.log`、`comic_cache.json`、`cogs/*_cache.json`（執行期產物）

專案內附 `.git/hooks/pre-commit` 防護腳本，會在 commit 前自動掃描憑證與玩家資料並阻擋提交。

---

## 📄 授權

本專案未指定授權條款。內含成人向內容相關功能，請自行確認符合你所在地的法律規範。
