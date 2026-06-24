# -*- coding: utf-8 -*-
import csv, re
from datetime import datetime
from collections import defaultdict

RDIR = r'c:\MyW\OSkhQuant\backtest_results\strategy_eda6de92_20240101_20260618_20260624_003125'
OUT  = r'c:\MyW\OSkhQuant\持股明细_V3.md'

# ── Stock name map from xtdata ──
NAME_MAP = {
    '000021.SZ':'深科技','000034.SZ':'神州数码','000066.SZ':'中国长城','000400.SZ':'许继电气',
    '000423.SZ':'东阿阿胶','000426.SZ':'兴业矿业','000559.SZ':'万向钱潮','000636.SZ':'风华高科',
    '000728.SZ':'国元证券','000733.SZ':'振华科技','000831.SZ':'中国稀土','000878.SZ':'云南铜业',
    '000932.SZ':'华菱钢铁','000988.SZ':'华工科技','002008.SZ':'大族激光','002064.SZ':'华峰化学',
    '002065.SZ':'东华软件','002080.SZ':'中材科技','002085.SZ':'万丰奥威','002155.SZ':'湖南黄金',
    '002156.SZ':'通富微电','002185.SZ':'华天科技','002195.SZ':'岩山科技','002202.SZ':'金风科技',
    '002240.SZ':'盛新锂能','002261.SZ':'拓维信息','002273.SZ':'水晶光电','002281.SZ':'光迅科技',
    '002340.SZ':'格林美','002384.SZ':'东山精密','002407.SZ':'多氟多','002409.SZ':'雅克科技',
    '002436.SZ':'兴森科技','002465.SZ':'海格通信','002532.SZ':'天山铝业','002558.SZ':'巨人网络',
    '002625.SZ':'光启技术','002673.SZ':'西部证券','002738.SZ':'中矿资源','002739.SZ':'万达电影',
    '002797.SZ':'第一创业','002966.SZ':'苏州银行','301236.SZ':'软通动力','301308.SZ':'江波龙',
    '301358.SZ':'湖南裕能','600008.SH':'首创环保','600021.SH':'上海电力','600096.SH':'云天化',
    '600143.SH':'金发科技','600157.SH':'永泰能源','600208.SH':'新湖中宝','600348.SH':'华阳股份',
    '600418.SH':'江淮汽车','600487.SH':'亨通光电','600497.SH':'驰宏锌锗','600498.SH':'烽火通信',
    '600536.SH':'中国软件','600546.SH':'山煤国际','600580.SH':'卧龙电驱','600642.SH':'申能股份',
    '600820.SH':'隧道股份','600839.SH':'四川长虹','600863.SH':'内蒙华电','600884.SH':'杉杉股份',
    '600895.SH':'张江高科','600988.SH':'赤峰黄金','601099.SH':'太平洋','601108.SH':'财通证券',
    '601128.SH':'常熟银行','601162.SH':'天风证券','601168.SH':'西部矿业','601179.SH':'中国西电',
    '601198.SH':'东兴证券','601233.SH':'桐昆股份','601456.SH':'国联证券','601555.SH':'东吴证券',
    '601577.SH':'长沙银行','601666.SH':'平煤股份','601717.SH':'郑煤机','603000.SH':'人民网',
    '603087.SH':'甘李药业','603160.SH':'汇顶科技','603444.SH':'吉比特','603606.SH':'东方电缆',
    '603893.SH':'瑞芯微',
}

def sname(code):
    return NAME_MAP.get(code, code)

# ── Load data ──
with open(RDIR + '/trades.csv', 'r', encoding='utf-8-sig') as f:
    trades = list(csv.DictReader(f))
with open(RDIR + '/daily_stats.csv', 'r', encoding='utf-8-sig') as f:
    daily = list(csv.DictReader(f))

# ── FIFO tracking ──
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
                if matched == pos['volume']:
                    pos['sell_date'] = dt; pos['sell_price'] = price
                    pos['comm_sell'] = float(t['commission']) + float(t['stamp_tax'])
                    pos['closed'] = True; closed.append(pos)
                else:
                    new = dict(pos); new['volume'] = matched
                    new['sell_date'] = dt; new['sell_price'] = price
                    new['comm_sell'] = float(t['commission']) + float(t['stamp_tax'])
                    new['closed'] = True; closed.append(new)
                    pos['volume'] -= matched
                remaining -= matched
                if remaining == 0: break

for p in closed:
    p['pnl_pct'] = (p['sell_price'] / p['buy_price'] - 1) * 100
    try:
        p['hold_days'] = (datetime.strptime(p['sell_date'],'%Y-%m-%d') - datetime.strptime(p['buy_date'],'%Y-%m-%d')).days
    except: p['hold_days'] = 0
    p['pnl_amount'] = (p['sell_price'] - p['buy_price']) * p['volume'] - p.get('comm_buy',0) - p.get('comm_sell',0)

open_active = [p for p in open_pos if not p.get('closed')]

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

