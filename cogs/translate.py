# -*- coding: utf-8 -*-
"""🌐 顯示層翻譯引擎（零新依賴、純顯示、不動任何資料）

這個模組只做三件事，全部集中在「顯示層」：

  1. **標籤查表**：nhentai 的受控標籤 → 中文（`glossary_zh.NH_TAG_ZH`，覆蓋 86%）
  2. **標題機翻**：英／日文書名 → 中文（免費端點 + 本機快取 + fallback）
  3. **原文保留**：括號內容（社團名、場次、作品名）原則上不翻

🔒 安全保證（非常重要）
----------------------
本模組**絕對不修改**任何既有資料：
  • 不快取回寫 `comic_cache.json` / `nhentai_download_cache.json`
  • 不影響 `favorites_store`（收藏庫存的仍是原始標題）
  • 不動 `progress_status` / `dl_manager` 的書名（`/queue` 顯示原文）
翻譯只在「組 embed 字串」的那一刻發生，所以：
  • 任何翻譯失敗、逾時、被限流 → 一律退回原文，功能不會壞
  • `TRANSLATE_ENABLED=0` 可一鍵完全關閉，行為與加入本模組前 100% 相同

⚠️ 顯示長度
----------
Discord 單一 field value 上限 1024 字、整個 embed 6000 字。加上「中文（原文）」
對照後長度會膨脹 2～3 倍，所以所有對外函式都接受 `budget` 參數並自我截斷，
**寧可少顯示，也絕不讓 embed 因超長而整個送失敗**。

環境變數
--------
    TRANSLATE_ENABLED      預設 1（開啟）。設 0 / false / off / no 即完全停用。
    TRANSLATE_TIMEOUT      單次機翻逾時秒數，預設 3.0（主機實測延遲 86～1883ms）
    TRANSLATE_CACHE_PATH   標題快取檔路徑，預設 cogs/translate_cache.json
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import aiohttp

try:
    from logger_config import log
except ImportError:  # 獨立測試（不走 main.py）時也要能載入
    import logging

    log = logging.getLogger(__name__)

from glossary_zh import ALL_TERMS_ZH, NH_TAG_ZH, fix_machine_translation, fix_simplified

# ──────────────────────────────────────────────────────────────
# 設定
# ──────────────────────────────────────────────────────────────
_CACHE_PATH = Path(
    os.getenv("TRANSLATE_CACHE_PATH")
    or Path(__file__).with_name("translate_cache.json")
)

# 標題快取最多保留幾筆，避免長時間運行後無限成長（1GB RAM 環境務必設上限）
_CACHE_MAX_ENTRIES = 2000
# 快取有效期（秒）：30 天。書名翻譯幾乎不會變，過期只是避免資料無限陳舊。
_CACHE_TTL_SECONDS = 30 * 24 * 3600

_TIMEOUT = float(os.getenv("TRANSLATE_TIMEOUT", "3.0"))

# 全域併發上限。1GB RAM 的主機不適合一次打十幾個外部請求，
# 而且免費端點也不喜歡突發流量（JM 那邊就為此做了 0.6 秒禮貌間隔）。
_MAX_CONCURRENT = 2
_semaphore: Optional[asyncio.Semaphore] = None

# 同一個事件迴圈共用一個 session（建立連線的成本不該每次重付）
_session: Optional[aiohttp.ClientSession] = None

# 端點順序：主機實測 `gtx` 最快（136～169ms）、`clients5` 次之但更穩。
# 兩者都實測「成人向內容零審查」，其中一個 429 時自動換另一個。
_ENDPOINTS: Sequence[Tuple[str, str]] = (
    ("gtx", "https://translate.googleapis.com/translate_a/single?client=gtx&dt=t&sl={sl}&tl={tl}&q={q}"),
    ("clients5", "https://clients5.google.com/translate_a/t?client=dict-chrome-ex&sl={sl}&tl={tl}&q={q}"),
)

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


# ──────────────────────────────────────────────────────────────
# 開關
# ──────────────────────────────────────────────────────────────
def _env_flag(name: str, default: bool = True) -> bool:
    """把環境變數讀成布林。沒設定就用 default；設定 0/false/off/no 視為關閉。"""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() not in ("0", "false", "off", "no", "disable", "disabled")


def enabled() -> bool:
    """翻譯功能是否開啟（每次呼叫都重讀環境變數，方便測試時即時切換）。"""
    return _env_flag("TRANSLATE_ENABLED", True)


# ──────────────────────────────────────────────────────────────
# 語言判斷
# ──────────────────────────────────────────────────────────────
# 日文假名（平假名 + 片假名）。有假名一定是日文，優先翻成中文。
_KANA_RE = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]")
# CJK 漢字
_HAN_RE = re.compile(r"[\u4e00-\u9fff]")
# 拉丁字母
_LATIN_RE = re.compile(r"[A-Za-z]")


def has_kana(text: str) -> bool:
    return bool(_KANA_RE.search(text or ""))


def is_probably_chinese(text: str) -> bool:
    """判斷文字是否「已經是中／日文」，不需要再翻。

    規則刻意保守：
      • 有假名 → 是日文，**要翻**
      • 沒有任何漢字 → 不是中文，要翻
      • 有漢字但沒假名、且漢字佔比高 → 視為已是中文，不翻
    這樣 `[Jun Tokutyu Kuromask]` 會被翻（但我們靠括號保護擋掉），
    `如月ちゃんの受難` 會被翻，`[ぽんぽんぺいん]` 也會被翻，
    而 `[黎欧出资汉化]` 這種已是中文的標註不會被浪費一次 API 呼叫。
    """
    text = (text or "").strip()
    if not text:
        return True
    if has_kana(text):
        return False
    han = len(_HAN_RE.findall(text))
    if han == 0:
        return False
    # 有漢字就大致算中文（漢字佔非空白字元的比例）
    visible = len(re.sub(r"\s", "", text))
    return visible > 0 and (han / visible) >= 0.5


def detect_source_lang(text: str) -> str:
    """猜來源語言：有假名 → ja，否則 en（Google 端點接受 en/ja 兩種）。"""
    return "ja" if has_kana(text) else "en"


# ──────────────────────────────────────────────────────────────
# 標題快取（JSON，附 TTL 與筆數上限）
# ──────────────────────────────────────────────────────────────
_cache_data: Optional[Dict[str, Any]] = None


def _load_cache() -> Dict[str, Any]:
    global _cache_data
    if _cache_data is not None:
        return _cache_data
    try:
        raw = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        _cache_data = raw if isinstance(raw, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        _cache_data = {}
    return _cache_data


def _save_cache() -> None:
    """原子寫入（先寫 .tmp 再 replace），避免行程被中斷時留下半個 JSON。"""
    data = _cache_data or {}
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = _CACHE_PATH.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        temporary.replace(_CACHE_PATH)
    except OSError as error:
        # 快取寫不進去不該影響任何功能，只記錄
        log.warning(f"⚠️ [翻譯] 標題快取寫入失敗（不影響功能）: {error!r}")


def _cache_get(text: str) -> Optional[str]:
    entry = _load_cache().get(text)
    if not isinstance(entry, dict):
        return None
    translated = entry.get("t")
    if not isinstance(translated, str) or not translated:
        return None
    created = entry.get("at")
    if isinstance(created, (int, float)) and (time.time() - created) > _CACHE_TTL_SECONDS:
        return None
    return translated


def _cache_put(source: str, translated: str) -> None:
    data = _load_cache()
    data[source] = {"t": translated, "at": int(time.time())}

    # 超過上限就丟掉最舊的（依時間戳排序）
    if len(data) > _CACHE_MAX_ENTRIES:
        ordered = sorted(
            data.items(),
            key=lambda kv: kv[1].get("at", 0) if isinstance(kv[1], dict) else 0,
        )
        for key, _ in ordered[: len(data) - _CACHE_MAX_ENTRIES]:
            data.pop(key, None)

    _save_cache()


def cache_stats() -> Dict[str, int]:
    """回傳快取統計（給 /translate_test 之類的診斷指令用）。"""
    return {"entries": len(_load_cache()), "path_ok": int(_CACHE_PATH.exists())}


# ──────────────────────────────────────────────────────────────
# 機翻
# ──────────────────────────────────────────────────────────────
def _parse_response(raw: str) -> str:
    """解析兩個端點的回應格式。

      gtx      → [[["翻譯","原文",...], ...], ...]
      clients5 → ["翻譯"]
    """
    data = json.loads(raw)
    if isinstance(data, list):
        if data and isinstance(data[0], list):
            parts = [
                segment[0]
                for segment in data[0]
                if isinstance(segment, list) and segment and isinstance(segment[0], str)
            ]
            return "".join(parts).strip()
        return "".join(item for item in data if isinstance(item, str)).strip()
    if isinstance(data, dict):
        return str(data.get("translatedText") or "").strip()
    return ""


def _finalize_translation(raw: str) -> str:
    """把「原始機翻輸出」變成最終顯示字串（唯一做後處理的地方）。

    兩層後處理，順序有意義：
      1. `fix_simplified`        —— 簡體修正（製服 → 制服）
      2. `fix_machine_translation` —— 術語修正（山雀 → 胸部、烏龜 → NTR、憂鬱 → 悶騷…）

    實測 94 個常見日文詞機翻只有 32 個正確，這層修正把差距補回來。

    ⚠️ **只呼叫一次、且只作用在 raw 上**。快取裡存的也是 raw，
    每次讀取時才經過這裡，所以修正表更新後立刻生效，也不會重複套用
    （重複套用會產生「才才不會輸給」這種疊字，已發生過一次）。
    """
    if not raw:
        return ""
    return fix_machine_translation(fix_simplified(raw))


async def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(
            headers={"User-Agent": _UA},
            timeout=aiohttp.ClientTimeout(total=max(_TIMEOUT, 1.0)),
        )
    return _session


async def close_session() -> None:
    """機器人關閉時釋放連線（main.py 的 on_close 會呼叫）。"""
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
    _session = None


async def _translate_raw(text: str, source_lang: Optional[str] = None) -> Tuple[str, str]:
    """呼叫機翻端點。回傳 (譯文, 使用的端點名)；失敗或無譯文時回傳 ("", ...)。

    實作上有兩個關鍵設計（都踩過坑，別改）：

    🔑 **快取只存「原始機翻輸出」，不存修正後的結果。**
       曾經把修正後的譯文寫進快取，結果讀取時又修正一次 → 重複套用，
       產生「才才不會輸給」這種疊字。現在快取存 raw，修正一律在
       `_finalize_translation()` 出口做，所以修正表改了也能立刻生效、
       不需要清快取，而且永遠只套用一次。

    🔑 **端點依序嘗試（gtx → clients5）**，任一個成功就停。
       兩者都實測「成人向內容零審查」，其中一個 429 時自動換另一個。

    ✅ 先查快取 → 再打 API
    ✅ 全部失敗 → 回傳空字串，由呼叫端決定要不要顯示原文
    ⚠️ 這裡**不會拋例外**。翻譯是附加功能，絕對不能讓指令失敗。
    """
    text = (text or "").strip()
    if not text:
        return "", ""

    cached = _cache_get(text)
    if cached is not None:
        # 🛠️ 修復（重複修正）：快取存的是**原始機翻輸出**，所以讀取時要再過一次
        # 後處理。若哪天修正表更新了，舊快取也會自動套用新規則。
        return _finalize_translation(cached), "cache"

    language = source_lang or detect_source_lang(text)
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(_MAX_CONCURRENT)

    try:
        session = await _get_session()
    except Exception as error:  # pragma: no cover - 極端情況
        log.warning(f"⚠️ [翻譯] 無法建立連線: {error!r}")
        return "", ""

    async with _semaphore:
        for endpoint_name, template in _ENDPOINTS:
            url = template.format(sl=language, tl="zh-TW", q=urllib.parse.quote(text))
            try:
                async with session.get(url) as response:
                    if response.status != 200:
                        # 429 = 這個端點對本機 IP 限流 → 直接換下一個端點
                        continue
                    body = await response.text()
            except asyncio.TimeoutError:
                continue
            except aiohttp.ClientError:
                continue
            except Exception as error:  # pragma: no cover
                log.warning(f"⚠️ [翻譯] 端點 {endpoint_name} 未預期錯誤: {error!r}")
                continue

            try:
                raw = _parse_response(body)
            except (ValueError, TypeError):
                continue

            # ⚠️ 端點有時會把原文原樣丟回來（羅馬字、已翻過的字串）→ 視為沒翻到。
            # 這裡比對的是「原始輸出」，所以要先做這個判斷、再做修正。
            if not raw or raw == text:
                continue

            _cache_put(text, raw)
            return _finalize_translation(raw), endpoint_name

    return "", ""


# ──────────────────────────────────────────────────────────────
# 對外：標籤翻譯
# ──────────────────────────────────────────────────────────────
def tag_zh(name: str) -> Optional[str]:
    """查中文譯名。查不到回傳 None（呼叫端會顯示原文，不機翻）。

    查的是 `glossary_zh.ALL_TERMS_ZH`，它是「標籤 + 作品分類 + 語言 + 泛用角色詞
    + 熱門系列作」的合併表。因為 nhentai 的藝術家／作品名／系列名分散在不同型別，
    但同一個字串不管出現在哪個欄位，中文譯名都應該一致，所以合併成一份查表。
    """
    if not name:
        return None
    key = str(name).strip().lower()
    return ALL_TERMS_ZH.get(key)


def tag_label(name: str, show_original: bool = True) -> str:
    """把單一標籤排成「中文（原文）」；查不到字典就只顯示原文。

    只有真的查到字典（＝譯文可信）才顯示對照，
    機翻的標籤不在此函式處理（避免把「摩洛伊斯蘭解放陣線」印給使用者）。
    """
    original = str(name or "").strip()
    if not original:
        return ""
    translated = tag_zh(original)
    if not translated:
        return original
    if not show_original or translated.lower() == original.lower():
        return translated
    return f"{translated}（{original}）"


async def translate_tag_line(
    names: Iterable[str], show_original: bool = True, budget: int = 900
) -> str:
    """把一串標籤轉成一行顯示字串（**不打 API**，只查字典）。

    策略：
      • 查得到字典 → 「中文（原文）」
      • 查不到     → 只顯示原文（**不機翻**，因為機翻這些術語的品質極差，
                     實測會出現「摩洛伊斯蘭解放陣線」「鞣酸」「鼻煙」這種災難）
      • 長度超過 budget → 自動截斷並補「…」，確保永遠不會撐爆 embed

    這也是為什麼這個函式不需要 await 打 API —— 它是 0ms 的純查表。
    """
    items: List[str] = []
    used = 0
    for name in names:
        text = str(name or "").strip()
        if not text:
            continue
        label = tag_label(text, show_original)
        separator = 2 if items else 0
        if used + len(label) + separator > budget:
            if items:
                items.append("…")
            break
        items.append(label)
        used += len(label) + separator
    return "，".join(items) if items else "無"


# ──────────────────────────────────────────────────────────────
# 對外：標題翻譯（含括號保護）
# ──────────────────────────────────────────────────────────────
# 括號區段：[] 【】 () （） 都是常見的社團名／場次／作品名標註
_BRACKET_RE = re.compile(r"(\[[^\]]*\]|【[^】]*】|\([^)]*\)|（[^）]*）)")


def _split_protected(title: str) -> List[Tuple[bool, str]]:
    """把標題切成 (是否受保護, 文字) 片段。

    受保護＝括號內的「專有名詞」，翻譯只作用在括號外的書名主體上。

    判斷規則（依實測結果設計，`tools/preview_translation.py` 可重現）：

      `[Jun Tokutyu Kuromask (Hetaren)]` → 端點本來就會原樣保留不翻
      `Jun Tokutyu Kuromask`（失去括號保護時）→ 被翻成「黑面具純德泰」！
      `[ぽんぽんぺいん (ぽんぽん)]` → 被翻成「[蓬蓬佩因（蓬蓬）]」❌ 社團名不該翻
      `[アクアドロップ (三上ミカ)]` → 人名被翻成「三上三」❌
      `(C108)`、`[Chinese]`、`[DL版]` → 場次／語言／版本標註，翻了只會更糟
      `(碧藍航線)`、`(Undertale)` → 作品名，中文使用者看得懂，但翻了也還能接受

    所以規則是：**括號內含拉丁字母、數字、或日文假名 → 視為專有名詞，保護**。
    這樣 `[社團名]`、`[場次]`、`[作品名原文]` 全部原樣保留，
    而括號外的書名主體照常翻譯 —— 這也是中文使用者真正需要的部分。
    """
    parts: List[Tuple[bool, str]] = []
    for segment in _BRACKET_RE.split(title):
        if not segment:
            continue
        if _BRACKET_RE.fullmatch(segment):
            inner = segment[1:-1]
            # 含拉丁字母、數字、或假名 → 專有名詞（社團名／場次／人物名），保護起來
            proper_noun = bool(re.search(r"[A-Za-z0-9\u3040-\u309f\u30a0-\u30ff]", inner))
            parts.append((proper_noun, segment))
        else:
            parts.append((False, segment))
    return parts


async def translate_title(title: str, limit: int = 240) -> Optional[str]:
    """把書名翻成中文。**失敗一律回傳 None**，由呼叫端決定要不要顯示原文。

    重要：這個函式**不回傳原文充當譯文**。呼叫端必須自己判斷：
        translated = await translate_title(title)
        display = f"{translated}（{title}）" if translated else title
    這樣才不會出現「中文（中文）」這種重複顯示。

    括號內容依 `_split_protected` 規則保留原文。
    已經是中／日文、或整體不需要翻譯時 → 回傳 None（呼叫端顯示原文即可）。
    """
    if not enabled():
        return None

    original = (title or "").strip()
    if not original:
        return None

    # 整串已經是中文（例如 JM 的簡體中文標題）：不需要翻，但做簡繁修正
    # （JM 站方標題是簡體，這裡不強制轉換，維持與站方一致，避免看起來不一樣）
    if is_probably_chinese(original):
        return None

    segments = _split_protected(original)
    if not segments:
        return None

    translated_parts: List[str] = []
    changed = False

    for protected, segment in segments:
        if protected:
            translated_parts.append(segment)
            continue

        piece = segment.strip()
        if not piece:
            # 保留空白（標題排版用）
            translated_parts.append(segment)
            continue

        # 純符號／純數字不需要翻
        if not re.search(r"[A-Za-z\u3040-\u30ff\u4e00-\u9fff]", piece):
            translated_parts.append(piece)
            continue

        if is_probably_chinese(piece):
            translated_parts.append(piece)
            continue

        result, _endpoint = await _translate_raw(piece)
        if result:
            translated_parts.append(result)
            changed = True
        else:
            translated_parts.append(piece)

    if not changed:
        return None

    combined = "".join(translated_parts)
    # 中文與括號之間補一個空格，避免「…失敗路線（[じゅらい]…）」這種擠在一起的排版
    combined = re.sub(r"([\u4e00-\u9fff])([\[\(（【])", r"\1 \2", combined)
    combined = re.sub(r"\s{2,}", " ", combined).strip()
    if not combined or combined == original:
        return None
    return combined[:limit] if len(combined) > limit else combined


async def translate_display(title: str, limit: int = 240) -> str:
    """最常用的入口：回傳「中文（原文）」或原文。

    • 翻譯成功 → `中文（原文）`
    • 翻譯失敗／不需要 → `原文`
    • 功能關閉 → `原文`

    這是顯示層唯一需要呼叫的標題函式，呼叫端不必再判斷 None。
    """
    original = (title or "").strip()
    if not original:
        return ""
    if not enabled():
        return original

    translated = await translate_title(original, limit=limit)
    if not translated:
        return original

    # 避免「中文（原文）」長到爆掉：預算不足時就只顯示中文
    combined = f"{translated}（{original}）"
    if len(combined) <= limit:
        return combined
    if len(translated) <= limit:
        return translated
    return translated[: limit - 1] + "…"


# ──────────────────────────────────────────────────────────────
# 診斷
# ──────────────────────────────────────────────────────────────
async def diagnostics(sample: str = "nakadashi") -> Dict[str, Any]:
    """跑一輪實際翻譯，回傳診斷資訊（給 /translate_test 用）。"""
    started = time.time()
    translated, endpoint = await _translate_raw(sample)
    return {
        "enabled": enabled(),
        "sample": sample,
        "translated": translated or "(無譯文)",
        "endpoint": endpoint or "(全部端點失敗)",
        "latency_ms": int((time.time() - started) * 1000),
        "source_lang": detect_source_lang(sample),
        "glossary_size": len(ALL_TERMS_ZH),
        "tag_glossary_size": len(NH_TAG_ZH),
        "cache": cache_stats(),
        "timeout_seconds": _TIMEOUT,
    }
