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
    """817肉杂棚前 → [{region, spec, price, cell}]"""
    rows = []
    for line in text_lines(html):
        m = re.search(
            r"(\S{2,8}?)([0-9.]+(?:-[0-9.]+)?斤[^，。]*?)棚前(?:收购价|价)([0-9]+(?:\.[0-9]+)?-[0-9.]+|[0-9.]+)元/斤，与\d+日相比(下滑|上涨|持平)(?:([0-9.]+)元/斤)?",
            line,
        )
        if not m:
            continue
        region, spec, price, direction, num = m.groups()
        if "苗" in region + spec:
            continue
        if direction == "持平" or not num:
            cell = {"t": "→", "dir": "flat"}
        else:
            arrow = "↓" if direction == "下滑" else "↑"
            cell = {"t": arrow + num, "dir": "down" if direction == "下滑" else "up"}
        rows.append({"region": region, "spec": spec, "price": price, "cell": cell})
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
    """从 mffb 首页找当天标题含 keyword 的最新文章，返回 (url, title)"""
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


def main():
    today = datetime.date.today()
    date_cn = "%d年%d月%d日" % (today.year, today.month, today.day)
    weekday = "周%s" % "一二三四五六日"[today.weekday()]
    date_short = "%d/%d" % (today.month, today.day)

    with open(os.path.join(HERE, "data.json"), encoding="utf-8") as f:
        d = json.load(f)
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
        downs = sum(1 for p in trend_by_prov if max(trend_by_prov[p], key=trend_by_prov[p].get) == "down")
        ups = sum(1 for p in trend_by_prov if max(trend_by_prov[p], key=trend_by_prov[p].get) == "up")
        sec["analysis"] = {
            "title": "📊 鸡蛋分析（%s数据）" % sum_date,
            "up": ["今日主产省蛋价：%d省落、%d省涨、其余持稳，均价区间 %s-%s 元/斤"
                   % (downs, ups, fmt(min(egg_avg.values())), fmt(max(egg_avg.values()))),
                   "数据源：鸡病专业网汇总"],
            "ref": ["蛋价与淘汰鸡存在传导：蛋价走弱→养殖端淘汰意愿增强",
                    "蛋价连续上涨→延淘惜售，淘汰鸡供应收紧"],
        }
        sec["tag"] = "数据日期 %s · 鸡病专业网 · 云端自动更新" % sum_date

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
        if ch["dates"] and ch["dates"][-1] == md:
            vals[-1] = round(avg, 2)          # 同一天重复跑，覆盖当天值
        else:
            ch["dates"].append(md)
            vals.append(round(avg, 2))
            if len(ch["dates"]) > 10:
                ch["dates"] = ch["dates"][-10:]
                ch["series"][0]["values"] = vals[-10:]
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
    if live_rows:
        sec = d["sections"][3]
        keep_tables = sec.get("tables", [])
        # 保留黑凤公鸡等沿用行
        carry_rows = [r for r in keep_tables[0]["rows"] if "黑凤" in str(r)]
        live_tables = [{"headers": ["地区", "品种", "棚前价（元/斤）", "环比", "数据日期"],
                        "rows": live_rows + carry_rows}]
        sec["tables"] = live_tables + keep_tables[1:]
        sec["tag"] = "活禽棚前价 · 数据源：鸡病专业网各产区日报 · 云端自动更新（黑凤公鸡等未覆盖项沿用%s数据）" % prev_date
        sec["analysis"] = {
            "title": "📊 公鸡分析（各产区标注数据日期）",
            "up": ["今日云端抓取 %d 个产区报价，每行标注数据日期" % len(live_rows),
                   "黑凤公鸡等未覆盖项沿用 %s 数据，请核对" % (prev_date or "前日")],
            "ref": ["麻公/黄花公产区价差大，跨区采购先算运费",
                    "中元节/中秋等节前备货常推高价，节后常有回调"],
        }

    # ===== 板块三：817肉杂（快大类活数据）=====
    rows817, d817 = [], ""
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
    if rows817:
        sec = d["sections"][2]
        # 剔除上次的817表（避免重复运行累积），保留其余冻结表
        keep_tables = [t for t in sec.get("tables", [])
                       if "817" not in t["headers"][0]]
        t817 = {"headers": ["地区（817肉杂·快大类）", "规格", "棚前价（元/斤）", "环比", "数据日期"],
                "rows": [[r["region"], r["spec"], r["price"], r["cell"], "数据" + d817] for r in rows817]}
        sec["tables"] = [keep_tables[0], t817] + keep_tables[1:] if keep_tables else [t817]
        sec["tag"] = ("口径：快大类前端活禽=冻品三黄鸡成本基准。817肉杂为当日云端抓取（%s）；"
                      "快大三黄各省价沿用%s AI校准数据；两者均为快大类，可互相印证。" % (d817, prev_date))

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
                "cap": "冻品流通三黄鸡·快大类活禽出栏价（元/斤）· 数据源：网易农财宝典畜牧版「全国鸡价」日报",
                "headers": ["省份/地区", "冻品流通品种（快大类）", "出栏价（元/斤）", "环比", "数据来源"],
                "rows": tbl
            }] + keep
            new_state["sanhuang"] = {r["prov"]: float(re.split(r"-", r["price"])[0]) for r in rows}
            sec["tag"] = ("快大类分省价 %s 云端抓取（农财宝典）；817肉杂同日抓取（%s）。"
                          "慢速类(项鸡/阉鸡/竹丝鸡)见下方参考表，非冻品主渠道。" % (ncb_date, d817))
            print("三黄鸡主表更新: %d 行" % len(tbl))

            # 同步维护三黄鸡速览卡（口径：主表快大类均价），并累积历史供趋势使用
            mids = []
            for r in rows:
                parts = [float(x) for x in re.split(r"-", r["price"]) if x]
                mids.append(sum(parts) / len(parts))
            sh_avg = round(sum(mids) / len(mids), 2)
            cards = d.setdefault("trend_cards", [])
            sh = None
            for c in cards:
                if "三黄" in (c.get("name") or ""):
                    sh = c
                    break
            if sh is None:
                sh = {"name": "三黄鸡", "unit": "元/斤",
                      "chart": {"dates": [], "series": [{"name": "快大类出栏均价", "values": []}]}}
                cards.append(sh)
            ch = sh.setdefault("chart", {})
            ch.setdefault("dates", [])
            if not ch.get("series"):
                ch["series"] = [{"name": "快大类出栏均价", "values": []}]
            ch["series"][0].setdefault("values", [])
            md = "%d/%d" % (today.month, today.day)
            if ch["dates"] and ch["dates"][-1] == md:
                ch["series"][0]["values"][-1] = sh_avg
            else:
                ch["dates"].append(md)
                ch["series"][0]["values"].append(sh_avg)
                if len(ch["dates"]) > 10:
                    ch["dates"] = ch["dates"][-10:]
                    ch["series"][0]["values"] = ch["series"][0]["values"][-10:]
            sh["price"] = fmt(sh_avg)
            sh["note"] = ("冻品流通口径·快大类活禽出栏：%d省均价 %s 元/斤（区间 %s-%s）；"
                          "数据源：农财宝典全国鸡价 %s。注意：三黄项/矮脚黄项 7.6-8.5 为活禽鲜销，"
                          "非冻品渠道，勿按此价核算"
                          % (len(rows), fmt(sh_avg), fmt(min(mids)), fmt(max(mids)), ncb_date))
            print("三黄鸡速览卡: %s 元/斤，历史 %d 天" % (sh["price"], len(ch["dates"])))
    else:
        # ncb_aid 已定位到最新一期但正文未抓到（rows 为空）→ 降级：沿用上次报价并标注已定位日期
        if ncb_aid:
            sec = d["sections"][2]
            sec["tag"] = ("快大类分省价 %s 已定位（农财宝典聚合页），正文暂未抓到、沿用上次报价；"
                          "817肉杂同日抓取（%s）。" % (ncb_date or "?", d817))
            print("农财宝典: 已定位最新一期 %s，正文未抓到，沿用原有报价" % (ncb_date or "?"))
        else:
            print("农财宝典: 未找到全国鸡价文章，沿用原有数据")

    # ===== 板块三增强：②兄弟品种参照（鸡病专业网快大类） + ③冻品批发补高校后勤参照 =====
    sec = d["sections"][2]
    # ② 鸡病专业网 青脚麻公鸡/麻公鸡/黄花公鸡（快大类兄弟品种）跨源参照，与农财宝典三黄鸡互为印证
    if brother_rows:
        bro_tbl = {
            "cap": "快大类兄弟品种参照（鸡病专业网活禽棚前·云端每日更新）· 与农财宝典三黄鸡同属快大类，可交叉印证",
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
    tips.append("三黄鸡/黑凤公鸡板块：今日云端未抓到新价，沿用 %s 数据，重要决策前请人工核实" % (prev_date or "前日"))
    d["sections"][5]["blocks"] = [
        {"h": "📌 今日区域价差与机会（规则自动计算）", "p": tips[0] if tips else "—"},
        {"h": "🥚 蛋鸡链传导", "p": tips[1] if len(tips) > 1 else "—"},
        {"h": "🍗 白羽肉鸡操作", "p": tips[2] if len(tips) > 2 else "—"},
        {"h": "⚠️ 数据完整性提示", "p": tips[-1]},
    ]
    d["sections"][5]["tag"] = "规则自动生成（无AI研判）· 生成时间 %s" % date_short

    # ===== 走势卡：老母鸡追加当日点 =====
    try:
        if cull_avg:
            nat = round(sum(cull_avg.values()) / len(cull_avg), 2)
            tc = d["trend_cards"][0]
            chart = tc["chart"]
            dates = chart.get("dates") or []
            if dates and dates[-1] == date_short:
                dates = dates[:-1]  # 重复运行幂等：替换当日点而非追加
            chart["dates"] = dates[-9:] + [date_short]
            for s in chart["series"]:
                vals = (s.get("values") or [])
                if len(vals) > len(dates):
                    vals = vals[:-1]
                s["values"] = vals[-9:] + [nat]
            tc["note"] = ("全国淘汰鸡均价 %s 元/斤（%s，%d省）。云端自动更新，数据源：鸡病专业网。"
                          % (fmt(nat), sum_date, len(cull_avg)))
    except Exception as e:
        print("[warn] 走势卡更新失败:", e)

    # ===== meta / footer =====
    stale_days = 0          # 最新板块日期相对今天的滞后天数（供新鲜度闸门使用）
    d["meta"]["date_cn"] = date_cn
    d["meta"]["date_iso"] = today.isoformat()
    d["meta"]["weekday"] = weekday
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
    except Exception as e:
        print("[warn] 更新时间写入失败:", e)
    d["footer"] = ("云端自动更新 · 数据源：鸡病专业网 jbzyw.com / mffb.com.cn · 页面生成 %s %s · "
                   "手机页面每2分钟自动检查新版" % (date_cn, weekday))

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
