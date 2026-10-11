# JM-DC-bot-

多功能 Discord 機器人：禁漫（JM）漫畫下載、nhentai 查詢、社群網址預覽修復，
外加收藏庫、萌力經濟、賭場、個人檔案卡與顯示層翻譯。

> 含成人向（NSFW）功能，僅供個人／私人社群使用。

目前服務 166 個伺服器、3,569 位使用者，跑在約 1 GB RAM 的容器上。

---

## 功能

| 功能 | 內容 |
|---|---|
| **禁漫下載** | 逐批管線（下載 500 頁 → 合成 PDF → 上傳 Pixeldrain → 發連結），自動分冊、下載快取、佇列管理 |
| **nhentai** | 搜尋、隨機推薦、資訊預覽、下載為 PDF |
| **顯示層翻譯** | 標籤走本地字典（零延遲）、書名機翻、括號保護、機翻修正表。只作用在顯示層，不動資料 |
| **網址預覽修復** | Twitter/X、Threads、Instagram、TikTok 自動轉為可預覽網域，可用 `/settings` 逐平台開關 |
| **萌力經濟** | 簽到、補簽、發電廠全服 Buff、跨伺服器排行榜、200 級階級換算 |
| **賭場** | 雙骰、命運輪盤、21 點 |
| **個人檔案卡** | 可跨伺服器查詢的名片卡，自訂背景與職業稱號 |
| **收藏庫** | 禁漫與 nhentai 共用同一份收藏庫 |
| **重啟保護** | 定時重啟前偵測下載狀態，有人在下載就等到完成；開機自動清理殘留暫存 |
| **Log 轉發** | 批次轉發到指定頻道，未設定頻道時完全不緩衝 |

---

## 指令

<details><summary><b>漫畫與本子</b></summary>

| 指令 | 說明 |
|---|---|
| `/jmv` `/jmbatch` | 預覽禁漫本子（批量最多 10 本） |
| `/jm` | 下載禁漫本子並轉 PDF 上傳雲端 |
| `/queue` | 下載佇列與預估等待時間 |
| `/nhv` `/nhbatch` `/nhsearch` `/nhrandom` | nhentai 查詢與搜尋 |
| `/nh` | 下載 nhentai 作品為 PDF |
| `/favorite` `/unfavorite` `/favorites` `/favorites_clear` | 收藏庫 |

</details>

<details><summary><b>經濟與賭場</b></summary>

| 指令 | 說明 |
|---|---|
| `/hourly` `/daily` `/reclaim` | 領取萌力值、簽到、補簽 |
| `/power_plant` | 注入萌力值開啟全服 Buff |
| `/leaderboard` `/level_info` | 排行榜與階級換算 |
| `/dice` `/wheel` `/blackjack` | 賭場 |

</details>

<details><summary><b>個人檔案與其他</b></summary>

| 指令 | 說明 |
|---|---|
| `/profile` `/set_profession` `/set_background` `/reset_background` | 個人檔案卡 |
| `/settings` | 設定要修復的社群平台（需管理權限） |
| `/feedback` `/help` | 回饋與指令列表 |

</details>

<details><summary><b>開發者專用（僅 Owner）</b></summary>

| 指令 | 說明 |
|---|---|
| `/restart [force]` | 手動重啟（`force:True` 跳過等待下載） |
| `/translate_test` | 翻譯功能診斷 |
| `/announcement` | 向所有伺服器發公告 |
| `/setlogchannel` `/set_feedback_channel` | 設定轉發頻道 |
| `/debug_give` | 調整玩家等級與貨幣 |

</details>

---

## 安裝

需要 **Python 3.9+**、一個 Discord Bot Token，並在 Developer Portal 開啟
**Message Content Intent**（網址修復功能必需）。

```bash
git clone https://github.com/EasonLai96/JM-DC-bot-.git
cd JM-DC-bot-
pip install -r requirements.txt
```

建立 `token.env`（**絕對不要提交**）：

```env
DISCORD_TOKEN=你的_DISCORD_BOT_TOKEN
PIXELDRAIN_API_KEY=你的_PIXELDRAIN_API_KEY
NHENTAI_API_KEY=你的_NHENTAI_API_KEY
```

```bash
python tools/precheck.py    # 先確認相依都裝好了
python main.py
```

常用環境變數：

| 變數 | 預設 | 說明 |
|---|---|---|
| `TRANSLATE_ENABLED` | `1` | 設 `0` 停用翻譯 |
| `RESTART_DOWNLOAD_GRACE_SECONDS` | `7200` | 定時重啟最多等下載完成幾秒 |
| `BOT_RESTART_STRATEGY` | `supervisor` | 本機無 process manager 時設 `exec` |

---

## 專案結構

