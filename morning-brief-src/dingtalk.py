#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把晨报文本推到钉钉群（自定义机器人，加签模式）。

用法：
    python3 dingtalk.py                 # 推精简版：标题 + 开头一句话 + 链接（默认）
    python3 dingtalk.py --full          # 推全文
    python3 dingtalk.py --test          # 发一条测试消息

配置在 dingtalk.json（不入 git 仓库）：
    {"webhook": "https://oapi.dingtalk.com/robot/send?access_token=...",
     "secret": "SEC..."}

加签算法（钉钉官方文档）：timestamp + "\\n" + secret 做 HMAC-SHA256，
base64 后 URL 编码，作为 &timestamp=xx&sign=xx 附在 webhook 后。
"""
import base64
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(HERE, "dingtalk.json")
TXT = os.path.join(HERE, "out", "morning_brief.txt")
SITE_URL = "https://ai-cloudo.github.io/-qinxing-daily-/morning-brief/index.html"


def load_cfg():
    """配置来源：dingtalk.json → 环境变量（GitHub Actions 里用 secrets）。"""
    webhook = os.environ.get("DINGTALK_WEBHOOK")
    secret = os.environ.get("DINGTALK_SECRET")
    if webhook and secret:
        return {"webhook": webhook, "secret": secret}
    if not os.path.exists(CFG):
        raise SystemExit("✗ 没有 dingtalk.json，也没有 DINGTALK_WEBHOOK/"
                         "DINGTALK_SECRET 环境变量")
    c = json.load(open(CFG, encoding="utf-8"))
    if not c.get("webhook") or not c.get("secret"):
        raise SystemExit("✗ dingtalk.json 里 webhook 或 secret 缺失")
    return c


def signed_url(webhook, secret):
    ts = str(round(time.time() * 1000))
    sign = base64.b64encode(
        hmac.new(secret.encode(), ("%s\n%s" % (ts, secret)).encode(),
                 hashlib.sha256).digest()).decode()
    return "%s&timestamp=%s&sign=%s" % (
        webhook, ts, urllib.parse.quote_plus(sign))


def send(cfg, markdown):
    """markdown: (title, md_body)。返回 (ok, msg)。

    用系统 curl 而不是 urllib：launchd 极简环境里 Python 找不到根证书，
    SSL 验证会失败（2026-11-18 实测）；curl 自带证书，任何环境都能跑。
    """
    title, body = markdown
    payload = json.dumps({
        "msgtype": "markdown",
        "markdown": {"title": title, "text": body},
    })
    url = signed_url(cfg["webhook"], cfg["secret"])
    try:
        p = subprocess.run(
            ["curl", "-sS", "-m", "20", "--fail", "-X", "POST",
             "-H", "Content-Type: application/json",
             "-d", payload, url],
            capture_output=True, text=True, timeout=30)
        out = p.stdout.strip()
    except Exception as e:
        return False, "✗ curl 执行失败：%s" % e
    if not out:
        return False, "✗ 钉钉无响应：%s" % p.stderr.strip()
    try:
        resp = json.loads(out)
    except ValueError:
        return False, "✗ 响应不是 JSON：%s" % out[:200]
    if resp.get("errcode") == 0:
        return True, "✓ 推送成功"
    return False, "✗ 钉钉返回：%s" % json.dumps(resp, ensure_ascii=False)


def md_escape(t):
    """钉钉 markdown 里 # > - 等是语法字符，行首 escape 掉。"""
    out = []
    for ln in t.splitlines():
        s = ln.rstrip()
        if s[:1] in ("#", "-", ">", "+"):
            s = " " + s
        out.append(s.replace("_", "\\_").replace("`", " "))
    return "\n\n".join(x for x in out if x.strip())


def build_full():
    t = open(TXT, encoding="utf-8").read().strip()
    lines = t.splitlines()
    title = lines[0].strip("【】") if lines else "上游行情"
    return title, md_escape(t)


def build_short():
    t = open(TXT, encoding="utf-8").read().strip()
    lines = [x for x in t.splitlines() if x.strip()]
    # 标题 + 开头一句话（第一段正文）
    title = lines[0].strip("【】") if lines else "上游行情"
    first_para = next((x for x in lines[1:] if not x.startswith(("─", "──"))), "")
    body = md_escape(title + "\n\n" + first_para + "\n\n点开看全部图表：\n\n" + SITE_URL)
    return title, body


def main():
    args = sys.argv[1:]
    if not os.path.exists(TXT):
        raise SystemExit("✗ 找不到 %s —— 先跑生成" % TXT)
    if "--full" in args:
        md = build_full()
    else:
        md = build_short()
    cfg = load_cfg()
    if "--test" in args:
        md = ("测试", "✅ 禽类行情日报机器人已接好。\n\n以后每天早上 6 点半左右，"
              "当日上游行情会自动发到这个群。")
    ok, msg = send(cfg, md)
    print(msg)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
