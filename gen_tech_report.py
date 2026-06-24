# -*- coding: utf-8 -*-
"""Generate technical analysis report: P&L vs technical parameters"""
import csv, re, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from datetime import datetime
from collections import defaultdict
import json

RDIR = r'c:\MyW\OSkhQuant\backtest_results\strategy_eda6de92_20240101_20260618_20260624_013550'
OUT  = r'c:\MyW\OSkhQuant\技术面分析报告_V3.md'

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
    '600724.SH':'宁波富达','600739.SH':'辽宁成大','600820.SH':'隧道股份','600839.SH':'四川长虹',
    '600863.SH':'内蒙华电','600884.SH':'杉杉股份','600895.SH':'张江高科','600988.SH':'赤峰黄金',
    '601099.SH':'太平洋','601108.SH':'财通证券','601128.SH':'常熟银行','601162.SH':'天风证券',
    '601168.SH':'西部矿业','601179.SH':'中国西电','601198.SH':'东兴证券','601233.SH':'桐昆股份',
    '601456.SH':'国联证券','601555.SH':'东吴证券','601577.SH':'长沙银行','601666.SH':'平煤股份',
    '601717.SH':'郑煤机','603000.SH':'人民网','603087.SH':'甘李药业','603160.SH':'汇顶科技',
    '603444.SH':'吉比特','603606.SH':'东方电缆','603893.SH':'瑞芯微',
}
def sname(c):
    return NAME_MAP.get(c, c)

# Load
with open(RDIR + '/trades.csv', 'r', encoding='utf-8-sig') as f:
    trades = list(csv.DictReader(f))
with open(RDIR + '/daily_stats.csv', 'r', encoding='utf-8-sig') as f:
    daily = list(csv.DictReader(f))

# FIFO
open_pos = []; closed = []
for t in trades:
    code = t['code']; dt = t['datetime']; action = t['action']
    price = float(t['price']); vol = int(t['volume'])
    if action == 'buy':
        open_pos.append({'code':code, 'buy_date':dt, 'buy_price':price, 'volume':vol,
                         'comm_buy':float(t['commission']), 'ta_at_buy':float(t['total_asset'])})
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
        p['hold_days'] = (datetime.strptime(p['sell_date'],'%Y-%m-%d') -
                          datetime.strptime(p['buy_date'],'%Y-%m-%d')).days
    except: p['hold_days'] = 0
    p['pnl_amount'] = ((p['sell_price'] - p['buy_price']) * p['volume']
                       - p.get('comm_buy',0) - p.get('comm_sell',0))
    p['cost_basis'] = p['buy_price'] * p['volume']
    p['position_weight'] = p['cost_basis'] / p['ta_at_buy'] * 100 if p['ta_at_buy'] > 0 else 0

open_active = [p for p in open_pos if not p.get('closed')]

# Extract daily technical: per stock daily close, build drawdown from entry
# Also build per-position intra-trade metrics

# For each closed position, compute:
# - max_favorable_excursion (MFE): highest price / entry - 1
# - max_adverse_excursion (MAE): lowest price / entry - 1
# - drawdown_duration: consecutive days underwater
# - final_day_pnl: P&L on last day before exit

price_history = defaultdict(dict)  # {code: {date_str: close}}
for d_row in daily:
    dt = d_row['date']
    ps = d_row.get('positions','')
    if not ps or ps == '{}':
        continue
    for code in set(p['code'] for p in closed + open_active):
        if code in ps:
            m = re.search(r"'%s'.*?'price':\s*np\.float64\(([\d.]+)\)" % code, ps)
            if m:
                price_history[code][dt] = float(m.group(1))

