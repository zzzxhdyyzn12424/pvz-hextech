"""
fastpeek.py —— 高频采样局部内存，找每帧匀速递增的 float

用法：
    python fastpeek.py <pid> <base_hex> <size_hex> [frames]

只扫给定区域，所以每次只要几毫秒，可以真正做到 ~30ms 一采样。
"""
import ctypes
import ctypes.wintypes as wt
import struct
import sys
import time

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010

k32.OpenProcess.restype = wt.HANDLE
k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
k32.ReadProcessMemory.restype = wt.BOOL
k32.ReadProcessMemory.argtypes = [wt.HANDLE, wt.LPCVOID, wt.LPVOID,
                                  ctypes.c_size_t,
                                  ctypes.POINTER(ctypes.c_size_t)]


def main():
    pid = int(sys.argv[1])
    base = int(sys.argv[2], 16)
    size = int(sys.argv[3], 16)
    frames = int(sys.argv[4]) if len(sys.argv) > 4 else 40

    h = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not h:
        print("[x] OpenProcess failed")
        return 1

    buf = ctypes.create_string_buffer(size)
    got = ctypes.c_size_t(0)

    series = {}     # offset -> [(t, value)]
    nslots = size // 4

    t0 = time.perf_counter()
    for f in range(frames):
        ok = k32.ReadProcessMemory(h, ctypes.c_void_p(base), buf, size,
                                   ctypes.byref(got))
        if not ok:
            print(f"[x] read failed at frame {f}")
            break
        raw = buf.raw[:got.value]
        now = time.perf_counter() - t0
        for i in range(nslots):
            v, = struct.unpack_from("<f", raw, i * 4)
            # 只关心像坐标/速度的合理浮点
            if not (-10000.0 < v < 10000.0):
                continue
            series.setdefault(i, []).append((now, v))
        time.sleep(0.03)

    k32.CloseHandle(h)

    print(f"[i] {len(series)} 个 float 槽位有数据，{frames} 帧，总时长 "
          f"{time.perf_counter()-t0:.2f}s")

    # 找线性递增：拟合斜率，要求 R^2 高且斜率非零
    cand = []
    for i, pts in series.items():
        if len(pts) < frames * 0.8:
            continue
        xs = [p[0] for p in pts]
        vs = [p[1] for p in pts]
        n = len(xs)
        mx = sum(xs) / n
        my = sum(vs) / n
        sxx = sum((x - mx) ** 2 for x in xs)
        if sxx == 0:
            continue
        sxy = sum((xs[k] - mx) * (vs[k] - my) for k in range(n))
        slope = sxy / sxx
        if abs(slope) < 1.0:
            continue
        # R^2
        ss_tot = sum((v - my) ** 2 for v in vs)
        ss_res = sum((vs[k] - (my + slope * (xs[k] - mx))) ** 2
                     for k in range(n))
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0
        if r2 < 0.97:
            continue
        cand.append((r2, slope, i, vs[0], vs[-1]))

    cand.sort(reverse=True)
    print(f"[+] 线性漂移槽位 {len(cand)} 个：")
    for r2, slope, i, v0, v1 in cand[:20]:
        print(f"    +0x{i*4:03X}  slope={slope:10.3f}/s  "
              f"{v0:10.3f} -> {v1:10.3f}  R2={r2:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
