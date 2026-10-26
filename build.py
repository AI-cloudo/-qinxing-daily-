import json, re, os, time, urllib.request

# 脚本自身所在目录：本地与 GitHub Actions 云端通用（勿写绝对路径）
BASE = os.path.dirname(os.path.abspath(__file__))
JB_BASE = "https://www.jbzyw.com"
UA = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15"}

tmpl = open(os.path.join(BASE, 'template.html'), encoding='utf-8').read()

# 数据源：本地精修用 cloud-data.json，云端 Actions 只有 data.json
DATA_FILE = os.path.join(BASE, 'cloud-data.json')
if not os.path.exists(DATA_FILE):
    DATA_FILE = os.path.join(BASE, 'data.json')
print('数据源:', os.path.basename(DATA_FILE))
data = json.load(open(DATA_FILE, encoding='utf-8'))

# 给 sections 补 tab 短名
short = ['淘汰鸡', '鸡蛋', '三黄鸡', '公鸡', '白羽', '研判']
for i, s in enumerate(data.get('sections', [])):
    s['tab'] = short[i] if i < len(short) else s.get('title', '')
    for t in s.get('tables', []):
        t.setdefault('cap', '')

# ===== 板块角标：每个板块提炼「数据源 + 日期」，落后页面基准日 >=2 天自动标旧 =====
import datetime as _dt


def _each_date(txt):
    """从文本提取全部日期 -> [(月, 日, 位置)]，支持 9/4、08-31、9月4日、2026年9月4日"""
    out = []
    txt = str(txt)
    for m in re.finditer(r'(\d{1,2})月(\d{1,2})日|(\d{1,2})[/\-.](\d{1,2})(?!\d)', txt):
        if m.group(3):
            out.append((int(m.group(3)), int(m.group(4)), m.start()))
        else:
            out.append((int(m.group(1)), int(m.group(2)), m.start()))
    return out


def _nearest(dates, pos, max_gap=24):
    """取距 pos 最近的日期；太远(>max_gap字符)视为无关，返回 None"""
    if not dates:
        return None
    m, d, p = min(dates, key=lambda x: abs(x[2] - pos))
    if abs(p - pos) > max_gap:
        return None
    return (m, d)


def build_badges(data):
    """给每个 section 附 badges=[{src, d, stale}]，供标题行角标展示"""
    meta = data.get('meta', {}) or {}
    try:
        y, mo, dd = [int(x) for x in str(meta.get('date_iso', '')).split('-')[:3]]
        today = _dt.date(y, mo, dd)
    except Exception:
        today = _dt.date.today()

    def _gap(md):
        d1 = _dt.date(today.year, md[0], md[1])
        if d1 > today:
            # 2026-10-26 修正：AI 周研判写的是当天日期（如 tag「AI周度研判 10-26」），
            # 而 meta 基准日常落后一天（今晨数据未跑），旧逻辑按跨年回退误判滞后近一年。
            # 仅 11/12月 vs 1/2月 属真跨年，其余未来日期视为同步（gap=0）。
            if md[0] >= 11 and today.month <= 2:
                d1 = _dt.date(today.year - 1, md[0], md[1])
                return (today - d1).days
            return 0
        return (today - d1).days

    def _date_col(s, kw='数据日期'):
        """取板块内所有表「数据日期」列的最新 (月,日)，无则 None"""
        best = None
        for t in (s.get('tables') or []):
            hs = [str(h or '') for h in (t.get('headers') or [])]
            ci = next((i for i, h in enumerate(hs) if kw in h), None)
            if ci is None:
                continue
            for row in (t.get('rows') or []):
                if ci >= len(row):
                    continue
                v = row[ci]
                v = v.get('t', v) if isinstance(v, dict) else v
                for m_, d_, _p in _each_date(v):
                    if best is None or (m_, d_) > best:
                        best = (m_, d_)
        return best

    for s in (data.get('sections') or []):
        tab = s.get('tab', '')
        tag = str(s.get('tag') or '')
        badges = []

        def push(src, md):
            for b in badges:
                if b['src'] == src:
                    if md and _gap(md) >= 2:
                        b['stale'] = True
                    return
            badges.append({'src': src,
                           'd': ('%d/%d' % md) if md else '',
                           'stale': bool(md and _gap(md) >= 2)})

        if tab in ('淘汰鸡', '鸡蛋'):
            # tag 句式: 数据日期 9/4 · 鸡病专业网 · 云端自动更新
            m = re.search(r'数据日期\s*(\d{1,2})[/\-.](\d{1,2})', tag)
            md = (int(m.group(1)), int(m.group(2))) if m else _date_col(s)
            push('鸡病专业网', md)

        elif tab == '三黄鸡':
            # tag 句式: 快大类分省价 9/3 云端抓取（农财宝典）；817肉杂同日抓取（9/4）
            ds = _each_date(tag)
            p = tag.find('农财宝典')
            if p >= 0:
                push('农财宝典', _nearest(ds, p) or _date_col(s))
            p = tag.find('817')
            if p >= 0:
                push('817棚前', _nearest(ds, p))
            if not badges:                      # 兜底：主表日期列
                push('农财宝典', _date_col(s))

        elif tab == '公鸡':
            # 活禽棚前主源 = 鸡病专业网；日期取表「数据日期」列最新
            md = _date_col(s)
            m = re.search(r'数据日期\s*(\d{1,2})[/\-.](\d{1,2})', tag)
            push('鸡病专业网', m and (int(m.group(1)), int(m.group(2))) or md)

        elif tab == '白羽':
            # tag 用 | 分隔多源: 数据日期 9/4 · 鸡病专业网 | mffb 8/28 … | Mysteel 08-31
            for seg in re.split(r'[|｜]', tag):
                for src in ('鸡病专业网', 'mffb', 'Mysteel'):
                    p = seg.find(src)
                    if p >= 0:
                        push(src, _nearest(_each_date(seg), p))

        elif tab == '研判':
            ds = _each_date(tag)
            md = max(((m_, d_) for m_, d_, _p in ds), key=lambda x: x) if ds else None
            # 2026-10-12 修正：周研判写入后 tag 形如「AI周度研判 10-12 · 规则提示自动生成 10/11」，
            # 原判断先命中「规则」→ 角标误显示为"规则研判"，把 AI 研判标没了。改为 AI 优先。
            if '无AI' in tag:
                src = '规则研判'
            elif 'AI' in tag or '周度' in tag or '人工' in tag:
                src = 'AI研判'
            elif '规则' in tag:
                src = '规则研判'
            else:
                src = '研判'
            push(src, md)

        if not badges and _each_date(tag):      # 兜底：至少给出日期 + 已知源词
            src = '数据源'
            for w in ('鸡病专业网', '农财宝典', 'mffb', 'Mysteel', '鸡网'):
                if w in tag:
                    src = w
                    break
            ds = _each_date(tag)
            push(src, max(((m_, d_) for m_, d_, _p in ds), key=lambda x: x))

        s['badges'] = badges
        print('  角标[%s]: %s' % (tab, ' | '.join(
            '%s %s%s' % (b['src'], b['d'], '⚠滞后' if b['stale'] else '')
            for b in badges) or '（无）'))