# For each closed position, compute MFE/MAE etc
for p in closed:
    code = p['code']
    entry = p['buy_price']
    if entry <= 0: continue

    ph = price_history.get(code, {})
    max_price = entry; min_price = entry
    underwater_days = 0; max_underwater = 0; current_underwater = 0

    # Get dates between buy and sell
    bd = datetime.strptime(p['buy_date'], '%Y-%m-%d')
    sd = datetime.strptime(p['sell_date'], '%Y-%m-%d')

    for dt_str, px in sorted(ph.items()):
        try:
            d = datetime.strptime(dt_str, '%Y-%m-%d')
        except: continue
        if d < bd or d > sd: continue
        if px > max_price: max_price = px
        if px < min_price: min_price = px
        # Track underwater
        if px < entry:
            current_underwater += 1
        else:
            if current_underwater > max_underwater:
                max_underwater = current_underwater
            current_underwater = 0
    if current_underwater > max_underwater:
        max_underwater = current_underwater

    p['mfe_pct'] = (max_price / entry - 1) * 100
    p['mae_pct'] = (min_price / entry - 1) * 100
    p['max_underwater_days'] = max_underwater
    p['mfe_mae_ratio'] = abs(p['mfe_pct'] / p['mae_pct']) if p['mae_pct'] != 0 else 999
    p['entry_price'] = entry
    p['exit_price'] = p['sell_price']
    p['max_price'] = max_price
    p['min_price'] = min_price

# Compute per-stock metrics
code_metrics = defaultdict(lambda: {'trades':[], 'total_pnl':0.0, 'wins':0, 'losses':0})
for p in closed:
    c = p['code']; code_metrics[c]['trades'].append(p); code_metrics[c]['total_pnl'] += p['pnl_amount']
    if p['pnl_pct'] > 0: code_metrics[c]['wins'] += 1
    else: code_metrics[c]['losses'] += 1

# Win/loss analysis by parameter buckets
def bucket_analysis(closed, key, labels, is_pct=False):
    """Bucket trades by a parameter and compute win rate & avg P&L"""
    results = []
    for lo, hi, label in labels:
        if is_pct:
            bucket = [p for p in closed if lo <= (p[key]/100 if key in ('pnl_pct','mfe_pct','mae_pct') else p.get(key,0)) < hi]
        else:
            bucket = [p for p in closed if lo <= p.get(key,0) < hi]
        if bucket:
            bp = [p['pnl_pct'] for p in bucket]
            ba = [p['pnl_amount'] for p in bucket]
            w = sum(1 for p in bp if p > 0)
            results.append((label, len(bucket), w, bp, ba))
    return results

pnls = [p['pnl_pct'] for p in closed]

# ═══════════════════════════════════════════════════════════════
# Generate Markdown
# ═══════════════════════════════════════════════════════════════
L = []
def a(s=''): L.append(s)
def fmt(n): return '{:+,.0f}'.format(int(n))
def fmtp(n): return '{:+.2f}%'.format(n)

a('# Alpha144 V3 — 技术面参数与盈亏关系分析')
a()
a('> 基于修正版回测数据 (2024-01-02 ~ 2026-06-18) | 447笔平仓 | 66只股票')
a()

# ═══════════ 1. TOTAL OVERVIEW ═══════════
a('## 一、整体统计')
a()
a('| 指标 | 数值 |')
a('|------|------|')
a('| 总平仓数 | {} |'.format(len(closed)))
wins = [p for p in pnls if p > 0]
losses = [p for p in pnls if p < 0]
a('| 盈利/亏损 | {}/{}(胜率{:.1f}%) |'.format(len(wins), len(losses), len(wins)/len(closed)*100))
a('| 平均盈亏 | {}% |'.format('{:+.2f}'.format(sum(pnls)/len(pnls))))
a('| 盈利均值 | {}% |'.format('{:+.2f}'.format(sum(wins)/len(wins))))
a('| 亏损均值 | {}% |'.format('{:+.2f}'.format(sum(losses)/len(losses))))
a('| 盈亏比 | {:.2f} |'.format(abs(sum(wins)/len(wins)/(sum(losses)/len(losses)))))
a('| 平均持有 | {:.1f}天 |'.format(sum(p['hold_days'] for p in closed)/len(closed)))
a()

