#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
autocollect.py —— 给 PVZ 1.0.0.1051 加「自动收集阳光/金币」

原理（一句话）
--------------
游戏里金币/阳光对象每帧走一个更新函数，入口处有个二选一：

    0x0043158B   cmp  byte ptr [ebx+0x50], 0     ; +0x50 是"已被收集"标记
    0x0043158F   75 08    jne  0x431599          ; 有标记 → 走"已收集"分支
    0x00431591   push ebx
    0x00431592   call 0x430ac0                    ; 没标记 → 走"下落/停留"分支
    0x00431599   ...
    0x0043159B   call 0x430e40                    ; 已收集分支（飞向阳光栏）

把 0x0043158F 的那个字节 0x75(jne) 改成 0xEB(jmp)，就变成**无条件**走
"已收集"分支 —— 阳光/金币一出现就直接飞进阳光栏，等于自动收集。

0x430ac0 里能直接看到证据：
    fld [ebp+0x2c] / fadd [ebp+0x24] / fstp [ebp+0x24]
    fld [ebp+0x30] / fadd [ebp+0x28] / fstp [ebp+0x28]
即 +0x24=posX(float) +0x28=posY(float) +0x2C=velX +0x30=velY，是下落更新。

偏移来源
--------
与开源项目 PvZ Toolkit (lmintlcx, GPL-3.0) 的 1.0.0.1051 版本表一致：
    auto_collected  = {0x0043158F, 补丁 0xEB, 原值 0x75}
    sun             = 0x5560
已在本地 exe 上核对：0x43158F 现为 0x75、0x430A23 现为 0x7E，均与"原值"吻合。

用法
----
    # 看当前状态（只读，安全）
    python tools/autocollect.py status

    # 永久开启（会先把原 exe 备份成 PlantsVsZombies.exe.orig）
    python tools/autocollect.py on

    # 永久关闭（从备份还原）
    python tools/autocollect.py off

    # 只对正在运行的游戏生效，不改文件（游戏重启后失效）
    python tools/autocollect.py on --live
    python tools/autocollect.py off --live

    # 指定游戏目录
    python tools/autocollect.py on --dir "F:\\pvz\\Plants_Vs_Zombies_V1.0.0.1051_CN_V2"
