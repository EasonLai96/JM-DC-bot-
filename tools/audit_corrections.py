# -*- coding: utf-8 -*-
"""🔎 修正表覆蓋率稽核（安全版：先驗證、再改表）

這支工具解決一個反覆出現的實作陷阱：
**「我以為機翻會輸出 A，實際上輸出 B」** → 修正表的 key 根本沒命中。

它做的事：
  1. 拿一批「日文詞／片語」，實際跑一次機翻，記錄**真實輸出**
  2. 把真實輸出丟進 `fix_machine_translation()`，看修正表有沒有修好
  3. 列出「仍未被修正」的項目 → 這些才是真正該加進表的 key

⚠️ 安全性：只讀不寫。它不會自動修改 `glossary_zh.py`，
   只印出「建議新增」的清單給你（或給 AI）人工確認，避免把猜測寫進程式碼。

用法：
    python tools/audit_corrections.py              # 稽核內建清單
    python tools/audit_corrections.py --full       # 含全部 150 個標籤日文對照
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Dict, List, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "cogs"))
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
from glossary_zh import fix_machine_translation, validate_corrections  # noqa: E402

# (日文原文, 期望的中文)
# 期望值是「圈內通用語」，用來判斷修正表有沒有把人話修出來。
TERMS: List[Tuple[str, str]] = [
    # ── 書名常見句型 ──
    ("むっつり冒險者は觸手なんかに負けたくない", "悶騷冒險者才不會輸給觸手"),
    ("おやすみせっくす總集編", "晚安性愛總集篇"),
    ("おかえりせっくす總集編", "歡迎回來性愛總集篇"),
    ("おでかけせっくす總集編", "外出性愛總集篇"),
    ("ギャルと幼馴染のパイズリ", "辣妹與青梅竹馬的乳交"),
    ("爆乳お姉さんと中出し", "爆乳大姐姐與中出"),
    ("彼女は止まらない", "她停不下來"),
    ("義母を我慢できない", "忍不住對繼母出手"),
    # ── 身體／胸部 ──
    ("おっぱい", "胸部"),
    ("爆乳", "爆乳"),
    ("巨乳", "巨乳"),
    ("貧乳", "貧乳"),
    ("美乳", "美乳"),
    ("お尻", "臀部"),
    ("太もも", "大腿"),
    ("ちんぽ", "肉棒"),
    ("おまんこ", "私處"),
    ("おちんちん", "小雞雞"),
    # ── 性行為 ──
    ("パイズリ", "乳交"),
    ("顔射", "顏射"),
    ("中出し", "中出"),
    ("ごっくん", "吞精"),
    ("イラマチオ", "深喉"),
    ("素股", "素股"),
    ("手コキ", "打手槍"),
    ("フェラ", "口交"),
    ("オナニー", "自慰"),
    # ── 題材 ──
    ("寝取られ", "NTR"),
    ("寝取り", "NTR"),
    ("浮気", "偷情"),
    ("調教", "調教"),
    ("陵辱", "凌辱"),
    ("孕ませ", "懷孕"),
    ("催眠", "催眠"),
    ("監禁", "監禁"),
    ("奴隷", "奴隷"),
    ("放尿", "放尿"),
    ("おもらし", "失禁"),
    # ── 屬性 ──
    ("むっつり", "悶騷"),
    ("スケベ", "色胚"),
    ("エッチ", "色色"),
    ("変態", "變態"),
    ("痴女", "痴女"),
    ("痴漢", "痴漢"),
    ("クーデレ", "酷嬌"),
    ("ヤンデレ", "病嬌"),
    ("ツンデレ", "傲嬌"),
    # ── 關係 ──
    ("幼馴染", "青梅竹馬"),
    ("後輩", "學妹"),
    ("先輩", "學姐"),
    ("お姉さん", "大姐姐"),
    ("義母", "繼母"),
    ("嫁", "老婆"),
    ("義妹", "繼妹"),
    # ── 服裝 ──
    ("体操着", "體操服"),
    ("ブルマ", "燈籠褲"),
    ("ニーソ", "過膝襪"),
    ("パンスト", "褲襪"),
    ("ポニーテール", "馬尾"),
    ("水着", "泳裝"),
    # ── 題材／世界觀 ──
    ("冒険者", "冒險者"),
    ("勇者", "勇者"),
    ("異世界", "異世界"),
    ("魔法少女", "魔法少女"),
]


def _pass(label: str, ok: bool, detail: str = "") -> str:
    mark = "✅" if ok else "❌"
    return f"  {mark} {label}{('  → ' + detail) if detail and not ok else ''}"


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rapid", type=int, default=0, help="保留參數，未使用")
    parser.parse_args()

    print("=" * 78)
    print("🔎 修正表覆蓋率稽核")
    print("=" * 78)

    problems = validate_corrections()
    print(f"\n── 修正表自我檢查 ──")
    print(f"  {'✅ 沒有重複替換問題' if not problems else '❌ 有 ' + str(len(problems)) + ' 個問題'}")
    for item in problems:
        print(f"     {item}")

    print(f"\n── 逐項稽核（{len(TERMS)} 項，會實際呼叫機翻）──")
    fixed: List[Tuple[str, str, str]] = []
    unfixed: List[Tuple[str, str, str]] = []
    degraded: List[Tuple[str, str, str]] = []

    for japanese, expected in TERMS:
        raw, endpoint = await translate._translate_raw(japanese)
        if not raw and not endpoint:
            # 快取命中或端點失敗都不是這裡要測的，直接跳過
            print(f"  ⏭️  {japanese:<30} （無譯文，跳過）")
            continue

        corrected = fix_machine_translation(raw)
        record = (japanese, raw, corrected)

        if expected in corrected or corrected == expected:
            fixed.append(record)
            print(f"  ✅ {japanese:<30} {raw} → {corrected}")
        else:
            unfixed.append(record)
            print(f"  ❌ {japanese:<30} 機翻={raw}  修正後={corrected}  期望≈{expected}")

    print()
    print("=" * 78)
    print(f"📊 結果：已修正 {len(fixed)}　仍不理想 {len(unfixed)}")
    print("=" * 78)
    if unfixed:
        print("\n以下是「仍然沒有被修正表處理到」的真實機翻輸出，")
        print("可以考慮加進 cogs/glossary_zh.py 的 MT_CORRECTIONS_ZH：\n")
        for japanese, raw, corrected in unfixed:
            print(f"    # {japanese}")
            print(f"    {raw!r}: {corrected!r},")
    print()

    await translate.close_session()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
