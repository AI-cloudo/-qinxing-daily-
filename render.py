#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全国禽类行情日报 · 模板渲染器（版式固化，仅数据变化）

用法：python3 render.py [data.json] [输出.html]
默认：渲染同目录下 data.json -> index.html

说明：
- 本文件包含日报全部 CSS 与页面骨架，属于"模板"，不要轻易修改。
- 每日自动化只需更新 data.json 中的数据，再运行本脚本即可生成新版日报。
- data.json 中允许内联少量 HTML 标记（如 <strong class='up'>红涨</strong>），
  class 可用：up(涨/红)、down(跌/绿)、flat(平)、hi(高价强调)。
- 表格单元格：纯字符串=普通单元格；{"t":"↑1.2%","dir":"up"}=涨跌着色单元格。
"""
import json
import os
import sys

# ============ 固定样式（模板，勿动） ============
CSS = """
:root {
  --bg: #f5f6fa;
  --card-bg: #ffffff;
  --text: #1a1a2e;
  --text-sec: #555;
  --text-muted: #999;
  --accent: #d4380d;
  --accent-light: #fff2e8;
  --up: #cf1322;
  --down: #389e0d;
  --flat: #8c8c8c;
  --border: #e8e8e8;
  --shadow: 0 1px 4px rgba(0,0,0,0.06);
  --radius: 8px;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.6;
  font-size: 14px;
}
.container { max-width: 960px; margin: 0 auto; padding: 20px; }

