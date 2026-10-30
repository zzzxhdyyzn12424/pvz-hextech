# -*- coding: utf-8 -*-
"""
probe_seed.py —— 种子银行 / 盲盒卡的现场验证工具

逆向依据（全部静态反汇编，见 docs/hextech_values.md §盲盒卡）：

    0x40B1DA  mov ecx, [ebp+0x144]      ; SeedBank = Board->mSeedBank
    0x40B1E0  mov [ecx+0x24], eax       ; mNumPackets
    0x40B1F6  lea esi, [ebx+edx+0x28]   ; 第一张卡在 SeedBank+0x28
    0x40B204  add ebx, 0x50             ; 步长 0x50
    0x40B20A  cmp ebx, 0x320            ; 0x320/0x50 = 最多 10 张
    0x489B57  mov [esi+0x34], edi       ; SetPacketType: mPacketType
    0x488F7D  mov [edi+0x28], eax       ; 冷却 = gPlantDefs[type].mRefreshTime

用法（先开游戏、进关卡、选好卡，保持游戏窗口在前台）：

    python tools/probe_seed.py dump
        把种子银行整个打印出来：基址、卡数、每张卡的类型 / 冷却 / 可用标志 /
        用了几次 / 卡面坐标。用来核对偏移猜得对不对。

    python tools/probe_seed.py defs
        打印 gPlantDefs 表（0x69F2C0，步长 0x24）：每种植物的
        阳光成本 / 冷却帧数 / 攻速。

    python tools/probe_seed.py watch 15
        只读观测 15 秒，盯住 0 号卡：什么时候进入冷却、什么时候恢复、
        「用了几次」什么时候 +1。用来确认 mActive / mCooling / mUses 的语义。

    python tools/probe_seed.py blind [秒]
        现场试盲盒：每 0.5 秒把 0 号卡的 mPacketType 换一种随机植物，
        然后你去点它种下去，看种出来的是不是卡面上那一种。
        ★ 这是判断「卡面会不会跟着变」的关键实验：
            种出来 ≠ 卡面  → 卡面是定死的，盲盒成立
            种出来 = 卡面  → 卡面每帧重建，得换实现方式
        跑完自动还原成一开始的类型。

注意：PVZ 失焦/最小化会自动暂停，观测期间必须让游戏窗口保持前台。
"""

import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import hextech_core as C


def _need_seeds(game):
    seeds = game.scan_seedbank()
    if not seeds:
        sb = game.seedbank()
        print("读不到种子银行。")
        print("  Board          = %s" % (hex(game.board) if game.board else "None"))
        print("  Board+0x144    = %s" % (hex(sb) if sb else "None"))
        if sb:
            print("  mNumPackets    = %s" % game.i32(sb + C.SB_NUM))
            print("  第一张卡 +0x00 = %s（应为 gLawnApp %s）"
                  % (hex(game.u32(sb + C.SB_FIRST) or 0), hex(game.gapp or 0)))
            print("  第一张卡 +0x04 = %s（应为 Board %s）"
                  % (hex(game.u32(sb + C.SB_FIRST + 4) or 0), hex(game.board)))
        print("多半是还没进关卡，或者偏移不对。")
        return []
    return seeds


def cmd_dump(game):
    seeds = _need_seeds(game)
    if not seeds:
        return 1
    sb = game.seedbank()
    print("SeedBank = %s   mNumPackets = %d" % (hex(sb), len(seeds)))
    print("  %-4s %-8s %-6s %-8s %-6s %-6s %-6s %-6s %s"
          % ("slot", "addr", "type", "名称", "cool", "active", "uses", "xy", "imit"))
    for s in seeds:
        print("  %-4d %-8s %-6d %-8s %-6d %-6d %-6d %-6s %d"
              % (s["slot"], hex(s["addr"]), s["type"],
                 C.PLANT_NAMES.get(s["type"], "?"),
                 s["cooling"], s["active"], s["uses"],
                 "%d,%d" % (s["x"], s["y"]), s["imitater"]))
    return 0


def cmd_defs(game):
    print("gPlantDefs = %s  (步长 %#x)" % (hex(C.GPLANTDEFS), C.PLANTDEF_STRIDE))
    print("  %-4s %-10s %-6s %-8s %-8s" % ("type", "名称", "成本", "冷却帧", "攻速帧"))
    for t in range(49):
        base = C.GPLANTDEFS + t * C.PLANTDEF_STRIDE
        cost = game.i32(base + C.PLANTDEF_COST)
        ref = game.i32(base + C.PLANTDEF_REFRESH)
        rate = game.i32(base + C.PLANTDEF_RATE)
        print("  %-4d %-10s %-6s %-8s %-8s"
              % (t, C.PLANT_NAMES.get(t, "?"), cost, ref, rate))
    return 0


def cmd_watch(game, seconds=15.0):
    seeds = _need_seeds(game)
    if not seeds:
        return 1
    slot = 0
    print("观测 %d 号卡 %.1f 秒（只读，不改内存）。现在去点它种一株。" % (slot, seconds))
    last = None
    t0 = time.time()
    while time.time() - t0 < seconds:
        seeds = game.scan_seedbank()
        if seeds and slot < len(seeds):
            s = seeds[slot]
            cur = (s["type"], s["cooling"], s["active"], s["uses"], s["refresh"])
            if cur != last:
                print("  %6.2fs  type=%-3d cool=%-2d active=%-2d uses=%-3d refresh=%-6d"
                      % (time.time() - t0, *cur))
                last = cur
        time.sleep(0.05)
    print("观测结束。")
    return 0


def cmd_blind(game, seconds=30.0):
    seeds = _need_seeds(game)
    if not seeds:
        return 1
    slot = 0
    addr = seeds[slot]["addr"] + C.SEED_TYPE
    orig = game.i32(addr)
    print("0 号卡原始类型 = %d（%s）" % (orig, C.PLANT_NAMES.get(orig, "?")))
    print("现在开始每 0.5 秒随机换一种，去点它种下去，看种出来是什么。")
    print("按 Ctrl+C 结束（会自动还原）。")
    rng = random.Random()
    pool = list(range(49))
    try:
        t0 = time.time()
        while time.time() - t0 < seconds:
            t = rng.choice([x for x in pool if x != game.i32(addr)])
            game.write_i32(addr, t)
            print("  → 卡面现在是 %d（%s）" % (t, C.PLANT_NAMES.get(t, "?")))
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        game.write_i32(addr, orig)
        print("已还原 0 号卡为 %d（%s）" % (orig, C.PLANT_NAMES.get(orig, "?")))
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
        if cmd == "dump":
            return cmd_dump(game)
        if cmd == "defs":
            return cmd_defs(game)
        if cmd == "watch":
            return cmd_watch(game, float(argv[1]) if len(argv) > 1 else 15.0)
        if cmd == "blind":
            return cmd_blind(game, float(argv[1]) if len(argv) > 1 else 30.0)
        print("未知命令：%s" % cmd)
        print(__doc__)
        return 2
    finally:
        game.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