"""

import argparse
import ctypes
import ctypes.wintypes as wt
import hashlib
import os
import shutil
import struct
import sys

# ---- 补丁点 ----
PATCH_VA = 0x0043158F
BYTE_ORIG = 0x75      # jne
BYTE_HACK = 0xEB      # jmp

DEFAULT_DIR = r"F:\pvz\Plants_Vs_Zombies_V1.0.0.1051_CN_V2"
EXE_NAME = "PlantsVsZombies.exe"
BAK_SUFFIX = ".orig"


# ============================================================ PE 解析
def pe_sections(path):
    """返回 (image_base, [(name, va, vsize, raw_off, raw_size), ...])"""
    with open(path, "rb") as f:
        data = f.read()
    if data[:2] != b"MZ":
        raise SystemExit("%s 不是 PE 文件" % path)
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        raise SystemExit("PE 签名不对")
    nsec = struct.unpack_from("<H", data, pe + 6)[0]
    optsz = struct.unpack_from("<H", data, pe + 20)[0]
    base = struct.unpack_from("<I", data, pe + 0x34)[0]
    sec0 = pe + 24 + optsz
    out = []
    for i in range(nsec):
        o = sec0 + i * 40
        name = data[o:o + 8].rstrip(b"\0").decode("latin1")
        vsize, va, rsize, roff = struct.unpack_from("<4I", data, o + 8)
        out.append((name, base + va, vsize, roff, rsize))
    return base, out


def va_to_file_off(path, va):
    base, secs = pe_sections(path)
    rva = va - base
    for name, sva, vsize, roff, rsize in secs:
        if sva - base <= rva < (sva - base) + max(vsize, rsize):
            return roff + (rva - (sva - base))
    raise SystemExit("VA 0x%X 不在任何节里" % va)


def read_byte_at_va(path, va):
    off = va_to_file_off(path, va)
    with open(path, "rb") as f:
        f.seek(off)
        return f.read(1)[0], off


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ============================================================ 文件模式
def cmd_status(exe, bak):
    b, off = read_byte_at_va(exe, PATCH_VA)
    print("exe        : %s" % exe)
    print("文件大小   : %d" % os.path.getsize(exe))
    print("md5        : %s" % md5(exe))
    print("补丁点     : VA 0x%08X -> 文件偏移 0x%X" % (PATCH_VA, off))
    print("当前字节   : 0x%02X" % b)
    if b == BYTE_ORIG:
        print("状态       : 原版（自动收集 关）")
    elif b == BYTE_HACK:
        print("状态       : 已开启自动收集（自动收集 开）")
    else:
        print("状态       : ⚠ 未知字节，这个 exe 可能不是 1.0.0.1051，别乱改")
    if os.path.isfile(bak):
        print("备份       : %s  (md5 %s)" % (bak, md5(bak)))
    else:
        print("备份       : 无（第一次 on 时会自动创建）")
    return 0


def cmd_on(exe, bak):
    b, off = read_byte_at_va(exe, PATCH_VA)
    if b == BYTE_HACK:
        print("已经是开启状态，不用重复打补丁。")
        return 0
    if b != BYTE_ORIG:
        print("⚠ 补丁点当前是 0x%02X，既不是原值 0x75 也不是补丁值 0xEB。" % b)
        print("  这个 exe 可能不是 1.0.0.1051，拒绝修改以免损坏文件。")
        return 1
    if not os.path.isfile(bak):
        shutil.copy2(exe, bak)
        print("已备份原版 exe -> %s" % bak)
    else:
        print("备份已存在，沿用：%s" % bak)
    with open(exe, "r+b") as f:
        f.seek(off)
        f.write(bytes([BYTE_HACK]))
    b2, _ = read_byte_at_va(exe, PATCH_VA)
    print("已写入 0x%02X，回读 = 0x%02X  %s" % (BYTE_HACK, b2, "OK" if b2 == BYTE_HACK else "失败"))
    print("自动收集已开启。启动游戏即可，阳光一出现就会自己飞进阳光栏。")
    return 0


def cmd_off(exe, bak):
    if not os.path.isfile(bak):
        b, off = read_byte_at_va(exe, PATCH_VA)
        if b == BYTE_ORIG:
            print("没有备份，但当前已经是原版状态，无需还原。")
            return 0
        print("找不到备份 %s，无法还原。" % bak)
        return 1
    shutil.copy2(bak, exe)
    b, _ = read_byte_at_va(exe, PATCH_VA)
    print("已从备份还原，当前字节 = 0x%02X  %s"
          % (b, "OK（原版）" if b == BYTE_ORIG else "⚠ 异常"))
    print("md5 = %s" % md5(exe))
    return 0


# ============================================================ 进程模式
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
k32.OpenProcess.restype = wt.HANDLE
k32.ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                  ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k32.WriteProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                   ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k32.VirtualProtectEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t,
                                 wt.DWORD, ctypes.POINTER(wt.DWORD)]
k32.CloseHandle.argtypes = [wt.HANDLE]

PROCESS_VM_OPERATION = 0x0008
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_QUERY_INFORMATION = 0x0400
PAGE_EXECUTE_READWRITE = 0x40


def find_pid(name=EXE_NAME):
    TH32CS_SNAPPROCESS = 0x02

    class PE32(ctypes.Structure):
        _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD),
                    ("th32ProcessID", wt.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
                    ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                    ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wt.DWORD), ("szExeFile", ctypes.c_char * 260)]

    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == wt.HANDLE(-1).value or not snap:
        return None
    e = PE32()
    e.dwSize = ctypes.sizeof(PE32)
    ok = k32.Process32First(snap, ctypes.byref(e))
    while ok:
        if e.szExeFile.decode("mbcs", "ignore").lower() == name.lower():
            k32.CloseHandle(snap)
            return e.th32ProcessID
        ok = k32.Process32Next(snap, ctypes.byref(e))
    k32.CloseHandle(snap)
    return None


def cmd_live(want):
    pid = find_pid()
    if not pid:
        print("没找到 %s，请先启动游戏。" % EXE_NAME)
        return 1
    h = k32.OpenProcess(PROCESS_VM_OPERATION | PROCESS_VM_READ | PROCESS_VM_WRITE
                        | PROCESS_QUERY_INFORMATION, False, pid)
    if not h:
        print("OpenProcess 失败 (err=%d)，试试管理员权限。" % ctypes.get_last_error())
        return 1
    try:
        cur = ctypes.c_ubyte(0)
        got = ctypes.c_size_t(0)
        ok = k32.ReadProcessMemory(h, ctypes.c_void_p(PATCH_VA), ctypes.byref(cur), 1,
                                   ctypes.byref(got))
        if not ok or got.value != 1:
            print("读取失败 (err=%d)" % ctypes.get_last_error())
            return 1
        print("进程 PID=%d，当前字节 = 0x%02X" % (pid, cur.value))
        if cur.value == want:
            print("已经是目标状态，不用改。")
            return 0
        if cur.value not in (BYTE_ORIG, BYTE_HACK):
            print("⚠ 字节既不是 0x75 也不是 0xEB，拒绝修改。")
            return 1
        old = wt.DWORD(0)
        if not k32.VirtualProtectEx(h, ctypes.c_void_p(PATCH_VA), 1,
                                    PAGE_EXECUTE_READWRITE, ctypes.byref(old)):
            print("VirtualProtectEx 失败 (err=%d)" % ctypes.get_last_error())
            return 1
        val = ctypes.c_ubyte(want)
        ok = k32.WriteProcessMemory(h, ctypes.c_void_p(PATCH_VA), ctypes.byref(val), 1,
                                    ctypes.byref(got))
        if not ok:
            print("WriteProcessMemory 失败 (err=%d)" % ctypes.get_last_error())
            return 1
        k32.VirtualProtectEx(h, ctypes.c_void_p(PATCH_VA), 1, old.value, ctypes.byref(old))
        back = ctypes.c_ubyte(0)
        k32.ReadProcessMemory(h, ctypes.c_void_p(PATCH_VA), ctypes.byref(back), 1,
                              ctypes.byref(got))
        print("已写入 0x%02X，回读 = 0x%02X  %s"
              % (want, back.value, "OK" if back.value == want else "失败"))
        print("⚠ 只对当前这次运行有效，游戏重启后失效。")
        return 0
    finally:
        k32.CloseHandle(h)


def main():
    ap = argparse.ArgumentParser(description="PVZ 自动收集阳光/金币")
    ap.add_argument("action", choices=["status", "on", "off"])
    ap.add_argument("--dir", default=DEFAULT_DIR, help="游戏目录")
    ap.add_argument("--live", action="store_true",
                    help="改正在运行的游戏进程，不动 exe 文件")
    a = ap.parse_args()

    exe = os.path.join(a.dir, EXE_NAME)
    bak = exe + BAK_SUFFIX

    if a.live:
        if a.action == "status":
            print("--live 模式下请用 on / off 查看写入结果。")
            return 1
        return cmd_live(BYTE_HACK if a.action == "on" else BYTE_ORIG)

    if not os.path.isfile(exe):
        raise SystemExit("找不到 %s（用 --dir 指定游戏目录）" % exe)

    if a.action != "status" and find_pid():
        print("⚠ 游戏正在运行，exe 文件被占用，先关掉游戏再改。")
        return 1

    if a.action == "status":
        return cmd_status(exe, bak)
    if a.action == "on":
        return cmd_on(exe, bak)
    return cmd_off(exe, bak)


if __name__ == "__main__":
    sys.exit(main())
