#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
晨报数据生产脚本 —— 在 8 点前拿到当天上游行情。

设计要点（实测验证，勿随意改动）：

1. 环比基准必须来自云端 state.json
   本地 cloud-report/state.json 停留在 2026-09-01，直接用它算环比会得出
   "蛋价 ↓0.62" 这种荒谬结果（真实为 ↑0.07）—— 基准陈旧导致价格全错。
   云端 state.json 每天由 GitHub Actions 更新，是唯一的可信基准。

2. 为什么要在本地重跑 scrape.py，而不是直接用云端 data.json
   云端 4 轮实测落地时间：07:32 / 08:24 / 09:06 / 09:51。
   首轮 07:35 落库时，鸡病专业网当天早班稿尚未发布，各板块仍是 D-1 数据
   （实测 07:35 那轮 6 个板块全部标注 11/5）。
   而源站会提前一天发稿（817 棚前 11/7 的稿当天白天就上线），
   本地 11:23 跑能拿到 11/6 全量当日数据 —— 比云端首轮新鲜。
   所以：拉云端 state.json 作基准 → 本地重跑 scrape.py → 得到当日数据。

3. 抓取失败时不阻塞出报
   scrape.py 失败时回退到云端 data.json，并在简报里如实标注数据日期，
   绝不编造数字。

用法：
    python3 fetch_data.py            # 正常跑
    python3 fetch_data.py --fallback # 跳过本地抓取，直接用云端 data.json
