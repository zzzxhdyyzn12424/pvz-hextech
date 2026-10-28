#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
probe.py —— PVZ 外部只读内存探针（不注入 / 不挂钩 / 不写内存）

为什么要有它：
  上一版把力气全花在「注入 DLL + 打代码钩子」上，结果钩子零命中，而且每次
  调试都要重启游戏、重装 DLL，代价极高。本工具改成**纯外部 ReadProcessMemory**：
    - 不注入：游戏进程里一行代码都不改
    - 不写内存：全程只读，理论上不可能让游戏崩
    - 可反复跑：游戏开着就能随时采样
  用来做的事只有一件：**确定性地把偏移量量出来**，不靠猜。

已知锚点（已在 PlantsVsZombies.exe 1.0.0.1051-zh 上静态验证）：
    gLawnApp = 0x006A9EC0
        证据：0x0044E8FE `push 0x8C8`(=sizeof(LawnApp)) -> call operator new
              -> 0x0044E969 `mov [0x6A9EC0], eax`
    LawnApp+0x768 = mBoard          （.text 里 274 处 [reg+0x768] 引用）
        证据：0x407CA3 `mov [ebp+0x8c], esi` + 0x407CAE `mov [esi+0x768], ebp`
              紧接着 `push 0x57000` —— Board 的分配大小
    Board+0x08C   = mApp            （同上，成对出现）
    sizeof(Board) = 0x57000         （356,352 字节）
    LawnApp+0x7F8 = mGameMode       （被反复和 0x2B / 0x33..0x3C / 0x14 / 0x18 / 0 比较）

用法：
    # 0. 自检（不需要游戏，验证读写链路通不通）
    python probe.py selftest

    # 1. 看 gApp / Board 是否正常，dump Board 头部
    python probe.py dump

    # 2. 扫 Board 里的指针数组，找出「抛射物列表」候选
    python probe.py scan

    # 3. 盯住一个对象，连续采样看哪个字段在动
    python probe.py watch 0x12345678 3

    # 4. 自动找「向右匀速飞行」的对象（游戏里先射一颗豌豆，再跑这个）
    python probe.py findpea 5

    # 5. 后台连续采集（推荐）：先跑它，再去游戏里打，事后读 JSON
    #    PVZ 切出窗口会自动暂停，所以没法"在开火那一刻"手动触发，
    #    只能让采集一直跑着等。每 2 秒落盘，中途被杀也不丢数据。
    python probe.py capture 300 build/pea_capture.json

    # 指定进程 / 手动给 gApp
    python probe.py --pid 1234 dump
    python probe.py --gapp 0x6A9EC0 dump