build_badges(data)

# ===== 数据源策略：间断源只留数据不展示（每周一自动化维护 src_policy.json）=====
# 依据 2026-09-07 用户决定：日更不断更的固定源作展示数据；间断式更新源只用于周研判。
# 只过滤注入页面的快照，不修改 data.json 文件（间断源数据继续由云端爬虫积累，供周一 AI 周研判用）。
SRC_POLICY_FILE = os.path.join(BASE, 'src_policy.json')
import src_policy  # 停更源删除 / 间断源隐藏的执行器（scrape.py 与 build.py 共用同一套规则）


_pol = src_policy.load_policy(BASE)
if _pol:
    print('数据源策略: src_policy.json (updated %s)' % _pol.get('updated', '?'))
else:
    print('数据源策略: 未读到 src_policy.json，本次不做停更源清理')

# 主清理在 scrape.py 写 data.json 之前完成（停更源真删）；这里再跑一次作双保险，
# 保证本地精修构建、以及任何绕过 scrape 的数据来源都走同一套规则。
_hidden_words = list(src_policy.apply_policy(data, _pol)) if _pol else []
for _s in (data.get('sections') or []):        # 停更/间断源角标同步剔除
    bs = _s.get('badges') or []
    if bs:
        _s['badges'] = [b for b in bs if not any(w in (b.get('src') or '') for w in _hidden_words)]

# 停更源字样从「会渲染的文字」中清理：速览卡 note + 页脚 footer
# （tag/analysis 不渲染，保留给周一 AI 周研判作全源参考）
src_policy.scrub_text(data, _hidden_words)

# 停更分级（用户 2026-10-14 立规）：>10 天清退、7~10 天不展示、行内标停更一律清退。
# 与 scrape.py 用同一套阈值（写死在 src_policy.py），保证页面快照与云端 data.json 一致。
_st = src_policy.apply_staleness(data)
if _st['purged'] or _st['hidden']:
    print('停更分级: 清退 %d 行 / 暂不展示 %d 行（沉源板块：%s）'
          % (_st['purged'], _st['hidden'], '、'.join(sorted(_st['boards'])) or '无'))