"""
import datetime
import json
import os
import shutil
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, "work")
OUT = os.path.join(HERE, "out")
CACHE = os.path.join(HERE, ".cache")

REPO = "AI-cloudo/-qinxing-daily-"
RAW_DATA = "https://raw.githubusercontent.com/%s/main/data.json" % REPO
RAW_STATE = "https://raw.githubusercontent.com/%s/main/state.json" % REPO
API = "https://api.github.com/repos/%s/contents/" % REPO

# 抓取源工程（含 scrape.py / build.py / src_policy.*）
# 依次找：环境变量指定 → 工程自带 cloud-report/（云端 GitHub Actions 用）
#        → WorkBuddy 本地 cloud-report（平时本地跑用）
SRC_CLOUD = next((
    p for p in (
        os.environ.get("MB_SCRAPE_SRC"),
        os.path.join(HERE, "cloud-report"),
        "/Users/yitouxiaomaolv/WorkBuddy/2026-08-26-10-07-56/cloud-report",
    ) if p and os.path.isdir(p)), "/Users/yitouxiaomaolv/WorkBuddy/2026-08-26-10-07-56/cloud-report")
# 环比基准（云端 state.json 的本地副本，跟随每周精修更新）
TOKEN_FILE = "/Users/yitouxiaomaolv/WorkBuddy/2026-08-26-10-07-56/workbench/.gh-token"

PC_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def log(msg):
    print("[morning] %s" % msg, flush=True)


def fetch(url, token=None, timeout=30):
    headers = {"User-Agent": PC_UA}
    if token:
        headers["Authorization"] = "Bearer " + token
        headers["Accept"] = "application/vnd.github+json"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def get_token():
    try:
        with open(TOKEN_FILE, encoding="utf-8") as f:
            t = f.read().strip()
        return t or None
    except Exception:
        return None


def pull_cloud_state():
    """拉云端 state.json —— 环比基准。优先走 GitHub API（raw CDN 有分钟级滞后）。"""
    token = get_token()
    if token:
        try:
            raw = fetch(API + "state.json", token=token).decode("utf-8")
            j = json.loads(raw)
            import base64
            data = base64.b64decode(j["content"]).decode("utf-8")
            st = json.loads(data)
            log("云端 state.json（API）date=%s prev_date=%s"
                % (st.get("date"), st.get("prev_date")))
            return st
        except Exception as e:
            log("API 拉 state.json 失败（%s），改走 raw" % e)
    try:
        st = json.loads(fetch(RAW_STATE).decode("utf-8"))
        log("云端 state.json（raw）date=%s prev_date=%s"
            % (st.get("date"), st.get("prev_date")))
        return st
    except Exception as e:
        log("拉 state.json 失败：%s（沿用本地旧基准，环比可能不准）" % e)
        return None


def pull_cloud_data():
    """拉云端 data.json —— 兜底数据源（也用于拿白羽等补充板块）。"""
    token = get_token()
    if token:
        try:
            import base64
            j = json.loads(fetch(API + "data.json", token=token).decode("utf-8"))
            d = json.loads(base64.b64decode(j["content"]).decode("utf-8"))
            log("云端 data.json（API）update_time=%s"
                % (d.get("meta", {}).get("update_time")))
            return d
        except Exception as e:
            log("API 拉 data.json 失败（%s），改走 raw" % e)
    try:
        d = json.loads(fetch(RAW_DATA).decode("utf-8"))
        log("云端 data.json（raw）update_time=%s"
            % (d.get("meta", {}).get("update_time")))
        return d
    except Exception as e:
        log("拉云端 data.json 失败：%s" % e)
        return None


def prepare_workdir(cloud_data=None):
    """把抓取工程复制到 work/，避免污染云端同步目录。

    必须用云端 data.json 播种：本地 cloud-report/data.json 是 11/2 的旧副本，
    会让 817 历史序列断档（缺中间几天），涨跌方向被算错
    （实测本地 down vs 线上 up）。
    """
    for sub in ("work", "out", ".cache"):
        os.makedirs(os.path.join(HERE, sub), exist_ok=True)
    if os.path.isdir(WORK):
        shutil.rmtree(WORK)
    shutil.copytree(SRC_CLOUD, WORK,
                    ignore=shutil.ignore_patterns("__pycache__", "*.bak*", ".DS_Store"))
    if cloud_data:
        with open(os.path.join(WORK, "data.json"), "w", encoding="utf-8") as f:
            json.dump(cloud_data, f, ensure_ascii=False, indent=1)
        log("已用云端 data.json 播种历史序列")


def run_local_scrape(state, cloud_data=None):
    """本地跑 scrape.py，拿当日数据。返回 (data, ok)。"""
    prepare_workdir(cloud_data)
    if state:
        # 写入云端基准，环比才准确
        with open(os.path.join(WORK, "state.json"), "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
    try:
        p = subprocess.run([sys.executable, "scrape.py"], cwd=WORK,
                           capture_output=True, text=True, timeout=420)
    except subprocess.TimeoutExpired:
        log("本地抓取超时（420s）")
        return None, False
    out = (p.stdout or "") + (p.stderr or "")
    with open(os.path.join(CACHE, "scrape.log"), "w", encoding="utf-8") as f:
        f.write(out)
    tail = out.strip().splitlines()[-6:]
    for line in tail:
        log("  scrape | %s" % line)
    if p.returncode != 0:
        log("本地抓取退出码 %s" % p.returncode)
        return None, False
    path = os.path.join(WORK, "data.json")
    if not os.path.exists(path):
        log("本地抓取未产出 data.json")
        return None, False
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    log("本地抓取成功：%s span=%s" % (d.get("meta", {}).get("update_time"),
                                    d.get("meta", {}).get("data_span")))
    return d, True


def board_date(d):
    """取各板块中最新的数据日期（用于标注口径）。"""
    best = ""
    for s in d.get("sections", []):
        import re
        for mo, dd in re.findall(r"(\d{1,2})/(\d{1,2})", str(s.get("tag", ""))):
            key = "%02d/%02d" % (int(mo), int(dd))
            if key > best:
                best = key
    return best


def main():
    os.makedirs(CACHE, exist_ok=True)
    cloud_data = pull_cloud_data()
    state = pull_cloud_state()

    local_data, ok = (None, False)
    if "--fallback" not in sys.argv:
        local_data, ok = run_local_scrape(state, cloud_data)

    if ok and local_data:
        chosen, src = local_data, "local"
    elif cloud_data:
        chosen, src = cloud_data, "cloud"
        log("回退到云端 data.json")
    else:
        log("!! 本地与云端均无数据，无法出报")
        sys.exit(2)

    meta = chosen.get("meta", {})
    payload = {
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "source": src,
        "data_update_time": meta.get("update_time"),
        "data_span": meta.get("data_span"),
        "board_date": board_date(chosen),
        "health": meta.get("health", {}).get("level"),
        "health_summary": meta.get("health", {}).get("summary"),
        "trend_cards": chosen.get("trend_cards", []),
        "sections": chosen.get("sections", []),
        "news": chosen.get("news", []),
    }

    # 补库工作台数据（库存/动销）—— 拿不到不阻塞，但要在日志里说明
    try:
        import replenish_data
        data = replenish_data.load()
        if data:
            s = replenish_data.summarize(data)
            payload["replenish"] = {
                "avail_kg": s["total_avail_kg"],
                "daily_kg": s["total_daily_kg"],
                "days": s["total_days"],
                "sku_total": s["sku_total"],
                "st_cnt": s["st_cnt"],
                "mainlines": {k: replenish_data.pick_mainline(s, k)
                              for k in ("老母鸡", "三黄鸡", "公鸡")},
            }
            log("补库数据已并入：库存 %.0fkg 可售 %.0f 天"
                % (s["total_avail_kg"], s["total_days"] or 0))
        else:
            payload["replenish"] = None
            log("补库数据不可用（缓存缺失或解析失败），本期晨报不带库存板块")
    except Exception as e:
        payload["replenish"] = None
        log("补库数据读取异常：%s" % e)

    # 次日行情 —— 源站当天晚上就发明天的稿（817 棚前最典型）
    try:
        import next_day_price
        nd = next_day_price.fetch()
        payload["next_day"] = nd.get("next_day")
        # 原因类信息：源站的行情分析文（肉鸡/鸡苗）+ 鸡苗价
        payload["analysis"] = nd.get("analysis") or {}
        payload["chick_egg"] = nd.get("chick_egg")
        payload["egg_cull_detail"] = nd.get("today_egg_cull")
        if payload["next_day"]:
            n = payload["next_day"]
            log("次日行情：%s %s（涨%d 平%d 跌%d）"
                % (n["title"], n.get("view") or "", n.get("up", 0),
                   n.get("flat", 0), n.get("down", 0)))
        for k, v in (payload["analysis"] or {}).items():
            log("源站%s分析：%s" % (k, v.get("view") or ""))
    except Exception as e:
        payload["next_day"] = None
        payload["analysis"] = {}
        payload["chick_egg"] = None
        payload["egg_cull_detail"] = None
        log("次日行情抓取异常：%s" % e)

    out_path = os.path.join(OUT, "today.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    log("今日数据已落盘：%s (源=%s 板块日期=%s)"
        % (out_path, src, payload["board_date"]))
    print("OK %s %s" % (out_path, payload["board_date"]))


if __name__ == "__main__":
    main()
