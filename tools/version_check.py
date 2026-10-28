# -*- coding: utf-8 -*-
"""
version_check.py —— 版本指纹校验

为什么不用 md5？

    本项目所有偏移量（Plant+0x5C、Zombie+0x2C、gLawnApp 0x6A9EC0 …）都是
    从「PVZ 1.0.0.1051 中文版」这一份 exe 里逆向出来的。换个版本，偏移全错，
    写进去就是随机改内存，轻则崩游戏，重则坏存档。

    但 md5 太严：本项目自己就给 exe 打过「自动收阳光」单字节补丁
    （文件偏移 0x3158F，0x75 → 0xEB），md5 立刻变了。玩家装了任何别的补丁也一样。
    用 md5 会把「版本对但打过补丁」的玩家全部误杀。

    所以改用「代码段指纹」：挑 4 处与玩法无关、且必须精确匹配才能说明结构一致的
    指令片段。补丁一般不会动这些地方；换了版本则必然对不上。

指纹来源（全部 VA，ImageBase 0x400000，无 ASLR）：

    0x44E969  a3 c0 9e 6a 00   mov [0x6A9EC0], eax   ← gLawnApp = new LawnApp
    0x407CA9  68 00 70 05 00   push 0x57000          ← sizeof(Board) = 356352
    0x45F8A9  83 46 58 ff      add [esi+0x58], -1    ← 植物射击倒计时
    0x45F8BF  8b 4e 5c         mov ecx, [esi+0x5c]   ← 读射击间隔（海克斯攻速靠它）

用法：

    python tools/version_check.py                # 校验当前运行中的游戏
    python tools/version_check.py --exe PATH     # 校验指定 exe 文件
    python tools/version_check.py --verbose
"""

import ctypes
import ctypes.wintypes as wt
import os
import struct
import sys

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
k32.OpenProcess.restype = wt.HANDLE
k32.ReadProcessMemory.argtypes = [wt.HANDLE, wt.LPCVOID, wt.LPVOID,
                                  ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k32.ReadProcessMemory.restype = wt.BOOL
k32.CloseHandle.argtypes = [wt.HANDLE]
k32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD,
                                           wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
k32.QueryFullProcessImageNameW.restype = wt.BOOL

EXE_NAME = "PlantsVsZombies.exe"
IMAGE_BASE = 0x400000

# (VA, 期望字节, 说明, 是否必须)
FINGERPRINTS = [
    (0x44E969, "a3c09e6a00", "gLawnApp = new LawnApp", True),
    (0x407CA9, "6800700500", "sizeof(Board) = 0x57000", True),
    (0x45F8A9, "834658ff", "植物射击倒计时 add [esi+0x58],-1", True),
    (0x45F8BF, "8b4e5c", "植物读射击间隔 mov ecx,[esi+0x5c]", True),
]

# 已知打过补丁的文件偏移（只做提示，不参与判定）
KNOWN_PATCHES = [
    (0x3158F, "自动收集阳光单字节补丁 0x75->0xEB"),
]

REF_SIZE = 3007800          # 1.0.0.1051-zh 原版 exe 大小，仅作提示


# ---------------------------------------------------------------- PE 解析
class _PEInfo:
    def __init__(self, data):
        self.ok = False
        self.error = ""
        self.size = len(data)
        try:
            self._parse(data)
            self.ok = True
        except Exception as e:              # noqa: BLE001
            self.error = "不是有效的 PE 文件：%s" % e

    def _parse(self, d):
        if len(d) < 0x40 or d[:2] != b"MZ":
            raise ValueError("缺少 MZ 头")
        e = struct.unpack_from("<I", d, 0x3C)[0]
        if d[e:e + 4] != b"PE\0\0":
            raise ValueError("缺少 PE 签名")
        nsec = struct.unpack_from("<H", d, e + 6)[0]
        opt_size = struct.unpack_from("<H", d, e + 20)[0]
        opt = e + 24
        magic = struct.unpack_from("<H", d, opt)[0]
        if magic == 0x10B:
            self.image_base = struct.unpack_from("<I", d, opt + 28)[0]
        elif magic == 0x20B:
            self.image_base = struct.unpack_from("<Q", d, opt + 24)[0]
        else:
            raise ValueError("未知可选头 magic 0x%X" % magic)
        sec = opt + opt_size
        self.sections = []
        for i in range(nsec):
            b = sec + i * 40
            name = d[b:b + 8].rstrip(b"\x00").decode("latin1", "replace")
            vsize, va, rawsize, rawptr = struct.unpack_from("<IIII", d, b + 8)
            self.sections.append((name, va, vsize, rawptr, rawsize))

    def va_to_off(self, va):
        rva = va - self.image_base
        for _name, sva, vsize, rawptr, rawsize in self.sections:
            if sva <= rva < sva + max(vsize, rawsize):
                delta = rva - sva
                if delta < rawsize:
                    return rawptr + delta
                return None             # 落在未初始化区（.bss 之类）
        return None

    def read_va(self, data, va, n):
        off = self.va_to_off(va)
        if off is None or off + n > len(data):
            return None
        return data[off:off + n]


# ---------------------------------------------------------------- 校验结果
class Result:
    def __init__(self):
        self.ok = False
        self.rows = []          # [(说明, 期望, 实际, 是否通过)]
        self.notes = []
        self.exe_path = None
        self.exe_size = None
        self.image_base = None

    def add(self, desc, want, got, passed):
        self.rows.append((desc, want, got, passed))

    def summary(self):
        bad = [r for r in self.rows if not r[3]]
        if not bad:
            return "版本校验通过"
        return "版本校验失败：%d/%d 处指纹不符" % (len(bad), len(self.rows))


def _judge(res, fingerprints_bytes, image_base):
    """fingerprints_bytes: [(va, want_hex, desc)] -> 填进 res"""
    res.image_base = image_base
    for va, want, desc, required in FINGERPRINTS:
        got = fingerprints_bytes.get(va)
        if got is None:
            res.add(desc, want, "读取失败", False)
        else:
            res.add(desc, want, got.hex(), got.hex().lower().startswith(want.lower()))
    res.ok = all(r[3] for r in res.rows)


# ---------------------------------------------------------------- 文件校验
def check_file(path):
    res = Result()
    if not path or not os.path.isfile(path):
        res.notes.append("找不到文件：%s" % path)
        res.add("exe 存在", "是", "否", False)
        return res

    res.exe_path = os.path.abspath(path)
    with open(path, "rb") as f:
        data = f.read()
    res.exe_size = len(data)

    pe = _PEInfo(data)
    if not pe.ok:
        res.notes.append(pe.error)
        res.add("PE 结构", "可解析", "否", False)
        return res

    res.notes.append("ImageBase = 0x%X，节数 %d" % (pe.image_base, len(pe.sections)))
    if pe.image_base != IMAGE_BASE:
        res.notes.append("⚠ ImageBase 不是 0x%X，偏移量会全部失效" % IMAGE_BASE)

    # 文件名
    base = os.path.basename(path).lower()
    res.add("文件名", EXE_NAME.lower(), base, base == EXE_NAME.lower())

    # 大小（只提示，不判定）
    if res.exe_size != REF_SIZE:
        res.notes.append(
            "文件大小 %d，参考原版 %d（差 %+d 字节）。"
            "打过补丁就会不一样，只要指纹全对就没问题。"
            % (res.exe_size, REF_SIZE, res.exe_size - REF_SIZE))
    for off, why in KNOWN_PATCHES:
        if off < len(data) and data[off] == 0xEB:
            res.notes.append("检测到已知补丁：%s" % why)

    got = {}
    for va, _want, _desc, _req in FINGERPRINTS:
        b = pe.read_va(data, va, 8)
        if b is not None:
            got[va] = b
    _judge(res, got, pe.image_base)
    return res


# ---------------------------------------------------------------- 进程校验
def find_pid(name=EXE_NAME):
    TH32CS_SNAPPROCESS = 0x02

    class PE32(ctypes.Structure):
        _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD),
                    ("th32ProcessID", wt.DWORD),
                    ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                    ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                    ("th32ParentProcessID", wt.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                    ("szExeFile", ctypes.c_char * 260)]

    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == wt.HANDLE(-1).value:
        return None
    e = PE32()
    e.dwSize = ctypes.sizeof(PE32)
    ok = k32.Process32First(snap, ctypes.byref(e))
    found = None
    while ok:
        if e.szExeFile.decode("mbcs", "ignore").lower() == name.lower():
            found = e.th32ProcessID
            break
        ok = k32.Process32Next(snap, ctypes.byref(e))
    k32.CloseHandle(snap)
    return found


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_VM_READ = 0x0010


