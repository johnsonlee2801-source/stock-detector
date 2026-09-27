#!/usr/bin/env python3
"""
部署"加密货币波动率压缩观察站"到 GitHub 并触发一次扫描。

用法：
  python3 ~/Desktop/美港股检测器/skills/deploy_vol.py              # 先本地跑一遍扫描再上传
  python3 ~/Desktop/美港股检测器/skills/deploy_vol.py --no-scan    # 只上传代码，让 GitHub Actions 去生成报告

做的事：
  1. （可选）本地运行 vol_scan.py 生成 report.html / vol_data.json / vol_history.json
  2. 通过 GitHub Contents API 上传所有相关文件（内容没变的跳过）
  3. 触发 scan.yml 的 workflow_dispatch

Token 来源：macOS 钥匙串（stock-detector-token），其次项目根目录的 .github_token 文件。
"""
import base64
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 上一级 = 美港股检测器/
REPO = "johnsonlee2801-source/stock-detector"
BRANCH = "main"
API = f"https://api.github.com/repos/{REPO}"
SITE = "https://watching.flowhunt.net/report.html"

# 上传顺序：先代码和配置，最后报告
FILES = [
    "vol_scan.py",
    "watchlist.txt",
    "index.html",
    "skills/SKILLS.md",
    "skills/deploy_vol.py",
    ".github/workflows/scan.yml",
    "report.html",
    "vol_data.json",
    "vol_history.json",
]


def get_token():
    r = subprocess.run(["security", "find-generic-password", "-a", "github",
                        "-s", "stock-detector-token", "-w"], capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip()
    f = os.path.join(DIR, ".github_token")
    if os.path.exists(f):
        return open(f).read().strip()
    raise RuntimeError("找不到 GitHub Token：钥匙串里没有 stock-detector-token，项目根目录也没有 .github_token")


TOKEN = None
HDR = {}


def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    url = path if path.startswith("https://") else f"{API}/{path}"
    r = urllib.request.Request(url, data=data, headers=HDR, method=method)
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            raw = resp.read()
            return (json.loads(raw) if raw.strip() else {}), resp.status
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return json.loads(raw), e.code
        except ValueError:
            return {"message": raw.decode(errors="replace")}, e.code


def blob_sha(data: bytes) -> str:
    """GitHub 的文件 sha 就是 git blob sha，本地算一遍可以判断内容有没有变。"""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def local_scan():
    script = os.path.join(DIR, "vol_scan.py")
    cmd = [sys.executable, script, "--top-volume", "100", "--file", "watchlist.txt",
           "--html", "report.html", "--json", "vol_data.json", "--history", "vol_history.json", "--quiet"]
    print("🔎 本地运行扫描（约 1 到 2 分钟）...")
    try:
        r = subprocess.run(cmd, cwd=DIR, timeout=900)
        if r.returncode == 0:
            print("   ✅ 本地报告已生成")
            return True
        print(f"   ⚠️  扫描退出码 {r.returncode}，改由 GitHub Actions 生成报告")
    except subprocess.TimeoutExpired:
        print("   ⚠️  扫描超时，改由 GitHub Actions 生成报告")
    return False


def push_file(rel):
    path = os.path.join(DIR, rel)
    if not os.path.exists(path):
        print(f"   ⏭  {rel}：本地不存在，跳过")
        return "skip"
    with open(path, "rb") as f:
        data = f.read()
    resp, code = req("GET", f"contents/{rel}?ref={BRANCH}")
    remote_sha = resp.get("sha") if code == 200 else None
    if remote_sha and remote_sha == blob_sha(data):
        print(f"   ＝ {rel}：内容未变")
        return "same"
    body = {"message": f"🫧 deploy: {rel}", "content": base64.b64encode(data).decode(), "branch": BRANCH}
    if remote_sha:
        body["sha"] = remote_sha
    resp2, code2 = req("PUT", f"contents/{rel}", body)
    if code2 in (200, 201):
        print(f"   ✅ {rel}：{'更新' if remote_sha else '新建'}")
        return "ok"
    msg = resp2.get("message", "")
    print(f"   ❌ {rel}：{code2} {msg}")
    if rel.startswith(".github/workflows") and code2 in (403, 404, 422):
        print("      提示：改 workflow 文件需要 Token 带 workflow 权限。"
              "如果一直失败，到 GitHub 网页上手动把本地 .github/workflows/scan.yml 的内容贴进去。")
    return "fail"


def main():
    global TOKEN, HDR
    no_scan = "--no-scan" in sys.argv
    print("=" * 56)
    print("  部署：加密货币波动率压缩观察站 → watching.flowhunt.net")
    print("=" * 56)

    TOKEN = get_token()
    HDR = {"Authorization": f"token {TOKEN}", "Accept": "application/vnd.github.v3+json",
           "Content-Type": "application/json", "User-Agent": "vol-scan-deploy"}
    resp, code = req("GET", API)  # 注意不能带末尾斜杠，GitHub 会返回 404
    if code != 200:
        print(f"❌ 无法访问仓库 {REPO}：{code} {resp.get('message', '')}")
        u_resp, u_code = req("GET", "https://api.github.com/user")
        if u_code == 200:
            print(f"   Token 本身有效，属于账号 {u_resp.get('login')}，但没有这个仓库的权限。"
                  "要么账号不对，要么是细粒度 Token 没勾选这个仓库。")
        else:
            print(f"   Token 已失效（GitHub 返回 {u_code}）。")
        print("   处理：用 johnsonlee2801-source 账号到 https://github.com/settings/tokens 生成"
              " classic token，勾 repo 和 workflow，写进项目根目录 .github_token，"
              "再跑 存储Token到钥匙串.sh")
        sys.exit(1)
    print(f"✅ 仓库可访问（默认分支 {resp.get('default_branch')}）\n")

    if not no_scan:
        local_scan()
        print()

    print("⬆️  上传文件...")
    status = {}
    for rel in FILES:
        status[rel] = push_file(rel)
        time.sleep(0.5)
    fails = [k for k, v in status.items() if v == "fail"]
    print()

    print("⏳ 等待 GitHub 同步 (8s)...")
    time.sleep(8)
    print("🚀 触发扫描 workflow...")
    d_resp, d_code = req("POST", "actions/workflows/scan.yml/dispatches", {"ref": BRANCH})
    if d_code == 204:
        print("   ✅ 已触发，约 2 到 3 分钟后网站更新")
    else:
        print(f"   ⚠️  触发失败({d_code})：{d_resp.get('message', '')}")
        print(f"   到 https://github.com/{REPO}/actions 手动点 Run workflow")

    print("\n" + "=" * 56)
    if fails:
        print(f"  ⚠️  有 {len(fails)} 个文件上传失败：{', '.join(fails)}")
    print(f"  网站：{SITE}")
    print(f"  Actions：https://github.com/{REPO}/actions")
    print("  GitHub Pages 有约 1 分钟缓存，刷新看不到就等一会。")
    print("=" * 56)


if __name__ == "__main__":
    main()
