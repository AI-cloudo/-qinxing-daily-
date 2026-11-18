#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
晨报渲染器 —— 把 out/today.json 渲染成手机端简报 HTML。

定位（采购经理 2026-11-06 明确）：
  **只给销售看，不掺采购决策。**
  不出现：锁价比例、落单、补货量、压力等级、采购策略
  只出现：上游价格涨跌、为什么、跟客户怎么说、我们手上还有多少货

所有给销售看的句子都在 talk.py 里（口语化措辞集中管理），
本文件只负责取数、算结构、排版。
版式在 template.html —— 禁止在本文件里手写 HTML 版式。
"""
import html
import json
import os
import re
import sys
import time
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
TPL = os.path.join(HERE, "template.html")

sys.path.insert(0, HERE)
try:
    import talk
except Exception:
    talk = None
try:
    import replenish_data
except Exception:
    replenish_data = None


def esc(s):
    return html.escape(str(s if s is not None else ""))


# 禁用的说法：源站的四字标题 + 研究化/采购化措辞。
# 报告里一旦出现就告警 —— 销售看不懂，等于这一句白写。
# 采购经理 2026-11-07："这虽然专业，但是让人看不懂。"
BANNED = (
    "供需博弈", "高举高打", "链上赋能", "蓄势破防", "抬头翘尾", "循规蹈矩",
    "上下求索", "抱团对标", "虚实交集", "久久比价", "高低相推", "一枝独秀",
    "背离", "观察线", "天花板", "中枢", "传导", "惜售", "延淘",
    "基差", "口径", "成本下移", "挺价", "压栏", "屠企",
)


def jargon_guard(text, where=""):
    hits = [w for w in BANNED if w in str(text or "")]
    if hits:
        print("[warn] %s里出现不好懂的说法：%s" % (where or "报告", "、".join(hits)))
    return hits


def consistency_check(c):
    """晨报正文引用的均价必须和速览卡对得上 —— 数据一致性闸。

    速览卡是采购经理每天看的基准（工作台口径）。正文另算一套数，
    一旦和卡打架，他讲给销售听就会出现两个价（11-17 用户要求对齐工作台）。
    """
    cards = {x.get("name"): x for x in c["cards"]}
    warns = []

    def _chk(card_name, mine, label):
        card = cards.get(card_name)
        cp = num((card or {}).get("price"))
        if cp is None or mine is None:
            return
        if abs(cp - mine) > 0.02:
            warns.append("%s卡价 %s vs 正文%s %s 对不上" % (card_name, card.get("price"), label, mine))

    _chk("老母鸡", c.get("oldhen_avg"), "博亚表均值")
    sd = c.get("sh_detail") or {}
    _chk("三黄鸡", sd.get("all"), "817表均价")
    _chk("公鸡", c.get("gj_avg"), "公鸡表均值")
    for w in warns:
        print("[warn] 数据一致性：" + w)
    if not warns:
        print("数据一致性核对：正文均价与速览卡全部一致")
    return warns


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def safe(fn, *a, **kw):
    """渲染兜底：某个板块出错不该让整份晨报出不来。

    销售早上 8 点前要用这份稿，宁可少一段也不能没有。
    """
    try:
        return fn(*a, **kw)
    except Exception as e:
        print("[warn] 渲染 %s 失败：%s" % (getattr(fn, "__name__", fn), e))
        return ""


def arrow_of(dir_):
    return {"up": "↑", "down": "↓", "flat": "→"}.get(dir_, "→")


def series_of(card, pick_last=True):
    """取走势卡的数值序列（默认取第一条）。"""
    ch = (card or {}).get("chart") or {}
    ss = ch.get("series") or []
    if not ss:
        return []
    return ss[0].get("values") or []


def flat_periods(vals):
    """连着几期没动。老母鸡钉在 4.67 多久了，销售得知道。"""
    vs = [num(v) for v in (vals or [])]
    vs = [x for x in vs if x is not None]
    n = 0
    for i in range(len(vs) - 1, 0, -1):
        if abs(vs[i] - vs[i - 1]) < 0.001:
            n += 1
        else:
            break
    return n


def dir_text(dir_):
    return {"up": "涨", "down": "跌", "flat": "持平"}.get(dir_, "持平")


def pick_card(cards, name):
    for c in cards:
        if c.get("name") == name:
            return c
    return None


def section_by_title(sections, kw):
    for s in sections:
        if kw in str(s.get("title", "")):
            return s
    return None


def find_table(section, kw):
    if not section:
        return None
    for t in section.get("tables", []):
        if kw in str(t.get("cap") or "") or kw in str(t.get("headers") or ""):
            return t
    return None


def row_val(row, idx, default=""):
    try:
        v = row[idx]
    except (IndexError, TypeError):
        return default
    if isinstance(v, dict):
        return v.get("t", default)
    return v


def _chg_amt(chg_text):
    """从「↑0.07」「↓0.01」这类文本里取涨跌金额，取不到算 0。"""
    if not chg_text:
        return 0.0
    m = re.search(r"([\d.]+)", str(chg_text))
    if not m:
        return 0.0
    try:
        return float(m.group(1))
    except ValueError:
        return 0.0


def row_dir(row, idx):
    try:
        v = row[idx]
    except (IndexError, TypeError):
        return "flat"
    if isinstance(v, dict):
        return v.get("dir", "flat")
    return "flat"


# ── 迷你走势图（SVG）────────────────────────────────────────────────
def sparkline(card):
    ch = card.get("chart") or {}
    dates = ch.get("dates") or []
    series = ch.get("series") or []
    if not series or not dates:
        return ""
    vals = [v for v in series[0].get("values", []) if num(v) is not None]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-9:
        hi = lo + 0.01
    W, H, PAD = 600.0, 52.0, 7.0
    n = len(vals)
    step = (W - 2 * PAD) / (n - 1)

    def y(v):
        return PAD + (hi - v) / (hi - lo) * (H - 2 * PAD)

    pts = [(PAD + i * step, y(v)) for i, v in enumerate(vals)]
    d = " ".join("%s%.1f %.1f" % ("M" if i == 0 else "L", x, yy)
                 for i, (x, yy) in enumerate(pts))
    last = pts[-1]
    first = pts[0]
    rising = last[1] < first[1]
    color = "#d63b3b" if rising else ("#0f9d5f" if last[1] > first[1] else "#8a9691")
    area = d + " L%.1f %.1f L%.1f %.1f Z" % (last[0], H - PAD, first[0], H - PAD)
    dots = "".join('<circle cx="%.1f" cy="%.1f" r="2.6" fill="%s"/>' % (x, yy, color)
                   for x, yy in pts)
    return ('<svg class="spark" viewBox="0 0 %.0f %.0f" preserveAspectRatio="none">'
            '<path d="%s" fill="%s" opacity=".10"/>'
            '<path d="%s" fill="none" stroke="%s" stroke-width="2" '
            'stroke-linejoin="round" stroke-linecap="round"/>%s</svg>'
            % (W, H, area, color, d, color, dots))


# ── 数据准备 ───────────────────────────────────────────────────────
def _card_asof(card):
    """速览卡最后一期的数据日期（走势序列的末点日期）。"""
    ch = (card or {}).get("chart") or {}
    ds = ch.get("dates") or []
    return str(ds[-1]) if ds else ""


def _sec_asof(sec, kw="数据日期"):
    """板块标注里的数据日期，如「数据日期 11/10 · 鸡病专业网」。"""
    m = re.search(r"%s\s*(\d{1,2}/\d{1,2})" % re.escape(kw), str((sec or {}).get("tag") or ""))
    return m.group(1) if m else ""


def _price_ctx(d, cards, sections):
    """各品类这期报价的数据日期 + 本简报是哪天出的。

    为什么要这个：源站发稿时间不一样（博亚和讯淘鸡 08:20、鸡蛋 09:20 出稿），
    而晨报 6:30 就生成 —— 所以「老母鸡 4.54」到底是 11/11 的价还是 11/10 的价，
    必须逐个品类标清楚，不能一律写「今天」，更不能编。

    issue = 本简报出的那天（判断「今天」用）；asof = 各品类这期报价的日期。
    """
    ctx = {"asof": {}, "board": d.get("board_date") or "",
           "issue": _issue_date(d)}
    by_name = {c.get("name"): c for c in cards}

    # 老母鸡 / 三黄鸡 / 公鸡：取速览卡走势的末点日期
    for name in ("老母鸡", "三黄鸡", "公鸡"):
        a = _card_asof(by_name.get(name))
        if a:
            ctx["asof"][name] = a

    # 鸡蛋：主产省表来自鸡病专业网汇总稿，日期在板块标注里
    egg_sec = section_by_title(sections, "鸡蛋")
    a = _sec_asof(egg_sec) or (d.get("egg_cull_detail") or {}).get("date") or ""
    if a:
        ctx["asof"]["鸡蛋"] = a
    return ctx


def _issue_date(d):
    """本简报是哪天出的（生成时间），用来判断某个价算不算「今天」。"""
    ga = str(d.get("generated_at") or "")
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", ga)
    if m:
        return "%d/%d" % (int(m.group(2)), int(m.group(3)))
    t = datetime.date.today()
    return "%d/%d" % (t.month, t.day)


def collect(d):
    """从 today.json 汇总晨报需要的全部数据。"""
    cards = d.get("trend_cards", [])
    sections = d.get("sections", [])
    out = {"cards": cards, "sections": sections}

    # 各品类这一期报价是几号的（源站发稿有先后，早班跑常拿到上一期的价）
    out["ctx"] = _price_ctx(d, cards, sections)

    # 鸡蛋：主产省均价 + 方向
    egg_sec = section_by_title(sections, "鸡蛋")
    t = find_table(egg_sec, "主产省") if egg_sec else None
    if not t and egg_sec and egg_sec.get("tables"):
        t = egg_sec["tables"][0]
    egg_avg, egg_dir = None, "flat"
    egg_rows = []
    if t:
        for r in t.get("rows", []):
            v = num(row_val(r, 1))
            if v is not None:
                egg_rows.append((str(r[0]), v, row_val(r, 2), row_dir(r, 2)))
        vals = [x[1] for x in egg_rows]
        if vals:
            egg_avg = round(sum(vals) / len(vals), 2)
            up_n = sum(1 for x in egg_rows if x[3] == "up")
            down_n = sum(1 for x in egg_rows if x[3] == "down")
            flat_n = len(egg_rows) - up_n - down_n
            # 整体方向只认「家数占优」（11-17 用户定，原金额加权规则作废）：
            # 20 个省 1 跌 1 涨 18 平 = 整体没动，个别地方动只是区域性动作，
            # 不能代表全部——哪怕跌的金额大也只能说区域性下跌。
            # 涨/跌的省占 4 成以上、且比对方多，才叫整体涨/整体跌。
            total_n = len(egg_rows)
            if down_n > up_n and down_n >= total_n * 0.4:
                egg_dir = "down"
            elif up_n > down_n and up_n >= total_n * 0.4:
                egg_dir = "up"
            else:
                egg_dir = "flat"
            out["egg_up_n"] = up_n
            out["egg_down_n"] = down_n
            out["egg_flat_n"] = flat_n
            out["egg_total"] = len(egg_rows)
            # 谁在涨、谁在跌、涨跌几个钱 —— 文案里要把金额说清楚
            out["egg_movers_up"] = [(x[0], "up", _chg_amt(x[2]))
                                    for x in egg_rows if x[3] == "up"]
            out["egg_movers_down"] = [(x[0], "down", _chg_amt(x[2]))
                                      for x in egg_rows if x[3] == "down"]
    out["egg_avg"] = egg_avg
    out["egg_dir"] = egg_dir
    out["egg_rows"] = egg_rows[:6]

    # 老母鸡分省明细（博亚和讯出场价）—— 报区间和涨跌家数用。
    # 源页里带「博亚和讯」的表不止一份，取解析出行数最多的那份（全 19 省）。
    old_sec = section_by_title(sections, "淘汰鸡")
    oldhen_prov = None
    if old_sec:
        for t in old_sec.get("tables", []):
            cap = str(t.get("cap") or "")
            if "博亚和讯" not in cap:
                continue
            vals, ups_l, downs_l = [], [], []
            for r in t.get("rows", []):
                v = num(row_val(r, 1))
                dr = row_dir(r, 2)
                if v is not None:
                    vals.append(v)
                if dr == "up":
                    ups_l.append((str(r[0]), "up", _chg_amt(row_val(r, 2))))
                elif dr == "down":
                    downs_l.append((str(r[0]), "down", _chg_amt(row_val(r, 2))))
            if vals and (oldhen_prov is None or len(vals) > oldhen_prov["total"]):
                oldhen_prov = {"lo": min(vals), "hi": max(vals),
                               "total": len(vals),
                               "up": len(ups_l), "down": len(downs_l),
                               "movers": ups_l + downs_l}
    out["oldhen_prov"] = oldhen_prov
    out["oldhen_avg"] = None   # 下方重算：用于跟速览卡卡价核对一致性

    # 老母鸡全国均值（重算一份用于一致性核对：必须等于速览卡的卡价）
    if oldhen_prov is not None:
        old_sec2 = section_by_title(sections, "淘汰鸡")
        for t in (old_sec2.get("tables") or []) if old_sec2 else []:
            cap = str(t.get("cap") or "")
            if "博亚和讯" not in cap:
                continue
            vals2 = [num(row_val(r, 1)) for r in t.get("rows", [])]
            vals2 = [v for v in vals2 if v is not None]
            if len(vals2) == oldhen_prov["total"]:
                out["oldhen_avg"] = round(sum(vals2) / len(vals2), 2)
                break

    # 三黄鸡817：大/小规格均价 + 当日报价点涨跌（口径同速览卡：区间取中值再平均）
    sh_sec = section_by_title(sections, "三黄鸡")
    sh_detail = None
    if sh_sec:
        for t in sh_sec.get("tables", []):
            if "817肉杂棚前价" not in str(t.get("cap") or ""):
                continue
            big, small, movers = [], [], []
            for r in t.get("rows", []):
                nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?",
                                                     str(row_val(r, 2)))]
                if not nums:
                    continue
                mid = sum(nums) / len(nums)
                (small if "小规格" in str(r[0]) else big).append(mid)
                dr = row_dir(r, 3)
                if dr in ("up", "down"):
                    movers.append((str(r[0]), dr, _chg_amt(row_val(r, 3))))
            if big:
                sh_detail = {
                    "big": round(sum(big) / len(big), 3),
                    "small": round(sum(small) / len(small), 3) if small else None,
                    "all": round((sum(big) + sum(small)) / (len(big) + len(small)), 3)
                           if small else round(sum(big) / len(big), 3),
                    "movers": movers,
                    "total": len(big) + len(small),
                }
            break
    out["sh_detail"] = sh_detail

    # 公鸡产区区间
    #
    # 口径必须跟速览卡完全一致，否则正文和卡片会自相矛盾：
    # 速览卡取的是「品种名里带『公』的公鸡行」的中值（6 个产区），
    # 区间报 4.70-8.50。而正文原来只排除了「817」品种，
    # 于是把「皖南麻鸡成鸡 4.30-4.40」（母鸡行）和「菏泽大红公鸡 8.00-9.00」
    # 的区间端点也算了进去，正文报「最便宜 4 块 3、最贵 9 块钱」，
    # 和卡片上的「区间 4.70-8.50」直接打架（11-17 修）。
    # 教训同 11-16：拿哪几行当公鸡样本，只认速览卡那一份，别自己另算一套。
    gj_sec = section_by_title(sections, "公鸡")
    gj_lo = gj_hi = None
    gj_avg = None
    gj_cnt = {}          # 当日涨跌家数（口径同速览卡 note 的「N涨/N平/N跌」）
    if gj_sec and gj_sec.get("tables"):
        pv = []
        for r in gj_sec["tables"][0].get("rows", []):
            try:
                raw = str(r[2])
                breed = str(r[1]) if len(r) > 1 else ""
                dtag = str(r[4]) if len(r) > 4 else ""
            except (IndexError, TypeError):
                continue
            if "公" not in breed:            # 只要公鸡行：跟速览卡同一样本
                continue
            if "滞后" in dtag or "未日更" in dtag:
                continue
            nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", raw.replace(",", ""))]
            if nums:
                pv.append(sum(nums) / len(nums))   # 取中值，跟速览卡一致
                gj_cnt[row_dir(r, 3)] = gj_cnt.get(row_dir(r, 3), 0) + 1
        if pv:
            gj_lo = min(pv)
            gj_hi = max(pv)
            gj_avg = round(sum(pv) / len(pv), 2)
    out["gj_lo"], out["gj_hi"], out["gj_avg"] = gj_lo, gj_hi, gj_avg
    out["gj_cnt"] = gj_cnt

    # 菏泽本地价
    heze = []
    if gj_sec and gj_sec.get("tables"):
        for r in gj_sec["tables"][0].get("rows", []):
            try:
                if "菏泽" in str(r[0]):
                    heze.append("%s %s 元/斤" % (r[1], r[2]))
            except (IndexError, TypeError):
                continue
    out["heze"] = "；".join(heze) if heze else None

    # 补库货盘（只保留销售关心的：能卖几天、哪些缺货）
    rep = d.get("replenish")
    if rep:
        mains = rep.get("mainlines") or {}
        out["stock"] = {
            "avail_kg": rep.get("avail_kg"),
            "days": rep.get("days"),
            "mains": {k: {"days": v.get("days"), "sku_count": v.get("sku_count"),
                           "urgent": v.get("urgent") or [],
                           "out_of_stock": v.get("out_of_stock") or [],
                           "out_of_stock_count": v.get("out_of_stock_count") or 0}
                      for k, v in mains.items() if v},
        }
    else:
        out["stock"] = None
    return out


# ── 板块渲染 ───────────────────────────────────────────────────────
def render_chain(c):
    """传导链总览 —— 一眼看出谁牵动谁。"""
    old = pick_card(c["cards"], "老母鸡")
    sh = pick_card(c["cards"], "三黄鸡")
    gj = pick_card(c["cards"], "公鸡")
    items = talk.chain_overview(
        c.get("egg_avg"), c.get("egg_dir"),
        num(old.get("price")) if old else None, old.get("dir") if old else "flat",
        num(sh.get("price")) if sh else None, sh.get("dir") if sh else "flat",
        num(gj.get("price")) if gj else None, gj.get("dir") if gj else "flat",
        next_day=c.get("next_day"), ctx=c.get("ctx"))
    rows = []
    for it in items:
        rows.append(
            '<div class="chnode">'
            '<span class="cfrm">%s</span><span class="carw">→</span>'
            '<span class="cto">%s</span>'
            '<span class="cstate">%s</span>'
            '<span class="cwhy">%s</span>'
            '</div>' % (esc(it["from"]), esc(it["to"]),
                        esc(it["state"]), esc(it["why"])))
    return ('<div class="chain">'
            '<div class="ctitle">先看这条线：谁带动谁</div>%s</div>' % "".join(rows))


def render_factors(title, tag, head, factors, price=None, unit="", dir_="flat"):
    """一个品类卡：标题 + 一句话结论 + 分条因素。"""
    dc = {"up": "up", "down": "down", "flat": "flat"}.get(dir_, "flat")
    price_html = ""
    if price not in (None, ""):
        u = unit if unit.startswith(" ") else (" " + unit if unit else "")
        price_html = ('<div class="price"><span class="v">%s</span>'
                      '<span class="u">%s</span>'
                      '<span class="chg %s">%s</span></div>'
                      % (esc(price), esc(u), dc, arrow_of(dir_)))
    fac_html = ""
    for i, (name, txt) in enumerate(factors or []):
        fac_html += ('<div class="fac">'
                     '<span class="fn">%s</span>'
                     '<span class="ft">%s</span>'
                     '</div>' % (esc(name), esc(txt)))
    tag_html = '<span class="tag%s">%s</span>' % (
        "" if tag == "主" else " g", esc(tag))
    return """<div class="cat">
  <div class="cat-h"><span class="nm">%s</span>%s</div>
  %s
  <div class="fhead">%s</div>
  <div class="facs">%s</div>
