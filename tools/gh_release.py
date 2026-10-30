# -*- coding: utf-8 -*-
"""一键发 GitHub Release：建 tag 对应的 release + 上传两个 zip。

用法：
    python tools/gh_release.py --version 1.6.5
    python tools/gh_release.py --version 1.6.5 --notes docs/release_1.6.5.md
    python tools/gh_release.py --version 1.6.5 --dry-run

前置：
    1. tools/make_release.py --version X.Y.Z  已经把 zip 打到
       build/release_hextech/PVZ海克斯版_vX.Y.Z.zip
    2. git push 过，tag vX.Y.Z 已存在（没有的话本脚本会建，但建议自己
       `git tag -a` 再 push，这样 tag 带注释）
    3. git 凭据里存着有 repo 权限的 GitHub token（推过码就有）

关于文件名：
    GitHub 的资产上传 API 会把 `name` 参数里的非 ASCII 字符整段剥掉
    （`PVZ海克斯版_v1.6.5.zip` → `PVZ._v1.6.5.zip`），所以本地中文名
    在上传时会映射成 ASCII 资产名。README 里写的是 ASCII 名。
"""

from __future__ import print_function

import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BUILD = os.path.join(ROOT, "build", "release_hextech")

OWNER = "zzzxhdyyzn12424"
REPO = "pvz-hextech"
API = "https://api.github.com"

MOD_NAME = "PVZ海克斯版"


def asset_map(version):
    """(本地文件名, 上传后的资产名)"""
    return [
        ("%s_v%s.zip" % (MOD_NAME, version),
         "PVZ_Hextech_v%s.zip" % version),
        ("%s_v%s_源码版.zip" % (MOD_NAME, version),
         "PVZ_Hextech_v%s_src.zip" % version),
    ]


# ---------------------------------------------------------------- 凭据
def get_token():
    """从 git 凭据管理器取 token。取不到返回 None。"""
    try:
        p = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n\n",
            capture_output=True, text=True, timeout=25,
        )
    except Exception:
        return None
    for line in p.stdout.splitlines():
        if line.startswith("password="):
            return line[len("password="):]
    return None


# ---------------------------------------------------------------- HTTP
def call(token, method, url, data=None, raw=None, ctype="application/json",
         timeout=600):
    body = raw if raw is not None else (
        json.dumps(data).encode("utf-8") if data is not None else None)
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "pvz-hextech-release")
    if body is not None:
        req.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            txt = r.read().decode("utf-8", "replace")
            return r.status, (json.loads(txt) if txt.strip() else {})
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode("utf-8", "replace")[:500]}
    except Exception as e:
        return 0, {"error": str(e)}


# ---------------------------------------------------------------- 正文
DEFAULT_BODY = """本包**不含游戏本体**，需要自备《植物大战僵尸》**1.0.0.1051 中文版**。

用法：解压 → 先打开游戏进入一关 → 双击 `PVZ海克斯版.exe`。
热键 `F7` 血量 / `F8` 面板 / `F9` 暂停结算 / `F10` 重抽词条。

完整词条表见 `docs/hextech_values.md`。
"""


def read_body(version, notes_path):
    if notes_path and os.path.isfile(notes_path):
        with open(notes_path, encoding="utf-8") as f:
            return f.read()
    return DEFAULT_BODY


# ---------------------------------------------------------------- 主流程
def main(argv):
    version = None
    notes = None
    dry = "--dry-run" in argv

    if "--version" in argv:
        i = argv.index("--version")
        if i + 1 < len(argv):
            version = argv[i + 1].lstrip("vV")
    if "--notes" in argv:
        i = argv.index("--notes")
        if i + 1 < len(argv):
            notes = argv[i + 1]
    if not version:
        print(__doc__)
        return 2

    tag = "v" + version
    pairs = asset_map(version)
    missing = [loc for loc, _ in pairs
               if not os.path.isfile(os.path.join(BUILD, loc))]
    if missing:
        print("[x] 找不到构建产物，先跑 make_release.py：")
        for m in missing:
            print("    build/release_hextech/%s" % m)
        return 1

    print("=" * 60)
    print(" 发 Release %s" % tag)
    print("=" * 60)
    for loc, rem in pairs:
        p = os.path.join(BUILD, loc)
        print("  %-34s -> %-30s %6.1f MB"
              % (loc, rem, os.path.getsize(p) / 1048576.0))
    print()

    if dry:
        print("[dry-run] 不实际请求")
        return 0

    token = get_token()
    if not token:
        print("[x] 取不到 GitHub 凭据。先 git push 一次让凭据管理器记住。")
        return 1
    st, me = call(token, "GET", API + "/user")
    if st != 200:
        print("[x] 凭据无效 HTTP %d %s" % (st, me.get("error", "")))
        return 1
    print("[*] 身份 %s" % me.get("login"))

    # 已有就复用，没有就建
    st, rel = call(token, "GET", "%s/repos/%s/%s/releases/tags/%s"
                   % (API, OWNER, REPO, tag))
    if st == 200:
        print("[*] Release 已存在 #%s，复用" % rel["id"])
    else:
        st, rel = call(token, "POST", "%s/repos/%s/%s/releases" % (API, OWNER, REPO),
                       data={
                           "tag_name": tag,
                           "name": "%s %s" % (MOD_NAME, tag),
                           "body": read_body(version, notes),
                           "draft": False,
                           "prerelease": False,
                       })
        if st not in (200, 201):
            print("[x] 建 Release 失败 HTTP %d\n%s" % (st, rel.get("error", "")))
            return 1
        print("[+] Release 已创建 #%s" % rel["id"])

    up = rel["upload_url"].split("{")[0]
    existing = {a["name"]: a for a in rel.get("assets", [])}

    for loc, rem in pairs:
        if rem in existing:
            st, _ = call(token, "DELETE",
                         "%s/repos/%s/%s/releases/assets/%d"
                         % (API, OWNER, REPO, existing[rem]["id"]))
            print("  覆盖旧资产 %-32s HTTP %d" % (rem, st))
        with open(os.path.join(BUILD, loc), "rb") as f:
            blob = f.read()
        st, d = call(token, "POST",
                     up + "?name=" + urllib.parse.quote(rem), raw=blob,
                     ctype="application/zip")
        if st in (200, 201):
            print("[+] 上传 %-32s %.1f MB" % (rem, len(blob) / 1048576.0))
        else:
            print("[x] 上传 %s 失败 HTTP %d\n%s" % (rem, st, d.get("error", "")))

    st, rel = call(token, "GET", "%s/repos/%s/%s/releases/tags/%s"
                   % (API, OWNER, REPO, tag))
    print()
    for a in rel.get("assets", []):
        print("  资产  %-32s %7.1f MB" % (a["name"], a["size"] / 1048576.0))
    print("  页面  %s" % rel.get("html_url", ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