# Buckets
bucket_data = []
for lo, hi in [(0,5),(5,10),(10,20),(20,30),(30,50),(50,100),(100,500)]:
    b = [p for p in closed if lo <= p['hold_days'] < hi]
    if b:
        bp = [p['pnl_pct'] for p in b]; ba = [p['pnl_amount'] for p in b]
        w = sum(1 for p in bp if p > 0)
        bucket_data.append((lo, hi, len(b), w, bp, ba))

# Open positions detail
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
    if m not in monthly:
        monthly[m] = {'first_ta':float(d_row['total_asset']), 'last_ta':float(d_row['total_asset']), 'first_bm':None, 'last_bm':None}
    monthly[m]['last_ta'] = float(d_row['total_asset'])
    if d_row['benchmark_close']:
        if monthly[m]['first_bm'] is None: monthly[m]['first_bm'] = float(d_row['benchmark_close'])
        monthly[m]['last_bm'] = float(d_row['benchmark_close'])

# ═══════════════════════════════════════════════════════════════
# 生成中文 Markdown 报告
# ═══════════════════════════════════════════════════════════════
L = []
def a(s=''): L.append(s)
def fmt(n): return '{:+,.0f}'.format(n)
def fmtp(n): return '{:+.2f}%'.format(n)

a('# Alpha144 V3 优化版 — 详细持股明细')
a()
a('> **策略**: Alpha144因子逆向选择（因子值最小的12只）| 剔除创业板(300)和科创板(688) | 分段止损+浮亏确认+移动止盈+大盘两级防御')
a('> **回测区间**: 2024-01-02 ~ 2026-06-18（594个交易日）')
a('> **回测结果**: 总收益 **+87.37%** | 年化收益 **+30.25%** | 最大回撤 **-18.30%** | 超额收益 **+41.10%**')
a()

# ═══════════════════════ 一、回测概况 ═══════════════════════
a('## 一、回测概况')
a()
a('| 指标 | 数值 |')
a('|------|------|')
a('| 回测区间 | 2024-01-02 ~ 2026-06-18（594个交易日） |')
a('| 初始资金 | 500,000 元 |')
a('| **期末资产** | **936,857 元** |')
a('| **总收益率** | **+87.37%** |')
a('| **年化收益率** | **+30.25%** |')
a('| **最大回撤** | **-18.30%**（峰值 {:,} 元于 {}，谷底 {}）|'.format(int(max_ta), peak_d, trough_d))
a('| 基准（沪深300）| ' + fmtp(bm_ret) + ' |')
a('| **超额收益** | **+{:.2f}%** |'.format(87.37 - bm_ret))
a('| 交易标的数 | {} 只 |'.format(len(set(t['code'] for t in trades))))
a('| 买入 / 卖出 | {} / {} 次 |'.format(sum(1 for t in trades if t['action']=='buy'), sum(1 for t in trades if t['action']=='sell')))
a('| 手续费合计 | {:,} 元 |'.format(int(sum(float(t['commission'])+float(t['stamp_tax']) for t in trades))))
a()

# ═══════════════════════ 二、月度收益 ═══════════════════════
a('## 二、月度收益明细')
a()
a('| 月份 | 策略收益 | 沪深300 | 超额 |')
a('|------|----------|---------|------|')
for m in sorted(monthly.keys()):
    d = monthly[m]
    ret = (d['last_ta'] / d['first_ta'] - 1) * 100
    bm_s = '-'; ex_s = '-'
    if d['first_bm'] and d['last_bm']:
        bm_r = (d['last_bm'] / d['first_bm'] - 1) * 100
        bm_s = fmtp(bm_r)
        ex_s = fmtp(ret - bm_r)
    flag = ' ⚠️' if ret < -3 else (' 🔥' if ret > 8 else '')
    a('| {} | {}%{} | {} | {} |'.format(m, '{:+.2f}'.format(ret), flag, bm_s, ex_s))
a()

# ═══════════════════════ 三、盈亏总览 ═══════════════════════
a('## 三、盈亏总览')
a()
a('| 指标 | 数值 |')
a('|------|------|')
a('| 平仓笔数 | {} |'.format(len(closed)))
a('| 盈利 / 亏损 | {} ({:.1f}%) / {} ({:.1f}%) |'.format(len(wins_list), len(wins_list)/len(closed)*100, len(losses_list), len(losses_list)/len(closed)*100))
a('| **平均盈亏** | **{}%** |'.format('{:+.2f}'.format(sum(pnls)/len(pnls))))
a('| 平均盈利 | {}% |'.format('{:+.2f}'.format(sum(wins_list)/len(wins_list))))
a('| 平均亏损 | {}% |'.format('{:+.2f}'.format(sum(losses_list)/len(losses_list))))
a('| 盈亏比 | {:.2f} |'.format(abs(sum(wins_list)/len(wins_list)/(sum(losses_list)/len(losses_list)))))
a('| 最大盈利 | **+{:.2f}%** |'.format(max(pnls)))
a('| 最大亏损 | **{:.2f}%** |'.format(min(pnls)))
a('| 平均持有天数 | {:.1f} 天 |'.format(sum(days_held)/len(days_held)))
a('| 最长持有 | {} 天 |'.format(max(days_held)))
a('| **已平仓累计净盈亏** | **{} 元** |'.format(fmt(sum(amounts))))
a()

