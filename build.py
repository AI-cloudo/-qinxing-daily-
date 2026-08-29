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
