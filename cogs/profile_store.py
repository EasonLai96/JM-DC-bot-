# -*- coding: utf-8 -*-
"""
個人檔案與萌力次元經濟資料儲存層 (JSON-based) - 創新互動融合版
"""
import os
import json
import time
import math
import asyncio
from typing import Optional, Dict, Any
from config import current_dir
from logger_config import log

# 定義資料夾路徑
DATA_DIR = os.path.join(current_dir, "profile_data")
BACKGROUND_DIR = os.path.join(DATA_DIR, "custom_backgrounds")
PROFILES_PATH = os.path.join(DATA_DIR, "profiles.json")
GLOBAL_STORE_PATH = os.path.join(DATA_DIR, "global_economy.json")  # 全服發電廠儲存檔

os.makedirs(BACKGROUND_DIR, exist_ok=True)

# 🪐 核心設定：1~20等簽到等級數據矩陣 (嚴格對接你的等級換算表)
SIGN_LEVEL_MATRIX = {
    1: {"mp_cost": 0, "dc_cost": 0, "hours": 1, "h_mult": 1.00, "d_mult": 1.00},
    2: {"mp_cost": 1000, "dc_cost": 0, "hours": 2, "h_mult": 1.10, "d_mult": 1.00},
    3: {"mp_cost": 5000, "dc_cost": 0, "hours": 3, "h_mult": 1.20, "d_mult": 1.10},
    4: {"mp_cost": 10000, "dc_cost": 1, "hours": 4, "h_mult": 1.25, "d_mult": 1.20},
    5: {"mp_cost": 20000, "dc_cost": 1, "hours": 5, "h_mult": 1.35, "d_mult": 1.35},
    6: {"mp_cost": 30000, "dc_cost": 2, "hours": 6, "h_mult": 1.45, "d_mult": 1.45},
    7: {"mp_cost": 50000, "dc_cost": 2, "hours": 8, "h_mult": 1.50, "d_mult": 1.55},
    8: {"mp_cost": 90000, "dc_cost": 3, "hours": 12, "h_mult": 1.75, "d_mult": 1.75},
    9: {"mp_cost": 125000, "dc_cost": 4, "hours": 18, "h_mult": 2.00, "d_mult": 2.00},
    10: {"mp_cost": 180000, "dc_cost": 5, "hours": 24, "h_mult": 2.25, "d_mult": 2.50},
    11: {"mp_cost": 250000, "dc_cost": 6, "hours": 36, "h_mult": 2.50, "d_mult": 3.50},
    12: {"mp_cost": 400000, "dc_cost": 7, "hours": 48, "h_mult": 3.00, "d_mult": 5.00},
    13: {"mp_cost": 1000000, "dc_cost": 9, "hours": 72, "h_mult": 3.50, "d_mult": 6.50},
    14: {"mp_cost": 3000000, "dc_cost": 11, "hours": 120, "h_mult": 5.00, "d_mult": 7.50},
    15: {"mp_cost": 5000000, "dc_cost": 13, "hours": 168, "h_mult": 7.50, "d_mult": 9.00},
    16: {"mp_cost": 10000000, "dc_cost": 15, "hours": 168, "h_mult": 10.00, "d_mult": 12.50},
    17: {"mp_cost": 30000000, "dc_cost": 17, "hours": 168, "h_mult": 25.00, "d_mult": 25.00},
    18: {"mp_cost": 50000000, "dc_cost": 19, "hours": 168, "h_mult": 50.00, "d_mult": 50.00},
    19: {"mp_cost": 100000000, "dc_cost": 20, "hours": 168, "h_mult": 100.00, "d_mult": 100.00},
    20: {"mp_cost": 1000000000, "dc_cost": 20, "hours": 168, "h_mult": 250.00, "d_mult": 200.00},
    # 🪐 21~200 等擴充：採收斂型指數成長曲線，與 Lv.20 平滑接續，
    # MP/DC 成長率隨等級遞增逐漸放緩，避免數值暴衝到失控的天文數字。
    # Lv.200 封頂約 8兆 MP / 60 DC / 2500x(每時) / 2200x(每日)。
    # 🔮 DC 需求曲線於後續版本重新設計（原版 Lv.200 高達 6 萬 DC，但 DC 掉落量
    # 遠遠跟不上需求，導致練滿等級需要數百年）。新曲線總需求壓低至約 7,597 顆，
    # 搭配擴大後的多元掉落管道（/daily、發言、語音），目標讓中等活躍玩家
    # 約 6 個月、低活躍玩家約 6~7 個月可練滿 Lv.200。
    21: {"mp_cost": 1359878300, "dc_cost": 21, "hours": 168, "h_mult": 276.88, "d_mult": 222.44},
    22: {"mp_cost": 1619884400, "dc_cost": 22, "hours": 168, "h_mult": 291.85, "d_mult": 234.98},
    23: {"mp_cost": 1873496900, "dc_cost": 22, "hours": 168, "h_mult": 304.55, "d_mult": 245.64},
    24: {"mp_cost": 2131649000, "dc_cost": 22, "hours": 168, "h_mult": 316.09, "d_mult": 255.34},
    25: {"mp_cost": 2398969900, "dc_cost": 23, "hours": 168, "h_mult": 326.9, "d_mult": 264.44},
    26: {"mp_cost": 2678171500, "dc_cost": 23, "hours": 168, "h_mult": 337.19, "d_mult": 273.11},
    27: {"mp_cost": 2971174400, "dc_cost": 23, "hours": 168, "h_mult": 347.11, "d_mult": 281.49},
    28: {"mp_cost": 3279519400, "dc_cost": 24, "hours": 168, "h_mult": 356.74, "d_mult": 289.63},
    29: {"mp_cost": 3604550200, "dc_cost": 24, "hours": 168, "h_mult": 366.15, "d_mult": 297.59},
    30: {"mp_cost": 3947506300, "dc_cost": 24, "hours": 168, "h_mult": 375.39, "d_mult": 305.4},
    31: {"mp_cost": 4309574000, "dc_cost": 25, "hours": 168, "h_mult": 384.47, "d_mult": 313.11},
    32: {"mp_cost": 4691917800, "dc_cost": 25, "hours": 168, "h_mult": 393.45, "d_mult": 320.72},
    33: {"mp_cost": 5095700000, "dc_cost": 25, "hours": 168, "h_mult": 402.32, "d_mult": 328.26},
    34: {"mp_cost": 5522094100, "dc_cost": 25, "hours": 168, "h_mult": 411.12, "d_mult": 335.74},
    35: {"mp_cost": 5972294700, "dc_cost": 26, "hours": 168, "h_mult": 419.86, "d_mult": 343.17},
    36: {"mp_cost": 6447523900, "dc_cost": 26, "hours": 168, "h_mult": 428.54, "d_mult": 350.56},
    37: {"mp_cost": 6949037700, "dc_cost": 26, "hours": 168, "h_mult": 437.18, "d_mult": 357.93},
    38: {"mp_cost": 7478129900, "dc_cost": 26, "hours": 168, "h_mult": 445.79, "d_mult": 365.27},
    39: {"mp_cost": 8036136400, "dc_cost": 27, "hours": 168, "h_mult": 454.37, "d_mult": 372.6},
    40: {"mp_cost": 8624438300, "dc_cost": 27, "hours": 168, "h_mult": 462.94, "d_mult": 379.91},
    41: {"mp_cost": 9244465100, "dc_cost": 27, "hours": 168, "h_mult": 471.49, "d_mult": 387.23},
    42: {"mp_cost": 9897697700, "dc_cost": 27, "hours": 168, "h_mult": 480.03, "d_mult": 394.54},
    43: {"mp_cost": 10585670900, "dc_cost": 28, "hours": 168, "h_mult": 488.57, "d_mult": 401.85},
    44: {"mp_cost": 11309975900, "dc_cost": 28, "hours": 168, "h_mult": 497.11, "d_mult": 409.17},
    45: {"mp_cost": 12072263400, "dc_cost": 28, "hours": 168, "h_mult": 505.66, "d_mult": 416.49},
    46: {"mp_cost": 12874245500, "dc_cost": 28, "hours": 168, "h_mult": 514.21, "d_mult": 423.83},
    47: {"mp_cost": 13717698700, "dc_cost": 28, "hours": 168, "h_mult": 522.77, "d_mult": 431.18},
    48: {"mp_cost": 14604466300, "dc_cost": 29, "hours": 168, "h_mult": 531.35, "d_mult": 438.55},
    49: {"mp_cost": 15536461000, "dc_cost": 29, "hours": 168, "h_mult": 539.94, "d_mult": 445.94},
    50: {"mp_cost": 16515667600, "dc_cost": 29, "hours": 168, "h_mult": 548.55, "d_mult": 453.35},
    51: {"mp_cost": 17544145400, "dc_cost": 29, "hours": 168, "h_mult": 557.19, "d_mult": 460.78},
    52: {"mp_cost": 18624031500, "dc_cost": 30, "hours": 168, "h_mult": 565.84, "d_mult": 468.24},
    53: {"mp_cost": 19757542800, "dc_cost": 30, "hours": 168, "h_mult": 574.52, "d_mult": 475.72},
    54: {"mp_cost": 20946979800, "dc_cost": 30, "hours": 168, "h_mult": 583.22, "d_mult": 483.23},
    55: {"mp_cost": 22194728700, "dc_cost": 30, "hours": 168, "h_mult": 591.96, "d_mult": 490.77},
    56: {"mp_cost": 23503264900, "dc_cost": 30, "hours": 168, "h_mult": 600.72, "d_mult": 498.33},
    57: {"mp_cost": 24875155900, "dc_cost": 31, "hours": 168, "h_mult": 609.51, "d_mult": 505.93},
    58: {"mp_cost": 26313064900, "dc_cost": 31, "hours": 168, "h_mult": 618.34, "d_mult": 513.56},
    59: {"mp_cost": 27819753300, "dc_cost": 31, "hours": 168, "h_mult": 627.19, "d_mult": 521.23},
    60: {"mp_cost": 29398084900, "dc_cost": 31, "hours": 168, "h_mult": 636.09, "d_mult": 528.93},
    61: {"mp_cost": 31051028700, "dc_cost": 31, "hours": 168, "h_mult": 645.01, "d_mult": 536.66},
    62: {"mp_cost": 32781663100, "dc_cost": 32, "hours": 168, "h_mult": 653.98, "d_mult": 544.43},
    63: {"mp_cost": 34593179100, "dc_cost": 32, "hours": 168, "h_mult": 662.98, "d_mult": 552.24},
    64: {"mp_cost": 36488884300, "dc_cost": 32, "hours": 168, "h_mult": 672.03, "d_mult": 560.08},
    65: {"mp_cost": 38472206700, "dc_cost": 32, "hours": 168, "h_mult": 681.11, "d_mult": 567.97},
    66: {"mp_cost": 40546699000, "dc_cost": 32, "hours": 168, "h_mult": 690.23, "d_mult": 575.89},
    67: {"mp_cost": 42716042500, "dc_cost": 33, "hours": 168, "h_mult": 699.39, "d_mult": 583.86},
    68: {"mp_cost": 44984051200, "dc_cost": 33, "hours": 168, "h_mult": 708.6, "d_mult": 591.86},
    69: {"mp_cost": 47354676800, "dc_cost": 33, "hours": 168, "h_mult": 717.85, "d_mult": 599.91},
    70: {"mp_cost": 49832012400, "dc_cost": 33, "hours": 168, "h_mult": 727.14, "d_mult": 608.0},
    71: {"mp_cost": 52420298100, "dc_cost": 33, "hours": 168, "h_mult": 736.48, "d_mult": 616.13},
    72: {"mp_cost": 55123924900, "dc_cost": 34, "hours": 168, "h_mult": 745.87, "d_mult": 624.31},
    73: {"mp_cost": 57947440400, "dc_cost": 34, "hours": 168, "h_mult": 755.3, "d_mult": 632.53},
    74: {"mp_cost": 60895553400, "dc_cost": 34, "hours": 168, "h_mult": 764.77, "d_mult": 640.8},
    75: {"mp_cost": 63973139300, "dc_cost": 34, "hours": 168, "h_mult": 774.3, "d_mult": 649.11},
    76: {"mp_cost": 67185245800, "dc_cost": 35, "hours": 168, "h_mult": 783.87, "d_mult": 657.47},
    77: {"mp_cost": 70537097800, "dc_cost": 35, "hours": 168, "h_mult": 793.5, "d_mult": 665.88},
    78: {"mp_cost": 74034104000, "dc_cost": 35, "hours": 168, "h_mult": 803.17, "d_mult": 674.34},
    79: {"mp_cost": 77681861900, "dc_cost": 35, "hours": 168, "h_mult": 812.89, "d_mult": 682.84},
    80: {"mp_cost": 81486164700, "dc_cost": 35, "hours": 168, "h_mult": 822.66, "d_mult": 691.39},
    81: {"mp_cost": 85453007000, "dc_cost": 36, "hours": 168, "h_mult": 832.49, "d_mult": 699.99},
    82: {"mp_cost": 89588591500, "dc_cost": 36, "hours": 168, "h_mult": 842.36, "d_mult": 708.64},
    83: {"mp_cost": 93899335600, "dc_cost": 36, "hours": 168, "h_mult": 852.29, "d_mult": 717.34},
    84: {"mp_cost": 98391878200, "dc_cost": 36, "hours": 168, "h_mult": 862.27, "d_mult": 726.09},
    85: {"mp_cost": 103073086800, "dc_cost": 36, "hours": 168, "h_mult": 872.31, "d_mult": 734.9},
    86: {"mp_cost": 107950064700, "dc_cost": 37, "hours": 168, "h_mult": 882.4, "d_mult": 743.75},
    87: {"mp_cost": 113030158500, "dc_cost": 37, "hours": 168, "h_mult": 892.54, "d_mult": 752.66},
    88: {"mp_cost": 118320965600, "dc_cost": 37, "hours": 168, "h_mult": 902.74, "d_mult": 761.62},
    89: {"mp_cost": 123830342300, "dc_cost": 37, "hours": 168, "h_mult": 913.0, "d_mult": 770.63},
    90: {"mp_cost": 129566412100, "dc_cost": 37, "hours": 168, "h_mult": 923.31, "d_mult": 779.69},
    91: {"mp_cost": 135537573500, "dc_cost": 38, "hours": 168, "h_mult": 933.68, "d_mult": 788.81},
    92: {"mp_cost": 141752509300, "dc_cost": 38, "hours": 168, "h_mult": 944.1, "d_mult": 797.99},
    93: {"mp_cost": 148220194800, "dc_cost": 38, "hours": 168, "h_mult": 954.59, "d_mult": 807.22},
    94: {"mp_cost": 154949907500, "dc_cost": 38, "hours": 168, "h_mult": 965.13, "d_mult": 816.5},
    95: {"mp_cost": 161951236100, "dc_cost": 38, "hours": 168, "h_mult": 975.73, "d_mult": 825.84},
    96: {"mp_cost": 169234090300, "dc_cost": 38, "hours": 168, "h_mult": 986.39, "d_mult": 835.24},
    97: {"mp_cost": 176808711000, "dc_cost": 39, "hours": 168, "h_mult": 997.11, "d_mult": 844.7},
    98: {"mp_cost": 184685679800, "dc_cost": 39, "hours": 168, "h_mult": 1007.88, "d_mult": 854.21},
    99: {"mp_cost": 192875930300, "dc_cost": 39, "hours": 168, "h_mult": 1018.72, "d_mult": 863.78},
    100: {"mp_cost": 201390758700, "dc_cost": 39, "hours": 168, "h_mult": 1029.62, "d_mult": 873.4},
    101: {"mp_cost": 210241834400, "dc_cost": 39, "hours": 168, "h_mult": 1040.59, "d_mult": 883.09},
    102: {"mp_cost": 219441212300, "dc_cost": 40, "hours": 168, "h_mult": 1051.61, "d_mult": 892.83},
    103: {"mp_cost": 229001343900, "dc_cost": 40, "hours": 168, "h_mult": 1062.69, "d_mult": 902.64},
    104: {"mp_cost": 238935090000, "dc_cost": 40, "hours": 168, "h_mult": 1073.84, "d_mult": 912.5},
    105: {"mp_cost": 249255732500, "dc_cost": 40, "hours": 168, "h_mult": 1085.05, "d_mult": 922.42},
    106: {"mp_cost": 259976987700, "dc_cost": 40, "hours": 168, "h_mult": 1096.33, "d_mult": 932.41},
    107: {"mp_cost": 271113019400, "dc_cost": 41, "hours": 168, "h_mult": 1107.67, "d_mult": 942.45},
    108: {"mp_cost": 282678452400, "dc_cost": 41, "hours": 168, "h_mult": 1119.07, "d_mult": 952.56},
    109: {"mp_cost": 294688386400, "dc_cost": 41, "hours": 168, "h_mult": 1130.54, "d_mult": 962.73},
    110: {"mp_cost": 307158410200, "dc_cost": 41, "hours": 168, "h_mult": 1142.07, "d_mult": 972.96},
    111: {"mp_cost": 320104617000, "dc_cost": 41, "hours": 168, "h_mult": 1153.67, "d_mult": 983.25},
    112: {"mp_cost": 333543618800, "dc_cost": 42, "hours": 168, "h_mult": 1165.34, "d_mult": 993.6},
    113: {"mp_cost": 347492562500, "dc_cost": 42, "hours": 168, "h_mult": 1177.07, "d_mult": 1004.02},
    114: {"mp_cost": 361969145800, "dc_cost": 42, "hours": 168, "h_mult": 1188.87, "d_mult": 1014.51},
    115: {"mp_cost": 376991633700, "dc_cost": 42, "hours": 168, "h_mult": 1200.74, "d_mult": 1025.05},
    116: {"mp_cost": 392578875000, "dc_cost": 42, "hours": 168, "h_mult": 1212.67, "d_mult": 1035.67},
    117: {"mp_cost": 408750320500, "dc_cost": 43, "hours": 168, "h_mult": 1224.67, "d_mult": 1046.34},
    118: {"mp_cost": 425526040300, "dc_cost": 43, "hours": 168, "h_mult": 1236.74, "d_mult": 1057.09},
    119: {"mp_cost": 442926742400, "dc_cost": 43, "hours": 168, "h_mult": 1248.88, "d_mult": 1067.9},
    120: {"mp_cost": 460973791300, "dc_cost": 43, "hours": 168, "h_mult": 1261.09, "d_mult": 1078.77},
    121: {"mp_cost": 479689228000, "dc_cost": 43, "hours": 168, "h_mult": 1273.37, "d_mult": 1089.71},
    122: {"mp_cost": 499095789600, "dc_cost": 44, "hours": 168, "h_mult": 1285.72, "d_mult": 1100.72},
    123: {"mp_cost": 519216929700, "dc_cost": 44, "hours": 168, "h_mult": 1298.15, "d_mult": 1111.8},
    124: {"mp_cost": 540076839800, "dc_cost": 44, "hours": 168, "h_mult": 1310.64, "d_mult": 1122.94},
    125: {"mp_cost": 561700470700, "dc_cost": 44, "hours": 168, "h_mult": 1323.2, "d_mult": 1134.15},
    126: {"mp_cost": 584113555100, "dc_cost": 44, "hours": 168, "h_mult": 1335.84, "d_mult": 1145.43},
    127: {"mp_cost": 607342630200, "dc_cost": 45, "hours": 168, "h_mult": 1348.54, "d_mult": 1156.78},
    128: {"mp_cost": 631415061200, "dc_cost": 45, "hours": 168, "h_mult": 1361.33, "d_mult": 1168.2},
    129: {"mp_cost": 656359065700, "dc_cost": 45, "hours": 168, "h_mult": 1374.18, "d_mult": 1179.69},
    130: {"mp_cost": 682203738200, "dc_cost": 45, "hours": 168, "h_mult": 1387.11, "d_mult": 1191.25},
    131: {"mp_cost": 708979076000, "dc_cost": 46, "hours": 168, "h_mult": 1400.11, "d_mult": 1202.88},
    132: {"mp_cost": 736716005200, "dc_cost": 46, "hours": 168, "h_mult": 1413.18, "d_mult": 1214.58},
    133: {"mp_cost": 765446407300, "dc_cost": 46, "hours": 168, "h_mult": 1426.34, "d_mult": 1226.35},
    134: {"mp_cost": 795203147500, "dc_cost": 46, "hours": 168, "h_mult": 1439.56, "d_mult": 1238.2},
    135: {"mp_cost": 826020102700, "dc_cost": 46, "hours": 168, "h_mult": 1452.86, "d_mult": 1250.12},
    136: {"mp_cost": 857932190500, "dc_cost": 47, "hours": 168, "h_mult": 1466.24, "d_mult": 1262.11},
    137: {"mp_cost": 890975399600, "dc_cost": 47, "hours": 168, "h_mult": 1479.7, "d_mult": 1274.17},
    138: {"mp_cost": 925186820600, "dc_cost": 47, "hours": 168, "h_mult": 1493.23, "d_mult": 1286.31},
    139: {"mp_cost": 960604677000, "dc_cost": 47, "hours": 168, "h_mult": 1506.84, "d_mult": 1298.52},
    140: {"mp_cost": 997268358300, "dc_cost": 47, "hours": 168, "h_mult": 1520.52, "d_mult": 1310.8},
    141: {"mp_cost": 1035218453200, "dc_cost": 48, "hours": 168, "h_mult": 1534.29, "d_mult": 1323.16},
    142: {"mp_cost": 1074496783800, "dc_cost": 48, "hours": 168, "h_mult": 1548.13, "d_mult": 1335.59},
    143: {"mp_cost": 1115146440500, "dc_cost": 48, "hours": 168, "h_mult": 1562.05, "d_mult": 1348.11},
    144: {"mp_cost": 1157211818700, "dc_cost": 48, "hours": 168, "h_mult": 1576.05, "d_mult": 1360.69},
    145: {"mp_cost": 1200738655400, "dc_cost": 48, "hours": 168, "h_mult": 1590.13, "d_mult": 1373.35},
    146: {"mp_cost": 1245774067600, "dc_cost": 49, "hours": 168, "h_mult": 1604.3, "d_mult": 1386.09},
    147: {"mp_cost": 1292366591000, "dc_cost": 49, "hours": 168, "h_mult": 1618.54, "d_mult": 1398.91},
    148: {"mp_cost": 1340566220800, "dc_cost": 49, "hours": 168, "h_mult": 1632.86, "d_mult": 1411.8},
    149: {"mp_cost": 1390424452200, "dc_cost": 49, "hours": 168, "h_mult": 1647.27, "d_mult": 1424.78},
    150: {"mp_cost": 1441994323200, "dc_cost": 49, "hours": 168, "h_mult": 1661.75, "d_mult": 1437.83},
    151: {"mp_cost": 1495330458000, "dc_cost": 50, "hours": 168, "h_mult": 1676.32, "d_mult": 1450.96},
    152: {"mp_cost": 1550489111500, "dc_cost": 50, "hours": 168, "h_mult": 1690.97, "d_mult": 1464.17},
    153: {"mp_cost": 1607528215000, "dc_cost": 50, "hours": 168, "h_mult": 1705.71, "d_mult": 1477.46},
    154: {"mp_cost": 1666507423500, "dc_cost": 50, "hours": 168, "h_mult": 1720.52, "d_mult": 1490.82},
    155: {"mp_cost": 1727488163800, "dc_cost": 50, "hours": 168, "h_mult": 1735.43, "d_mult": 1504.27},
    156: {"mp_cost": 1790533683900, "dc_cost": 51, "hours": 168, "h_mult": 1750.41, "d_mult": 1517.8},
    157: {"mp_cost": 1855709104200, "dc_cost": 51, "hours": 168, "h_mult": 1765.49, "d_mult": 1531.42},
    158: {"mp_cost": 1923081469200, "dc_cost": 51, "hours": 168, "h_mult": 1780.64, "d_mult": 1545.11},
    159: {"mp_cost": 1992719801600, "dc_cost": 51, "hours": 168, "h_mult": 1795.88, "d_mult": 1558.89},
    160: {"mp_cost": 2064695157000, "dc_cost": 51, "hours": 168, "h_mult": 1811.21, "d_mult": 1572.75},
    161: {"mp_cost": 2139080680000, "dc_cost": 52, "hours": 168, "h_mult": 1826.63, "d_mult": 1586.69},
    162: {"mp_cost": 2215951662900, "dc_cost": 52, "hours": 168, "h_mult": 1842.13, "d_mult": 1600.71},
    163: {"mp_cost": 2295385604500, "dc_cost": 52, "hours": 168, "h_mult": 1857.72, "d_mult": 1614.82},
    164: {"mp_cost": 2377462271100, "dc_cost": 52, "hours": 168, "h_mult": 1873.4, "d_mult": 1629.02},
    165: {"mp_cost": 2462263759500, "dc_cost": 52, "hours": 168, "h_mult": 1889.16, "d_mult": 1643.3},
    166: {"mp_cost": 2549874560700, "dc_cost": 53, "hours": 168, "h_mult": 1905.02, "d_mult": 1657.66},
    167: {"mp_cost": 2640381626200, "dc_cost": 53, "hours": 168, "h_mult": 1920.96, "d_mult": 1672.11},
    168: {"mp_cost": 2733874435500, "dc_cost": 53, "hours": 168, "h_mult": 1936.99, "d_mult": 1686.65},
    169: {"mp_cost": 2830445065300, "dc_cost": 53, "hours": 168, "h_mult": 1953.12, "d_mult": 1701.27},
    170: {"mp_cost": 2930188260800, "dc_cost": 54, "hours": 168, "h_mult": 1969.33, "d_mult": 1715.98},
    171: {"mp_cost": 3033201508700, "dc_cost": 54, "hours": 168, "h_mult": 1985.63, "d_mult": 1730.77},
    172: {"mp_cost": 3139585112400, "dc_cost": 54, "hours": 168, "h_mult": 2002.03, "d_mult": 1745.66},
    173: {"mp_cost": 3249442268200, "dc_cost": 54, "hours": 168, "h_mult": 2018.52, "d_mult": 1760.63},
    174: {"mp_cost": 3362879144900, "dc_cost": 54, "hours": 168, "h_mult": 2035.09, "d_mult": 1775.7},
    175: {"mp_cost": 3480004964200, "dc_cost": 55, "hours": 168, "h_mult": 2051.77, "d_mult": 1790.85},
    176: {"mp_cost": 3600932083900, "dc_cost": 55, "hours": 168, "h_mult": 2068.53, "d_mult": 1806.09},
    177: {"mp_cost": 3725776082700, "dc_cost": 55, "hours": 168, "h_mult": 2085.39, "d_mult": 1821.42},
    178: {"mp_cost": 3854655848100, "dc_cost": 55, "hours": 168, "h_mult": 2102.34, "d_mult": 1836.84},
    179: {"mp_cost": 3987693665400, "dc_cost": 55, "hours": 168, "h_mult": 2119.39, "d_mult": 1852.36},
    180: {"mp_cost": 4125015309900, "dc_cost": 56, "hours": 168, "h_mult": 2136.53, "d_mult": 1867.96},
    181: {"mp_cost": 4266750140900, "dc_cost": 56, "hours": 168, "h_mult": 2153.77, "d_mult": 1883.66},
    182: {"mp_cost": 4413031198500, "dc_cost": 56, "hours": 168, "h_mult": 2171.1, "d_mult": 1899.45},
    183: {"mp_cost": 4563995302600, "dc_cost": 56, "hours": 168, "h_mult": 2188.53, "d_mult": 1915.33},
    184: {"mp_cost": 4719783154600, "dc_cost": 57, "hours": 168, "h_mult": 2206.06, "d_mult": 1931.31},
    185: {"mp_cost": 4880539441600, "dc_cost": 57, "hours": 168, "h_mult": 2223.68, "d_mult": 1947.38},
    186: {"mp_cost": 5046412943400, "dc_cost": 57, "hours": 168, "h_mult": 2241.4, "d_mult": 1963.54},
    187: {"mp_cost": 5217556642000, "dc_cost": 57, "hours": 168, "h_mult": 2259.22, "d_mult": 1979.8},
    188: {"mp_cost": 5394127834200, "dc_cost": 57, "hours": 168, "h_mult": 2277.14, "d_mult": 1996.15},
    189: {"mp_cost": 5576288246600, "dc_cost": 58, "hours": 168, "h_mult": 2295.15, "d_mult": 2012.6},
    190: {"mp_cost": 5764204154000, "dc_cost": 58, "hours": 168, "h_mult": 2313.27, "d_mult": 2029.15},
    191: {"mp_cost": 5958046500600, "dc_cost": 58, "hours": 168, "h_mult": 2331.49, "d_mult": 2045.79},
    192: {"mp_cost": 6157991024300, "dc_cost": 58, "hours": 168, "h_mult": 2349.8, "d_mult": 2062.53},
    193: {"mp_cost": 6364218384100, "dc_cost": 58, "hours": 168, "h_mult": 2368.22, "d_mult": 2079.37},
    194: {"mp_cost": 6576914290600, "dc_cost": 59, "hours": 168, "h_mult": 2386.74, "d_mult": 2096.3},
    195: {"mp_cost": 6796269640100, "dc_cost": 59, "hours": 168, "h_mult": 2405.36, "d_mult": 2113.34},
    196: {"mp_cost": 7022480652100, "dc_cost": 59, "hours": 168, "h_mult": 2424.08, "d_mult": 2130.47},
    197: {"mp_cost": 7255749009500, "dc_cost": 59, "hours": 168, "h_mult": 2442.9, "d_mult": 2147.7},
    198: {"mp_cost": 7496282003800, "dc_cost": 60, "hours": 168, "h_mult": 2461.83, "d_mult": 2165.03},
    199: {"mp_cost": 7744292682200, "dc_cost": 60, "hours": 168, "h_mult": 2480.86, "d_mult": 2182.47},
    200: {"mp_cost": 8000000000000, "dc_cost": 60, "hours": 168, "h_mult": 2500.0, "d_mult": 2200.0},
}

