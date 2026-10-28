#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
probe_speed.py —— 真机验证「僵尸移速加成」方案

结论先行（详细推导见 hextech_core._speed_apply 的注释）：
    ✗ 写 Zombie+0x34（速度字段）没用。Zombie::Update 每帧做
      speed = min(speed + 0.01, target)，target 由类型/状态现算，
      外部写大下一帧就被夹回去。实测 ×2 无效、×3 更慢。
    ✓ 写 Zombie+0x2C（float 真实坐标）游戏完全接受。移速加成 = 每帧补位移。

本脚本用游戏自己的帧钟（Zombie+0x60，每秒 +100）归一化，
所以不受这台机器帧率抖动影响，A/B 可重复。

用法（先进关卡，草坪上有僵尸在走）：
    python tools/probe_speed.py                 # A/B/A，boost=+100%
    python tools/probe_speed.py --boost 0.5     # 测 +50%
    python tools/probe_speed.py --secs 2 --trials 2
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import hextech_core as C

TICK_FIELD = 0x60          # int，每秒 +100 的游戏帧钟


def pick_zombie(g):
    """挑一只在草坪中间、正在行走的僵尸。"""
    for z in g.scan_zombies():
        if z["status"] != 0:
            continue
        if not (150 < z["x"] < 650):
            continue
        v = g.f32(z["addr"] + C.Z_SPEED_FIELD)
        if v and 0.01 < v < 2.0:
            return z, v
    return None, None


def measure(g, addr, v, bonus, secs, hz=60.0):
    """跑 secs 秒，返回 (位移, tick 增量, px/tick)。

    补位移用和 Engine._speed_apply 完全一致的自适应算法：
      每帧观测僵尸自身速度 obs，再补 obs × bonus × dt。
    没有基准速度常数，所以不受「僵尸在啃食/停顿」影响。
    ★ 写入节流到 hz（默认 60Hz，和引擎 tick 一致）：紧循环每微秒写一次
      会和游戏自己的 posX 写入抢跑，把游戏那一步覆盖掉。
    """
    t0 = time.time()
    c0 = g.i32(addr + TICK_FIELD)
    prev = g.f32(addr + C.O_FX)
    prev_x = prev
    obs = 0.0
    dist = 0.0
    last_write = time.time()
    while time.time() - t0 < secs:
        now = time.time()
        cur = g.f32(addr + C.O_FX)
        if cur is None:
            continue
        if now - last_write >= 1.0 / hz:
            dt = now - last_write
            last_write = now
            inst = (cur - prev_x) / dt
            obs += (inst - obs) * C.Engine.Z_SPEED_SMOOTH
            if bonus > 0:
                px = obs * bonus * dt
                g.write_f32(addr + C.O_FX, cur + px)
                g.write_i32(addr + C.O_X, int(round(cur + px)))
                cur = g.f32(addr + C.O_FX)
            prev_x = cur
        if prev is not None:
            dist += abs(cur - prev)
        prev = cur
    ticks = g.i32(addr + TICK_FIELD) - c0
    return dist, ticks, (dist / ticks if ticks else 0.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--boost", type=float, default=1.0, help="补多少倍速，默认 1.0")
    ap.add_argument("--secs", type=float, default=2.0)
    ap.add_argument("--trials", type=int, default=1)
    ap.add_argument("--wait", type=float, default=0.0,
                    help="最多等多少秒，等僵尸走进草坪再测")
    args = ap.parse_args()

    g = C.Game()
    print("已连接 pid=%d" % g.pid)

    z, v = pick_zombie(g)
    if z is None and args.wait > 0:
        t0 = time.time()
        print("等僵尸走进草坪…（最多 %.0fs）" % args.wait)
        while time.time() - t0 < args.wait:
            time.sleep(0.3)
            z, v = pick_zombie(g)
            if z is not None:
                break
    if z is None:
        print("没找到草坪中间正在行走的僵尸。")
        print("（如果游戏是暂停状态，先点「继续游戏」；僵尸在草坪外时也算不出。）")
        return 1

    a = z["addr"]
    print("样本 idx=%d type=%d x=%d  +0x34=%.4f" % (z["idx"], z["type"], z["x"], v))
    print("\n%-16s %9s %7s %11s %8s" % ("场景", "位移px", "tick", "px/tick", "相对"))
    print("-" * 56)

    base_runs, boost_runs = [], []
    for _ in range(args.trials):
        for label, bonus, bucket in (("基准", 0.0, base_runs),
                                     ("加速", args.boost, boost_runs),
                                     ("还原", 0.0, base_runs)):
            d, t, r = measure(g, a, v, bonus, args.secs)
            # 僵尸走到房子 / 死亡 / 游戏被暂停时这轮无效，不计入均值
            valid = t >= args.secs * 50 and d > 0.5
            if valid:
                bucket.append(r)
            ref = base_runs[0] if base_runs else r
            print("%-16s %9.2f %7d %11.5f %7.2fx %s"
                  % (label + (" +%.0f%%" % (bonus * 100) if bonus else ""),
                     d, t, r, (r / ref if ref else 0),
                     "" if valid else "  ← 无效(僵尸已离场/游戏暂停)"))

    if base_runs and boost_runs:
        b = sum(base_runs) / len(base_runs)
        s = sum(boost_runs) / len(boost_runs)
        print("\n基准 %.5f (%d轮)  →  加速 %.5f (%d轮)  =  %.2f×（期望 %.2f×）"
              % (b, len(base_runs), s, len(boost_runs), s / b, 1.0 + args.boost))
        ok = abs(s / b - (1.0 + args.boost)) < 0.35 * (1.0 + args.boost)
        print("结论：%s" % ("补位移方案生效 ✓" if ok else
                            "偏差偏大，僵尸可能在啃食/停顿，多测几轮或换一只 ✗"))
    g.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
