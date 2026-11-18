#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
补库工作台数据刷新 —— 通过「取件单 + 下载」两段式刷新本地缓存。

为什么这么做：
资料库导出（library_export）是 MCP 工具，只有在 WorkBuddy 会话里才能调用，
没有稳定的裸 HTTP 入口（试过 /api/space/node/export 与 /api/space/nodes/{id}，
都是 404 Route Not Found）。

所以拆成两段：
  段1（本脚本）：写 .cache/request.json 取件单，并检查 .cache/url.txt
  段2（会话内）：WorkBuddy 调 library_export 拿到 download_url，写进 .cache/url.txt
  段3（本脚本下一轮）：读 url.txt 下载覆盖 .cache/replenish.html

这样定时任务里也能自动刷新：会话里的自动化 prompt 会做段2。
刷新失败时保留旧缓存继续出报，绝不阻塞晨报。

用法：
    python3 refresh_replenish.py          # 写取件单 + 下载（若 url.txt 存在）
    python3 refresh_replenish.py --status # 只看缓存新鲜度
"""
import datetime
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, ".cache")
TARGET = os.path.join(CACHE, "replenish.html")
REQUEST = os.path.join(CACHE, "request.json")
URLFILE = os.path.join(CACHE, "url.txt")
NODE_ID = "U3dPL1OYi84owkD4vSIdAP"
NODE_TITLE = "补库工作台-亿成旗舰店.html"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def log(m):
    print("[replenish] %s" % m, flush=True)


def write_request():
    """写取件单 —— 告诉会话侧该导哪个节点。"""
    os.makedirs(CACHE, exist_ok=True)
    with open(REQUEST, "w", encoding="utf-8") as f:
        json.dump({
            "node_id": NODE_ID,
            "node_title": NODE_TITLE,
            "requested_at": datetime.datetime.now().isoformat(),
            "url_file": URLFILE,
            "target": TARGET,
        }, f, ensure_ascii=False, indent=1)
    log("取件单已写：%s" % REQUEST)


def download():
    """若 url.txt 存在则下载覆盖缓存。"""
    if not os.path.exists(URLFILE):
        return False, "url.txt 未就绪（需会话内调 library_export 写入）"
    with open(URLFILE, encoding="utf-8") as f:
        url = f.read().strip()
    if not url:
        return False, "url.txt 为空"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
    except Exception as e:
        return False, "下载失败：%s" % e
    if len(data) < 5000:
        return False, "内容过短（%d 字节），疑似异常页" % len(data)
    with open(TARGET, "wb") as f:
        f.write(data)
    os.utime(TARGET, None)
    # 用掉即清，避免下次拿过期签名链接
    try:
        os.remove(URLFILE)
    except OSError:
        pass
    return True, "已更新（%d 字节）" % len(data)


def status():
    if not os.path.exists(TARGET):
        return False, "无缓存"
    age_h = (datetime.datetime.now().timestamp() - os.path.getmtime(TARGET)) / 3600
    mtime = datetime.datetime.fromtimestamp(os.path.getmtime(TARGET))
    return (age_h <= 48), "缓存 %.1f 小时前更新（%s）" % (age_h, mtime.strftime("%m-%d %H:%M"))


if __name__ == "__main__":
    os.makedirs(CACHE, exist_ok=True)
    if "--status" in sys.argv:
        fresh, desc = status()
        print(("OK  " if fresh else "STALE ") + desc)
        sys.exit(0 if fresh else 1)
    write_request()
    ok, msg = download()
    log(msg)
    fresh, desc = status()
    log("当前缓存：%s" % desc)
    # 刷新失败不算错误：沿用旧缓存，晨报照常出报
    sys.exit(0)
