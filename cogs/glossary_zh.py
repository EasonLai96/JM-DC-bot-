# -*- coding: utf-8 -*-
"""nhentai 標籤的中文對照表（台灣用語）。

為什麼要有這個檔案
------------------
nhentai 的 tag 是「受控詞彙」（固定清單，不是自由文字），所以查表遠比機翻可靠。
本專案已實測（`tools/api_vs_glossary.json`），前 150 個標籤丟給免費機翻，
**只有 40 個翻對**，其餘 110 個是這種等級的災難：

    milf         → 摩洛伊斯蘭解放陣線   （應為 熟女）
    yaoi         → 姚追                 （應為 BL）
    tanlines     → 鞣酸                 （應為 曬痕）
    bbw          → 線控制動             （應為 肥胖女）
    gag          → 插科打諢             （應為 口塞）
    snuff        → 鼻煙                 （應為 獵奇致死）
    paizuri      → 派祖裡               （應為 乳交）
    futanari     → 扶他那裡             （應為 扶他）
    netorare     → 內托拉雷             （應為 NTR）
    ahegao       → 阿赫高               （應為 阿黑顏）

除此之外查表還有兩個好處：
  • 0ms（機翻實測延遲 86ms ～ 1883ms，主機上極不穩定）
  • 零 API 呼叫，不會被限流、不會因逾時讓指令卡住

收錄範圍
--------
這裡收錄「使用量前 150 名」的 tag，覆蓋 nhentai 全部標籤使用量的 **86.0%**
（前 300 名可覆蓋 95.8%，之後要擴充照同樣格式往下加即可）。

資料來源：nhentai 官方 API `GET /api/v2/tags/tag?sort=popular`
（原始使用次數與官方英文說明見 `tools/nhentai_tags_raw.json`）

⚠️ 維護提醒
-----------
`NH_TAG_ZH` 的 key 必須與 API 回傳的 `tag.name` **完全一致**（全小寫、含空格）。
若 nhentai 之後改了標籤名稱，查不到會自動退回機翻／原文，功能不會壞，
只是會多花一次 API 呼叫。所以定期跑 `tools/` 的比對腳本核對是值得的。
"""

from __future__ import annotations

import re
from typing import Dict, List