LEVEL_CAP = 200  # 玩家可升級的最高萌力階級上限


def format_cn_number(n: int) -> str:
    """
    🪐 巨大數值中文單位格式化（萬 / 億 / 兆）。
    因 21~200 等的 MP/DC 費用會成長到萬億規模，直接印出完整阿拉伯數字
    會在 Discord embed 中過長甚至觸發字數限制，故統一改用中文單位顯示。
    例：12345 -> "1.23萬"；987654321 -> "9.88億"；8000000000000 -> "8兆"
    1萬以下的小數字維持原樣（加千分位逗號），不套用單位。
    """
    n = int(n)
    sign = "-" if n < 0 else ""
    n = abs(n)

    def _fmt(val: float, unit: str) -> str:
        # 四捨五入到小數點後兩位後，若剛好湊整數則不顯示多餘的 .00
        rounded = round(val, 2)
        return f"{sign}{rounded:.2f}{unit}" if rounded % 1 else f"{sign}{int(rounded)}{unit}"

    if n < 10_000:
        return f"{sign}{n:,}"

    # 四捨五入後若達到下一個單位的門檔（例如 9999.996 萬會被印成 10000.00萬），
    # 直接升級到下一個單位重新計算，避免顯示出「10000.00萬」這種未進位的數字。
    val_wan = round(n / 10_000, 2)
    if val_wan < 10_000:
        return _fmt(val_wan, "萬")

    val_yi = round(n / 100_000_000, 2)
    if val_yi < 10_000:
        return _fmt(val_yi, "億")

    val_zhao = round(n / 1_000_000_000_000, 2)
    return _fmt(val_zhao, "兆")


