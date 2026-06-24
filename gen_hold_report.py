# -*- coding: utf-8 -*-
import csv, re, sys
from datetime import datetime
from collections import defaultdict

RESULT_DIR = r'c:\MyW\OSkhQuant\backtest_results\strategy_eda6de92_20240101_20260618_20260624_003125'
OUTPUT = r'c:\MyW\OSkhQuant\持股明细_V3.md'

# Load trades
with open(RESULT_DIR + '/trades.csv', 'r', encoding='utf-8-sig') as f:
    trades = list(csv.DictReader(f))
with open(RESULT_DIR + '/daily_stats.csv', 'r', encoding='utf-8-sig') as f:
    daily = list(csv.DictReader(f))

# Load sell reasons from app.log V3 section
sell_reason_map = {}
try:
    with open(r'c:\MyW\OSkhQuant\app.log', 'r', encoding='utf-8', errors='replace') as f:
        log_lines = f.readlines()
    v3_start = None
    for i, line in enumerate(log_lines):
        if 'Alpha144-V3' in line:
            v3_start = i
            break
    v3_end = len(log_lines) - 1
    for i in range(v3_start, len(log_lines)):
        if '回测完成' in log_lines[i]:
            v3_end = i
            break

    for i in range(v3_start, v3_end):
        line = log_lines[i]
        if '生成卖出信号' not in line:
            continue
        m = re.search(r"'code':\s*'([^']+)'.*?'price':\s*([\d.]+).*?'volume':\s*(\d+).*?'reason':\s*'([^']+)'", line)
        if m:
            code = m.group(1)
            price = float(m.group(2))
            vol = int(m.group(3))
            reason = m.group(4)
            key = '%s_%.2f_%d' % (code, price, vol)
            sell_reason_map[key] = reason
    print('V3 log: lines %d-%d, sell reasons extracted: %d' % (v3_start, v3_end, len(sell_reason_map)))
except Exception as e:
    print('Log extraction warning: %s' % str(e))

# FIFO matching
open_pos = []; closed = []
for t in trades:
    code = t['code']; dt = t['datetime']; action = t['action']
    price = float(t['price']); vol = int(t['volume'])
    if action == 'buy':
        open_pos.append({'code':code, 'buy_date':dt, 'buy_price':price, 'volume':vol, 'comm_buy':float(t['commission'])})
    elif action == 'sell':
        remaining = vol
        for pos in open_pos:
            if pos['code'] == code and not pos.get('closed'):
                matched = min(pos['volume'], remaining)
                skey = '%s_%.2f_%d' % (code, price, vol)
                reason = sell_reason_map.get(skey, '')
                if matched == pos['volume']:
                    pos['sell_date'] = dt; pos['sell_price'] = price
                    pos['comm_sell'] = float(t['commission']) + float(t['stamp_tax'])
                    pos['reason'] = reason; pos['closed'] = True
                    closed.append(pos)
                else:
                    new = dict(pos); new['volume'] = matched
                    new['sell_date'] = dt; new['sell_price'] = price
                    new['comm_sell'] = float(t['commission']) + float(t['stamp_tax'])
                    new['reason'] = reason; new['closed'] = True
                    closed.append(new)
                    pos['volume'] -= matched
                remaining -= matched
                if remaining == 0: break

for p in closed:
    p['pnl_pct'] = (p['sell_price'] / p['buy_price'] - 1) * 100
    try:
        bd = datetime.strptime(p['buy_date'], '%Y-%m-%d')
        sd = datetime.strptime(p['sell_date'], '%Y-%m-%d')
        p['hold_days'] = (sd - bd).days
    except:
        p['hold_days'] = 0
    p['pnl_amount'] = (p['sell_price'] - p['buy_price']) * p['volume'] - p.get('comm_buy', 0) - p.get('comm_sell', 0)

open_active = [p for p in open_pos if not p.get('closed')]
unique_codes = set(t['code'] for t in trades)
print('Trades: %d  Closed: %d  Open: %d  Stocks: %d' % (len(trades), len(closed), len(open_active), len(unique_codes)))

# ── Stats ──
pnls = [p['pnl_pct'] for p in closed]
days_held = [p['hold_days'] for p in closed]
amounts = [p['pnl_amount'] for p in closed]
wins_list = [p for p in pnls if p > 0]
losses_list = [p for p in pnls if p < 0]

