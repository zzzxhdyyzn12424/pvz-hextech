# -*- coding: utf-8 -*-
"""
probe_shoot.py —— 验证「植物攻速 = 真的多射豌豆」

原理（来自 0x45F8A9 的逆向）：

    0x45F8A9  add  [esi+0x58], -1        ; 倒计时每帧 -1
    0x45F8B3  jg   skip                  ; 没到 0 就跳过
    0x45F8BA  call 0x5af400              ; rand(0,15)
    0x45F8BF  mov  ecx, [esi+0x5c]       ; 射击间隔
    0x45F8C2  sub  ecx, eax
    0x45F8CA  mov  [esi+0x58], ecx       ; ★ 重置倒计时 = 开了一枪
    0x45F917  call 0x45ef10              ; 生成豌豆

所以「+0x58 从接近 0 跳回大数」这个动作，就是游戏自己生成了一颗豌豆。
本工具高频采样 +0x58，数它跳变的次数，直接得到「每秒几发」。

用法（先开游戏，进关卡，种一株豌豆射手，保持游戏窗口在前台）：

    python tools/probe_shoot.py show
        列出所有植物，打印它们的 +0x58 倒计时 / +0x5C 射击间隔。

    python tools/probe_shoot.py count 12
        不改任何内存，纯观测 12 秒，报告实际每秒开火次数（基准应为 ~0.67）。

    python tools/probe_shoot.py test 12
        把射击间隔改成 原值/1.25（灵巧），观测 12 秒，报告每秒开火次数，
        结束时自动还原。期望从 ~0.67 提升到 ~0.83（+25%）。

    python tools/probe_shoot.py slow 24
        验证反方向：带【大腕】的植物 +0x5C 应该是 600（150×4）。
        观测 24 秒，报告每次开火后 +0x58 被重置成的值、以及两次开火的
        实际间隔。期望：重置值 ≈ 600、间隔 ≈ 20 秒、是原版的 4 倍。

注意：PVZ 失焦/最小化会自动暂停，观测期间必须让游戏窗口保持前台。
"""

import sys
import time

import hextech_core as C


def find_shooters(game):
    """返回会射击的植物列表。"""
    out = []
    for p in game.scan_plants():
        if p["type"] not in C.OUTPUT_PLANTS:
            continue
        itv = game.i32(p["addr"] + C.P_SHOOT_INTERVAL)
        cnt = game.i32(p["addr"] + C.P_SHOOT_COUNTER)
        if itv is None or cnt is None:
            continue
        if not (1 < itv < 100000):
            continue
        p["interval"] = itv
        p["counter"] = cnt
        out.append(p)
    return out


def cmd_show(game):
    ps = find_shooters(game)
    if not ps:
        print("没找到会射击的植物。先进关卡种一株豌豆射手。")
        return
    print("%-6s %-10s %-4s %-4s %-12s %-12s" %
          ("类型", "名字", "行", "列", "倒计时+0x58", "间隔+0x5C"))
    print("-" * 56)
    for p in ps:
        print("%-6d %-10s %-4d %-4d %-12d %-12d" % (
            p["type"], C.PLANT_NAMES.get(p["type"], "?"),
            p["row"], p["col"], p["counter"], p["interval"]))


class ShotCounter:
    """把 +0x58 的读数流变成「开了几枪」。

    判定条件：上一次读数 <= 8（倒计时快归零），这一次 > 40（被重置成大数）。
    这两个阈值留了余量，因为重置值是 interval - rand(0,15)，
    最短的豌豆射手也有 150-15=135，远大于 40。
    """

    LOW = 8
    HIGH = 40

    def __init__(self):
        self.prev = None
        self.shots = 0

    def feed(self, value):
        """喂一帧读数，返回这一帧是否触发了一次开火。"""
        fired = False
        if value is not None and self.prev is not None:
            if self.prev <= self.LOW and value > self.HIGH:
                self.shots += 1
                fired = True
        self.prev = value
        return fired


def watch_shots(game, addr, seconds, interval=0.016):
    """高频采样 +0x58，数开火次数。返回 (开火次数, 实际时长)。"""
    a = addr + C.P_SHOOT_COUNTER
    c = ShotCounter()
    t0 = time.time()
    deadline = t0 + seconds
    while time.time() < deadline:
        c.feed(game.i32(a))
        time.sleep(interval)
    return c.shots, time.time() - t0