"""

import ctypes
import ctypes.wintypes as wt
import json
import struct
import sys
import time
import os

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
MEM_COMMIT = 0x1000
PAGE_NOACCESS = 0x01
PAGE_GUARD = 0x100

GAPP_VA = 0x006A9EC0
OFF_BOARD = 0x768
OFF_SCENE = 0x7F8
BOARD_SIZE = 0x57000
BOARD_OFF_APP = 0x8C

# ---- 显式声明签名，否则 64 位下指针会被当成 int 截断 ----
k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
k32.OpenProcess.restype = wt.HANDLE
k32.ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                  ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k32.ReadProcessMemory.restype = wt.BOOL
k32.VirtualQueryEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                               ctypes.c_size_t]
k32.VirtualQueryEx.restype = ctypes.c_size_t
k32.CloseHandle.argtypes = [wt.HANDLE]
k32.CloseHandle.restype = wt.BOOL
k32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
k32.Process32First.argtypes = [wt.HANDLE, ctypes.c_void_p]
k32.Process32First.restype = wt.BOOL
k32.Process32Next.argtypes = [wt.HANDLE, ctypes.c_void_p]
k32.Process32Next.restype = wt.BOOL

psapi.EnumProcessModulesEx.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD,
                                       ctypes.POINTER(wt.DWORD), wt.DWORD]
psapi.EnumProcessModulesEx.restype = wt.BOOL
psapi.GetModuleFileNameExW.argtypes = [wt.HANDLE, wt.HMODULE, wt.LPWSTR, wt.DWORD]
psapi.GetModuleFileNameExW.restype = wt.DWORD


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wt.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wt.DWORD),
        ("Protect", wt.DWORD),
        ("Type", wt.DWORD),
    ]


class Proc:
    def __init__(self, pid=None, name="PlantsVsZombies.exe"):
        if pid is None:
            pid = find_pid(name)
            if pid is None:
                raise SystemExit("找不到进程 %s，请先启动游戏，或用 --pid 指定" % name)
        self.pid = pid
        h = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
        if not h:
            raise SystemExit("OpenProcess 失败 (err=%d)。试过管理员权限吗？" % ctypes.get_last_error())
        self.h = h
        self.base = self._main_base(name)
        self._regions = None

    # ---------------- 模块 ----------------
    def _enum_base(self, name):
        mods = (wt.HMODULE * 1024)()
        needed = wt.DWORD()
        if not psapi.EnumProcessModulesEx(self.h, ctypes.byref(mods),
                                          ctypes.sizeof(mods),
                                          ctypes.byref(needed), 0x03):
            return None
        n = needed.value // ctypes.sizeof(wt.HMODULE)
        buf = ctypes.create_unicode_buffer(260)
        for i in range(n):
            psapi.GetModuleFileNameExW(self.h, mods[i], buf, 260)
            if os.path.basename(buf.value).lower() == name.lower():
                return int(mods[i] or 0)
        return int(mods[0] or 0)

    def _main_base(self, name):
        b = self._enum_base(name)
        if b:
            return b
        # 回退：PVZ 1.0.0.1051 没有 ASLR，镜像基址固定 0x400000。
        # 从 64 位 Python 枚举 32 位进程的模块有时会失败，这条路更稳。
        if self.read(0x400000, 2) == b"MZ":
            print("（模块枚举失败，回退到固定基址 0x400000）")
            return 0x400000
        return None

    # ---------------- 读 ----------------
    def read(self, addr, n):
        buf = ctypes.create_string_buffer(n)
        got = ctypes.c_size_t(0)
        ok = k32.ReadProcessMemory(self.h, ctypes.c_void_p(addr), buf, n,
                                   ctypes.byref(got))
        if not ok or got.value != n:
            return None
        return buf.raw

    def u32(self, addr):
        b = self.read(addr, 4)
        return struct.unpack("<I", b)[0] if b else None

    def i32(self, addr):
        b = self.read(addr, 4)
        return struct.unpack("<i", b)[0] if b else None

    def f32(self, addr):
        b = self.read(addr, 4)
        return struct.unpack("<f", b)[0] if b else None

    def readable(self, addr, n=4):
        return self.read(addr, n) is not None

    def regions(self):
        """枚举所有可读的已提交区域（用于安全扫描）"""
        if self._regions is not None:
            return self._regions
        out = []
        addr = 0
        mbi = MEMORY_BASIC_INFORMATION()
        while addr < 0x80000000:
            r = k32.VirtualQueryEx(self.h, ctypes.c_void_p(addr),
                                   ctypes.byref(mbi), ctypes.sizeof(mbi))
            if not r:
                break
            base = mbi.BaseAddress or 0
            size = mbi.RegionSize
            if size == 0:
                break
            if (mbi.State == MEM_COMMIT and not (mbi.Protect & PAGE_NOACCESS)
                    and not (mbi.Protect & PAGE_GUARD)):
                out.append((base, size, mbi.Protect))
            addr = base + size
        self._regions = out
        return out

    def close(self):
        if self.h:
            k32.CloseHandle(self.h)
            self.h = None


def find_pid(name):
    TH32CS_SNAPPROCESS = 0x02

    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD),
                    ("th32ProcessID", wt.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
                    ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                    ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wt.DWORD), ("szExeFile", ctypes.c_char * 260)]

    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == -1:
        return None
    pe = PROCESSENTRY32()
    pe.dwSize = ctypes.sizeof(pe)
    ok = k32.Process32First(snap, ctypes.byref(pe))
    found = None
    while ok:
        if pe.szExeFile.decode("latin1").lower() == name.lower():
            found = pe.th32ProcessID
            break
        ok = k32.Process32Next(snap, ctypes.byref(pe))
    k32.CloseHandle(snap)
    return found


# ---------------------------------------------------------------- 命令
def cmd_selftest(p):
    print("pid        :", p.pid)
    print("module base: 0x%08X" % (p.base or 0))
    hdr = p.read(p.base, 2) if p.base else None
    print("read MZ    :", hdr)
    print("可读区域数 :", len(p.regions()))
    ok = hdr == b"MZ"
    print("自检       :", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def cmd_dump(p, gapp_va):
    gapp_slot = p.base + (gapp_va - 0x400000)
    gapp = p.u32(gapp_slot)
    print("gApp slot  : 0x%08X  ->  gApp = %s" %
          (gapp_slot, ("0x%08X" % gapp) if gapp else "NULL"))
    if not gapp:
        print("gApp 为空：游戏还没进主界面？等几秒再试。")
        return 1
    scene = p.i32(gapp + OFF_SCENE)
    print("mGameScene : %s" % scene)
    board = p.u32(gapp + OFF_BOARD)
    print("mBoard     : %s" % (("0x%08X" % board) if board else "NULL"))
    if not board:
        print("Board 为空：还在菜单，没进关卡。")
        return 1
    back = p.u32(board + BOARD_OFF_APP)
    print("Board+0x%02X  : %s  (应等于 gApp %s)" %
          (BOARD_OFF_APP, ("0x%08X" % back) if back else "NULL",
           "✅" if back == gapp else "❌ 偏移可能不对"))
    print("\n--- LawnApp 头部 0x00..0x60 ---")
    for off in range(0, 0x60, 4):
        v = p.u32(gapp + off)
        f = p.f32(gapp + off)
        print("  +0x%03X  u32=0x%08X  int=%-12d  float=%s" %
              (off, v or 0, struct.unpack("<i", struct.pack("<I", v or 0))[0], f))
    print("\n--- Board 头部 0x00..0x140 ---")
    for off in range(0, 0x140, 4):
        v = p.u32(board + off)
        f = p.f32(board + off)
        print("  +0x%03X  u32=0x%08X  int=%-12d  float=%s" %
              (off, v or 0, struct.unpack("<i", struct.pack("<I", v or 0))[0], f))
    return 0


def looks_like_ptr(v):
    return v is not None and 0x00010000 <= v < 0x80000000


# EXE 映像范围：0x400000 起，4 个节总共约 0x394000。
# 落在这个区间的"指针"多半是 vtable / 静态表，不是游戏对象，扫描时要排除。
IMAGE_LO = 0x00400000
IMAGE_HI = 0x00794000


def looks_like_obj(v):
    """像堆上的游戏对象：是合法指针，且不在 EXE 映像里"""
    if not looks_like_ptr(v):
        return False
    return not (IMAGE_LO <= v < IMAGE_HI)


def cmd_scan(p, gapp_va, span=BOARD_SIZE):
    """在 Board 区间里找「连续的指针数组」——抛射物/僵尸/植物列表都是这种形态"""
    gapp = p.u32(p.base + (gapp_va - 0x400000))
    if not gapp:
        print("gApp 为空")
        return 1
    board = p.u32(gapp + OFF_BOARD)
    if not board:
        print("Board 为空：先进入关卡（选卡进 1-1）")
        return 1
    print("Board = 0x%08X，扫描 +0x0000..+0x%05X（sizeof(Board)=0x57000）" % (board, span))
    raw = p.read(board, span)
    if not raw:
        print("Board 区间不可读")
        return 1

    print("\n--- 指针数组候选（>=6 个连续堆对象指针，已排除 EXE 映像内的 vtable）---")
    found = []
    i = 0
    n = len(raw) // 4
    while i < n:
        v = struct.unpack_from("<I", raw, i * 4)[0]
        if looks_like_obj(v):
            j = i
            ptrs = []
            while j < n:
                vv = struct.unpack_from("<I", raw, j * 4)[0]
                if not looks_like_obj(vv):
                    break
                ptrs.append(vv)
                j += 1
            # 只验证首元素可读，避免几千次 RPM
            if len(ptrs) >= 6 and p.readable(ptrs[0], 0x40):
                found.append((i * 4, ptrs))
                i = j
                continue
        i += 1

    for off, ptrs in found:
        print("  Board+0x%05X  %d 个指针  首元素=0x%08X" % (off, len(ptrs), ptrs[0]))
        o = ptrs[0]
        fs = []
        for k in range(0x24, 0x40, 4):
            f = p.f32(o + k)
            fs.append("+0x%02X=%s" % (k, ("%.3f" % f) if f is not None else "?"))
        print("        首元素 +0x24..+0x3C: " + "  ".join(fs))
        # 再抽一个元素看是否同类
        if len(ptrs) > 1:
            o2 = ptrs[-1]
            fs2 = []
            for k in (0x24, 0x28, 0x2C, 0x30):
                f = p.f32(o2 + k)
                fs2.append("%s" % (("%.2f" % f) if f is not None else "?"))
            print("        末元素 +0x24/+28/+2C/+30: " + " / ".join(fs2))
    if not found:
        print("  （没找到。可能该列表是链表结构，用 findpea 按行为特征找）")
    return 0


def build_candidates(p, raw):
    """
    从 Board 的原始字节里挑出「符合抛射物签名」的对象指针。

    签名：+0x24 x 在屏幕内、+0x28 y 在屏幕内、+0x2C vx 是 0~30 的正小数。
    会把候选对象内部指向的对象再展开一层（抛射物可能挂在链表节点里）。
    """
    cands = []
    for i in range(len(raw) // 4):
        v = struct.unpack_from("<I", raw, i * 4)[0]
        if not looks_like_obj(v):
            continue
        b = p.read(v, 0x40)
        if not b:
            continue
        x, y, vx, vy = struct.unpack_from("<4f", b, 0x24)
        if 0.0 <= x <= 1200.0 and 0.0 <= y <= 800.0 and 0.0 < vx < 30.0:
            cands.append((i * 4, v))

    seen = {o for _, o in cands}
    extra = []
    for off, o in cands:
        b = p.read(o, 0x40)
        if not b:
            continue
        for k in range(0, 0x40, 4):
            vv = struct.unpack_from("<I", b, k)[0]
            if not looks_like_obj(vv) or vv in seen:
                continue
            bb = p.read(vv, 0x40)
            if not bb:
                continue
            x, y, vx, vy = struct.unpack_from("<4f", bb, 0x24)
            if 0.0 <= x <= 1200.0 and 0.0 <= y <= 800.0 and 0.0 < vx < 30.0:
                seen.add(vv)
                extra.append((off, vv))
    cands.extend(extra)
    return cands


def _write_json(obj, out):
    """原子落盘：先写 .tmp 再 replace，避免读到写了一半的文件"""
    try:
        d = os.path.dirname(os.path.abspath(out))
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        tmp = out + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        os.replace(tmp, out)
    except Exception as e:
        print("  写文件失败:", e)


def _save_capture(stats, out, rounds, elapsed):
    """每 2 秒落一次盘，这样即使进程被提前杀掉也留得下数据"""
    items = []
    for o, st in stats.items():
        dx = st["last"] - st["first"]
        items.append(dict(ptr=o, board_off=st["off"], first_x=st["first"],
                          last_x=st["last"], dx=dx, inc=st["inc"], n=st["n"]))
    items.sort(key=lambda d: (d["inc"], d["dx"]), reverse=True)
    payload = dict(rounds=rounds, elapsed=round(elapsed, 1), total=len(items),
                   top=items[:60])
    try:
        d = os.path.dirname(os.path.abspath(out))
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        tmp = out + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        os.replace(tmp, out)
    except Exception as e:
        print("  写文件失败:", e)


def cmd_capture(p, gapp_va, seconds=120, out="build/pea_capture.json"):
    """
    连续采集模式：一边跑一边落盘，用户不用配合"某个瞬间"。

    用法：先启动它（可以放后台），再去游戏里种豌豆射手开火，打几轮，
    回来读 out 文件。每 2 秒落一次盘，中途被杀也不丢数据。
    """
    print("READY 开始采集 %d 秒，输出 %s" % (seconds, out), flush=True)
    stats = {}
    last = {}
    t0 = time.time()
    t_end = t0 + seconds
    next_refresh = 0.0
    next_save = 0.0
    next_report = 0.0
    cands = []
    rounds = 0
    empty_rounds = 0
    max_right = 0.0
    move_hits = 0
    prev_move_hits = 0

    try:
        while time.time() < t_end:
            now = time.time()
            gapp = p.u32(p.base + (gapp_va - 0x400000))
            board = p.u32(gapp + OFF_BOARD) if gapp else None
            if not board:
                empty_rounds += 1
                time.sleep(0.2)
                continue

            if now >= next_refresh:
                raw = p.read(board, BOARD_SIZE)
                if raw:
                    cands = build_candidates(p, raw)
                next_refresh = now + 0.5

            for off, o in cands:
                b = p.read(o + 0x24, 0x10)
                if not b:
                    continue
                x, y, vx, vy = struct.unpack("<4f", b)
                st = stats.get(o)
                if st is None:
                    stats[o] = dict(off=off, first=x, last=x, inc=0, n=1)
                else:
                    d = x - st["last"]
                    if 0.05 < d < 60.0:
                        st["inc"] += 1
                    if 0.02 < abs(d) < 60.0:
                        move_hits += 1
                    st["last"] = x
                    st["n"] += 1
                    st["off"] = off
            rounds += 1

            if now >= next_save:
                _save_capture(stats, out, rounds, now - t0)
                next_save = now + 2.0

            if now >= next_report:
                for st in stats.values():
                    d = st["last"] - st["first"]
                    if d > max_right:
                        max_right = d
                alive = (move_hits - prev_move_hits) > 30
                prev_move_hits = move_hits
                if not cands:
                    state = "关卡未加载，等待进入关卡"
                elif alive:
                    state = "游戏推进中 OK"
                else:
                    state = "★游戏暂停中★ 请点回游戏窗口！"
                print("  [%3ds] 轮=%-5d 候选=%-4d 最大右移=%6.1f   %s" %
                      (int(now - t0), rounds, len(stats), max_right, state), flush=True)
                next_report = now + 5.0

            time.sleep(0.01)
    except KeyboardInterrupt:
        print("\n收到中断，正在保存已有数据...")
    finally:
        _save_capture(stats, out, rounds, time.time() - t0)

    print("DONE 轮数=%d 候选=%d 空转轮=%d" % (rounds, len(stats), empty_rounds))

    items = sorted(stats.items(), key=lambda kv: (kv[1]["inc"], kv[1]["last"] - kv[1]["first"]),
                   reverse=True)
    print("\n--- 位移最明显的对象 ---")
    shown = 0
    for o, st in items:
        if st["inc"] < 8:
            continue
        print("  0x%08X  Board+0x%05X  dx=%7.1f  x %.1f -> %.1f  递增 %d/%d" %
              (o, st["off"], st["last"] - st["first"], st["first"], st["last"],
               st["inc"], st["n"]))
        shown += 1
        if shown >= 12:
            break
    if shown == 0:
        print("  （没有明显向右位移的对象）")
    return 0


def cmd_spawnwatch(p, gapp_va, seconds=240, out="build/spawn_watch.json"):
    """
    Board 差异监视 —— 抓「新对象出生」的那一瞬间。

    原理：抛射物一出生，Board 里必然有一个槽从 0 变成指向它的指针。
    只要盯住 Board 的 0x57000 字节，逐帧比对，任何新槽位都会被记下来，
    并带上它在 Board 里的偏移。**不依赖抓时机**：豌豆只存在一帧也够。

    这比"追踪坐标运动"可靠得多，因为它看的是写指针这个动作本身。
    """
    try:
        import numpy as np
    except ImportError:
        print("需要 numpy")
        return 1

    print("SPAWN-WATCH 开始 %d 秒，输出 %s" % (seconds, out), flush=True)
    print("现在切回游戏：进关卡、种豌豆射手、让僵尸走过来触发开火。\n", flush=True)

    gapp = p.u32(p.base + (gapp_va - 0x400000))
    if not gapp:
        print("gApp 为空")
        return 1

    prev = None
    events = []
    spawned = set()      # 已记录过的出生指针，用来过滤"槽归零"噪音
    t0 = time.time()
    t_end = t0 + seconds
    next_save = 0.0
    next_report = 0.0
    rounds = 0
    board_seen = None

    try:
        while time.time() < t_end:
            now = time.time()
            board = p.u32(gapp + OFF_BOARD)
            if not board:
                if now >= next_report:
                    print("  [%3ds] 还没进关卡（mBoard 为空），等待中..." %
                          int(now - t0), flush=True)
                    next_report = now + 5.0
                prev = None
                time.sleep(0.15)
                continue

            if board != board_seen:
                print("  [%3ds] 关卡 Board = 0x%08X，开始监视" %
                      (int(now - t0), board), flush=True)
                board_seen = board
                prev = None
                spawned.clear()

            raw = p.read(board, BOARD_SIZE)
            if not raw:
                time.sleep(0.1)
                continue
            cur = np.frombuffer(raw, dtype="<u4").copy()
            rounds += 1

            if prev is not None and cur.size == prev.size:
                diff = np.nonzero(cur != prev)[0]
                for i in diff:
                    old = int(prev[i])
                    new = int(cur[i])
                    off = int(i) * 4
                    if old == 0 and looks_like_obj(new):
                        b = p.read(new, 0x40)
                        if not b:
                            continue
                        d0, d1 = struct.unpack_from("<2I", b, 0)
                        if d0 != gapp or d1 != board:
                            continue
                        x, y, w, h = struct.unpack_from("<4i", b, 8)
                        f24, f28, f2c, f30 = struct.unpack_from("<4f", b, 0x24)
                        ev = dict(kind="spawn", board_off=off, ptr=new,
                                  x=x, y=y, w=w, h=h,
                                  f24=f24, f28=f28, f2c=f2c, f30=f30,
                                  t=round(now - t0, 2))
                        events.append(ev)
                        spawned.add(new)
                        print("  ★ [%6.2fs] Board+0x%05X 出现新对象 0x%08X  "
                              "x=%-4d y=%-4d %dx%-3d f24=%8.2f f28=%7.2f f2c=%6.2f f30=%6.2f"
                              % (now - t0, off, new, x, y, w, h, f24, f28, f2c, f30),
                              flush=True)
                    elif old != 0 and new == 0 and old in spawned:
                        # 只记"我们见过的对象被清空"，不记计数器归零这类噪音
                        events.append(dict(kind="free", board_off=off, ptr=old,
                                           t=round(now - t0, 2)))
            prev = cur

            if now >= next_save:
                _write_json(dict(rounds=rounds, elapsed=round(now - t0, 1),
                                 events=events[-4000:]), out)
                next_save = now + 2.0
            if now >= next_report:
                print("  [%3ds] 轮=%-6d 事件=%d" % (int(now - t0), rounds, len(events)),
                      flush=True)
                next_report = now + 10.0
            time.sleep(0.01)   # 约 50Hz，足够；槽位不会一帧就消失
    except KeyboardInterrupt:
        print("\n中断，保存中...")
    finally:
        _write_json(dict(rounds=rounds, elapsed=round(time.time() - t0, 1),
                         events=events), out)

    spawns = [e for e in events if e["kind"] == "spawn"]
    print("\nDONE 轮=%d 事件=%d（出生 %d / 消失 %d）" %
          (rounds, len(events), len(spawns), len(events) - len(spawns)))
    if spawns:
        print("\n--- 新对象出生位置统计（Board 偏移 → 次数）---")
        stat = {}
        for e in spawns:
            stat[e["board_off"]] = stat.get(e["board_off"], 0) + 1
        for off, n in sorted(stat.items(), key=lambda kv: -kv[1])[:15]:
            print("  Board+0x%05X  %d 次" % (off, n))
    return 0


def cmd_boardmap(p, gapp_va):
    """
    Board 内存地图 —— 把 0x57000 字节按 4 字节逐格分类，找出「对象指针数组」。

    为什么需要它：
      找 mProjectiles 一直靠猜基址（0x1138 / 0x2138 ...）全失败。
      但指针数组有一个不会骗人的特征：**整段里每个 dword 要么是 0，
      要么是指向合法 GameObject 的指针**。把 Board 扫一遍，
      把满足这个特征的最长区间找出来，数组基址和长度就自己浮出来了。

    分类：Z=0  G=GameObject  P=其它可读指针  I=小整数  F=像浮点  X=其它
    """
    try:
        import numpy as np
    except ImportError:
        print("需要 numpy：.venv\\Scripts\\python.exe -m pip install numpy")
        return 1

    gapp = p.u32(p.base + (gapp_va - 0x400000))
    if not gapp:
        print("gApp 为空")
        return 1
    board = p.u32(gapp + OFF_BOARD)
    if not board:
        print("Board 为空：先进入关卡")
        return 1

    raw = p.read(board, BOARD_SIZE)
    if not raw:
        print("读不到 Board")
        return 1
    arr = np.frombuffer(raw, dtype="<u4")
    n = arr.size
    print("Board=0x%08X  %d 个 dword (0x%X 字节)" % (board, n, n * 4))

    # 先找出所有 GameObject 指针位置（向量化两两判据 + 单点确认）
    is_obj = np.zeros(n, dtype=bool)
    for i in range(n):
        v = int(arr[i])
        if v == 0 or not looks_like_obj(v):
            continue
        b = p.read(v, 8)
        if b:
            d0, d1 = struct.unpack("<2I", b)
            if d0 == gapp and d1 == board:
                is_obj[i] = True

    zero = (arr == 0)
    obj_idx = np.nonzero(is_obj)[0]
    print("GameObject 指针槽 %d 个：%s" %
          (len(obj_idx), ", ".join("+0x%05X" % (int(i) * 4) for i in obj_idx[:20])))

    # 这 10 个槽各自指向谁
    print("\n--- 这 %d 个槽指向的对象 ---" % len(obj_idx))
    for i in obj_idx:
        ptr = int(arr[i])
        b = p.read(ptr, 0x40)
        if not b:
            continue
        x, y, w, h = struct.unpack_from("<4i", b, 8)
        f24, f28, f2c, f30 = struct.unpack_from("<4f", b, 0x24)
        print("  Board+0x%05X -> 0x%08X  x=%-5d y=%-5d %dx%-3d  f24=%8.2f f28=%7.2f"
              % (int(i) * 4, ptr, x, y, w, h, f24, f28))

    # ---- 1.5 整张 Board 的类别压缩图 ----
    print("\n--- Board 类别图（连续同类合并，只显示 >= 0x40 字节的段）---")
    print("  Z=0  G=对象指针  P=堆指针  I=小整数  F=浮点  X=其它")

    def classify(v, g):
        if v == 0:
            return "Z"
        if g:
            return "G"
        if 0x01000000 <= v < 0x7F000000 and (v & 3) == 0:
            return "P"
        if v < 0x10000:
            return "I"
        f = struct.unpack("<f", struct.pack("<I", v))[0]
        if f == f and abs(f) > 1e-6 and abs(f) < 1e7:
            return "F"
        return "X"

    classes = [classify(int(arr[k]), bool(is_obj[k])) for k in range(n)]
    k = 0
    segs = []
    while k < n:
        c = classes[k]
        j = k
        while j < n and classes[j] == c:
            j += 1
        segs.append((k * 4, (j - k) * 4, c))
        k = j
    for off, ln, c in segs:
        if ln < 0x40:
            continue
        print("  Board+0x%05X .. +0x%05X  %s  长 0x%-6X (%d 槽)"
              % (off, off + ln, c, ln, ln // 4))
    print("（段数 %d，只列了 >= 0x40 的）" % len(segs))

    # ---- 2. 大块全零区间（数组的空槽区） ----
    print("\n--- 大块全零区间（>= 0x400 字节），数组多半藏在这里面 ---")
    i = 0
    zruns = []
    while i < n:
        if not zero[i]:
            i += 1
            continue
        j = i
        while j < n and zero[j]:
            j += 1
        if (j - i) * 4 >= 0x400:
            zruns.append((i * 4, (j - i) * 4))
        i = j
    for off, ln in zruns[:25]:
        print("  Board+0x%05X .. +0x%05X   长 0x%-6X (%d 槽)"
              % (off, off + ln, ln, ln // 4))
    return 0


# ---------------------------------------------------------------- 窗口焦点
# PVZ 一旦失去焦点就自己暂停（不是最小化才暂停，切走就停），
# 而探针只能在游戏真的跑起来的时候才有意义。所以这里提供「把游戏拉回前台」，
# 让采集可以完全自动，不用人守着。
u32dll = ctypes.WinDLL("user32", use_last_error=True)
u32dll.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
u32dll.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u32dll.SetForegroundWindow.argtypes = [wt.HWND]
u32dll.BringWindowToTop.argtypes = [wt.HWND]
u32dll.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
u32dll.IsIconic.argtypes = [wt.HWND]
u32dll.IsWindowVisible.argtypes = [wt.HWND]
u32dll.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
u32dll.GetForegroundWindow.restype = wt.HWND
k32.GetCurrentThreadId.restype = wt.DWORD

_WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def _wtitle(h):
    b = ctypes.create_unicode_buffer(512)
    u32dll.GetWindowTextW(h, b, 512)
    return b.value


def _wpid(h):
    p = wt.DWORD()
    u32dll.GetWindowThreadProcessId(h, ctypes.byref(p))
    return p.value


def game_hwnd(pid):
    """按进程 PID 找它的可见顶层窗口（PVZ 窗口标题随版本/汉化而变，按 PID 最稳）"""
    hits = []

    def _cb(hwnd, lp):
        if u32dll.IsWindowVisible(hwnd) and _wpid(hwnd) == pid and _wtitle(hwnd):
            hits.append(hwnd)
        return True

    u32dll.EnumWindows(_WNDENUMPROC(_cb), 0)
    return hits[0] if hits else None


def focus_game(pid):
    """把游戏窗口拉到前台。返回 (hwnd, 是否成功在前台)"""
    h = game_hwnd(pid)
    if not h:
        return None, False
    if u32dll.IsIconic(h):
        u32dll.ShowWindow(h, 9)      # SW_RESTORE
    fg = u32dll.GetForegroundWindow()
    if fg == h:
        return h, True
    cur = k32.GetCurrentThreadId()
    tf = u32dll.GetWindowThreadProcessId(fg, None)
    tt = u32dll.GetWindowThreadProcessId(h, None)
    # 前台锁：先把自己的输入队列挂到当前前台线程和目标线程上，再抢
    u32dll.AttachThreadInput(cur, tf, True)
    u32dll.AttachThreadInput(cur, tt, True)
    u32dll.BringWindowToTop(h)
    u32dll.SetForegroundWindow(h)
    u32dll.AttachThreadInput(cur, tf, False)
    u32dll.AttachThreadInput(cur, tt, False)
    return h, u32dll.GetForegroundWindow() == h


def cmd_focus(p):
    h, ok = focus_game(p.pid)
    if not h:
        print("找不到游戏窗口（进程 %d 没有可见顶层窗口）" % p.pid)
        return 1
    print("游戏窗口 HWND=%s  标题=%r  已在前台=%s" % (h, _wtitle(h), ok))
    return 0


def _find_refs(p, value, cap=400):
    """全进程搜「哪个 dword 等于 value」，返回命中地址列表。约 0.5 秒。"""
    import numpy as np
    hits = []
    for (base, size, prot) in p.regions():
        off = 0
        while off < size:
            n = min(0x200000, size - off)
            buf = p.read(base + off, n)
            if buf and len(buf) >= 4:
                arr = np.frombuffer(buf, dtype="<u4")
                for i in np.nonzero(arr == value)[0]:
                    hits.append(int(base + off + int(i) * 4))
                    if len(hits) >= cap:
                        return hits
            off += n
    return hits


def _refs_desc(p, ptr, board):
    """把引用点翻译成人话：在 Board 里就报 Board+偏移"""
    out = []
    for a in _find_refs(p, ptr):
        if board and board <= a < board + BOARD_SIZE:
            out.append("Board+0x%05X" % (a - board))
        else:
            out.append("0x%08X" % a)
    return out


def _scan_objects(p, gapp, board):
    """全进程扫 GameObject，返回地址列表。判据 +0x00==gApp 且 +0x04==Board。"""
    import numpy as np
    found = []
    for (base, size, prot) in p.regions():
        off = 0
        while off < size:
            n = min(0x200000, size - off)
            buf = p.read(base + off, n)
            if buf and len(buf) >= 0x40:
                arr = np.frombuffer(buf, dtype="<u4")
                m = arr.size - 16
                if m > 0:
                    mask = (arr[0:m] == gapp) & (arr[1:m + 1] == board)
                    if mask.any():
                        for i in np.nonzero(mask)[0]:
                            found.append(int(base + off + int(i) * 4))
            off += n
    return found


def _obj_desc(p, ptr):
    b = p.read(ptr, 0x40)
    if not b:
        return None
    x, y, w, h = struct.unpack_from("<4i", b, 8)
    f24, f28, f2c, f30 = struct.unpack_from("<4f", b, 0x24)
    return dict(x=x, y=y, w=w, h=h, f24=f24, f28=f28, f2c=f2c, f30=f30)


def _fmt_obj(d):
    return ("x=%-5d y=%-5d %dx%-3d f24=%8.2f f28=%7.2f f2c=%8.2f f30=%8.2f"
            % (d["x"], d["y"], d["w"], d["h"], d["f24"], d["f28"], d["f2c"], d["f30"]))


def cmd_peawatch(p, gapp_va, seconds=150.0, out="build/pea_watch.json", keep_focus=False):
    """
    专抓豌豆 —— 两路同时盯：

      路 A（高频，约 200~500Hz）：只读 Board 的 0x57000 字节，
              找出所有「值为合法 GameObject 指针」的 dword 槽位。
              槽位集合有任何变化（新出现 / 消失）都立刻打印。
              → 只要豌豆被 Board 引用，它的槽位偏移就会自己暴露。

      路 B（低频，约 1.5Hz）：全进程扫 GameObject，对比指针集合。
              → 新出现的对象就是豌豆 / 阳光 / 粒子；
                记下指针后就能用 findval 反查它在 Board 里的槽位。

    为什么两路都要：上一版已经证实 Board 里只有 10 个对象指针槽，
    豌豆可能根本不走 Board 的指针数组，路 B 兜底。
    """
    try:
        import numpy as np
    except ImportError:
        print("需要 numpy：.venv\\Scripts\\python.exe -m pip install numpy")
        return 1

    gapp = p.u32(p.base + (gapp_va - 0x400000))
    if not gapp:
        print("gApp 为空")
        return 1

    print("PEA-WATCH 开始 %.0f 秒" % seconds, flush=True)
    if keep_focus:
        h, ok = focus_game(p.pid)
        print("  keep-focus: 游戏窗口 HWND=%s，已拉到前台=%s" % (h, ok), flush=True)
    print("★ 请把游戏窗口点一下保持在最前面，别切走（切走游戏会暂停）。", flush=True)
    print("★ 让豌豆射手真的开火：关卡里要有僵尸走到射程内。\n", flush=True)

    t0 = time.time()
    t_end = t0 + seconds
    board = None
    prev_slots = None          # {(board_off, ptr)}
    known_objs = set()         # 已见过的对象指针
    slot_events = []           # 路 A 事件
    obj_events = []            # 路 B 事件
    next_full = 0.0
    next_save = 0.0
    next_report = 0.0
    next_focus = 0.0
    next_regions = 0.0
    rounds_a = 0
    rounds_b = 0
    baseline = []              # 第一次全扫时的对象快照（含原始字节）
    frozen = 0                 # 连续多少轮 Board 内容完全没变（游戏暂停的迹象）
    prev_raw = None

    try:
        while time.time() < t_end:
            now = time.time()
            if keep_focus and now >= next_focus:
                h, ok = focus_game(p.pid)
                if not ok:
                    print("  [%3ds] ⚠ 拉不回前台（HWND=%s），游戏可能是暂停的"
                          % (int(now - t0), h), flush=True)
                next_focus = now + 1.0
            b = p.u32(gapp + OFF_BOARD)
            if not b:
                if now >= next_report:
                    print("  [%3ds] 还没进关卡..." % int(now - t0), flush=True)
                    next_report = now + 5.0
                time.sleep(0.1)
                continue
            if b != board:
                print("  [%3ds] Board = 0x%08X" % (int(now - t0), b), flush=True)
                board = b
                prev_slots = None
                known_objs = set()
                next_full = 0.0

            # ---------- 路 A：Board 里的对象指针槽 ----------
            raw = p.read(board, BOARD_SIZE)
            if raw:
                if prev_raw is not None and raw == prev_raw:
                    frozen += 1
                else:
                    frozen = 0
                prev_raw = raw
                arr = np.frombuffer(raw, dtype="<u4")
                cur = set()
                for i in range(arr.size):
                    v = int(arr[i])
                    if v == 0 or not looks_like_obj(v):
                        continue
                    hb = p.read(v, 8)
                    if hb and struct.unpack("<2I", hb) == (gapp, board):
                        cur.add((i * 4, v))
                rounds_a += 1
                if prev_slots is not None:
                    for off, ptr in sorted(cur - prev_slots):
                        d = _obj_desc(p, ptr)
                        if not d:
                            continue
                        print("  ▲A [%6.2fs] Board+0x%05X 新增 0x%08X  %s"
                              % (now - t0, off, ptr, _fmt_obj(d)), flush=True)
                        slot_events.append(dict(kind="add", t=round(now - t0, 2),
                                                board_off=off, ptr=ptr, **d))
                    for off, ptr in sorted(prev_slots - cur):
                        print("  ▼A [%6.2fs] Board+0x%05X 移除 0x%08X"
                              % (now - t0, off, ptr), flush=True)
                        slot_events.append(dict(kind="del", t=round(now - t0, 2),
                                                board_off=off, ptr=ptr))
                prev_slots = cur

            # ---------- 路 B：全进程新对象 ----------
            if now >= next_full:
                if now >= next_regions:
                    # 堆会长出新区域，缓存必须定期失效，否则新分配的对象永远扫不到
                    p._regions = None
                    next_regions = now + 30.0
                objs = set(_scan_objects(p, gapp, board))
                rounds_b += 1
                if not known_objs and not baseline:
                    # 第一次全扫 = 基线快照，把每个对象的原始字节都存下来，
                    # 之后离线就能看清每种对象长什么样、字段在哪
                    for ptr in sorted(objs):
                        d = _obj_desc(p, ptr)
                        rawb = p.read(ptr, 0x60) or b""
                        if d:
                            baseline.append(dict(ptr=ptr, raw=rawb.hex(), **d))
                    print("  [基线] 当前共 %d 个对象，已存入 JSON" % len(baseline),
                          flush=True)
                    grp = {}
                    for e in baseline:
                        grp.setdefault((e["w"], e["h"]), []).append(e)
                    for (w, h), lst in sorted(grp.items(), key=lambda kv: -len(kv[1])):
                        print("     %dx%-3d  %d 个   x=%s"
                              % (w, h, len(lst),
                                 ",".join(str(e["x"]) for e in lst[:8])),
                              flush=True)
                if known_objs:
                    for ptr in sorted(objs - known_objs):
                        d = _obj_desc(p, ptr)
                        if not d:
                            continue
                        # 立刻反查「谁引用了这个新对象」—— 引用点就是数组槽位
                        refs = _refs_desc(p, ptr, board)
                        rawb = p.read(ptr, 0x60) or b""
                        print("  ★B [%6.2fs] 新对象 0x%08X  %s\n"
                              "        引用点 %d 处: %s"
                              % (now - t0, ptr, _fmt_obj(d), len(refs),
                                 ", ".join(refs[:12]) or "（无，可能只在栈/寄存器里）"),
                              flush=True)
                        obj_events.append(dict(kind="new", t=round(now - t0, 2),
                                               ptr=ptr, refs=refs,
                                               raw=rawb.hex(), **d))
                    for ptr in sorted(known_objs - objs):
                        obj_events.append(dict(kind="gone", t=round(now - t0, 2),
                                               ptr=ptr))
                known_objs = objs
                next_full = now + 0.6

            if now >= next_save:
                _write_json(dict(rounds_a=rounds_a, rounds_b=rounds_b,
                                 elapsed=round(now - t0, 1),
                                 baseline=baseline,
                                 slot_events=slot_events[-3000:],
                                 obj_events=obj_events[-3000:]), out)
                next_save = now + 2.0
            if now >= next_report:
                tag = "  游戏在跑" if frozen < 3 else "  ⚠ 连续 %d 轮 Board 没变化（游戏暂停中）" % frozen
                print("  [%3ds] A轮=%-6d B轮=%-4d 活对象=%-3d 槽事件=%d 对象事件=%d%s"
                      % (int(now - t0), rounds_a, rounds_b, len(known_objs),
                         len(slot_events), len(obj_events), tag), flush=True)
                next_report = now + 10.0
    except KeyboardInterrupt:
        print("\n中断，保存中...")
    finally:
        _write_json(dict(rounds_a=rounds_a, rounds_b=rounds_b,
                         elapsed=round(time.time() - t0, 1),
                         baseline=baseline,
                         slot_events=slot_events, obj_events=obj_events), out)

    print("\nDONE  A轮=%d B轮=%d 槽事件=%d 对象事件=%d"
          % (rounds_a, rounds_b, len(slot_events), len(obj_events)))
    if slot_events:
        print("\n--- 路 A：Board 槽位变化统计 ---")
        stat = {}
        for e in slot_events:
            k = (e["board_off"], e["kind"])
            stat[k] = stat.get(k, 0) + 1
        for (off, kind), n in sorted(stat.items(), key=lambda kv: -kv[1])[:20]:
            print("  Board+0x%05X  %s  %d 次" % (off, kind, n))
    news = [e for e in obj_events if e["kind"] == "new"]
    if news:
        print("\n--- 路 B：新出现对象（前 30 个，找小尺寸且 x 递增的就是豌豆）---")
        for e in news[:30]:
            print("  t=%6.2fs 0x%08X  x=%-5d y=%-5d %dx%-3d f24=%8.2f f28=%7.2f"
                  % (e["t"], e["ptr"], e["x"], e["y"], e["w"], e["h"],
                     e["f24"], e["f28"]))
    else:
        print("\n路 B 没抓到新对象。")
    return 0


def cmd_findarrays(p, gapp_va, min_slots=256):
    """
    全进程找「对象指针数组」—— 不限位置。

    背景：boardmap 已证明 Board 内部只有 10 个对象指针，没有大数组。
    那 mPlants / mZombies / mProjectiles 很可能是单独 new 出来的一块内存，
    Board 里只存它的首地址。所以要在整个进程里找：
       一段连续内存，每个 dword 要么是 0，要么是指向合法 GameObject 的指针。
    这种形态在别的地方几乎不会出现，误报率极低。

    找到后打印基址、长度、以及非空槽的偏移 —— 非空槽偏移就是
    「第 i 个植物/僵尸/豌豆」的下标，直接给出数组语义。
    """
    try:
        import numpy as np
    except ImportError:
        print("需要 numpy：.venv\\Scripts\\python.exe -m pip install numpy")
        return 1

    gapp = p.u32(p.base + (gapp_va - 0x400000))
    if not gapp:
        print("gApp 为空")
        return 1
    board = p.u32(gapp + OFF_BOARD)
    print("gApp=0x%08X  Board=0x%08X  全进程找指针数组（>= %d 槽）..."
          % (gapp, board or 0, min_slots), flush=True)

    min_bytes = min_slots * 4
    results = []
    scanned = 0
    t0 = time.time()

    for (base, size, prot) in p.regions():
        off = 0
        while off < size:
            n = min(0x400000, size - off)
            buf = p.read(base + off, n)
            if not buf or len(buf) < min_bytes:
                off += n
                continue
            arr = np.frombuffer(buf, dtype="<u4")
            v = arr.astype("u8")
            # 候选：0，或者「4 字节对齐的堆指针范围」
            cand = (v == 0) | ((v >= 0x00010000) & (v < 0x7F000000) & ((v & 3) == 0))
            if not cand.all():
                # 找出所有「全 cand」的连续段
                idx = np.nonzero(~cand)[0]
                starts = np.concatenate(([0], idx + 1))
                ends = np.concatenate((idx, [arr.size]))
            else:
                starts, ends = np.array([0]), np.array([arr.size])
            for s, e in zip(starts, ends):
                if (e - s) * 4 < min_bytes:
                    continue
                seg = arr[s:e]
                nz = np.nonzero(seg)[0]
                if nz.size == 0:
                    continue          # 全零段没意义
                # 逐个确认非空槽真的是 GameObject
                ok = 0
                bad = 0
                for i in nz:
                    ptr = int(seg[i])
                    b = p.read(ptr, 8)
                    if b and struct.unpack("<2I", b) == (gapp, board):
                        ok += 1
                    else:
                        bad += 1
                if bad == 0 and ok > 0:
                    results.append(dict(addr=base + off + s * 4,
                                        bytes=(e - s) * 4, slots=int(e - s),
                                        nonnull=ok,
                                        offs=[int(i) * 4 for i in nz[:12]]))
            scanned += len(buf)
            off += n

    print("扫描 %.1f MB / %.1fs，命中 %d 段\n"
          % (scanned / 1048576.0, time.time() - t0, len(results)))
    for r in results:
        print("  基址 0x%08X  长 0x%-6X (%5d 槽)  非空 %d  槽偏移 %s"
              % (r["addr"], r["bytes"], r["slots"], r["nonnull"],
                 ", ".join("+0x%X" % o for o in r["offs"])))
    if not results:
        print("  没找到。说明这些数组不是「纯指针数组」，或者元素不是 GameObject。")
    return 0


def cmd_arrays(p, gapp_va, slots=1024):
    """
    验证 Board 里的「1024 槽对象指针数组」布局。

    已知：mPlants 在 Board+0x138（两棵植物分别在 +0x138、+0x13C）。
    PVZ 的 Board 通常有 mPlants / mZombies / mProjectiles 三个同尺寸数组。
    这里按 0x1000 步长试几个候选基址，统计每个窗口里
    「非空且指向合法 GameObject」的槽位数，用来确认布局。
    """
    gapp = p.u32(p.base + (gapp_va - 0x400000))
    if not gapp:
        print("gApp 为空")
        return 1
    board = p.u32(gapp + OFF_BOARD)
    if not board:
        print("Board 为空：先进入关卡")
        return 1

    nbytes = slots * 4
    print("Board=0x%08X  每数组按 %d 槽 = 0x%X 字节计\n" % (board, slots, nbytes))
    print("  %-12s %-10s %s" % ("候选基址", "非空槽", "首几个非空对象"))

    bases = [0x0F38, 0x138, 0x1138, 0x2138, 0x3138, 0x4138, 0x5138]
    for base in bases:
        raw = p.read(board + base, nbytes)
        if not raw:
            print("  Board+0x%05X  <不可读>" % base)
            continue
        nonnull = 0
        samples = []
        for i in range(slots):
            v = struct.unpack_from("<I", raw, i * 4)[0]
            if v == 0:
                continue
            if not looks_like_obj(v):
                samples.append("槽%d=坏指针0x%08X" % (i, v))
                continue
            b = p.read(v, 0x10)
            if not b:
                samples.append("槽%d=不可读" % i)
                continue
            d0, d1 = struct.unpack_from("<2I", b, 0)
            if d0 == gapp and d1 == board:
                nonnull += 1
                if len(samples) < 4:
                    x, y = struct.unpack_from("<2i", b, 8) if len(b) >= 0x10 else (0, 0)
                    samples.append("槽%d→0x%08X" % (i, v))
            else:
                samples.append("槽%d→0x%08X(非GO)" % (i, v))
        print("  Board+0x%05X  %-10d %s" %
              (base, nonnull, "  ".join(samples[:5]) if samples else "-"))
    return 0


def cmd_boardobj(p, gapp_va):
    """
    从 Board 出发反查对象，不做任何布局假设。

    做法：把 Board 的 0x57000 字节里所有「对齐的堆指针」都捞出来，
    逐个解引用，看它 +0x00 是不是一个落在 EXE 映像内的地址（即虚表）。
    是的话就按虚表分组打印，并给出这个指针在 Board 里的偏移。

    这样即使不知道 +0x04 是不是 mBoard，也能把类标识和数组位置都拿到。
    """
    gapp = p.u32(p.base + (gapp_va - 0x400000))
    if not gapp:
        print("gApp 为空")
        return 1
    board = p.u32(gapp + OFF_BOARD)
    if not board:
        print("Board 为空：先进入关卡")
        return 1
    print("gApp=0x%08X  Board=0x%08X  扫 Board 内部指针..." % (gapp, board))

    raw = p.read(board, BOARD_SIZE)
    if not raw:
        print("Board 不可读")
        return 1

    groups = {}
    cand = 0
    for i in range(len(raw) // 4):
        v = struct.unpack_from("<I", raw, i * 4)[0]
        if not looks_like_obj(v) or (v & 3):
            continue
        cand += 1
        b = p.read(v, 0x40)
        if not b:
            continue
        d0 = struct.unpack_from("<I", b, 0)[0]
        if not (IMAGE_LO <= d0 < IMAGE_HI):
            continue
        groups.setdefault(d0, []).append((i * 4, v, b))

    print("Board 里 %d 个对齐堆指针，其中 %d 个指向「首字段是映像内地址」的对象\n" %
          (cand, sum(len(v) for v in groups.values())))

    if not groups:
        print("没有找到。可能这两棵植物不在 Board 的直接指针里。")
        return 1

    print("--- 按对象 +0x00（疑似虚表）分组 ---")
    for d0, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        print("\n  虚表候选 0x%08X   数量 %d" % (d0, len(items)))
        for off, v, b in items[:6]:
            i08, i0c, i10, i14 = struct.unpack_from("<4i", b, 8)
            x, y, vx, vy = struct.unpack_from("<4f", b, 0x24)
            d04 = struct.unpack_from("<I", b, 4)[0]
            print("    Board+0x%05X -> 0x%08X  +0x04=0x%08X  +0x08=%-5d +0x0C=%-5d "
                  "x=%7.1f y=%6.1f vx=%5.2f vy=%5.2f" %
                  (off, v, d04, i08, i0c, x, y, vx, vy))

    # ---- 第二遍：不要求虚表，改用「屏幕内整数坐标」找植物/僵尸 ----
    print("\n--- 按坐标特征筛选（+0x08 x、+0x0C y 都在屏幕内）---")
    hits = []
    for i in range(len(raw) // 4):
        v = struct.unpack_from("<I", raw, i * 4)[0]
        if not looks_like_obj(v) or (v & 3):
            continue
        b = p.read(v, 0x40)
        if not b:
            continue
        i08, i0c = struct.unpack_from("<2i", b, 8)
        if not (-50 <= i08 <= 900 and -50 <= i0c <= 700):
            continue
        hits.append((i * 4, v, b, i08, i0c))

    seen = set()
    for off, v, b, i08, i0c in hits:
        if v in seen:
            continue
        seen.add(v)
        d = struct.unpack_from("<10I", b, 0)
        print("    Board+0x%05X -> 0x%08X  dwords: %s" %
              (off, v, " ".join("%08X" % t for t in d)))
        print("        +0x08 x=%-5d +0x0C y=%-5d   +0x24..=%s" %
              (i08, i0c,
               " ".join("%.2f" % t for t in struct.unpack_from("<4f", b, 0x24))))
        if len(seen) >= 25:
            break
    print("    共 %d 个不同对象符合坐标特征" % len(seen))
    return 0


def cmd_findval(p, value, limit=12):
    """
    在整进程内存里搜一个 dword 值，并把命中点前后的内存按 dword 打出来。

    用途：想知道「Board 指针被谁引用、引用它的对象长什么样」时最直接。
    如果 GameObject 的 +0x04 是 mBoard，那么命中点就是 obj+4，
    前面那个 dword（命中点 -4）就应该是虚表指针。
    """
    try:
        import numpy as np
    except ImportError:
        print("需要 numpy")
        return 1

    print("搜索 dword = 0x%08X ..." % value)
    hits = []
    scanned = 0
    for (base, size, prot) in p.regions():
        off = 0
        while off < size:
            n = min(0x200000, size - off)
            buf = p.read(base + off, n)
            if buf and len(buf) >= 4:
                arr = np.frombuffer(buf, dtype="<u4")
                idx = np.nonzero(arr == value)[0]
                for i in idx:
                    hits.append(int(base + off + int(i) * 4))
                scanned += len(buf)
            off += n

    print("扫描 %.1f MB，命中 %d 处\n" % (scanned / 1048576.0, len(hits)))
    for h in hits[:limit]:
        print("--- 命中 @0x%08X （下面以命中点为 +0，往前 4 个 dword，往后 8 个）---" % h)
        b = p.read(h - 0x10, 0x30)
        if not b:
            print("    不可读")
            continue
        vals = struct.unpack("<12I", b)
        for k, v in enumerate(vals):
            rel = k * 4 - 0x10
            f = struct.unpack("<f", struct.pack("<I", v))[0]
            tag = ""
            if rel == 0:
                tag = "  <== 命中点"
            elif rel == -4:
                tag = "  ← 若是对象，这里是虚表"
            print("    %+03X 0x%08X  int=%-12d float=%.6g%s" %
                  (rel, v, struct.unpack("<i", struct.pack("<I", v))[0], f, tag))
    return 0


def cmd_scanobj(p, gapp_va, seconds=1.0):
    """
    全进程扫描 GameObject —— 不依赖任何数组偏移。

    ★ 实测确认的 GameObject 布局（本汉化版，见 boardobj 的输出）：
        +0x00  == gApp      （不是虚表！PVZ 用显式类型分发，没有虚函数）
        +0x04  == Board
        +0x08  mX   (int)
        +0x0C  mY   (int)
        +0x10  mWidth  (int，植物是 0x50=80)
        +0x14  mHeight (int)
        +0x18  ...
        +0x24  浮点字段（抛射物的 mPosX 在这里）

    判据用「+0x00==gApp 且 +0x04==Board」，误判率极低。
    """
    try:
        import numpy as np
    except ImportError:
        print("需要 numpy：.venv\\Scripts\\python.exe -m pip install numpy")
        return 1

    gapp = p.u32(p.base + (gapp_va - 0x400000))
    if not gapp:
        print("gApp 为空")
        return 1
    board = p.u32(gapp + OFF_BOARD)
    if not board:
        print("Board 为空：先进入关卡")
        return 1
    print("gApp=0x%08X  Board=0x%08X  全进程扫描（+0x00==gApp 且 +0x04==Board）..."
          % (gapp, board))

    found = []
    scanned = 0
    t0 = time.time()
    for (base, size, prot) in p.regions():
        off = 0
        while off < size:
            n = min(0x200000, size - off)
            buf = p.read(base + off, n)
            if buf and len(buf) >= 0x40:
                arr = np.frombuffer(buf, dtype="<u4")
                m = arr.size - 16
                if m > 0:
                    mask = (arr[0:m] == gapp) & (arr[1:m + 1] == board)
                    if mask.any():
                        for i in np.nonzero(mask)[0]:
                            found.append(int(base + off + int(i) * 4))
                scanned += len(buf)
            off += n
    print("扫描 %.1f MB / %.2fs，命中 %d 个 GameObject\n" %
          (scanned / 1048576.0, time.time() - t0, len(found)))
    if not found:
        print("没找到。关卡里没有活着的对象（先种植物/等僵尸）。")
        return 1

    print("--- 对象清单（最多 40 个）---")
    for a in found[:40]:
        b = p.read(a, 0x40)
        if not b:
            continue
        x, y, w, h = struct.unpack_from("<4i", b, 8)
        t18, t1c, t20 = struct.unpack_from("<3i", b, 0x18)
        f24, f28, f2c, f30 = struct.unpack_from("<4f", b, 0x24)
        print("  0x%08X  x=%-4d y=%-4d %dx%-3d  +0x18=%-6d +0x1C=%-6d +0x20=%-8d "
              "f24=%8.2f f28=%7.2f f2c=%6.2f f30=%6.2f" %
              (a, x, y, w, h, t18, t1c, t20, f24, f28, f2c, f30))

    # 采样两次看谁在动
    x1 = {}
    for a in found:
        v = p.i32(a + 0x8)
        if v is not None:
            x1[a] = v
    time.sleep(seconds)
    print("\n--- 移动中的对象（间隔 %.1fs，|dx| > 1）---" % seconds)
    movers = []
    for a in found:
        x2 = p.i32(a + 0x8)
        if x2 is None or a not in x1:
            continue
        d = x2 - x1[a]
        if abs(d) > 1:
            movers.append((d, a))
    movers.sort(key=lambda t: -abs(t[0]))
    for d, a in movers[:25]:
        b = p.read(a, 0x40)
        x, y, w, h = struct.unpack_from("<4i", b, 8)
        f24, f28, f2c, f30 = struct.unpack_from("<4f", b, 0x24)
        print("  0x%08X dx=%+6d  x=%-4d y=%-4d  f24=%8.2f f28=%7.2f f2c=%6.2f f30=%6.2f" %
              (a, d, x, y, f24, f28, f2c, f30))
    if not movers:
        print("  （没有对象移动 —— 游戏暂停中）")
    return 0


def cmd_watch(p, addr, seconds=3, off_start=0x00, off_end=0x60):
    print("监视 0x%08X，%.1f 秒" % (addr, seconds))
    hdr = "  +off   " + "".join("%9d" % (i + 1) for i in range(0))
    samples = []
    t0 = time.time()
    while time.time() - t0 < seconds:
        row = []
        for off in range(off_start, off_end, 4):
            row.append(p.f32(addr + off))
        samples.append(row)
        time.sleep(0.05)
    if not samples:
        print("没采到样本")
        return 1
    print("\n  +off      first      last       delta    n_change")
    for idx, off in enumerate(range(off_start, off_end, 4)):
        vals = [s[idx] for s in samples if s[idx] is not None]
        if len(vals) < 2:
            continue
        changes = sum(1 for a, b in zip(vals, vals[1:]) if a != b)
        if changes == 0:
            continue
        print("  +0x%03X  %10.3f %10.3f %10.3f   %d/%d" %
              (off, vals[0], vals[-1], vals[-1] - vals[0], changes, len(vals) - 1))
    return 0


def cmd_findpea(p, gapp_va, seconds=5, span=BOARD_SIZE):
    """
    在 Board 里找「+0x24 单调递增、量级 0~1000」的对象 —— 那就是飞行中的豌豆。
    用法：游戏里先种豌豆射手让它开火，然后立刻跑这个。
    """
    gapp = p.u32(p.base + (gapp_va - 0x400000))
    if not gapp:
        print("gApp 为空")
        return 1
    board = p.u32(gapp + OFF_BOARD)
    if not board:
        print("Board 为空：先进入关卡")
        return 1
    raw = p.read(board, span)
    if not raw:
        print("Board 区间不可读")
        return 1

    # 收集候选，并用「抛射物签名」预筛：
    #   +0x24 x 在屏幕内、+0x28 y 在屏幕内、+0x2C vx 是 0~30 的正小数
    # 这一步能把上万个噪声指针压到个位数，之后按帧采样才有意义。
    cands = []
    for i in range(len(raw) // 4):
        v = struct.unpack_from("<I", raw, i * 4)[0]
        if not looks_like_obj(v):
            continue
        b = p.read(v, 0x40)
        if not b:
            continue
        x, y, vx, vy = struct.unpack_from("<4f", b, 0x24)
        if 0.0 <= x <= 1200.0 and 0.0 <= y <= 800.0 and 0.0 < vx < 30.0:
            cands.append((i * 4, v))

    # 二级间接：抛射物也可能挂在链表节点里（节点里存着真正的对象指针）。
    seen = {o for _, o in cands}
    extra = []
    for off, o in cands:
        b = p.read(o, 0x40)
        if not b:
            continue
        for k in range(0, 0x40, 4):
            vv = struct.unpack_from("<I", b, k)[0]
            if not looks_like_obj(vv) or vv in seen:
                continue
            bb = p.read(vv, 0x40)
            if not bb:
                continue
            x, y, vx, vy = struct.unpack_from("<4f", bb, 0x24)
            if 0.0 <= x <= 1200.0 and 0.0 <= y <= 800.0 and 0.0 < vx < 30.0:
                seen.add(vv)
                extra.append((off, vv))
    if extra:
        print("二级展开新增 %d 个候选" % len(extra))
    cands.extend(extra)
    print("Board 里 %d 个符合抛射物签名的候选，采样 %.1f 秒..." % (len(cands), seconds))

    first = {}
    last = {}
    inc = {}
    t0 = time.time()
    rounds = 0
    while time.time() - t0 < seconds:
        for off, o in cands:
            b = p.read(o + 0x24, 0x10)
            if not b:
                continue
            x, y, vx, vy = struct.unpack("<4f", b)
            if o not in first:
                first[o] = (x, y, vx, vy)
                inc[o] = 0
            else:
                px = last.get(o, (x, y))[0]
                if 0.05 < (x - px) < 40.0:
                    inc[o] += 1
            last[o] = (x, y, vx, vy)
        rounds += 1
        time.sleep(0.03)

    print("采样 %d 轮\n" % rounds)
    hits = []
    for off, o in cands:
        if o not in first or o not in last:
            continue
        dx = last[o][0] - first[o][0]
        if inc[o] >= max(3, rounds // 3) and dx > 20:
            hits.append((dx, off, o))
    hits.sort(reverse=True)
    if not hits:
        print("没抓到向右飞的对象。确认：①已在关卡里 ②豌豆射手真的在开火 ③采样窗口够长")
        return 1
    print("--- 向右飞行的对象（按位移排序）---")
    for dx, off, o in hits[:10]:
        x0, y0, _, _ = first[o]
        x1, y1, vx, vy = last[o]
        print("  Board+0x%05X -> 0x%08X  dx=%7.1f  x %.1f->%.1f  y %.1f->%.1f  "
              "+0x2C=%6.3f +0x30=%6.3f  递增%d/%d" %
              (off, o, dx, x0, x1, y0, y1, vx, vy, inc[o], rounds))
    print("\n如果这里出现了稳定的 Board+0x???? 偏移，那就是抛射物数组的位置。")
    return 0


def parse_hex_arg(s):
    """地址一律按十六进制解析，允许带 0x 前缀"""
    s = s.strip()
    if s.lower().startswith("0x"):
        s = s[2:]
    return int(s, 16)


def main():
    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        return 1
    pid = None
    gapp_va = GAPP_VA
    if "--pid" in argv:
        k = argv.index("--pid"); pid = int(argv[k + 1]); del argv[k:k + 2]
    if "--gapp" in argv:
        k = argv.index("--gapp"); gapp_va = int(argv[k + 1], 16); del argv[k:k + 2]
    keep_focus = "--focus" in argv
    if keep_focus:
        argv.remove("--focus")
    cmd = argv[0]

    if cmd == "selftest" and pid is None:
        pid = os.getpid()

    p = Proc(pid)
    try:
        if cmd == "selftest":
            return cmd_selftest(p)
        if cmd == "dump":
            return cmd_dump(p, gapp_va)
        if cmd == "scan":
            return cmd_scan(p, gapp_va)
        if cmd == "watch":
            return cmd_watch(p, int(argv[1], 16),
                             float(argv[2]) if len(argv) > 2 else 3.0)
        if cmd == "findpea":
            return cmd_findpea(p, gapp_va,
                               float(argv[1]) if len(argv) > 1 else 5.0)
        if cmd == "capture":
            secs = float(argv[1]) if len(argv) > 1 else 120.0
            out = argv[2] if len(argv) > 2 else "build/pea_capture.json"
            return cmd_capture(p, gapp_va, secs, out)
        if cmd == "scanobj":
            return cmd_scanobj(p, gapp_va,
                               seconds=float(argv[1]) if len(argv) > 1 else 1.0)
        if cmd == "findval":
            return cmd_findval(p, parse_hex_arg(argv[1]))
        if cmd == "boardobj":
            return cmd_boardobj(p, gapp_va)
        if cmd == "arrays":
            return cmd_arrays(p, gapp_va)
        if cmd == "boardmap":
            return cmd_boardmap(p, gapp_va)
        if cmd == "findarrays":
            n = int(argv[1]) if len(argv) > 1 else 256
            return cmd_findarrays(p, gapp_va, n)
        if cmd == "peawatch":
            secs = float(argv[1]) if len(argv) > 1 else 150.0
            out = argv[2] if len(argv) > 2 else "build/pea_watch.json"
            return cmd_peawatch(p, gapp_va, secs, out, keep_focus)
        if cmd == "focus":
            return cmd_focus(p)
        if cmd == "spawnwatch":
            secs = float(argv[1]) if len(argv) > 1 else 240.0
            out = argv[2] if len(argv) > 2 else "build/spawn_watch.json"
            return cmd_spawnwatch(p, gapp_va, secs, out)
        print(__doc__)
        return 1
    finally:
        p.close()


if __name__ == "__main__":
    sys.exit(main())