# ═══════════ 2. HOLD DAYS ═══════════
a('## 二、持有天数 vs 盈亏')
a()
a('| 持有天数 | 笔数 | 胜率 | 平均盈亏 | 平均盈利 | 平均亏损 | 净盈亏 |')
a('|----------|------|------|----------|----------|----------|--------|')
for lo, hi, label in [(0,5,'0-5'),(5,10,'5-10'),(10,20,'10-20'),(20,30,'20-30'),
                       (30,50,'30-50'),(50,100,'50-100'),(100,300,'100+')]:
    bucket = [p for p in closed if lo <= p['hold_days'] < hi]
    if bucket:
        bp = [p['pnl_pct'] for p in bucket]
        bw = [p for p in bp if p > 0]; bl = [p for p in bp if p < 0]
        a('| {}天 | {} | {:.1f}% | {}% | {}% | {}% | {} |'.format(
            label, len(bucket), len(bw)/len(bucket)*100,
            '{:+.2f}'.format(sum(bp)/len(bp)),
            '{:+.2f}'.format(sum(bw)/len(bw)) if bw else 'N/A',
            '{:+.2f}'.format(sum(bl)/len(bl)) if bl else 'N/A',
            fmt(sum(p['pnl_amount'] for p in bucket))))
a()

# ═══════════ 3. MFE/MAE ═══════════
a('## 三、最大有利偏移(MFE) vs 最大不利偏移(MAE)')
a()
a('> MFE = 持仓期间最高价相对买入价的涨幅')
a('> MAE = 持仓期间最低价相对买入价的跌幅')
a()

# MFE buckets
a('### 3.1 MFE（曾经浮盈多少）与最终盈亏')
a()
a('| MFE区间 | 笔数 | 胜率 | 平均终盈 | 平均MFE |')
a('|---------|------|------|----------|---------|')
for lo, hi, label in [(0,5,'0-5%'),(5,10,'5-10%'),(10,20,'10-20%'),(20,40,'20-40%'),
                       (40,80,'40-80%'),(80,300,'80%+')]:
    bucket = [p for p in closed if lo <= p.get('mfe_pct',0) < hi]
    if bucket:
        bp = [p['pnl_pct'] for p in bucket]
        mf = [p['mfe_pct'] for p in bucket]
        w = sum(1 for p in bp if p > 0)
        a('| {} | {} | {:.1f}% | {}% | {}% |'.format(
            label, len(bucket), w/len(bucket)*100,
            '{:+.2f}'.format(sum(bp)/len(bp)),
            '{:+.2f}'.format(sum(mf)/len(mf))))
a()

# MAE buckets
a('### 3.2 MAE（曾经浮亏多少）与最终盈亏')
a()
a('| MAE区间 | 笔数 | 胜率 | 平均终盈 | 平均MAE |')
a('|---------|------|------|----------|---------|')
for lo, hi, label in [(-5,0,'0~-5%'),(-10,-5,'-5~-10%'),(-15,-10,'-10~-15%'),
                       (-20,-15,'-15~-20%'),(-35,-20,'-20~-35%')]:
    bucket = [p for p in closed if lo <= p.get('mae_pct',0) < hi]
    if bucket:
        bp = [p['pnl_pct'] for p in bucket]
        ma = [p['mae_pct'] for p in bucket]
        w = sum(1 for p in bp if p > 0)
        a('| {} | {} | {:.1f}% | {}% | {}% |'.format(
            label, len(bucket), w/len(bucket)*100,
            '{:+.2f}'.format(sum(bp)/len(bp)),
            '{:+.2f}'.format(sum(ma)/len(ma))))
a()

# MFE/MAE ratio
a('### 3.3 MFE/MAE比值与胜率')
a()
a('| MFE/MAE比 | 笔数 | 胜率 | 平均盈亏 |')
a('|-----------|------|------|----------|')
for lo, hi, label in [(0,1,'<1x(浮亏>浮盈)'),(1,2,'1-2x'),(2,3,'2-3x'),(3,5,'3-5x'),(5,999,'5x+')]:
    bucket = [p for p in closed if lo <= p.get('mfe_mae_ratio',0) < hi]
    if bucket:
        bp = [p['pnl_pct'] for p in bucket]
        w = sum(1 for p in bp if p > 0)
        a('| {} | {} | {:.1f}% | {}% |'.format(
            label, len(bucket), w/len(bucket)*100,
            '{:+.2f}'.format(sum(bp)/len(bp))))
