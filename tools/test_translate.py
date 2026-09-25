# -*- coding: utf-8 -*-
"""🧪 cogs/translate.py 的自我測試（獨立執行，不影響機器人）

測試重點：
  1. 純函式（不連網）：標籤查表、括號保護、語言偵測、簡體修正、長度截斷
  2. 實際連網：標題翻譯、快取命中
  3. Fallback：端點全掛時必須退回原文、不拋例外
  4. 開關：TRANSLATE_ENABLED=0 時行為與加入本模組前相同

用法：
    python tools/test_translate.py            # 全部測試
    python tools/test_translate.py --offline  # 只跑不連網的測試
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from typing import Any, Dict

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "cogs"))

# 本機（Windows）的依賴裝在 .local/lib/python*/site-packages（那是下載給機器人的
# venv，Python 版本字尾可能與本機不同）。主機上依賴是直接裝好的，這行會自動跳過。
for _candidate in (
    os.path.join(_ROOT, ".local", "lib", "python3.11", "site-packages"),
    os.path.join(_ROOT, ".local", "lib", "python3.12", "site-packages"),
):
    if os.path.isdir(_candidate) and _candidate not in sys.path:
        sys.path.append(_candidate)

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

import translate  # noqa: E402
from glossary_zh import (  # noqa: E402
    ALL_TERMS_ZH,
    NH_CATEGORY_ZH,
    NH_LANGUAGE_ZH,
    NH_TAG_ZH,
    fix_machine_translation,
    fix_simplified,
)

_passed = 0
_failed = 0
_tag_line_cache: Dict[Any, str] = {}


def check(label: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  ✅ {label}")
    else:
        _failed += 1
        print(f"  ❌ {label}{('  → ' + detail) if detail else ''}")


def section(title: str) -> None:
    print()
    print(f"── {title} ──")


# ──────────────────────────────────────────────────────────────
# 1. 標籤字典
# ──────────────────────────────────────────────────────────────
def test_glossary() -> None:
    section("1. 標籤字典")
    check("標籤字典有 150 個", len(NH_TAG_ZH) == 150, f"實際 {len(NH_TAG_ZH)}")
    check("分類/語言/角色/系列已併入合併表",
          len(ALL_TERMS_ZH) > len(NH_TAG_ZH),
          f"合併表 {len(ALL_TERMS_ZH)} vs 標籤 {len(NH_TAG_ZH)}")

    for name in ["nakadashi", "ahegao", "netorare", "milf", "yaoi", "bbw", "gag", "snuff"]:
        check(f"{name} → {translate.tag_zh(name)}", bool(translate.tag_zh(name)))

    # 每一部作品都會出現的欄位，必須有譯名
    for name, expected in [("doujinshi", "同人誌"), ("manga", "商業誌"),
                           ("japanese", "日文"), ("chinese", "中文"),
                           ("english", "英文"), ("translated", "翻譯版"),
                           ("original", "原創"), ("touhou project", "東方 Project")]:
        check(f"{name} → {expected}", translate.tag_zh(name) == expected,
              str(translate.tag_zh(name)))

    check("查不到的標籤回傳 None", translate.tag_zh("this-is-not-a-real-tag") is None)
    check("大小寫不敏感", translate.tag_zh("NAKADASHI") == "中出")

    # 機翻災難必須被字典擋掉（這些是實測機翻真的翻出來的鬼東西）
    check("milf 不是『摩洛伊斯蘭解放陣線』",
          translate.tag_zh("milf") != "摩洛伊斯蘭解放陣線")
    check("yaoi 不是『姚追』", translate.tag_zh("yaoi") != "姚追")
    check("bbw 不是『線控制動』", translate.tag_zh("bbw") != "線控制動")
    check("gag 不是『插科打諢』", translate.tag_zh("gag") != "插科打諢")
    check("netorare 是 NTR", translate.tag_zh("netorare") == "NTR")
    check("ahegao 是阿黑顏", translate.tag_zh("ahegao") == "阿黑顏")


def test_tag_label() -> None:
    section("2. 標籤顯示格式")
    check("中文＋原文對照", translate.tag_label("nakadashi") == "中出（nakadashi）",
          translate.tag_label("nakadashi"))
    check("關閉對照時只顯示中文", translate.tag_label("nakadashi", False) == "中出")
    check("查不到字典時顯示原文", translate.tag_label("unknown-tag") == "unknown-tag")
    check("空字串安全", translate.tag_label("") == "")


# ──────────────────────────────────────────────────────────────
# 2. 多標籤合併（async，需要 await）
# ──────────────────────────────────────────────────────────────
async def test_tag_line() -> None:
    section("3. 多標籤合併與長度控制")
    line = await translate.translate_tag_line(["nakadashi", "ahegao", "stockings"])
    check("多標籤用『，』分隔",
          "中出" in line and "阿黑顏" in line and "絲襪" in line, line)

    empty = await translate.translate_tag_line([])
    check("空清單回傳『無』", empty == "無", empty)

    # 長度上限：絕不可超過 budget（這是防止 embed 被 Discord 拒絕的關鍵）
    many = list(NH_TAG_ZH)[:120]
    for budget in (200, 500, 900, 1024):
        result = await translate.translate_tag_line(many, budget=budget)
        check(f"budget={budget} 時長度 {len(result)} 未超標",
              len(result) <= budget, f"實際 {len(result)}")
        _tag_line_cache[budget] = result


def test_brackets() -> None:
    section("4. 括號保護規則")
    cases = [
        ("[Jun Tokutyu Kuromask (Hetaren)] Omoi to Afurete Kuruizaku (Zenless Zone Zero)",
         ["[Jun Tokutyu Kuromask (Hetaren)]", "(Zenless Zone Zero)"]),
        ("(C108) [Jun Tokutyu Kuromask] Title Here",
         ["(C108)", "[Jun Tokutyu Kuromask]"]),
    ]
    for title, must_protect in cases:
        parts = translate._split_protected(title)
        protected = [seg for is_prot, seg in parts if is_prot]
        for expected in must_protect:
            check(f"保護 {expected!r}", expected in protected, f"實際保護 {protected}")

    # 🛠️ 實測修正：日文社團名原本會被翻爛（[ぽんぽんぺいん] → [蓬蓬佩因]），
    # 所以規則改成「括號內含假名也視為專有名詞 → 保護」。
    # 注意連帶效果：`[ぽんぽんぺいん] 如月ちゃんの受難` 這種「括號是社團名」的標題，
    # 括號會被保留原文、只有括號外的書名主體會被翻譯 —— 這正是我们要的行為。
    parts = translate._split_protected("[ぽんぽんぺいん] 如月ちゃんの受難")
    protected = [seg for is_prot, seg in parts if is_prot]
    check("日文社團名括號被保護（不被翻爛）",
          protected == ["[ぽんぽんぺいん]"], f"實際保護 {protected}")

    # 但括號外的書名主體必須仍可被翻譯
    outside = [seg for is_prot, seg in parts if not is_prot]
    check("括號外的書名主體不受保護",
          any("如月" in seg for seg in outside), f"實際 {outside}")


def test_language_detect() -> None:
    section("5. 語言偵測")
    cases = [
        ("如月ちゃんの受難", False, "ja"),   # 有假名 → 要翻
        ("High School Legend", False, "en"),  # 英文 → 要翻
        ("[黎欧出资汉化]", True, "en"),        # 已是中文 → 不翻
        ("中出", True, "en"),
        ("", True, "en"),
    ]
    for text, should_be_chinese, expected_lang in cases:
        check(f"{text!r} 判定為中文={should_be_chinese}",
              translate.is_probably_chinese(text) == should_be_chinese,
              f"實際 {translate.is_probably_chinese(text)}")
        if text:
            check(f"{text!r} 語言={expected_lang}",
                  translate.detect_source_lang(text) == expected_lang,
                  translate.detect_source_lang(text))


def test_machine_translation_fix() -> None:
    section("6-2. 機翻術語修正（治本層）")
    # 這些都是實測機翻真的吐出來的鬼東西（完整對照見 tools/term_quality.json）
    cases = [
        ("山雀", "胸部", "おっぱい 被翻成一種鳥"),
        ("他媽的奶", "乳交", "パイズリ"),
        ("烏龜", "NTR", "寝取られ"),
        ("內取", "NTR", "寝取り"),
        ("憂鬱", "悶騷", "むっつり"),
        ("性別", "色色", "エッチ"),
        ("轉型", "變態", "変態"),
        ("加侖", "辣妹", "ギャル"),
        ("甜褲", "燈籠褲", "ブルマ"),
        ("兒時的朋友", "青梅竹馬", "幼馴染"),
        ("一個人的小輩", "學妹", "後輩"),
        ("進階的", "學姐", "先輩"),
        ("我不想失去", "不想輸", "負けたくない"),
        ("浸漬", "懷孕", "孕ませ"),
        ("訓練", "調教", "調教"),
        ("作弊", "偷情", "浮気"),
    ]
    for wrong, right, note in cases:
        check(f"{wrong} → {right}（{note}）",
              fix_machine_translation(wrong) == right,
              fix_machine_translation(wrong))

    # 順序：長詞必須先於短詞處理，否則「巨大的乳房」會被「乳房」攔截成半截
    check("長詞優先：巨大的乳房 → 爆乳",
          fix_machine_translation("巨大的乳房") == "爆乳",
          fix_machine_translation("巨大的乳房"))
    check("長詞優先：大乳房 → 巨乳",
          fix_machine_translation("大乳房") == "巨乳",
          fix_machine_translation("大乳房"))
    check("短詞仍可單獨運作：乳房 → 胸部",
          fix_machine_translation("乳房") == "胸部",
          fix_machine_translation("乳房"))

    # 不該亂改正常的譯文
    check("正確譯文不被改動",
          fix_machine_translation("如月醬的痛苦") == "如月醬的痛苦")
    check("空字串安全", fix_machine_translation("") == "")


def test_simp_fix() -> None:
    section("6. 簡體修正（保守策略）")
    check("製服 → 制服", fix_simplified("女學生製服") == "女學生制服")
    check("一般繁體不受影響",
          fix_simplified("墮落的妻子和兒時朋友") == "墮落的妻子和兒時朋友")
    check("不會誤傷『公里』", fix_simplified("公里") == "公里")
    check("不會誤傷『能干』", fix_simplified("能干") == "能干")
    check("空字串安全", fix_simplified("") == "")


# ──────────────────────────────────────────────────────────────
# 3. 連網測試
# ──────────────────────────────────────────────────────────────
async def test_network() -> None:
    section("7. 實際翻譯（連網）")

    info = await translate.diagnostics("nakadashi")
    print(f"     端點={info['endpoint']}  延遲={info['latency_ms']}ms  "
          f"譯文={info['translated']}  快取={info['cache']}")
    check("診斷函式可執行", isinstance(info, dict))

    title = "妹の友達がこんなに濡れてるはずがない"
    started = time.time()
    result = await translate.translate_title(title)
    elapsed = int((time.time() - started) * 1000)
    print(f"     原文：{title}")
    print(f"     譯文：{result}   ({elapsed}ms)")
    check("日文標題翻譯成功", bool(result), "回傳 None 代表翻譯失敗")

    junk = "[Jun Tokutyu Kuromask] High School Legend"
    result2 = await translate.translate_title(junk)
    print(f"     原文：{junk}")
    print(f"     譯文：{result2}")
    if result2:
        check("社團名沒有被翻爛（不是黑面具純德泰）",
              "黑面具" not in result2 and "純德泰" not in result2, result2)
        check("括號結構保留", "[" in result2 and "]" in result2, result2)
    else:
        check("社團名沒有被翻爛（翻譯失敗＝原文，也算安全）", True)

    started = time.time()
    await translate.translate_title(title)
    cached_ms = int((time.time() - started) * 1000)
    print(f"     第二次呼叫（應命中快取）：{cached_ms}ms")
    check("快取命中 < 20ms", cached_ms < 20, f"實際 {cached_ms}ms")

    # 🔒 迴歸測試：快取讀取必須跟第一次「完全一樣」。
    # 曾經的 bug：把「修正後」的譯文寫進快取，讀取時又修正一次
    # → 產出「才才不會輸給」這種疊字。修法是把 raw 存進快取、
    # 修正只在出口做，這個測試就是為了鎖住那個修正。
    regression_title = "[Test Circle] むっつり冒險者は觸手なんかに負けたくない"
    fresh = await translate.translate_title(regression_title)   # 第一次（打 API）
    for round_index in range(3):
        again = await translate.translate_title(regression_title)  # 之後都走快取
        check(f"快取第 {round_index + 2} 次結果與首次相同",
              again == fresh, f"首次={fresh!r} 本次={again!r}")
    if fresh:
        check("沒有疊字（才才／的的／了了）",
              not any(bad in fresh for bad in ("才才", "的的", "了了", "會會")),
              fresh)

    check("純中文標題回傳 None（不需要翻）",
          await translate.translate_title("如月醬的痛苦") is None)

    display = await translate.translate_display("High School Legend : Red Dragon")
    print(f"     translate_display：{display}")
    check("translate_display 有輸出", bool(display))
    check("顯示長度未超標", len(display) <= 240, f"實際 {len(display)}")


async def test_fallback() -> None:
    section("8. Fallback 行為（模擬端點全掛）")
    original = translate._ENDPOINTS
    broken = (("broken", "https://127.0.0.1:9/?q={q}&sl={sl}&tl={tl}"),)
    try:
        translate._ENDPOINTS = broken
        result = await translate._translate_raw("uncached-text-for-fallback-12345")
        check("端點全掛時 _translate_raw 回傳空、不拋例外", result == ("", ""), str(result))

        title_result = await translate.translate_title("Uncached Fallback Title 98765")
        check("端點全掛時 translate_title 回傳 None", title_result is None, str(title_result))

        display = await translate.translate_display("Uncached Fallback Title ABCDEF 4242")
        check("端點全掛時 translate_display 退回原文",
              display == "Uncached Fallback Title ABCDEF 4242", display)
    finally:
        translate._ENDPOINTS = original


async def test_disabled() -> None:
    section("9. TRANSLATE_ENABLED 開關")
    os.environ["TRANSLATE_ENABLED"] = "0"
    try:
        check("enabled() 為 False", translate.enabled() is False)
        check("關閉時 translate_title 回傳 None",
              await translate.translate_title("High School Legend") is None)
        check("關閉時 translate_display 退回原文",
              await translate.translate_display("High School Legend") == "High School Legend")
    finally:
        os.environ.pop("TRANSLATE_ENABLED", None)

    for value, expected in [("1", True), ("true", True), ("0", False),
                            ("false", False), ("off", False), ("no", False),
                            ("yes", True), ("", True)]:
        os.environ["TRANSLATE_ENABLED"] = value
        check(f"TRANSLATE_ENABLED={value!r} → {expected}", translate.enabled() is expected)
    os.environ.pop("TRANSLATE_ENABLED", None)


# ──────────────────────────────────────────────────────────────
async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true", help="只跑不連網的測試")
    args = parser.parse_args()

    print("=" * 70)
    print("🧪 cogs/translate.py 自我測試")
    print("=" * 70)

    test_glossary()
    test_tag_label()
    await test_tag_line()
    test_brackets()
    test_language_detect()
    test_machine_translation_fix()
    test_simp_fix()

    if not args.offline:
        await test_network()
        await test_fallback()
        await test_disabled()
        await translate.close_session()
    else:
        section("7-9. 連網測試（已跳過 --offline）")

    print()
    print("=" * 70)
    print(f"📊 結果：✅ {_passed} 通過　❌ {_failed} 失敗")
    print("=" * 70)
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