```
main.py              進入點：載入 cogs、註冊指令樹
config.py            全域設定、下載佇列管理器
utils.py             共用工具（Pixeldrain 上傳、NSFW 檢查…）
logger_config.py     log 設定 + Discord 轉發 handler
bot_monitor.py       統一的 log 格式（含 RAM 監控）
comic_cache.py       禁漫下載結果快取
cogs/
  comic.py           禁漫下載管線（含逐批處理）
  nhentai*.py        nhentai 查詢、搜尋、快取
  translate.py       顯示層翻譯引擎
  glossary_zh.py     中文對照表與機翻修正表
  fixlink.py         社群網址預覽修復
  economy.py casino.py profile_card.py profile_store.py
  favorites.py favorites_store.py
  logrelay.py admin.py Autorestart.py
assets/fonts/        檔案卡用字型
tools/               開發與驗證工具
```

---

## 開發工具

全部獨立執行，不需要啟動 bot。

| 工具 | 用途 |
|---|---|
| `precheck.py` | 啟動前檢查關鍵 import（**改完程式碼、重啟前先跑**） |
| `test_translate.py` | 翻譯引擎測試（103 項；`--offline` 跳過連網，剩 77 項） |
| `test_autorestart.py` | 重啟保護邏輯測試（22 項） |
| `test_jm_search.py` | 禁漫標題搜尋測試 |
| `probe_translate.py` | 探測機翻端點（**換主機時先跑這個**） |
| `audit_corrections.py` | 稽核機翻修正表覆蓋率 |
| `preview_translation.py` | 用真實資料預覽翻譯效果 |

---

## 部署（Pterodactyl）

1. 上傳專案檔案，**不要上傳** `token.env`、`profile_data/`、`.cache/`、`.local/`
2. 建立 `token.env` 填入憑證
3. 啟動指令 `python main.py`（首次啟動後指令需幾秒同步）

用 `git pull` 部署時，執行期資料已列在 `.gitignore`，不會因「本地檔案已修改」而失敗。

---

## 設計筆記

改動前請先讀過這幾條。

**1. 下載必須逐批，不能整本做完**

Pterodactyl 量的是**整個容器的 cgroup 記憶體，不是 Python 行程的 RSS**。
舊版把整本（實測 8131 頁）抓完才開始做 PDF，來源圖的 page cache 一路累積到
995 MB / 上限 1024 MB 被 OOM 砍掉 —— 而 `bot.log` 一直顯示「機器人: 228MB」很安全。
症狀是「下載永遠下不完」，三次都斷在 5490～5640 頁。改成逐批後峰值降到 391 MB。

**2. `finally` 不能假設變數已綁定**

`output_pdf_paths, target_folder = await download_worker` 若丟例外，`finally` 裡的
`for p in output_pdf_paths` 會 `NameError`，把真正的錯誤蓋掉。所有在 `finally`
用到的變數都要在 `try` 之前初始化。

**3. 重啟保護的誤判代價不對稱**

偵測不到下載狀態時一律當成「有下載」。誤判成「沒下載」會砍掉使用者的下載，
代價遠高於多等一輪。

**4. log 轉發沒有目標時必須直接丟棄**

`DiscordRelayHandler` 無條件掛在 logger 上，但清空緩衝區的迴圈只在 `set_target()`
時建立。沒設定頻道時若不直接 `return`，每筆 log 都會永久堆在記憶體裡。

**5. 執行期資料不能進 repo**

`comic_cache.json`、`profile_data/` 這類檔案會被 bot 持續改寫。被 git 追蹤會導致
`git pull` 失敗，以及有人 `git add .` 時把玩家資料推上公開 repo。

---

## 疑難排解

| 症狀 | 先查 |
|---|---|
| bot 不講話、log 沒線索 | `token.env` 憑證；API 錯誤會把狀態碼寫進 log |
| 下載中斷 | `/queue` 看進度。記憶體接近上限會被 OOM 砍，重啟後暫存自動清理，需重新發起（無續傳） |
| 連結預覽沒修好 | `/settings` 確認平台已開啟；Instagram/TikTok 的修復網域會失效 |
| 翻譯品質怪 | 跑 `tools/audit_corrections.py` 看哪些詞還沒進修正表 |
| 換主機後翻譯掛掉 | 跑 `tools/probe_translate.py`；免費機翻端點常擋資料中心 IP |

---

## 安全性

憑證全部從 `token.env` 讀取，不寫入程式碼。以下已列入 `.gitignore`：

- `token.env` / `.env`
- `profile_data/`、`favorites_data/`（含 Discord ID）
- `.cache/`、`.local/`
- `bot.log`、`comic_cache.json`、`cogs/*_cache.json`

> 這個 repo 是**公開的**。git hooks 不會跟著 clone 走，不能依賴它把關。

---

## 授權

未指定授權條款。內含成人向內容相關功能，請自行確認符合你所在地的法律規範。