def get_reclaim_discount_factor(level: int) -> float:
    """
    🔮 補簽階級折扣係數（指數衰減版，1~200 等皆適用）。

    原公式 max(1.045 - level*0.045, 0.2) 是針對 1~20 等設計的線性公式，
    Lv.20 以上會直接卡死在下限 0.2，等級越高完全沒有「越貴族越便宜」的效果。

    擴充到 200 等後改用指數衰減：
        discount(lv) = floor + (1 - floor) * e^(-k * (lv - 1))
    其中 floor=0.02（永不到 0），k 經校正讓 Lv.20 約落在 0.35
    （介於舊值 0.2 與「整段平滑但衝擊現有玩家」版本的 0.61 之間，取折衷）。
    曲線特性：Lv.1~15 與舊公式幾乎相同（幾乎無感），Lv.20 起比舊版貴，
    但 Lv.30 起逐漸比舊版更便宜，直到 Lv.200 趨近 0.02 的下限。
    """
    floor = 0.02
    k = 0.05729  # 校正後的衰減速率，使 Lv.20 ≈ 0.35
    return floor + (1 - floor) * math.exp(-k * (level - 1))

# 全服發電廠設定
PP_TARGET = 500000  # 滿載目標：50萬 MP

DEFAULT_PROFILE = {
    "profession": "無業遊民",
    "deposit": 100,           # 舊數據相容 (系統內等同於隨時取用的萌力值)
    "sha_coin": 0,            # 舊數據相容 (系統內等同於隨時取用的次元結晶)
    "friends_count": 0,
    "achievements": [],
    
    # 🪐 新自創雙幣制核心欄位
    "moe_point": 100,         # 萌力值 (MP)
    "dimension_crystal": 0,   # 次元結晶 (DC)
    
    # 簽到等級控制欄位
    "sign_level": 1,          # 簽到/萌力階級
    "last_hourly_time": 0.0,  # 上次提取時間戳
    "last_daily_time": "",    # 上次每日簽到日期 (YYYY-MM-DD)
    "streak_days": 0,         # 連續簽到天數
    "total_reclaims": 0,      # 歷史總補簽次數 N

    # 🔮 DC 多元掉落管道追蹤欄位
    "voice_join_time": 0.0,   # 本次進入語音頻道的時間戳 (0 表示目前不在語音中)
}

