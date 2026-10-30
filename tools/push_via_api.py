# -*- coding: utf-8 -*-
"""github.com 连不上时，用 REST API 把本地 HEAD 提交同步到远端。

只做一件事：把 HEAD 与远端 main 的差异（HEAD 是远端 main 的单个子提交）
通过 Git Data API 重放上去，再更新 main 引用 + 打 annotated tag。

用法：python tools/push_via_api.py v1.6.7 "tag message"
"""
import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

OWNER, REPO = "zzzxhdyyzn12424", "pvz-hextech"
API = "https://api.github.com"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_token():
    p = subprocess.run(["git", "credential", "fill"],
                       input="protocol=https\nhost=github.com\n\n",
                       capture_output=True, text=True, timeout=25, cwd=ROOT)
    for line in p.stdout.splitlines():
        if line.startswith("password="):
            return line[len("password="):]
    return None


TOKEN = get_token()


def call(method, url, data=None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", "Bearer " + TOKEN)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "pvz-hextech-push")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            txt = r.read().decode("utf-8", "replace")
            return r.status, (json.loads(txt) if txt.strip() else {})
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode("utf-8", "replace")[:600]}


def git(*args):
    return subprocess.run(["git"] + list(args), cwd=ROOT,
                          capture_output=True, check=True).stdout


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    tag, tag_msg = sys.argv[1], sys.argv[2]

    local_head = git("rev-parse", "HEAD").decode().strip()
    parent = git("rev-parse", "HEAD~1").decode().strip()
    msg = git("log", "-1", "--format=%B", "HEAD").decode()

    st, ref = call("GET", "%s/repos/%s/%s/git/ref/heads/main" % (API, OWNER, REPO))
    if st != 200:
        print("[x] 读 ref 失败", st, ref.get("error", ""))
        return 1
    remote_head = ref["object"]["sha"]
    print("[*] 本地 HEAD  %s" % local_head[:9])
    print("[*] 远端 main  %s" % remote_head[:9])

    if remote_head == local_head:
        print("[=] 已经一致，跳过")
    else:
        if remote_head != parent:
            print("[x] 远端 main 不等于本地 HEAD 的父提交，拒绝重放")
            print("    remote=%s parent=%s" % (remote_head[:9], parent[:9]))
            return 1

        st, base = call("GET", "%s/repos/%s/%s/git/commits/%s"
                        % (API, OWNER, REPO, remote_head))
        base_tree = base["tree"]["sha"]

        names = git("diff", "--name-only", remote_head, local_head).decode().split()
        print("[*] 差异文件 %d 个" % len(names))

        entries = []
        for path in names:
            blob = git("cat-file", "blob", "%s:%s" % (local_head, path))
            st, b = call("POST", "%s/repos/%s/%s/git/blobs" % (API, OWNER, REPO),
                         {"content": base64.b64encode(blob).decode(),
                          "encoding": "base64"})
            if st not in (200, 201):
                print("[x] blob %s 失败 %d %s" % (path, st, b.get("error", "")))
                return 1
            entries.append({"path": path, "mode": "100644",
                            "type": "blob", "sha": b["sha"]})
            print("    blob %-28s %5d B" % (path, len(blob)))

        st, tree = call("POST", "%s/repos/%s/%s/git/trees" % (API, OWNER, REPO),
                        {"base_tree": base_tree, "tree": entries})
        if st not in (200, 201):
            print("[x] tree 失败 %d %s" % (st, tree.get("error", "")))
            return 1

        st, cm = call("POST", "%s/repos/%s/%s/git/commits" % (API, OWNER, REPO),
                      {"message": msg, "tree": tree["sha"],
                       "parents": [remote_head]})
        if st not in (200, 201):
            print("[x] commit 失败 %d %s" % (st, cm.get("error", "")))
            return 1
        new_sha = cm["sha"]
        print("[+] 远端新提交 %s" % new_sha[:9])

        st, up = call("PATCH", "%s/repos/%s/%s/git/refs/heads/main"
                      % (API, OWNER, REPO), {"sha": new_sha, "force": False})
        if st != 200:
            print("[x] 更新 main 失败 %d %s" % (st, up.get("error", "")))
            return 1
        print("[+] main 已指向 %s" % new_sha[:9])
        local_head = new_sha

    # ---- annotated tag
    st, ex = call("GET", "%s/repos/%s/%s/git/ref/tags/%s" % (API, OWNER, REPO, tag))
    if st == 200:
        print("[=] tag %s 已存在" % tag)
    else:
        st, t = call("POST", "%s/repos/%s/%s/git/tags" % (API, OWNER, REPO),
                     {"tag": tag, "message": tag_msg,
                      "object": local_head, "type": "commit"})
        if st not in (200, 201):
            print("[x] 建 tag 对象失败 %d %s" % (st, t.get("error", "")))
            return 1
        st, r = call("POST", "%s/repos/%s/%s/git/refs" % (API, OWNER, REPO),
                     {"ref": "refs/tags/" + tag, "sha": t["sha"]})
        if st not in (200, 201):
            print("[x] 建 tag ref 失败 %d %s" % (st, r.get("error", "")))
            return 1
        print("[+] tag %s -> %s" % (tag, local_head[:9]))

    st, ref = call("GET", "%s/repos/%s/%s/git/ref/heads/main" % (API, OWNER, REPO))
    print()
    print("  main = %s" % ref["object"]["sha"][:9])
    print("  tag  = %s" % tag)
    return 0


if __name__ == "__main__":
    sys.exit(main())