# Per-code
code_data = defaultdict(lambda: {'trades':[], 'total_pnl':0.0, 'wins':0, 'losses':0, 'holding':False})
for p in closed:
    c = p['code']; code_data[c]['trades'].append(p); code_data[c]['total_pnl'] += p['pnl_amount']
    if p['pnl_pct'] > 0: code_data[c]['wins'] += 1
    else: code_data[c]['losses'] += 1
for p in open_active:
    code_data[p['code']]['holding'] = True

# Sell reasons
reason_cats = defaultdict(lambda: {'cnt':0, 'pnls':[], 'amounts':[]})
for p in closed:
    r = p.get('reason','')
    if '止损' in r: cat = '止损'
    elif '移动止盈' in r: cat = '移动止盈'
    elif '调仓' in r: cat = '调仓(轮动)'
    elif '补卖' in r: cat = '补卖(跌停)'
    elif '硬防御' in r: cat = '大盘硬防御'
    elif '浮亏确认' in r: cat = '浮亏确认减半仓'
    else: cat = '其他/未知'
    reason_cats[cat]['cnt'] += 1
    reason_cats[cat]['pnls'].append(p['pnl_pct'])
    reason_cats[cat]['amounts'].append(p['pnl_amount'])

# Buckets
bucket_data = []
for lo, hi in [(0,5),(5,10),(10,20),(20,30),(30,50),(50,100),(100,500)]:
    b = [p for p in closed if lo <= p['hold_days'] < hi]
    if b:
        bp = [p['pnl_pct'] for p in b]; ba = [p['pnl_amount'] for p in b]
        w = sum(1 for p in bp if p > 0)
        bucket_data.append((lo, hi, len(b), w, bp, ba))

# Open positions
open_detail = []; total_open_pnl = 0
for p in open_active:
    code = p['code']; last_px = 0
    for d_row in reversed(daily):
        ps = d_row.get('positions','')
        if code in ps:
            m = re.search(r"'%s'.*?'price':\s*np\.float64\(([\d.]+)\)" % code, ps)
            if m: last_px = float(m.group(1)); break
    pnl_pct = (last_px / p['buy_price'] - 1) * 100 if last_px > 0 else 0
    pnl_amt = (last_px - p['buy_price']) * p['volume']
    total_open_pnl += pnl_amt
    open_detail.append((code, p['buy_date'], p['buy_price'], p['volume'], last_px, pnl_pct, pnl_amt))

# Benchmark
bm_first = bm_last = None
for d_row in daily:
    if d_row['benchmark_close']:
        if bm_first is None: bm_first = float(d_row['benchmark_close'])
        bm_last = float(d_row['benchmark_close'])
bm_ret = (bm_last / bm_first - 1) * 100 if bm_first and bm_last else 0

# Drawdown
max_ta = 500000.0; peak_d = ''; min_dd = 0; trough_d = ''
for d_row in daily:
    ta = float(d_row['total_asset'])
    if ta > max_ta: max_ta = ta; peak_d = d_row['date']
    dd = (ta / max_ta - 1) * 100
    if dd < min_dd: min_dd = dd; trough_d = d_row['date']

# Monthly
monthly = {}
for d_row in daily:
    m = d_row['date'][:7]
    if m not in monthly: monthly[m] = {'first_ta':float(d_row['total_asset']), 'last_ta':float(d_row['total_asset']), 'first_bm':None, 'last_bm':None}
    monthly[m]['last_ta'] = float(d_row['total_asset'])
    if d_row['benchmark_close']:
        if monthly[m]['first_bm'] is None: monthly[m]['first_bm'] = float(d_row['benchmark_close'])
        monthly[m]['last_bm'] = float(d_row['benchmark_close'])

# ═══ Generate Markdown ═══
L = []
def a(s=''): L.append(s)

a('# Alpha144 V3 优化版 — 详细持股明细')
a()
a('> **策略**: 逆向选择Alpha144因子最低12只 | 剔除创业板/科创板 | 分段止损+浮亏确认+移动止盈+大盘两级防御')
a('> **回测**: 2024-01-02 ~ 2026-06-18 (594个交易日) | **结果**: +87.37% 总收益, +30.25% 年化, -18.30% 最大回撤')
a()