_lock = asyncio.Lock()

def _load_raw() -> dict:
    if not os.path.exists(PROFILES_PATH):
        return {}
    try:
        with open(PROFILES_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.error(f"❌ [ProfileStore] 讀取 profiles.json 失敗: {e}")
        return {}

def _save_raw(data: dict):
    try:
        tmp_path = PROFILES_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        os.replace(tmp_path, PROFILES_PATH)
    except Exception as e:
        log.error(f"❌ [ProfileStore] 寫入 profiles.json 失敗: {e}")

def _load_global() -> dict:
    if not os.path.exists(GLOBAL_STORE_PATH):
        return {"total_contributed": 0, "buff_end_time": 0.0}
    try:
        with open(GLOBAL_STORE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"total_contributed": 0, "buff_end_time": 0.0}

def _save_global(data: dict):
    try:
        with open(GLOBAL_STORE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        log.error(f"❌ [ProfileStore] 寫入 global_economy.json 失敗: {e}")

# ==================== 基礎玩家 API ====================

async def get_profile(user_id: int) -> dict:
    """取得玩家個人檔案，並動態修復與相容全新自創幣架構"""
    async with _lock:
        data = _load_raw()
        uid_str = str(user_id)
        if uid_str not in data:
            data[uid_str] = DEFAULT_PROFILE.copy()
            data[uid_str]["last_hourly_time"] = time.time()  # 新玩家初始化提取時間
            _save_raw(data)
        
        user_data = data[uid_str]
        # 自動補齊新欄位
        for key, val in DEFAULT_PROFILE.items():
            if key not in user_data:
                user_data[key] = val
        
        # ⭐ 數據相容大核心：如果老舊存款有錢，自動等值繼承到萌力值與次元結晶
        if user_data["deposit"] > 100 and user_data["moe_point"] == 100:
            user_data["moe_point"] = user_data["deposit"]
        if user_data["sha_coin"] > 0 and user_data["dimension_crystal"] == 0:
            user_data["dimension_crystal"] = user_data["sha_coin"]
            
        return user_data

async def update_profession(user_id: int, profession: str):
    """更新玩家職業稱號"""
    async with _lock:
        data = _load_raw()
        uid_str = str(user_id)
        if uid_str not in data:
            data[uid_str] = DEFAULT_PROFILE.copy()
        data[uid_str]["profession"] = profession[:15]
        _save_raw(data)

async def add_mp(user_id: int, amount: int) -> int:
    """增減玩家的【萌力值 (MP)】(連動舊型 deposit 方便外部名片卡讀取)"""
    async with _lock:
        data = _load_raw()
        uid_str = str(user_id)
        if uid_str not in data:
            data[uid_str] = DEFAULT_PROFILE.copy()
        
        current = data[uid_str].get("moe_point", 0)
        new_val = max(0, current + amount)
        data[uid_str]["moe_point"] = new_val
        data[uid_str]["deposit"] = new_val  # 雙向同步同步
        _save_raw(data)
        return new_val

async def add_dc(user_id: int, amount: int) -> int:
    """增減玩家的【次元結晶 (DC)】(連動舊型 sha_coin)"""
    async with _lock:
        data = _load_raw()
        uid_str = str(user_id)
        if uid_str not in data:
            data[uid_str] = DEFAULT_PROFILE.copy()
        
        current = data[uid_str].get("dimension_crystal", 0)
        new_val = max(0, current + amount)
        data[uid_str]["dimension_crystal"] = new_val
        data[uid_str]["sha_coin"] = new_val  # 雙向同步同步
        _save_raw(data)
        return new_val

async def add_achievement(user_id: int, achievement_name: str) -> bool:
    """為玩家增添成就勳章"""
    async with _lock:
        data = _load_raw()
        uid_str = str(user_id)
        if uid_str not in data:
            data[uid_str] = DEFAULT_PROFILE.copy()
        
        achievements = data[uid_str].get("achievements", [])
        if achievement_name in achievements:
            return False
        achievements.append(achievement_name)
        data[uid_str]["achievements"] = achievements
        _save_raw(data)
        return True

def get_custom_bg_path(user_id: int) -> Optional[str]:
    p = os.path.join(BACKGROUND_DIR, f"{user_id}.png")
    return p if os.path.exists(p) else None


# ==================== 🛠️ 創新互動核心系統 API ====================

def get_profession_talent(profession: str) -> str:
    """根據現有名稱，智慧歸類四大專屬互動流派天賦"""
    p = profession.strip()
    if any(k in p for k in ["無業", "遊民", "新手", "平民"]):
        return "平庸之福"
    if any(k in p for k in ["宅", "工程", "科技", "程式", "潛水", "魔法師", "開發"]):
        return "時空定錨"
    if any(k in p for k in ["商", "精明", "錢", "走私", "偷", "忘記"]):
        return "結晶走私"
    # 其他所有自創職業（例如：冒險者、忍者、Gay、不認識妳等）皆歸為最受歡迎的暴擊流派
    return "萌力暴走"

async def check_global_buff() -> bool:
    """檢查全服發電廠 Buff 是否正在生效中"""
    g = _load_global()
    return time.time() < g.get("buff_end_time", 0.0)

async def contribute_to_power_plant(user_id: int, amount: int) -> dict:
    """玩家注入萌力值到全服發電廠發電"""
    # 先扣錢
    await add_mp(user_id, -amount)
    
    async with _lock:
        g = _load_global()
        g["total_contributed"] += amount
        
        triggered = False
        # 如果達標，銷毀蓄能並開啟全服 24 小時倍率狂歡
        if g["total_contributed"] >= PP_TARGET:
            g["total_contributed"] = g["total_contributed"] % PP_TARGET # 餘額留到下一輪
            g["buff_end_time"] = time.time() + 86400  # 24 小時
            triggered = True
            
        _save_global(g)
        return {"current": g["total_contributed"], "target": PP_TARGET, "triggered": triggered}

async def update_hourly_time(user_id: int, new_time: float):
    """更新玩家上一次提取每小時萌力的時間"""
    async with _lock:
        data = _load_raw()
        uid_str = str(user_id)
        if uid_str in data:
            data[uid_str]["last_hourly_time"] = new_time
            _save_raw(data)

async def save_daily_signin(user_id: int, date_str: str, new_streak: int):
    """更新玩家每日簽到紀錄與連續天數"""
    async with _lock:
        data = _load_raw()
        uid_str = str(user_id)
        if uid_str in data:
            data[uid_str]["last_daily_time"] = date_str
            data[uid_str]["streak_days"] = new_streak
            _save_raw(data)

async def execute_upgrade(user_id: int, target_lvl: int, mp_cost: int, dc_cost: int):
    """扣除升級費用，並提升階級"""
    await add_mp(user_id, -mp_cost)
    await add_dc(user_id, -dc_cost)
    async with _lock:
        data = _load_raw()
        uid_str = str(user_id)
        if uid_str in data:
            data[uid_str]["sign_level"] = target_lvl
            _save_raw(data)

async def execute_reclaim(user_id: int, mp_cost: int, dc_cost: int):
    """執行時空扭轉補簽扣費，並更新總補簽次數"""
    await add_mp(user_id, -mp_cost)
    await add_dc(user_id, -dc_cost)
    async with _lock:
        data = _load_raw()
        uid_str = str(user_id)
        if uid_str in data:
            data[uid_str]["total_reclaims"] += 1
            data[uid_str]["streak_days"] += 1  # 補回天數
            _save_raw(data)


# ==================== 🔮 DC 多元掉落管道 API ====================

async def set_voice_join_time(user_id: int, join_time: float):
    """
    記錄玩家進入語音頻道的時間戳。
    join_time=0.0 表示玩家已離開語音（清空計時，避免下次進入時誤判已停留很久）。
    """
    async with _lock:
        data = _load_raw()
        uid_str = str(user_id)
        if uid_str not in data:
            data[uid_str] = DEFAULT_PROFILE.copy()
        data[uid_str]["voice_join_time"] = join_time
        _save_raw(data)


async def get_voice_join_time(user_id: int) -> float:
    """取得玩家本次進入語音頻道的時間戳，0.0 表示目前不在語音中"""
    p_data = await get_profile(user_id)
    return p_data.get("voice_join_time", 0.0)


# ==================== 🏆 全服排行榜 API ====================

LEADERBOARD_SORT_KEYS = {
    "level": "sign_level",
    "mp": "moe_point",
    "dc": "dimension_crystal",
    "streak": "streak_days",
}


async def get_leaderboard(sort_by: str = "level", limit: int = 10) -> list:
    """
    取得全服排行榜（跨伺服器，因為 profiles.json 本身就是全域儲存，
    不分玩家來自哪個伺服器，所有人共用同一份資料）。

    sort_by 對應 LEADERBOARD_SORT_KEYS 的其中一個 key：
      - "level"  -> 依萌力階級 (sign_level) 排序
      - "mp"     -> 依萌力資產 (moe_point) 排序
      - "dc"     -> 依次元結晶 (dimension_crystal) 排序
      - "streak" -> 依連續簽到天數 (streak_days) 排序

    回傳格式：[(user_id:int, value:int), ...]，已依 value 由大到小排序，最多 limit 筆。
    """
    field = LEADERBOARD_SORT_KEYS.get(sort_by, "sign_level")

    async with _lock:
        data = _load_raw()

    rankings = []
    for uid_str, user_data in data.items():
        try:
            user_id = int(uid_str)
        except (TypeError, ValueError):
            continue  # 防呆：略過任何格式異常、無法轉成整數的 key

        # 防呆：舊資料或尚未補齊欄位的玩家，缺少欄位時視為 0，不讓排行榜直接噴錯
        value = user_data.get(field, 0)
        if not isinstance(value, (int, float)):
            value = 0

        rankings.append((user_id, value))

    rankings.sort(key=lambda pair: pair[1], reverse=True)
    return rankings[:limit]