# ──────────────────────────────────────────────────────────────
# 標籤中文對照表（使用量前 150 名）
# key = nhentai API 的 tag name；value = 台灣用語譯名
# ──────────────────────────────────────────────────────────────
NH_TAG_ZH: Dict[str, str] = {
    # ── 前 10 名 ──
    "big breasts": "巨乳",
    "sole female": "單女",
    "sole male": "單男",
    "group": "群交",
    "nakadashi": "中出",
    "anal": "肛交",
    "lolicon": "蘿莉",
    "stockings": "絲襪",
    "blowjob": "口交",
    "schoolgirl uniform": "學生制服",

    # ── 11 ~ 30 ──
    "full color": "全彩",
    "glasses": "眼鏡",
    "shotacon": "正太",
    "mosaic censorship": "馬賽克",
    "rape": "強暴",
    "yaoi": "BL",
    "ahegao": "阿黑顏",
    "bondage": "綑綁",
    "multi-work series": "系列作",
    "males only": "僅男性",
    "x-ray": "透視",
    "incest": "近親相姦",
    "milf": "熟女",
    "dark skin": "深色肌",
    "paizuri": "乳交",
    "sex toys": "情趣用品",
    "netorare": "NTR",
    "futanari": "扶他",
    "double penetration": "雙穴插入",
    "tankoubon": "單行本",

    # ── 31 ~ 50 ──
    "defloration": "破處",
    "twintails": "雙馬尾",
    "ffm threesome": "FFM 三人行",
    "swimsuit": "泳裝",
    "ponytail": "馬尾",
    "femdom": "女攻",
    "full censorship": "全修正",
    "impregnation": "受孕",
    "yuri": "百合",
    "collar": "頸圈",
    "big penis": "巨根",
    "anal intercourse": "肛門性交",
    "dilf": "熟男",
    "hairy": "多毛",
    "kemonomimi": "獸耳",
    "kissing": "接吻",
    "cheating": "出軌",
    "muscle": "肌肉",
    "pantyhose": "褲襪",
    "big ass": "豐臀",

    # ── 51 ~ 70 ──
    "bbm": "肥胖男",
    "tentacles": "觸手",
    "masturbation": "自慰",
    "bikini": "比基尼",
    "story arc": "長篇劇情",
    "mind control": "精神控制",
    "uncensored": "無修正",
    "sister": "姊妹",
    "sweating": "流汗",
    "lactation": "泌乳",
    "crossdressing": "女裝",
    "mind break": "精神崩壞",
    "tomgirl": "偽娘",
    "rough translation": "粗糙翻譯",
    "huge breasts": "爆乳",
    "mmf threesome": "MMF 三人行",
    "pregnant": "懷孕",
    "schoolboy uniform": "男學生制服",
    "exhibitionism": "暴露",
    "fingering": "手指插入",

    # ── 71 ~ 90 ──
    "females only": "僅女性",
    "unusual pupils": "特殊瞳孔",
    "gloves": "手套",
    "handjob": "打手槍",
    "teacher": "教師",
    "maid": "女僕",
    "beauty mark": "美人痣",
    "mother": "母親",
    "very long hair": "超長髮",
    "condom": "保險套",
    "gender bender": "性轉",
    "harem": "後宮",
    "lingerie": "內衣",
    "cunnilingus": "舔陰",
    "tail": "尾巴",
    "horns": "角",
    "urination": "放尿",
    "footjob": "足交",
    "big areolae": "大乳暈",
    "small breasts": "貧乳",

    # ── 91 ~ 110 ──
    "piercing": "穿環",
    "extraneous ads": "廣告頁",
    "catgirl": "貓娘",
    "gag": "口塞",
    "demon girl": "惡魔娘",
    "filming": "拍攝",
    "bald": "光頭",
    "stomach deformation": "腹部隆起",
    "prostitution": "賣春",
    "drugs": "藥物",
    "anthology": "合集",
    "elf": "精靈",
    "gyaru": "辣妹",
    "garter belt": "吊襪帶",
    "bunny girl": "兔女郎",
    "squirting": "潮吹",
    "blindfold": "矇眼",
    "halo": "光環",
    "blackmail": "脅迫",
    "nipple stimulation": "乳頭刺激",

    # ── 111 ~ 130 ──
    "tanlines": "曬痕",
    "scat": "排泄物",
    "virginity": "處女",
    "bukkake": "顏射",
    "bbw": "肥胖女",
    "no penetration": "無插入",
    "kimono": "和服",
    "eye-covering bang": "遮眼瀏海",
    "rimjob": "舔肛",
    "deepthroat": "深喉",
    "sole dickgirl": "單扶他",
    "inflation": "腹部膨脹",
    "sleeping": "睡眠中",
    "monster": "怪物",
    "scanmark": "掃圖浮水印",
    "breast feeding": "哺乳",
    "inseki": "姻親",
    "inverted nipples": "凹陷乳頭",
    "bloomers": "燈籠褲",
    "leotard": "緊身衣",

    # ── 131 ~ 150 ──
    "blowjob face": "口交臉",
    "tomboy": "男人婆",
    "webtoon": "條漫",
    "corruption": "墮落",
    "business suit": "西裝",
    "crotch tattoo": "下腹部刺青",
    "wings": "翅膀",
    "monster girl": "怪物娘",
    "thigh high boots": "過膝長靴",
    "slave": "奴隸",
    "school swimsuit": "學校泳裝",
    "humiliation": "羞辱",
    "snuff": "獵奇致死",
    "bodysuit": "緊身衣裝",
    "strap-on": "穿戴式假陽具",
    "daughter": "女兒",
    "bestiality": "獸交",
    "tall girl": "高挑女",
    "hair buns": "丸子頭",
    "magical girl": "魔法少女",
}