def process_exe_path(pid):
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = wt.DWORD(1024)
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return buf.value
        return None
    finally:
        k32.CloseHandle(h)


def check_process(pid):
    res = Result()
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ, False, pid)
    if not h:
        res.notes.append("打不开进程 pid=%d（可能权限不足，试试右键管理员运行）" % pid)
        res.add("进程可读", "是", "否", False)
        return res
    try:
        res.exe_path = process_exe_path(pid)
        if res.exe_path and os.path.isfile(res.exe_path):
            res.exe_size = os.path.getsize(res.exe_path)

        got = {}
        for va, _want, _desc, _req in FINGERPRINTS:
            buf = ctypes.create_string_buffer(8)
            read = ctypes.c_size_t(0)
            ok = k32.ReadProcessMemory(h, ctypes.c_void_p(va), buf, 8,
                                       ctypes.byref(read))
            if ok and read.value >= 4:
                got[va] = buf.raw[:8]
        _judge(res, got, IMAGE_BASE)

        if res.exe_path:
            res.notes.append("进程映像：%s" % res.exe_path)
    finally:
        k32.CloseHandle(h)
    return res


# ---------------------------------------------------------------- 输出
def format_report(res, verbose=True):
    lines = []
    for desc, want, got, passed in res.rows:
        lines.append("  %s  %-38s 期望 %-12s 实际 %s"
                     % ("OK  " if passed else " XX ", desc, want, got))
    if verbose:
        for n in res.notes:
            lines.append("  · %s" % n)
    lines.append("")
    lines.append("  " + res.summary())
    if not res.ok:
        lines.append("")
        lines.append("  本 mod 的偏移量只对「PVZ 1.0.0.1051 中文版」有效。")
        lines.append("  请确认：")
        lines.append("    1. 游戏是不是 1.0.0.1051（右键 PlantsVsZombies.exe → 属性 → 详细信息）")
        lines.append("    2. 是不是中文版底包（英文原版、年度版、杂交版偏移都不同）")
        lines.append("    3. exe 有没有被别的 mod 改过")
    return "\n".join(lines)


def main(argv):
    verbose = "--verbose" in argv or "-v" in argv
    if "--exe" in argv:
        i = argv.index("--exe")
        path = argv[i + 1] if i + 1 < len(argv) else ""
        res = check_file(path)
    else:
        pid = find_pid()
        if not pid:
            print("找不到运行中的 %s。" % EXE_NAME)
            print("先开游戏，或者用 --exe 指定文件路径。")
            return 1
        res = check_process(pid)
        if res.exe_path:
            print("已连上 pid=%d" % pid)
    print(format_report(res, verbose))
    return 0 if res.ok else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