# ── 1. Overview ──
a('## 一、回测概况')
a()
a('| 指标 | 数值 |')
a('|------|------|')
a('| 回测区间 | 2024-01-02 ~ 2026-06-18 (594个交易日) |')
a('| 初始资金 | 500,000 元 |')
a('| **期末资产** | **936,857 元** |')
a('| **总收益率** | **+87.37%** |')
a('| **年化收益率** | **+30.25%** |')
dd_info = '峰值 ' + str(int(max_ta)) + ' 于 ' + peak_d + ' -> 谷底 ' + trough_d
a('| **最大回撤** | **-18.30%** (' + dd_info + ') |')
a('| 基准(沪深300) | %+.2f%% |' % bm_ret)
a('| **超额收益** | **+%.2f%%** |' % (87.37 - bm_ret))
a('| 总交易股票 | %d 只 |' % len(unique_codes))
a('| 买入/卖出 | %d / %d 次 |' % (sum(1 for t in trades if t['action']=='buy'), sum(1 for t in trades if t['action']=='sell')))
a('| 手续费 | %,.0f 元 |' % sum(float(t['commission'])+float(t['stamp_tax']) for t in trades))
a('| 平均持股 | %.1f 天 | 最长 %d 天 |' % (sum(days_held)/len(days_held), max(days_held)))
a()

# ── 2. Monthly ──
a('## 二、月度收益')
a()
a('| 月份 | 策略收益 | 沪深300 | 超额 | 资产峰值 |')
a('|------|----------|---------|------|----------|')
prev_ta = 500000.0
for m in sorted(monthly.keys()):
    d = monthly[m]
    ret = (d['last_ta'] / d['first_ta'] - 1) * 100
    bm_m = ''
    ex = ''
    if d['first_bm'] and d['last_bm']:
        bm_r = (d['last_bm'] / d['first_bm'] - 1) * 100
        bm_m = '%+.2f%%' % bm_r
        ex = '%+.2f%%' % (ret - bm_r)
    marker = ' ⚠️' if ret < -4 else ''
    a('| %s | %+.2f%%%s | %s | %s | %.0f |' % (m, ret, marker, bm_m, ex, d['last_ta']))
a()

# ── 3. P&L ──
a('## 三、盈亏总览')
a()
a('| 指标 | 数值 |')
a('|------|------|')
a('| 平仓笔数 | %d |' % len(closed))
a('| 盈利 / 亏损 / 持平 | %d (%.1f%%) / %d (%.1f%%) / %d |' % (len(wins_list), len(wins_list)/len(closed)*100, len(losses_list), len(losses_list)/len(closed)*100, len(closed)-len(wins_list)-len(losses_list)))
a('| **平均盈亏** | **%+.2f%%** |' % (sum(pnls)/len(pnls)))
a('| 盈利均值 | %+.2f%% |' % (sum(wins_list)/len(wins_list)))
a('| 亏损均值 | %+.2f%% |' % (sum(losses_list)/len(losses_list)))
a('| 盈亏比 | %.2f |' % abs(sum(wins_list)/len(wins_list)/(sum(losses_list)/len(losses_list))))
a('| 最大盈利 | **+%.2f%%** |' % max(pnls))
a('| 最大亏损 | %.2f%% |' % min(pnls))
a('| **已平仓累计净盈亏** | **%+,.0f 元** |' % sum(amounts))
a()

# ── 4. Sell Reasons ──
a('## 四、卖出原因分布')
a()
a('| 卖出原因 | 笔数 | 占比 | 平均盈亏 | 胜率 | 累计金额 |')
a('|----------|------|------|----------|------|----------|')
for cat, d in sorted(reason_cats.items(), key=lambda x: x[1]['cnt'], reverse=True):
    ps = d['pnls']; ws = sum(1 for p in ps if p>0)
    a('| %s | %d | %.1f%% | %+.2f%% | %.1f%% | %+,.0f |' % (cat, d['cnt'], d['cnt']/len(closed)*100, sum(ps)/len(ps), ws/d['cnt']*100, sum(d['amounts'])))
a()

# ── 5. Hold Days ──
a('## 五、持有天数 vs 盈亏')
a()
a('| 持有天数 | 笔数 | 胜率 | 平均盈亏 | 累计净盈亏 |')
a('|----------|------|------|----------|------------|')
for lo, hi, cnt, w_cnt, bp, ba in bucket_data:
    a('| %d-%d天 | %d | %.1f%% | %+.2f%% | %+,.0f |' % (lo, hi, cnt, w_cnt/cnt*100, sum(bp)/len(bp), sum(ba)))
a()