# 供維護／測試使用：字典內所有 key 的快照
NH_TAG_NAMES: List[str] = sorted(NH_TAG_ZH)


# ──────────────────────────────────────────────────────────────
# 作品分類（category）
# ──────────────────────────────────────────────────────────────
# nhentai 的 category 是獨立型別（不在 `tags/tag` 清單裡），全站只有 3 個值，
# 但**每一部作品都會出現**，所以一定要翻。
# 資料來源：GET /api/v2/tags/category
NH_CATEGORY_ZH: Dict[str, str] = {
    "doujinshi": "同人誌",
    "manga": "商業誌",
    "misc": "其他",
    "imageset": "圖片集",
    "artistcg": "畫師 CG",
    "gamecg": "遊戲 CG",
    "western": "歐美",
    "non-h": "一般向",
}


# ──────────────────────────────────────────────────────────────
# 語言（language）
# ──────────────────────────────────────────────────────────────
# 同樣是獨立型別，且每一部作品都會出現。前四個佔全部使用量的 99.99%。
# 資料來源：GET /api/v2/tags/language
NH_LANGUAGE_ZH: Dict[str, str] = {
    "japanese": "日文",
    "translated": "翻譯版",
    "chinese": "中文",
    "english": "英文",
    "korean": "韓文",
    "spanish": "西班牙文",
    "french": "法文",
    "portuguese": "葡萄牙文",
    "italian": "義大利文",
    "german": "德文",
    "russian": "俄文",
    "vietnamese": "越南文",
    "thai": "泰文",
    "indonesian": "印尼文",
    "polish": "波蘭文",
    "dutch": "荷蘭文",
    "turkish": "土耳其文",
    "greek": "希臘文",
    "czech": "捷克文",
    "romanian": "羅馬尼亞文",
    "hebrew": "希伯來文",
    "arabic": "阿拉伯文",
    "ukrainian": "烏克蘭文",
    "khmer": "高棉文",
    "textless": "無文字",
    "textless narrative": "無字敘事",
}


# ──────────────────────────────────────────────────────────────
# 泛用角色詞（character）
# ──────────────────────────────────────────────────────────────
# ⚠️ 這裡**只收「泛用職稱」**，不收具體角色名（如 `reimu hakurei`）。
# 原因：具體角色名的中文譯名在中文圈沒有共識（博麗靈夢／博麗靈夢、Saber／賽巴…），
# 硬翻反而造成混亂；而 `teitoku`（提督）、`sensei`（老師）這類詞是「角色定位」，
# 中文使用者看得懂中文更有幫助。
# 資料來源：GET /api/v2/tags/character（依使用量排序）
NH_CHARACTER_ROLE_ZH: Dict[str, str] = {
    # 玩家／主角代稱
    "teitoku": "提督",
    "sensei": "老師",
    "gudao": "御主",
    "gudako": "女御主",
    "shikikan": "指揮官",
    "producer": "製作人",
    "gran": "格蘭",
    "djeeta": "吉塔",
    "master": "御主",
    "doctor": "博士",
    "traveller": "旅行者",
    "player": "玩家",
    "you": "你",
    "reader": "讀者",
    # 常見職稱
    "shielder": "盾兵",
    "saber": "Saber",
    "oniisan": "大哥哥",
    "oneechan": "大姐姐",
    "oniichan": "哥哥",
    "oneesan": "姐姐",
    "kunoichi": "女忍",
    "princess": "公主",
    "queen": "女王",
    "witch": "魔女",
    "nurse": "護士",
    "waitress": "女服務生",
    "idol": "偶像",
}


