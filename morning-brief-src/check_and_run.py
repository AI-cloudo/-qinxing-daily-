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


def _dkey(s):
    """'11/21' → (11, 21)，用于比较大小。

    为什么不能直接比字符串（2026-11-21 修）：
      "11/21" > "11/9" 是 False（按字符比，'2' < '9'），
      会把「源站已到 11/21、简报还在 11/9」误判成「没有更新」→
      该重出的版本不重出，线上价格整天停在旧日期。
      跨月更糟："12/1" < "11/30"。
    """
    m = re.match(r"^\s*(\d{1,2})/(\d{1,2})\s*$", str(s or ""))
    return (int(m.group(1)), int(m.group(2))) if m else None


def _sec_asof(sec, kw="数据日期"):
    """板块角标里的数据日期，如「数据日期 11/20 · 鸡病专业网」。"""
    m = re.search(r"%s\s*(\d{1,2}/\d{1,2})" % re.escape(kw), str(sec or ""))
    return m.group(1) if m else ""


def _used_state():
    """上一版简报实际用的各品类日期 + 公鸡各产区日期。

    分两部分：
      used   —— 各品类的数据日期（老母鸡/三黄鸡/公鸡取速览卡末点，
                鸡蛋取板块角标，白羽从白羽板块角标补）
      regions—— 公鸡各产区的数据日期（从公鸡板块的表格行里逐行取）
    公鸡必须**按产区**比：各产区发稿有先后（菏泽常慢一天），
    只比一个总日期会把「只有某个产区更新了」误判成「没更新」。
    """
    path = os.path.join(HERE, "out", "today.json")
    if not os.path.exists(path):
        return {}, {}
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
        cards = d.get("trend_cards") or []
        secs = d.get("sections") or []
        used = dict(mod._price_ctx(d, cards, secs).get("asof") or {})
        # build.py 的 asof 只映射老母鸡/三黄鸡/公鸡三张卡 + 鸡蛋板块，
        # 白羽没进去 —— 这里从白羽板块角标补上，否则白羽更新永远触发不了重出。
        for s in secs:
            if "白羽" in str(s.get("title") or ""):
                a = _sec_asof(s)
                if a:
                    used["白羽肉鸡"] = a
                break
        # 公鸡各产区的日期（表格每行末尾带「数据M/D」）
        regions = {}
        for s in secs:
            if "公鸡行情" not in str(s.get("title") or ""):
                continue
            for t in (s.get("tables") or []):
                for r in (t.get("rows") or []):
                    if len(r) < 5:
                        continue
                    m = re.search(r"数据\s*(\d{1,2}/\d{1,2})", str(r[4]))
                    if m:
                        regions[str(r[0])] = m.group(1)
            break
        return used, regions
    except Exception as e:
        print("[refresh] 读取上一版数据日期失败：%s" % e)
        return {}, {}


def _used_dates():
    """上一版简报实际用的各品类数据日期（取自 out/today.json）。"""
    used, _regions = _used_state()
    return used


def _probe_sig(probe):
    """探针结果的指纹 —— 用来防止「同一份源站状态被反复重出」。

    为什么需要（2026-11-21 修）：
      探针的日期口径与简报的取数口径不可能百分百同源
      （例如 817 的数据日是正文基准日+1，与标题日期差一天的情况偶有发生）。
      口径一旦对不齐，就会出现「重出之后仍然判定有更新」的循环 ——
      每 8:25/8:55 触发都重出一版、白推一次 GitHub。
      指纹相同 = 源站状态没变 = 上次已经针对这份状态重出过了 → 不再重出。
    """
    return json.dumps(probe, ensure_ascii=False, sort_keys=True)


def _sig_path():
    return os.path.join(HERE, "out", ".refresh_sig.json")


def _read_sig():
    try:
        with open(_sig_path(), encoding="utf-8") as f:
            return json.load(f).get("sig")
    except Exception:
        return None


def _write_sig(sig):
    try:
        with open(_sig_path(), "w", encoding="utf-8") as f:
            json.dump({"sig": sig, "at": datetime.datetime.now().isoformat()}, f)
    except Exception as e:
        print("[refresh] 写刷新指纹失败：%s" % e)


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

    探针覆盖范围（2026-11-21 修）：原来只探博亚和讯（老母鸡/鸡蛋），
    漏了鸡病专业网。鸡病专业网的公鸡是**按产区逐篇发稿**的，
    菏泽/吉林/苏州那几篇常常在本机抓完之后才上线 ——
    11/21 实测 08:44 抓到公鸡 11/20，08:50 源站就发了 11/21 的菏泽稿，
    探针却报「源站无更新」→ 不重出 → 线上公鸡价整整差一天。
    现在各品类的探针口径与简报取数口径一一对应（见 scrape.source_latest_dates）。
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

    used, used_regions = _used_state()
    if not used:
        print("[refresh] 读不到上一版用的数据日期，跳过刷新判断")
        return False

    # 条件 2：逐品类比（源站最新一期 vs 简报已用）
    try:
        probe = scrape.source_latest_dates() or {}
    except Exception as e:
        print("[refresh] 源站探针失败，跳过刷新判断：%s" % e)
        return False

    sig = _probe_sig(probe)
    if _read_sig() == sig:
        print("[refresh] 源站状态与上次重出时一致（指纹未变）→ 不重复重出")
        return False

    stale = []
    for cat, ds in probe.items():
        if cat.startswith("_"):        # _regions 是明细，不直接参与比较
            continue
        u = used.get(cat)
        if not u or not ds:
            continue
        ku, ks = _dkey(u), _dkey(ds)
        if ku and ks and ks > ku:
            stale.append("%s（简报 %s → 源站 %s）" % (cat, u, ds))
    # 公鸡：按产区逐个比（各产区发稿有先后，只比总日期会漏掉单产区的更新）
    src_regions = probe.get("_regions") or {}
    for label, ds in sorted(src_regions.items()):
        u = used_regions.get(label)
        if not ds:
            continue
        if not u:
            stale.append("公鸡·%s（简报无此产区 → 源站 %s）" % (label, ds))
            continue
        ku, ks = _dkey(u), _dkey(ds)
        if ku and ks and ks > ku:
            stale.append("公鸡·%s（简报 %s → 源站 %s）" % (label, u, ds))
    if stale:
        _write_sig(sig)
        print("[refresh] 源站有更新：%s" % "；".join(stale))
        return True
    _write_sig(sig)
    print("[refresh] 源站无更新（简报已用 %s / 源站 %s）"
          % (", ".join("%s %s" % kv for kv in sorted(used.items())) or "无",
             ", ".join("%s %s" % kv for kv in sorted(probe.items()) if not kv[0].startswith("_")) or "无"))
    return False


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