def cmd_count(game, seconds):
    ps = find_shooters(game)
    if not ps:
        print("没找到会射击的植物。先进关卡种一株豌豆射手。")
        return
    p = ps[0]
    print("观测对象：%s（行%d 列%d）  间隔 +0x5C = %d 帧" % (
        C.PLANT_NAMES.get(p["type"], "?"), p["row"], p["col"], p["interval"]))
    print("请让游戏窗口保持在前台，观测 %.1f 秒……" % seconds)
    shots, dur = watch_shots(game, p["addr"], seconds)
    rate = shots / dur if dur > 0 else 0.0
    expect = 30.0 / p["interval"] if p["interval"] else 0.0
    print()
    print("  实际开火 %d 发 / %.1f 秒 = %.3f 发/秒" % (shots, dur, rate))
    print("  理论值 30fps / %d 帧 = %.3f 发/秒" % (p["interval"], expect))
    if expect > 0 and abs(rate - expect) < expect * 0.35:
        print("  -> 吻合，说明 +0x58 的跳变确实对应真实开火。")
    elif rate < 0.05:
        print("  -> 几乎没开火。可能：游戏被暂停 / 僵尸还没进射程 / 前台丢失。")


def cmd_test(game, seconds):
    ps = find_shooters(game)
    if not ps:
        print("没找到会射击的植物。先进关卡种一株豌豆射手。")
        return
    p = ps[0]
    base = p["interval"]
    want = max(1, int(round(base / 1.25)))
    name = C.PLANT_NAMES.get(p["type"], "?")
    print("观测对象：%s（行%d 列%d）" % (name, p["row"], p["col"]))
    print("原间隔 +0x5C = %d 帧，写入 %d 帧（模拟灵巧 +25%%）" % (base, want))
    print("请让游戏窗口保持在前台……")
    print()

    game.write_i32(p["addr"] + C.P_SHOOT_INTERVAL, want)
    time.sleep(0.2)

    shots, dur = watch_shots(game, p["addr"], seconds)
    now_val = game.i32(p["addr"] + C.P_SHOOT_INTERVAL)
    rate = shots / dur if dur > 0 else 0.0

    # 还原
    game.write_i32(p["addr"] + C.P_SHOOT_INTERVAL, base)

    print("  开火 %d 发 / %.1f 秒 = %.3f 发/秒" % (shots, dur, rate))
    print("  期间 +0x5C 读数 = %s（写入的是 %d）" % (now_val, want))
    print()
    if now_val == want:
        print("  [OK] 写入的间隔没有被游戏每帧重算覆盖。")
    else:
        print("  [!!] +0x5C 被游戏改回了 %s，说明这个字段会被重算，需要换方案。" % now_val)

    base_rate = 30.0 / base
    print("  基准 %.3f 发/秒 -> 实测 %.3f 发/秒（%.2fx）" % (
        base_rate, rate, rate / base_rate if base_rate else 0))
    if rate > base_rate * 1.12:
        print("  [OK] 真的多射了豌豆，不是数值补偿。")
    else:
        print("  [!!] 没看到开火变快。检查游戏是否在前台、僵尸是否在射程内。")
    print()
    print("  已还原 +0x5C = %d" % base)


