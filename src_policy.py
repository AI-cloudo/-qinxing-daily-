# -*- coding: utf-8 -*-
"""数据源展示策略统一执行器（scrape.py 云端抓取 / build.py 页面构建 共用）

规则（2026-10-14 用户拍板）：
  1. purge_tables / purge_rows —— 「源站已停更、沉没、长期沿用旧值」的源：
     **不采用、不展示、删除**。在写 data.json 之前真删（表级或行级），
     页面快照与前端实时拉取都不会再出现。
  2. hide_tables —— 仍在更新但节奏间断的源：只从页面快照隐藏，data.json 保留供周研判。
  3. src_policy 的 apply_staleness() —— 「停更分级」（用户 2026-10-14 立规）：
     行内标注「停更」→ 清退；数据日期 >10 天 → 清退；7~10 天 → 不展示待补源；
     2~7 天 → 保留但由角标标滞后。有沉源的板块写进 needs_replacement。
  4. 策略文件 src_policy.json 由「每周一 8 点 AI 精修」自动化判定维护；
     读不到策略文件时不做任何清理（避免误删）。但 apply_staleness 不依赖策略文件，
     始终按阈值执行（阈值写死在本文件，防止 AI 误改）。
"""
import datetime
import json
import os
import re

# ---- 停更分级阈值（用户 2026-10-14 立规，硬性）----
#   行内已标注「停更」        → 一律清退
#   数据日期距今 > 10 天      → 清退、删除、不展示
#   数据日期距今 7~10 天      → 不展示（本轮移除，等补日更源）
#   数据日期距今 2~7 天       → 保留，由板块/表角标标「滞后」
#   找不到显式日期            → 不动（不能凭猜删数据）
STALE_PURGE_DAYS = 10
STALE_HIDE_DAYS = 7
STALE_TAG_DAYS = 2

# 只在「显式日期语境」里取日期，避免把 "3.15-3.25" 这类价格区间误读成日期
_DATE_CTX = [
    re.compile(r"20\d{2}\s*[-/年]\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})"),
    re.compile(r"数据(?:日期)?\s*[:：]?\s*(\d{1,2})\s*[/\-]\s*(\d{1,2})"),
    re.compile(r"(\d{1,2})\s*[/\-]\s*(\d{1,2})\s*(?:数据|更新)"),
    re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日"),
    re.compile(r"(?:宝典|网|中心|日报|汇总|集团|公司|钢厂|部|源|价)\s*[:：]?\s*"
               r"(\d{1,2})\s*[/\-]\s*(\d{1,2})"),
]
_STOP_MARK = re.compile(r"源站已停更|源已停更|已停更|已停止更新|源站停更|停更源")


def _newest_date(text, today):
    """文本里最新的一个显式日期 → datetime.date；解析不到返回 None"""
    cands = []
    for pat in _DATE_CTX:
        for m in pat.finditer(str(text or "")):
            mo, dy = int(m.group(1)), int(m.group(2))
            if not (1 <= mo <= 12 and 1 <= dy <= 31):
                continue
            try:
                cands.append(datetime.date(today.year, mo, dy))
            except ValueError:
                continue
    if not cands:
        return None
    d0 = max(cands)
    if (d0 - today).days > 90:          # 明显是上一年（跨年场景）
        try:
            d0 = d0.replace(year=d0.year - 1)
        except ValueError:
            pass
    return d0


def apply_staleness(data, today=None, log=print):
    """按用户 2026-10-14 立规执行「停更分级」，原地修改 data。

    返回 {"purged": 行数, "hidden": 行数, "boards": set(板块名)}
    → boards 是「有数据沉了、需要补日更源」的板块，供写入 needs_replacement。
    """
    today = today or datetime.date.today()
    stat = {"purged": 0, "hidden": 0, "boards": set()}
    for s in (data.get("sections") or []):
        tab = str(s.get("tab") or s.get("title") or "")
        keep_tables = []
        for t in (s.get("tables") or []):
            cap = str(t.get("cap") or "")
            # 表级：cap 已明示源站停更 → 整表清退（不含「停更源已清退」这类结果描述）
            if _STOP_MARK.search(cap) and "已清退" not in cap:
                stat["purged"] += len(t.get("rows") or [])
                stat["boards"].add(tab)
                log("  停更分级[%s]: 整表清退（cap 标注停更）" % tab)
                continue
            keep_rows, hidden_rows = [], 0
            for r in (t.get("rows") or []):
                txt = _row_text(r)
                if _STOP_MARK.search(txt) and "已清退" not in txt:
                    stat["purged"] += 1
                    stat["boards"].add(tab)
                    continue
                d0 = _newest_date(txt, today)
                if d0 is not None:
                    age = (today - d0).days
                    if age > STALE_PURGE_DAYS:
                        stat["purged"] += 1
                        stat["boards"].add(tab)
                        log("  停更分级[%s]: 清退 1 行（数据 %d/%d，滞后 %d 天，超 %d 天）"
                            % (tab, d0.month, d0.day, age, STALE_PURGE_DAYS))
                        continue
                    if age > STALE_HIDE_DAYS:
                        stat["hidden"] += 1
                        stat["boards"].add(tab)
                        log("  停更分级[%s]: 暂不展示 1 行（数据 %d/%d，滞后 %d 天，待补源）"
                            % (tab, d0.month, d0.day, age))
                        continue
                keep_rows.append(r)
            if keep_rows or not (t.get("rows") or []):
                t = dict(t)
                t["rows"] = keep_rows
                keep_tables.append(t)
            else:
                log("  停更分级[%s]: 整表空 → 一并清退" % tab)
        s["tables"] = keep_tables
    return stat