</div>""" % (esc(title), tag_html, price_html, esc(head), fac_html)


def render_verdict(c):
    """开头一句话 —— 销售早会念的第一句。

    措辞全在 talk.verdict()，这里只负责把数凑齐。
    """
    old = pick_card(c["cards"], "老母鸡")
    sh = pick_card(c["cards"], "三黄鸡")
    gj = pick_card(c["cards"], "公鸡")
    return talk.verdict(
        c.get("egg_avg"), c.get("egg_dir") or "flat",
        num(old.get("price")) if old else None, old.get("dir") if old else "flat",
        num(sh.get("price")) if sh else None, sh.get("dir") if sh else "flat",
        num(gj.get("price")) if gj else None, gj.get("dir") if gj else "flat",
        next_day=c.get("next_day"), series=series_of(sh), ctx=c.get("ctx"))


def render_stock(c):
    """手上还有多少货 —— 销售要知道能不能接单、哪些要提前说。"""
    st = c.get("stock")
    if not st:
        return ""
    days = st.get("days")
    rows = []
    for name in ("老母鸡", "三黄鸡", "公鸡"):
        m = (st.get("mains") or {}).get(name)
        if not m:
            continue
        d = m.get("days")
        cls = "up" if (d is not None and d <= 15) else ""
        rows.append("<tr><td>%s</td><td class='n %s'>%s</td></tr>"
                    % (esc(name), cls, ("%.0f 天" % d) if d is not None else "—"))
    if not rows:
        return ""
    short = []
    for name in ("老母鸡", "三黄鸡", "公鸡"):
        m = (st.get("mains") or {}).get(name)
        if not m:
            continue
        n_short = m.get("out_of_stock_count") or 0
        if n_short:
            short.append((name, n_short))
    short_html = ""
    if short:
        # 措辞统一在 talk.py，build.py 只负责排版
        head, details = talk.stock_out_note(st.get("mains"))
        if head:
            short_html = ('<div class="hint" style="margin-top:8px">%s</div>' % esc(head))
        if details:
            short_html += ('<div class="hint" style="margin-top:4px">%s</div>'
                           % esc("；".join(details)))
    return """<div class="cat">
  <div class="cat-h"><span class="nm">我们手上还有什么货</span><span class="tag g">接单前看一眼</span></div>
  <table><tr><th>品类</th><th>大概还能卖</th></tr>%s</table>
  %s
