#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
口语化文案 —— 晨报里所有给销售看的话，都从这里出。

写作规矩（采购经理 2026-11-06 提出，11-07 三次追加）：

  1. 只讲上游行情，不掺采购决策（不提锁价比例、不提落单、不提补货）
  2. 大白话、接地气、口语化 —— 销售是发给客户看的，不是给研究员看的
  3. 禁用的词：背离 / 观察线 / 天花板 / 中枢 / 传导 / 惜售 / 延淘 /
     锚 / 系数 / 基差 / 口径 / 成本下移 / 供需博弈 / 高举高打 /
     链上赋能 / 蓄势破防 / 抬头翘尾 / 挺价 ……
  4. 要有条理：按价格关系排（蛋价 → 老母鸡 → 817 → 公鸡），
     每个品类拆成几个因素，一条一个原因，不混着说
  5. **不要照搬源站**（11-07 最新反馈）：
     "我需要是分析，尽量避免照搬，比如'供需博弈，举高打高'、
      '链上赋能，蓄势破防'这虽然专业，但是让人看不懂。"
     → 源站的四字标题一律不抄。要么翻成人话，要么干脆不提。
     → 结论必须从自己的数字里算出来：连涨了几天、跌的是哪几个省、
       苗价和毛鸡有没有打架、山东还扛不扛得住。
       这才是分析，抄观点不是。
     → 「事实」和「我的判断」分开写。判断必须说明依据，
       不能让人以为是我的原话就是结论。

版式约定：
  每个品类返回 {"head": 一句话结论, "factors": [(因素名, 说明), ...]}
  由 build.py 渲染成"标题 + 分条因素"。

