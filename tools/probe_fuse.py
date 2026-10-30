# -*- coding: utf-8 -*-
"""
probe_fuse.py —— 同格叠种实验台（「融合」的原语验证）

背景
----
融合版（B 站 @蓝飘飘fly）的招牌机制就是「两株植物叠在同一格，变成杂交体」。
那是改过 exe 的独立构建，新增了 SeedType、gPlantDefs 条目、行为函数和 reanim，
搬不过来。但**「两株叠一格」这件事，原生引擎本来就支持**：

    0x40D1A0  Board::GetPlantAt(row, col)
        call 0x41c950              ; 植物数组迭代器
        cmp  [eax+0x28], col       ; 线性比对列
        cmp  [eax+0x1c], row       ; 线性比对行
        ...跳过南瓜头的某些状态 / +0x142 非 0 / +0x134==2 / +0x141 dead

没有网格缓存数组，就是纯线性扫描。南瓜头 / 睡莲 / 花盆 / 咖啡豆本来就是
和宿主同格共存 —— 引擎原生允许一格里有多株，只是靠标志位区分谁是「格子主体」。

所以：把一株的 row/col/mX/mY 改到另一株的格子上，两株就叠住了。

用法（先开游戏、进关卡、种两株植物）
------------------------------------
    python tools\\probe_fuse.py list
        列出场上所有植物：编号 / 类型 / 行 / 列 / 坐标 / 血量。

    python tools\\probe_fuse.py fuse 1 0
        把 1 号植物叠到 0 号植物的格子上。会先把 1 号的原位置写进
        work/fuse_backup.json，随时能还原。

    python tools\\probe_fuse.py unfuse 1
        把 1 号植物搬回原来的格子。

    python tools\\probe_fuse.py restore
        把所有动过的植物全部还原。

也可以直接给地址：`fuse 0x1A2B3C4 0x1A2B510`。

要观察什么
----------
叠上之后盯这几件事：
    1. 两株是不是都还在画、都还在工作（产阳光的产、射击的射）
    2. 僵尸来啃的时候啃哪一株，另一株会不会跟着掉血
    3. 铲子铲掉一株，另一株会不会崩
    4. 阳光 / 冷却 / 过关结算有没有异常
只要 1 成立，「融合」在玩法上就成立了，剩下的都是打磨。
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import hextech_core as C

BACKUP = os.path.join(C.ROOT, "work", "fuse_backup.json")


def load_backup():
    try:
        with open(BACKUP, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_backup(d):
    os.makedirs(os.path.dirname(BACKUP), exist_ok=True)
    with open(BACKUP, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def _pick(plants, token):
    """索引或 0x 地址都能选。"""
    t = token.strip()
    if t.lower().startswith("0x"):
        addr = int(t, 16)
        for p in plants:
            if p["addr"] == addr:
                return p
        return None
    try:
        i = int(t)
    except ValueError:
        return None
    return plants[i] if 0 <= i < len(plants) else None


def cmd_list(game):
    plants = game.scan_plants()
    if not plants:
        print("场上没有植物。先种两株再试。")
        return 1
    print("  %-3s %-8s %-10s %-4s %-4s %-10s %s"
          % ("#", "addr", "类型", "行", "列", "坐标", "血量"))
    for i, p in enumerate(plants):
        print("  %-3d %-8s %-10s %-4d %-4d %-10s %d/%d"
              % (i, hex(p["addr"]), C.PLANT_NAMES.get(p["type"], "?%d" % p["type"]),
                 p["row"] + 1, p["col"] + 1,
                 "%d,%d" % (p["x"], p["y"]), p["hp"], p["hp_max"]))
    print("\n叠种：python tools\\probe_fuse.py fuse <被搬的> <目标>")
    return 0


def cmd_fuse(game, src_tok, dst_tok):
    plants = game.scan_plants()
    src, dst = _pick(plants, src_tok), _pick(plants, dst_tok)
    if src is None or dst is None:
        print("选不到植物，先跑 list 看看编号。")
        return 1
    if src["addr"] == dst["addr"]:
        print("不能叠到自己身上。")
        return 1
    bk = load_backup()
    key = hex(src["addr"])
    if key not in bk:
        bk[key] = dict(row=src["row"], col=src["col"],
                       x=src["x"], y=src["y"], type=src["type"])
        save_backup(bk)
    if not game.fuse_plant(src, dst):
        print("写入失败。")
        return 1
    print("已把 %s（%s）叠到 %s（%s）的格子 第%d行 第%d列"
          % (hex(src["addr"]), C.PLANT_NAMES.get(src["type"], "?"),
             hex(dst["addr"]), C.PLANT_NAMES.get(dst["type"], "?"),
             dst["row"] + 1, dst["col"] + 1))
    print("原位置已记到 %s，用 unfuse 还原。" % BACKUP)
    print("现在去游戏里看：两株是不是都还在、都还在干活。")
    return 0


def cmd_unfuse(game, tok):
    plants = game.scan_plants()
    p = _pick(plants, tok)
    bk = load_backup()
    key = hex(p["addr"]) if p else ("0x%X" % int(tok, 16) if tok.lower().startswith("0x") else None)
    if key is None or key not in bk:
        print("这个植物没有备份记录，没法还原。")
        return 1
    b = bk[key]
    addr = int(key, 16)
    ok = game.move_plant(addr, row=b["row"], col=b["col"], x=b["x"], y=b["y"])
    if ok:
        del bk[key]
        save_backup(bk)
        print("已还原 %s 到 第%d行 第%d列" % (key, b["row"] + 1, b["col"] + 1))
        return 0
    print("写入失败（植物可能已经没了）。")
    return 1


def cmd_restore(game):
    bk = load_backup()
    if not bk:
        print("没有需要还原的。")
        return 0
    n = 0
    for key, b in list(bk.items()):
        if game.move_plant(int(key, 16), row=b["row"], col=b["col"],
                           x=b["x"], y=b["y"]):
            n += 1
        del bk[key]
    save_backup(bk)
    print("还原了 %d 株。" % n)
    return 0


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd = argv[0]
    pid = C.find_pid()
    if not pid:
        print("找不到 %s 进程。先把游戏开起来并进入关卡。" % C.EXE_NAME)
        return 1
    game = C.Game(pid)
    print("已连上 pid=%d" % pid)
    try:
        if cmd == "list":
            return cmd_list(game)
        if cmd == "fuse" and len(argv) >= 3:
            return cmd_fuse(game, argv[1], argv[2])
        if cmd == "unfuse" and len(argv) >= 2:
            return cmd_unfuse(game, argv[1])
        if cmd == "restore":
            return cmd_restore(game)
        print("参数不对。")
        print(__doc__)
        return 2
    finally:
        game.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
