#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
static_re.py —— PVZ 静态逆向辅助（不需要游戏在跑）

用法：
    python static_re.py pe            # 打印节表
    python static_re.py refs  0x6A9EC0          # 找所有引用该地址的指令
    python static_re.py dis   0x520AC0 200      # 线性反汇编
    python static_re.py str   "PopCap"          # 在文件里搜字符串，给出 VA
    python static_re.py strref "PopCap"         # 搜字符串 + 找引用它的代码
    python static_re.py xref 0x520AC0           # 找 call/jmp 到该地址的地方

设计原则：
  - 纯静态：只读 EXE 文件，不碰进程，结果可重复
  - 地址一律用 VA（ImageBase 0x400000），和 OD/CE 里看到的一致
"""

import sys
import struct
import os

EXE_DEFAULT = r"F:\pvz\Plants_Vs_Zombies_V1.0.0.1051_CN_V2\PlantsVsZombies.exe"

try:
    import capstone
except ImportError:
    print("需要 capstone:  .venv\\Scripts\\python.exe -m pip install capstone")
    sys.exit(2)


class PE:
    def __init__(self, path):
        self.path = path
        with open(path, "rb") as f:
            self.data = f.read()
        self.base = 0x400000
        e_lfanew = struct.unpack_from("<I", self.data, 0x3C)[0]
        self.nsec = struct.unpack_from("<H", self.data, e_lfanew + 6)[0]
        opt = e_lfanew + 24
        self.base = struct.unpack_from("<I", self.data, opt + 28)[0]
        self.sizeofimage = struct.unpack_from("<I", self.data, opt + 56)[0]
        self.entry = self.base + struct.unpack_from("<I", self.data, opt + 16)[0]
        sh = opt + struct.unpack_from("<H", self.data, e_lfanew + 20)[0]
        self.sections = []
        for i in range(self.nsec):
            o = sh + i * 40
            name = self.data[o:o + 8].rstrip(b"\0").decode("latin1")
            vsize, va, rsize, rptr = struct.unpack_from("<IIII", self.data, o + 8)
            chars = struct.unpack_from("<I", self.data, o + 36)[0]
            self.sections.append(dict(name=name, va=self.base + va, vsize=vsize,
                                      rsize=rsize, rptr=rptr, chars=chars))

    # ---- 地址换算 ----
    def sec_of(self, va):
        for s in self.sections:
            if s["va"] <= va < s["va"] + max(s["vsize"], s["rsize"]):
                return s
        return None

    def va2off(self, va):
        s = self.sec_of(va)
        if not s:
            return None
        d = va - s["va"]
        if d >= s["rsize"]:
            return None          # 在 BSS 区，文件里没有
        return s["rptr"] + d

    def off2va(self, off):
        for s in self.sections:
            if s["rptr"] <= off < s["rptr"] + s["rsize"]:
                return s["va"] + (off - s["rptr"])
        return None

    def read(self, va, n):
        o = self.va2off(va)
        if o is None:
            return b""
        return self.data[o:o + n]

    def u32(self, va):
        b = self.read(va, 4)
        return struct.unpack("<I", b)[0] if len(b) == 4 else None

    @property
    def text(self):
        for s in self.sections:
            if s["name"] == ".text":
                return s
        return self.sections[0]

    def text_bytes(self):
        s = self.text
        return self.data[s["rptr"]:s["rptr"] + s["rsize"]], s["va"]


def make_md(pe):
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.detail = True
    md.skipdata = False
    return md


def dis_range(pe, va, nbytes, md=None, indent=""):
    md = md or make_md(pe)
    code = pe.read(va, nbytes)
    out = []
    for ins in md.disasm(code, va):
        out.append(ins)
    return out


def fmt(ins, va0, extra=""):
    mark = ">>>" if ins.address == va0 else "   "
    return "%s 0x%08X  %-24s %s%s" % (mark, ins.address,
                                       ins.bytes.hex(" "), ins.mnemonic + " " + ins.op_str,
                                       extra)


# ---------------------------------------------------------------- 子命令
def cmd_pe(pe):
    print("file      :", pe.path)
    print("size      :", len(pe.data))
    print("imagebase : 0x%08X" % pe.base)
    print("entry     : 0x%08X" % pe.entry)
    print("sizeofimg : 0x%08X" % pe.sizeofimage)
    print("%-10s %-12s %-12s %-12s %-12s" % ("name", "VA", "VSize", "RawPtr", "RawSize"))
    for s in pe.sections:
        print("%-10s 0x%08X   0x%08X   0x%08X   0x%08X" %
              (s["name"], s["va"], s["vsize"], s["rptr"], s["rsize"]))


def cmd_refs(pe, target):
    """扫 .text，找 imm32 == target 的指令（mov/push/cmp 都算）"""
    code, base = pe.text_bytes()
    pat = struct.pack("<I", target)
    md = make_md(pe)
    hits = []
    start = 0
    while True:
        i = code.find(pat, start)
        if i < 0:
            break
        hits.append(base + i)
        start = i + 1

    print("imm32 0x%08X 在 .text 里出现 %d 次" % (target, len(hits)))
    for h in hits:
        # 从命中点前 16 字节开始反汇编，定位真正包含该立即数的指令
        s = max(base, h - 16)
        ins_list = list(md.disasm(pe.read(s, (h - s) + 24), s))
        for ins in ins_list:
            if ins.address <= h < ins.address + ins.size:
                print("  ref @0x%08X   %-22s %s" %
                      (ins.address, ins.bytes.hex(" "), ins.mnemonic + " " + ins.op_str))
                break
    return hits


def cmd_dis(pe, va, n):
    md = make_md(pe)
    for ins in dis_range(pe, va, n, md):
        print(fmt(ins, va))


def cmd_str(pe, text):
    b = text.encode("latin1") if isinstance(text, str) else text
    off = 0
    hits = []
    while True:
        i = pe.data.find(b, off)
        if i < 0:
            break
        va = pe.off2va(i)
        hits.append((i, va))
        off = i + 1
    print('"%s" 出现 %d 次' % (text, len(hits)))
    for i, va in hits[:40]:
        if va is None:
            print("  file 0x%X (不在节映射内)" % i)
            continue
        # 打印所在字符串
        end = pe.data.find(b"\0", i)
        s = pe.data[i:end][:80].decode("latin1", "replace")
        print("  file 0x%06X  VA 0x%08X  %r" % (i, va, s))
    return hits


def cmd_strref(pe, text):
    hits = cmd_str(pe, text)
    seen = set()
    for _, va in hits:
        if va is None or va in seen:
            continue
        seen.add(va)
        print("\n--- 引用 0x%08X ---" % va)
        cmd_refs(pe, va)


def cmd_xref(pe, target):
    """
    找 E8/E9 rel32 指向 target 的位置。

    注意：x86 变长指令没有天然对齐，所以这里给两档结果：
      [对齐]   —— 从附近多个起点反汇编都能在 src 处落到指令边界，可信
      [未对齐] —— 只是字节模式匹配上了，可能是别的指令的立即数，需人工看
    只看 [对齐] 的行会漏；只看全部又会误报。两档都打出来。
    """
    code, base = pe.text_bytes()
    md = make_md(pe)
    raw_hits = []
    for i in range(len(code) - 5):
        if code[i] in (0xE8, 0xE9):
            rel = struct.unpack_from("<i", code, i + 1)[0]
            src = base + i
            if (src + 5 + rel) & 0xFFFFFFFF == target:
                raw_hits.append(src)

    aligned = []
    for src in raw_hits:
        ok = False
        for back in range(1, 17):
            s = src - back
            if s < base:
                continue
            for ins in md.disasm(pe.read(s, (src - s) + 5), s):
                if ins.address == src:
                    ok = True
                    break
                if ins.address > src:
                    break
            if ok:
                break
        if ok:
            aligned.append(src)

    for src in raw_hits:
        op = "call" if pe.read(src, 1) == b"\xe8" else "jmp"
        tag = "[对齐]  " if src in aligned else "[未对齐]"
        print("  %s 0x%08X  %s 0x%08X" % (tag, src, op, target))
    print("共 %d 处（其中 %d 处对齐可信）" % (len(raw_hits), len(aligned)))
    return raw_hits


def parse_hex(s):
    return int(s, 16) if s.lower().startswith("0x") else int(s, 16)


def main():
    argv = sys.argv[1:]
    exe = EXE_DEFAULT
    if "--exe" in argv:
        k = argv.index("--exe")
        exe = argv[k + 1]
        del argv[k:k + 2]
    if not argv:
        print(__doc__)
        return 1
    if not os.path.exists(exe):
        print("找不到 EXE:", exe)
        return 1
    pe = PE(exe)
    cmd = argv[0]

    if cmd == "pe":
        cmd_pe(pe)
    elif cmd == "refs":
        cmd_refs(pe, parse_hex(argv[1]))
    elif cmd == "dis":
        cmd_dis(pe, parse_hex(argv[1]), int(argv[2], 0) if len(argv) > 2 else 200)
    elif cmd == "str":
        cmd_str(pe, argv[1])
    elif cmd == "strref":
        cmd_strref(pe, argv[1])
    elif cmd == "xref":
        cmd_xref(pe, parse_hex(argv[1]))
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
