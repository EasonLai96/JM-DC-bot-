# -*- coding: utf-8 -*-
import os
import aiohttp


def is_nsfw_allowed(channel) -> bool:
    """判斷某個頻道是否允許成人內容（所有 cog 共用同一套判斷邏輯）。

    🛠️ 修復：舊版各處一律寫 `hasattr(channel, "nsfw") and not channel.nsfw`，
    但 discord.py 的 Thread **沒有** `nsfw` 屬性（官方 migration 文件明確說明，
    另見 discord.py issue #10248），所以在一般頻道底下開的討論串裡 hasattr 會是
    False → 整個檢查被跳過 → `/jm`、`/nh` 等成人指令可以直接在一般頻道的討論串使用。
    討論串必須改看它的 parent 頻道。

    回傳 True 代表「允許使用」，與原本 is_nsfw_channel() 的語意一致。
    """
    if channel is None:
        return False

    # 1) 有 nsfw 屬性的頻道（文字／語音／論壇／分類…）直接看它
    if hasattr(channel, "nsfw"):
        return bool(channel.nsfw)

    # 2) 沒有 nsfw → 可能是討論串（Thread）或私訊。
    #    Thread 一定有 parent 屬性（即使值為 None），私訊則完全沒有 parent。
    #    ⚠️ 這裡刻意用「有沒有 parent 屬性」而不是「parent is not None」來區分：
    #    若討論串的 parent 取不到（未快取 / 已被刪除），parent 會是 None，
    #    此時必須**保守拒絕**，否則又會變成另一個繞過漏洞。
    if hasattr(channel, "parent"):
        parent = getattr(channel, "parent", None)
        return bool(getattr(parent, "nsfw", False))

    # 3) 完全沒有 nsfw 也沒有 parent → 私訊（DM）等沒有年齡限制概念的場合，維持允許
    return True


# ==================== 🧰 批量操作用的共用工具 ====================

# 使用者可能用各種分隔符貼上一串 ID／網址（含全形逗號、頓號、全形空白）
_ID_LIST_SEPARATORS = (",", "，", ";", "；", "、", "|", "\n", "\r", "\t", "　")


def split_id_tokens(raw) -> list:
    """把使用者輸入的一串 ID／網址切成 token 清單（支援空白與各種分隔符）。

    只負責「切」，不負責「解析成 ID」——因為禁漫與 nhentai 的 ID 規則不同，
    解析與去重交由各自的呼叫端處理。
    """
    if raw is None:
        return []
    text = str(raw)
    for separator in _ID_LIST_SEPARATORS:
        text = text.replace(separator, " ")
    return [token for token in text.split() if token]


def pack_embed_fields(fields, *, max_fields: int = 25, max_name: int = 256,
                      max_value: int = 1024, max_total: int = 5500) -> list:
    """把 [(name, value), ...] 打包成「一個或多個 embed 能安全容納」的群組。

    Discord 的硬限制：單一 embed 最多 25 個 field、field name ≤256 字元、
    field value ≤1024 字元，整個 embed 的總文字量建議不超過 6000。
    批次查詢一次回傳多筆結果時很容易撞到這些限制（撞到就是整則訊息發送失敗），
    這裡統一處理截斷與分頁，回傳 [[(name, value), ...], ...]。

    純函式，不需要任何 discord 物件，方便單獨測試。
    """
    groups: list = []
    current: list = []
    used = 0
    for name, value in fields:
        name = str(name)[:max_name]
        value = str(value)
        if len(value) > max_value:
            value = value[: max_value - 1] + "…"
        cost = len(name) + len(value)
        if current and (len(current) >= max_fields or used + cost > max_total):
            groups.append(current)
            current, used = [], 0
        current.append((name, value))
        used += cost
    if current:
        groups.append(current)
    return groups


async def safe_respond(interaction, text=None, *, embed=None, ephemeral: bool = True) -> None:
    """在「還不確定 interaction 是否已回應」的情況下安全地回覆。

    先試 send_message；若已經回應過（或 response 已失效）就改用 followup。
    兩者都失敗時保持安靜——使用者體驗是「沒收到訊息」，而不是整台 bot 噴例外。
    """
    try:
        if interaction.response.is_done():
            await interaction.followup.send(text, embed=embed, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(text, embed=embed, ephemeral=ephemeral)
    except Exception:
        pass


async def upload_to_pixeldrain(file_path: str) -> str:
    """
    將指定檔案上傳至 Pixeldrain 免空，成功則返回下載 URL，失敗拋出 Exception。
    """
    url = "https://pixeldrain.com/api/file"
    file_name = os.path.basename(file_path)

    api_key = os.getenv("PIXELDRAIN_API_KEY", "")
    auth = aiohttp.BasicAuth(login="api", password=api_key) if api_key else None

    timeout = aiohttp.ClientTimeout(total=1800)  # 30分鐘逾時上限

    # 🛠️ 修復（fd 洩漏）：原本是 data.add_field('file', open(file_path,'rb'))，
    # 檔案 handle 從來沒有被明確關閉，上傳失敗或中途例外時都會留下未關閉的 fd，
    # 長時間運行會慢慢累積到 fd 耗盡。改用 with 區塊，並把整個非同步上傳包在裡面
    # （aiohttp 是等到實際送出 request 時才讀取檔案，所以不能在建立 FormData 後就關閉）。
    with open(file_path, 'rb') as file_handle:
        # 流式上傳，防止大檔案擠爆記憶體
        data = aiohttp.FormData()
        data.add_field('file', file_handle, filename=file_name)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, data=data, auth=auth) as response:
                if response.status in [200, 201]:
                    res_json = await response.json()
                    if res_json.get("success"):
                        file_id = res_json.get("id")
                        return f"https://pixeldrain.com/u/{file_id}"
                    else:
                        raise Exception(f"Pixeldrain 回傳失敗: {res_json.get('message')}")
                else:
                    text = await response.text()
                    raise Exception(f"Pixeldrain 錯誤 ({response.status}): {text}")