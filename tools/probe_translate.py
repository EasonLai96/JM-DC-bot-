# -*- coding: utf-8 -*-
"""🔬 免費機翻端點探測工具（獨立執行，不影響機器人）

用途
----
在決定「要不要替機器人加翻譯」之前，先確認這台機器所處的網路環境
到底能不能連上免費機翻端點、會不會被限流、成人向內容會不會被審查擋掉。

這支腳本「只讀不寫」機器人的任何資料檔，純粹發 HTTP 請求做測試。
唯一會寫入的檔案是它自己的報告（預設 tools/probe_report.json）。

用法
----
    python tools/probe_translate.py                # 完整探測（約 30 秒）
    python tools/probe_translate.py --quick        # 快速版（約 5 秒）
    python tools/probe_translate.py --text "..."   # 自訂要翻的字串
    python tools/probe_translate.py --rapid 20     # 連續請求壓力測試次數

判讀重點
--------
1. 端點 http=200 且 translated 非空 → 可用
2. http=429 → 被限流（換 IP 或稍後再試）
3. translated == source → 端點「跳過不翻」（羅馬字／純符號常見），不是錯誤
4. adult_* 案例若 translated 非空 → 沒有內容審查問題

⚠️ 這支腳本沒有安裝任何新套件，只用標準庫 urllib（以及有的話用 requests）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

# Windows 主控台常常不是 UTF-8，中文會變亂碼或直接丟 UnicodeEncodeError。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPORT_PATH = os.path.join(_HERE, "probe_report.json")

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

# ──────────────────────────────────────────────────────────────
# 端點定義
# ──────────────────────────────────────────────────────────────
# 每個端點提供 build_url() 與 parse()，方便日後新增/替換。
# 統一介面：translate(session_like, text, source_lang, target_lang) -> (http_status, text)


def _url_google_clients5(text: str, sl: str, tl: str) -> str:
    return (
        "https://clients5.google.com/translate_a/t"
        "?client=dict-chrome-ex&sl={sl}&tl={tl}&q={q}".format(
            sl=sl, tl=tl, q=urllib.parse.quote(text)
        )
    )


def _url_google_gtx(text: str, sl: str, tl: str) -> str:
    return (
        "https://translate.googleapis.com/translate_a/single"
        "?client=gtx&dt=t&sl={sl}&tl={tl}&q={q}".format(
            sl=sl, tl=tl, q=urllib.parse.quote(text)
        )
    )


def _url_mymemory(text: str, sl: str, tl: str) -> str:
    return "https://api.mymemory.translated.net/get?q={q}&langpair={sl}|{tl}".format(
        q=urllib.parse.quote(text), sl=sl, tl=tl
    )


def _url_libretranslate(text: str, sl: str, tl: str) -> str:
    return "https://translate.terraprint.co/translate"


def _parse_google_simple(raw: str) -> str:
    """clients5 / gtx 這種回傳 JSON 陣列的形式，取出翻譯文字。"""
    data = json.loads(raw)
    if isinstance(data, list):
        # clients5 → ["翻譯"]；gtx → [[["翻譯","原文",...]],...]
        if data and isinstance(data[0], list):
            parts = [seg[0] for seg in data[0] if isinstance(seg, list) and seg]
            return "".join(p for p in parts if isinstance(p, str))
        return "".join(str(x) for x in data if isinstance(x, str))
    if isinstance(data, dict):
        return str(data.get("translatedText") or data.get("translation") or "")
    return ""


def _parse_mymemory(raw: str) -> str:
    data = json.loads(raw)
    return str((data.get("responseData") or {}).get("translatedText") or "")


def _parse_libretranslate(raw: str) -> str:
    data = json.loads(raw)
    return str(data.get("translatedText") or "")


# name → (build_url, parse, needs_post)
ENDPOINTS: Dict[str, Tuple[Any, Any, bool]] = {
    "google_clients5": (_url_google_clients5, _parse_google_simple, False),
    "google_gtx": (_url_google_gtx, _parse_google_simple, False),
    "mymemory": (_url_mymemory, _parse_mymemory, False),
    "libretranslate_terraprint": (_url_libretranslate, _parse_libretranslate, True),
}

# 只保留最值得測的幾個（避免一次打太多、也避免拖時間）
DEFAULT_ENDPOINTS = ["google_clients5", "google_gtx", "mymemory"]

# ──────────────────────────────────────────────────────────────
# 測試案例
# ──────────────────────────────────────────────────────────────
# (案例名稱, 原文, 來源語言, 目標語言)
FULL_CASES: List[Tuple[str, str, str, str]] = [
    # ── 基本連通性 ──
    ("neutral_single", "stockings", "en", "zh-TW"),
    # ── nhentai 標籤：成人向，用來測審查 ──
    ("adult_nakadashi", "nakadashi", "en", "zh-TW"),
    ("adult_femdom", "femdom", "en", "zh-TW"),
    ("adult_netorare", "netorare", "en", "zh-TW"),
    ("adult_lolicon", "lolicon", "en", "zh-TW"),
    ("adult_incest", "incest", "en", "zh-TW"),
    ("adult_ahegao", "ahegao", "en", "zh-TW"),
    ("adult_yuri", "yuri", "en", "zh-TW"),
    # ── 常見標籤（受控詞彙，字典表的主要內容）──
    ("tag_solefemale", "sole female", "en", "zh-TW"),
    ("tag_bigbreasts", "big breasts", "en", "zh-TW"),
    ("tag_schoolgirl", "schoolgirl uniform", "en", "zh-TW"),
    # ── 日文書名 ──
    ("ja_title", "如月ちゃんの受難", "ja", "zh-TW"),
    ("ja_title_2", "ぽんぽんぺいん", "ja", "zh-TW"),
    # ── 英文書名（JM 少數英文本）──
    ("en_title", "High School Legend : Red Dragon", "en", "zh-TW"),
    # ── 社團名（應該被跳過不翻，這是「期望行為」）──
    ("circle_name", "Jun Tokutyu Kuromask", "en", "zh-TW"),
    # ── 多標籤換行合併（字典表建好前的過渡方案）──
    (
        "multi_tags_newline",
        "sole female\nnakadashi\nstockings\nfemdom\nnetorare\nahegao\nbig breasts\nsole male",
        "en",
        "zh-TW",
    ),
]

QUICK_CASES: List[Tuple[str, str, str, str]] = [
    ("neutral_single", "stockings", "en", "zh-TW"),
    ("adult_nakadashi", "nakadashi", "en", "zh-TW"),
    ("ja_title", "如月ちゃんの受難", "ja", "zh-TW"),
]


# ──────────────────────────────────────────────────────────────
# HTTP 層
# ──────────────────────────────────────────────────────────────
def _http_get(url: str, timeout: float) -> Tuple[Optional[int], str, Optional[str]]:
    """回傳 (status, body, error_message)。status 為 None 代表連線層失敗。"""
    request = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            return response.status, body, None
    except urllib.error.HTTPError as exc:
        # 429 / 403 這類「有回應但被拒」也要把 body 讀出來，方便判讀
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return exc.code, body, None
    except Exception as exc:  # URLError, timeout, ssl, DNS…
        return None, "", "{0}: {1}".format(type(exc).__name__, exc)


def probe_one(
    endpoint: str, text: str, source_lang: str, target_lang: str, timeout: float = 20.0
) -> Dict[str, Any]:
    """對單一端點、單一字串做一次翻譯，並記錄狀態／延遲／結果。"""
    build_url, parse, needs_post = ENDPOINTS[endpoint]
    started = time.time()

    if needs_post:
        # LibreTranslate 是 POST JSON，這裡獨立處理
        payload = json.dumps(
            {"q": text, "source": source_lang.split("-")[0], "target": target_lang.split("-")[0], "format": "text"}
        ).encode("utf-8")
        request = urllib.request.Request(
            build_url(text, source_lang, target_lang),
            data=payload,
            headers={"User-Agent": _UA, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status, body, error = response.status, response.read().decode("utf-8", "replace"), None
        except urllib.error.HTTPError as exc:
            status, body, error = exc.code, "", None
        except Exception as exc:
            status, body, error = None, "", "{0}: {1}".format(type(exc).__name__, exc)
    else:
        status, body, error = _http_get(build_url(text, source_lang, target_lang), timeout)

    latency_ms = int((time.time() - started) * 1000)
    result: Dict[str, Any] = {
        "endpoint": endpoint,
        "source": text,
        "src_lang": source_lang,
        "tgt_lang": target_lang,
        "http_status": status,
        "latency_ms": latency_ms,
        "error": error,
        "translated": "",
        "skipped": False,
        "parse_error": None,
    }

    if error or not body:
        return result

    try:
        translated = parse(body)
    except Exception as exc:
        result["parse_error"] = "{0}: {1}".format(type(exc).__name__, exc)
        return result

    translated = (translated or "").strip()
    result["translated"] = translated
    # 「翻完等於原文」代表端點直接跳過（羅馬字、純英文名詞常見）→ 不是錯誤
    result["skipped"] = translated == text.strip()
    return result


def _is_blocked(row: Dict[str, Any]) -> bool:
    """判斷這一筆是不是「端點拒翻成人內容」（而不是單純連線失敗或跳過）。

    判定條件：HTTP 200 但譯文為空，或譯文與原文完全相同（跳過不翻）。
    連線失敗／429 屬於網路與限流問題，不算審查阻擋。
    """
    if row.get("error") or row.get("http_status") != 200:
        return False
    return row.get("skipped") or not row.get("translated")


# ──────────────────────────────────────────────────────────────
# 報表
# ──────────────────────────────────────────────────────────────
def _short(text: str, limit: int = 34) -> str:
    text = (text or "").replace("\n", "\\n")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _verdict(rows: List[Dict[str, Any]], endpoint: str) -> str:
    mine = [r for r in rows if r["endpoint"] == endpoint]
    if not mine:
        return "no-data"
    ok = [r for r in mine if r["http_status"] == 200 and r["translated"] and not r["skipped"]]
    limited = [r for r in mine if r["http_status"] == 429]
    conn = [r for r in mine if r["http_status"] is None]

    if len(conn) == len(mine):
        return "❌ 無法連線"
    if len(limited) > len(mine) / 2:
        return "⚠️ 被限流 (429)"
    if not ok:
        return "⚠️ 可連線但無有效譯文"
    return "✅ 可用 ({0}/{1} 成功)".format(len(ok), len(mine))


def run(args: argparse.Namespace) -> int:
    endpoints = args.endpoint or DEFAULT_ENDPOINTS
    unknown = [e for e in endpoints if e not in ENDPOINTS]
    if unknown:
        print("❌ 未知端點：{0}".format(", ".join(unknown)))
        print("   可用端點：{0}".format(", ".join(ENDPOINTS)))
        return 2

    if args.text:
        cases = [("custom", t, args.source_lang, args.target_lang) for t in args.text]
    else:
        cases = QUICK_CASES if args.quick else FULL_CASES

    print("=" * 78)
    print("🔬 免費機翻端點探測 — {0} 個端點 × {1} 個案例".format(len(endpoints), len(cases)))
    print("=" * 78)
    print()

    rows: List[Dict[str, Any]] = []
    for endpoint in endpoints:
        print("── {0} ──".format(endpoint))
        for name, text, sl, tl in cases:
            row = probe_one(endpoint, text, sl, tl, timeout=args.timeout)
            row["case"] = name
            rows.append(row)

            if row["error"]:
                status_text = "CONN-ERR"
            elif row["http_status"] != 200:
                status_text = "HTTP {0}".format(row["http_status"])
            elif row["parse_error"]:
                status_text = "PARSE-ERR"
            elif row["skipped"]:
                status_text = "skipped"
            else:
                status_text = "ok"

            print(
                "  {0:<20} {1:<9} {2:>6}ms  {3:<28} → {4}".format(
                    name,
                    status_text,
                    row["latency_ms"],
                    _short(text),
                    _short(row["translated"], 40) or "(空)",
                )
            )
            if row["error"]:
                print("      ⚠️ {0}".format(row["error"]))
            if row["parse_error"]:
                # HTTP 非 200 時 body 通常是 HTML 錯誤頁，解析失敗是必然結果，
                # 印出來只會洗版；只回報「HTTP 200 卻解析失敗」這種真正異常的情況。
                if row["http_status"] == 200:
                    print("      ⚠️ parse: {0}".format(row["parse_error"]))
                else:
                    print("      ℹ️ 非 200 回應（body 非 JSON，已略過解析）")
            time.sleep(args.delay)
        print()

    # ── 壓力測試：確認會不會被限流 ──
    rapid_results: List[Dict[str, Any]] = []
    if args.rapid > 0:
        target = endpoints[0]
        print("── 連續請求壓力測試：{0} × {1} ──".format(target, args.rapid))
        latencies: List[int] = []
        ok = empty = failed = 0
        for index in range(args.rapid):
            row = probe_one(target, "sole female", "en", "zh-TW", timeout=args.timeout)
            rapid_results.append(row)
            latencies.append(row["latency_ms"])
            if row["error"] or row["http_status"] != 200:
                failed += 1
                print("  #{0:<3} 失敗：{1} / HTTP {2}".format(index, row["error"], row["http_status"]))
            elif not row["translated"]:
                empty += 1
                print("  #{0:<3} 空回應".format(index))
            else:
                ok += 1

        if latencies:
            print(
                "  結果：成功 {0}　空回應 {1}　失敗 {2}　｜　延遲 min/avg/max = {3}/{4}/{5} ms".format(
                    ok,
                    empty,
                    failed,
                    min(latencies),
                    sum(latencies) // len(latencies),
                    max(latencies),
                )
            )
            if failed or empty:
                print("  ⚠️ 有失敗或空回應 → 上線時務必做快取 + fallback，不能假設每次都成功。")
            else:
                print("  ✅ 連續 {0} 次全數成功，短時間內未觸發限流。".format(args.rapid))
        print()

    # ── 總結 ──
    print("=" * 78)
    print("📊 總結")
    print("=" * 78)
    for endpoint in endpoints:
        print("  {0:<28} {1}".format(endpoint, _verdict(rows, endpoint)))

    adult_cases = [r for r in rows if r["case"].startswith("adult_")]
    adult_ok = [r for r in adult_cases if r["translated"] and not r["skipped"] and r["http_status"] == 200]
    adult_blocked = [r for r in adult_cases if _is_blocked(r)]
    if adult_cases:
        print()
        print(
            "  🔞 成人向內容審查測試：{0}/{1} 成功取得譯文".format(len(adult_ok), len(adult_cases))
        )
        if not adult_blocked:
            print("     ✅ 沒有觀察到內容審查阻擋，標題翻譯方案可行。")
        else:
            # 逐端點列出被擋的案例，才知道該不該換端點
            by_endpoint: Dict[str, List[str]] = {}
            for row in adult_blocked:
                by_endpoint.setdefault(row["endpoint"], []).append(row["case"])
            for endpoint, names in by_endpoint.items():
                print("     ⚠️ {0}：被跳過/無譯文 {1} 筆（{2}）".format(
                    endpoint, len(names), ", ".join(names)
                ))
            print("     → 這些案例必須有 fallback 顯示原文；或是改用本地標籤字典。")

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "endpoints": endpoints,
        "cases": rows,
        "rapid_test": rapid_results,
        "verdicts": {e: _verdict(rows, e) for e in endpoints},
    }
    try:
        with open(_REPORT_PATH, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        print()
        print("📄 完整報告已寫入：{0}".format(_REPORT_PATH))
    except OSError as exc:
        print("⚠️ 報告寫入失敗：{0}".format(exc))

    # 有任何一個端點可用就回 0，方便在 shell 裡判斷
    return 0 if any(_verdict(rows, e).startswith("✅") for e in endpoints) else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="探測免費機翻端點是否可用（不影響機器人、不修改任何現有資料）"
    )
    parser.add_argument("--quick", action="store_true", help="只跑 3 個案例，約 5 秒")
    parser.add_argument("--text", action="append", default=[], help="自訂要翻譯的字串（可重複指定）")
    parser.add_argument("--source-lang", default="en", help="自訂字串的來源語言（預設 en）")
    parser.add_argument("--target-lang", default="zh-TW", help="目標語言（預設 zh-TW）")
    parser.add_argument("--endpoint", action="append", default=[], help="只測指定端點（可重複指定）")
    parser.add_argument("--rapid", type=int, default=12, help="連續請求次數，0 代表跳過（預設 12）")
    parser.add_argument("--delay", type=float, default=0.7, help="每個案例之間的間隔秒數（預設 0.7）")
    parser.add_argument("--timeout", type=float, default=20.0, help="單次請求逾時秒數（預設 20）")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
