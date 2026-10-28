#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pvz_pak.py — Plants vs. Zombies main.pak 解包 / 重打包 / 校验

格式（已在 1.0.0.1051 CN 上逐字节验证）:

    整个文件用 32-bit 小端 XOR 加密，key = 0xF7F7F7F7
    实际首 4 字节 0x4D37BD37 ^ 0xF7F7F7F7 == 0xBAC04AC0  (MAGIC)

    解密后布局:

        offset 0    magic   u32   0xBAC04AC0
        offset 4    version u8    0
        offset 5    flags   u8    0
        offset 6    保留    3 bytes (00 00 00)
        offset 9    索引区开始

        索引条目（紧密连续）:
            name_len  u8            0 表示索引结束
            name      name_len bytes  ASCII, 反斜杠分隔, 如 "compiled\\particles\\Award.xml.compiled"
            size      u32   LE       数据字节数
            mtime     u64   LE       FILETIME 风格时间戳
            sep       u8            0x00

        索引结束后紧接数据区, 按索引顺序为每个条目连续存放 size 字节
        sum(size) + 索引结束位置 == 文件长度  (已核对: 109028 + 24911306 == 25020334)

用法:
    python tools/pvz_pak.py list    <main.pak> [-o index.csv]
    python tools/pvz_pak.py unpack  <main.pak> -d <outdir>
    python tools/pvz_pak.py pack    <srcdir>   -o <main.pak> [--template main.pak]
    python tools/pvz_pak.py verify  <main.pak>