改措辞只改这个文件，别去 build.py 里改。
"""
import re


# ══════════════════════════════════════════════════════════════════
#  基础小工具
# ══════════════════════════════════════════════════════════════════
def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _yuan(v):
    """把 4.67 说成「4块6毛7」，销售听着一秒就懂。

    注意别写成"4块67分"——那不是中国话。
    """
    if v is None:
        return ""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    whole = int(v)
    frac = int(round((v - whole) * 100))
    if frac == 100:          # 3.996 这类四舍五入进位
        whole, frac = whole + 1, 0
    if frac == 0:
        return "%d块钱" % whole
    jiao, fen = frac // 10, frac % 10
    if fen == 0:
        return "%d块%d毛" % (whole, jiao)
    if jiao == 0:
        return "%d块零%d分" % (whole, fen)
    return "%d块%d毛%d" % (whole, jiao, fen)


def _fen(v):
    """涨跌的几个钱：0.10 → 「1毛」，0.05 → 「5分」，0.12 → 「1毛2」。"""
    try:
        n = int(round(abs(float(v)) * 100))
    except (TypeError, ValueError):
        return ""
    if n == 0:
        return "没动"
    if n < 10:
        return "%d分" % n
    if n % 10 == 0:
        return "%d毛" % (n // 10)
    return "%d毛%d" % (n // 10, n % 10)


def _dir_word(dir_):
    return {"up": "涨了", "down": "跌了", "flat": "没动"}.get(dir_, "没动")


def _arrow(dir_):
    return {"up": "↑", "down": "↓", "flat": "→"}.get(dir_, "→")


# ══════════════════════════════════════════════════════════════════
#  价格出处 —— 铁律：每个价格都要带数据日期和来源
# ══════════════════════════════════════════════════════════════════
# 源站发稿时间不一样（博亚和讯淘鸡 08:20、鸡蛋 09:20 出稿），
# 早班跑的晨报拿到的常常还是上一期的价。以前正文直接写「今天」，
# 销售会把前天的价当今天的报给客户 —— 所以逐个品类标出日期和来源。
#
# 口径按采购经理定的写死在这里；日期由 build.py 取好后传进来。
PRICE_META = {
    "老母鸡": ("全国19省平均", "博亚和讯"),
    "三黄鸡": ("817肉杂棚前价", "鸡病专业网"),
    "公鸡": ("六个产区平均", "鸡病专业网"),
    "鸡蛋": ("20个主产省均价", "鸡病专业网"),
}


def _asof_of(ctx, kind):
    """这个品类最新一期报价是几号的。"""
    return ((ctx or {}).get("asof") or {}).get(kind, "") or ""


def _is_today(ctx, kind):
    """这期报价是不是本简报当天出的。

    判据用「本简报出的那天」（ctx["issue"]），不是「板块最新日期」。
    因为 board_date 是各板块里最新的那个日期 —— 白羽等板块更新到 11/11，
    并不意味着老母鸡的价也是 11/11 的。
    """
    asof = _asof_of(ctx, kind)
    ref = (ctx or {}).get("issue") or (ctx or {}).get("board") or ""
    if not asof or not ref:
        return True
    return asof == ref


def _pfx(ctx, kind):
    """句子里的时间词：是当天的说「今天」，不是当天的就不说今天。

    不是当天的，日期由 _dmark 挂在价格后面，不在句子开头重复一遍。
    """
    return "今天" if _is_today(ctx, kind) else ""


def _dmark(ctx, kind):
    """价格后面的日期小尾巴；是当天的就不重复标。"""
    if _is_today(ctx, kind):
        return ""
    return "（%s）" % _asof_of(ctx, kind)


def _cal(ctx, kind, caliber=None):
    """口径括号；数据不是当天的，把日期一并写进去。"""
    cal, src = PRICE_META.get(kind, ("", ""))
    caliber = caliber if caliber is not None else cal
    bits = []
    if caliber:
        bits.append(caliber)
    if not _is_today(ctx, kind) and _asof_of(ctx, kind):
        bits.append(_asof_of(ctx, kind))
    return "（%s）" % "，".join(bits) if bits else ""


def _stamp(ctx, kind, caliber=None):
    """价格出处：口径 + 数据日期 + 来源。日期和来源一个都不少。"""
    cal, src = PRICE_META.get(kind, ("", ""))
    caliber = caliber if caliber is not None else cal
    asof = _asof_of(ctx, kind)
    bits = []
    if caliber:
        bits.append(caliber)
    if asof:
        bits.append("%s %s" % (asof, src) if src else asof)
    elif src:
        bits.append(src)
    return "（%s）" % "，".join(bits) if bits else ""


def _streak(series):
    """连涨/连跌几天、累计多少钱。

    这是「分析」和「照搬」的分界线之一：源站只说"今天涨"，
    但连涨第 3 天和第 1 天，对销售的意义完全不同。
    返回 (方向, 天数, 累计变化, 起涨/起跌的价) 或 None。
    """
    vs = [x for x in (_num(v) for v in (series or [])) if x is not None]
    if len(vs) < 2:
        return None
    d, n = None, 0
    for i in range(len(vs) - 1, 0, -1):
        step = vs[i] - vs[i - 1]
        cur = "up" if step > 0.002 else ("down" if step < -0.002 else "flat")
        if cur == "flat":
            if d is None:
                return None
            break
        if d is None:
            d, n = cur, 1
        elif cur == d:
            n += 1
        else:
            break
    if d is None or n == 0:
        return None
    return d, n, round(vs[-1] - vs[-1 - n], 3), vs[-1 - n]


def _md_tuple(s):
    """'11/18' → (11, 18)；解析不了返回 None。"""
    m = re.match(r"^\s*(\d{1,2})\s*/\s*(\d{1,2})\s*$", str(s or ""))
    return (int(m.group(1)), int(m.group(2))) if m else None


def _chick_label(chick_egg, ctx):
    """鸡苗这段的时间词 —— 必须跟稿子的日期对上。

    源站的《X月X日鸡苗行情分析》讲的是**第二天**的苗价：
    11/17 早上抓到的稿子，标题是「11月18日鸡苗行情分析」，讲的是 11/18 的苗。
    原来不管哪天的稿子一律写「今天」，销售念出来就跟客户说的日期对不上
    （11-17 修）。这里按稿子日期和本简报日期的关系说清楚。
    """
    ds = str((chick_egg or {}).get("date") or "")
    issue = str((ctx or {}).get("issue") or "")
    d1, d2 = _md_tuple(ds), _md_tuple(issue)
    if not ds:
        return "今天", ""
    if not d1 or not d2:
        return "今天", "（%s）" % ds
    if d1 == d2:
        return "今天", ""
    if d1 > d2:
        return "明天（%s）" % ds, ""
    return "%s 的" % ds, ""


def _split_chg(rows):
    """把逐地报价按涨/平/跌分开。"""
    downs, ups, flats = [], [], []
    for r in rows or []:
        c = str(r.get("chg") or "")
        if c.startswith("-"):
            downs.append(r)
        elif c.startswith("+"):
            ups.append(r)
        elif c == "→" or not c:
            flats.append(r)
    return downs, ups, flats


def _chg_amt_of(row):
    """从一条逐地报价里取涨跌金额（「+0.05」→ 0.05），取不到算 0。"""
    return _num(re.sub(r"[^\d.]", "", str((row or {}).get("chg") or ""))) or 0.0


def _bias(ups, downs, flats):
    """方向判定：个数要占优（≥4成），金额还不能落后。

    采购经理 2026-11-17 点名的硬伤：24 个产区 2 个涨、22 个平，
    被总结成「上涨信号」。这是错的 —— 一两个地方动，不叫方向。
    只有涨的地方占多数、跌的地方明显少，才配叫「涨」；反之叫「跌」；
    其余一律叫「基本持平」。
    返回 "up" / "down" / "flat"。
    """
    n = len(ups or []) + len(downs or []) + len(flats or [])
    if not n:
        return "flat"
    up_n, down_n = len(ups or []), len(downs or [])
    up_a = sum(_chg_amt_of(r) for r in (ups or []))
    down_a = sum(_chg_amt_of(r) for r in (downs or []))
    if up_n > down_n and up_n >= n * 0.4 and up_a >= down_a:
        return "up"
    if down_n > up_n and down_n >= n * 0.4 and down_a >= up_a:
        return "down"
    return "flat"


def _counts_txt(ups, downs, flats):
    """「2个涨、22个平」—— 一个不落，0 的不说。"""
    bits = []
    if ups:
        bits.append("%d个涨" % len(ups))
    if downs:
        bits.append("%d个跌" % len(downs))
    if flats:
        bits.append("%d个平" % len(flats))
    return "、".join(bits) if bits else "全是持平"


def _mover_sentence(movers, verb="跌"):
    """「广东跌4分、江苏跌1毛」—— 涨跌要把金额说清楚，光说家数没用。"""
    segs = []
    for m in (movers or [])[:3]:
        name, d, a = m[0], m[1], m[2]
        segs.append("%s%s%s" % (name, verb if d == "down" else "涨", _fen(a)))
    return "、".join(segs)


def _egg_counts(up_n, down_n, flat_n, mv_d, mv_u):
    """鸡蛋逐省计数：「1个在跌（广东跌4分）、1个在涨（河南涨1分）、18个持平」。"""
    det = []
    if down_n:
        det.append("%d个在跌%s" % (down_n, "（%s）" % mv_d if mv_d else ""))
    if up_n:
        det.append("%d个在涨%s" % (up_n, "（%s）" % mv_u if mv_u else ""))
    if flat_n:
        det.append("%d个持平" % flat_n)
    return "、".join(det) if det else "持平为主"


def _by_amount(rows):
    """按涨跌金额归堆：[ (0.1, [地名...]), (0.05, [地名...]) ]，跌得多的在前。"""
    buckets = {}
    for r in rows:
        amt = _num(re.sub(r"[^\d.]", "", str(r.get("chg") or ""))) or 0.0
        buckets.setdefault(round(amt, 3), []).append(str(r.get("place") or ""))
    return sorted(buckets.items(), key=lambda kv: -kv[0])


def _places_sentence(rows, verb):
    """「江苏南通、河南开封、浙江金华各跌1毛」这样的句子。

    一个地方不能写"各"，那是两个以上才用的。
    """
    if not rows:
        return ""
    segs = []
    for amt, places in _by_amount(rows)[:3]:
        if len(places) == 1:
            segs.append("%s%s%s" % (places[0], verb, _fen(amt)))
        elif len(places) <= 3:
            segs.append("%s各%s%s" % ("、".join(places), verb, _fen(amt)))
        else:
            segs.append("%s等%d个各%s%s" % ("、".join(places[:3]), len(places),
                                          verb, _fen(amt)))
    return "，".join(segs)


def _prov_count(places):
    """按省归堆，全国小规格这种不算省。"""
    cnt, order = {}, []
    for p in places:
        if p.startswith("全国"):
            continue
        k = p[:2]
        if k not in cnt:
            cnt[k] = 0
            order.append(k)
        cnt[k] += 1
    order.sort(key=lambda k: -cnt[k])
    return [(k, cnt[k]) for k in order]


# ══════════════════════════════════════════════════════════════════
#  源站黑话 → 人话
#  只用于"翻译"，绝不把原话贴到报告里。翻不出来的就不提。
# ══════════════════════════════════════════════════════════════════
JARGON = [
    # 长词在前：先整句替掉，避免"苗企抱团对标"被拆成"苗厂+抱团对标"重复一遍
    ("苗企抱团对标", "苗厂一起把价咬住，谁都不先松"),
    ("慎持收购合同", "收鸡收得小心，不太敢签大单"),
    ("外运拉动抬头翘尾", "外地来拉货，把尾市价格往上顶了一下"),
    ("供需博弈", "买家和卖家在僵着，谁也不肯先让"),
    ("高举高打", "报价一路往上抬，不太留还价的空"),
    ("蓄势破防", "憋着劲想涨，在等一个能突破的口子"),
    ("链上赋能", ""),                       # 纯套话，没有信息，丢掉
    ("抬头翘尾", "到尾市价格往上翘了一点"),
    ("循规蹈矩", "按老路子走，没出新情况"),
    ("上下求索", "往上往下都在试，方向还没定"),
    ("抱团对标", "一起把价咬住，谁都不先松"),
    ("虚实交集", "有真下单的，也有光问价不买的"),
    ("久久比价", "来回比价，谈得比较久"),
    ("高低相推", "高的往下让一点，低的往上抬一点"),
    ("外运拉动", "外地来拉货"),
    ("一枝独秀", "就它一个在涨"),
    ("跌势放缓", "跌得没前几天那么狠了"),
    ("冲高回落", "先涨上去又掉下来"),
    ("止跌企稳", "不跌了，稳住了"),
    ("量价齐升", "卖得多，价也涨"),
    ("有价无市", "报着价但没人真买"),
    ("交投清淡", "买卖都少"),
    ("弱势稳定", "卖得不算好，但价没掉"),
    ("稳中偏弱", "看着稳，其实在松"),
    ("稳中偏强", "看着稳，其实在抬"),
    ("探底", "快跌到底了"),
    ("挺价", "咬住不降"),
    ("慎持", "收得小心，不敢多收"),
    ("压栏", "该卖的不卖，压着等涨价"),
    ("惜售", "舍不得卖"),
    ("走货一般", "卖得一般"),
    ("走货", "卖得好不好"),
    ("屠企", "屠宰场"),
    ("苗企", "苗厂"),
    ("棚前价", "鸡棚门口收鸡的价"),
    ("到户价", "送到养殖户手上的价"),
]
_JARGON_SORTED = sorted(JARGON, key=lambda kv: -len(kv[0]))


def decode_jargon(text):
    """把源站的一句话翻成大白话。翻不动就返回空串 —— 宁可不提，也不抄。

    两个坑：
      1. 原句标点稀疏（"苗企抱团对标虚实交集"中间没逗号），
         逐句翻完会粘成一句。所以每个译文后面垫一个哨兵，翻完再换逗号。
      2. 长词必须先匹配，否则"抱团对标"会把"苗企抱团对标"拆开重复一遍。
    """
    t = str(text or "").strip()
    if not t:
        return ""
    MARK = "\u0001"
    hit = False
    out = []
    for cl in re.split(r"[，、；,;]", t):
        cl = cl.strip()
        if not cl:
            continue
        for word, plain in _JARGON_SORTED:
            if word in cl:
                cl = cl.replace(word, plain + MARK)
                hit = True
        cl = cl.strip().strip(MARK).strip()
        if cl:
            out.append(cl)
    if not hit:
        return ""
    s = "，".join(out).replace(MARK, "，")
    s = re.sub(r"，{2,}", "，", s).strip("，")
    return s


# ══════════════════════════════════════════════════════════════════
#  鸡蛋 —— 整轮行情的发动机，放在最前面讲
# ══════════════════════════════════════════════════════════════════
def egg_factors(egg_avg, egg_dir, stat=None, ctx=None):
    """鸡蛋：为什么涨、为什么跌。

    stat 给的是这一期逐省涨跌家数，用来说"几个地方在涨"，
    比笼统一句"鸡蛋在涨"有说服力。
    """
    if egg_avg is None:
        return None
    e = _yuan(egg_avg)
    st = stat or {}
    up_n, down_n, flat_n = st.get("up_n"), st.get("down_n"), st.get("flat_n")
    total = st.get("total")
    f = []
    pfx = _pfx(ctx, "鸡蛋")
    dm = _dmark(ctx, "鸡蛋")

    # ① 现在什么情况 —— 只说整体，个别地方动叫区域性动作，不代表全部
    #（11-17 用户：「就算涨幅过大，只能说区域性上涨，我需要的是整体的情况」。
    #  方向判定在 build.py：涨/跌的省要占 4 成以上才算整体方向，否则整体=没动。）
    mv_d = _mover_sentence(st.get("movers_down"), "跌")
    mv_u = _mover_sentence(st.get("movers_up"), "涨")
    mv = "、".join(x for x in (mv_d, mv_u) if x)
    n_moved = (up_n or 0) + (down_n or 0)
    if egg_dir == "up":
        seg = "%s鸡蛋整体在涨，全国平均%s%s" % (pfx, e, dm)
        if total:
            seg += "——%d个主产省里%s，涨的省快一半，这是整体往上" % (
                total, _egg_counts(up_n, down_n, flat_n, mv_d, mv_u))
        f.append(("现在什么情况", seg + "。"))
    elif egg_dir == "down":
        seg = "%s鸡蛋整体在跌，全国平均%s%s" % (pfx, e, dm)
        if total:
            seg += "——%d个主产省里%s，跌的省快一半，这是整体往下" % (
                total, _egg_counts(up_n, down_n, flat_n, mv_d, mv_u))
        f.append(("现在什么情况", seg + "。"))
    else:
        seg = "%s鸡蛋整体没动，全国平均%s%s" % (pfx, e, dm)
        if total:
            if n_moved == 0:
                seg += "，%d个主产省里%d个持平，一个动的都没有" % (total, flat_n or total)
            elif mv:
                seg += ("，%d个主产省里%d个持平，就%s——这是个别地方的区域性动作，"
                        "不代表整体" % (total, flat_n or 0, mv))
            else:
                seg += "，%d个主产省里%d个持平" % (total, flat_n or 0)
        f.append(("现在什么情况", seg + "。"))

    # ② 为什么
    if egg_dir == "up":
        f.append(("为什么涨", "两件事凑一块。一是鸡少了——前阵子蛋价低，"
                              "养鸡的把老母鸡处理掉一批，现在下蛋的鸡不够数；"
                              "二是天冷了，鸡还是那些鸡，下蛋量会自己往下掉一点。"))
        f.append(("会怎样", "养鸡的这时候更不愿意卖老母鸡了——"
                                 "留着下蛋比卖了划算。老母鸡的货就紧，"
                                 "这是后面最要紧的一件事。"))
    elif egg_dir == "down":
        f.append(("为什么跌", "前阵子蛋价高，养鸡的补了一批小鸡，"
                              "现在这批鸡陆续开产，蛋多起来了；"
                              "天还没大冷，蛋也没掉量。两头一凑，蛋就多了。"))
        f.append(("会怎样", "蛋价一软，养鸡的就想赶紧张罗卖老母鸡换钱，"
                                 "鸡一下子多起来，收鸡的顺势往下压。"))
    else:
        f.append(("为什么没动", "该处理的鸡上一轮已经处理得差不多了，"
                                "新补的小鸡还没到开产的时候。"
                                "蛋量不多也不少，买蛋的也没多要，就横在这。"))
        f.append(("我的判断", "这种横着一般横不了太久。天再冷一点，"
                              "鸡下蛋会自己少下来，蛋价容易先动。"
                              "这是按往年这个节气的规律推的，不是现在就有的事。"))

    if egg_dir == "up":
        head = "鸡蛋涨到%s%s—— 这一串里它最先动，后面的鸡价跟着它走。" % (
            e, _stamp(ctx, "鸡蛋"))
    elif egg_dir == "down":
        head = "鸡蛋跌到%s%s—— 它一软，老母鸡跟着松。" % (
            e, _stamp(ctx, "鸡蛋"))
    else:
        head = "鸡蛋%s%s，横着没动—— 但它是这一串里最先动的，得盯住。" % (
            e, _stamp(ctx, "鸡蛋"))
    return {"head": head, "factors": f}


# ══════════════════════════════════════════════════════════════════
#  老母鸡 —— 我们冻品最直接的成本
# ══════════════════════════════════════════════════════════════════
def oldhen_factors(price, dir_, egg_avg, egg_dir, flat_days=None, prov=None, ctx=None):
    """老母鸡：为什么涨、为什么跌、后面怎么看。

    prov：分省明细 {"lo","hi","total","up","down","movers"} —— 有数就报区间
    和涨跌家数，别只报一个平均价（11-17 用户：涨落的数据是多少？）。
    横盘天数越长，解释越要收着说，不能每天重复同一段「两头都在撑」。
    """
    if price is None:
        return None
    p = _yuan(price)
    f = []
    pfx = _pfx(ctx, "老母鸡")
    dm = _dmark(ctx, "老母鸡")
    pv = prov or {}
    fd = flat_days or 0

    # ① 现在什么情况
    if dir_ == "flat":
        # 口径要说清楚：4.53 是「全国19省的平均价」，不是每个省都这个数。
        # 有分省数就把区间亮出来：从 4块1 到 4块9，销售报区间不会报错。
        seg = "%s老母鸡没动，还是%s%s" % (pfx, p, dm)
        if pv.get("total"):
            rng = ""
            if pv.get("lo") and pv.get("hi"):
                rng = "，各省%s到%s" % (_yuan(pv["lo"]), _yuan(pv["hi"]))
            movers = pv.get("up", 0) + pv.get("down", 0)
            mv_txt = _mover_sentence(pv.get("movers"), "跌")
            if movers:
                cnt_txt = ("%d个涨、%d个跌" % (pv.get("up", 0), pv.get("down", 0))
                           if pv.get("up") and pv.get("down")
                           else "%d个%s" % (movers, "涨" if pv.get("up") else "跌"))
                seg += "，%d个省里%s%s%s" % (
                    pv["total"], cnt_txt,
                    "（%s）" % mv_txt if mv_txt else "", rng)
            else:
                seg += "，%d个省全是持平%s" % (pv["total"], rng)
        if fd >= 3:
            seg += "，连着%d个报价日没动" % fd
        f.append(("现在什么情况", seg + "。"))
    else:
        f.append(("现在什么情况", "%s老母鸡%s，%s%s。"
                                 % (pfx, _dir_word(dir_), p, dm)))

    # ② 为什么 —— 横盘第几天说什么话，按天数分档，不每天复读同一段
    if dir_ == "flat":
        if fd >= 10:
            f.append(("为什么没动", "横了%d个报价日了。到这个份上就别找新原因了——"
                                    "价钉得死，说明买卖两边都认这个数。"
                                    "要动，得等蛋价先掉一截。" % fd))
        elif fd >= 6:
            f.append(("为什么没动", "连着%d个报价日了，原因还是前几天那些："
                                    "养鸡的不急着卖，收鸡的也不敢多收，两边都不让。" % fd))
        else:
            f.append(("为什么没动", "两头都在撑。养鸡的这边，前阵子蛋价低的时候"
                                    "已经集中卖过一批，手里剩的鸡不多，不着急出；"
                                    "收鸡的这边，冻品卖得一般，不敢多收。"
                                    "谁也没占到便宜，价就钉在这。"))
    elif dir_ == "up":
        f.append(("为什么涨", "蛋价先涨起来了，养鸡的改主意——"
                              "留着下蛋比卖鸡划算，就不出栏了。"
                              "市面上鸡一少，收鸡的就得加钱。"))
    else:
        f.append(("为什么跌", "蛋价软下来，养鸡的赶紧张罗卖鸡换现金，"
                              "鸡一下子多起来，收鸡的顺势压价。"))

    # ③ 我的判断（明确标成判断，并给出依据）
    if egg_avg is not None:
        if egg_dir == "up" and dir_ == "flat":
            f.append(("我的判断", "蛋价在涨、老母鸡还没动，这个先后差是常有的——"
                                  "养鸡的先看几天蛋价稳不稳，才决定留不留鸡。"
                                  "蛋价要是再往上走两三天，这边大概率会跟着紧。"))
        elif egg_dir == "down" and dir_ == "up":
            f.append(("我的判断", "蛋价在跌、老母鸡却在涨，这两头对不上。"
                                  "这种涨法一般撑不了几天，别照着涨价往后推。"))
        elif egg_dir == "up" and dir_ == "up":
            f.append(("我的判断", "蛋和鸡一起涨，这是一条线上的事，"
                                  "涨得有道理，不是虚的。"))
        elif egg_dir == "down" and dir_ == "down":
            f.append(("我的判断", "蛋和鸡一起跌，也是一条线上的事。"
                                  "跌到养鸡的不愿意卖了，就会停。"))
        elif egg_dir == "down" and dir_ == "flat":
            # 11-17 补：原来这种情况会掉进下面的 else，说成「蛋价也横着」——
            # 鸡蛋那段明明写着在跌，两处自相矛盾，销售念出来自己都犯迷糊。
            f.append(("我的判断", "蛋价在跌、老母鸡还没动，这两头差着一步。"
                                  "蛋价先软下来，养鸡的还没决定卖不卖鸡，"
                                  "所以鸡价暂时横着。蛋价要是一直这样软下去，"
                                  "老母鸡跟着松是迟早的事。"))
        else:
            f.append(("我的判断", "蛋价也横着，两边都在等——"
                                  "养鸡的等蛋价再抬，收鸡的等鸡价再松。"
                                  "这种僵持最容易冷不丁出方向，盯蛋价就行。"))

    # ④ 跟我们什么关系
    f.append(("跟我们", "老母鸡是我们冻品最直接的成本。"
                              "它涨，我们卖货的价就得往上走；它不动，成本就稳着。"))

    # ⑤ 后面怎么看
    if egg_dir == "up" and dir_ == "flat":
        f.append(("后面怎么看", "盯蛋价就够。蛋价还涨，老母鸡跟涨是迟早的事。"))
    elif egg_dir == "down" and dir_ == "up":
        f.append(("后面怎么看", "这波涨别当成趋势，报价上留点余地。"))
    elif egg_dir == "up" and dir_ == "up":
        f.append(("后面怎么看", "蛋和鸡一起往上，短期跌不下来，报价按新的走。"))
    elif egg_dir == "down" and dir_ == "down":
        f.append(("后面怎么看", "两头一起跌，等跌到养鸡的不愿意卖了才会停，别抢着接。"))
    elif egg_dir == "down" and dir_ == "flat":
        if fd >= 6:
            f.append(("后面怎么看", "还是那句话：盯蛋价。它连着跌几天，"
                                    "这边就跟着松——横的这几天不是不跌，是还没轮到。"))
        else:
            f.append(("后面怎么看", "盯蛋价。蛋价已经在跌，老母鸡没跟上只是早晚的事。"))
    else:
        f.append(("后面怎么看", "先看蛋价往哪边动，它动了这边才动。"))

    return {"head": "老母鸡%s，%s%s" % (_dir_word(dir_), p, _stamp(ctx, "老母鸡")),
            "factors": f}


# ══════════════════════════════════════════════════════════════════
#  三黄鸡 817 —— 今天真正有故事的一个
# ══════════════════════════════════════════════════════════════════
def sanhuang_factors(price, dir_, next_day=None, chick_egg=None,
                     analysis=None, series=None, ctx=None, detail=None):
    """三黄鸡：涨了几天、为什么、明天为什么拐头、这跌算不算掉头。

    detail：817 报价点明细 {"big","small","movers","total"} ——
    展示逻辑对齐工作台速览卡（8 点均价、大/小规格分列、当日涨跌家数）。
    """
    if price is None:
        return None
    p = _yuan(price)
    nd = next_day or {}
    f = []
    st = _streak(series)
    pfx = _pfx(ctx, "三黄鸡")
    dm = _dmark(ctx, "三黄鸡")
    det = detail or {}

    # ① 现在什么情况（事实 + 连涨几天 + 报价点涨跌家数 + 大小规格）
    if dir_ == "up":
        seg = "%s817涨到%s%s" % (pfx, p, dm)
        if st and st[0] == "up" and st[1] >= 2:
            # 只报"涨了几天、一共涨多少"，不复述起点价 —— 起点是均价，
            # 四舍五入再和现价比容易被算出对不上
            seg += "，已经连涨%d天，这%d天一共涨了%s" % (st[1], st[1], _fen(st[2]))
    elif dir_ == "down":
        seg = "%s817跌到%s%s" % (pfx, p, dm)
        if st and st[0] == "down" and st[1] >= 2:
            seg += "，已经连跌%d天，这%d天一共跌了%s" % (st[1], st[1], _fen(st[2]))
    else:
        seg = "%s817没动，还是%s%s" % (pfx, p, dm)

    # 报价点涨跌家数（8 个报价点里几个动）—— 工作台展示逻辑，正文同步
    mv = det.get("movers") or []
    if det.get("total") and mv:
        grp = {}
        for _, d_, a_ in mv:
            grp[(d_, round(a_ or 0, 2))] = grp.get((d_, round(a_ or 0, 2)), 0) + 1
        parts = ["%d个%s%s" % (n, "跌" if d_ == "down" else "涨", _fen(a_))
                 for (d_, a_), n in grp.items()]
        seg += "，%d个报价点里%s" % (det["total"], "、".join(parts))
    f.append(("现在什么情况", seg + "。"))

    # 大/小规格分开报（工作台卡同样分列）—— 销售报价比价不混
    if det.get("big"):
        spec = "大规格（3斤上下那种）平均%s" % _yuan(det["big"])
        if det.get("small"):
            spec += "，小规格（1.5-1.7斤）平均%s" % _yuan(det["small"])
        f.append(("分规格", spec + "。"))

    # ② 为什么涨/跌（用鸡苗的真实数字说话；苗价没成气候就不许说「苗在涨」）
    ce = (chick_egg or {}).get("rows") or []
    downs, ups, flats = _split_chg(ce)
    cb = _bias(ups, downs, flats)          # 鸡苗自身的方向，按占优判定
    chick_txt = ""
    if ce:
        chick_txt = "鸡苗那边%d个地方，%s" % (len(ce), _counts_txt(ups, downs, flats))
    # 鸡苗稿子讲的是哪一天，时间词就按哪一天说（见 _chick_label）
    ck_when, _ = _chick_label(chick_egg, ctx)
    if dir_ == "up":
        if cb == "up":
            f.append(("为什么涨", "817就看两样：鸡苗贵不贵，收鸡的收得凶不凶。"
                     "鸡苗贵了料钱就高，卖鸡的必然往上要价。"
                     + ck_when + chick_txt + "——成本这头在往上顶。"))
        elif cb == "flat":
            f.append(("为什么涨", "这涨不是苗价推的：%s%s，苗这头没在往上顶。"
                     "多半是收鸡的收得急、货不够卖。苗要是不跟着涨，"
                     "成本撑不住，这涨就得打个问号。" % (ck_when, chick_txt or "苗价没新价")))
        else:
            f.append(("为什么涨", "苗价反而在跌（%s%s），成本这头撑不住，"
                     "这涨别当趋势，报价留点余地。" % (ck_when, chick_txt)))
    elif dir_ == "down":
        if cb == "down":
            f.append(("为什么跌", "苗和毛鸡两头一起往下：苗价在跌（%s%s），"
                     "养鸡的不敢进苗；收鸡的压价，卖鸡的只能跟着让。"
                     "两头一起软，这跌就实了。" % (ck_when, chick_txt)))
        elif cb == "flat":
            f.append(("为什么跌", "苗价基本没动：%s%s。这跌不是苗价推的——"
                     "多半是收鸡的往下压、或者走货慢了。"
                     "苗要是也不跌，这跌一般深不了。" % (ck_when, chick_txt or "苗价没新价")))
        else:
            f.append(("为什么跌", "苗价还在涨（%s%s），成本顶着，"
                     "这跌多半是把前面涨快的部分吐回来，不是往下走。"
                     % (ck_when, chick_txt)))

    # ③ 明天（事实）—— 最有价值的一段。方向按占优判定，不许 2 涨 22 平叫「要涨」
    if nd.get("rows"):
        d_down, d_up, d_flat = _split_chg(nd.get("rows"))
        nb = _bias(d_up, d_down, d_flat)
        if nb == "down":
            seg = "明天（%s）的价源站已经出了：%s，一个涨的都没有。" % (
                nd.get("date"), _counts_txt(d_up, d_down, d_flat))
            det = _places_sentence(d_down, "跌")
            if det:
                seg += det + "。"
            if d_flat:
                seg += "%s没动。" % "、".join(str(r.get("place")) for r in d_flat)
            # 分省归堆放在"事实"这段，让下面的判断可以只讲结论
            pv = _prov_count([str(r.get("place")) for r in d_down])
            if len(pv) >= 2:
                seg += "跌的按省归堆：%s。" % "、".join("%s%d个" % (k, n) for k, n in pv)
            f.append(("明天要跌", seg))
        elif nb == "up":
            seg = "明天（%s）的价源站已经出了：%s。" % (
                nd.get("date"), _counts_txt(d_up, d_down, d_flat))
            det = _places_sentence(d_up, "涨")
            if det:
                seg += det + "。"
            f.append(("明天要涨", seg))
        elif d_up or d_down:
            seg = ("明天（%s）基本持平：%s。就个别地方动，没成气候，"
                   "别当成涨跌方向。" % (nd.get("date"), _counts_txt(d_up, d_down, d_flat)))
            movers = d_down + d_up
            det = _places_sentence(movers, "跌" if d_down else "涨")
            if det:
                seg += det + "。"
            f.append(("明天", seg))
        else:
            f.append(("明天", "明天（%s）源站也出价了，%s。"
                             % (nd.get("date"),
                                "价格没动" if len(d_flat) <= 1
                                else "%d个地方全是持平" % len(d_flat))))

    # ④ 这跌怎么看（分析的核心：跌在哪些省 + 苗价和毛鸡是否打架）
    if nd.get("rows"):
        d_down, d_up, d_flat = _split_chg(nd.get("rows"))
        nb = _bias(d_up, d_down, d_flat)
        if nb == "down":
            downs_p = [str(r.get("place")) for r in d_down]
            flats_p = [str(r.get("place")) for r in d_flat]
            seg = ""
            sd_down = any(x.startswith("山东") for x in downs_p)
            sd_flat = any(x.startswith("山东") for x in flats_p)
            if sd_flat and not sd_down:
                seg += ("跌的全压在河南和南方销区，山东主产区一个没松。"
                        "这种走法一般是高价区先往下让，不是全国一起塌。")
            elif sd_down:
                seg += "山东主产区也在跌，这就不是局部松动了，是全国一起往下走。"

            # 苗价和毛鸡打架 —— 这才是判断"回调还是掉头"的关键。
            # 苗的判断也按占优算，不许 2 涨 22 平说成「苗还在涨」
            if cb == "up":
                seg += ("再对着鸡苗看：苗还在涨（%s），毛鸡先跌，"
                        "等于养鸡的这头成本涨、那头卖价跌，两头一挤利润就薄了。"
                        "这一般是把前面涨太快的部分吐回来，不是掉头往下走。"
                        % _counts_txt(ups, downs, flats))
            elif cb == "down":
                seg += ("鸡苗也在跌，苗和毛鸡一起往下，"
                        "那就是养鸡的不愿意补栏了，这才是真掉头。")
            else:
                seg += ("苗价没跟上：%s，苗这头没在涨。毛鸡这一跌没有成本顶着，"
                        "更像收鸡那边在压价——先按回调看，但比苗还涨的时候"
                        "要多留个心眼，苗一转跌就不是回调了。"
                        % _counts_txt(ups, downs, flats))
            if seg:
                f.append(("算不算掉头", seg))

            # ⑤ 接下来盯什么（可验证的判断）
            watch = []
            if sd_flat and not sd_down:
                watch.append("山东明天松不松——山东要是也跌，就不是回调了")
            if cb != "down":
                watch.append("鸡苗会不会跟着转跌——苗一转跌，说明养鸡的不愿意进苗，那才是真拐头")
            if watch:
                f.append(("盯什么", "；".join(watch) + "。这两件事最值得盯。"))

    # ⑥ 源站说法 —— 只给人话版本，原话不抄；跟数字对得上就不废话
    view_src = (nd.get("view") or (analysis or {}).get("肉鸡", {}).get("view") or "")
    plain = decode_jargon(view_src)
    if plain:
        d_down, d_up, _ = _split_chg(nd.get("rows"))
        nb = _bias(d_up, d_down, _split_chg(nd.get("rows"))[2])
        if nb == "down":
            f.append(("行业说法", "源站对明天的说法，翻成大白话是「%s」。"
                                 "可它自己列的价%d个在跌——说法和数字对不上，"
                                 "这种话别拿去跟客户讲。" % (plain, len(d_down))))
        elif nb == "up":
            f.append(("行业说法", "源站对明天的说法，翻成大白话是「%s」，"
                                 "跟它列的价对得上。" % plain))
        else:
            f.append(("行业说法", "源站对明天的说法，翻成大白话是「%s」。"
                                 "不过它列的价基本持平，就个别地方动，"
                                 "别把说法当成要大涨大跌。" % plain))

    # ⑦ 跟我们什么关系
    f.append(("跟我们", "817就是我们三黄鸡冻品的原料。"
                              "它涨成本就上去，跌了就有空间。"))

    head = "三黄鸡817%s，%s%s" % (_dir_word(dir_), p, _stamp(ctx, "三黄鸡"))
    if nd.get("rows"):
        d_down, d_up, d_flat = _split_chg(nd.get("rows"))
        nb = _bias(d_up, d_down, d_flat)
        if nb == "down":
            head = "三黄鸡817涨到%s%s（明天要跌）" % (p, _stamp(ctx, "三黄鸡")) \
                if dir_ == "up" \
                else "三黄鸡817%s，%s%s（明天要跌）" % (
                    _dir_word(dir_), p, _stamp(ctx, "三黄鸡"))
        elif nb == "up" and dir_ != "up":
            head = "三黄鸡817%s，%s%s（明天还涨）" % (
                _dir_word(dir_), p, _stamp(ctx, "三黄鸡"))
    return {"head": head, "factors": f}


# ══════════════════════════════════════════════════════════════════
#  公鸡
# ══════════════════════════════════════════════════════════════════
def gongji_factors(price, dir_, lo=None, hi=None, heze=None,
                   flat_days=None, cnt=None, ctx=None):
    """公鸡：为什么涨、为什么跌。横盘久了换说法，不每天复读。

    cnt：当日产区涨跌家数（口径同速览卡 note 的「N涨/N平/N跌」）。
    """
    if price is None:
        return None
    p = _yuan(price)
    f = []
    pfx = _pfx(ctx, "公鸡")
    dm = _dmark(ctx, "公鸡")
    fd = flat_days or 0
    ct = cnt or {}
    n_total = sum(ct.get(k, 0) for k in ("up", "flat", "down")) or 6
    n_moved = ct.get("up", 0) + ct.get("down", 0)

    if dir_ == "flat":
        seg = "%s公鸡没动，%d个产区平均还是%s%s" % (pfx, n_total, p, dm)
        if n_moved == 0 and ct:
            # 时间词要跟数据日期走：不是当天的报价，说成「今天」就跟客户对不上
            #（源站没发当日稿时容易犯，11-18 修）。日期已经由 _dmark 挂在价后面。
            seg += "，%d个产区报价%s一个都没动" % (n_total, pfx)
        elif n_moved:
            seg += "，%d个产区里%d个在动" % (n_total, n_moved)
        if fd >= 3:
            seg += "，连着%d个报价日没动" % fd
        f.append(("现在什么情况", seg + "。"))
    else:
        seg = "%s公鸡%s，%d个产区平均%s%s" % (pfx, _dir_word(dir_), n_total, p, dm)
        if n_moved:
            seg += "，%d个产区里%d个在动" % (n_total, n_moved)
        f.append(("现在什么情况", seg + "。"))

    # 公鸡走的是跟蛋、跟老母鸡都不挨着的一条线，所以先说清它看什么
    if dir_ == "flat":
        if fd >= 6:
            f.append(("为什么没动", "原因还是前几天那些：饭店、食堂要货平稳，"
                                    "出栏量也没大变化，两头都不着急，价就横着。"))
        else:
            f.append(("为什么没动", "公鸡不跟鸡蛋、老母鸡一路，它看的是饭店、酒席、"
                                    "学校食堂什么时候要货，还有麻鸡公、黄花公这些品种"
                                    "这段时间出栏多不多。这几头都没什么变化，价格就定在这。"))
    elif dir_ == "up":
        f.append(("为什么涨", "饭店、酒席、食堂这段时间要货转旺，"
                              "出栏的鸡跟不上，价格就被顶上去了。"))
    else:
        f.append(("为什么跌", "饭店、酒席、食堂这段时间要货不旺，"
                              "该出栏的鸡没少，价格就被压下来了。"))

    if lo and hi:
        try:
            ratio = float(hi) / float(lo) if float(lo) > 0 else 0
        except (TypeError, ValueError, ZeroDivisionError):
            ratio = 0
        if ratio >= 1.5:
            f.append(("各地差多少", "最便宜%s、最贵%s，差了一倍多——"
                                          "这不是同一个品种在比价，青脚麻公和大红公"
                                          "本来就不是一个价。跨区比价先把品种和运费"
                                          "算清楚，光看单价容易比亏。"
                                          % (_yuan(lo), _yuan(hi))))
        else:
            f.append(("各地差多少", "最便宜%s、最贵%s，价差不小。"
                                    "跨区比价一定先把运费算进去，光看单价容易比亏。"
                                    % (_yuan(lo), _yuan(hi))))

    if heze:
        f.append(("菏泽价", heze))

    f.append(("跟我们", "公鸡是我们白条鸡、公鸡类冻品的原料。"
                              "它不动的时候，可以拿它带动别的品一起卖。"))

    return {"head": "公鸡%s，%s%s" % (_dir_word(dir_), p, _stamp(ctx, "公鸡")),
            "factors": f}


# ══════════════════════════════════════════════════════════════════
#  价格关系总览 —— 一眼看出谁牵动谁
# ══════════════════════════════════════════════════════════════════
def chain_overview(egg_avg, egg_dir, old_price, old_dir,
                   sh_price, sh_dir, gj_price, gj_dir, next_day=None, ctx=None):
    """用箭头串起来，销售一眼就能看懂"为什么这几个价会一起动"。"""
    e = _yuan(egg_avg) if egg_avg is not None else "—"
    o = _yuan(old_price) if old_price is not None else "—"
    s = _yuan(sh_price) if sh_price is not None else "—"
    g = _yuan(gj_price) if gj_price is not None else "—"
    d_egg = _dmark(ctx, "鸡蛋")
    d_sh = _dmark(ctx, "三黄鸡")
    d_gj = _dmark(ctx, "公鸡")


    sh_why = "鸡苗贵→养鸡成本高→卖鸡的要价硬"
    nd = next_day or {}
    if nd.get("rows"):
        d_down, d_up, d_flat = _split_chg(nd.get("rows"))
        b = _bias(d_up, d_down, d_flat)
        if b == "down":
            sh_why += "；明天%d个地方跌、%d个平，要往下" % (len(d_down), len(d_flat))
        elif b == "up":
            sh_why += "；明天%d个地方还在涨" % len(d_up)
        else:
            sh_why += "；明天基本持平"

    return [
        {"from": "鸡蛋", "to": "老母鸡",
         "why": "蛋涨价→养鸡的想留着鸡下蛋→卖的老母鸡少→鸡价涨",
         "state": "%s%s %s" % (e, d_egg, _arrow(egg_dir))},
        {"from": "鸡苗·收鸡", "to": "三黄鸡817",
         "why": sh_why,
         "state": "%s%s %s" % (s, d_sh, _arrow(sh_dir))},
        {"from": "饭店·食堂", "to": "公鸡",
         "why": "要货多→抢着出栏→价往上；要货少→价就软",
         "state": "%s%s %s" % (g, d_gj, _arrow(gj_dir))},
    ]


# ══════════════════════════════════════════════════════════════════
#  开头一句话 —— 销售早会念的第一句
# ══════════════════════════════════════════════════════════════════
def _cnt(n, word):
    """「2个地方涨」「0个持平」这种，0 就不说。"""
    return ("%d个地方%s" % (n, word)) if n else ""


def verdict(egg_avg, egg_dir, old_p, old_dir, sh_p, sh_dir,
            gj_p, gj_dir, next_day=None, series=None, ctx=None):
    """先说今天真正有动静的那件事，再一句带过没动的。

    忌讳：把"今天涨"和"明天跌"平铺成两句话，听着自相矛盾。
    必须点明这是一个转折。另外"三个都跌"不能写成"三个都没动"。
    """
    nd = next_day or {}
    st = _streak(series)
    parts = []
    pfx_sh = _pfx(ctx, "三黄鸡")
    dmark_sh = _dmark(ctx, "三黄鸡")


    if sh_p is not None:
        if sh_dir == "up":
            lead = "%s三黄鸡817涨到%s%s" % (pfx_sh, _yuan(sh_p), dmark_sh)
            if st and st[0] == "up" and st[1] >= 2:
                lead += "，连涨第%d天" % st[1]
        elif sh_dir == "down":
            lead = "%s三黄鸡817跌到%s%s" % (pfx_sh, _yuan(sh_p), dmark_sh)
            if st and st[0] == "down" and st[1] >= 2:
                lead += "，连跌第%d天" % st[1]
        else:
            lead = "%s三黄鸡817还是%s%s" % (pfx_sh, _yuan(sh_p), dmark_sh)

        if nd.get("rows"):
            d_down, d_up, d_flat = _split_chg(nd.get("rows"))
            tail = _counts_txt(d_up, d_down, d_flat)
            b = _bias(d_up, d_down, d_flat)
            if b == "down":
                lead += "，可明天（%s）就拐头了——%s" % (nd.get("date"), tail)
            elif b == "up":
                lead += "，明天（%s）接着涨——%s" % (nd.get("date"), tail)
            elif d_up or d_down:
                # 个别地方在动，但没成气候 —— 不能写成「接着涨」
                lead += ("，明天（%s）基本持平——%s，就个别地方动，别当方向"
                         % (nd.get("date"), tail))
            else:
                lead += "，明天（%s）全部持平" % nd.get("date")
        parts.append(lead)

    still, moved = [], []
    for name, p, d in (("老母鸡", old_p, old_dir), ("公鸡", gj_p, gj_dir),
                       ("鸡蛋", egg_avg, egg_dir)):
        if p is None:
            continue
        dm = _dmark(ctx, name)
        if d == "flat":
            still.append("%s还是%s%s" % (name, _yuan(p), dm))
        else:
            moved.append("%s%s到%s%s" % (name, "涨" if d == "up" else "跌",
                                          _yuan(p), dm))
    if moved:
        parts.append("、".join(moved))
    if still:
        parts.append("、".join(still) + "，没动")

    if not parts:
        return ""
    return "；".join(parts) + "。"


# ══════════════════════════════════════════════════════════════════
#  销售动作 —— 只讲对外沟通和报价节奏
# ══════════════════════════════════════════════════════════════════
def today_action(egg_avg, egg_dir, old_dir, sh_dir, gj_dir,
                 gj_days=None, next_day=None):
    """给销售的动作建议。

    必须和正文一致：817 明天要跌，就不能同时说"817在涨、按涨价谈"。
    这是 11-07 发现的硬伤。
    """
    out = []
    nd = next_day or {}
    d_down, d_up, d_flat = _split_chg(nd.get("rows"))
    # 明天是不是真要跌：按占优判定，2 涨 22 平这种不能算「要跌」
    sh_turn_down = _bias(d_up, d_down, d_flat) == "down"

    # 报价格
    if sh_turn_down:
        out.append(("报价格", "明天817已经出价，是跌的。三黄鸡的报价按明天的数走，"
                             "今天别再往高了报——报高了明天要返口。"))
    elif old_dir == "up":
        out.append(("报价格", "老母鸡在涨，手上有货的客户今天就把价报了，"
                             "明天不一定还是这个数。"))
    elif old_dir == "down":
        out.append(("报价格", "老母鸡在跌，价可以松一点，先把货走起来。"))
    else:
        out.append(("报价格", "老母鸡没动，按老价报就行，不用急着让价。"))

    # 提前打招呼
    if egg_avg is not None and egg_dir == "up":
        out.append(("提前打招呼", "鸡蛋在涨这件事主动跟客户提一句："
                                 "养鸡的现在更不愿意卖老母鸡了，这就是后面涨的理由。"
                                 "先说比事后解释好使。"))
    elif egg_avg is not None and egg_dir == "down":
        out.append(("提前打招呼", "鸡蛋在跌，老母鸡后面可能跟着松，"
                                 "手里有单子的先探探客户的口气。"))

    # 三黄鸡怎么谈
    if sh_turn_down:
        out.append(("谈三黄鸡", "817涨了几天，明天开始跌。跟客户别当涨价谈，"
                               "可以拿这个说'原料已经在回落'，把单子定下来。"))
    elif sh_dir == "up":
        out.append(("谈三黄鸡", "817在涨，三黄鸡成本跟着上。"
                               "谈的时候把原因讲清楚，别让对方以为是乱涨。"))

    # 推公鸡
    if gj_dir == "flat" and gj_days is not None and gj_days >= 40:
        out.append(("推公鸡", "公鸡价格没动、手里货也够，可以多推一推，"
                             "拿它带动别的品一起卖。"))
    return out


# ══════════════════════════════════════════════════════════════════
#  断货提醒 —— 销售接单前必须知道哪些货已经没了
# ══════════════════════════════════════════════════════════════════
def stock_out_note(mains):
    """哪些品项断了货，一句话说清 + 列出具体品项名。

    11-10 修：原来只数「急补」（库存 0 且本月有成交），漏掉「缺货」
    （库存 0 但本月没成交），结果老母鸡报 2 个、实际断了 6 个。
    销售照着报会接下单子发不出货，所以这里必须按真实 0 库存算。
    """
    if not mains:
        return "", []
    counts = []
    details = []
    for name in ("老母鸡", "三黄鸡", "公鸡"):
        m = mains.get(name) or {}
        items = m.get("out_of_stock") or []
        n = m.get("out_of_stock_count")
        if n is None:
            n = len(items)
        if not n:
            continue
        counts.append((name, n))
        details.append((name, items))
    if not counts:
        return "", []
    head = "这几类有 %s 品项已经断了货，跟客户报价前先确认有没有货。" % (
        "、".join("%s %d 个" % (n, k) for n, k in counts))
    # 每个品类最多列 3 个品项名，缺口最大的排前面
    lines = []
    for name, items in details:
        names = [x.get("sku") for x in (items or [])[:3] if x.get("sku")]
        if not names:
            continue
        more = len(items) - len(names)
        tail = "等 %d 个" % more if more > 0 else ""
        lines.append("%s断了 %d 个：%s%s"
                     % (name, len(items), "、".join(names), tail))
    return head, lines