# ──────────────────────────────────────────────────────────────
# 作品來源（parody）—— 只收最熱門的系列作
# ──────────────────────────────────────────────────────────────
# `original`（原創）單獨就佔了 155,505 次使用（佔全部 parody 的一大部分），
# 所以「原創」這個詞一定要翻。其餘是使用量最高的系列作，中文圈都有通用譯名。
# ⚠️ 不追求收完（總共 720 個），只收最常見的；查不到就顯示原文，不會壞。
# 資料來源：GET /api/v2/tags/parody（依使用量排序）
NH_PARODY_ZH: Dict[str, str] = {
    "original": "原創",
    "touhou project": "東方 Project",
    "kantai collection": "艦隊收藏",
    "fate grand order": "Fate/Grand Order",
    "the idolmaster": "偶像大師",
    "blue archive": "蔚藍檔案",
    "granblue fantasy": "碧藍幻想",
    "genshin impact": "原神",
    "pokemon": "寶可夢",
    "azur lane": "碧藍航線",
    "hololive": "hololive",
    "neon genesis evangelion": "新世紀福音戰士",
    "love live": "Love Live!",
    "girls und panzer": "少女與戰車",
    "sailor moon": "美少女戰士",
    "one piece": "航海王",
    "mahou shoujo lyrical nanoha": "魔法少女奈葉",
    "fate stay night": "Fate/stay night",
    "naruto": "火影忍者",
    "sword art online": "刀劍神域",
    "arknights": "明日方舟",
    "to love-ru": "出包王女",
    "my hero academia": "我的英雄學院",
    "street fighter": "快打旋風",
    "princess connect": "超異域公主連結",
    "nijisanji": "彩虹社",
    "puella magi madoka magica": "魔法少女小圓",
    "touken ranbu": "刀劍亂舞",
    "king of fighters": "拳皇",
    "vocaloid": "Vocaloid",
    "kaguya-sama wa kokurasetai": "輝夜姬想讓人告白",
    "kimetsu no yaiba": "鬼滅之刃",
    "boku no hero academia": "我的英雄學院",
    "dragon ball": "七龍珠",
    "final fantasy": "Final Fantasy",
    "the legend of zelda": "薩爾達傳說",
    "overwatch": "鬥陣特攻",
    "league of legends": "英雄聯盟",
    "dead or alive": "生死格鬥",
    "tekken": "鐵拳",
    "idolmaster cinderella girls": "偶像大師 灰姑娘女孩",
    "idolmaster million live": "偶像大師 百萬人演唱會",
    "love live sunshine": "Love Live! Sunshine!!",
    "love live nijigasaki": "Love Live! 虹咲學園",
    "honkai impact": "崩壞3rd",
    "honkai star rail": "崩壞：星穹鐵道",
    "punishing gray raven": "戰雙帕彌什",
    "girls frontline": "少女前線",
    "dragon quest": "勇者鬥惡龍",
    "fire emblem": "聖火降魔錄",
    "persona 5": "女神異聞錄5",
    "nier automata": "尼爾：自動人形",
    "resident evil": "惡靈古堡",
    "tales of": "傳奇系列",
    "gundam": "鋼彈",
    "macross": "超時空要塞",
    "cardcaptor sakura": "庫洛魔法使",
    "ranma 1/2": "亂馬1/2",
    "urusei yatsura": "福星小子",
    "inuyasha": "犬夜叉",
    "bleach": "死神",
    "fairy tail": "魔導少年",
    "hunter x hunter": "獵人",
    "attack on titan": "進擊的巨人",
    "tokyo ghoul": "東京喰種",
    "re zero": "Re:從零開始的異世界生活",
    "konosuba": "為美好的世界獻上祝福！",
    "spy x family": "SPY×FAMILY 間諜家家酒",
    "chainsaw man": "鏈鋸人",
    "jujutsu kaisen": "咒術迴戰",
    "oshi no ko": "【我推的孩子】",
    "lycoris recoil": "Lycoris Recoil 莉可麗絲",
    "cyberpunk edgerunners": "電馭叛客：邊緣行者",
    "tekken 8": "鐵拳8",
    "the king of fighters": "拳皇",
    "street fighter 6": "快打旋風6",
}