def load_policy(base_dir):
    try:
        with open(os.path.join(base_dir, "src_policy.json"), encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _sig(t):
    """表的匹配签名：cap + 表头（任一命中关键词即视为该源的表）"""
    return " ".join([str(t.get("cap") or "")] + [str(x) for x in (t.get("headers") or [])])


def _row_text(r):
    return " ".join(str(x) for x in (r if isinstance(r, (list, tuple)) else [r]))


_SHORT = ['淘汰鸡', '鸡蛋', '三黄鸡', '公鸡', '白羽', '研判']


def _sections(data):
    """板块索引：优先用 section 自带 tab，其次按固定顺序短名（scrape.py 写出的
    data.json 没有 tab 字段，只有 build.py 会补，所以必须留顺序兜底）"""
    out = {}
    for i, s in enumerate(data.get('sections') or []):
        if s.get('tab'):
            out[str(s['tab'])] = s
        if i < len(_SHORT):
            out.setdefault(_SHORT[i], s)
        if s.get('title'):
            out.setdefault(str(s['title']), s)
    return out


def apply_policy(data, policy, log=print):
    """按策略清理 data（原地修改）。返回命中/删除的源关键词集合，供角标剔除复用。"""
    touched = set()
    if not policy:
        return touched
    secs = _sections(data)

    # ---- 1) 停更源：整表删除 ----
    for rule in (policy.get("purge_tables") or []):
        words = [str(w) for w in (rule.get("match") or []) if w]
        s = secs.get(str(rule.get("sec") or ""))
        if not s or not words:
            continue
        keep, dropped = [], []
        for t in (s.get("tables") or []):
            hit = [w for w in words if w in _sig(t)]
            if hit:
                dropped.append(t)
                touched.update(hit)
            else:
                keep.append(t)
        if dropped:
            s["tables"] = keep
            log("  停更源删除[%s]: %d 张表（%s）" % (rule.get("sec"), len(dropped), "、".join(words)))

    # ---- 2) 停更源：行级删除（整行任一单元格命中）----
    for rule in (policy.get("purge_rows") or []):
        words = [str(w) for w in (rule.get("match") or []) if w]
        s = secs.get(str(rule.get("sec") or ""))
        if not s or not words:
            continue
        total = 0
        kept_tables = []
        for t in (s.get("tables") or []):
            keep_rows, dropped_rows = [], []
            for r in (t.get("rows") or []):
                hit = [w for w in words if w in _row_text(r)]
                if hit:
                    dropped_rows.append(r)
                    touched.update(hit)
                else:
                    keep_rows.append(r)
            if dropped_rows:
                total += len(dropped_rows)
                t = dict(t)
                t["rows"] = keep_rows
            if (t.get("rows") or []) or not dropped_rows:
                kept_tables.append(t)
        if total:
            s["tables"] = kept_tables
            log("  停更源删除[%s]: %d 行（%s）" % (rule.get("sec"), total, "、".join(words)))

    # ---- 3) 间断源：仅页面不展示（表级）----
    for rule in (policy.get("hide_tables") or []):
        words = [str(w) for w in (rule.get("match") or []) if w]
        s = secs.get(str(rule.get("sec") or ""))
        if not s or not words:
            continue
        keep, dropped = [], []
        for t in (s.get("tables") or []):
            hit = [w for w in words if w in _sig(t)]
            if hit:
                dropped.append(t)
                touched.update(hit)
            else:
                keep.append(t)
        if dropped:
            s["tables"] = keep
            log("  间断源隐藏[%s]: %d 张表（%s）" % (rule.get("sec"), len(dropped), "、".join(words)))

    return touched


def scrub_text(data, words, log=print):
    """把停更/隐藏源的名字从「会渲染的文字」里清掉：速览卡 note、页脚 footer。"""
    words = [w for w in (words or []) if w]
    if not words:
        return
    import re
    for tc in (data.get("trend_cards") or []):
        note = str(tc.get("note") or "")
        for w in words:
            note = re.sub(r"\s*[·+]\s*%s[^·]*" % re.escape(w), "", note)
        tc["note"] = note
    ft = str(data.get("footer") or "")
    if ft:
        for w in words:
            ft = re.sub(r"\s*/\s*%s(?:\.com\.cn|\.com|\.cn)?" % re.escape(w), "", ft)
        data["footer"] = ft