</div>""" % ("".join(rows), short_html)


def render_actions(c):
    """销售今天该做什么 —— 只讲对外沟通和报价节奏，不讲采购。"""
    old = pick_card(c["cards"], "老母鸡")
    sh = pick_card(c["cards"], "三黄鸡")
    gj = pick_card(c["cards"], "公鸡")
    stock = c.get("stock") or {}
    gj_days = ((stock.get("mains") or {}).get("公鸡") or {}).get("days")
    acts = talk.today_action(
        c.get("egg_avg"), c.get("egg_dir"),
        old.get("dir") if old else "flat",
        sh.get("dir") if sh else "flat",
        gj.get("dir") if gj else "flat",
        gj_days=gj_days, next_day=c.get("next_day"))
    if not acts:
        return ""
    out = ['<div class="cat"><div class="cat-h"><span class="nm">今天跟客户怎么说</span>'
           '</div><div class="acts">']
    for tag, txt in acts:
        out.append('<div class="act"><span class="b">%s</span><span>%s</span></div>'
                   % (esc(tag), esc(txt)))
    out.append("</div></div>")
    return "".join(out)


def render_news(d):
    items = []
    for n in (d.get("news") or [])[:5]:
        t_ = esc(n.get("title"))
        date = esc(n.get("date") or "")
        src_ = esc(n.get("src") or "")
        extra_lbl = " ".join(x for x in (date, src_) if x)
        items.append('<a href="%s" target="_blank">%s<span>%s</span></a>'
                     % (esc(n.get("url", "#")), t_, extra_lbl))
    return "".join(items) or '<div style="color:#8a9691;font-size:13px">暂无</div>'


def _title_of(board_date):
    """页面标题 = 手机桌面图标下显示的名字。

    board_date 形如 "11/06" → "11.6号行情日报"
    销售一眼能认出是当天的；桌面图标名也跟着变。
    """
    if not board_date:
        return "行情日报"
    m = re.match(r"(\d{1,2})/(\d{1,2})", str(board_date))
    if not m:
        return "%s 行情日报" % board_date
    mo, day = int(m.group(1)), int(m.group(2))
    return "%d.%d号行情日报" % (mo, day)


def build():
    """渲染晨报。

    加了整体兜底：任何单点异常（比如次日稿没抓到、某个字段缺失）
    都不该让整份晨报出不来 —— 销售早上 8 点前要用，宁可少一段也不能没有。
    """
    src = os.path.join(OUT, "today.json")
    if not os.path.exists(src):
        raise SystemExit("缺少 %s，请先跑 fetch_data.py" % src)
    with open(src, encoding="utf-8") as f:
        d = json.load(f)

    c = collect(d)
    c["next_day"] = d.get("next_day") or {}
    c["analysis"] = d.get("analysis") or {}
    c["chick_egg"] = d.get("chick_egg") or {}
    safe(consistency_check, c)

    old_c = pick_card(c["cards"], "老母鸡")
    sh_c = pick_card(c["cards"], "三黄鸡")
    gj_c = pick_card(c["cards"], "公鸡")

    # 顺序按传导关系：蛋价（发动机）→ 老母鸡（主线）→ 三黄鸡 → 公鸡
    # 同一个原因只在一处讲透，后面引用不复述
    parts = []
    parts.append(safe(render_chain, c))

    egg_block = talk.egg_factors(c.get("egg_avg"), c.get("egg_dir"), stat={
        "up_n": c.get("egg_up_n"), "down_n": c.get("egg_down_n"),
        "flat_n": c.get("egg_flat_n"), "total": c.get("egg_total"),
        "movers_up": c.get("egg_movers_up"),
        "movers_down": c.get("egg_movers_down")},
        ctx=c.get("ctx"))
    if egg_block:
        parts.append(safe(render_factors, "鸡蛋", "发动机", egg_block["head"],
                          egg_block["factors"], price=c.get("egg_avg"),
                          unit="元/斤", dir_=c.get("egg_dir")))

    old_block = talk.oldhen_factors(
        num(old_c.get("price")) if old_c else None,
        old_c.get("dir") if old_c else "flat",
        c.get("egg_avg"), c.get("egg_dir"),
        flat_days=flat_periods(series_of(old_c)),
        prov=c.get("oldhen_prov"), ctx=c.get("ctx"))
    if old_block:
        parts.append(safe(render_factors, "老母鸡", "主", old_block["head"],
                          old_block["factors"],
                          price=old_c.get("price") if old_c else None,
                          unit=(old_c.get("unit") or "") if old_c else "",
                          dir_=old_c.get("dir") if old_c else "flat"))

    sh_block = talk.sanhuang_factors(
        num(sh_c.get("price")) if sh_c else None,
        sh_c.get("dir") if sh_c else "flat",
        next_day=c.get("next_day"), chick_egg=c.get("chick_egg"),
        analysis=c.get("analysis"), series=series_of(sh_c), ctx=c.get("ctx"),
        detail=c.get("sh_detail"))
    if sh_block:
        parts.append(safe(render_factors, "三黄鸡（817）", "冻品原料", sh_block["head"],
                          sh_block["factors"],
                          price=sh_c.get("price") if sh_c else None,
                          unit=(sh_c.get("unit") or "") if sh_c else "",
                          dir_=sh_c.get("dir") if sh_c else "flat"))

    gj_block = talk.gongji_factors(
        num(gj_c.get("price")) if gj_c else None,
        gj_c.get("dir") if gj_c else "flat",
        lo=c.get("gj_lo"), hi=c.get("gj_hi"), heze=c.get("heze"),
        flat_days=flat_periods(series_of(gj_c)), cnt=c.get("gj_cnt"),
        ctx=c.get("ctx"))
    if gj_block:
        parts.append(safe(render_factors, "公鸡", "活禽", gj_block["head"],
                          gj_block["factors"],
                          price=gj_c.get("price") if gj_c else None,
                          unit=(gj_c.get("unit") or "") if gj_c else "",
                          dir_=gj_c.get("dir") if gj_c else "flat"))

    parts.append(safe(render_stock, c))
    parts.append(safe(render_actions, c))

    with open(TPL, encoding="utf-8") as f:
        tpl = f.read()

    # 标题：手机上"添加到主屏幕"后，桌面图标下面的名字取自这里。
    # 所以做成「11.6号行情日报」这种一眼能认出是当天的写法。
    board = d.get("board_date") or ""
    title = _title_of(board)
    verdict = render_verdict(c)

    health = d.get("health")
    if health == "ok":
        hn = "各板块数据均为最新一期。"
    else:
        hn = ("注意：%s（部分源站更新节奏偏慢，该项按它最新一期算，"
              "正文里已标明日期）" % (d.get("health_summary") or "有滞后项"))

    src_desc = ("本地实时抓取" if d.get("source") == "local" else "云端自动更新")
    nd = d.get("next_day") or {}
    if nd.get("rows"):
        src_desc += "，含明日（%s）已发布价格" % nd.get("date")

    # 版本号：每次生成都变。写进页面，手机打开时能确认是不是最新的一份。
    site_dir = os.path.join(HERE, "site")
    os.makedirs(site_dir, exist_ok=True)
    ver = "%s-%d" % (d.get("board_date") or "v", int(time.time()))

    html_out = (tpl
                .replace("__TITLE__", esc(title))
                .replace("__BOARD_DATE__", esc(d.get("board_date") or "—"))
                .replace("__GEN_TIME__", esc(d.get("generated_at") or "—"))
                .replace("__VERDICT__", esc(verdict))
                .replace("__CATEGORIES__", "".join(parts))
                .replace("__NEWS__", render_news(d))
                .replace("__SOURCE_DESC__", esc(src_desc))
                .replace("__HEALTH_NOTE__", esc(hn))
                .replace("__BUILD_VERSION__", esc(ver)))

    out_path = os.path.join(OUT, "morning_brief.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_out)

    # 同步到 site/ 目录（用于发布成手机可访问的在线链接）
    with open(os.path.join(site_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(html_out)

    # 版本号落盘，方便排查"手机看到的是不是最新一份"
    with open(os.path.join(site_dir, "version.txt"), "w", encoding="utf-8") as f:
        f.write(ver)
    print("版本号: %s" % ver)

    # 纯文本版，方便贴微信 —— 与 HTML 同结构（因素分行）
    txt = ["【%s 上游行情】" % (d.get("board_date") or ""), "", verdict, ""]

    def dump(title, head, factors):
        txt.append("── %s ──" % title)
        txt.append(head)
        for name, t in (factors or []):
            txt.append("  · %s：%s" % (name, t))
        txt.append("")

    if egg_block:
        dump("鸡蛋", egg_block["head"], egg_block["factors"])
    if old_block:
        dump("老母鸡", old_block["head"], old_block["factors"])
    if sh_block:
        dump("三黄鸡（817）", sh_block["head"], sh_block["factors"])
    if gj_block:
        dump("公鸡", gj_block["head"], gj_block["factors"])

    if c.get("stock"):
        txt.append("── 我们手上还有什么货 ──")
        for name in ("老母鸡", "三黄鸡", "公鸡"):
            m = (c["stock"].get("mains") or {}).get(name)
            if m and m.get("days") is not None:
                txt.append("  %s：大概还能卖 %.0f 天" % (name, m["days"]))
        # 断货品项（措辞在 talk.py）
        _head, _details = talk.stock_out_note(c["stock"].get("mains"))
        for _d in _details:
            txt.append("  %s" % _d)
        txt.append("")
    txt.append("── 今天跟客户怎么说 ──")
    for tag, t in talk.today_action(
            c.get("egg_avg"), c.get("egg_dir"),
            old_c.get("dir") if old_c else "flat",
            sh_c.get("dir") if sh_c else "flat",
            gj_c.get("dir") if gj_c else "flat",
            gj_days=((c.get("stock") or {}).get("mains") or {}).get("公鸡", {}).get("days"),
            next_day=c.get("next_day")):
        txt.append("  【%s】%s" % (tag, t))
    txt.append("")
    txt.append(hn)
    txt_path = os.path.join(OUT, "morning_brief.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(txt))

    print("VERDICT: %s" % verdict)
    print("HTML: %s" % out_path)
    print("TXT : %s" % txt_path)
    jargon_guard("\n".join(txt), "正文")


if __name__ == "__main__":
    build()