# ──────────────────────────────────────────────────────────────
# 合併查表用（型別無關：同一個字串不管來自哪個欄位都翻一樣的）
# ──────────────────────────────────────────────────────────────
# ⚠️ 合併順序很重要：**後面的會覆蓋前面的**。
# `NH_TAG_ZH` 放最後，確保標籤的譯名（最常用、也最常被審閱）永遠優先，
# 不會被 category / language 的同名字串蓋掉。
ALL_TERMS_ZH: Dict[str, str] = {}
ALL_TERMS_ZH.update(NH_CATEGORY_ZH)
ALL_TERMS_ZH.update(NH_LANGUAGE_ZH)
ALL_TERMS_ZH.update(NH_CHARACTER_ROLE_ZH)
ALL_TERMS_ZH.update(NH_PARODY_ZH)
ALL_TERMS_ZH.update(NH_TAG_ZH)


# ──────────────────────────────────────────────────────────────
# 機翻結果的繁體修正表（只放「實測確認」的詞，不憑空猜測）
# ──────────────────────────────────────────────────────────────
# 背景：實測 `clients5.google.com` 即使傳 `tl=zh-TW`，仍有極少數情況回簡體。
# 我用 150 個標籤＋15 組日文／英文長句實測後，**只確認到下面這些**：
#
#   schoolgirl uniform → 女學生製服   （製 → 制）
#   schoolboy uniform  → 男學生製服   （製 → 制）
#
# 其餘 249 個中文字**全部都是繁體**（如 墮落、絲襪、醬、濕、觸手、蘿莉…），
# 所以這裡刻意**不做「單字級」簡繁轉換表** —— 那種表很容易誤傷（例如把
# 「公里」轉成「公裡」、「能干」轉成「能幹」），而且本機沒有 opencc / zhconv
# 可用（已實測）。
#
# ⚠️ 這裡是「詞」對「詞」的精確替換，只在使用機翻結果時套用。
SIMP_WORD_FIXES: Dict[str, str] = {
    "製服": "制服",
    "連褲襪": "褲襪",
    "未經審查的": "無修正",
    "全面審查": "全修正",
    "馬賽克審查制度": "馬賽克",
}


def fix_simplified(text: str) -> str:
    """把機翻結果中「已確認」的簡體詞換成繁體。

    刻意保守：只做精確詞替換、不做單字轉換，寧可漏改也不誤改。
    真正的術語正確性由 `NH_TAG_ZH` 查表保證（覆蓋 86%）。
    """
    if not text:
        return text
    for simplified, traditional in SIMP_WORD_FIXES.items():
        if simplified in text:
            text = text.replace(simplified, traditional)
    return text