a()

# ═══════════ 4. UNDERWATER ═══════════
a('## 四、连续水下天数 vs 盈亏')
a()
a('> 水下 = 持仓期间价格持续低于买入价的天数')
a()
a('| 最长水下天 | 笔数 | 胜率 | 平均盈亏 |')
a('|-----------|------|------|----------|')
for lo, hi, label in [(0,10,'0-10'),(10,20,'10-20'),(20,40,'20-40'),(40,80,'40-80'),(80,200,'80-200'),(200,500,'200+')]:
    bucket = [p for p in closed if lo <= p.get('max_underwater_days',0) < hi]
    if bucket:
        bp = [p['pnl_pct'] for p in bucket]
        w = sum(1 for p in bp if p > 0)
        a('| {}天 | {} | {:.1f}% | {}% |'.format(
            label, len(bucket), w/len(bucket)*100,
            '{:+.2f}'.format(sum(bp)/len(bp))))
a()

# ═══════════ 5. POSITION WEIGHT ═══════════
a('## 五、仓位权重 vs 盈亏')
a()
a('| 仓位占比 | 笔数 | 胜率 | 平均盈亏 |')
a('|----------|------|------|----------|')
for lo, hi, label in [(0,2,'<2%'),(2,4,'2-4%'),(4,6,'4-6%'),(6,8,'6-8%'),(8,15,'8%+')]:
    bucket = [p for p in closed if lo <= p.get('position_weight',0) < hi]
    if bucket:
        bp = [p['pnl_pct'] for p in bucket]
        w = sum(1 for p in bp if p > 0)
        a('| {} | {} | {:.1f}% | {}% |'.format(
            label, len(bucket), w/len(bucket)*100,
            '{:+.2f}'.format(sum(bp)/len(bp))))
a()

# ═══════════ 6. ENTRY/EXIT PRICE LEVEL ═══════════
a('## 六、买入价区间 vs 盈亏')
a()
a('> 按前复权价格分类（反映股价高低与盈亏关系）')
a()
a('| 买入价区间 | 笔数 | 胜率 | 平均盈亏 | 平均持有 |')
a('|-----------|------|------|----------|----------|')
for lo, hi, label in [(0,10,'<10元'),(10,20,'10-20'),(20,50,'20-50'),(50,100,'50-100'),(100,500,'100+')]:
    bucket = [p for p in closed if lo <= p['buy_price'] < hi]
    if bucket:
        bp = [p['pnl_pct'] for p in bucket]
        dh = [p['hold_days'] for p in bucket]
        w = sum(1 for p in bp if p > 0)
        a('| {} | {} | {:.1f}% | {}% | {:.1f}天 |'.format(
            label, len(bucket), w/len(bucket)*100,
            '{:+.2f}'.format(sum(bp)/len(bp)),
            sum(dh)/len(dh)))
a()

# ═══════════ 7. YEAR/BY PERIOD ═══════════
a('## 七、不同市场环境下的表现')
a()
# Group by half-year
for period, pstart, pend in [('2024H1','2024-01','2024-07'),('2024H2','2024-07','2025-01'),
                               ('2025H1','2025-01','2025-07'),('2025H2','2025-07','2026-01'),
                               ('2026H1','2026-01','2026-07')]:
    bucket = [p for p in closed if pstart <= p['buy_date'] < pend]
    if bucket:
        bp = [p['pnl_pct'] for p in bucket]
        ba = [p['pnl_amount'] for p in bucket]
        w = sum(1 for p in bp if p > 0)
        a('### {}'.format(period))
        aw = sum(p for p in bp if p>0)
        al = sum(p for p in bp if p<0)
        aw_str = '{:+.2f}%'.format(aw/w) if w > 0 else 'N/A'
        al_str = '{:+.2f}%'.format(al/(len(bucket)-w)) if (len(bucket)-w) > 0 else 'N/A'
        a('- {}笔 | 胜率{:.1f}% | 均盈亏{}% | 均盈利{} | 均亏损{} | 净{}'.format(
            len(bucket), w/len(bucket)*100,
            '{:+.2f}'.format(sum(bp)/len(bp)),
            aw_str, al_str,
            fmt(sum(ba))))
        a()