# ═══════════════════════ 四、持有天数分析 ═══════════════════════
a('## 四、持有天数 vs 盈亏')
a()
a('| 持有天数 | 笔数 | 胜率 | 平均盈亏 | 净盈亏 |')
a('|----------|------|------|----------|--------|')
for lo, hi, cnt, w_cnt, bp, ba in bucket_data:
    a('| {:>2}-{:<4}天 | {} | {:.1f}% | {}% | {} 元 |'.format(lo, hi, cnt, w_cnt/cnt*100, '{:+.2f}'.format(sum(bp)/len(bp)), fmt(sum(ba))))
a()

# ═══════════════════════ 五、最佳/最差交易 ═══════════════════════
sorted_closed = sorted(closed, key=lambda x: x['pnl_pct'], reverse=True)
a('## 五、最佳 15 笔交易')
a()
a('| 代码 | 名称 | 买入日 | 买入价 | 卖出日 | 卖出价 | 盈亏 | 持有 | 金额 |')
a('|------|------|--------|--------|--------|--------|------|------|------|')
for p in sorted_closed[:15]:
    a('| {} | {} | {} | {:.2f} | {} | {:.2f} | **+{:.2f}%** | {}天 | +{} |'.format(
        p['code'], sname(p['code']), p['buy_date'], p['buy_price'], p['sell_date'], p['sell_price'],
        p['pnl_pct'], p['hold_days'], fmt(int(p['pnl_amount']))))

a()
a('## 六、最差 15 笔交易')
a()
a('| 代码 | 名称 | 买入日 | 买入价 | 卖出日 | 卖出价 | 盈亏 | 持有 | 金额 |')
a('|------|------|--------|--------|--------|--------|------|------|------|')
for p in sorted_closed[-15:]:
    a('| {} | {} | {} | {:.2f} | {} | {:.2f} | {:.2f}% | {}天 | {} |'.format(
        p['code'], sname(p['code']), p['buy_date'], p['buy_price'], p['sell_date'], p['sell_price'],
        p['pnl_pct'], p['hold_days'], fmt(int(p['pnl_amount']))))
a()

# ═══════════════════════ 七、当前持仓 ═══════════════════════
a('## 七、当前持仓（未平仓）')
a()
if open_detail:
    a('| 代码 | 名称 | 买入日 | 买入价 | 股数 | 最新价 | 浮盈% | 浮盈金额 |')
    a('|------|------|--------|--------|------|--------|-------|----------|')
    for code, bd, bp, vol, last_px, pnl_p, pnl_a in open_detail:
        emoji = '🟢' if pnl_p > 0 else ('🔴' if pnl_p < -5 else '🟡')
        a('| {} | {} | {} | {:.2f} | {} | {:.2f} | {}% | {} {} |'.format(
            code, sname(code), bd, bp, vol, last_px, '{:+.1f}'.format(pnl_p), emoji, fmt(int(pnl_a))))
    a('| **合计** | | | | | | | **{} 元** |'.format(fmt(int(total_open_pnl))))
else:
    a('无未平仓持仓。')
a()

# ═══════════════════════ 八、逐只股票明细 ═══════════════════════
a('## 八、逐只股票交易明细')
a()
a('> 共 **{}** 只股票，按累计盈亏从高到低排列'.format(len(code_data)))
a()

for code in sorted(code_data.keys(), key=lambda c: code_data[c]['total_pnl'], reverse=True):
    d = code_data[code]
    holding = d['holding']
    tag = ' 🔴**持有中**' if holding else ''

    a('### {} {} — {}{}'.format(code, sname(code),
        '{:+,.0f}元'.format(d['total_pnl']) if d['total_pnl'] >= 0 else '{:,.0f}元'.format(d['total_pnl']),
        tag))
    a()

    tr = d['trades']
    if tr:
        wr = d['wins'] / len(tr) * 100 if len(tr) > 0 else 0
        a('> {}笔交易 | {}赢{}输 | 累计盈亏: **{}元** | 胜率: {:.1f}%'.format(
            len(tr), d['wins'], d['losses'], fmt(int(d['total_pnl'])), wr))
    elif holding:
        a('> 首次买入后持有至今')
    a()

    if tr:
        a('| # | 买入日 | 买入价 | 卖出日 | 卖出价 | 盈亏% | 持有天 | 金额 |')
        a('|---|--------|--------|--------|--------|-------|--------|------|')
        for i, p in enumerate(tr):
            pnl_s = '+{:.2f}%'.format(p['pnl_pct']) if p['pnl_pct'] > 0 else '{:.2f}%'.format(p['pnl_pct'])
            a('| {} | {} | {:.2f} | {} | {:.2f} | {} | {} | {} |'.format(
                i+1, p['buy_date'], p['buy_price'], p['sell_date'], p['sell_price'],
                pnl_s, p['hold_days'], fmt(int(p['pnl_amount']))))
    a()

# ── Write ──
with open(OUT, 'w', encoding='utf-8') as f:
    f.write('\n'.join(L))
print('报告已生成: {}（{} 行）'.format(OUT, len(L)))
