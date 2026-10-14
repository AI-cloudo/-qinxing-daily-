# -*- coding: utf-8 -*-
"""数据源展示策略统一执行器（scrape.py 云端抓取 / build.py 页面构建 共用）

规则（2026-10-14 用户拍板）：
  1. purge_tables / purge_rows —— 「源站已停更、沉没、长期沿用旧值」的源：
     **不采用、不展示、删除**。在写 data.json 之前真删（表级或行级），
     页面快照与前端实时拉取都不会再出现。
  2. hide_tables —— 仍在更新但节奏间断的源：只从页面快照隐藏，data.json 保留供周研判。
  3. 策略文件 src_policy.json 由「每周一 8 点 AI 精修」自动化判定维护；
     读不到策略文件时不做任何清理（避免误删）。
"""
import json
import os


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
