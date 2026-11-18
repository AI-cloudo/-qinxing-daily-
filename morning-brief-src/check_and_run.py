#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
晨报补跑守卫 —— 解决「电脑夜间休眠，7:20 定时任务没触发」的问题。

用法：
    python3 check_and_run.py            # 缺当天晨报就补跑，已有则跳过
    python3 check_and_run.py --force    # 强制重跑

判定依据：out/morning_brief.html 里写的"数据日期"是否等于今天。
注意：数据日期可能比今天晚（源站提前一天发稿，如今天 11/6 早上跑可能拿到 11/7 的价），
所以只要产物存在且是今天生成的，就算已完成。
"""
import datetime
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
HTML = os.path.join(OUT, "morning_brief.html")


def already_done_today():
    if not os.path.exists(HTML):
        return False
    try:
        mtime = datetime.datetime.fromtimestamp(os.path.getmtime(HTML))
    except OSError:
        return False
    today = datetime.date.today()
    # 当天 6:00 之前生成的算前一天那批，不算今天已完成
    # （2026-11-10 起 LaunchAgent 提前到 6:30/6:55 跑，所以阈值从 7 点降到 6 点）
    if mtime.date() == today and mtime.hour >= 6:
        return True
    # 昨天生成、但简报内标注的数据日期已经是今天/明天 → 视为已覆盖
    with open(HTML, encoding="utf-8") as f:
        head = f.read(4000)
    m = re.search(r"数据日期\s*([\d/]+)", head)
    if m and mtime.date() >= today - datetime.timedelta(days=1):
        mm = re.findall(r"\d+", m.group(1))
        if len(mm) == 2:
            try:
                d = datetime.date(int(mm[0]), int(mm[1]), today.year)
            except ValueError:
                d = None
            if d and d >= today - datetime.timedelta(days=1):
                return True
    return False


def source_has_newer():
    """源站有没有出比简报里更新的数据。

    为什么需要这个：源站发稿有先后 —— 博亚和讯淘鸡 08:20 出稿、鸡蛋 09:20 出稿，
    而晨报 6:30 就生成了。所以 6:30 那版必然带着上一期的价。
    如果之后源站补发了当天的稿，应该重出一版，否则销售手机上永远看到昨天收盘价。

    两条触发条件（任一成立即重出）：

    1. **早于 8 点出的那版必须重出**。源站 8:20 才开始发当天的稿，
       所以 8 点前出的简报天生带上一期价；到 8 点后一律重出一次。
       这条是兜底 —— 鸡病专业网的公鸡/817 没有便宜探针，靠它兜住。
    2. **逐品类比对**：上一版用的日期（取自 out/today.json —— 那正是上次出报时的数据）
       对比源站当前最新一期。任一品类更新了 ⇒ 该重出。
    """
    try:
        sys.path.insert(0, os.path.join(HERE, "work"))
        import scrape
    except Exception as e:
        print("[refresh] 无法加载抓取模块，跳过刷新判断：%s" % e)
        return False

    # 条件 1：8 点前出的简报，出稿时源站当天稿还没发 → 到点必须重出
    # （现在 ≥ 08:30 才触发，避免 6:30/6:55 两次跑重复劳动）
    try:
        now = datetime.datetime.now()
        mtime = datetime.datetime.fromtimestamp(os.path.getmtime(HTML))
        if (now.hour, now.minute) >= (8, 30) and mtime.date() == now.date() \
                and mtime.hour < 8:
            print("[refresh] 上一版是 %02d:%02d 出的（早于源站发稿时间）→ 重出"
                  % (mtime.hour, mtime.minute))
            return True
    except OSError:
        pass

    used = _used_dates()
    if not used:
        print("[refresh] 读不到上一版用的数据日期，跳过刷新判断")
        return False

    # 条件 2：逐品类比（源站最新一期 vs 简报已用）
    src = {}
    for key, fn in (("老母鸡", scrape.boyar_latest), ("鸡蛋", scrape.boyar_egg_latest)):
        try:
            url, ds = fn()
            if ds:
                src[key] = ds
        except Exception:
            continue

    stale = []
    for cat, ds in src.items():
        u = used.get(cat)
        if u and ds > u:
            stale.append("%s（简报 %s → 源站 %s）" % (cat, u, ds))
    if stale:
        print("[refresh] 源站有更新：%s" % "；".join(stale))
        return True
    print("[refresh] 源站无更新（简报已用 %s / 源站 %s）"
          % (", ".join("%s %s" % kv for kv in sorted(used.items())) or "无",
             ", ".join("%s %s" % kv for kv in sorted(src.items())) or "无"))
    return False


def _used_dates():
    """上一版简报实际用的各品类数据日期（取自 out/today.json）。"""
    path = os.path.join(HERE, "out", "today.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        # 必须按绝对路径加载本工程的 build.py —— work/ 目录下也有一份同名模块
        # （云端抓取工程），sys.path 里 work 优先，会导入错的那份。
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "morning_build", os.path.join(HERE, "build.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        ctx = mod._price_ctx(d, d.get("trend_cards") or [], d.get("sections") or [])
        return ctx.get("asof") or {}
    except Exception as e:
        print("[refresh] 读取上一版数据日期失败：%s" % e)
        return {}


def main():
    force = "--force" in sys.argv
    if not force and already_done_today():
        # 今天的简报已在。但源站可能后来才补发当天的稿（博亚和讯 08:20、
        # 鸡蛋 09:20 出稿，而晨报 6:30 就生成）—— 此时应重出一版，
        # 否则手机上永远停在昨天收盘价。
        if source_has_newer():
            print("RUN: 源站有新数据，重新生成 …")
        else:
            print("SKIP: 今日晨报已存在且已是最新，不重复生成")
            return 0
    else:
        print("RUN: 补跑晨报 …")
    r = subprocess.run(["bash", os.path.join(HERE, "run_morning_brief.sh")],
                       capture_output=True, text=True)
    out = (r.stdout or "") + (r.stderr or "")
    print(out.strip()[-1500:])
    if r.returncode != 0 or not os.path.exists(HTML):
        print("FAIL: 补跑未成功")
        return 1
    print("OK: 晨报已生成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