# ── 6. Top/Bottom 15 ──
a('## 六、最佳/最差交易 Top 15')
a()
sorted_closed = sorted(closed, key=lambda x: x['pnl_pct'], reverse=True)
a('### 最佳 15 笔')
a()
a('| 代码 | 买入日 | 买入价 | 卖出日 | 卖出价 | 盈亏 | 持有 | 金额 | 原因 |')
a('|------|--------|--------|--------|--------|------|------|------|------|')
for p in sorted_closed[:15]:
    r = p.get('reason','')
    if '移动止盈' in r: r_s = '止盈'
    elif '止损' in r: r_s = '止损'
    elif '调仓' in r: r_s = '调仓'
    elif '浮亏确认' in r: r_s = '浮亏减半'
    else: r_s = r[:8] if r else '-'
    a('| %s | %s | %.2f | %s | %.2f | **+%.2f%%** | %dd | +%,.0f | %s |' % (p['code'], p['buy_date'], p['buy_price'], p['sell_date'], p['sell_price'], p['pnl_pct'], p['hold_days'], p['pnl_amount'], r_s))

a()
a('### 最差 15 笔')
a()
a('| 代码 | 买入日 | 买入价 | 卖出日 | 卖出价 | 盈亏 | 持有 | 金额 | 原因 |')
a('|------|--------|--------|--------|--------|------|------|------|------|')
for p in sorted_closed[-15:]:
    r = p.get('reason','')
    if '止损' in r: r_s = '止损'
    elif '浮亏确认' in r: r_s = '浮亏减半'
    elif '调仓' in r: r_s = '调仓'
    else: r_s = r[:8] if r else '-'
    a('| %s | %s | %.2f | %s | %.2f | %.2f%% | %dd | %,.0f | %s |' % (p['code'], p['buy_date'], p['buy_price'], p['sell_date'], p['sell_price'], p['pnl_pct'], p['hold_days'], p['pnl_amount'], r_s))
a()

# ── 7. Open Positions ──
a('## 七、当前持仓（未平仓）')
a()
if open_detail:
    a('| 代码 | 买入日期 | 买入价 | 股数 | 最新价 | 浮盈% | 浮盈金额 |')
    a('|------|----------|--------|------|--------|-------|----------|')
    for code, bd, bp, vol, last_px, pnl_p, pnl_a in open_detail:
        emoji = '🟢' if pnl_p > 0 else ('🔴' if pnl_p < -5 else '🟡')
        a('| %s %s | %s | %.2f | %d | %.2f | %+.1f%% | %+,.0f |' % (emoji, code, bd, bp, vol, last_px, pnl_p, pnl_a))
    a('| **合计** | | | | | | **%+,.0f** |' % total_open_pnl)
a()

# ── 8. Per-Stock Detail ──
a('## 八、逐只股票交易明细')
a()
a('> 共 **%d** 只股票，按累计盈亏从高到低排列' % len(code_data))
a()

for code in sorted(code_data.keys(), key=lambda c: code_data[c]['total_pnl'], reverse=True):
    d = code_data[code]
    holding_now = d['holding']
    tag = ' 🔴**持有中**' if holding_now else ''

    a('### %s%s' % (code, tag))
    a()

    tr = d['trades']
    if tr:
        wr = d['wins'] / len(tr) * 100 if len(tr) > 0 else 0
        a('> %d笔 | %d赢%d输 | 累计: **%+,.0f元** | 胜率: %.1f%%' % (len(tr), d['wins'], d['losses'], d['total_pnl'], wr))
    elif holding_now:
        a('> 首次买入后持有至今')
    a()

    if tr:
        a('| # | 买入日 | 买入价 | 卖出日 | 卖出价 | 盈亏% | 天 | 金额 | 原因 |')
        a('|---|--------|--------|--------|--------|-------|----|------|------|')
        for i, p in enumerate(tr):
            r = p.get('reason', '')
            if '移动止盈' in r: r_s = '🟢止盈'
            elif '止损' in r: r_s = '🟥止损'
            elif '调仓' in r: r_s = '🔵调仓'
            elif '补卖' in r: r_s = '🟡补卖'
            elif '硬防御' in r: r_s = '🔴硬防御'
            elif '浮亏确认' in r: r_s = '🟠浮亏减半'
            else: r_s = '-'
            pnl_s = '+%.2f%%' % p['pnl_pct'] if p['pnl_pct'] > 0 else '%.2f%%' % p['pnl_pct']
            a('| %d | %s | %.2f | %s | %.2f | %s | %d | %+,.0f | %s |' % (i+1, p['buy_date'], p['buy_price'], p['sell_date'], p['sell_price'], pnl_s, p['hold_days'], p['pnl_amount'], r_s))
    a()

# Write
output = '\n'.join(L)
with open(OUTPUT, 'w', encoding='utf-8') as f:
    f.write(output)
print('\nReport: %s (%d lines)' % (OUTPUT, len(L)))