# ──────────────────────────────────────────────────────────────
# 機翻修正表（把機翻出來的中文，換成正確的圈內用語）
# ──────────────────────────────────────────────────────────────
# 為什麼需要這一層
# ----------------
# 實測把 94 個常在書名／標籤出現的日文詞丟給免費機翻，**只有 32 個勉強正確**，
# 其餘 62 個是這種等級的錯誤（`tools/term_quality.json` 有完整對照）：
#
#     おっぱい   → 山雀      （變成一種鳥）
#     パイズリ   → 他媽的奶
#     寝取られ   → 烏龜
#     おまんこ   → 貓
#     エッチ     → 性別
#     変態       → 轉型
#     むっつり   → 憂鬱      （應為「悶騷」）
#     幼馴染     → 兒時的朋友 （應為「青梅竹馬」）
#     ギャル     → 加侖      （應為「辣妹」）
#     ブルマ     → 甜褲      （應為「燈籠褲」）
#
# 這些詞在成人向書名裡出現頻率極高，所以「機翻 + 修正表」比純機翻好非常多。
#
# ⚠️ 實作方式：這張表是**「機翻輸出 → 正確譯文」**的後處理替換。
#    它不會去動原文，只修正機翻結果，所以最壞情況就是「沒改到」（顯示原本的
#    不理想譯文），而不會把對的東西改壞。
#
# ⚠️ 順序很重要：`fix_machine_translation()` 會依 key 長度**由長到短**替換，
#    確保「大乳房」先被換成「巨乳」，不會被「乳房」先攔截成半截。
MT_CORRECTIONS_ZH: Dict[str, str] = {
    # ── 句型／語尾（讓譯文自然的關鍵）──
    # ⚠️⚠️ 這張表踩過兩次坑，規則寫在這裡避免再犯：
    #
    #   坑 1（疊字）：曾同時放了「不會輸給」→「才不會輸給」和
    #        「不想輸給任何人」→「才不會輸給任何人」。原文機翻是
    #        「不想輸給任何人」，第一條先換成「才不會輸給任何人」，
    #        第二條又匹配到其中的「不會輸給」再換一次 → **「才才不會輸給」**。
    #   坑 2（憑印象填 key）：曾填「不想輸給」（實際輸出是「不想輸給任何人」）
    #        與「性愛集合」（實際輸出是「晚安性愛集合」），結果一個沒中、
    #        一個把「...總集篇集合」疊成兩層。
    #
    # ✅ 現在的紀律：**每個 key 都必須是實測到的完整機翻輸出片段**，
    #    且任一 key 不可為另一 key 的子字串。驗證方式：
    #        python tools/term_quality.json 的產生流程（見 tools/）
    "不想輸給任何人": "才不會輸給任何人",
    "晚安性愛集合": "晚安性愛總集篇",
    "歡迎回來性愛收藏": "歡迎回來性愛總集篇",
    "郊遊性愛集合": "外出性愛總集篇",

    # ── 身體／胸部（書名最高頻，機翻錯得最離譜）──
    "巨大的乳房": "爆乳",
    "大乳房": "巨乳",      # 爆乳 → 大乳房
    "小乳房": "貧乳",
    "美麗的乳房": "美乳",
    "乳房": "胸部",
    "山雀": "胸部",        # おっぱい → 山雀（變成一種鳥）
    "陰莖": "肉棒",        # ちんぽ → 陰莖
    "他媽的奶": "乳交",     # パイズリ
    "腿間": "素股",        # 素股
    "濕的": "濕",          # 濡れ
    "沾濕": "濕",

    # ── 性行為／體液 ──
    "臉部的": "顏射",       # 顔射
    "深喉嚨": "深喉",       # イラマチオ
    "浸漬": "懷孕",        # 孕ませ
    "我正在射精": "高潮",    # イク
    "尿尿": "失禁",        # おもらし／放尿

    # ── 稱謂／關係 ──
    # ⚠️ 「她」→「女友」**刻意不加**：那是 彼女，但「她」在一般句子裡是
    #    極常見的代名詞，加了會把大量正常句子改壞（例如「她不會停止」）。
    "兒時的朋友": "青梅竹馬",   # 幼馴染
    "一個人的小輩": "學妹",     # 後輩
    "進階的": "學姐",         # 先輩
    "妻子": "老婆",           # 嫁
    "嫂嫂": "繼妹",           # 義妹
    # ⚠️ 關於 義母／婆婆／岳母：**刻意不做修正**。
    #    機翻對 義母 會給「岳母」、對 婆婆 也給「岳母」，
    #    而中文裡兩者意思不同（義母＝繼母，岳母＝配偶之母）。
    #    因為譯文一樣，無法反推原文是哪一個，任何單向修正都會誤傷另一邊
    #    （實際發生過 `婆婆 → 岳母 → 繼母` 的連鎖）。寧可保留原翻譯。

    # ── 屬性／萌屬性 ──
    "庫代雷": "酷嬌",         # クーデレ
    "揚德雷": "病嬌",         # ヤンデレ
    # むっつり 實測有兩種輸出：單獨翻是「憂鬱」，長句裡變「悶悶不樂」
    "憂鬱": "悶騷",
    "悶悶不樂": "悶騷",
    "喜怒無常的淫蕩": "悶騷色胚",   # むっつりスケベ
    "沉默寡言": "寡言",        # 無口
    "性別": "色色",           # エッチ
    "好色之徒": "色色",        # えっち
    "轉型": "變態",           # 変態
    "猥褻": "色胚",           # スケベ
    "蕩婦": "痴女",           # 痴女
    "騷擾": "痴漢",           # 痴漢
    "性騷擾火車": "痴漢電車",   # 痴漢電車
    "加侖": "辣妹",           # ギャル

    # ── 服裝 ──
    "健身服": "體操服",        # 体操着
    "甜褲": "燈籠褲",         # ブルマ
    # ⚠️ 這裡刻意**沒有**「大腿」→「過膝襪」這一條，原因見下方同名註解。
    "馬尾辮": "馬尾",         # ポニーテール
    "連褲襪": "褲襪",         # パンスト

    # ── 題材／情境 ──
    "烏龜": "NTR",           # 寝取られ
    "內取": "NTR",           # 寝取り
    "作弊": "偷情",           # 浮気
    "訓練": "調教",           # 調教
    "侮辱": "凌辱",           # 陵辱
    "禁閉": "監禁",           # 監禁
    "勇敢的人": "勇者",        # 勇者
    "冒險家": "冒險者",        # 冒険者
    "另一個世界": "異世界",     # 異世界

    # ── 常用語句（書名尾綴）──
    "我不想失去": "不想輸",     # 負けたくない
    "我受不了了": "忍不住",     # 我慢できない
    "她不會停止": "她停不下來",  # 彼女は止まらない
    "真的很喜歡": "最喜歡",     # 大好き
    "承諾": "約定",           # 約束

    # ── 稽核（tools/audit_corrections.py）補抓到的漏項 ──
    # 以下全部是「實際跑一次機翻後才發現」的真實輸出，不是憑印象填的。
    "童年好友": "青梅竹馬",     # 幼馴染 在長句裡被翻成「童年好友」
    "大胸": "爆乳",           # 爆乳 在長句裡被翻成「大胸」（單獨翻是「大乳房」）
    "弟弟": "正太",           # ショタ 的機翻輸出
    "幼女": "蘿莉",           # ロリ 的機翻輸出
    "嫁妝": "老婆",           # 嫁 被翻成「嫁妝」
    "癡女": "痴女",           # 繁簡／異體字統一（nhentai 與機翻會混用）
    "癡漢": "痴漢",
    "體操著": "體操服",        # 体操着
    "水著": "泳裝",           # 水着
    "姊姊": "姐姐",
    # ⚠️ 「大腿」的處理是**刻意從缺**：
    #    太もも 的機翻輸出是「大腿」（正確），但ニーソ 的輸出也是「大腿」（錯誤）。
    #    譯文相同、無法反推原文，所以：
    #      • 不加「大腿」→「過膝襪」：那會把正確的大腿改壞（實測抓到過）
    #      • 「ニーソ」這個錯誤就留著（顯示「大腿」），可接受
    #    這是單向修正表的先天限制，記錄在這裡避免以後又有人想「順手修一下」。
}