def cmd_slow(game, seconds):
    """验证「把 +0x5C 调大 = 真的少射」（大腕 / 攻速降低类）。

    和 test 相反：test 证明调小能多射，slow 证明调大能少射。
    判据有两条，缺一不可：
      1. 开火后 +0x58 被重置成的值 ≈ +0x5C（而不是被夹回原版 150）
      2. 两次开火的墙钟间隔 ≈ +0x5C / 100 秒

    ★ 帧率是 100 Hz 不是 30 Hz：游戏每秒 Update 100 次（Zombie+0x60 实测）。
      所以 150 帧 = 1.5 秒一发（0.67 发/秒），600 帧 = 6 秒一发。
      用 30 算会得出「20 秒一发」的错误期望。
    """
    fps = C.Engine.Z_SPEED_HZ
    ps = find_shooters(game)
    if not ps:
        print("没找到会射击的植物。先进关卡种一株豌豆射手。")
        return

    print("观测 %d 株植物 %.0f 秒（游戏 %.0f Update/秒，所以 N 帧 = N/%.0f 秒）"
          % (len(ps), seconds, fps, fps))
    print("请让游戏窗口保持在前台……")
    print()

    # 同时采样所有植物：每个 addr 一个「上一次读数」
    prev = {p["addr"]: game.i32(p["addr"] + C.P_SHOOT_COUNTER) for p in ps}
    resets = {p["addr"]: [] for p in ps}
    t0 = time.time()
    while time.time() - t0 < seconds:
        for p in ps:
            a = p["addr"] + C.P_SHOOT_COUNTER
            v = game.i32(a)
            pv = prev[p["addr"]]
            if v is not None and pv is not None and v - pv > 20:
                resets[p["addr"]].append((time.time() - t0, v))
            prev[p["addr"]] = v
        time.sleep(0.012)
    dur = time.time() - t0

    print("%-4s %-3s %-3s %-8s %-6s %-9s %-9s %-9s" %
          ("行", "列", "间隔", "理论间隔", "开火", "实测间隔", "重置值", "判定"))
    print("-" * 66)
    for p in sorted(ps, key=lambda x: -x["interval"]):
        rs = resets[p["addr"]]
        want = p["interval"] / fps
        gaps = [rs[i + 1][0] - rs[i][0] for i in range(len(rs) - 1)]
        avg = sum(gaps) / len(gaps) if gaps else None
        top = max((v for _, v in rs), default=None)
        if avg is None:
            verdict = "开火<2次"
        elif abs(avg - want) <= max(0.35, want * 0.25):
            verdict = "OK"
        else:
            verdict = "偏差大"
        print("%-4d %-3d %-8d %-9.2f %-6d %-9s %-9s %-9s" % (
            p["row"], p["col"], p["interval"], want, len(rs),
            ("%.2f" % avg) if avg else "-",
            top if top is not None else "-", verdict))

    print()
    slow = [p for p in ps if p["interval"] >= 500]
    fast = [p for p in ps if p["interval"] <= 200]
    if slow and fast:
        ratio = (sum(p["interval"] for p in slow) / len(slow)) / \
                (sum(p["interval"] for p in fast) / len(fast))
        print("带大腕的 %d 株 平均间隔 %.0f 帧，原版 %d 株 平均 %.0f 帧，比值 %.2fx"
              % (len(slow), sum(p["interval"] for p in slow) / len(slow),
                 len(fast), sum(p["interval"] for p in fast) / len(fast), ratio))
        ok_reset = any(
            max((v for _, v in resets[p["addr"]]), default=0) >= p["interval"] - 16
            for p in slow if resets[p["addr"]])
        if ok_reset and 3.5 <= ratio <= 4.5:
            print("[OK] 攻速确实降了：+0x58 被重置到 ~%d，游戏按这个帧数在等。"
                  % slow[0]["interval"])
            print("     不是数值补偿，是真的少生成了豌豆。")
        else:
            print("[!!] 没验证到，看上面表格里哪一行偏差大。")
    else:
        print("场上没有同时存在「带大腕」和「原版」的植物，只能看表格单行。")


def main(argv):
    if not argv:
        print(__doc__)
        return 0
    cmd = argv[0]
    if cmd in ("-h", "--help", "help"):
        print(__doc__)
        return 0

    pid = C.find_pid()
    if not pid:
        print("找不到 %s 进程。先把游戏开起来并进入关卡。" % C.EXE_NAME)
        return 1
    game = C.Game(pid)
    print("已连上 pid=%d" % pid)
    try:
        if cmd == "show":
            cmd_show(game)
        elif cmd == "count":
            cmd_count(game, float(argv[1]) if len(argv) > 1 else 12.0)
        elif cmd == "test":
            cmd_test(game, float(argv[1]) if len(argv) > 1 else 12.0)
        elif cmd == "slow":
            cmd_slow(game, float(argv[1]) if len(argv) > 1 else 24.0)
        else:
            print("未知命令：%s" % cmd)
            print(__doc__)
            return 2
    finally:
        game.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
