#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
次日行情 + 原因类信息抓取。

源站（鸡病专业网）有个特点：行情稿和分析稿分开发。
  · 行情稿：《11月8日部分地区肉杂817棚前价格行情》—— 给价格、给逐地涨跌
  · 分析稿：《11月8日肉鸡行情分析：链上赋能，蓄势破防》
              《11月8日鸡苗行情分析：循规蹈矩，上下求索》—— 给原因、给地区行情

本模块把两类都抓下来，晨报才能讲清"为什么涨为什么跌"，而不是只报数字。

抓取要点（实测踩过的坑）：
  1. 正文必须带 Referer + 桌面 UA，否则被反爬挡掉只返回 script
  2. 页面导航栏文字会混进正文，要按关键词过滤
  3. 地名正则要排除「小规格/大规格」这类规格词（会被误当地名）
  4. 鸡蛋汇总同一省有多条（不同城市），要按省去重，涨跌取多数派
"""
import datetime
import re
import urllib.request

UA = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Referer": "https://www.jbzyw.com/",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

BASE = "https://www.jbzyw.com"

# 导航栏/页脚噪声，正文提取时要滤掉
NAV_WORDS = set("首页 市场 肉鸡 蛋鸡 817 麻鸡 豆粕 玉米 添加剂 资讯 行业关注 法规 "
                 "会讯 企业传真 兽药 经营 看天气 圣农 饲养 市场行情 817/麻鸡行情 "
                 "肉鸡价格行情 蛋鸡价格行情".split())


def _get(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="ignore")


def _text(html):
    """去 script/style，取正文文本行（滤掉导航噪声）。"""
    h = re.sub(r"<script.*?</script>", " ", html, flags=re.S)
    h = re.sub(r"<style.*?</style>", " ", h, flags=re.S)
    h = re.sub(r"<[^>]+>", "\n", h)
    lines = [l.strip() for l in h.split("\n") if l.strip()]
    return [l for l in lines
            if l not in NAV_WORDS
            and len(l) > 8
            and not l.startswith("http")
            and "鸡病专业网肉鸡信息采集" not in l
            and not l.startswith("◆")
            and "凡注明为其它媒体" not in l
            and "如对本文有任何疑问" not in l
            and "上述价格仅供参考" not in l
            and "同一地区可能存在一些浮动差异" not in l
            and "建议实单实谈" not in l]


def _month_day(s):
    m = re.search(r"(\d{1,2})月(\d{1,2})日", s)
    return (int(m.group(1)), int(m.group(2))) if m else None


def _list_articles():
    arts = {}
    for path in ("", "/lists/3", "/lists/192", "/lists/279", "/lists/1"):
        try:
            h = _get(BASE + path)
        except Exception:
            continue
        for m in re.finditer(r'href="(/view/\d+)"[^>]*title="([^"]+)"', h):
            arts[m.group(1)] = m.group(2)
    return arts


# ── 817 棚前价 ─────────────────────────────────────────────────────
def _parse_817(lines):
    rows = []
    for l in lines:
        if "元/斤" not in l or "棚前" not in l:
            continue
        m = re.match(r"^([\u4e00-\u9fa5]{2,4}?)(?=[\d.]|[一-鿿]*斤)", l)
        if not m:
            continue
        cand = m.group(1)
        place = ("全国小规格" if "小" in cand else "全国大规格") if "规格" in cand else cand
        pm = None
        for c in re.finditer(r"(\d+\.\d+)-(\d+\.\d+)元/斤", l):
            pm = c
        if pm is None:
            p1 = re.search(r"(\d+\.\d+)元/斤", l)
            if not p1:
                continue
            lo = hi = float(p1.group(1))
        else:
            lo, hi = float(pm.group(1)), float(pm.group(2))
        chg = ""
        cm = re.search(r"与\d+日相比(上涨|下跌|下滑|回落|持平|持平不变)([\d.]*)", l)
        if cm:
            chg = {"上涨": "+", "下跌": "-", "下滑": "-", "回落": "-",
                   "持平": "→", "持平不变": "→"}[cm.group(1)]
            if cm.group(2):
                chg += cm.group(2)
        spec = ""
        sm = re.search(r"([\d.]+(?:-\d+[\d.]*)?斤(?:以上)?)", l)
        if sm:
            spec = sm.group(1)
        rows.append({"place": place, "lo": lo, "hi": hi, "chg": chg,
                     "spec": spec, "raw": l})
    return rows


# ── 鸡蛋 / 淘汰鸡 ─────────────────────────────────────────────────
def _parse_egg_cull(lines):
    by_prov = {}
    for l in lines:
        m = re.search(r"([\u4e00-\u9fa5]{2,4})地区[：:].{0,80}?(\d+\.\d+)-?(\d+\.\d+)?元/斤[，,]\s*"
                      r"与昨日相比(涨|跌|持平)[，，]\s*淘汰鸡(\d+\.\d+)元/斤", l)
        if not m:
            continue
        prov = m.group(1)
        egg_lo = float(m.group(2))
        egg_hi = float(m.group(3)) if m.group(3) else egg_lo
        d = {"涨": "up", "跌": "down", "持平": "flat"}[m.group(4)]
        by_prov.setdefault(prov, []).append(
            {"prov": prov, "egg_lo": egg_lo, "egg_hi": egg_hi,
             "egg_dir": d, "cull": float(m.group(5))})
    rows = []
    for prov, lst in by_prov.items():
        best = max(lst, key=lambda x: (x["egg_lo"] + x["egg_hi"]))
        dirs = [x["egg_dir"] for x in lst]
        up, down = dirs.count("up"), dirs.count("down")
        best["egg_dir"] = "up" if up > down else ("down" if down > up else "flat")
        best["cities"] = len(lst)
        rows.append(best)
    return rows


# ── 行情分析文（原因）──────────────────────────────────────────────
def _parse_analysis(lines):
    """从分析文里提取：总观点 + 地区观点。

    源站结构：
      《11月8日肉鸡行情分析：链上赋能，蓄势破防》
        → "鸡病专业网独家分析：链上赋能，蓄势破防"      ← 整体判断
        → "山东地区：历劫松绑驭势竞合，佛系比价按需加收" ← 地区判断
    """
    view = ""
    region = ""
    for l in lines:
        if "独家分析" in l and "：" in l:
            view = l.split("：", 1)[1].strip()
            continue
        if "：" in l:
            head, rest = l.split("：", 1)
            if head.strip().endswith("地区") and len(rest.strip()) < 60:
                region = rest.strip()
    return view, region


def _parse_chick_egg(lines):
    """鸡苗价：苗鸡开票价/到户价，元/羽。"""
    rows = []
    for l in lines:
        if "元/羽" not in l:
            continue
        m = re.match(r"^([\u4e00-\u9fa5]{2,6}地区)苗鸡(开票|到户)价(\d+\.\d+)-(\d+\.\d+)元/羽"
                     r"，与\d+日相比(上涨|下跌|持平)([\d.]*)", l)
        if m:
            rows.append({"place": m.group(1).replace("地区", ""),
                         "kind": m.group(2),
                         "lo": float(m.group(3)), "hi": float(m.group(4)),
                         "chg": {"上涨": "+", "下跌": "-", "持平": "→"}[m.group(5)]
                                 + (m.group(6) or "")})
    return rows


def fetch():
    """抓次日行情与分析。返回 dict。"""
    out = {"next_day": None, "today_egg_cull": None,
           "analysis": {}, "chick_egg": None, "notes": []}
    today = datetime.date.today()
    arts = _list_articles()
    if not arts:
        out["notes"].append("源站列表页未取到文章")
        return out

    def is_future(md):
        try:
            return datetime.date(today.year, md[0], md[1]) > today
        except ValueError:
            return False

    # 1) 次日 817 棚前价
    for url, title in arts.items():
        md = _month_day(title)
        if not md or "817棚前" not in title.replace(" ", "") or not is_future(md):
            continue
        try:
            lines = _text(_get(BASE + url))
        except Exception:
            continue
        rows = _parse_817(lines)
        if not rows:
            continue
        view = ""
        for l in lines:
            if "市场" in l and "：" in l and ("行情" in l or "棚前" in l):
                seg = l.split("：", 1)[1].strip()
                if seg and len(seg) < 40:
                    view = seg
                    break
        out["next_day"] = {
            "title": title, "date": "%d/%d" % (md[0], md[1]), "view": view,
            "rows": rows,
            "up": sum(1 for r in rows if r["chg"].startswith("+")),
            "flat": sum(1 for r in rows if r["chg"] == "→"),
            "down": sum(1 for r in rows if r["chg"].startswith("-")),
        }
        break

    # 2) 行情分析（原因）+ 鸡苗价
    #    同类可能有多篇（11/7、11/8 各一篇），取行情日最新的那篇
    for kind, kw in (("肉鸡", "肉鸡行情分析"), ("鸡苗", "鸡苗行情分析")):
        cands = []
        for url, title in arts.items():
            md = _month_day(title)
            if not md or kw not in title:
                continue
            cands.append((md, url, title))
        if not cands:
            continue
        cands.sort(key=lambda x: (x[0][0], x[0][1]))
        for md, url, title in reversed(cands):
            try:
                lines = _text(_get(BASE + url))
            except Exception:
                continue
            view, region = _parse_analysis(lines)
            if view:
                out["analysis"][kind] = {
                    "title": title, "date": "%d/%d" % (md[0], md[1]),
                    "view": view, "region": region,
                }
            if kind == "鸡苗":
                rows = _parse_chick_egg(lines)
                if rows:
                    out["chick_egg"] = {
                        "title": title, "date": "%d/%d" % (md[0], md[1]),
                        "rows": rows,
                    }
            if view:
                break

    # 3) 最新的鸡蛋淘汰鸡汇总
    best = None
    for url, title in arts.items():
        if "鸡蛋、淘汰鸡价格汇总" not in title:
            continue
        md = _month_day(title)
        if not md:
            continue
        key = (md[0], md[1])
        if best is None or key > best[0]:
            best = (key, url, title)
    if best:
        try:
            lines = _text(_get(BASE + best[1]))
            rows = _parse_egg_cull(lines)
            if rows:
                out["today_egg_cull"] = {"title": best[2],
                                         "date": "%d/%d" % best[0], "rows": rows}
        except Exception:
            pass

    return out


if __name__ == "__main__":
    import json
    d = fetch()
    if d["next_day"]:
        n = d["next_day"]
        print("【次日817】%s (%s) %s" % (n["title"], n["date"], n["view"]))
        print("  涨%d 平%d 跌%d" % (n["up"], n["flat"], n["down"]))
        for r in n["rows"]:
            print("   %-8s %s-%s %s" % (r["place"], r["lo"], r["hi"], r["chg"]))
    else:
        print("无次日817稿")
    print()
    for k, v in (d.get("analysis") or {}).items():
        print("【%s分析】%s" % (k, v["view"]))
        if v.get("region"):
            print("   地区：%s" % v["region"])
    if d.get("chick_egg"):
        c = d["chick_egg"]
        print("\n【鸡苗】%s (%s)" % (c["title"], c["date"]))
        for r in c["rows"][:8]:
            print("   %-6s %s %s-%s %s" % (r["place"], r["kind"], r["lo"], r["hi"], r["chg"]))
    if d.get("today_egg_cull"):
        e = d["today_egg_cull"]
        print("\n【鸡蛋淘汰鸡】%s (%s) %d 省" % (e["title"], e["date"], len(e["rows"])))
        for r in e["rows"][:8]:
            print("   %-4s 蛋%s-%s %s 淘汰鸡%s" % (r["prov"], r["egg_lo"], r["egg_hi"],
                                              r["egg_dir"], r["cull"]))
    print("\nnotes:", d["notes"])