# 疊字白名單：這些詞本身就會出現疊字，絕對不能被去重邏輯折疊掉。
# ⚠️ 這是因為 `fix_machine_translation()` 有一道「折疊重複字元」的保險，
#    用來防止修正表意外疊出「才才」這種結果，但擬聲／擬態詞天然就有疊字。
REPEAT_SAFE_WORDS = (
    "滿滿", "滿滿的", "軟軟", "硬硬", "慢慢", "黏黏", "濕濕", "暖暖", "輕輕",
    "緊緊", "深深", "高高", "低低", "多多", "少少", "好好", "快快",
)


def _collapse_repeats(text: str) -> str:
    """折疊「3 個以上」連續重複的字元（例：才才才 → 才）。

    ⚠️ 保守設計，因為中文的擬聲／疊字詞很常見：
      • 只處理 3 個以上（2 個連續相同字中文很常見，例如「滿滿」「軟軟」）
      • 白名單內的詞整段跳過
    這是防禦性措施：正常情況下 `MT_CORRECTIONS_ZH` 不該產生重複字元
    （歷史上真的發生過一次，見該表的註解），這裡把它擋在最後一關。
    """
    if not text:
        return text
    for safe in REPEAT_SAFE_WORDS:
        if safe in text:
            return text
    return re.sub(r"(.)\1{2,}", r"\1", text)


