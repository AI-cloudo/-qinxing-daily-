#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
禽类行情日报 - 云端自动抓取脚本（GitHub Actions 定时运行，不依赖本机电脑）
数据源：鸡病专业网 jbzyw.com（鸡蛋+淘汰鸡汇总 / 白羽肉鸡 / 各地麻鸡公鸡 / 817肉杂）
原则：
  1. 每张表都标注数据日期（来自文章标题），绝不把旧价冒充当日价
  2. 单个源抓取失败不影响其他板块，失败板块沿用上次数据并醒目标注
  3. 环比通过与上一次运行的 state.json 对比自动计算
"""
import re
import json
import os
import sys
import time
import datetime
import urllib.request

BASE = "https://www.jbzyw.com"
UA = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15"}
HERE = os.path.dirname(os.path.abspath(__file__))


# 桌面 UA：网易搜索页对移动端 UA 只返回 JS 空壳（约50KB），桌面端才有明文结果（约300KB）
PC_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}


def fetch(url, timeout=20, headers=None):
    req = urllib.request.Request(url, headers=headers or UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="ignore")


def text_lines(html):
    txt = re.sub(r"<[^>]+>", "\n", html)
    return [l.strip() for l in txt.split("\n") if l.strip()]


def collect_articles():
    """从首页和两个栏目页收集最新文章链接 (url, title)"""
    arts = {}
    for path in ["", "/lists/279", "/lists/192"]:
        try:
            h = fetch(BASE + path)
        except Exception as e:
            print("[warn] 拉取列表失败 %s: %s" % (path, e))
            continue
        for m in re.finditer(r'href="(/view/\d+)"[^>]*title="([^"]+)"', h):
            arts[m.group(2)] = BASE + m.group(1)
    return arts


def collect_news(max_news=6):
    """从行业关注/市场分析/经营看点抓取禽类相关新闻标题+链接"""
    keywords = re.compile(
        r"肉鸡|蛋鸡|行情|价格|市场|冻品|白条|屠宰|毛鸡|蛋价|鸡价|817|麻鸡|"
        r"三黄鸡|淘汰鸡|白羽|公鸡|活禽|棚前|收购价|批发|鸡肉|禽类|家禽|肉类",
        re.I,
    )
    seen = set()
    items = []
    for path in ["/lists/1", "/lists/204", "/lists/269"]:
        try:
            h = fetch(BASE + path, timeout=15)
        except Exception as e:
            print("[warn] 新闻列表失败 %s: %s" % (path, e))
            continue
        for m in re.finditer(r'href="(/view/\d+)"[^>]*title="([^"]+)"', h):
            title = m.group(2).strip()
            url = BASE + m.group(1)
            if url in seen or not keywords.search(title):
                continue
            seen.add(url)
            # 尽量从标题提取日期
            dm = re.search(r"(\d{4}年)?(\d{1,2})月(\d{1,2})日", title)
            date_str = "%s/%s" % (dm.group(2), dm.group(3)) if dm else ""
            items.append({"title": title, "url": url, "date": date_str, "src": "鸡病专业网"})
    # 按 URL 中 view id 降序（新文章 id 更大），取最新 N 条
    items.sort(key=lambda x: int(re.search(r"/view/(\d+)", x["url"]).group(1)), reverse=True)
    return items[:max_news]


def latest(arts, keyword, exclude=None):
    """找标题含关键词的最新文章（按标题日期排序）"""
    best = None
    for title, url in arts.items():
        if keyword not in title:
            continue
        if exclude and any(x in title for x in exclude):
            continue
        m = re.search(r"(\d+)月(\d+)日", title)
        key = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
        if best is None or key > best[0]:
            best = (key, title, url)
    return best


# ---------- 解析器 ----------

def parse_summary(html):
    """鸡蛋+淘汰鸡汇总 → [{prov, city, egg, egg_trend, cull}]"""
    rows = []
    for line in text_lines(html):
        m = re.match(
            r"(\S+?)地区：(.+?)(?:红蛋散筐大码|红蛋|鸡蛋)?\s*([0-9]+(?:\.[0-9]+)?(?:-[0-9.]+)?)元/斤"
            r"[，,]?\s*(落|涨|与昨日相比持平|稳)?[，,;；]?\s*(?:淘汰鸡([0-9.]+)元/斤)?",
            line,
        )
        if not m:
            continue
        prov, city, egg, trend, cull = m.groups()
        city = re.sub(r"红蛋散筐大码|红蛋|鸡蛋|散筐大码|大码", "", city).strip()
        if not city or len(city) > 6:
            continue
        rows.append(
            {"prov": prov, "city": city, "egg": egg,
             "trend": {"落": "down", "涨": "up"}.get(trend, "flat") if trend else None,
             "cull": cull}
        )
    return rows


def parse_broiler(html):
    """白羽肉鸡 → [{city, price, delta}]"""
    rows = []
    for line in text_lines(html):
        m = re.search(
            r"(\S+?)地区肉(?:毛)?鸡棚前收购价([0-9]+(?:\.[0-9]+)?-[0-9.]+|[0-9.]+)元/斤，与\d+日相比(下滑|上涨|持平)(?:([0-9.]+)元/斤)?",
            line,
        )
        if m:
            city, price, direction, num = m.groups()
            if direction == "持平" or not num:
                cell = {"t": "→", "dir": "flat"}
            else:
                arrow = "↓" if direction == "下滑" else "↑"
                cell = {"t": arrow + num, "dir": "down" if direction == "下滑" else "up"}
            rows.append({"city": city, "price": price, "cell": cell})
    return rows


def parse_region_prices(html):
    """产区文章正文 → [(品种, price, trend)]，跳过鸡苗"""
    out = []
    for line in text_lines(html):
        m = re.match(
            r"([^：,，\s]{2,12})：.*?([0-9]+(?:\.[0-9]+)?(?:-[0-9.]+)?)\s*元/斤.*?[，,]\s*(稳|涨|落|下滑|上涨|持平)",
            line,
        )
        if not m:
            continue
        name, price, trend = m.groups()
        if "苗" in name:
            continue
        d = {"稳": "flat", "持平": "flat", "涨": "up", "上涨": "up",
             "落": "down", "下滑": "down"}.get(trend, "flat")
        out.append((name, price, d))
    return out


def parse_817(html):
    """817肉杂棚前 → [{region, spec, price, cell, bday}]

    bday = 正文「与N日相比」的 N。该栏（鸡病专业网《部分地区肉杂817棚前价格行情》）
    是**滚动更新页**：报价日 = bday + 1，标题日期在次日尚未改标题时会滞后一天
    （实测 2026-10-16：页面标题仍写「10月15日」，正文写「与15日相比上涨0.10元/斤」，
    而 10/15 的濮阳报价恰为 3.25-3.35、10/16 为 3.35-3.45，+0.10 严丝合缝）。
    故数据日期一律按 bday+1 校正，不能直接用标题日期。
    """
    rows = []
    for line in text_lines(html):
        m = re.search(
            r"(\S{2,8}?)([0-9.]+(?:-[0-9.]+)?斤[^，。]*?)棚前(?:收购价|价)([0-9]+(?:\.[0-9]+)?-[0-9.]+|[0-9.]+)元/斤，与(\d+)日相比(下滑|上涨|持平)(?:([0-9.]+)元/斤)?",
            line,
        )
        if not m:
            continue
        region, spec, price, bday, direction, num = m.groups()
        if "苗" in region + spec:
            continue
        if direction == "持平" or not num:
            cell = {"t": "→", "dir": "flat"}
        else:
            arrow = "↓" if direction == "下滑" else "↑"
            cell = {"t": arrow + num, "dir": "down" if direction == "下滑" else "up"}
        rows.append({"region": region, "spec": spec, "price": price, "cell": cell,
                     "bday": int(bday)})
    return rows


# ---------- mffb.com.cn（每日鸡蛋行情 / 鸡价行情，销区市场口径） ----------

MFFB = "https://mffb.com.cn"

# 重点销区/集散地城市（与「又鸟蛋」「小鲜农价」等小程序口径一致的市场报价）
MFFB_CITIES = [
    "北京", "上海", "广州", "东莞", "深圳", "天津", "重庆", "成都",
    "武汉", "孝感", "长沙", "常德", "杭州", "南京", "福州", "泉州",
    "南昌", "合肥", "郑州", "济南", "青岛", "临沂", "菏泽", "西安",
    "太原", "石家庄", "沈阳", "哈尔滨", "长春", "昆明", "贵阳", "南宁",
    "兰州", "乌鲁木齐", "呼和浩特", "银川", "海口", "苏州", "徐州", "盐城",
]


def title_date(title):
    """从文章标题里抠出数据日期，统一返回 'M/D'；抠不出返回 ''。
    兼容：2026.08.28鸡价行情 / 2026年8月28日鸡蛋价格行情 / 8月28日…"""
    m = re.search(r"(\d{4})\s*[.\-年/]\s*(\d{1,2})\s*[.\-月/]\s*(\d{1,2})", title or "")
    if m:
        return "%d/%d" % (int(m.group(2)), int(m.group(3)))
    m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日", title or "")
    if m:
        return "%d/%d" % (int(m.group(1)), int(m.group(2)))
    return ""


def mffb_latest(keyword):
    """从 mffb 首页找当天标题含 keyword 的最新文章，返回 (url, title)

    ⛔ 2026-10-14 停更源清退（用户拍板「停更的数据源一律不采用、不展示、删除」）：
    mffb 最新一期停在 8/28（连续 7 周无新文），仍在抓取只会把 8/28 的陈价
    当成新数据写进页面（板块二销区表 / 板块五分市表），故直接停用抓取，
    返回空值让下游按「本板块未抓到」处理。若该站日后复刊，删掉下面这行即可恢复。
    """
    return None, ""


def _mffb_latest_disabled(keyword):
    """（停用）原 mffb 抓取实现，保留备查"""
    home = None
    for _ in range(3):  # mffb 偶发 SSL 中断，重试
        try:
            home = fetch(MFFB + "/")
            break
        except Exception as e:
            print("[warn] mffb 首页抓取失败，重试:", e)
    if not home:
        return None, ""
    best = None
    for m in re.finditer(r'href="(/a/(\d+)\.html)"[^>]*>([^<]{4,60})<', home):
        title = m.group(3).strip()
        if keyword in title:
            if best is None or int(m.group(2)) > best[0]:
                best = (int(m.group(2)), MFFB + m.group(1), title)
    return (best[1], best[2]) if best else (None, "")


def parse_mffb_egg(html):
    """mffb 鸡蛋行情 → [{city, price, trend, txt}] 只保留 MFFB_CITIES 中的市场
    兼容两种版式：①分省城市均价列表（信息多）②市场段落实录（信息少，作为兜底）"""
    flat = re.sub(r"<(br|/p|/div|/tr|/td)[^>]*>", " ", html)
    flat = re.sub(r"<[^>]+>", " ", flat).replace("\xa0", " ")
    out, seen = [], set()

    def add(city, price, mark):
        city = city[:-1] if city.endswith("市") else city
        if city in seen or city not in MFFB_CITIES:
            return
        seen.add(city)
        trend = "up" if mark == "涨" else ("down" if mark == "落" else "flat")
        out.append({"city": city, "price": price, "trend": trend,
                    "txt": {"涨": "涨", "落": "落", "稳": "稳"}.get(mark, "—")})

    # 版式①：城市均价列表（如「东莞市 5.38 元/斤 涨」）
    for m in re.finditer(r"([一-龥]{2,8}?市?)\s*(\d\.\d{2})\s*元/斤\s*(涨|落|稳)?", flat):
        add(m.group(1), m.group(2), m.group(3) or "")
    if len(out) >= 3:
        return out

    # 版式②：段落实录（如「今日黑龙江哈尔滨、拉林主流鸡蛋价落0.1，褐壳大蛋到户价参考5.2元/斤」）
    for m in re.finditer(r"([一-龥]{2,12})[^。；]{0,40}?([\d]\.\d{1,2})\s*元/斤", flat):
        seg = m.group(1)
        hit_city = None
        for c in MFFB_CITIES:
            if seg.endswith(c):
                hit_city = c
                break
        if not hit_city:
            continue
        mark = "落" if "落" in seg else ("涨" if "涨" in seg else "")
        add(hit_city, m.group(2), mark)
    return out


SHANDONG = ("滨州", "德州", "菏泽", "济南", "济宁", "莱芜", "临沂", "聊城", "日照", "青岛",
            "威海", "潍坊", "莱阳", "招远", "烟台", "东营", "淄博", "泰安", "枣庄")


def backfill_broiler_history(arts, days=7):
    """从鸡病专业网历史「肉鸡行情分析」文章回填山东棚前均价，
    让白羽速览卡立刻有昨日/3天/7天趋势，而不是等 7 天攒数据"""
    out = []
    today = datetime.date.today()
    for i in range(days, 0, -1):
        dt = today - datetime.timedelta(days=i)
        md = "%d/%d" % (dt.month, dt.day)
        pat = "%d月%d日" % (dt.month, dt.day)
        # collect_articles() 返回 {标题: 链接}
        url = None
        for title, link in arts.items():
            if "肉鸡行情分析" in title and pat in title:
                url = link
                break
        if not url:
            continue
        try:
            rows = parse_broiler(fetch(url, timeout=15))
        except Exception:
            continue
        sd = [r for r in rows if r["city"] in SHANDONG]
        if not sd:
            continue
        v = avg_of([r["price"] for r in sd])
        if v:
            out.append((md, round(v, 2)))
    return out


MYSTEEL_BY = "https://www.mysteel.com/hot/1667520.html"   # 我的钢铁网·鸡业·2026年白羽肉鸡行情

# ===== 三黄鸡速览卡口径（用户 2026-10-16 定：「三黄鸡指的是 817 鸡杂较多」）=====
# 817肉杂棚前均价 = 顶栏「三黄鸡」卡的口径；chart.name 固定为此名，不符即重置序列。
CARD817_NAME = "817肉杂棚前均价"
# 历史补种：口径切换时用历史 commit 复算的真实值补足趋势线
# （取每个数据日最后一个 commit 的 817 表，按区间中值算 8 点均价；n=8 每日恒定）
SEED_817 = [("10/1", 3.612), ("10/2", 3.525), ("10/3", 3.487), ("10/4", 3.462),
            ("10/5", 3.500), ("10/6", 3.450), ("10/7", 3.450), ("10/8", 3.438),
            ("10/9", 3.413), ("10/10", 3.388), ("10/11", 3.431), ("10/13", 3.406),
            ("10/14", 3.388), ("10/15", 3.362)]

# ===== 三黄鸡（冻品快大类）数据源：网易·农财宝典畜牧版「全国鸡价」日报 =====
NCB_ART = "https://www.163.com/dy/article/%s.html"
NCB_SEEDS = ["L5C669FL0514E1NL"]     # 已知的一期「全国鸡价」，据此顺藤摸瓜找更新一期

# 冻品流通的快大类品种（三黄鸡口径），慢速类(项鸡/阉鸡/竹丝鸡等)一律排除
FAST_BREEDS = ("快大三黄鸡", "快大三黄", "肉杂鸡", "雪原林鸡", "青脚麻公鸡",
               "青麻公鸡", "麻公鸡", "黄花公鸡", "黄花公", "铁脚麻公",
               "青脚麻混鸡", "青脚麻公")
def _flex(name):
    """品种名容错：正文里「青脚麻 公鸡」这类被换行/标签拆开的名字也要能匹配上"""
    return r"\s*".join(re.escape(c) for c in name)


# 价格区间分隔符要含全角波浪号～（正文常用 4.0～4.4），否则整条价格匹配失败
NCB_BREED_RE = re.compile(
    r"(" + "|".join(_flex(b) for b in FAST_BREEDS) + r")\s*[:：]\s*"
    r"([\d.]+(?:[~\～\-－—][\d.]+)?)\s*元\s*/\s*斤")
NCB_PROV_RE = re.compile(
    r"(浙江|江西|福建|湖南|湖北|广东|粤东|江苏|安徽|山东|四川|重庆|贵州|"
    r"云南|广西|河南|河北|辽宁|吉林|黑龙江|山西|陕西)[^。\n]{0,16}?"
    r"(?:鸡价|肉鸡价格|肉鸡行情|区域价格|地区价格|区域肉鸡|地区肉鸡|肉鸡)")


NCB_SEARCH = "https://www.163.com/search?keyword=%E5%85%A8%E5%9B%BD%E9%B8%A1%E4%BB%B7"
# 搜索页正文里的三元组：文章URL,,'标题','', '封面图URL'（封面图路径含 /YYYY/MMDD/，用来判年份）
NCB_SEARCH_RE = re.compile(
    r"/dy/article/([A-Z0-9]{15,20})\.html[^']*','([^']*?(\d{1,2})月(\d{1,2})日全国鸡价"
    r"[^']*)','','([^']*)'")       # 组2=完整标题（含日期，下游要靠它取数据日期）

# 【首选发现通道】网易财经「鸡价」关键词聚合页：纯静态、列出农财宝典各期及日期。
# 2026-09-01 起 www.163.com/search 改版为 JS 渲染（只回 50KB 空壳，搜不出结果），
# 该聚合页仍是静态 HTML，实测可稳定取到最新一期，故提升为首选。
NCB_KEYWORD = "https://money.163.com/keywords/9/2/9e214ef7/1.html"
# 块结构：<div class="keyword_new ..."> <div class="keyword_img">封面图</div>
#         <h3><a href="/dy/article/{ID}.html">标题</a></h3>
#         <div class="keyword_other">...<div class="keyword_source">农财宝典畜牧版 2026-08-31</div>
# 边界用「下一个块起点或文末」的前瞻，不能用固定 </div> 收尾（块内嵌套层数不固定）
NCB_KW_BLOCK_RE = re.compile(r'<div class="keyword_new[^"]*"[^>]*>(.*?)(?=<div class="keyword_new|\Z)', re.S)
NCB_KW_AID_RE = re.compile(r"/dy/article/([A-Z0-9]{15,20})\.html")
NCB_KW_TITLE_RE = re.compile(r"<h3>\s*<a[^>]*>([^<]{5,90})</a>")
# keyword_source 里是「来源 2026-08-31」完整日期，最可靠
NCB_KW_SRC_RE = re.compile(r'class="keyword_source">\s*([^<]*?)\s*(20\d{2})-(\d{1,2})-(\d{1,2})')
# 兜底：封面图 URL 里有 URL 编码的 /YYYY/MMDD/
NCB_KW_DATE_RE = re.compile(r"(20\d{2})%2F(\d{4})%2F|(20\d{2})/(\d{4})/")


def ncb_discover_keyword():
    """从网易财经「鸡价」关键词聚合页取各期列表（首选通道）。

    返回 [( (年,月,日), 文章ID, 标题 ), ...]，只保留标题含「全国鸡价」的条目。
    """
    out, seen = [], set()
    try:
        h = fetch(NCB_KEYWORD, timeout=25, headers=PC_UA)
    except Exception as e:
        print("[warn] 农财宝典关键词页失败:", e)
        return out
    if h.count("全国鸡价") < 3:          # 页面改版/被挡时命中数会骤降
        print("[warn] 农财宝典关键词页疑似改版，命中%d处" % h.count("全国鸡价"))
        return out
    for blk in NCB_KW_BLOCK_RE.finditer(h):
        seg = blk.group(1)
        am = NCB_KW_AID_RE.search(seg)
        if not am:
            continue
        aid = am.group(1)
        if aid in seen:
            continue
        tm = NCB_KW_TITLE_RE.search(seg)
        title = (tm.group(1).strip() if tm else "")
        if "全国鸡价" not in title:
            continue
        dm = re.search(r"(\d{1,2})月(\d{1,2})日", title)
        sm = NCB_KW_SRC_RE.search(seg)
        if sm:                             # 优先用来源行的完整日期
            year, mo, dd = int(sm.group(2)), int(sm.group(3)), int(sm.group(4))
        elif dm:
            im = NCB_KW_DATE_RE.search(seg)
            if im:
                year = int(im.group(1) or im.group(3))
                mdy = im.group(2) or im.group(4)
                mo, dd = int(mdy[:2]), int(mdy[2:])
            else:                          # 拿不到年份就用标题里的月日，年份留 0
                year, mo, dd = 0, int(dm.group(1)), int(dm.group(2))
        else:
            continue
        seen.add(aid)
        out.append(((year, mo, dd), aid, title))
    return out


def ncb_discover_search():
    """网易站内搜索发现「全国鸡价」各期（备用通道）。

    2026-09-01 起搜索页改为 JS 渲染，多数时候只返回空壳，命中为空属正常。
    """
    out, seen = [], set()
    # 搜索页偶发返回 JS 空壳（约50KB、无结果），重试到拿到有效结果为止
    h = ""
    for i in range(3):
        try:
            t = fetch(NCB_SEARCH, timeout=25, headers=PC_UA)
        except Exception as e:
            print("[warn] 农财宝典搜索失败(%d): %s" % (i + 1, e))
            continue
        if len(t) > len(h):
            h = t
        if "全国鸡价" in h and len(h) > 100000:
            break
    if not h:
        return out
    for m in NCB_SEARCH_RE.finditer(h):
        aid, title, mo, dd, img = m.groups()
        if aid in seen:
            continue
        ym = re.search(r"/(20\d{2})/", img) or re.search(r"(20\d{2})", img)
        year = int(ym.group(1)) if ym else 0
        seen.add(aid)
        out.append(((year, int(mo), int(dd)), aid, title))
    return out


def ncb_discover():
    """发现「全国鸡价」各期：关键词聚合页优先，站内搜索兜底。"""
    out = ncb_discover_keyword()
    if not out:
        out = ncb_discover_search()
        if out:
            print("[info] 关键词页无结果，站内搜索兜底命中 %d 期" % len(out))
    return out


def ncb_fetch(aid):
    """抓取一期「全国鸡价」正文：PC 站优先，失败再试 m.163 移动站（同内容不同域名，
    可绕过云端个别 IP 对 www.163 的偶发封锁）。带重试。"""
    urls = [NCB_ART % aid, "https://m.163.com/dy/article/%s.html" % aid]
    for u in urls:
        for _ in range(2):
            try:
                h = fetch(u, timeout=15, headers=PC_UA)
                if h and len(h) > 2000:
                    return h
            except Exception as e:
                print("[warn] ncb 抓取 %s 失败: %s" % (u.split("/")[-1], e))
    return None


def ncb_latest(seed_ids):
    """取最新一期「全国鸡价」。

    优先级：关键词聚合页（静态、无需正文即可拿到「最新日期」）＞ 种子文章推荐位扩散。

    硬规则（修复「云端偶发拉不到聚合页→退化旧种子→数据滞留旧日期如 8/20」）：
    只要关键词聚合页成功解析出最新日期 kw_max，最终返回的文章日期绝不允许早于 kw_max。
    即便正文暂时抓不到，也返回聚合页定位到的最新一期（正文置空），交由上层降级处理，
    绝不停留在旧数据上。
    """
    now = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    # 通道一：关键词聚合页（首选，可稳定拿到各期及日期）
    kw = ncb_discover_keyword()
    kw_max = max((c[0] for c in kw), default=None)
    cands = [c for c in kw if c[0][0] == 0 or c[0][0] == now.year]
    cands.sort(key=lambda x: x[0], reverse=True)
    for key, aid, title in cands[:5]:
        h = ncb_fetch(aid)
        if h and ("全国鸡价" in title or "全国鸡价" in h):
            print("[ncb] 聚合页命中 %d/%d %s" % (key[1], key[2], aid))
            return aid, title, h

    # 通道二：仅当聚合页完全无结果时才用种子扩散；任何结果不得旧于 kw_max
    if not kw:
        found, tried, frontier = [], set(), list(seed_ids)
        for _ in range(2):          # 最多向外扩展两层
            nxt = []
            for aid in frontier:
                if aid in tried:
                    continue
                tried.add(aid)
                h = ncb_fetch(aid)
                if not h:
                    continue
                tm = re.search(r"<title>([^<]*)</title>", h)
                title = tm.group(1) if tm else ""
                if "全国鸡价" in title:
                    dm = re.search(r"(\d+)月(\d+)日", title)
                    key = (int(dm.group(1)), int(dm.group(2))) if dm else (0, 0)
                    if kw_max and key < kw_max:   # 闸门：不得旧于聚合页已知最新
                        print("[ncb] 跳过旧文 %d/%d（聚合页已知最新 %d/%d）"
                              % (key[0], key[1], kw_max[1], kw_max[2]))
                        continue
                    found.append((key, aid, title, h))
                nxt.extend(x for x in re.findall(r"(L[A-Z0-9]{15,20})", h) if x not in tried)
            frontier = nxt[:8]
            if not frontier:
                break
        if found:
            found.sort(key=lambda x: x[0], reverse=True)
            _, aid, title, html = found[0]
            return aid, title, html

    # 通道三：聚合页已定位最新一期（日期已知），但正文暂时抓不到——
    # 返回其 ID/标题（正文置空），上层标记为「已定位 X/X，正文暂未抓到」，绝不停留旧数据
    if cands:
        key, aid, title = cands[0]
        print("[ncb] 正文暂未抓到，但聚合页已定位最新一期 %d/%d %s" % (key[1], key[2], aid))
        return aid, title, None
    return None, "", None


def parse_ncb_chicken(html):
    """全国鸡价日报 → [{prov, breed, price}]，只取冻品快大类"""
    txt = re.sub(r"<script.*?</script>|<style.*?</style>", "", html, flags=re.S)
    txt = re.sub(r"<[^>]+>", " ", txt)
    # 换行一律转空格：正文里「湖北」与「鸡价稳定」常被换行断开，不合并会漏掉整个省份
    txt = re.sub(r"\s+", " ", txt)
    marks = [(m.start(), m.group(1)) for m in NCB_PROV_RE.finditer(txt)]
    if not marks:
        return []
    out, seen = [], set()
    for i, (pos, prov) in enumerate(marks):
        seg = txt[pos:(marks[i + 1][0] if i + 1 < len(marks) else len(txt))]
        for m in NCB_BREED_RE.finditer(seg):
            breed = re.sub(r"\s+", "", m.group(1))     # 「青脚麻 公鸡」→「青脚麻公鸡」
            price = re.sub(r"[~\～－—]", "-", m.group(2))
            key = (prov, breed)
            if key in seen:
                continue
            seen.add(key)
            lo = float(re.split(r"[-]", price)[0])
            if not (3.0 <= lo <= 7.0):      # 快大类合理区间
                continue
            out.append({"prov": prov, "breed": breed, "price": price})
    return out


def parse_mysteel_broiler(html):
    """我的钢铁网（Mysteel 钢联）白羽肉鸡分市场报价
    → {date, rows:[{market, src, spec, price}]}
    页面形如：白羽肉鸡 社会 临沂 4.5-6.0斤 3.05"""
    txt = re.sub(r"<script.*?</script>|<style.*?</style>", "", html, flags=re.S)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = re.sub(r"[ \t\xa0]+", " ", txt)
    dm = re.search(r"今日价格[^。]{0,40}?(20\d\d-\d\d-\d\d)", txt)
    date = dm.group(1) if dm else ""
    rows, seen = [], set()
    for m in re.finditer(r"白羽肉鸡\s+(社会|共担)\s+([一-龥]{2,6}?)\s+([\d.]+)-([\d.]+)斤\s+([\d.]{3,5})", txt):
        src, market, spec, price = m.group(1), m.group(2), m.group(3) + "-" + m.group(4), m.group(5)
        key = (market, src)
        if key in seen:
            continue
        seen.add(key)
        pv = float(price)
        if not (2.5 <= pv <= 5.0):      # 白羽毛鸡合理区间
            continue
        rows.append({"market": market, "src": src, "spec": spec, "price": price})
    return {"date": date, "rows": rows}


def parse_mffb_broiler(html):
    """mffb 鸡价行情 → [{region, city, price, chg, amt}] 只取肉毛鸡（白羽）区块"""
    txt = re.sub(r"<script.*?</script>", "", html, flags=re.S)
    txt = re.sub(r"<[^>]+>", "\n", txt)
    lines = [l.strip() for l in txt.split("\n") if l.strip()]
    out, region, kind = [], "", ""
    for ln in lines:
        m = re.match(r"^([一-龥]{2,8}地区)$", ln)
        if m:
            region = m.group(1).replace("地区", "")
            continue
        if "817" in ln:
            kind = "817"
            continue
        if "苗行情" in ln or "鸡苗" in ln:
            kind = "miao"
            continue
        if "肉毛鸡行情" in ln:
            kind = "broiler"
            continue
        if kind != "broiler":
            continue
        m = re.match(r"^([一-龥]{2,6})\s+(\d\.\d{2}-\d\.\d{2})\s+(下滑|上涨|持平)([\d.]*)$", ln)
        if not m:
            continue
        lo, hi = (float(x) for x in m.group(2).split("-"))
        # 白羽肉毛鸡全国合理区间约 2.8-4.2 元/斤，剔除同页混入的麻鸡/817 等高价行
        if not (2.8 <= (lo + hi) / 2 <= 4.2):
            continue
        out.append({"region": region, "city": m.group(1), "price": m.group(2),
                    "chg": m.group(3), "amt": m.group(4) or ""})
    return out


def fetch_retry(url, times=3):
    """mffb 偶发 SSL 中断，重试几次"""
    for i in range(times):
        try:
            return fetch(url)
        except Exception as e:
            print("[warn] 抓取失败(%d/%d) %s: %s" % (i + 1, times, url, e))
    return None


# ===== 鸡病专业网《山东地区X-X日鸡苗-肉鸡冻品市场动态速览》→ 白羽分割品【日更】 =====
# 2026-10-09 新增：用户要求白羽分割品由「周一AI周更」升级为每日自动收集。
# 该文每日一篇（标题形如「山东地区10-11日鸡苗-肉鸡冻品市场动态速览」，日期是覆盖期而非发文日），
# 正文直接给出山东冻品成交价区间：胸类/板冻/翅类/腿类/爪类，云端可直抓。
# 辽宁口径暂无免费日更源（Mysteel 同名日更表在付费墙后），仍走周一 AI 校准的周更表并另行标注。
FROZEN_KW = "冻品市场动态速览"
FROZEN_ORDER = ["单冻大胸", "单冻毛胸", "板冻大胸", "板冻小胸", "大中小翅根",
                "大中小翅中", "单冻翅尖", "腿类", "大中小", "大小凤爪"]
FROZEN_GROUP = {"单冻大胸": "鸡胸", "单冻毛胸": "鸡胸", "板冻大胸": "鸡胸", "板冻小胸": "鸡胸",
                "大中小翅根": "鸡翅", "大中小翅中": "鸡翅", "单冻翅尖": "鸡翅",
                "腿类": "鸡腿", "大中小": "鸡爪", "大小凤爪": "鸡爪"}
FROZEN_LABEL = {"腿类": "腿类（80-180g）", "大中小": "爪类（大中小）", "大小凤爪": "凤爪（大小）",
                "大中小翅根": "翅根（大中小）", "大中小翅中": "翅中（大中小）"}


def frozen_pick(arts):
    """取最新一期冻品速览。标题不含「X月X日」，不能用 latest() 的日期排序，改按 view id 降序。"""
    best = None
    for title, url in arts.items():
        if FROZEN_KW not in title:
            continue
        m = re.search(r"/view/(\d+)", url)
        key = int(m.group(1)) if m else 0
        if best is None or key > best[0]:
            best = (key, title, url)
    return best


def parse_frozen_sd(html):
    """冻品速览正文 → 山东冻品成交区间。返回 [{name,label,group,lo,hi,mid}]"""
    txt = re.sub(r"<[^>]+>", " ", html).replace("&nbsp;", " ")
    # 腿类都写成「腿类(80-180g )成交价区间…」，规格括号会污染品名提取，先归一掉
    txt = re.sub(r"腿类\s*[（(]\s*80\s*-\s*180\s*g?\s*[)）]", "腿类", txt)
    txt = re.sub(r"\s+", "", txt)
    out, seen = [], set()
    for m in re.finditer(r"成交价区间[（(]?([\d.]+)[-~～－—]([\d.]+)", txt):
        pre = txt[max(0, m.start() - 20):m.start()]
        pre = re.split(r"[；;，,、（(]", pre)[-1].replace("主流", "")
        for nm in FROZEN_ORDER:
            if nm in pre and nm not in seen:
                seen.add(nm)
                lo, hi = float(m.group(1)), float(m.group(2))
                out.append({"name": nm, "label": FROZEN_LABEL.get(nm, nm),
                            "group": FROZEN_GROUP.get(nm, ""),
                            "lo": lo, "hi": hi, "mid": round((lo + hi) / 2, 2)})
                break
    return out


def frozen_pub_date(html):
    """发文日期：正文头部固定为「YYYY-MM-DD 来源:鸡病专业网…」。
    标题里的「10-11日」是覆盖期而非发文日，不能当数据日期用。"""
    txt = re.sub(r"<[^>]+>", " ", html).replace("&nbsp;", " ")
    m = re.search(r"(20\d{2})-(\d{1,2})-(\d{1,2})\s*(?:来源|文章编辑)", txt)
    return "%d/%d" % (int(m.group(2)), int(m.group(3))) if m else ""


# ===== 博亚和讯《全国部分地区淘鸡出场价格》→ 板块一「淘汰鸡」分省分市【日更】 =====
# 2026-10-09 新增。工作日日更（国庆等长假停发），表格含 省份/地区/蛋鸡品种/上期/本期/涨跌，
# 粒度到地级市，覆盖 19 省（辽宁/河北/陕西/河南/山东/湖南/湖北/江苏/安徽/浙江/山西/甘肃/
# 吉林/黑龙江/新疆/内蒙古/贵州/四川/江西），明显多于鸡病专业网汇总（11 省）且自带涨跌列。
# search 页可稳定发现最新一期 article id（标题内"淘鸡"被 <font> 包裹，需先去标签再匹配）。
BOYAR_TJ_SEARCH = "https://www.boyar.cn/search/%E6%B7%98%E9%B8%A1.html"
# 注意：站点对移动 UA 返回精简页（无文章列表），必须用桌面 UA，勿用全局默认移动 UA
BOYAR_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}

# 2026-10-15 新增：云端对本站 article 页**偶发 read timeout**（实测 10/14、10/15 连续两天
# 淘鸡文章抓取失败 → 触发「沿用上一期」→ 页面把下跌伪装成持平）。裸 fetch 一次失败就丢
# 当期数据，代价太大。这里重试 3 次 + 放宽超时 + 间隔退避；仍失败返回 None 交给沿用逻辑。
BOYAR_RETRY = 3


def boyar_fetch(url, times=BOYAR_RETRY, timeout=30):
    """博亚和讯专用抓取（重试 + 长超时）。失败返回 None，绝不抛异常。"""
    for i in range(times):
        try:
            return fetch(url, timeout=timeout, headers=BOYAR_HEADERS)
        except Exception as e:
            print("[warn] 博亚和讯抓取失败(%d/%d) %s: %s" % (i + 1, times, url, e))
            if i < times - 1:
                time.sleep(2 + 2 * i)
    return None


def boyar_latest():
    """博亚和讯搜索页 → 最新一期《YYYY年M月D日全国部分地区淘鸡出场价格》的 (url, "M/D")"""
    h = boyar_fetch(BOYAR_TJ_SEARCH, timeout=25)
    if not h:
        print("[warn] 博亚和讯搜索页抓取失败（重试 %d 次）" % BOYAR_RETRY)
        return None, ""
    cands = []
    for m in re.finditer(r'href="(/article/(\d+)\.html)"[^>]*>(.{0,300}?)</a>', h, re.S):
        u, aid, inner = m.group(1), m.group(2), m.group(3)
        txt = re.sub(r"<[^>]+>", "", inner).strip()
        if "淘鸡" not in txt or "出场价" not in txt:
            continue
        dm = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", txt)
        if not dm:
            continue
        cands.append((int(aid), "https://www.boyar.cn" + u,
                      "%d/%d" % (int(dm.group(2)), int(dm.group(3)))))
    if not cands:
        return None, ""
    cands.sort(key=lambda x: x[0], reverse=True)
    return cands[0][1], cands[0][2]


def parse_boyar_tj(html):
    """博亚淘鸡出场价表 → [{"prov","city","breed","prev","cur","chg"}]
    省份列用 rowspan 跨行，其后各行只有 5 个 td（省份沿用上一行）。"""
    rows, prov = [], ""
    num = re.compile(r"^\d+(?:\.\d+)?$")
    for tr in re.findall(r"<tr.*?</tr>", html, re.S):
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        cells = [re.sub(r"<[^>]+>", "", c).replace("&nbsp;", " ").strip() for c in tds]
        if len(cells) == 6 and num.match(cells[4] or ""):
            prov = cells[0]
            rows.append({"prov": prov, "city": cells[1], "breed": cells[2],
                         "prev": cells[3], "cur": cells[4], "chg": cells[5]})
        elif len(cells) == 5 and prov and num.match(cells[3] or ""):
            rows.append({"prov": prov, "city": cells[0], "breed": cells[1],
                         "prev": cells[2], "cur": cells[3], "chg": cells[4]})
    return rows


def boyar_trend(chgs):
    """省级涨跌：多数决（flat 家数占多数时优先判稳，避免单市波动带偏）"""
    ups = sum(1 for c in chgs if c > 0)
    downs = sum(1 for c in chgs if c < 0)
    flats = len(chgs) - ups - downs
    if flats >= max(ups, downs):
        d = "flat"
    elif ups > downs:
        d = "up"
    elif downs > ups:
        d = "down"
    else:
        d = "flat"
    return {"t": {"up": "↑", "down": "↓", "flat": "→"}[d], "dir": d}


# ===== 博亚和讯《全国部分地区鸡蛋出场价格》→ 鸡蛋分省/分市出场价【日更】 =====
# 2026-10-14 新增：替代**已停更**的 mffb「主销区蛋价表」（mffb 最新一期停在 8/28，
# 连续 7 周无新文；页面侧那张「主销区」表是它的遗留快照，无 cap 未被策略扫到）。
# 本表覆盖 20+ 省 130+ 市，含北京石门/大洋路/回龙观/新发地、上海浦东/浦西、
# 广东广州/东莞/深圳等销区，自带「昨日 / 今日 / 涨跌」三列，工作日日更。
BOYAR_EGG_SEARCH = "https://www.boyar.cn/search/%E9%B8%A1%E8%9B%8B.html"   # 鸡蛋


def boyar_egg_latest():
    """博亚和讯搜索页 → 最新一期《YYYY年M月D日全国部分地区鸡蛋出场价格》的 (url, "M/D")"""
    h = boyar_fetch(BOYAR_EGG_SEARCH, timeout=25)
    if not h:
        print("[warn] 博亚和讯鸡蛋搜索页抓取失败（重试 %d 次）" % BOYAR_RETRY)
        return None, ""
    cands = []
    for m in re.finditer(r'href="(/article/(\d+)\.html)"[^>]*>(.{0,300}?)</a>', h, re.S):
        u, aid, inner = m.group(1), m.group(2), m.group(3)
        txt = re.sub(r"<[^>]+>", "", inner).strip()
        if "鸡蛋" not in txt or "出场价" not in txt:
            continue
        dm = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", txt)
        if not dm:
            continue
        cands.append((int(aid), "https://www.boyar.cn" + u,
                      "%d/%d" % (int(dm.group(2)), int(dm.group(3)))))
    if not cands:
        return None, ""
    cands.sort(key=lambda x: x[0], reverse=True)
    return cands[0][1], cands[0][2]


def parse_boyar_egg(html):
    """博亚鸡蛋出场价表 → [{"prov","city","breed","prev","cur","chg"}]
    省份列 rowspan 跨行，其后各行只有 5 个 td（省份沿用上一行）。"""
    rows, prov = [], ""
    num = re.compile(r"^\d+(?:\.\d+)?$")
    for tr in re.findall(r"<tr.*?</tr>", html, re.S):
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        cells = [re.sub(r"<[^>]+>", "", c).replace("&nbsp;", " ").strip() for c in tds]
        if len(cells) == 6 and num.match(cells[3] or "") and num.match(cells[4] or ""):
            prov = cells[0]
            rows.append({"prov": prov, "city": cells[1], "breed": cells[2],
                         "prev": cells[3], "cur": cells[4], "chg": cells[5]})
        elif len(cells) == 5 and prov and num.match(cells[2] or "") and num.match(cells[3] or ""):
            rows.append({"prov": prov, "city": cells[0], "breed": cells[1],
                         "prev": cells[2], "cur": cells[3], "chg": cells[4]})
    return rows


# 销区代表（用户是冻品采购方，看销区蛋价了解终端走货；北京/上海/广东为一贯口径）
EGG_XIAOQU = ["北京市", "上海市", "广东省"]
EGG_XIAOQU_SRC = "博亚和讯《全国部分地区鸡蛋出场价格》"


# ===== Mysteel《辽宁屠宰企业白羽肉鸡X类冻品》→ 辽宁冻品分规格中间价【日更】 =====
# 2026-10-14 新增：用户要求白羽分割品「停更的清退、补日更源」。
# 具体文章页（ncp.mysteel.com/a/...）正文有付费墙、只露前 3-4 行，但站内聚合页
# www.mysteel.com/hot/1585859.html（肉鸡实时行情）会**全文转载**当日的
# 【爪类/胸类/翅类/腿类/副产】五篇快讯，含「X中间价N.NN元/公斤」，无需登录即可读。
# 用它承接原「山东/辽宁中价周更表」里的**辽宁口径**（山东口径已由鸡病专业网日更覆盖）。
MYSTEEL_FG_LIST = "https://jiye.mysteel.com/rjfgp/article/p-4862----071202---------1.html"
MYSTEEL_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                 "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"}

_MY_CATS = ["爪类", "胸类", "翅类", "腿类", "副产"]
_MY_PROV = [("山东", "山东屠宰企业"), ("辽宁", "辽宁屠宰企业")]
# 展示用核心规格（源站规格更多，按冻品采购常用项挑选，控制手机端表格长度）
_MY_SPECS = {
    ("山东", "爪类"): ["小凤爪", "大凤爪", "30-40小爪", "40-50中爪", "50-60大爪"],
    ("山东", "胸类"): ["单冻大胸", "板冻大胸", "板冻小胸"],
    ("山东", "翅类"): ["30以下翅中", "30-40翅中", "40以上翅中"],
    ("山东", "腿类"): ["80-100琵琶腿", "带皮腿肉"],
    ("山东", "副产"): ["大脖", "中脖", "大骨架", "鸡胗", "鸡心", "大板肝"],
    ("辽宁", "爪类"): ["小凤爪", "大凤爪", "30-40爪", "40-50爪", "50-60爪"],
    ("辽宁", "胸类"): ["单冻大胸", "板冻大胸", "板冻小胸"],
    ("辽宁", "翅类"): ["30-40翅中", "40-50翅中", "50以上翅中", "小全翅", "大全翅"],
    ("辽宁", "腿类"): ["80-100琵琶腿", "带皮腿肉"],
    ("辽宁", "副产"): ["大脖", "中脖", "小脖", "大骨架", "叉骨", "大鸡胗", "小鸡胗",
                     "鸡心", "大板肝"],
}
_CAT_GROUP = {"爪类": "鸡爪", "胸类": "鸡胸", "翅类": "鸡翅", "腿类": "鸡腿", "副产": "副产"}
# 解析用全量规格白名单（含未在页面展示的项，避免散文里「其中XX」这类前缀把名称带偏）
_MY_ALL = {
    ("山东", "爪类"): ["小凤爪", "大凤爪", "30-40小爪", "40-50中爪", "50-60大爪", "60-70大爪", "70以上大爪"],
    ("山东", "胸类"): ["单冻大胸", "毛胸", "板冻大胸", "板冻小胸", "胸皮"],
    ("山东", "翅类"): ["30以下翅中", "30-40翅中", "40以上翅中", "30-40翅根", "40-50翅根", "翅尖", "小全翅", "大全翅"],
    ("山东", "腿类"): ["70-80腿肉", "80-100琵琶腿", "100-120琵琶腿", "120-140琵琶腿", "160-180琵琶腿", "5连腿", "带皮腿肉", "排腿"],
    ("山东", "副产"): ["大脖", "中脖", "小脖", "脖皮", "大骨架", "叉骨", "鸡胗", "鸡心", "大板肝"],
    ("辽宁", "爪类"): ["小凤爪", "大凤爪", "30-40爪", "40-50爪", "50-60爪", "60-70爪", "70以上爪"],
    ("辽宁", "胸类"): ["单冻大胸", "毛胸", "板冻大胸", "板冻小胸", "胸皮"],
    ("辽宁", "翅类"): ["30-40翅中", "40-50翅中", "50以上翅中", "30-40翅根", "40-50翅根", "翅尖", "小全翅", "大全翅"],
    ("辽宁", "腿类"): ["80-100琵琶腿", "100-120琵琶腿", "120-140琵琶腿", "160-180琵琶腿", "带皮腿肉", "排腿"],
    ("辽宁", "副产"): ["大脖", "中脖", "小脖", "脖皮", "大骨架", "叉骨", "大鸡胗", "小鸡胗", "鸡心", "大板肝"],
}


def _my_parse_article(html, prov, cat):
    """一篇文章 → {"规格": [中间价, 涨跌]}。
    数据来源＝页头「智能摘要」+ 可见表格片段（原文正文有登录墙，只露前 3-4 行）。
    只输出当日确实报价的规格，未报价项不返回（绝不沿用旧值）。"""
    ai_m = re.search(r'<pre class="ai-summary__text"[^>]*>([\s\S]*?)</pre>', html)
    ai = re.sub(r"<[^>]+>", "", ai_m.group(1)) if ai_m else ""
    i = html.find("单位：元/公斤")
    tb = html[i:i + 900] if i > 0 else ""
    tb = re.sub(r"<[^>]+>", " ", tb).split("免责声明")[0]
    specs = _MY_ALL.get((prov, cat), [])
    vals = {}
    # ① 可见表格：规格 最低价 最高价 中间价 涨跌
    for m in re.finditer(r"([\u4e00-\u9fa5A-Za-z0-9\-]{2,12})\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s+"
                         r"(\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)", tb):
        nm = m.group(1)
        if nm in specs and nm not in vals:
            vals[nm] = [float(m.group(4)), float(m.group(5))]
    # ② 智能摘要散文：规格…N.NN元/公斤
    # 【坑·2026-10-14 实测】源站有个别条目只写涨跌、不写价（例「…50以上翅中下跌0.10元/公斤…」），
    # 泛化取值会把涨跌值 0.10 当成中间价（线上曾出现「辽宁 50以上翅中 0.10」这种显然错值）。
    # 两道闸：a) 规格与数值之间出现「涨/跌」→ 判为涨跌值，跳过并继续找下一处；
    #          b) 值 < 1.0 元/公斤 → 白羽分割品最便宜的项（大脖/脖皮）也在 2 元/公斤以上，一律视为误取。
    # 全篇找不到合法报价 → 该规格当日不输出（绝不沿用旧值）。
    for sp in specs:
        if sp in vals:
            continue
        for mm in re.finditer(re.escape(sp) + r"([^。；，]{0,16}?)(\d+(?:\.\d+)?)元/公斤", ai):
            gap, px = mm.group(1), float(mm.group(2))
            if re.search(r"[涨跌]", gap) or px < 1.0:
                continue
            vals[sp] = [px, None]
            break
    # ③ 涨跌方向：规格…(上涨|下跌|…)D 元
    # 源站常写「规格价39.35元/公斤，较上一工作日下跌0.10元/公斤」，故允许间隔跨「，」（上限 24 字）；
    # 但若间隔里混入**同品类其他规格名**，说明已串到下一规格，跳过（防串味）。
    for sp in list(vals):
        if vals[sp][1] is not None:
            continue
        vals[sp][1] = 0.0
        for mm in re.finditer(re.escape(sp) + r"([^。；]{0,24}?)(上涨|下跌|涨|跌|落)(\d+(?:\.\d+)?)元", ai):
            if any(o != sp and o in mm.group(1) for o in specs):
                continue
            vals[sp][1] = float(mm.group(3)) * (1 if mm.group(2) in ("上涨", "涨") else -1)
            break
    return vals


def mysteel_frozen_daily():
    """白羽分割品（山东+辽宁）日更快讯 → {"date":"M/D", "rows":{省份:[[品类,规格,中间价,涨跌],...]}}
    抓不到返回 None。列表页稳定日更；每省每品类取当天最新一篇。"""
    try:
        h = fetch(MYSTEEL_FG_LIST, timeout=25, headers=MYSTEEL_HEADERS)
    except Exception as e:
        print("[warn] Mysteel 分割品列表页抓取失败:", e)
        return None
    if not h:
        return None
    picked, seen = {}, set()
    for m in re.finditer(r'<a[^>]+href="(https?://[a-z.]*mysteel\.com/a/(\d+)/[0-9A-F]+\.html)"'
                         r'[^>]*>([\s\S]{0,200}?)</a>', h):
        url, aid, title = m.group(1), int(m.group(2)), re.sub(r"<[^>]+>", "", m.group(3)).strip()
        if url in seen:
            continue
        seen.add(url)
        for prov, key in _MY_PROV:
            if key not in title:
                continue
            cat = next((c for c in _MY_CATS if c in title), None)
            if not cat:
                continue
            k = (prov, cat)
            if k not in picked or aid > picked[k][0]:
                picked[k] = (aid, url)
    if not picked:
        print("Mysteel 分割品: 列表页未匹配到屠宰企业快讯")
        return None
    out, dates = {}, []
    # 先按 省份 → 品类 顺序抓取，保证输出的行序与 _MY_SPECS 的展示顺序一致
    for prov, _ in _MY_PROV:
        for cat in _MY_CATS:
            picked_url = picked.get((prov, cat))
            if not picked_url:
                continue
            url = picked_url[1]
            try:
                ah = fetch(url, timeout=25, headers=MYSTEEL_HEADERS)
            except Exception as e:
                print("[warn] Mysteel 快讯抓取失败 %s:" % url, e)
                continue
            got = _my_parse_article(ah, prov, cat)
            if not got:
                continue
            dm = re.search(r"(\d{1,2})月(\d{1,2})日", ah)
            if dm:
                dates.append((int(dm.group(1)), int(dm.group(2))))
            for sp in _MY_SPECS.get((prov, cat), []):
                if sp not in got:
                    continue
                val, chg = got[sp]
                out.setdefault(prov, []).append(
                    [_CAT_GROUP[cat], sp, fmt(val),
                     trend_cell(val, round(val - (chg or 0), 2))])
    if not out:
        return None
    dmax = max(dates, key=lambda x: x[0] * 100 + x[1]) if dates else None
    return {"date": "%d/%d" % dmax if dmax else "", "rows": out}


# ===== 鸡网 ckexc（板块一「老母鸡/淘汰鸡」横向参照源 v2，替代 meat360）=====
# meat360 全站反爬（本机与云端均 403/502）弃用；ckexc 实测可直连。
# ckexc 每日发《YYYY-M-DD 全国淘汰鸡参考价格》：红鸡/粉鸡分省分地市，活禽/屠宰双口径+涨跌，
# 粒度到地级市（菏泽/聊城/临沂/邯郸…全有），首页直挂最新一期 /quote/show-<id>.html。
# 主表仍以鸡病专业网为准，ckexc 仅作参照表展示 + 跨源价差提示；抓取失败自动跳过。
CKEXC = "https://www.ckexc.com"
CKEXC_HEADERS = {"User-Agent": PC_UA["User-Agent"]}


def fetch_ckexc(url, times=3):
    """鸡网无反爬，桌面 UA 直连即可"""
    for i in range(times):
        try:
            return fetch(url, headers=CKEXC_HEADERS)
        except Exception as e:
            print("[warn] 鸡网ckexc 抓取失败(%d/%d) %s: %s" % (i + 1, times, url, e))
    return None


def ckexc_latest():
    """鸡网首页找最新一期《全国淘汰鸡参考价格》→ (文章url, 日期串如"9/4"，失败=(None,""))"""
    html = fetch_ckexc(CKEXC + "/")
    if not html:
        return None, ""
    best = None
    for m in re.finditer(
            r'href="(https://www\.ckexc\.com/quote/show-(\d+)\.html)"[^>]*title="([^"]*全国淘汰鸡参考价格[^"]*)"',
            html):
        if best is None or int(m.group(2)) > best[0]:
            best = (int(m.group(2)), m.group(1), m.group(3))
    if not best:
        return None, ""
    _d = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", best[2])
    return best[1], ("%d/%d" % (int(_d.group(2)), int(_d.group(3))) if _d else "")


def parse_ckexc(html):
    """鸡网《全国淘汰鸡参考价格》→ {鸡种: [(省, 地区, 活禽, 活禽较昨, 屠宰, 屠宰较昨)...]}
    页面为单 <table>，结构：<th colspan=6>鸡种名</th> 分段 → 列头(省/地区/活禽/涨跌/屠宰/涨跌)
    → 数据行（省名用 rowspan 合并，缺值补空）。"""
    out = {}
    mt = re.search(r"<table.*?</table>", html, flags=re.S)
    if not mt:
        return out
    cur_type, cur_prov = None, None
    for r in re.findall(r"<tr[^>]*>(.*?)</tr>", mt.group(0), flags=re.S):
        mh = re.search(r'<th colspan="6"[^>]*>(.*?)</th>', r, flags=re.S)
        if mh:                                   # 鸡种分段头：红鸡 / 粉鸡 / 小型屠宰…
            t = re.sub(r"<[^>]+>", "", mh.group(1)).strip()
            if t and t not in ("省",):
                cur_type, cur_prov = t, None
            continue
        cells = []
        for td in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, flags=re.S):
            t = re.sub(r"<[^>]+>", "", td).strip().replace("　", "").replace("&nbsp;", "")
            cells.append(t)
        if not cells or cur_type is None:
            continue
        if cells[0] == "省" or "地区" in cells[:3]:
            continue                             # 列头行
        if re.search(r'<t[dh][^>]*rowspan', r):  # 新省开始（rowspan 合并）
            cur_prov = cells[0]
            cells = cells[1:]
        if not cur_prov:
            continue
        while len(cells) < 5:
            cells.append("")
        area, live, lc, sl, sc = cells[:5]
        out.setdefault(cur_type, []).append((cur_prov, area, live, lc, sl, sc))
    return out


def ck_cell(v):
    """ckexc 涨跌文本 → 页面单元格 {t, dir}（国内习惯：红涨/绿跌/平灰；"—"=无报价）"""
    s = str(v or "").strip()
    if not s or s in ("-", "－", "—", "−"):
        return {"t": "—", "dir": "flat"}
    if s in ("0", "0.00", "持平"):
        return {"t": "平", "dir": "flat"}
    if s.startswith(("+", "＋")):
        return {"t": s.lstrip("+＋"), "dir": "up"}
    if s.startswith(("-", "－", "—", "−")):
        return {"t": s, "dir": "down"}
    try:
        f = float(s)
    except ValueError:
        return {"t": s, "dir": "flat"}
    return {"t": s, "dir": "down" if f < 0 else ("up" if f > 0 else "flat")}


# ---------- 工具 ----------

def avg_of(prices):
    """'5.07-5.14' 或 '5.16' 或 5.16 的列表 → 均值"""
    vals = []
    for p in prices:
        if isinstance(p, (int, float)):
            vals.append(float(p))
            continue
        try:
            nums = [float(x) for x in str(p).split("-")]
            vals.append(sum(nums) / len(nums))
        except ValueError:
            pass
    return round(sum(vals) / len(vals), 2) if vals else None


def trend_cell(c, d):
    if c is None or d is None or abs(c - d) < 0.005:
        return {"t": "→", "dir": "flat"}
    diff = round(c - d, 2)
    return {"t": ("↑" if diff > 0 else "↓") + ("%.2f" % abs(diff)), "dir": "up" if diff > 0 else "down"}


def fmt(v):
    return ("%.2f" % v) if isinstance(v, float) else str(v)


def lag_days(ds, today=None):
    """数据日期字符串（M/D、M-D、YYYY-M-D、YYYY年M月D日）距今天数；解析失败返回 None。
    用于「源站是否停更」的判定与标注（2026-10-12 新增：原逻辑只看是否抓到，抓到的旧文也算"新数据"）。"""
    if not ds:
        return None
    m = re.search(r"(?:(\d{4})\s*[-/年])?\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})\s*日?", str(ds).strip())
    if not m:
        return None
    today = today or datetime.date.today()
    y = int(m.group(1)) if m.group(1) else today.year
    try:
        d0 = datetime.date(y, int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    return (today - d0).days


def main():
    today = datetime.date.today()
    date_cn = "%d年%d月%d日" % (today.year, today.month, today.day)
    weekday = "周%s" % "一二三四五六日"[today.weekday()]
    date_short = "%d/%d" % (today.month, today.day)

    with open(os.path.join(HERE, "data.json"), encoding="utf-8") as f:
        d = json.load(f)
    # 快照「上一期」各板块表格：云端网络抖动很常见（2026-10-14 实测一次抖动里
    # 博亚鸡蛋/博亚淘鸡/Mysteel 分割品三处同时 read timeout），而板块表格每轮是
    # **整体重建**的 → 抓失败就等于把上一期好表删掉。故失败时用本快照把上一期原样接回。
    _prev_tables = {i: list(s.get("tables") or []) for i, s in enumerate(d.get("sections") or [])}

    def _carry_prev(sec_i, pred):
        """抓不到当期时，取上一期同源的表原样接回（不改数值、不新增点）。
        cap 追加「沿用上一期」字样但不含「停更」等清退关键词，仍由 src_policy 的新鲜度分级决定去留。"""
        for _t in _prev_tables.get(sec_i, []):
            if pred(_t):
                _t = dict(_t)
                _cap = (_t.get("cap") or "").rstrip("。")
                if "沿用上一期" not in _cap:
                    _t["cap"] = ((_cap + "　|　" if _cap else "")
                                 + "本期未抓到当期更新，沿用上一期；连续多期沿用将按新鲜度分级自动清退。")
                return _t
        return None

    prev_date = d.get("meta", {}).get("date_cn", "")
    state = {}
    state_path = os.path.join(HERE, "state.json")
    if os.path.exists(state_path):
        try:
            with open(state_path, encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            state = {}

    new_state = {}          # 各板块按需写入，最后统一落盘 state.json

    # ===== 统一去旧稿：清除各板块历史遗留的 banner/summary 静态文本 =====
    # 背景：banner/summary 曾由旧版代码或 AI 精修"一次性写入后被逐日继承"（如公鸡板块
    # "8/27 中元节"、鸡蛋板块"9/1"），是过期研判残留的载体。每日先清空，随后各板块
    # 按当日抓取结果重建（当前仅三黄鸡板块在下方重建块写回），保证数据内不再滞留旧稿。
    _stale_flds = []
    for _s in (d.get("sections") or []):
        for _f in ("banner", "summary"):
            if _f in _s and isinstance(_s[_f], (str, list)):
                _stale_flds.append((_s.get("title", "?")[:8], _f))
                _s.pop(_f, None)
    if _stale_flds:
        print("已清除历史遗留静态稿 %d 处: %s" % (len(_stale_flds), _stale_flds))

    arts = collect_articles()
    print("共发现文章链接 %d 篇" % len(arts))

    # ===== 新闻资讯（禽类相关） =====
    news_items = collect_news()
    d["news"] = news_items
    print("抓取到禽类相关新闻 %d 条" % len(news_items))
    # 网站会提前发布"明日"文章，只选日期不超过今天的，防止取到预测价
    max_key = (today.month, today.day)

    def latest(arts, keyword, exclude=None, max_key=max_key):
        """找标题含关键词的最新文章（按标题日期排序，且日期不超过今天）"""
        best = None
        for title, url in arts.items():
            if keyword not in title:
                continue
            if exclude and any(x in title for x in exclude):
                continue
            m = re.search(r"(\d+)月(\d+)日", title)
            key = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
            if key > max_key:
                continue
            if best is None or key > best[0]:
                best = (key, title, url)
        return best

    # ===== 板块一/二：鸡蛋+淘汰鸡汇总 =====
    summary_rows, sum_date = [], ""
    hit = latest(arts, "鸡蛋、淘汰鸡价格汇总")
    if hit:
        _, title, url = hit
        m = re.search(r"(\d+)月(\d+)日", title)
        sum_date = "%s/%s" % (m.group(1), m.group(2)) if m else ""
        try:
            summary_rows = parse_summary(fetch(url))
        except Exception as e:
            print("[warn] 汇总文章抓取失败:", e)
    print("汇总文章(%s)解析行数: %d" % (sum_date, len(summary_rows)))

    cull_by_prov, egg_by_prov = {}, {}
    for r in summary_rows:
        if r["cull"]:
            cull_by_prov.setdefault(r["prov"], []).append(float(r["cull"]))
        if r["egg"]:
            egg_by_prov.setdefault(r["prov"], []).append(r["egg"])

    if cull_by_prov:
        cull_avg = {p: round(sum(v) / len(v), 2) for p, v in cull_by_prov.items()}
        cities_by_prov = {}
        for r in summary_rows:
            if r["cull"]:
                cities_by_prov.setdefault(r["prov"], []).append(r["city"])
        rows = []
        prev_cull = state.get("cull", {})
        for p in sorted(cull_avg, key=lambda x: -cull_avg[x]):
            note = "/".join(cities_by_prov[p][:4])
            if len(cities_by_prov[p]) > 4:
                note += "等%d市" % len(cities_by_prov[p])
            rows.append([p, fmt(cull_avg[p]), trend_cell(cull_avg[p], prev_cull.get(p)), note])
        sec = d["sections"][0]
        sec["tables"] = [{"headers": ["省份", "淘汰鸡价（元/斤）", "环比", "覆盖城市（同价）"], "rows": rows}]
        n = len(cull_avg)
        hi = max(cull_avg, key=cull_avg.get); lo = min(cull_avg, key=cull_avg.get)
        sec["analysis"] = {
            "title": "📊 淘汰鸡分析（%s数据）" % sum_date,
            "up": ["全国%d省均价区间 %s-%s 元/斤，高价区%s、低价区%s，价差%s元/斤"
                   % (n, fmt(min(cull_avg.values())), fmt(max(cull_avg.values())),
                      hi, lo, fmt(cull_avg[hi] - cull_avg[lo])),
                   "数据源：鸡病专业网汇总（红羽粉羽混合口径）"],
            "ref": ["价差%s元%s——跨区调运先核算运费再比价"
                    % (fmt(cull_avg[hi] - cull_avg[lo]),
                       "（>0.5元/斤有空间）" if cull_avg[hi] - cull_avg[lo] > 0.5 else ""),
                   "蛋价走弱时养殖端淘汰量增加，屠企压价空间大，逢低分批接"],
        }
        sec["tag"] = "数据日期 %s · 鸡病专业网 · 云端自动更新" % sum_date

    if egg_by_prov:
        egg_avg = {p: avg_of(v) for p, v in egg_by_prov.items()}
        trend_by_prov = {}
        for r in summary_rows:
            if r["trend"]:
                cur = trend_by_prov.setdefault(r["prov"], {"down": 0, "up": 0, "flat": 0})
                cur[r["trend"]] = cur.get(r["trend"], 0) + 1
        rows = []
        prev_egg = state.get("egg", {})
        for p in sorted(egg_avg, key=lambda x: -egg_avg[x]):
            t = trend_by_prov.get(p, {})
            mood = max(t, key=t.get) if t else "flat"
            mood_txt = {"down": "落为主", "up": "涨为主", "flat": "稳为主"}[mood]
            rows.append([p, fmt(egg_avg[p]), trend_cell(egg_avg[p], prev_egg.get(p)), mood_txt])
        sec = d["sections"][1]
        sec["tables"][0] = {"headers": ["主产省", "鸡蛋均价（元/斤）", "环比", "当日氛围"], "rows": rows}
        # 涨跌家数按「环比列」统计（与表格 trend_cell 同口径）。
        # 原先按源文「当日氛围」统计，会出现表格 7 省标 ↑ 而文案写「0省涨」的自相矛盾。
        _ed = {"up": 0, "down": 0, "flat": 0}
        for p in egg_avg:
            _ed[trend_cell(egg_avg[p], prev_egg.get(p))["dir"]] += 1
        downs, ups = _ed["down"], _ed["up"]
        sec["analysis"] = {
            "title": "📊 鸡蛋分析（%s数据）" % sum_date,
            "up": ["今日主产省蛋价环比：%d省涨、%d省落、%d省持平，均价区间 %s-%s 元/斤"
                   % (ups, downs, _ed["flat"], fmt(min(egg_avg.values())), fmt(max(egg_avg.values()))),
                   "数据源：鸡病专业网汇总（环比＝各省均价与上一交易日比较）"],
            "ref": ["蛋价与淘汰鸡存在传导：蛋价走弱→养殖端淘汰意愿增强",
                    "蛋价连续上涨→延淘惜售，淘汰鸡供应收紧"],
        }
        sec["tag"] = "数据日期 %s · 鸡病专业网 · 云端自动更新" % sum_date

    # ===== 板块二补充：博亚和讯《全国部分地区鸡蛋出场价格》→ 销区蛋价【日更】 =====
    # 2026-10-14：原「主销区」表是**已停更的 mffb** 遗留快照（8/28 后再无新文，
    # 且该表无 cap，未被 src_policy 的整表规则扫到）→ 按用户「停更源一律清退、
    # 补日更源」改用博亚和讯分省出场价里的销区城市重建。抓不到则整表不出现。
    eg_url, eg_date = boyar_egg_latest()
    eg_rows_raw = []
    if eg_url:
        _eh = boyar_fetch(eg_url)
        eg_rows_raw = parse_boyar_egg(_eh) if _eh else []
        if not eg_rows_raw:
            print("[warn] 博亚和讯鸡蛋出场价：%d 次重试后仍未取到 %s" % (BOYAR_RETRY, eg_url))
    print("博亚和讯鸡蛋(%s): %d 行" % (eg_date, len(eg_rows_raw)))
    if eg_rows_raw:
        xq_rows = []
        for p in EGG_XIAOQU:
            _rs = [r for r in eg_rows_raw if r["prov"] == p]
            if not _rs:
                continue
            _vals, _chgs, _cities, _breeds = [], [], [], set()
            for r in _rs:
                try:
                    _vals.append(float(r["cur"]))
                except ValueError:
                    continue
                _chgs.append(float(r["chg"] or 0))
                _cities.append(r["city"].rstrip("市区州"))
                _breeds.add(r["breed"])
            if not _vals:
                continue
            _avg = round(sum(_vals) / len(_vals), 2)
            note = "%s（%s）" % ("/".join(_cities[:5]) + ("等%d市" % len(_cities) if len(_cities) > 5 else ""),
                                 "/".join(sorted(_breeds)))
            xq_rows.append([p.rstrip("市省"), fmt(_avg), boyar_trend(_chgs), note])
        if xq_rows:
            sec = d["sections"][1]
            keep = [t for t in sec.get("tables", [])
                    if "主销区" not in " ".join(str(x) for x in (t.get("headers") or []))]
            sec["tables"] = keep + [{
                "headers": ["主销区", "均价（元/斤）", "涨跌", "覆盖市场"],
                "rows": xq_rows,
                "cap": ("鸡蛋销区均价【日更 %s】· 源：%s（原 mffb 主销区表已因停更清退）"
                        % (eg_date or "?", EGG_XIAOQU_SRC)),
            }]
            sec["tag"] = "%s　|　博亚和讯蛋价 %s" % (sec.get("tag", ""), eg_date or "?")
    else:
        # 抓不到时**不删表**：若上一期已是博亚销区表则沿用；只有页面上还挂着
        # 已停更 mffb 的遗留「主销区」表（cap 无「博亚和讯」）才清退，避免 8/28 陈价继续挂着。
        sec = d["sections"][1]
        _xq = (lambda t: "主销区" in " ".join(str(x) for x in (t.get("headers") or [])))
        _carried = _carry_prev(1, lambda t: _xq(t) and "博亚和讯" in (t.get("cap") or ""))
        sec["tables"] = [t for t in sec.get("tables", []) if not _xq(t)]
        if _carried:
            sec["tables"].append(_carried)
            sec["tag"] = "%s　|　博亚和讯蛋价（沿用上一期）" % (sec.get("tag", ""))
            print("博亚和讯鸡蛋: 未抓到当期 → 沿用上一期销区表")
        else:
            print("博亚和讯鸡蛋: 未抓到当期文章 → 已清退旧 mffb「主销区」表")

    # ===== 板块一补充：鸡网 ckexc 横向参照源（红鸡·山东及周边地市）=====
    # 主表不动，参照表并列展示；抓取/解析失败自动跳过，不影响其他板块
    ck_rows, ck_date = {}, ""
    ck_url, ck_date = ckexc_latest()
    if ck_url:
        _html = fetch_ckexc(ck_url)
        if _html:
            try:
                ck_rows = parse_ckexc(_html)
            except Exception as e:
                print("[warn] 鸡网ckexc 解析失败:", e)
    print("鸡网ckexc 淘汰鸡参考(%s): %s" % (ck_date, {k: len(v) for k, v in ck_rows.items()}))
    _red = ck_rows.get("红鸡", [])
    # 鸡网 ckexc 是「本地可抓、GitHub Actions 机房 IP 被拒」的源（云端每次必败），
    # 抓不到时必须沿用上一期，否则板块一主表整体重建会连带把这张表删掉。
    _ck_carried = _carry_prev(0, lambda t: (t.get("headers") or [""])[0] == "地市")
    if _red:
        # 优先山东全部地市；山东缺数据时退回周边主产省代表市（控制行数、便于手机浏览）
        sd = [r for r in _red if r[0] == "山东"]
        if not sd:
            picked, seen = [], set()
            for r in _red:
                if r[0] in ("河北", "河南", "江苏", "辽宁", "山西", "吉林") and r[0] not in seen:
                    seen.add(r[0])
                    picked.append(r)
                if len(picked) >= 10:
                    break
            sd = picked
            _area_note = "周边主产省代表市（山东缺当日数据）"
        else:
            _area_note = "山东全部地市"
        if sd:
            sec = d["sections"][0]
            keep = [t for t in sec.get("tables", []) if "鸡网红鸡" not in (t.get("cap") or "")]
            sec["tables"] = keep + [{
                "cap": "鸡网红鸡淘汰鸡参照 · %s（%s数据）· 活禽/屠宰双口径，与鸡病专业网交叉比对"
                       % (_area_note, ck_date),
                "headers": ["地市", "活禽价", "较昨", "屠宰价", "较昨"],
                "rows": [[r[1], r[2], ck_cell(r[3]), r[4], ck_cell(r[5])] for r in sd],
            }]
            sec["tag"] = "%s　|　鸡网红鸡参照 %s" % (sec.get("tag", ""), ck_date)
            # 跨源比对：ckexc 山东活禽均值 vs 鸡病专业网山东均价，差 ≥0.3 提示核价
            try:
                vals = [float(r[2]) for r in sd if re.fullmatch(r"\d+\.\d{1,2}", str(r[2]).strip())]
                if vals and cull_avg.get("山东"):
                    mid = round(sum(vals) / len(vals), 2)
                    diff = mid - cull_avg["山东"]
                    if abs(diff) >= 0.3 and "up" in sec.get("analysis", {}):
                        sec["analysis"]["up"].append(
                            "⚠ 跨源分歧：鸡网(ckexc) 山东红鸡活禽均值 %.2f 与鸡病专业网山东均价 %.2f 相差 %.2f 元/斤，下单前建议电话核价"
                            % (mid, cull_avg["山东"], diff))
            except Exception as e:
                print("[warn] 鸡网ckexc 跨源比对失败:", e)
    elif _ck_carried:
        # 云端抓不到 ckexc：把上一期红鸡参照表原样接回，由新鲜度分级决定何时清退
        sec = d["sections"][0]
        sec["tables"] = [t for t in sec.get("tables", [])
                         if (t.get("headers") or [""])[0] != "地市"] + [_ck_carried]
        print("鸡网ckexc 抓取失败 → 沿用上一期红鸡参照表（云端机房 IP 被源站拒，本地可抓）")

    # ===== 板块一补充：博亚和讯「淘鸡分省出场价」（日更·19省·含涨跌，与主表交叉核验）=====
    # 主表（鸡病专业网汇总，11省）保持不动，本表是粒度更细、覆盖更广的分省出场价（自带涨跌列）。
    # 抓取/解析失败自动跳过，不影响任何现有板块。
    bj_url, bj_date = boyar_latest()
    bj_rows_raw = []
    bj_avg = {}          # 预置：供下方「老母鸡速览卡」优先取用（抓失败时回退鸡病专业网）
    if bj_url:
        _bh = boyar_fetch(bj_url)
        bj_rows_raw = parse_boyar_tj(_bh) if _bh else []
        if not bj_rows_raw:
            print("[warn] 博亚和讯淘鸡出场价：%d 次重试后仍未取到 %s" % (BOYAR_RETRY, bj_url))
    bj_provs = sorted({r["prov"] for r in bj_rows_raw})
    print("博亚和讯淘鸡(%s): %d 行 / %d 省" % (bj_date, len(bj_rows_raw), len(bj_provs)))
    if len(bj_provs) >= 5:
        _bjprice, _bjcity, _bjchg = {}, {}, {}
        for r in bj_rows_raw:
            try:
                _bjprice.setdefault(r["prov"], []).append(float(r["cur"]))
            except ValueError:
                continue
            _bjchg.setdefault(r["prov"], []).append(float(r["chg"] or 0))
            _bjcity.setdefault(r["prov"], []).append(r["city"])
        bj_avg = {p: round(sum(v) / len(v), 2) for p, v in _bjprice.items()}
        bj_out = []
        for p in sorted(bj_avg, key=lambda x: -bj_avg[x]):
            cs = _bjcity[p]
            note = "/".join(cs[:4]) + ("等%d市" % len(cs) if len(cs) > 4 else "")
            bj_out.append([p, fmt(bj_avg[p]), boyar_trend(_bjchg.get(p, [])), note])
        sec = d["sections"][0]
        keep = [t for t in sec.get("tables", []) if "博亚和讯" not in (t.get("cap") or "")]
        sec["tables"] = keep + [{
            "cap": "淘汰鸡分省出场价 · 全部%d省（%s数据）· 博亚和讯日更，含涨跌，与上表鸡病专业网交叉核验"
                   % (len(bj_provs), bj_date),
            "headers": ["省份", "出场价（元/斤）", "涨跌", "覆盖城市"],
            "rows": bj_out,
        }]
        sec["tag"] = "%s　|　博亚和讯 %s" % (sec.get("tag", ""), bj_date)
        # 主表（鸡病专业网汇总）的淘汰鸡段更新偏慢（实测多期数值不变、只有蛋价在动），
        # 补 cap 明确来源与滞后，避免被当成当日行情 → 以本表（博亚分省出场价）为准。
        for _t in sec.get("tables", []):
            if (_t.get("headers") or [""])[0] == "省份" and "博亚和讯" not in (_t.get("cap") or ""):
                _t["cap"] = ("来源：鸡病专业网《鸡蛋、淘汰鸡价格汇总》· 数据日期 %s。"
                             "⚠ 该栏淘汰鸡段更新偏慢（多期同值、只有蛋价在日更），"
                             "当日真实行情请以下方「博亚和讯分省出场价」表为准。" % (sum_date or "?"))
        # 板块分析里补一条「当日真实行情」（analysis 在博亚抓取前已生成，此处追加）
        if cull_avg and "up" in sec.get("analysis", {}):
            _bn = round(sum(bj_avg.values()) / len(bj_avg), 2)
            _cn = round(sum(cull_avg.values()) / len(cull_avg), 2)
            sec["analysis"]["up"].append(
                "【当日真实行情·博亚和讯 %s】%d省均价 %s 元/斤；上表鸡病专业网汇总报 %s 元/斤（%d省），"
                "两者相差 %s 元/斤——该栏淘汰鸡段多期不更新，接货价请以本行为准"
                % (bj_date, len(bj_avg), fmt(_bn), fmt(_cn), len(cull_avg), fmt(abs(_bn - _cn))))
        # 跨源比对：博亚山东出场价 vs 鸡病专业网山东均价（阈值 0.12：淘汰鸡日均波动本就 0.1~0.2，
        # 原阈值 0.3 太高、实测从未触发，等于没有这道闸）
        try:
            if bj_avg.get("山东") and cull_avg.get("山东") and "up" in sec.get("analysis", {}):
                _bd = bj_avg["山东"] - cull_avg["山东"]
                if abs(_bd) >= 0.12:
                    sec["analysis"]["up"].append(
                        "⚠ 山东核价：博亚和讯出场价 %.2f vs 鸡病专业网均价 %.2f，相差 %.2f 元/斤，下单前请电话核实"
                        % (bj_avg["山东"], cull_avg["山东"], _bd))
        except Exception as e:
            print("[warn] 博亚和讯跨源比对失败:", e)
    else:
        # 抓不到（搜索页 read timeout 是云端常见抖动）→ **不删表**，沿用上一期博亚分省表；
        # 数据日期写在 cap 里，交由 src_policy 的新鲜度分级决定是保留标滞后还是清退。
        _carried = _carry_prev(0, lambda t: "博亚和讯" in (t.get("cap") or ""))
        if _carried:
            sec = d["sections"][0]
            sec["tables"] = [t for t in sec.get("tables", [])
                             if "博亚和讯" not in (t.get("cap") or "")] + [_carried]
            sec["tag"] = "%s　|　博亚和讯（沿用上一期）" % (sec.get("tag", ""))
            print("博亚和讯淘鸡: 未抓到当期 → 沿用上一期分省表")

    # ===== 板块二补充：mffb 重点销区市场蛋价（销区口径，与又鸟蛋/小鲜农价同类） =====
    mffb_egg_url, mffb_egg_title = mffb_latest("鸡蛋价格行情")
    print("mffb 鸡蛋文章: %s" % mffb_egg_title)
    if mffb_egg_url:
        try:
            egg_html = fetch_retry(mffb_egg_url)
            mffb_egg = parse_mffb_egg(egg_html) if egg_html else []
        except Exception as e:
            print("[warn] mffb 鸡蛋抓取失败:", e)
            mffb_egg = []
        print("mffb 销区市场解析: %d 个" % len(mffb_egg))
        if len(mffb_egg) < 3:
            print("[info] mffb 销区数据不足 3 个市场，本次跳过该表")
        if len(mffb_egg) >= 3:
            sec = d["sections"][1]
            keep = [t for t in sec.get("tables", []) if t["headers"][0] != "重点销区"]
            sec["tables"] = keep + [{
                "headers": ["重点销区", "蛋价（元/斤）", "当日氛围"],
                "rows": [[r["city"], {"t": r["price"], "dir": r["trend"]},
                          {"t": r["txt"], "dir": r["trend"]}] for r in mffb_egg]}]
            sec["tag"] = "%s　|　销区市场 %s · mffb" % (sec.get("tag", ""), mffb_egg_title[-6:])
            dips = [r["city"] for r in mffb_egg if r["trend"] == "down"]
            if dips and "ref" in sec.get("analysis", {}):
                sec["analysis"]["ref"].append(
                    "销区跌价市场：%s，采购可优先比价这些区域" % "/".join(dips[:6]))

    # ===== 板块五：白羽肉鸡 =====
    broiler_rows, bro_date = [], ""
    avg, mffb_jz = None, []   # 预置，供后面速览卡使用（抓取失败时不致报错）
    hit = latest(arts, "肉鸡行情分析")
    if hit:
        _, title, url = hit
        m = re.search(r"(\d+)月(\d+)日", title)
        bro_date = "%s/%s" % (m.group(1), m.group(2)) if m else ""
        try:
            broiler_rows = parse_broiler(fetch(url))
        except Exception as e:
            print("[warn] 白羽文章抓取失败:", e)
    print("白羽文章(%s)解析行数: %d" % (bro_date, len(broiler_rows)))
    if broiler_rows:
        sd = [r for r in broiler_rows if r["city"] in
              ("滨州", "德州", "菏泽", "济南", "济宁", "莱芜", "临沂", "聊城", "日照", "青岛",
               "威海", "潍坊", "莱阳", "招远", "烟台", "东营", "淄博", "泰安", "枣庄")]
        other = [r for r in broiler_rows if r not in sd]
        tables = [{"headers": ["山东各市", "棚前收购价（元/斤）", "环比"],
                   "rows": [[r["city"], r["price"], r["cell"]] for r in sd]}]
        if other:
            tables.append({"headers": ["省外地区", "棚前收购价（元/斤）", "环比"],
                           "rows": [[r["city"], r["price"], r["cell"]] for r in other]})
        sec = d["sections"][4]
        # 只保留冻结的旧表（品类参考等），动态表每次重建，保证重复运行不累积
        keep = [t for t in sec.get("tables", [])
                if t["headers"][0] not in ("山东各市", "省外地区")]
        sec["tables"] = tables + keep
        avg = avg_of([r["price"] for r in sd]) if sd else None
        sec["analysis"] = {
            "title": "📊 白羽肉鸡分析（%s数据）" % bro_date,
            "up": ["山东棚前均价 %s 元/斤（%d个市），省外另见下表" % (fmt(avg) if avg else "—", len(sd)),
                   "数据源：鸡病专业网"],
            "ref": ["%s" % ("棚前价低于3.3元时，屠企冻品成本有优势，可分批建仓" if avg and avg < 3.3
                            else "高位按需采购，不追高，关注屠企开工与走货节奏")],
        }
        sec["tag"] = "数据日期 %s · 鸡病专业网 · 云端自动更新" % bro_date

    # ===== 板块五补充：mffb 主产区棚前分市（含当日涨跌幅度） =====
    mffb_jz_url, mffb_jz_title = mffb_latest("鸡价行情")
    print("mffb 鸡价文章: %s" % mffb_jz_title)
    if mffb_jz_url:
        try:
            jz_html = fetch_retry(mffb_jz_url)
            mffb_jz = parse_mffb_broiler(jz_html) if jz_html else []
        except Exception as e:
            print("[warn] mffb 鸡价抓取失败:", e)
            mffb_jz = []
        print("mffb 白羽分市解析: %d 行" % len(mffb_jz))
        if mffb_jz:
            sec = d["sections"][4]
            keep = [t for t in sec.get("tables", []) if t["headers"][0] != "产区"]
            # 山东已在「山东各市」表全量展示，这里每产区取前 5 市，避免表格过长
            per_region, order = {}, []
            for r in mffb_jz:
                per_region.setdefault(r["region"], [])
                if r["region"] not in order:
                    order.append(r["region"])
                if len(per_region[r["region"]]) < 5:
                    per_region[r["region"]].append(r)
            rows = []
            for reg in order:
                for r in per_region[reg]:
                    if r["chg"] == "持平":
                        dr, txt = "flat", "—"
                    elif r["chg"] == "下滑":
                        dr, txt = "down", "-" + (r["amt"] or "")
                    else:
                        dr, txt = "up", "+" + (r["amt"] or "")
                    rows.append([r["region"], r["city"], {"t": r["price"], "dir": dr},
                                 {"t": txt, "dir": dr}])
            # mffb 常在周末/节假日停更，抓到的可能是几天前那一期。日期必须标出来，
            # 否则这张「分产区棚前价」表会被当成当日行情读。
            jz_date = title_date(mffb_jz_title)
            jz_lag = 0
            if jz_date:
                mm, dd = jz_date.split("/")
                try:
                    jz_lag = max(0, (today - datetime.date(today.year, int(mm), int(dd))).days)
                except ValueError:
                    jz_lag = 0
            sec["tables"] = keep + [{
                "headers": ["产区", "城市", "棚前价（元/斤）", "当日涨跌"], "rows": rows,
                "cap": "mffb 各产区棚前价%s · 每产区前 5 市" % ((" " + jz_date) if jz_date else ""),
            }]
            down_n = sum(1 for r in mffb_jz if r["chg"] == "下滑")
            sec["tag"] = "%s　|　mffb%s 分市 %d 市（%d 跌）%s" % (
                sec.get("tag", ""), (" " + jz_date) if jz_date else "",
                len(mffb_jz), down_n,
                "（源站滞后期）" if jz_lag >= 2 else "")

    # ===== 板块五补充：Mysteel（我的钢铁网·鸡业）白羽分市场报价 =====
    try:
        ms = parse_mysteel_broiler(fetch_retry(MYSTEEL_BY))
    except Exception as e:
        print("[warn] Mysteel 抓取失败:", e)
        ms = None
    if ms and ms.get("rows"):
        print("Mysteel 白羽分市场: %d 行（%s）" % (len(ms["rows"]), ms.get("date")))
        sec = d["sections"][4]
        keep = [t for t in sec.get("tables", []) if t["headers"][0] != "Mysteel市场"]
        rows = [[r["market"], r["src"], r["spec"] + "斤", r["price"]] for r in ms["rows"]]
        sec["tables"] = keep + [{"headers": ["Mysteel市场", "鸡源类型", "规格", "价格（元/斤）"],
                                 "rows": rows}]
        sec["tag"] = "%s　|　Mysteel %s" % (sec.get("tag", ""), ms.get("date", "")[5:] or "")
    else:
        print("Mysteel 白羽分市场: 无数据")

    # ===== 板块五补充：鸡病专业网《山东地区X-X日鸡苗-肉鸡冻品市场动态速览》→ 分割品【日更】 =====
    # 用户 2026-10-09 要求：白羽冻品行情必须每天收集，不能一周才一次（信息滞后）。
    # 本表每日随云端三班抓取刷新；辽宁口径仍由周一 AI 校准的周更表承载（表头已标注）。
    hit_fz = frozen_pick(arts)
    fz_date, fz_rows = "", []      # 预置：未命中时后面的滞后判定也要能安全引用
    if hit_fz:
        _, fz_title, fz_url = hit_fz
        try:
            fz_html = fetch(fz_url)
            fz_date = frozen_pub_date(fz_html)
            fz_rows = parse_frozen_sd(fz_html)
        except Exception as e:
            print("[warn] 冻品速览抓取失败:", e)
        print("冻品速览(%s): %d 项" % (fz_date, len(fz_rows)))
        if fz_rows:
            prev_fz = state.get("frozen_sd", {})
            fz_tbl = []
            for r in fz_rows:
                pv = prev_fz.get(r["name"])
                fz_tbl.append([r["group"], r["label"],
                               "%s-%s" % (fmt(r["lo"]), fmt(r["hi"])),
                               fmt(r["mid"]), trend_cell(r["mid"], pv)])
            sec = d["sections"][4]
            # 只移除本块自己那张《山东成交区间》表，不要连带删掉 Mysteel 的日更表
            # （删了的话只能靠下一段补回，一旦那段抓失败就整块消失）
            keep = [t for t in sec.get("tables", [])
                    if "山东成交区间" not in " ".join(str(x) for x in (t.get("headers") or []))]
            # 插到「周更分割品表」前面，日更与周更紧邻便于对照
            pos = next((i for i, t in enumerate(keep)
                        if (t.get("headers") or [""])[0] == "分割品"), len(keep))
            keep.insert(pos, {
                "headers": ["品类", "规格（山东成交区间·元/公斤）", "成交区间", "区间中值", "较昨日"],
                "rows": fz_tbl,
                "cap": ("白羽分割品·山东冻品成交价【日更 %s】· 源：鸡病专业网《山东地区鸡苗-肉鸡冻品市场动态速览》"
                        "；山东/辽宁分规格中间价见下方 Mysteel 日更表。" % (fz_date or "?")),
            })
            sec["tables"] = keep
            new_state["frozen_sd"] = {r["name"]: r["mid"] for r in fz_rows}
            sec["tag"] = "%s　|　冻品日更 %s" % (sec.get("tag", ""), fz_date or "?")
    else:
        print("冻品速览: 未找到当期文章（跳过，不影响其他板块）")

    # ===== 板块五补充2：Mysteel 山东/辽宁屠宰企业冻品【日更】=====
    # 用户 2026-10-14：白羽分割品「停更的清退、补日更源」。
    # 原「山东/辽宁中价周更表」（headers[0]=="分割品"）其源已停更（小明农牧 9/21 后
    # 无新期、Mysteel 汇总表付费墙）→ 按规则整表清退；改用 Mysteel 分割品栏目当天
    # 山东/辽宁各 5 篇快讯重建，粒度到规格、单位元/公斤，山东/辽宁各一张表。
    mf = mysteel_frozen_daily()
    mf_date = mf["date"] if mf else ""
    # 无论是否抓到，都先清掉遗留的周更表（不能把 9/21 旧值留在页面上）
    # 同时必须移除上一轮插入的 Mysteel 日更表本身：否则本块的 insert 会每次叠加两张，
    # 重跑 N 次就变成 2N 张「品类」表（2026-10-15 线上已累积到 5 张）。
    sec = d["sections"][4]

    def _is_mf(t):
        """是否为本块自己生成的 Mysteel 山东/辽宁中间价日更表"""
        h = " ".join(str(x) for x in (t.get("headers") or []))
        return ("山东中间价（元/公斤）" in h) or ("辽宁中间价（元/公斤）" in h)

    keep = [t for t in sec.get("tables", [])
            if (t.get("headers") or [""])[0] != "分割品" and not _is_mf(t)]

    # 无条件去重：同源的《山东冻品成交价》日更表只保留最后一张。
    # 背景（2026-10-15 线上实测）：Mysteel 抓失败的轮次里，下方 _carried 的旧判据
    # 「"Mysteel" in cap」会连《山东成交价》表一起接回（该表 cap 里带"见下方 Mysteel 日更表"
    # 字样被误命中），于是与 A 块沿用的那张并存 → 页面上同一张表出现两遍。
    # 这里在插入 Mysteel 表之前先归并，无论 A/B 哪块成功失败都不会累积。
    _fz_key = "山东成交区间"
    _fz_idx = [i for i, t in enumerate(keep)
               if _fz_key in " ".join(str(x) for x in (t.get("headers") or []))]
    if len(_fz_idx) > 1:
        for _i in reversed(_fz_idx[:-1]):
            keep.pop(_i)
        print("冻品速览: 清理重复的山东成交价表 %d 张（同源只保留最新一张）" % (len(_fz_idx) - 1))
    if mf and mf["rows"]:
        base = next((i for i, t in enumerate(keep)
                     if (t.get("headers") or [""])[0] == "品类"), -1) + 1
        new_tables = []
        for prov in ("山东", "辽宁"):
            rows = mf["rows"].get(prov)
            if not rows:
                continue
            new_tables.append({
                "headers": ["品类", "规格", "%s中间价（元/公斤）" % prov, "涨跌"],
                "rows": rows,
                "cap": ("白羽分割品·%s冻品中间价【日更 %s】· 源：Mysteel《%s屠宰企业白羽肉鸡"
                        "爪/胸/翅/腿/副产冻品》当日快讯（原文有登录墙，数据取自页头智能摘要+可见表格）；"
                        "仅列当日实际报价规格，未报价项不沿用旧值。" % (prov, mf_date or "?", prov)),
            })
        keep[base:base] = new_tables
        sec["tables"] = keep
        sec["tag"] = "%s　|　分割品日更 %s" % (sec.get("tag", ""), mf_date or "?")
        print("Mysteel 分割品(%s): 山东 %d 行 / 辽宁 %d 行"
              % (mf_date, len(mf["rows"].get("山东", [])), len(mf["rows"].get("辽宁", []))))
    else:
        # 抓不到时**不删表**（云端网络抖动会整页超时，2026-10-14 实测）：
        # 上一期若已是 Mysteel 日更表则原样沿用；只有页面上还挂着旧周更表才除名。
        # 判据必须是 headers（本块自产表才有「山东/辽宁中间价（元/公斤）」列），
        # **不能用 cap 含 "Mysteel"**——《山东成交价》表 cap 里写有"见下方 Mysteel 日更表"，
        # 会被误命中并重复接回（2026-10-15 线上重复表事故的根因）。
        _carried = [t for t in _prev_tables.get(4, []) if _is_mf(t)]
        if _carried:
            for _t in _carried:
                _t = dict(_t)
                _cap = (_t.get("cap") or "").rstrip("。")
                if "沿用上一期" not in _cap:
                    _t["cap"] = ((_cap + "　|　" if _cap else "")
                                 + "本期未抓到当期更新，沿用上一期；连续多期沿用将按新鲜度分级自动清退。")
                keep.append(_t)
            sec["tables"] = keep
            sec["tag"] = "%s　|　分割品日更（沿用上一期）" % (sec.get("tag", ""))
            print("Mysteel 分割品: 未抓到当期 → 沿用上一期 %d 张日更表" % len(_carried))
        else:
            sec["tables"] = keep
            print("Mysteel 分割品: 未抓到当期快讯（已清退停更周更表，不影响其他板块）")

    # ===== 顶栏第4张速览卡「白羽肉鸡」：每日累积历史，卡片才有昨日/3天/7天趋势 =====
    # 之前这张卡由前端临时拼出（只有1个点），导致趋势栏空白、备注无数据源
    if avg is not None:
        cards = d.setdefault("trend_cards", [])
        by = None
        for c in cards:
            if "白羽" in (c.get("name") or ""):
                by = c
                break
        if by is None:
            by = {"name": "白羽肉鸡", "unit": "元/斤",
                  "chart": {"dates": [], "series": [{"name": "山东棚前均价", "values": []}]}}
            cards.append(by)
        ch = by.setdefault("chart", {})
        ch.setdefault("dates", [])
        if not ch.get("series"):
            ch["series"] = [{"name": "山东棚前均价", "values": []}]
        vals = ch["series"][0].setdefault("values", [])
        # 历史不足 3 天时，从鸡病专业网往期文章回填，让趋势立刻可用
        if len(ch["dates"]) < 3:
            try:
                hist = backfill_broiler_history(arts)
            except Exception as e:
                print("[warn] 白羽历史回填失败:", e)
                hist = []
            if hist:
                ch["dates"] = [h[0] for h in hist]
                ch["series"][0]["values"] = [h[1] for h in hist]
                vals = ch["series"][0]["values"]
                print("白羽历史回填 %d 天: %s" % (len(hist), ch["dates"]))
        md = "%d/%d" % (today.month, today.day)

        def _bk(x):
            _m = re.match(r"(\d+)/(\d+)", str(x) or "")
            return (int(_m.group(1)), int(_m.group(2))) if _m else (0, 0)

        # 用鸡病专业网数据日期（bro_date）而非抓取当天写点，避免错位日期造成假涨跌
        point = bro_date or md
        if ch["dates"] and _bk(ch["dates"][-1]) >= _bk(point):
            prev_val = vals[-2] if len(vals) >= 2 else None
            base = ch["dates"][:-1]
            if base and _bk(base[-1]) == _bk(point):
                base = base[:-1]       # 去重：避免出现两个相同数据日
                prev_val = vals[-3] if len(vals) >= 3 else None
            _tail = False          # 覆盖末点：dates/values 都保留头部 N 个
        else:
            prev_val = vals[-1] if vals else None
            base = ch["dates"][-9:]
            _tail = True           # 追加新点：dates/values 都保留尾部 N 个
        ch["dates"] = base[-9:] + [point]
        # 2026-10-15 修复：追加新点时 dates 取的是尾部窗口 dates[-9:]，而 values 原先用
        # _bv[:N] 取头部 → 序列一旦满 10 点，之后每追加一天，日期与数值就整体错位一格，
        # 前端「昨日/3天/7天」标签据此算出假涨跌（曾误报三黄鸡单日跌 1 元）。
        _bv = ch["series"][0]["values"]
        if len(_bv) > len(base):
            _bv = _bv[-len(base):] if _tail else _bv[:len(base)]
        ch["series"][0]["values"] = _bv + [round(avg, 2)]
        # 涨跌方向：当日均价 vs 上一数据日值，阈值与 trend_cell 一致
        _bdir = trend_cell(round(avg, 2), prev_val) if prev_val is not None else {"dir": "flat"}
        by["dir"] = _bdir["dir"]
        # 首日没有历史时，用山东各市的环比幅度推算昨日价，让「较昨日」立刻有值
        if len(ch["dates"]) == 1:
            deltas = []
            for r in sd:
                c = r.get("cell")
                t = c.get("t") if isinstance(c, dict) else None
                m = re.match(r"^([↓↑])([\d.]+)$", t or "")
                if m:
                    v = float(m.group(2))
                    deltas.append(-v if m.group(1) == "↓" else v)
            if deltas:
                prev = round(avg - sum(deltas) / len(deltas), 2)
                yd = today - datetime.timedelta(days=1)
                ch["dates"].insert(0, "%d/%d" % (yd.month, yd.day))
                ch["series"][0]["values"].insert(0, prev)
                print("白羽昨日价(按环比推算): %s" % prev)
        by["price"] = fmt(avg)
        src = "鸡病专业网" + (" + mffb 分产区" if mffb_jz else "")
        by["note"] = ("山东%d市棚前均价（%s数据）· 数据源：%s · 云端每日更新"
                      % (len(sd), bro_date or "当日", src))
        print("白羽速览卡: %s 元/斤，历史 %d 天 %s"
              % (by["price"], len(ch["dates"]), ch["dates"][-3:]))

    # ===== 板块四：各地麻鸡/公鸡产区文章 =====
    region_pats = [
        (r"鲁西南（菏泽）地区麻鸡", "山东菏泽"),
        (r"吉林黄花公鸡", "吉林"),
        (r"西北（陕、甘、宁）地区麻鸡", "西北(陕甘宁)"),
        (r"江苏（苏州）优公、黄花公鸡", "江苏苏州"),
        (r"四川地区麻鸡、黄花公", "四川"),
        (r"山东地区青脚麻鸡", "山东"),
        (r"皖南地区817", "皖南"),
        (r"皖北地区817", "皖北"),
        (r"山西（阳城）麻鸡", "山西阳城"),
        (r"山西（运城）麻鸡", "山西运城"),
    ]
    live_rows = []
    brother_rows = []          # ② 快大类兄弟品种（青脚麻公/麻公/黄花公）= 鸡病专业网侧的三黄鸡参照
    BROTHER_RE = re.compile(r"青脚麻公|麻公鸡|黄花公|麻黄|青麻公")
    for pat, label in region_pats:
        hit = latest(arts, pat)
        if not hit:
            continue
        _, title, url = hit
        m = re.search(r"(\d+)月(\d+)日", title)
        adate = "%s/%s" % (m.group(1), m.group(2)) if m else ""
        try:
            items = parse_region_prices(fetch(url))
        except Exception as e:
            print("[warn] 产区文章失败 %s: %s" % (label, e))
            continue
        for name, price, dirc in items:
            cell = {"t": {"flat": "→", "up": "↑", "down": "↓"}[dirc], "dir": dirc}
            live_rows.append([label, name, price, cell, "数据" + adate])
            if BROTHER_RE.search(name):   # ② 兄弟品种：跨源印证三黄鸡快大类走势
                brother_rows.append([label, name, price, cell, "数据" + adate])
    print("产区文章共解析 %d 行" % len(live_rows))
    # 品种名归一 + 同品种重复行去重：
    # 源文常把引导语写进正文（如「皖南地区麻鸡成鸡方面：…」），解析后品种名会带「方面/行情/价格」
    # 尾巴；同一品种也可能因屠宰/活禽/农贸多口径重复出现，导致同一行连出 3 次。
    def _norm_breed(nm):
        for suf in ("方面", "行情", "价格"):
            if nm.endswith(suf) and len(nm) > len(suf) + 1:
                nm = nm[: -len(suf)]
        return nm
    _seen_r, _ded_rows = set(), []
    for r in live_rows:
        r[1] = _norm_breed(r[1])
        k = (r[0], r[1], r[2])
        if k in _seen_r:
            continue
        _seen_r.add(k)
        _ded_rows.append(r)
    if len(_ded_rows) != len(live_rows):
        print("产区文章去重: %d -> %d 行" % (len(live_rows), len(_ded_rows)))
    live_rows = _ded_rows
    if live_rows:
        sec = d["sections"][3]
        keep_tables = sec.get("tables", [])
        # ⛔ 2026-10-14 停更源清退：不再沿用黑凤公鸡旧行（源停在 10/3/9/6），
        # 黑凤及其冻品批发表已按「停更源不采用、不展示、删除」规则清除。
        live_tables = [{"headers": ["地区", "品种", "棚前价（元/斤）", "环比", "数据日期"],
                        "rows": live_rows}]
        sec["tables"] = live_tables + [t for t in keep_tables[1:]
                                       if "冻品批发价" not in " ".join(str(x) for x in t.get("headers", []))]
        # 角标需要「数据日期」字样才能标出新鲜度（build.py build_badges 按此提取）
        # 各产区源文更新节奏不一（同一天可能混有新老两期），取**最新**数据日期——
        # 与用户「要看最新盘面、别显示旧日期」的诉求一致（2026-10-14）
        _lds = [re.search(r"数据(\d+/\d+)", str(r[4])).group(1)
                for r in live_rows if re.search(r"数据(\d+/\d+)", str(r[4]))]
        _live_date = (max(_lds, key=lambda x: tuple(int(v) for v in x.split("/")))
                      if _lds else "")
        sec["tag"] = ("活禽棚前价 · 数据日期 %s · 数据源：鸡病专业网各产区日报 · 云端自动更新"
                      % _live_date)
        sec["analysis"] = {
            "title": "📊 公鸡分析（各产区标注数据日期）",
            "up": ["今日云端抓取 %d 个产区报价，每行标注数据日期" % len(live_rows),
                   "停更源已清退：黑凤公鸡活禽（源停 10/3、邢台 9/6）及其冻品批发基准（8/27）、"
                   "579小公鸡/黄脚大冠/青脚公鸡冻品（9/21）均无新一期，已按「停更源不采用、"
                   "不展示」规则从本板块移除；如需采购请直接电话询厂实价"],
            "ref": ["麻公/黄花公产区价差大，跨区采购先算运费",
                    "中元节/中秋等节前备货常推高价，节后常有回调"],
        }

    # ===== 顶栏第3张速览卡「公鸡」：改为「各省公鸡棚前价」日更（2026-10-14 用户要求）=====
    # 背景：此卡原为"孤儿卡"——云端从无构建/追加逻辑，走势图停更于 8/28（仅5点），
    # 主价长期停留在农财宝典 9/30 的"浙江矮脚黄公鸡"（活禽鲜销口径），只能靠周一 AI 手改，
    # 导致用户看到 9/30 陈数据。现与淘汰鸡卡同口径：各省公鸡棚前价 → 均价 → 每日追加走势点。
    # 数据源复用板块四已抓到的鸡病专业网各产区日报（live_rows），不新增外部依赖。
    try:
        _krows = []
        for _r in live_rows:
            _nm, _pv, _dt = str(_r[1]), str(_r[2]), str(_r[4])
            if "公" not in _nm:
                continue                        # 只取公鸡类（青脚麻公鸡/大红公鸡/黄花公鸡/优公/黑凤公鸡）
            if "滞后" in _dt or "未日更" in _dt:
                continue                        # 源站未日更项（黑凤等）不参与均价，避免连续同值拖成"假横盘"
            _nums = [float(x) for x in re.findall(r"\d+\.?\d*", _pv)]
            if not _nums:
                continue
            _km = re.search(r"(\d+)/(\d+)", _dt)
            _krows.append((_r[0], _nm, sum(_nums) / len(_nums), _km.group(0) if _km else ""))
        if _krows:
            _kavg = round(sum(x[2] for x in _krows) / len(_krows), 2)
            _kds = [x[3] for x in _krows if x[3]]
            # 多产区源文更新节奏不一，取最新日期（与板块四角标同口径，避免两处日期打架）
            _kdate = (max(_kds, key=lambda x: tuple(int(v) for v in x.split("/")))
                      if _kds else "")
            cards = d.setdefault("trend_cards", [])
            ck = None
            for c in cards:
                if "公鸡" in (c.get("name") or ""):
                    ck = c
                    break
            if ck is None:
                ck = {"name": "公鸡", "unit": "元/斤"}
                cards.append(ck)
            ch = ck.setdefault("chart", {})
            _SNAME = "各省公鸡棚前均价"
            # 口径切换（浙江矮脚黄公鸡 → 各省公鸡棚前均价）时重置序列，两个口径绝不混画
            if not ch.get("series") or (ch["series"][0].get("name") or "") != _SNAME:
                ch["series"] = [{"name": _SNAME, "style": "solid",
                                 "show_points": True, "values": []}]
                ch["dates"] = []
            ch.setdefault("dates", [])
            if not ch["series"][0].get("values"):
                ch["series"][0]["values"] = []
            vals = ch["series"][0]["values"]

            def _kk(x):
                _m = re.match(r"(\d+)/(\d+)", str(x) or "")
                return (int(_m.group(1)), int(_m.group(2))) if _m else (0, 0)

            # 用源站数据日期（非抓取日）写点，避免把昨日数据标成今日造成假涨跌
            point = _kdate or ("%d/%d" % (today.month, today.day))
            if ch["dates"] and _kk(ch["dates"][-1]) >= _kk(point):
                prev_val = vals[-2] if len(vals) >= 2 else None
                base = ch["dates"][:-1]
                if base and _kk(base[-1]) == _kk(point):
                    base = base[:-1]            # 去重：同数据日重跑
                    prev_val = vals[-3] if len(vals) >= 3 else None
                _tail = False          # 覆盖末点：dates/values 均保头部
            else:
                prev_val = vals[-1] if vals else None
                base = ch["dates"][-9:]
                _tail = True           # 追加新点：dates/values 均保尾部
            ch["dates"] = base[-9:] + [point]
            # 2026-10-15 修复：同白羽卡（dates 保尾、values 原先保头 → 满 10 点后逐日错位）
            _kv = ch["series"][0]["values"]
            if len(_kv) > len(base):
                _kv = _kv[-len(base):] if _tail else _kv[:len(base)]
            ch["series"][0]["values"] = _kv + [_kavg]
            # 涨跌：当日均价 vs 上一数据日均价（源站常不标涨跌，故不用多数决），阈值同 trend_cell
            ck["dir"] = (trend_cell(_kavg, prev_val)["dir"] if prev_val is not None else "flat")
            ck["price"] = fmt(_kavg)
            ck["wt_title"] = "近10日数据点（各省公鸡棚前均价）"
            ck["note"] = ("各省公鸡棚前价 %d 产区均价 %s 元/斤（区间 %s-%s，%s数据）· "
                          "数据源：鸡病专业网各产区日报 · 云端每日更新（停更源已清退）"
                          % (len(_krows), fmt(_kavg), fmt(min(x[2] for x in _krows)),
                             fmt(max(x[2] for x in _krows)), point))
            print("公鸡速览卡: %s 元/斤 dir=%s（%s数据，%d产区），历史 %d 天"
                  % (ck["price"], ck["dir"], point, len(_krows), len(ch["dates"])))
        else:
            print("公鸡速览卡: 本期无当日公鸡报价（源站未更新），保持原值")
    except Exception as e:
        print("[warn] 公鸡速览卡更新失败:", e)

    # ===== 板块三：817肉杂（快大类活数据）=====
    rows817, d817 = [], ""
    # 817 聚合变量必须在 if 之外初始化（2026-10-20 事故）：原定义放在 `if rows817:` 内，
    # 而下方板块三 analysis 段无条件引用 _m817big/_m817small →
    # 817 栏当天尚未发文（周末/上午未发）时抛 UnboundLocalError，进程 exit 1，
    # **整轮抓取结果全部作废**（10/20 早上博亚 10/20 已抓到 126 行却一个字段都没写进去）。
    _m817, _m817big, _m817small, _v817 = [], [], [], None
    hit = latest(arts, "肉杂817棚前")
    if hit:
        _, title, url = hit
        m = re.search(r"(\d+)月(\d+)日", title)
        d817 = "%s/%s" % (m.group(1), m.group(2)) if m else ""
        try:
            rows817 = parse_817(fetch(url))
        except Exception as e:
            print("[warn] 817文章抓取失败:", e)
    print("817文章(%s)解析行数: %d" % (d817, len(rows817)))

    # 数据日期校正：该栏为滚动更新页，报价日 = 正文对比基准日 + 1（见 parse_817 注释）
    d817_real = d817
    if rows817:
        _b = rows817[0].get("bday")
        _tmo = re.search(r"(\d+)月(\d+)日", (hit[1] if hit else ""))
        if _b:
            _mo = int(_tmo.group(1)) if _tmo else today.month
            _dd = _b + 1
            if _dd > 31:
                _mo, _dd = (_mo % 12) + 1, 1
            _cand = "%d/%d" % (_mo, _dd)
            _key = lambda s: (int(str(s).split("/")[0]) * 100 + int(str(s).split("/")[1])) \
                if re.match(r"^\d+/\d+$", str(s) or "") else (0)
            if not d817 or _key(_cand) >= _key(d817):
                d817_real = _cand
        if d817_real != d817:
            print("817数据日期校正: 标题 %s → 正文对比基准日+1 = %s" % (d817, d817_real))

    if rows817:
        sec = d["sections"][2]
        # 剔除上次的817表（避免重复运行累积），保留其余冻结表
        keep_tables = [t for t in sec.get("tables", [])
                       if "817" not in t["headers"][0]]
        t817 = {"cap": ("817肉杂棚前价（冻品三黄鸡原料基准·云端每日更新）· 数据源：鸡病专业网"
                        "《部分地区肉杂817棚前价格行情》%s（该栏为滚动更新页，已按正文对比基准日校正）"
                        % d817_real),
                "headers": ["地区（817肉杂·快大类）", "规格", "棚前价（元/斤）", "环比", "数据日期"],
                "rows": [[r["region"], r["spec"], r["price"], r["cell"], "数据" + d817_real]
                         for r in rows817]}
        sec["tables"] = [keep_tables[0], t817] + keep_tables[1:] if keep_tables else [t817]
        sec["tag"] = ("主口径：817肉杂棚前（冻品三黄鸡原料，云端每日更新，数据 %s）；"
                      "农财宝典快大类为分省参照（含麻鸡类，活禽鲜销口径，不与 817 混算）。"
                      % d817_real)

        # ===== 三黄鸡速览卡（口径：817肉杂棚前均价 · 用户 2026-10-16 定）=====
        # 用户原话「三黄鸡指的是 817 鸡杂较多」→ 本卡口径由「农财宝典快大类混合均价」
        # 切换为「817肉杂棚前均价」。原因：农财宝典快大类 8 省样本里 5 省是麻鸡/青脚麻公
        # （10/15 均价 5.09 元/斤），而用户实际采购的 817 系冻品原料（2.8-3.6 斤）当日
        # 均价仅 3.42 元/斤，两者差 1.6 元/斤，绝不可混算。
        # 817 表样本恒定：每天固定 8 个报价点（山东聊城/德州、河南濮阳/开封/新乡、
        # 江苏南通、浙江金华、小规格），无样本进出造成的假跳变。
        _m817, _m817big, _m817small, _v817 = [], [], [], None
        for _r in rows817:
            _nn = [float(x) for x in re.split(r"[-—–~]", str(_r["price"]))
                   if re.match(r"^[0-9.]+$", x)]
            if not _nn:
                continue
            _mid = sum(_nn) / len(_nn)
            _m817.append(_mid)
            if "小规格" in str(_r["region"]):
                _m817small.append(_mid)
            else:
                _m817big.append(_mid)
        if _m817:
            _v817 = round(sum(_m817) / len(_m817), 3)
            cards = d.setdefault("trend_cards", [])
            sh = None
            for c in cards:
                if "三黄" in (c.get("name") or "") or "817" in (c.get("name") or ""):
                    sh = c
                    break
            if sh is None:
                sh = {"name": "三黄鸡", "unit": "元/斤", "chart": {}}
                cards.append(sh)
            ch = sh.setdefault("chart", {})
            # 口径护栏：chart.name 不符即视为口径切换 → 清空序列并补种历史
            if ch.get("name") != CARD817_NAME:
                ch["name"] = CARD817_NAME
                ch["dates"] = [x[0] for x in SEED_817]
                ch["series"] = [{"name": CARD817_NAME, "values": [x[1] for x in SEED_817]}]
                sh["dir"] = "flat"
                print("三黄鸡速览卡: 口径切换为「%s」，序列重置并补种 %d 个历史点"
                      % (CARD817_NAME, len(SEED_817)))
            ch.setdefault("dates", [])
            if not ch.get("series"):
                ch["series"] = [{"name": CARD817_NAME, "values": []}]
            ch["series"][0].setdefault("values", [])
            ch["series"][0]["name"] = CARD817_NAME

            def _sk(x):
                _m = re.match(r"(\d+)/(\d+)", str(x) or "")
                return (int(_m.group(1)), int(_m.group(2))) if _m else (0, 0)

            _pt = d817_real or ("%d/%d" % (today.month, today.day))
            if ch["dates"] and _sk(ch["dates"][-1]) >= _sk(_pt):
                base = ch["dates"][:-1]
                if base and _sk(base[-1]) == _sk(_pt):
                    base = base[:-1]   # 去重：避免出现两个相同数据日
                _tail = False          # 覆盖末点：dates/values 均保头部
            else:
                base = ch["dates"][-9:]
                _tail = True           # 追加新点：dates/values 均保尾部
            ch["dates"] = base[-9:] + [_pt]
            _sv = ch["series"][0]["values"]
            if len(_sv) > len(base):
                _sv = _sv[-len(base):] if _tail else _sv[:len(base)]
            _prev_v = _sv[-1] if _sv else None
            ch["series"][0]["values"] = _sv + [_v817]
            if _prev_v is None:
                sh["dir"] = "flat"
            else:
                _dd817 = round(_v817 - _prev_v, 3)
                sh["dir"] = ("flat" if abs(_dd817) < 0.005
                             else ("up" if _dd817 > 0 else "down"))
            sh["price"] = fmt(_v817)
            sh["note"] = ("冻品流通口径·817肉杂棚前：%d 点均价 %s 元/斤（大规格 %d 点 %s"
                          "、小规格1.5-1.7斤 %s）；数据源：鸡病专业网《部分地区肉杂817棚前"
                          "价格行情》%s；本卡口径＝用户实际采购的 817 系冻品原料，农财宝典"
                          "快大类含麻鸡类、仅作分省参照，不参与本卡。"
                          % (len(_m817), fmt(_v817), len(_m817big),
                             fmt(sum(_m817big) / len(_m817big)) if _m817big else "—",
                             fmt(sum(_m817small) / len(_m817small)) if _m817small else "—",
                             d817_real))
            print("三黄鸡速览卡(817口径): %s 元/斤 dir=%s（%s数据），%d 点，历史 %d 天"
                  % (sh["price"], sh["dir"], _pt, len(_m817), len(ch["dates"])))
    else:
        print("817肉杂: 本期未抓到，速览卡沿用上一期（不重置口径）")

    # ===== 板块三补充：网易·农财宝典「全国鸡价」日报 → 三黄鸡快大类分省价 =====
    seeds = list(NCB_SEEDS)
    if state.get("ncb_seed") and state["ncb_seed"] not in seeds:
        seeds.insert(0, state["ncb_seed"])
    try:
        ncb_aid, ncb_title, ncb_html = ncb_latest(seeds)
    except Exception as e:
        print("[warn] 农财宝典抓取失败:", e)
        ncb_aid, ncb_html = None, None
    ncb_date = ""
    rows = []          # 预置：农财宝典抓取异常时（ncb_aid 为空）也能安全走"沿用"路径
    if ncb_aid:
        # 只要聚合页定位到了最新一期就记下种子，避免下一轮退化回旧种子（修复 8/20 回退 bug）
        new_state["ncb_seed"] = ncb_aid
        dm = re.search(r"(\d+)月(\d+)日", ncb_title or "")
        ncb_date = "%s/%s" % (dm.group(1), dm.group(2)) if dm else ""
        if ncb_html:
            rows = parse_ncb_chicken(ncb_html)
            print("农财宝典全国鸡价(%s): %d 个快大类报价" % (ncb_date, len(rows)))
        else:
            rows = []
            print("农财宝典: 已定位最新一期 %s，但正文暂未抓到，沿用上次报价" % ncb_date)
        if rows:
            prev_sh = state.get("sanhuang", {})
            # ===== 上一期主表明细 → 用于「可比样本」环比 =====
            # 背景（2026-10-15 发现）：农财宝典每日选登的省份并不固定，直接用「当日全样本均价」
            # 跨期比较会产生假跳变。实例：10/12 样本 6 条（广东/湖南/湖北/河北/福建）均价 5.48，
            # 10/13 样本 7 条（湖南/湖北/浙江/江西/安徽/江苏/福建）均价 5.01，看似暴跌 0.47 元；
            # 但两期都可比者仅湘鄂闽 3 省，真实变动只 −0.08 元，跌幅几乎全来自高价广东(5.95)掉出
            # 与低价江西(4.00)新入。故涨跌方向一律按可比样本判定，全样本均价仅用于展示水平。
            _prev_mid = {}
            for _t in (d["sections"][2].get("tables") or []):
                if ((_t.get("headers") or [""])[0] != "省份/地区"):
                    continue
                for _r in _t.get("rows") or []:
                    try:
                        _p = _r[2].get("t") if isinstance(_r[2], dict) else _r[2]
                        _nn = [float(x) for x in re.split(r"[-—–~]", str(_p)) if x]
                        if _nn:
                            _prev_mid[(str(_r[0]).strip(), str(_r[1]).strip())] = round(sum(_nn) / len(_nn), 3)
                    except Exception:
                        pass
            tbl = []
            for r in rows:
                lo = float(re.split(r"-", r["price"])[0])
                pv = prev_sh.get(r["prov"])
                if pv is None:
                    cell = {"t": "→", "dir": "flat"}
                else:
                    cell = trend_cell(lo, pv)
                tbl.append([r["prov"], r["breed"], {"t": r["price"], "dir": cell["dir"]},
                            cell, "农财宝典 " + ncb_date])
            sec = d["sections"][2]
            keep = [t for t in sec.get("tables", []) if t["headers"][0] != "省份/地区"]
            sec["tables"] = [{
                "cap": "快大类分省价·参照表（含麻鸡/青脚麻公，非纯三黄鸡口径）· 数据源：网易农财宝典畜牧版「全国鸡价」日报 · 三黄鸡速览卡口径已切至 817 肉杂棚前",
                "headers": ["省份/地区", "冻品流通品种（快大类）", "出栏价（元/斤）", "环比", "数据来源"],
                "rows": tbl
            }] + keep
            new_state["sanhuang"] = {r["prov"]: float(re.split(r"-", r["price"])[0]) for r in rows}
            _ncb_lag = lag_days(ncb_date, today)
            _ncb_note = ("（源站滞后期：已滞后%d天，非当日行情）" % _ncb_lag
                         if _ncb_lag is not None and _ncb_lag >= 2 else "")
            sec["tag"] = ("主口径：817肉杂棚前（冻品三黄鸡原料，云端每日更新，数据 %s）；"
                          "农财宝典快大类分省价 %s%s 为参照（含麻鸡类，不参与速览卡）。"
                          "慢速类(项鸡/阉鸡/竹丝鸡)见下方参考表，非冻品主渠道。"
                          % (d817_real, ncb_date, _ncb_note))
            print("三黄鸡主表更新: %d 行" % len(tbl))

            # 农财宝典快大类均价：仅用于本板块「分省参照」展示
            # （速览卡口径已于 2026-10-16 切换为 817 肉杂棚前均价，见上方板块三 817 块）
            mids = []
            for r in rows:
                parts = [float(x) for x in re.split(r"-", r["price"]) if x]
                if parts:
                    mids.append(sum(parts) / len(parts))
            sh_avg = round(sum(mids) / len(mids), 2) if mids else 0.0
            print("农财宝典快大类（分省参照）: %d 省，均价 %s 元/斤（不参与速览卡）"
                  % (len(rows), fmt(sh_avg)))

            # ===== 板块三「行情研判/核心信号/速览卡」随农财宝典最新一期日期每日重建 =====
            # 背景：此板块 analysis/banner/summary 曾是"一次性写入后被逐日继承"的静态稿，
            # 导致主表日期推进到 9/3 而研判文字仍停留 8/27。现与主表同源重建，杜绝过期残留。
            _cnt = {"up": 0, "down": 0, "flat": 0}
            for _r in tbl:
                _c = _r[3] if isinstance(_r[3], dict) else {}
                _cnt[_c.get("dir", "flat")] = _cnt.get(_c.get("dir", "flat"), 0) + 1
            if _cnt["up"] > _cnt["down"]:
                _mood, _mcls = "较上期整体上行", "up"
            elif _cnt["down"] > _cnt["up"]:
                _mood, _mcls = "较上期整体回落", "down"
            else:
                _mood, _mcls = "较上期整体持稳", "flat"
            _plist = sorted(((r["prov"], r["breed"],
                              float(re.split(r"-", r["price"])[0])) for r in rows),
                            key=lambda x: x[2])
            _loP, _hiP = _plist[0], _plist[-1]
            # 817（主口径）涨跌结构：源文自带涨跌标记，直接计数
            _c817 = {"up": 0, "down": 0, "flat": 0}
            for _r in (rows817 or []):      # rows817 为 dict 列表 [{region,spec,price,cell}]
                _cd = _r.get("cell") or {}
                _cd = _cd.get("dir", "flat") if isinstance(_cd, dict) else "flat"
                _c817[_cd] = _c817.get(_cd, 0) + 1
            if rows817 and _c817["up"] > _c817["down"]:
                _m817w, _m817c = "较上期整体上行", "up"
            elif rows817 and _c817["down"] > _c817["up"]:
                _m817w, _m817c = "较上期整体回落", "down"
            else:
                _m817w, _m817c = "较上期整体持稳", "flat"
            _big817 = (fmt(sum(_m817big) / len(_m817big)) if _m817big else "—")
            _small817 = (fmt(sum(_m817small) / len(_m817small)) if _m817small else "—")
            _line817 = None
            if rows817:
                _line817 = ("817肉杂棚前（%s 数据·**主口径**）：%d 个报价点均价 %s 元/斤"
                            "（大规格 %s、小规格1.5-1.7斤 %s），涨%d/跌%d/平%d，%s"
                            % (d817_real, len(_m817), fmt(_v817 or 0), _big817, _small817,
                               _c817["up"], _c817["down"], _c817["flat"], _m817w))
            sec["analysis"] = {
                "title": "📊 三黄鸡行情分析 · 今日涨落与采货参考",
                "up": ([_line817] if _line817 else []) + [
                    "农财宝典 %s（分省参照）：快大类 %d 省均价 %s 元/斤（区间 %s-%s），%s"
                    % (ncb_date, len(rows), fmt(sh_avg), fmt(min(mids)), fmt(max(mids)), _mood),
                    "农财宝典省际价差 %s 元/斤：%s%s %s vs %s%s %s（该表含麻鸡类，跨区先算运费）"
                    % (fmt(_hiP[2] - _loP[2]), _loP[0], _loP[1], _loP[2],
                       _hiP[0], _hiP[1], _hiP[2]),
                ],
                "ref": [
                    "**主口径＝817肉杂棚前均价**（用户 2026-10-16 定：三黄鸡指的就是 817 肉杂这一路）；"
                    "农财宝典「快大类」含麻鸡/青脚麻公，均价高出 1.5 元/斤以上，仅作分省参照，两口径不得混算",
                    "棚前毛鸡价低=屠宰原料成本低：三黄整鸡冻品批发参考 4.9-5.25 元/斤，关注屠宰开工与冻品报价联动",
                    "活禽鲜销参考（三黄项/矮脚黄项 7.6-8.5）源站已停更 30+ 天，按「停更源不采用、"
                    "不展示」规则已从本板块清退，不作为冻品成本参考",
                    "农财宝典为华东/华南产区口径；山东本省快大类以鸡病兄弟品种棚前表为准，两源可交叉印证",
                ],
            }
            if rows817:
                sec["banner"] = ("<strong>核心信号：</strong>冻品流通三黄鸡前端活禽"
                                 "（<strong>817肉杂·主口径</strong>）"
                                 "<strong class='%s'>%s</strong>——%d 个报价点棚前均价 "
                                 "<strong class='%s'>%s 元/斤</strong>（数据 %s；大规格 %s、小规格 %s）；"
                                 "农财宝典快大类分省参照 %s 元/斤"
                                 % (_m817c, _m817w, len(_m817), _m817c, fmt(_v817 or 0), d817_real,
                                    _big817, _small817, fmt(sh_avg)))
            else:
                sec["banner"] = ("<strong>核心信号：</strong>冻品流通三黄鸡前端活禽"
                                 "<strong class='%s'>%s</strong>——本期 817 棚前未抓到（沿用上一期），"
                                 "农财宝典快大类分省参照 %s 元/斤"
                                 % (_mcls, _mood, fmt(sh_avg)))
            sec["summary"] = [
                ({"label": "817肉杂棚前均价（主口径·%s）" % d817_real,
                  "value": fmt(_v817 or 0), "unit": "元/斤",
                  "trend": "<span class='%s'>%s %s</span>" % (
                      _m817c, {"up": "↑", "down": "↓", "flat": "→"}[_m817c], _m817w)}
                 if rows817 else
                 {"label": "817肉杂棚前均价（主口径）", "value": "待更新", "unit": "",
                  "trend": "<span class='flat'>本期未抓到，沿用上一期</span>"}),
                {"label": "农财宝典快大类均价（分省参照·%s）" % ncb_date,
                 "value": fmt(sh_avg), "unit": "元/斤",
                 "trend": "<span class='%s'>%s %s</span>" % (
                     _mcls, {"up": "↑", "down": "↓", "flat": "→"}[_mcls],
                     {"up": "较上期上行", "down": "较上期回落", "flat": "较上期持稳"}[_mcls])},
                {"label": "农财宝典省际区间（参照）",
                 "value": "%s ~ %s" % (fmt(min(mids)), fmt(max(mids))),
                 "unit": "元/斤",
                 "trend": "<span class='flat'>%s%s 最低 / %s%s 最高</span>"
                          % (_loP[0], fmt(_loP[2]), _hiP[0], fmt(_hiP[2]))},
            ]
            print("三黄鸡研判/核心信号/速览卡随 817(%s) + 农财宝典(%s) 数据重建"
                  % (d817_real, ncb_date))
    else:
        # ncb_aid 已定位到最新一期但正文未抓到（rows 为空）→ 降级：沿用上次报价并标注已定位日期
        if ncb_aid:
            sec = d["sections"][2]
            sec["tag"] = ("主口径 817肉杂棚前（%s 数据）；农财宝典快大类分省参照 %s 已定位（聚合页），"
                          "正文暂未抓到、沿用上次报价。"
                          % (d817_real, ncb_date or "?"))
            print("农财宝典: 已定位最新一期 %s，正文未抓到，沿用原有报价" % (ncb_date or "?"))
        else:
            print("农财宝典: 未找到全国鸡价文章，沿用原有数据")

    # ===== 板块三增强：②兄弟品种参照（鸡病专业网快大类） + ③冻品批发补高校后勤参照 =====
    sec = d["sections"][2]
    # ② 鸡病专业网 青脚麻公鸡/麻公鸡/黄花公鸡（快大类兄弟品种）跨源参照，与农财宝典三黄鸡互为印证
    if brother_rows:
        bro_tbl = {
            "cap": "快大类兄弟品种参照（鸡病专业网活禽棚前·云端每日更新）· 含麻鸡/黄花公等兄弟品种，与农财宝典分省参照同源，可交叉印证（主口径仍为 817 肉杂棚前）",
            "headers": ["产区", "品种（快大类兄弟）", "棚前价（元/斤）", "环比", "数据日期"],
            "rows": brother_rows,
        }
        sec["tables"] = [t for t in sec["tables"] if "兄弟品种参照" not in (t.get("cap") or "")] + [bro_tbl]
        print("三黄鸡板块②兄弟品种参照: %d 行" % len(brother_rows))
    # ③ 冻品批发参考价表补「高校后勤/餐饮采购价」参照行（与现有冻品批发区间 4.9-5.25 吻合）
    for t in sec["tables"]:
        if len(t["headers"]) > 1 and "冻品批发价" in t["headers"][1]:
            if not any("高校后勤" in str(r[0]) for r in t["rows"]):
                t["rows"].append(["高校后勤/餐饮采购（三黄鸡冻品）", "≈4.9（约115元/件·23斤装）",
                                  "整鸡件装", "高校后勤采购价参考（西北工大等）"])
                print("三黄鸡板块③冻品批发参照补高校后勤行")
            break

    # ===== 板块六：规则研判 =====
    tips = []
    if cull_by_prov:
        vals = cull_avg
        hi, lo = max(vals, key=vals.get), min(vals, key=vals.get)
        spread = vals[hi] - vals[lo]
        tips.append("淘汰鸡：高价区%s %s元/斤，低价区%s %s元/斤，价差%s元%s——%s"
                    % (hi, fmt(vals[hi]), lo, fmt(vals[lo]), fmt(spread),
                       "（>0.5元/斤，跨区调运有空间）" if spread > 0.5 else "",
                       "可从低价区询价锁单" if spread > 0.5 else "就近采购为主"))
    if egg_by_prov:
        tips.append("鸡蛋：主产省均价 %s 元/斤，蛋价走势影响养殖端淘鸡节奏，连续走弱时淘汰鸡供应将增加"
                    % fmt(avg_of(list(egg_avg.values()))))
    if broiler_rows:
        sd = [r for r in broiler_rows if r["city"] in
              ("滨州", "德州", "菏泽", "济南", "济宁", "临沂", "聊城", "日照", "青岛", "威海", "潍坊", "烟台", "东营", "淄博", "泰安", "枣庄")]
        avg = avg_of([r["price"] for r in sd]) if sd else None
        if avg:
            tips.append("白羽肉鸡：山东棚前均价 %s 元/斤，%s"
                        % (fmt(avg), "低位区间，可分批建仓" if avg < 3.3 else "按需采购，不追高"))
    # 数据完整性提示：不只看「今日是否抓到」，还要看「抓到的数据日期有多旧」
    # 2026-10-12 修复：农财宝典 10/8 那期一直被成功解析 → 旧逻辑误报"全部板块均抓到新数据"，
    # 而页面上的价格其实是 4 天前的。滞后 ≥2 天的源必须点名，避免误导采购决策。
    _stale, _lagged = [], []
    for _nm, _ds in (("淘汰鸡/鸡蛋汇总", sum_date), ("三黄鸡农财宝典", ncb_date),
                     ("817肉杂", d817_real), ("白羽棚前", bro_date), ("白羽冻品日更", fz_date)):
        _lg = lag_days(_ds, today)
        if _lg is not None and _lg >= 2:
            _lagged.append("%s（%s·滞后%d天）" % (_nm, str(_ds).strip(), _lg))
    if not cull_by_prov and not egg_by_prov:
        _stale.append("淘汰鸡/鸡蛋汇总")
    if not rows:
        _stale.append("三黄鸡农财宝典")
    if not rows817:
        _stale.append("817肉杂")
    if not live_rows:
        _stale.append("公鸡产区")
    if not broiler_rows:
        _stale.append("白羽")
    _msgs = []
    if _stale:
        _msgs.append("以下板块今日云端未抓到新价（%s），沿用 %s 数据"
                     % ("、".join(_stale), prev_date or "前日"))
    if _lagged:
        _msgs.append("以下源站本期尚未发布、页面显示为其最近一期数据：%s"
                     % "、".join(_lagged))
    if _msgs:
        tips.append("%s——相关数值非当日行情，下单前请按行内标注的数据日期核实" % "；".join(_msgs))
    else:
        tips.append("今日全部板块均抓到新数据，无沿用项（生成 %s）" % date_short)
    # ===== 保留 AI 周度研判 block（周一精修写入，整周沿用；下周一 AI 写入新研判时自然替换）=====
    # 识别标记：block 的 h 中含「本周研判」。云端每日重建规则 block，但周研判必须跨日存活。
    WEEKLY_MARK = "本周研判"
    _old_blocks = []
    if len(d.get("sections", [])) > 5:
        _old_blocks = d["sections"][5].get("blocks") or []
    weekly_blocks = [b for b in _old_blocks
                     if isinstance(b, dict) and WEEKLY_MARK in str(b.get("h", ""))]
    if weekly_blocks:
        print("板块六：保留 AI 周度研判 %d 个 block" % len(weekly_blocks))
    d["sections"][5]["blocks"] = weekly_blocks + [
        {"h": "📌 今日区域价差与机会（规则自动计算）", "p": tips[0] if tips else "—"},
        {"h": "🥚 蛋鸡链传导", "p": tips[1] if len(tips) > 1 else "—"},
        {"h": "🍗 白羽肉鸡操作", "p": tips[2] if len(tips) > 2 else "—"},
        {"h": "⚠️ 数据完整性提示", "p": tips[-1]},
    ]
    if weekly_blocks:
        _m = re.search(r"(\d{1,2}[-/]\d{1,2})", str(weekly_blocks[0].get("h", "")))
        _wt = "AI周度研判 %s" % _m.group(1) if _m else "AI周度研判"
    else:
        _wt = "无AI周度研判"
    d["sections"][5]["tag"] = "%s · 规则提示自动生成 %s" % (_wt, date_short)

    # ===== 走势卡：老母鸡（口径：博亚和讯分省出场价优先 → 回退鸡病专业网汇总） =====
    # 2026-10-15 修复（用户据微信淘淘鸡质疑"老母鸡连续三天下滑"后发现）：
    # 原实现固定用鸡病专业网汇总（11省）算均价，但该源**淘汰鸡栏并不日更**——
    # 实测 10/14、10/15 两篇原文逐省淘汰鸡价完全相同（辽宁都写 5.25），只有蛋价在动。
    # 而博亚和讯分省出场价每天在走（10/14 辽宁 5.40→5.20、山东 5.30→5.10、河南 -0.30）。
    # 用滞后源算均价会把真实下跌抹平成"持平"（页面 5.21 vs 实际 5.03，差 0.18 元），
    # 直接误导采购决策。现改为优先博亚（19省、自带涨跌列），抓不到才回退鸡病。
    try:
        # 取数优先级：①本期新抓到的博亚分省价 → ②页面上沿用的博亚表（上一期，仍比鸡病新）
        # → ③鸡病专业网汇总（该栏淘汰鸡段更新偏慢，仅兜底）
        _bj_tbl = None
        # 2026-10-19 修复：原按 cap 含「博亚和讯」找表，但**鸡病汇总表的 cap 也写着
        # 「当日真实行情请以下方『博亚和讯分省出场价』表为准」** → 误取到那张全「→ 持平」
        # 的鸡病表，导致 10/19 博亚全国普跌 0.20-0.30 元时，老母鸡卡仍标「持平」。
        # 改为按表头识别：headers = [省份, 出场价（元/斤）, 涨跌, 覆盖城市]。
        # 同类教训（2026-10-15 Mysteel 兜底）：唯一特征必须用 headers，不能用说明文字。
        for _t in (d["sections"][0].get("tables") or []):
            _hs = [str(x) for x in (_t.get("headers") or [])]
            if len(_hs) >= 3 and "出场价" in _hs[1] and "涨跌" in _hs[2]:
                _bj_tbl = _t
                break
        _bj_live = bool(bj_avg)
        _bj_carried = (not _bj_live) and bool(_bj_tbl)
        _avg_map = {}
        if _bj_live:
            _avg_map = dict(bj_avg)
            _src_name, _src_date = "博亚和讯分省出场价", (bj_date or date_short)
        elif _bj_carried:
            for _r in (_bj_tbl.get("rows") or []):
                try:
                    _avg_map[str(_r[0])] = float(str(_r[1]).strip("↑↓→ "))
                except Exception:
                    continue
            _md = re.search(r"(\d{1,2}/\d{1,2})", _bj_tbl.get("cap") or "")
            _src_name = "博亚和讯分省出场价（沿用上一期）"
            _src_date = _md.group(1) if _md else date_short
        else:
            _avg_map = dict(cull_avg)
            _src_name, _src_date = "鸡病专业网汇总（该栏更新偏慢）", (sum_date or date_short)
        if _avg_map:
            nat = round(sum(_avg_map.values()) / len(_avg_map), 2)
            tc = d["trend_cards"][0]
            chart = tc["chart"]
            dates = chart.get("dates") or []
            # 口径切换护栏：序列名不符说明是旧口径（鸡病11省）的历史点，混画会出假拐点 → 重置重来
            _SERIES_NAME = "淘汰鸡分省出场价均价"
            if chart.get("name") != _SERIES_NAME:
                print("老母鸡速览卡: 口径切换（%s → %s），走势序列重置"
                      % (chart.get("name"), _SERIES_NAME))
                chart["name"] = _SERIES_NAME
                chart["dates"] = []
                for s in chart["series"]:
                    s["values"] = []
                dates = []

            def _dkey(x):
                _m = re.match(r"(\d+)/(\d+)", str(x) or "")
                return (int(_m.group(1)), int(_m.group(2))) if _m else (0, 0)

            # 用源站文章的数据日期而非抓取当天：避免把昨日数据标成今日，
            # 前端拿错位点相减得出"假涨跌"（曾出现全板块持平却显示涨价）。
            point = _src_date
            if dates and _dkey(dates[-1]) >= _dkey(point):
                base = dates[:-1]      # 覆盖末点（同数据日重跑 / 纠正上一轮误标的抓取日）
                if base and _dkey(base[-1]) == _dkey(point):
                    base = base[:-1]   # 去重：避免出现两个相同数据日
                _tail = False          # 覆盖末点：dates/values 均保头部
            else:
                base = dates[-9:]
                _tail = True           # 追加新点：dates/values 均保尾部
            chart["dates"] = base[-9:] + [point]
            for s in chart["series"]:
                vals = (s.get("values") or [])
                if len(vals) > len(base):
                    # 2026-10-15 修复：同白羽卡（dates 保尾、values 原先保头 → 满 10 点后逐日错位）
                    vals = vals[-len(base):] if _tail else vals[:len(base)]
                s["values"] = vals + [nat]
            # 涨跌方向：博亚当期口径读表内自带涨跌列（多数决）；沿用口径方向未知 → 判稳，
            # 绝不把上一期的涨跌冒充当日；鸡病兜底口径无涨跌列，用跨期比价
            _dd = {"up": 0, "down": 0, "flat": 0}
            if _bj_live and _bj_tbl:
                for _r in (_bj_tbl.get("rows") or []):
                    _c = _r[2] if isinstance(_r[2], dict) else {}
                    _dd[_c.get("dir", "flat")] = _dd.get(_c.get("dir", "flat"), 0) + 1
            elif _bj_carried:
                _dd["flat"] = len(_avg_map)
            else:
                _pv = state.get("cull", {})
                for _p, _v in cull_avg.items():
                    _dd[trend_cell(_v, _pv.get(_p))["dir"]] += 1
            if _dd["flat"] > _dd["up"] and _dd["flat"] > _dd["down"]:
                tc["dir"] = "flat"
            elif _dd["up"] > _dd["down"]:
                tc["dir"] = "up"
            elif _dd["down"] > _dd["up"]:
                tc["dir"] = "down"
            else:
                tc["dir"] = "flat"
            tc["price"] = fmt(nat)     # 大数字与 note/走势末值同值（原来 price 长期不更新）
            _note = ("全国淘汰鸡均价 %s 元/斤（%s，%d省）。云端自动更新，数据源：%s。"
                     % (fmt(nat), _src_date, len(_avg_map), _src_name))
            # 跨源提示：鸡病专业网同口径若仍停在旧价，明确写出滞后，避免被当成当日价
            if (_bj_live or _bj_carried) and cull_avg:
                _cnat = round(sum(cull_avg.values()) / len(cull_avg), 2)
                if abs(_cnat - nat) >= 0.08:
                    _note += ("鸡病专业网汇总同口径仍报 %s 元/斤（%s，%d省；该栏淘汰鸡段更新偏慢），"
                              "两者相差 %s 元/斤，下单前请电话核实。"
                              % (fmt(_cnat), sum_date or "?", len(cull_avg), fmt(abs(_cnat - nat))))
            if _bj_carried:
                _note += "本期博亚和讯未抓到当期更新，本节沿用上一期分省表，涨跌方向未更新。"
            elif not _bj_live:
                _note += "本期博亚和讯未抓到，暂用鸡病专业网汇总兜底（该栏更新偏慢，务必核实）。"
            tc["note"] = _note
            print("老母鸡速览卡: %s 元/斤 dir=%s（%s数据，%d省，源=%s）"
                  % (fmt(nat), tc["dir"], point, len(_avg_map), _src_name))
    except Exception as e:
        print("[warn] 走势卡更新失败:", e)

    # ===== meta / footer =====
    stale_days = 0          # 最新板块日期相对今天的滞后天数（供新鲜度闸门使用）
    d["meta"]["date_cn"] = date_cn
    d["meta"]["date_iso"] = today.isoformat()
    d["meta"]["weekday"] = weekday
    # 内容日期（数据口径日）：原先只写在 except 分支里，成功路径永不赋值，
    # 导致该字段被 setdefault 永久锁在旧日期（曾在 2026-09-21 卡了整整两周，
    # 与 data_span 长期矛盾）。这里无条件跟随当日。
    d["meta"]["content_date"] = today.isoformat()
    # 更新时间（北京时间）：与「数据日期」分开，避免源未出新价时被误以为没更新
    try:
        bj = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
        d["meta"]["update_time"] = bj.strftime("%m-%d %H:%M")
        # 各板块实际数据日期：算跨度（最旧~最新）。
        # 注意：不能只取最旧值——各板块 tag 已各自标注日期，顶部若报单一「最旧」日期
        # 会和多数板块冲突且语义歧义。顶部只负责说明「日期不一致」这件事本身。
        tags = [s.get("tag", "") for s in d.get("sections", [])]
        data_dates = re.findall(r"(\d+)/(\d+)", " ".join(tags))
        if data_dates:
            uniq = sorted(set((int(a), int(b)) for a, b in data_dates))
            oldest, newest = uniq[0], uniq[-1]
            today_md = (bj.month, bj.day)
            d["meta"].pop("data_date", None)          # 废弃字段，避免前端误读
            if oldest == newest:
                d["meta"]["data_span"] = ""           # 全部同日，各板块 tag 已标注，顶部不再重复
                d["meta"]["data_stale"] = (oldest != today_md)
            else:
                d["meta"]["data_span"] = "%d/%d~%d/%d" % (oldest[0], oldest[1],
                                                          newest[0], newest[1])
                d["meta"]["data_stale"] = True
            # 滞后天数（跨年安全：板块日期 > 今天说明是去年底的那一期）
            nyear = bj.year if newest <= today_md else bj.year - 1
            try:
                stale_days = (datetime.date(bj.year, bj.month, bj.day)
                              - datetime.date(nyear, newest[0], newest[1])).days
            except ValueError:
                stale_days = 0
            # 最旧板块日期（ISO）——供前端多源择优排序：报告里最旧的一天越新，整体越新鲜
            oyear = bj.year if oldest <= today_md else bj.year - 1
            d["meta"]["oldest_date"] = "%04d-%02d-%02d" % (oyear, oldest[0], oldest[1])
    except Exception as e:
        print("[warn] 更新时间写入失败:", e)
        d["meta"].setdefault("content_date", today.isoformat())
    d["footer"] = ("云端自动更新 · 数据源：鸡病专业网 jbzyw.com / 鸡网 ckexc.com / 博亚和讯 boyar.cn · "
                   "页面生成 %s %s · 手机页面每2分钟自动检查新版" % (date_cn, weekday))

    # ===== 数据源策略执行（2026-10-14 用户拍板）=====
    # 「停更源一律不采用、不展示、删除」：在写 data.json 之前就把停更源的表/行真删，
    # 这样页面快照与前端实时拉取到的 data.json 都不会再有陈数据。
    # 策略文件 src_policy.json 由每周一 8 点 AI 精修自动化判定维护。
    try:
        import src_policy
        _pol = src_policy.load_policy(HERE)
        if _pol:
            _hit = src_policy.apply_policy(d, _pol)
            if _hit:
                src_policy.scrub_text(d, _hit)
            print("[policy] 策略执行完毕（updated %s）：停更源命中 %s"
                  % (_pol.get("updated", "?"), "、".join(sorted(_hit)) or "无（数据已是干净的）"))
        else:
            print("[policy] 未读到 src_policy.json，本次不做停更源清理")
        # 停更分级（用户 2026-10-14 立规）：>10 天清退、7~10 天不展示、行内标停更一律清退。
        # 阈值写死在 src_policy.py，不依赖 json，避免策略文件缺失时规则失效。
        _st = src_policy.apply_staleness(d, today=today)
        if _st["purged"] or _st["hidden"]:
            print("[policy] 停更分级：清退 %d 行 / 暂不展示 %d 行（沉源板块：%s）"
                  % (_st["purged"], _st["hidden"], "、".join(sorted(_st["boards"])) or "无"))
        if _st["boards"] and _pol:
            try:
                _f = os.path.join(HERE, "src_policy.json")
                with open(_f, encoding="utf-8") as _fh:
                    _pj = json.load(_fh)
                _pj["needs_replacement"] = sorted(set(_pj.get("needs_replacement") or [])
                                                  | _st["boards"])
                with open(_f, "w", encoding="utf-8") as _fh:
                    json.dump(_pj, _fh, ensure_ascii=False, indent=2)
            except Exception as _e:
                print("[warn] needs_replacement 回写失败:", _e)
    except Exception as e:
        print("[warn] 停更源策略执行失败（不阻断抓取）:", e)

    # ===== 保存 =====
    # new_state 在上方各板块里逐步填充（如 ncb_seed / sanhuang），这里补齐基础字段
    new_state.setdefault("date", today.isoformat())
    new_state.setdefault(
        "cull", {p: round(sum(v) / len(v), 2) for p, v in cull_by_prov.items()} if cull_by_prov else state.get("cull", {}))
    new_state.setdefault("egg", egg_avg if egg_by_prov else state.get("egg", {}))
    if "sanhuang" not in new_state:
        new_state["sanhuang"] = state.get("sanhuang", {})
    if "ncb_seed" not in new_state:
        new_state["ncb_seed"] = state.get("ncb_seed", NCB_SEEDS[0])
    if "frozen_sd" not in new_state:      # 山东冻品日更基准价，供次日算「较昨日」
        new_state["frozen_sd"] = state.get("frozen_sd", {})
    with open(os.path.join(HERE, "data.json"), "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, separators=(",", ":"))
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(new_state, f, ensure_ascii=False, indent=1)

    # ===== 滞后来源清单 =====
    # 顶部只显示一个跨度，看不出是哪个源慢了。这里把每个板块最旧的一期列出来，
    # workflow 日志里一眼可查，省得每次都翻 data.json。
    try:
        print("[lag] 各板块最旧一期：")
        for s in d.get("sections", []):
            tag = s.get("tag", "")
            ds = [(int(a), int(b)) for a, b in re.findall(r"(\d{1,2})/(\d{1,2})", tag)]
            ds += [(int(mo), int(dd))
                   for _y, mo, dd in re.findall(r"(\d{4})-(\d{2})-(\d{2})", tag)]
            ds = [x for x in ds if 1 <= x[0] <= 12 and 1 <= x[1] <= 31]
            if not ds:
                continue
            old = min(ds, key=lambda x: (x[0], x[1]))
            try:
                lag = max(0, (today - datetime.date(today.year, *old)).days)
            except ValueError:
                lag = 0
            print("   %-9s 最旧 %d/%d（滞后 %d 天）" % (s.get("title", "")[:9], old[0], old[1], lag))
    except Exception as e:
        print("[warn] 滞后清单输出失败:", e)

    # ===== 新鲜度闸门 =====
    # 源站每天发一期，滞后 1 天属正常（凌晨或周末尚未发文）；滞后 >=2 天说明抓取链路异常
    # （新一期没发现、解析失败等），此时以退出码 3 通知 workflow 等待后重跑，避免把陈旧
    # 数据当当天行情发给采购经理。仅在 REQUIRE_FRESH=1 时启用，本地调试不受影响。
    print("[fresh] 最新板块日期滞后 %d 天" % stale_days)
    if os.environ.get("REQUIRE_FRESH") == "1" and stale_days >= 2:
        print("[fresh] 数据滞后 %d 天 → 退出码 3，交 workflow 等待后重抓" % stale_days)
        sys.exit(3)

    print("data.json 更新完成 → %s" % date_cn)
    print("汇总省份: 淘汰鸡%d 鸡蛋%d | 白羽%d行 | 产区%d行 | 817 %d行"
          % (len(cull_by_prov), len(egg_by_prov), len(broiler_rows), len(live_rows), len(rows817)))


if __name__ == "__main__":
    main()