/* Header */
.header {
  background: linear-gradient(135deg, #d4380d, #fa541c);
  color: #fff;
  padding: 24px 28px;
  border-radius: var(--radius);
  margin-bottom: 20px;
  box-shadow: 0 2px 8px rgba(212,56,13,0.2);
}
.header h1 { font-size: 22px; font-weight: 700; margin-bottom: 4px; }
.header .subtitle { font-size: 13px; opacity: 0.9; }
.header .date-badge {
  display: inline-block;
  background: rgba(255,255,255,0.2);
  padding: 2px 10px;
  border-radius: 4px;
  font-size: 12px;
  margin-top: 6px;
}

/* Section */
.section {
  background: var(--card-bg);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  margin-bottom: 16px;
  overflow: hidden;
}
.section-header {
  padding: 14px 20px;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  gap: 8px;
}
.section-header .icon { font-size: 18px; }
.section-header h2 { font-size: 16px; font-weight: 600; flex: 1; }
.section-header .tag {
  font-size: 11px;
  color: var(--text-muted);
  background: var(--bg);
  padding: 2px 8px;
  border-radius: 4px;
}
.section-body { padding: 16px 20px; }

/* Tables */
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th {
  text-align: left;
  padding: 8px 10px;
  background: #fafafa;
  color: var(--text-sec);
  font-weight: 600;
  border-bottom: 2px solid var(--border);
  white-space: nowrap;
}
td { padding: 7px 10px; border-bottom: 1px solid #f0f0f0; }
tr:last-child td { border-bottom: none; }
tr:hover td { background: #fafcff; }

.up { color: var(--up); font-weight: 600; }
.down { color: var(--down); font-weight: 600; }
.flat { color: var(--flat); }

/* ===== 核心品类走势总览 ===== */
.trend-overview {
  background: var(--card-bg);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  margin-bottom: 16px;
  overflow: hidden;
}
.trend-overview .section-header {
  background: linear-gradient(135deg, #1a1a2e, #2d2d44);
  border-bottom: none;
}
.trend-overview .section-header h2 { color: #fff; }
.trend-overview .section-header .tag { background: rgba(255,255,255,0.15); color: #ddd; }
.trend-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 14px;
  padding: 16px 20px;
}
.trend-card {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 14px 16px;
  background: #fdfdfd;
}
.trend-card .tc-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 6px;
}
.trend-card .tc-name { font-size: 15px; font-weight: 700; }
.trend-card .tc-name .emoji { margin-right: 4px; }
.trend-card .tc-price { font-size: 22px; font-weight: 700; color: var(--accent); }
.trend-card .tc-price .unit { font-size: 12px; color: var(--text-sec); font-weight: 400; }
.trend-card .tc-days {
  display: flex;
  gap: 6px;
  margin: 8px 0 4px;
  flex-wrap: wrap;
}
.day-chip {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 4px;
  background: var(--bg);
  border: 1px solid var(--border);
}
.day-chip .lbl { color: var(--text-muted); margin-right: 3px; }
.trend-card .tc-note {
  font-size: 11px;
  color: var(--text-muted);
  margin-top: 4px;
  line-height: 1.5;
}
.trend-card svg { width: 100%; height: auto; display: block; margin-top: 6px; }

/* 涨跌表 */
.wave-table { margin-top: 10px; border-top: 1px dashed var(--border); padding-top: 8px; }
.wave-table .wt-title { font-size: 11px; color: var(--text-muted); margin-bottom: 4px; }
.wave-table table { font-size: 12px; }
.wave-table th { padding: 4px 8px; }
.wave-table td { padding: 4px 8px; }

/* 行情分析盒 */
.analysis-box {
  border: 1px solid #ffd8c4;
  background: linear-gradient(135deg, #fff8f4, #fff);
  border-radius: 6px;
  padding: 14px 16px;
  margin-top: 14px;
}
.analysis-box .ab-title {
  font-size: 13px;
  font-weight: 700;
  color: var(--accent);
  margin-bottom: 8px;
}
.analysis-box .ab-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.analysis-box .ab-col h4 { font-size: 12px; color: var(--text-sec); margin-bottom: 4px; }
.analysis-box .ab-col ul { list-style: none; }
.analysis-box .ab-col li {
  font-size: 12.5px;
  padding: 2px 0 2px 14px;
  position: relative;
}
.analysis-box .ab-col li::before { content: "•"; position: absolute; left: 2px; color: var(--accent); }
@media (max-width: 640px) { .analysis-box .ab-grid { grid-template-columns: 1fr; } }

/* National Summary */
.national-summary {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}
.summary-card {
  background: linear-gradient(135deg, #fff7f3, #fff);
  border: 1px solid #ffd8c4;
  border-radius: 6px;
  padding: 14px 16px;
}
.summary-card .label { font-size: 12px; color: var(--text-muted); margin-bottom: 4px; }
.summary-card .value { font-size: 20px; font-weight: 700; color: var(--accent); }
.summary-card .unit { font-size: 12px; color: var(--text-sec); }
.summary-card .trend { font-size: 12px; margin-top: 4px; }

.trend-banner {
  background: var(--accent-light);
  border-left: 3px solid var(--accent);
  padding: 10px 14px;
  border-radius: 4px;
  margin-bottom: 14px;
  font-size: 13px;
}
.trend-banner strong { color: var(--accent); }

/* Expandable Region - 直接平铺展示（无折叠） */
.region-list { display: flex; flex-direction: column; gap: 8px; }
.region-item { border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
.region-header {
  padding: 10px 16px;
  display: flex;
  align-items: center;
  background: #fafafa;
  border-bottom: 1px solid #f0f0f0;
}
.region-header .arrow { display: none; }
.region-header .name { font-weight: 600; font-size: 14px; margin-left: 8px; flex: 1; }
.region-header .price-range { font-size: 13px; color: var(--text-sec); }
.region-header .price-range .hi { color: var(--accent); font-weight: 600; }
.region-detail { max-height: none; overflow: visible; }
.region-detail-inner { padding: 8px 16px 12px; }

.policy-item { padding: 10px 0; border-bottom: 1px solid #f0f0f0; }
.policy-item:last-child { border-bottom: none; }
.policy-item .title { font-weight: 600; font-size: 13px; margin-bottom: 2px; }
.policy-item .desc { font-size: 12px; color: var(--text-sec); }

.analysis-final { background: #f6f8fc; border-radius: 6px; padding: 14px 16px; }
.analysis-final h3 { font-size: 14px; font-weight: 600; margin-bottom: 8px; color: var(--accent); }
.analysis-final ul { list-style: none; }
.analysis-final li { padding: 4px 0; font-size: 13px; padding-left: 16px; position: relative; }
.analysis-final li::before { content: "▸"; position: absolute; left: 0; color: var(--accent); }

.badge {
  display: inline-block;
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 3px;
  margin-left: 4px;
}
.badge-opportunity { background: #f6ffed; color: var(--down); border: 1px solid #b7eb8f; }
.badge-risk { background: #fff1f0; color: var(--up); border: 1px solid #ffa39e; }

.footer { text-align: center; padding: 16px; font-size: 12px; color: var(--text-muted); }
/* ===== 移动端优化（手机查看优先） ===== */
.section-body, .section { overflow-x: clip; }
.national-summary, .trend-cards { min-width: 0; }
@media (max-width: 640px) {
  table { display: block; overflow-x: auto; -webkit-overflow-scrolling: touch; white-space: nowrap; }
  .container { padding: 12px 10px; }
  header h1 { font-size: 20px !important; }
  .subtitle { font-size: 12px !important; }
  .section-header h2 { font-size: 15px !important; }
  .section-body { padding: 12px 10px !important; }
  .national-summary { grid-template-columns: 1fr 1fr; gap: 8px; }
  .summary-card { padding: 10px 12px; }
  .summary-card .value { font-size: 16px; }
  table { font-size: 12px; }
  th, td { padding: 6px 8px !important; }
  .analysis-box .ab-col li { font-size: 12px; line-height: 1.5; }
  .trend-banner { font-size: 13px; }
}
"""


# ============ 构件函数 ============

def cell_html(v):
    if isinstance(v, dict):
        return '<td class="%s">%s</td>' % (v.get("dir", ""), v.get("t", ""))
    return "<td>%s</td>" % (v,)


def table_html(t):
    style = ""
    if t.get("style"):
        style = ' style="%s"' % t["style"]
    out = ["<table%s>" % style, "<thead>", "<tr>"]
    for h in t["headers"]:
        out.append("<th>%s</th>" % (h,))
    out.append("</tr>")
    out.append("</thead>")
    out.append("<tbody>")
    for row in t["rows"]:
        out.append("<tr>" + "".join(cell_html(v) for v in row) + "</tr>")
    out.append("</tbody>")
    out.append("</table>")
    return "\n".join(out)


def sparkline_svg(ch):
    """走势小图。ch: {y_top, y_bottom, dates, series:[{values, style, show_points, label}]}"""
    dates = ch.get("dates", [])
    vals = []
    for s in ch.get("series", []):
        for v in s["values"]:
            if isinstance(v, (int, float)):
                vals.append(float(v))
    if not vals:
        vals = [0.0, 1.0]
    vmin, vmax = min(vals), max(vals)
    if vmax == vmin:
        vmax = vmin + 1.0
    pad = (vmax - vmin) * 0.10
    lo, hi = vmin - pad, vmax + pad

    def Y(v):
        return 84.0 - (float(v) - lo) / (hi - lo) * 62.0

    out = ['<svg viewBox="0 0 300 110" xmlns="http://www.w3.org/2000/svg">']
    for yy, st in ((20, "#eee"), (48, "#f5f5f5"), (77, "#eee")):
        out.append('<line x1="30" y1="%d" x2="270" y2="%d" stroke="%s" stroke-width="1"/>' % (yy, yy, st))
    if ch.get("y_top"):
        out.append('<text x="34" y="16" font-size="9" fill="#999">%s</text>' % ch["y_top"])
    if ch.get("y_bottom"):
        out.append('<text x="34" y="75" font-size="9" fill="#999">%s</text>' % ch["y_bottom"])
    for s in ch.get("series", []):
        vs = s["values"]
        m = len(vs)
        pts = []
        for i, v in enumerate(vs):
            if isinstance(v, (int, float)):
                x = 30 + (240.0 * i / (m - 1) if m > 1 else 120.0)
                pts.append((x, Y(v)))
        if len(pts) >= 2:
            pts_str = " ".join("%.1f,%.1f" % p for p in pts)
            if s.get("style") == "dash":
                out.append('<polyline points="%s" fill="none" stroke="#389e0d" stroke-width="1.5" stroke-dasharray="4,3"/>' % pts_str)
            else:
                out.append('<polyline points="%s" fill="none" stroke="#cf1322" stroke-width="2"/>' % pts_str)
        if s.get("show_points", True):
            for i, (p, v) in enumerate(zip(pts, vs)):
                x, y = p
                last = (i == len(pts) - 1)
                if last:
                    out.append('<circle cx="%.1f" cy="%.1f" r="4" fill="#d4380d"/>' % (x, y))
                    lx = x - 30 if x - 30 > 8 else 8
                    ly = y - 8 if y > 30 else y + 14
                    out.append('<text x="%.0f" y="%.0f" font-size="9" fill="#d4380d">%s今</text>' % (lx, ly, v))
                else:
                    out.append('<circle cx="%.1f" cy="%.1f" r="3" fill="#cf1322"/>' % (x, y))
                    lx = x - 8 if x - 8 > 8 else 8
                    ly = y - 6 if y > 30 else y + 14
                    col = "#cf1322" if i == 0 else "#555"
                    out.append('<text x="%.0f" y="%.0f" font-size="8" fill="%s">%s</text>' % (lx, ly, col, v))
        if s.get("label"):
            out.append('<text x="110" y="104" font-size="8" fill="#389e0d">%s</text>' % s["label"])
    nd = len(dates)
    for i, d in enumerate(dates):
        x = 22 + (236.0 * i / (nd - 1) if nd > 1 else 0)
        out.append('<text x="%.0f" y="104" font-size="8" fill="#999">%s</text>' % (x, d))
    out.append("</svg>")
    return "\n".join(out)


def _fmt_pct(pct):
    if abs(pct) < 0.05:
        return "→0.0%", "flat"
    return ("↑%.1f%%" % pct, "up") if pct > 0 else ("↓%.1f%%" % abs(pct), "down")


def derive_trend_card(c):
    """从 chart 走势数据自动推导 chips 和 wave_table，data.json 无需提供这两项。"""
    chart = c.get("chart")
    if not chart:
        return
    series = chart.get("series") or []
    dates = chart.get("dates") or []
    if not series:
        return
    vals = series[0].get("values") or []
    # 自动算 chips：近3/7/10天涨跌
    if not c.get("chips") and len(vals) >= 2:
        last = vals[-1]
        chips = []
        for n, lbl in ((3, "3天"), (7, "7天"), (10, "10天")):
            base = vals[-1 - n] if len(vals) > n else vals[0]
            pct = (last - base) / base * 100.0 if base else 0.0
            text, dirn = _fmt_pct(pct)
            chips.append({"label": lbl, "text": text, "dir": dirn})
        c["chips"] = chips
    # 自动算 wave_table：近10日数据点
    if not c.get("wave_table") and dates and vals:
        headers = ["日期"] + [str(d) for d in dates[-10:]]
        tail_vals = vals[-10:]
        cells = []
        for i, v in enumerate(tail_vals):
            if i == len(tail_vals) - 1 and len(tail_vals) >= 2:
                prev = tail_vals[-2]
                dirn = "up" if v > prev else ("down" if v < prev else "flat")
                cells.append({"t": "%.2f" % v, "dir": dirn})
            else:
                cells.append("%.2f" % v)
        c["wave_table"] = {
            "title": c.get("wt_title", "近10日数据点（全国均价）"),
            "headers": headers,
            "rows": [[series[0].get("label", "均价")] + cells],
        }


def trend_card_html(c):
    derive_trend_card(c)
    out = ['<div class="trend-card">']
    out.append('<div class="tc-head">')
    out.append('<span class="tc-name"><span class="emoji">%s</span>%s</span>' % (c.get("emoji", ""), c["name"]))
    out.append('<span class="tc-price">%s<span class="unit">%s</span></span>' % (c["price"], c.get("unit", " 元/斤")))
    out.append('</div>')
    chips = []
    for chp in c.get("chips", []):
        chips.append('<span class="day-chip"><span class="lbl">%s</span><span class="%s">%s</span></span>'
                     % (chp["label"], chp.get("dir", "flat"), chp["text"]))
    if chips:
        out.append('<div class="tc-days">%s</div>' % "".join(chips))
    if c.get("chart"):
        out.append(sparkline_svg(c["chart"]))
    wt = c.get("wave_table")
    if wt:
        out.append('<div class="wave-table">')
        out.append('<div class="wt-title">%s</div>' % wt["title"])
        out.append(table_html(wt))
        out.append('</div>')
    if c.get("note"):
        out.append('<div class="tc-note">%s</div>' % c["note"])
    out.append('</div>')
    return "\n".join(out)


def summary_cards_html(cards):
    out = ['<div class="national-summary">']
    for c in cards:
        out.append('<div class="summary-card">')
        out.append('<div class="label">%s</div>' % c["label"])
        out.append('<div class="value">%s<span class="unit">%s</span></div>' % (c["value"], c.get("unit", "")))
        if c.get("trend"):
            out.append('<div class="trend">%s</div>' % c["trend"])
        out.append('</div>')
    out.append('</div>')
    return "\n".join(out)


def analysis_box_html(a):
    out = ['<div class="analysis-box">']
    out.append('<div class="ab-title">%s</div>' % a.get("title", "📊 行情分析 · 今日涨落与采货参考"))
    out.append('<div class="ab-grid">')
    out.append('<div class="ab-col"><h4>%s</h4><ul>%s</ul></div>'
               % (a.get("up_title", "今日涨落"), "".join("<li>%s</li>" % x for x in a.get("up", []))))
    out.append('<div class="ab-col"><h4>%s</h4><ul>%s</ul></div>'
               % (a.get("ref_title", "采货参考"), "".join("<li>%s</li>" % x for x in a.get("ref", []))))
    out.append('</div>')
    out.append('</div>')
    return "\n".join(out)


def regions_html(regions):
    out = ['<div class="region-list">']
    for r in regions:
        badge = ""
        if r.get("badge"):
            btype = r["badge"].get("type", "opportunity")
            badge = ' <span class="badge badge-%s">%s</span>' % (btype, r["badge"]["text"])
        out.append('<div class="region-item">')
        out.append('<div class="region-header">')
        out.append('<span class="name">%s%s</span>' % (r["name"], badge))
        if r.get("price_range"):
            out.append('<span class="price-range">%s</span>' % r["price_range"])
        out.append('</div>')
        out.append('<div class="region-detail"><div class="region-detail-inner">')
        out.append(table_html({"headers": r["headers"], "rows": r["rows"]}))
        out.append('</div></div>')
        out.append('</div>')
    out.append('</div>')
    return "\n".join(out)


def section_html(sec):
    out = ['<div class="section">', '<div class="section-header">']
    out.append('<span class="icon">%s</span>' % sec.get("icon", ""))
    out.append('<h2>%s</h2>' % sec["title"])
    if sec.get("tag"):
        out.append('<span class="tag">%s</span>' % sec["tag"])
    out.append('</div>')
    out.append('<div class="section-body">')
    st = sec.get("type", "standard")
    if st == "policy":
        for it in sec.get("items", []):
            out.append('<div class="policy-item"><div class="title">%s</div><div class="desc">%s</div></div>'
                       % (it["title"], it["desc"]))
    elif st == "final":
        out.append('<div class="analysis-final">')
        for b in sec.get("blocks", []):
            mt = ' style="margin-top:12px"' if b.get("mt") else ''
            out.append('<h3%s>%s</h3>' % (mt, b["h"]))
            if b.get("p"):
                out.append('<p style="font-size:14px; margin-bottom:12px">%s</p>' % b["p"])
            if b.get("ul"):
                out.append("<ul>" + "".join("<li>%s</li>" % x for x in b["ul"]) + "</ul>")
        out.append('</div>')
    else:
        if sec.get("summary"):
            out.append(summary_cards_html(sec["summary"]))
        if sec.get("banner"):
            out.append('<div class="trend-banner">%s</div>' % sec["banner"])
        for t in sec.get("tables", []):
            out.append(table_html(t))
        if sec.get("regions"):
            out.append(regions_html(sec["regions"]))
        if sec.get("analysis"):
            out.append(analysis_box_html(sec["analysis"]))
    out.append('</div>')
    out.append('</div>')
    return "\n".join(out)


def render(d):
    m = d["meta"]
    out = ['<!DOCTYPE html>', '<html lang="zh-CN">', '<head>', '<meta charset="UTF-8">',
           '<meta name="viewport" content="width=device-width, initial-scale=1.0">',
           '<title>全国禽类行情日报 | %s</title>' % m["date_iso"],
           '<style>%s</style>' % CSS, '</head>', '<body>', '<div class="container">']
    # Header
    out.append('<div class="header">')
    out.append('<h1>🐔 全国禽类行情日报</h1>')
    out.append('<div class="subtitle">%s</div>' % m.get("subtitle", ""))
    out.append('<span class="date-badge">%s %s · 自动生成</span>' % (m["date_cn"], m.get("weekday", "")))
    out.append('</div>')
    # 核心品类走势总览
    tc = d.get("trend_cards", [])
    if tc:
        out.append('<div class="trend-overview">')
        out.append('<div class="section-header"><span class="icon">📈</span>'
                   '<h2>核心品类全国均价走势（老母鸡 / 三黄鸡 / 公鸡）</h2>'
                   '<span class="tag">近3天 · 7天 · 10天</span></div>')
        out.append('<div class="trend-cards">')
        for c in tc:
            out.append(trend_card_html(c))
        out.append('</div>')
        out.append('</div>')
    # 各板块
    for sec in d.get("sections", []):
        out.append(section_html(sec))
    # Footer
    if d.get("footer"):
        out.append('<div class="footer">%s</div>' % d["footer"])
    out.append('</div>')
    out.append('''<script>
(function(){
  var cur = document.title;
  function check(){
    fetch('index.html?_t=' + Date.now(), {cache: 'no-store'})
      .then(function(r){ return r.text(); })
      .then(function(t){
        var m = t.match(/<title>([^<]*)<\\/title>/);
        if (m && m[1] !== cur) { location.reload(); }
      })
      .catch(function(){});
  }
  setInterval(check, 120000);
  document.addEventListener('visibilitychange', function(){
    if (!document.hidden) { setTimeout(check, 2000); }
  });
})();
</script>''')
    out.append('</body>')
    out.append('</html>')
    return "\n".join(out)


def main():
    base = os.path.dirname(os.path.abspath(__file__))
    data_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(base, "data.json")
    out_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(base, "index.html")
    if not os.path.exists(data_path):
        print("ERROR: 找不到数据文件 %s" % data_path)
        sys.exit(1)
    with open(data_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    html_str = render(data)
    # 先写临时文件再替换，失败不会破坏现有日报
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(html_str)
    os.replace(tmp, out_path)
    print("OK 已生成 %s（%d 字符，数据文件：%s）" % (out_path, len(html_str), data_path))


if __name__ == "__main__":
    main()