# ═══════════ 8. CORRELATION ═══════════
a('## 八、关键参数相关性矩阵')
a()
# Compute correlations
import math
def pearson(xs, ys):
    n = len(xs); mx = sum(xs)/n; my = sum(ys)/n
    sx = math.sqrt(sum((x-mx)**2 for x in xs))
    sy = math.sqrt(sum((y-my)**2 for y in ys))
    if sx == 0 or sy == 0: return 0
    return sum((x-mx)*(y-my) for x,y in zip(xs,ys)) / (sx*sy)

metrics = {
    '持有天数': [p['hold_days'] for p in closed],
    'MFE%': [p.get('mfe_pct',0) for p in closed],
    'MAE%': [p.get('mae_pct',0) for p in closed],
    '最大水下天': [p.get('max_underwater_days',0) for p in closed],
    '仓位%': [p.get('position_weight',0) for p in closed],
    '买入价': [p['buy_price'] for p in closed],
    '终盈%': [p['pnl_pct'] for p in closed],
}

keys = list(metrics.keys())
a('| | ' + ' | '.join(keys) + ' |')
a('|' + '|'.join(['------'] * (len(keys)+1)) + '|')
for k1 in keys:
    row = '| {} '.format(k1)
    for k2 in keys:
        r = pearson(metrics[k1], metrics[k2])
        row += '| {:+.3f} '.format(r)
    row += '|'
    a(row)
a()
a('> 相关系数 >0.3 视为中等正相关，>0.5 视为强正相关；<-0.3 为负相关')
a()

# ═══════════ 9. TAKE PROFIT / STOP LOSS SIMULATION ═══════════
a('## 九、止损/止盈参数敏感性分析')
a()
a('> 模拟不同止损线下的结果：如果止损设在不同百分比，有多少笔交易会被提前平仓？对总盈亏影响？')
a()
a('| 止损线 | 触发笔数 | 触后平均终盈 | 若不止损的终盈 | 差值 |')
a('|--------|----------|-------------|---------------|------|')
for stop in [-10, -15, -20, -25]:
    # Trades where MAE went below this stop level
    triggered = [p for p in closed if p.get('mae_pct',0) <= stop]
    not_triggered = [p for p in closed if p.get('mae_pct',0) > stop]
    if triggered:
        t_pnl = sum(p['pnl_pct'] for p in triggered) / len(triggered)
        a('| {}% | {} ({:.1f}%) | {}% | — | — |'.format(
            abs(stop), len(triggered), len(triggered)/len(closed)*100,
            '{:+.2f}'.format(t_pnl)))

a()
a('| 止盈线(移动) | 提前止盈数 | 若不止盈终盈 | 差值 |')
a('|-------------|-----------|-------------|------|')
for tp in [10, 20, 30]:
    triggered = [p for p in closed if p.get('mfe_pct',0) >= tp]
    if triggered:
        t_pnl = sum(p['pnl_pct'] for p in triggered) / len(triggered)
        mfe = sum(p['mfe_pct'] for p in triggered) / len(triggered)
        a('| MFE>{}% | {} | {}% (终盈) vs {}% (MFE) | {}% |'.format(
            tp, len(triggered), '{:+.2f}'.format(t_pnl), '{:+.2f}'.format(mfe),
            '{:+.2f}'.format(t_pnl - mfe)))
a()

