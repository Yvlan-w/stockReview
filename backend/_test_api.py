import requests, json

def test_kline(label, index_code):
    r = requests.get(f'http://127.0.0.1:8011/api/market/kline?index={index_code}', timeout=10)
    d = r.json()
    print(f'=== {label} ===')
    print(f'  status: {r.status_code}')
    print(f'  index: {d.get("index")}')
    data = d.get("data", [])
    print(f'  数据条数: {len(data)}')
    if data:
        last = data[-1]
        print(f'  最新日期: {last["fullDate"]}')
        print(f'  最新成交额: {last["turnover"]:.0f}')
    print()

# 测试各指数
test_kline('上证K线', '1.000001')
test_kline('深证K线', '0.399001')
test_kline('创业板K线', '0.399006')
test_kline('科创50K线', '1.000688')

# 测试实时行情
r = requests.get('http://127.0.0.1:8011/api/market/realtime', timeout=10)
d = r.json()
print('=== 实时行情 ===')
print(f'  status: {r.status_code}')
if d.get('data'):
    indices = d['data'].get('indices', [])
    print(f'  指数数量: {len(indices)}')
    for idx in indices:
        name = idx.get('name', '?')
        val = idx.get('value', 0)
        pct = idx.get('changePct', 0)
        print(f'    {name}: {val} ({pct}%)')
else:
    print(f'  fetch_status: {d.get("fetch_status")}')
    print(f'  data: {d.get("data")}')