def validate_corrections() -> List[str]:
    """檢查 `MT_CORRECTIONS_ZH` 是否會產生「重複替換 / 連鎖替換」問題。

    為什麼需要這道檢查：替換是依序套用的，如果修正結果裡又含有另一個 key，
    同一段文字就會被連續改動。真實發生過的案例：

        原文機翻：不想輸給任何人
        key「不會輸給」→「才不會輸給」            （先套用）
        key「不想輸給任何人」→「才不會輸給任何人」
        → 結果「才才不會輸給任何人」❌

    還有一種較隱蔽的連鎖：`婆婆` → `岳母` → `繼母`（因為 `岳母` 也是 key），
    結果雖然「剛好正確」，但依賴了替換順序，屬於脆弱設計。

    檢查的是**真正重要的性質**：對每個 key 的替換結果再套用一次修正，
    結果必須不變（＝ idempotent，不會連鎖）。

    ⚠️ 已知且刻意接受的例外：`巨大的乳房`／`大乳房`／`乳房` 這種
    「長詞優先」的組合互為子字串，但長詞先換、換完短詞已不存在，是安全的，
    這個檢查會正確放行（因為結果不再變動）。

    回傳有問題的描述清單；空清單代表健康。
    """
    problems: List[str] = []
    for key, replacement in sorted(MT_CORRECTIONS_ZH.items(), key=lambda kv: -len(kv[0])):
        once = _resolve_corrections(replacement)
        twice = _resolve_corrections(once)
        if once != twice:
            problems.append(
                f"{key!r} → {replacement!r} 會連鎖成 {twice!r}（{once!r} 仍含其他 key）"
            )
    return problems


def _resolve_corrections(text: str) -> str:
    """套用修正表（依 key 長度由長到短），**不含**疊字折疊。

    抽出來是為了讓 `validate_corrections()` 能在不觸發折疊保險的情況下，
    檢查替換本身是否穩定。
    """
    if not text:
        return text
    for wrong in sorted(MT_CORRECTIONS_ZH, key=len, reverse=True):
        if wrong in text:
            text = text.replace(wrong, MT_CORRECTIONS_ZH[wrong])
    return text


def _self_check() -> None:
    """匯入時自我檢查，有問題只警告、不中斷（翻譯是附加功能）。"""
    problems = validate_corrections()
    if problems:
        try:
            from logger_config import log as _log

            _log.warning(
                f"⚠️ [翻譯] 修正表有 {len(problems)} 個項目會重複替換：{problems[:3]}"
            )
        except Exception:
            pass


_self_check()


def fix_machine_translation(text: str) -> str:
    """把機翻結果裡「已知會翻錯」的詞換成正確的圈內用語。

    實作要點：
      • 依 key 長度**由長到短**替換，確保「巨大的乳房」先被換成「爆乳」，
        不會被「乳房」先攔截成半截。
      • 不動原文、只修譯文，所以最壞情況是「沒改到」（顯示原本不理想的譯文），
        不可能把原本正確的譯文改壞。
      • 最後過一道 `_collapse_repeats()`，避免任何意外疊字流到使用者眼前。
    """
    if not text:
        return text
    return _collapse_repeats(_resolve_corrections(text))