# 构造第四张速览卡：白羽肉鸡（云端 scrape.py 已维护带历史的卡时，直接沿用不覆盖）
def avg_price(s):
    nums = re.findall(r'\d+\.?\d*', str(s))
    if not nums:
        return None
    return round(sum(float(x) for x in nums) / len(nums), 2)

by = data['sections'][4] if len(data['sections']) > 4 else None
baiyu_price = None
baiyu_note = '山东棚前均价 · 低位可分批建仓'
if by:
    # 优先找带“山东”/“平均”/“菏泽”的行
    for t in by.get('tables', []):
        for row in t.get('rows', []):
            c0 = str(row[0])
            if any(k in c0 for k in ['山东平均', '全省', '棚前均价', '平均价']):
                p = avg_price(row[1])
                if p is not None:
                    baiyu_price = str(p)
                    baiyu_note = c0 + ' · 低位可分批建仓'
                    break
        if baiyu_price:
            break
    if not baiyu_price:
        # 取表0第一行
        for row in by['tables'][0]['rows'][:1]:
            p = avg_price(row[1])
            if p is not None:
                baiyu_price = str(p)
                baiyu_note = str(row[0]) + '棚前 · 低位可分批建仓'
                break

data.setdefault('trend_cards', [])
if any(tc.get('name') == '白羽肉鸡' for tc in data['trend_cards']):
    # 云端已维护（含历史序列），直接沿用，否则每天重建会丢掉趋势数据
    print('白羽速览卡已存在，沿用（保留历史）')
elif baiyu_price:
    data['trend_cards'].append({
        'name': '白羽肉鸡',
        'price': baiyu_price,
        'unit': '元/斤',
        'note': baiyu_note,
        'chart': {
            'dates': [data['meta']['date_iso'][5:].replace('-', '/')],
            'series': [{'name': '棚前均价', 'values': [baiyu_price]}]
        }
    })

# ===== 本地抓取行业新闻注入快照（云端 data.json 下次更新后会自动携带） =====
def collect_news(max_news=6):
    keywords = re.compile(
        r"肉鸡|蛋鸡|行情|价格|市场|冻品|白条|屠宰|毛鸡|蛋价|鸡价|817|麻鸡|"
        r"三黄鸡|淘汰鸡|白羽|公鸡|活禽|棚前|收购价|批发|鸡肉|禽类|家禽|肉类",
        re.I,
    )
    seen = set()
    items = []
    for path in ["/lists/1", "/lists/204", "/lists/269"]:
        try:
            req = urllib.request.Request(JB_BASE + path, headers=UA)
            with urllib.request.urlopen(req, timeout=15) as r:
                h = r.read().decode("utf-8", errors="ignore")
        except Exception:
            continue
        for m in re.finditer(r'href="(/view/\d+)"[^>]*title="([^"]+)"', h):
            title = m.group(2).strip()
            url = JB_BASE + m.group(1)
            if url in seen or not keywords.search(title):
                continue
            seen.add(url)
            dm = re.search(r"(\d{4}年)?(\d{1,2})月(\d{1,2})日", title)
            date_str = "%s/%s" % (dm.group(2), dm.group(3)) if dm else ""
            items.append({"title": title, "url": url, "date": date_str, "src": "鸡病专业网"})
    items.sort(key=lambda x: int(re.search(r"/view/(\d+)", x["url"]).group(1)), reverse=True)
    return items[:max_news]

# 云端 scrape.py 已抓新闻时直接沿用，避免重复抓取
if data.get('news'):
    print('沿用已有新闻: %d 条' % len(data['news']))
else:
    data['news'] = collect_news()
    print('新闻条数:', len(data.get('news', [])))
    for n in data.get('news', [])[:3]:
        print(' -', n['title'][:40])

snap_json = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
html = tmpl.replace('/*__SNAPSHOT_JSON__*/', 'var SNAPSHOT = ' + snap_json + ';')

# 注入构建号：页面据此判断自己是否为最新版，过期则自动重载（绕开手机缓存）
build_id = time.strftime('%Y%m%d-%H%M')
html = html.replace('/*__BUILD_ID__*/"dev"', '"%s"' % build_id)
if '/*__BUILD_ID__*/' in html:
    print('[warn] BUILD_ID 占位符未替换')

# 页脚附上版本号，便于确认手机上跑的是哪一版
html = html.replace('</body>', '<div style="text-align:center;font-size:10px;color:#9aa;'
                    'padding:6px 0 10px">页面版本 %s</div></body>' % build_id)

open(os.path.join(BASE, 'index.html'), 'w', encoding='utf-8').write(html)
print('index.html:', len(html), 'B  版本', build_id)
print('trend_cards 数量:', len(data.get('trend_cards', [])))
for tc in data.get('trend_cards', []):
    print(' -', tc['name'], tc['price'], tc['unit'])