"""

import argparse
import csv
import os
import struct
import sys

MAGIC = 0xBAC04AC0
KEY = 0xF7F7F7F7
INDEX_START = 9


# ---------------------------------------------------------------- 加解密

def crypt(buf: bytes) -> bytes:
    """XOR 自反：加密和解密是同一个操作。末尾不足 4 字节的部分原样保留。"""
    out = bytearray(buf)
    n = len(buf) - len(buf) % 4
    if n:
        # 用大整数一次性异或，比逐 dword 快一个数量级
        chunk = int.from_bytes(buf[:n], "little")
        mask = int.from_bytes(bytes([0xF7] * n), "little")
        out[:n] = (chunk ^ mask).to_bytes(n, "little")
    out[n:] = buf[n:]
    return bytes(out)


# ---------------------------------------------------------------- 解析

class Entry:
    __slots__ = ("name", "size", "mtime", "data_off")

    def __init__(self, name, size, mtime, data_off):
        self.name = name
        self.size = size
        self.mtime = mtime
        self.data_off = data_off

    def __repr__(self):
        return f"<Entry {self.name} {self.size}B @{self.data_off}>"


def decrypt_file(path):
    with open(path, "rb") as f:
        raw = f.read()
    return crypt(raw)


def parse(buf: bytes):
    """返回 (entries, index_end)。buf 必须是已解密的明文。"""
    if len(buf) < INDEX_START:
        raise ValueError("文件太短")
    magic, = struct.unpack_from("<I", buf, 0)
    if magic != MAGIC:
        raise ValueError(f"magic 不匹配: 0x{magic:08X} (期望 0x{MAGIC:08X}) — 文件没解密或版本不同")

    entries = []
    p = INDEX_START
    while True:
        if p >= len(buf):
            break
        nl = buf[p]
        if nl == 0:
            p += 1
            break
        if nl > 240:
            raise ValueError(f"偏移 {p} 处怀疑解析错位: name_len={nl}")
        name_b = buf[p + 1:p + 1 + nl]
        if not all(32 <= c < 127 or c in (92, 47) for c in name_b):
            raise ValueError(f"偏移 {p} 处文件名含非法字节: {name_b!r}")
        name = name_b.decode("ascii")
        p += 1 + nl
        size, = struct.unpack_from("<I", buf, p)
        p += 4
        mtime, = struct.unpack_from("<Q", buf, p)
        p += 8
        sep = buf[p]
        if sep not in (0x00, 0x80):
            raise ValueError(f"偏移 {p} 处条目结束字节异常: 0x{sep:02X} (期望 0x00 或 0x80)")
        p += 1
        entries.append(Entry(name, size, mtime, 0))
        if sep & 0x80:      # 0x80 == 索引最后一条
            break

    index_end = p
    off = index_end
    for e in entries:
        e.data_off = off
        off += e.size
    if off != len(buf):
        raise ValueError(
            f"数据区长度不符: 索引结束 {index_end} + 数据 {off - index_end} = {off}, 文件 {len(buf)}"
        )
    return entries, index_end


# ---------------------------------------------------------------- 命令

def cmd_list(args):
    buf = decrypt_file(args.pak)
    entries, index_end = parse(buf)
    print(f"文件      : {args.pak}")
    print(f"总大小    : {len(buf):,} 字节")
    print(f"条目数    : {len(entries)}")
    print(f"索引区    : [0, {index_end:,})")
    print(f"数据区    : [{index_end:,}, {len(buf):,})")
    print()

    from collections import Counter
    ext = Counter(os.path.splitext(e.name)[1].lower() for e in entries)
    print("扩展名分布:")
    for k, v in ext.most_common():
        print(f"  {k or '(无)':<14} {v:>6}")

    if args.output:
        with open(args.output, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["name", "size", "mtime", "offset"])
            for e in entries:
                w.writerow([e.name, e.size, e.mtime, e.data_off])
        print(f"\n已写出索引: {args.output}")


def cmd_unpack(args):
    buf = decrypt_file(args.pak)
    entries, index_end = parse(buf)
    outdir = args.outdir
    os.makedirs(outdir, exist_ok=True)

    total = 0
    for e in entries:
        rel = e.name.replace("\\", os.sep)
        dst = os.path.join(outdir, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as f:
            f.write(buf[e.data_off:e.data_off + e.size])
        total += e.size

    # 顺带把明文索引也存一份，pack 时可以直接复用时间戳
    with open(os.path.join(outdir, "_pak_index.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["name", "size", "mtime"])
        for e in entries:
            w.writerow([e.name, e.size, e.mtime])

    print(f"解包完成: {len(entries)} 个文件 / {total:,} 字节 -> {outdir}")
    if args.list_after:
        from collections import Counter
        ext = Counter(os.path.splitext(e.name)[1].lower() for e in entries)
        for k, v in ext.most_common():
            print(f"  {k or '(无)':<14} {v:>6}")


def cmd_pack(args):
    src = args.srcdir
    if not os.path.isdir(src):
        sys.exit(f"源目录不存在: {src}")

    # 读原始索引（如果 src 里带 _pak_index.csv，沿用条目顺序与时间戳）
    old = {}
    idx_csv = os.path.join(src, "_pak_index.csv")
    if os.path.isfile(idx_csv):
        with open(idx_csv, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                old[row["name"]] = (int(row["size"]), int(row["mtime"]))

    # 收集磁盘上真实存在的文件
    found = []
    for root, _dirs, files in os.walk(src):
        for fn in files:
            if fn == "_pak_index.csv":
                continue
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, src).replace(os.sep, "\\")
            found.append((rel, full))

    present = {rel for rel, _ in found}

    # 顺序：先按原索引顺序输出保留下来的条目，再追加新文件
    ordered = [n for n in old if n in present]
    ordered += sorted(n for n in present if n not in old)

    entries = []
    for name in ordered:
        full = os.path.join(src, name.replace("\\", os.sep))
        size = os.path.getsize(full)
        mtime = old.get(name, (0, 0))[1] or 0x01CBFC1C9DC1400D
        name_b = name.encode("ascii")
        entries.append((name_b, size, mtime, full))

    # 先算索引长度，才能确定数据起点。注意：没有 name_len==0 终止符，
    # 索引靠最后一个条目的 sep 高位置 1（0x80）来结束。
    index_len = INDEX_START
    for name_b, size, mtime, _ in entries:
        index_len += 1 + len(name_b) + 4 + 8 + 1

    out = bytearray()
    out += struct.pack("<I", MAGIC)
    out += bytes([0, 0])          # version, flags
    out += bytes([0, 0, 0])       # 保留

    last = len(entries) - 1
    for i, (name_b, size, mtime, _) in enumerate(entries):
        out += bytes([len(name_b)])
        out += name_b
        out += struct.pack("<I", size)
        out += struct.pack("<Q", mtime)
        out += b"\x80" if i == last else b"\x00"

    if len(out) != index_len:
        sys.exit(f"内部错误: 索引实际 {len(out)} != 预算 {index_len}")

    for name_b, size, mtime, full in entries:
        with open(full, "rb") as f:
            data = f.read()
        if len(data) != size:
            sys.exit(f"读文件时长度变化: {full}")
        out += data

    enc = crypt(bytes(out))
    with open(args.output, "wb") as f:
        f.write(enc)

    print(f"打包完成: {len(entries)} 个文件 -> {args.output}")
    print(f"  明文长度 {len(out):,} / 密文长度 {len(enc):,}")

    # 立刻回读自检
    chk = parse(crypt(enc))[0]
    if len(chk) != len(entries):
        sys.exit("自检失败: 条目数不一致")
    print(f"  自检通过: 重新解析得到 {len(chk)} 个条目")


def cmd_verify(args):
    raw = open(args.pak, "rb").read()
    buf = crypt(raw)
    entries, index_end = parse(buf)
    print(f"OK  {args.pak}")
    print(f"  magic 0x{MAGIC:08X} / {len(entries)} 条目 / {len(raw):,} 字节")
    print(f"  索引区 [0,{index_end:,})  数据区 [{index_end:,},{len(buf):,})")
    # 抽查每个 PNG 的签名
    bad = 0
    for e in entries:
        if e.name.lower().endswith(".png"):
            sig = buf[e.data_off:e.data_off + 8]
            if sig != b"\x89PNG\r\n\x1a\n":
                bad += 1
                if bad <= 5:
                    print(f"  ! PNG 签名异常: {e.name} -> {sig!r}")
    print(f"  PNG 签名异常: {bad} 个")


def main():
    ap = argparse.ArgumentParser(description="PVZ main.pak 工具")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="列出索引")
    p.add_argument("pak")
    p.add_argument("-o", "--output", help="写出 index.csv")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("unpack", help="解包到目录")
    p.add_argument("pak")
    p.add_argument("-d", "--outdir", required=True)
    p.add_argument("--list-after", action="store_true")
    p.set_defaults(func=cmd_unpack)

    p = sub.add_parser("pack", help="从目录重打包")
    p.add_argument("srcdir")
    p.add_argument("-o", "--output", required=True)
    p.set_defaults(func=cmd_pack)

    p = sub.add_parser("verify", help="校验 pak 完整性")
    p.add_argument("pak")
    p.set_defaults(func=cmd_verify)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
