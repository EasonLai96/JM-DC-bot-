# -*- coding: utf-8 -*-
"""👁️ 翻譯效果預覽（用真實資料模擬 embed 會顯示什麼，不啟動機器人）

這支腳本會：
  1. 讀 comic_cache.json 的真實 JM 書名 → 跑翻譯 → 印出「中文（原文）」
  2. 用 nhentai API 抓一部真實作品 → 模擬 /nhv 的欄位會長什麼樣
  3. 統計字典命中率

⚠️ 只讀不寫：不會修改 comic_cache.json / nhentai_download_cache.json。

用法：
    python tools/preview_translation.py
    python tools/preview_translation.py --gallery 2112   # 指定 nhentai 作品
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import sys

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
from glossary_zh import ALL_TERMS_ZH, NH_TAG_ZH  # noqa: E402

try:
    import aiohttp
except ImportError:
    aiohttp = None

NHENTAI_API_BASE = "https://nhentai.net/api/v2"


def _load_api_key() -> str:
    """從 token.env 讀 NHENTAI_API_KEY（只讀，不印出）。"""
    for name in ("token.env", "token.env.txt", ".env"):
        path = os.path.join(_ROOT, name)
        if not os.path.exists(path):
            continue
        try:
            for line in io.open(path, encoding="utf-8"):
                if line.strip().startswith("NHENTAI_API_KEY"):
                    return line.split("=", 1)[1].strip()
        except OSError:
            continue
    return ""


async def preview_jm_titles() -> None:
    print("=" * 78)
    print("📕 禁漫（JM）快取書名的翻譯效果")
    print("=" * 78)
    path = os.path.join(_ROOT, "comic_cache.json")
    try:
        data = json.loads(io.open(path, encoding="utf-8").read())
    except (OSError, json.JSONDecodeError) as error:
        print(f"  讀不到 comic_cache.json：{error}")
        return

    if not isinstance(data, dict):
        print("  comic_cache.json 格式異常")
        return

    changed = 0
    total = 0
    for album_id, entry in list(data.items())[:12]:
        title = str((entry or {}).get("title") or "")
        if not title:
            continue
        total += 1
        result = await translate.translate_display(title, limit=240)
        is_changed = result != title
        if is_changed:
            changed += 1
        original = title if len(title) <= 70 else title[:69] + "…"
        shown = result if len(result) <= 70 else result[:69] + "…"
        mark = "🌐" if is_changed else "－"
        print(f"  {mark} ID {album_id}")
        print(f"      原：{original}")
        if is_changed:
            print(f"      新：{shown}")
    print()
    print(f"  統計：這 {total} 筆中有 {changed} 筆會顯示翻譯（中文標題本來就不會被動到）")


async def preview_nhentai(gallery_id: str) -> None:
    print()
    print("=" * 78)
    print(f"📗 nhentai 作品 {gallery_id} 的 /nhv 欄位預覽")
    print("=" * 78)

    if aiohttp is None:
        print("  缺少 aiohttp，跳過")
        return

    headers = {"Accept": "application/json", "User-Agent": "DiscordBot/1.0"}
    api_key = _load_api_key()
    if api_key:
        headers["Authorization"] = f"Key {api_key}"

    try:
        async with aiohttp.ClientSession(
            headers=headers, timeout=aiohttp.ClientTimeout(total=25)
        ) as session:
            async with session.get(f"{NHENTAI_API_BASE}/galleries/{gallery_id}") as response:
                if response.status != 200:
                    print(f"  API 回傳 HTTP {response.status}，跳過")
                    return
                data = await response.json()
    except Exception as error:
        print(f"  取得作品失敗：{type(error).__name__}: {error}")
        return

    def tag_names(tag_type: str):
        return [
            str(t["name"])
            for t in data.get("tags", [])
            if isinstance(t, dict) and t.get("type") == tag_type and t.get("name")
        ]

    title_data = data.get("title") or {}
    english = str(title_data.get("english") or "") if isinstance(title_data, dict) else ""
    japanese = str(title_data.get("japanese") or "") if isinstance(title_data, dict) else ""
    title = english or japanese or "(無標題)"

    print(f"  📖 原文書名：{title}")
    print(f"  📖 顯示書名：{await translate.translate_display(title, limit=240)}")
    if japanese and japanese != title:
        print(f"  🇯🇵 日文原名：{await translate.translate_display(japanese, limit=240)}")
    print()

    fields = [
        ("👤 作者", tag_names("artist"), 900),
        ("🎭 衍生同人誌", tag_names("parody"), 900),
        ("👥 登場角色", tag_names("character"), 900),
        ("🏷️ 標籤", tag_names("tag"), 900),
        ("👨‍👩‍👧‍👦 創作團隊", tag_names("group"), 900),
        ("🗣️ 語言", tag_names("language"), 900),
        ("📚 作品分類", tag_names("category"), 900),
    ]

    for name, values, budget in fields:
        if not values:
            continue
        rendered = await translate.translate_tag_line(values, budget=budget)
        # 模擬 Discord 的硬限制
        over = "  ⚠️ 超過 1024 字！" if len(rendered) > 1024 else ""
        print(f"  {name}（{len(values)} 項，{len(rendered)} 字）{over}")
        print(f"      {rendered[:400]}{'…' if len(rendered) > 400 else ''}")
        print()

    # 字典命中率
    all_tags = tag_names("tag")
    hit = sum(1 for t in all_tags if translate.tag_zh(t))
    print(f"  📊 標籤字典命中：{hit}/{len(all_tags)}"
          f"（{hit / len(all_tags) * 100:.0f}%）" if all_tags else "  （無標籤）")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gallery", default="", help="指定 nhentai 作品 ID")
    parser.add_argument("--skip-jm", action="store_true", help="跳過 JM 部分")
    args = parser.parse_args()

    print(f"翻譯功能狀態：{'✅ 啟用' if translate.enabled() else '⛔ 停用'}")
    print(f"字典收錄：標籤 {len(NH_TAG_ZH)} 個　｜　合併表（含分類／語言／角色／系列）共 {len(ALL_TERMS_ZH)} 筆")
    print()

    if not args.skip_jm:
        await preview_jm_titles()

    if args.gallery:
        await preview_nhentai(args.gallery)

    await translate.close_session()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