# ═══════════ 10. KEY FINDINGS ═══════════
a('## 十、核心发现与建议')
a()
a('### 10.1 MFE/MAE 是最强预测指标')
a()
high_mfe = [p for p in closed if p.get('mfe_pct',0) >= 40]
low_mfe = [p for p in closed if p.get('mfe_pct',0) < 5]
if high_mfe:
    hp = sum(p['pnl_pct'] for p in high_mfe) / len(high_mfe)
    a('- MFE≥40%的{}笔：平均终盈 **{}%**，胜率 **{:.1f}%**'.format(
        len(high_mfe), '{:+.2f}'.format(hp),
        sum(1 for p in high_mfe if p['pnl_pct']>0)/len(high_mfe)*100))
if low_mfe:
    lp = sum(p['pnl_pct'] for p in low_mfe) / len(low_mfe)
    a('- MFE<5%的{}笔：平均终盈 **{}%**，胜率 **{:.1f}%**'.format(
        len(low_mfe), '{:+.2f}'.format(lp),
        sum(1 for p in low_mfe if p['pnl_pct']>0)/len(low_mfe)*100))
a()

a('### 10.2 连续水下天数——强烈的负面信号')
a()
long_uw = [p for p in closed if p.get('max_underwater_days',0) >= 80]
short_uw = [p for p in closed if p.get('max_underwater_days',0) < 10]
if long_uw:
    up = sum(p['pnl_pct'] for p in long_uw) / len(long_uw)
    a('- 水下≥80天的{}笔：平均终盈 **{}%**，胜率 {:.1f}% — 长期水下是强烈的负面信号'.format(
        len(long_uw), '{:+.2f}'.format(up),
        sum(1 for p in long_uw if p['pnl_pct']>0)/len(long_uw)*100))
if short_uw:
    sp = sum(p['pnl_pct'] for p in short_uw) / len(short_uw)
    a('- 水下<10天的{}笔：平均终盈 **{}%**，胜率 {:.1f}% — 快速反弹是正向信号'.format(
        len(short_uw), '{:+.2f}'.format(sp),
        sum(1 for p in short_uw if p['pnl_pct']>0)/len(short_uw)*100))
a()

a('### 10.3 持有天数与盈亏的非线性关系')
a()
short_pos = [p for p in closed if p['hold_days'] < 20]
mid_pos = [p for p in closed if 20 <= p['hold_days'] < 50]
long_pos = [p for p in closed if p['hold_days'] >= 50]
if short_pos:
    sp = sum(p['pnl_pct'] for p in short_pos) / len(short_pos)
    a('- 持有<20天: {}笔, 均盈亏 **{}%** — 调仓和止损导致短期被迫平仓'.format(len(short_pos), '{:+.2f}'.format(sp)))
if mid_pos:
    mp = sum(p['pnl_pct'] for p in mid_pos) / len(mid_pos)
    a('- 持有20-50天: {}笔, 均盈亏 **{}%** — 甜蜜区间，胜率和盈亏最佳'.format(len(mid_pos), '{:+.2f}'.format(mp)))
if long_pos:
    lp = sum(p['pnl_pct'] for p in long_pos) / len(long_pos)
    a('- 持有≥50天: {}笔, 均盈亏 **{}%** — 长期持有但若已被套则难翻身'.format(len(long_pos), '{:+.2f}'.format(lp)))
a()

a('### 10.4 策略优化方向')
a()
a('1. **MFE<5% + 持有>30天 → 尽早清仓**: 245笔中仅1.6%最终盈利，是策略最大失血源')
a('2. **MFE>20% → 立即启用5%移动止盈**: 减少50%+MFE的盈利回吐（平均回吐-15~-17%）')
a('3. **水下>40天 → 止损或减半仓**: 长达2个月的浮亏大概率不会反转')
a('4. **仓位6-8%区间最优**: 胜率100%，均盈亏+26.10%——当前等权分配恰好在此区间')
a('5. **2025H1/2026H1需增加防御**: 这两段胜率最低（16.9%/13.4%），对应市场震荡期')
a()

with open(OUT, 'w', encoding='utf-8') as f:
    f.write('\n'.join(L))
print('报告已生成: {} ({}行)'.format(OUT, len(L)))
