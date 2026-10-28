"""
memscan.py —— 不依赖游戏代码的豌豆对象定位器

思路：豌豆对象里 +0x24 是 float x、+0x28 是 float y，每帧 x 增加。
      直接扫堆内存，找两次采样间「x 递增、y 基本不动」的候选地址。

用法：
    python memscan.py <pid> [--rounds 6]
"""
import ctypes
import ctypes.wintypes as wt
import sys
import time
from collections import defaultdict

k32 = ctypes.WinDLL("kernel32", use_last_error=True)

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010

MEM_COMMIT = 0x1000
PAGE_GUARD = 0x100
PAGE_NOACCESS = 0x01

k32.OpenProcess.restype = wt.HANDLE
k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
k32.ReadProcessMemory.restype = wt.BOOL
k32.ReadProcessMemory.argtypes = [wt.HANDLE, wt.LPCVOID, wt.LPVOID,
                                  ctypes.c_size_t,
                                  ctypes.POINTER(ctypes.c_size_t)]


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [("BaseAddress", wt.LPVOID),
                ("AllocationBase", wt.LPVOID),
                ("AllocationProtect", wt.DWORD),
                ("RegionSize", ctypes.c_size_t),
                ("State", wt.DWORD),
                ("Protect", wt.DWORD),
                ("Type", wt.DWORD)]


def enum_regions(h):
    """枚举所有可读的已提交私有内存页"""
    out = []
    addr = 0
    mbi = MEMORY_BASIC_INFORMATION()
    mbi_size = ctypes.sizeof(mbi)
    while k32.VirtualQueryEx(h, ctypes.c_void_p(addr),
                             ctypes.byref(mbi), mbi_size) == mbi_size:
        base = ctypes.cast(mbi.BaseAddress, ctypes.c_void_p).value or 0
        size = mbi.RegionSize
        if (mbi.State == MEM_COMMIT
                and not (mbi.Protect & PAGE_GUARD)
                and not (mbi.Protect & PAGE_NOACCESS)
                and size < 64 * 1024 * 1024):
            out.append((base, size))
        addr = base + size
        if addr > 0x7FFFFFFF:
            break
    return out


def read(h, addr, size):
    buf = ctypes.create_string_buffer(size)
    got = ctypes.c_size_t(0)
    if k32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, size,
                             ctypes.byref(got)):
        return buf.raw[:got.value]
    return None


def scan_snapshot(h, regions):
    """扫一遍，返回 {addr: (x, y)}，只要 float 落在合理区间"""
    found = {}
    import struct
    for base, size in regions:
        data = read(h, base, size)
        if not data:
            continue
        n = len(data) - 8
        for off in range(0, n, 4):
            x, = struct.unpack_from("<f", data, off)
            if not (0.0 <= x <= 900.0):
                continue
            y, = struct.unpack_from("<f", data, off + 4)
            if not (20.0 <= y <= 620.0):
                continue
            if x == 0.0 or y == 0.0:
                continue
            found[base + off] = (x, y)
    return found


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    pid = int(sys.argv[1])
    rounds = 6
    if "--rounds" in sys.argv:
        rounds = int(sys.argv[sys.argv.index("--rounds") + 1])

    h = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not h:
        print(f"[x] OpenProcess 失败: {ctypes.get_last_error()}")
        return 1

    regions = enum_regions(h)
    total = sum(s for _, s in regions)
    print(f"[i] 可读区域 {len(regions)} 个，共 {total/1024/1024:.1f} MB")

    xs_hist = defaultdict(list)
    ys_hist = defaultdict(list)

    for r in range(rounds):
        t0 = time.time()
        snap = scan_snapshot(h, regions)
        dt = time.time() - t0
        for addr, (x, y) in snap.items():
            xs_hist[addr].append(x)
            ys_hist[addr].append(y)
        print(f"    round {r+1}: {len(snap)} candidates  ({dt*1000:.0f} ms)")
        time.sleep(0.15)

    k32.CloseHandle(h)

    winners = []
    for addr, xs in xs_hist.items():
        if len(xs) < rounds:
            continue
        ys = ys_hist[addr]
        deltas = [xs[i + 1] - xs[i] for i in range(len(xs) - 1)]
        if not deltas:
            continue
        inc = sum(1 for d in deltas if 0.5 <= d <= 25.0)
        if inc < len(deltas) - 1 or inc < 3:
            continue
        yspan = max(ys) - min(ys)
        winners.append((y_span_score(yspan), addr, xs[0], xs[-1],
                        sum(deltas) / len(deltas), inc, yspan, ys[-1]))

    # y 越稳越像豌豆（水平飞行的子弹）
    winners.sort()
    print(f"\n[+] 水平右移候选 {len(winners)} 个（按 y 稳定度排序）：")
    for score, addr, x0, x1, avg, inc, yspan, y1 in winners[:25]:
        print(f"    0x{addr:08X}  x {x0:7.2f}->{x1:7.2f}  "
              f"dx_avg={avg:6.3f}  y={y1:7.2f}  yspan={yspan:6.3f}")
    return 0


def y_span_score(yspan):
    return yspan


if __name__ == "__main__":
    sys.exit(main())
