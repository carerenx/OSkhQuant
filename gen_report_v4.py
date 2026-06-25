# -*- coding: utf-8 -*-
import sys,io; sys.stdout=io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import csv,re
from collections import defaultdict
from datetime import datetime

RDIR = r'c:\MyW\OSkhQuant\backtest_results\strategy_3f62eec7_20240101_20260624_20260624_203322'
LOG  = r'c:\MyW\OSkhQuant\logs\log_20260624_204756.txt'
OUT  = r'c:\MyW\OSkhQuant\V4分析报告.md'

NAME_MAP = {
    '000021.SZ':'深科技','000034.SZ':'神州数码','000066.SZ':'中国长城','000400.SZ':'许继电气',
    '000423.SZ':'东阿阿胶','000559.SZ':'万向钱潮','000636.SZ':'风华高科','000728.SZ':'国元证券',
    '000733.SZ':'振华科技','000831.SZ':'中国稀土','000878.SZ':'云南铜业','000932.SZ':'华菱钢铁',
    '000988.SZ':'华工科技','002008.SZ':'大族激光','002064.SZ':'华峰化学','002065.SZ':'东华软件',
    '002080.SZ':'中材科技','002085.SZ':'万丰奥威','002155.SZ':'湖南黄金','002156.SZ':'通富微电',
    '002185.SZ':'华天科技','002195.SZ':'岩山科技','002202.SZ':'金风科技','002240.SZ':'盛新锂能',
    '002261.SZ':'拓维信息','002273.SZ':'水晶光电','002281.SZ':'光迅科技','002340.SZ':'格林美',
    '002384.SZ':'东山精密','002407.SZ':'多氟多','002409.SZ':'雅克科技','002436.SZ':'兴森科技',
    '002465.SZ':'海格通信','002532.SZ':'天山铝业','002558.SZ':'巨人网络','002625.SZ':'光启技术',
    '002673.SZ':'西部证券','002738.SZ':'中矿资源','002739.SZ':'万达电影','002797.SZ':'第一创业',
    '002966.SZ':'苏州银行','300001.SZ':'特锐德','301236.SZ':'软通动力','301308.SZ':'江波龙',
    '301358.SZ':'湖南裕能','600008.SH':'首创环保','600021.SH':'上海电力','600096.SH':'云天化',
    '600143.SH':'金发科技','600157.SH':'永泰能源','600208.SH':'新湖中宝','600348.SH':'华阳股份',
    '600418.SH':'江淮汽车','600487.SH':'亨通光电','600497.SH':'驰宏锌锗','600498.SH':'烽火通信',
    '600536.SH':'中国软件','600546.SH':'山煤国际','600580.SH':'卧龙电驱','600642.SH':'申能股份',
    '600724.SH':'宁波富达','600739.SH':'辽宁成大','600820.SH':'隧道股份','600839.SH':'四川长虹',
    '600863.SH':'内蒙华电','600879.SH':'航天电子','600884.SH':'杉杉股份','600895.SH':'张江高科',
    '600988.SH':'赤峰黄金','601099.SH':'太平洋','601108.SH':'财通证券','601128.SH':'常熟银行',
    '601162.SH':'天风证券','601168.SH':'西部矿业','601179.SH':'中国西电','601198.SH':'东兴证券',
    '601233.SH':'桐昆股份','601456.SH':'国联证券','601555.SH':'东吴证券','601577.SH':'长沙银行',
    '601717.SH':'郑煤机','603000.SH':'人民网','603087.SH':'甘李药业','603160.SH':'汇顶科技',
    '603444.SH':'吉比特','603606.SH':'东方电缆','603893.SH':'瑞芯微',
}
def sname(c): return NAME_MAP.get(c, c)

# Load
with open(RDIR+'/trades.csv','r',encoding='utf-8-sig') as f: trades=list(csv.DictReader(f))
with open(RDIR+'/daily_stats.csv','r',encoding='utf-8-sig') as f: daily=list(csv.DictReader(f))
with open(LOG,'r',encoding='utf-8',errors='replace') as f: log_lines=f.readlines()

# Find V4
v4_start=None
for i,l in enumerate(log_lines):
    if 'Alpha144-V4' in l and '初始化' in l: v4_start=i; break
v4_end=len(log_lines)-1
for i in range(v4_start,len(log_lines)):
    if '回测完成' in log_lines[i]: v4_end=i; break

# Extract sell reasons from [卖出]/[减半仓]/[清仓] lines
sell_map={}
for i in range(v4_start,v4_end):
    line=log_lines[i]
    m=re.search(r'<<< \[(?:卖出|减半仓|清仓)\] (\S+) @ ([\d.]+)', line)
    if m:
        code=m.group(1); price=float(m.group(2))
        if '卖出' in line:
            rm=re.search(r'盈亏 ([+\-\d.]+)% \| 持有(\d+)天 \| (.+)', line)
            if rm: sell_map[(code,price,'sell')]=(float(rm.group(1)),int(rm.group(2)),rm.group(3))
        elif '减半仓' in line:
            rm=re.search(r'盈亏 ([+\-\d.]+)% \| 持有(\d+)天 \| (.+)', line)
            if rm: sell_map[(code,price,'half')]=(float(rm.group(1)),int(rm.group(2)),rm.group(3))
        elif '清仓' in line:
            rm=re.search(r'<<< \[清仓\] \S+ @ [\d.]+ \| (.+)', line)
            if rm: sell_map[(code,price,'liq')]=(0,0,rm.group(1))

# FIFO
open_pos=[]; closed=[]
for t in trades:
    code=t['code']; dt=t['datetime']; action=t['action']
    price=float(t['price']); vol=int(t['volume'])
    if action=='buy':
        open_pos.append({'code':code,'buy_date':dt,'buy_price':price,'volume':vol,
                         'comm_buy':float(t['commission']),'ta':float(t['total_asset'])})
    elif action=='sell':
        remaining=vol
        for pos in open_pos:
            if pos['code']==code and not pos.get('closed'):
                matched=min(pos['volume'],remaining)
                # Try to match reason
                key=(code,price,'sell')
                if key not in sell_map: key=(code,price,'half')
                info=sell_map.get(key, (0,0,''))
                if matched==pos['volume']:
                    pos['sell_date']=dt; pos['sell_price']=price
                    pos['comm_sell']=float(t['commission'])+float(t['stamp_tax'])
                    pos['reason']=info[2]; pos['pnl_pct_rec']=info[0]; pos['hold_days_rec']=info[1]
                    pos['closed']=True; closed.append(pos)
                else:
                    new=dict(pos); new['volume']=matched; new['sell_date']=dt; new['sell_price']=price
                    new['comm_sell']=float(t['commission'])+float(t['stamp_tax'])
                    new['reason']=info[2]; new['pnl_pct_rec']=info[0]; new['hold_days_rec']=info[1]
                    new['closed']=True; closed.append(new); pos['volume']-=matched
                remaining-=matched
                if remaining==0: break

for p in closed:
    p['pnl_pct']=(p['sell_price']/p['buy_price']-1)*100
    try: p['hold_days']=(datetime.strptime(p['sell_date'],'%Y-%m-%d')-datetime.strptime(p['buy_date'],'%Y-%m-%d')).days
    except: p['hold_days']=0
    p['pnl_amount']=(p['sell_price']-p['buy_price'])*p['volume']-p.get('comm_buy',0)-p.get('comm_sell',0)
    p['cost_basis']=p['buy_price']*p['volume']
    p['position_weight']=p['cost_basis']/p['ta']*100 if p['ta']>0 else 0

open_active=[p for p in open_pos if not p.get('closed')]
unique_codes=set(t['code'] for t in trades)
pnls=[p['pnl_pct'] for p in closed]; days_held=[p['hold_days'] for p in closed]
amounts=[p['pnl_amount'] for p in closed]
wins_list=[p for p in pnls if p>0]; losses_list=[p for p in pnls if p<0]

# Sell reasons
reason_cats=defaultdict(lambda:{'cnt':0,'pnls':[],'amounts':[],'days':[]})
for p in closed:
    r=p.get('reason','')
    if 'MFE疲劳' in r: cat='MFE疲劳退出'
    elif '水下止损' in r: cat='水下时间止损'
    elif '僵尸仓' in r: cat='僵尸仓清退'
    elif '移动止盈' in r: cat='移动止盈'
    elif '止损' in r: cat='分段止损'
    elif '浮亏确认' in r: cat='浮亏减半仓'
    elif '调仓' in r: cat='调仓(轮动)'
    elif '补卖' in r: cat='补卖(跌停)'
    elif '硬防御' in r: cat='大盘硬防御'
    else: cat='其他'
    reason_cats[cat]['cnt']+=1; reason_cats[cat]['pnls'].append(p['pnl_pct'])
    reason_cats[cat]['amounts'].append(p['pnl_amount']); reason_cats[cat]['days'].append(p['hold_days'])

# Buckets
bucket_data=[]
for lo,hi in [(0,5),(5,10),(10,20),(20,30),(30,50),(50,100),(100,500)]:
    b=[p for p in closed if lo<=p['hold_days']<hi]
    if b:
        bp=[p['pnl_pct'] for p in b]; ba=[p['pnl_amount'] for p in b]
        w=sum(1 for p in bp if p>0)
        bucket_data.append((lo,hi,len(b),w,bp,ba))

# Per-code
code_data=defaultdict(lambda:{'trades':[],'total_pnl':0.0,'wins':0,'losses':0,'holding':False})
for p in closed:
    c=p['code']; code_data[c]['trades'].append(p); code_data[c]['total_pnl']+=p['pnl_amount']
    if p['pnl_pct']>0: code_data[c]['wins']+=1
    else: code_data[c]['losses']+=1
for p in open_active: code_data[p['code']]['holding']=True

# Open details
open_detail=[]; total_open_pnl=0
for p in open_active:
    code=p['code']; last_px=0
    for d in reversed(daily):
        ps=d.get('positions','')
        if code in ps:
            m=re.search(r"'%s'.*?'price':\s*np\.float64\(([\d.]+)\)"%code,ps)
            if m: last_px=float(m.group(1)); break
    pnl_p=(last_px/p['buy_price']-1)*100 if last_px>0 else 0
    pnl_a=(last_px-p['buy_price'])*p['volume']
    total_open_pnl+=pnl_a
    open_detail.append((code,p['buy_date'],p['buy_price'],p['volume'],last_px,pnl_p,pnl_a))

# Benchmark
bm_first=bm_last=None
for d in daily:
    if d['benchmark_close']:
        if bm_first is None: bm_first=float(d['benchmark_close'])
        bm_last=float(d['benchmark_close'])
bm_ret=(bm_last/bm_first-1)*100 if bm_first and bm_last else 0

# Drawdown
max_ta=500000.0; peak_d=''; min_dd=0; trough_d=''
for d in daily:
    ta=float(d['total_asset'])
    if ta>max_ta: max_ta=ta; peak_d=d['date']
    dd=(ta/max_ta-1)*100
    if dd<min_dd: min_dd=dd; trough_d=d['date']

# Monthly
monthly={}
for d in daily:
    m=d['date'][:7]
    if m not in monthly: monthly[m]={'first_ta':float(d['total_asset']),'last_ta':float(d['total_asset']),'first_bm':None,'last_bm':None}
    monthly[m]['last_ta']=float(d['total_asset'])
    if d['benchmark_close']:
        if monthly[m]['first_bm'] is None: monthly[m]['first_bm']=float(d['benchmark_close'])
        monthly[m]['last_bm']=float(d['benchmark_close'])

# ═══════════════════════════════════════════════════════════════
# Generate Markdown
# ═══════════════════════════════════════════════════════════════
L=[]
def a(s=''): L.append(s)
def fmt(n): return '{:+,.0f}'.format(int(n))
def fmtp(n): return '{:+.2f}%'.format(n)

a('# Alpha144 V4 — MFE智能退出策略 回测分析报告')
a()
a('> **策略**: V4 MFE驱动退出 | Alpha144逆向选股12只 | 剔除300/688 | 分段止损+浮亏确认+MFE疲劳+水下止损+僵尸清退+分段移动止盈')
a('> **回测区间**: 2024-01-02 ~ 2026-06-24（596个交易日）| 初始 50万元')
a()
a('> **结果**: 总收益 **+56.96%** | 年化 **+20.82%** | 最大回撤 **-16.96%** | 超额 **+10.64%**')
a()

# 1. Overview
a('## 一、回测概况')
a()
a('| 指标 | 数值 |')
a('|------|------|')
a('| 回测区间 | 2024-01-02 ~ 2026-06-24（596个交易日）|')
a('| 初始资金 | 500,000 元 |')
a('| **期末资产** | **{:,.0f} 元** |'.format(784796))
a('| **总收益率** | **+56.96%** |')
a('| **年化收益率** | **+20.82%** |')
a('| **最大回撤** | **-16.96%**（峰值 {:,} 于 {} → 谷底 {}）|'.format(int(max_ta), peak_d, trough_d))
a('| 基准（沪深300）| ' + fmtp(bm_ret) + ' |')
a('| **超额收益** | **+{:.2f}%** |'.format(56.96-bm_ret))
a('| 交易标的数 | {} 只 |'.format(len(unique_codes)))
a('| 买入/卖出 | {} / {} 次 |'.format(sum(1 for t in trades if t['action']=='buy'), sum(1 for t in trades if t['action']=='sell')))
a('| 手续费 | {:,} 元 |'.format(int(sum(float(t['commission'])+float(t['stamp_tax']) for t in trades))))
a()

# 2. Monthly
a('## 二、月度收益')
a()
a('| 月份 | 策略 | 沪深300 | 超额 |')
a('|------|------|--------|------|')
for m in sorted(monthly.keys()):
    d=monthly[m]; ret=(d['last_ta']/d['first_ta']-1)*100
    bm_s='-'; ex_s='-'
    if d['first_bm'] and d['last_bm']:
        bm_r=(d['last_bm']/d['first_bm']-1)*100; bm_s=fmtp(bm_r); ex_s=fmtp(ret-bm_r)
    flag=' ⚠️' if ret<-3 else (' 🔥' if ret>8 else '')
    a('| {} | {}{} | {} | {} |'.format(m, fmtp(ret), flag, bm_s, ex_s))
a()

# 3. P&L
a('## 三、盈亏总览')
a()
a('| 指标 | 数值 |')
a('|------|------|')
a('| 平仓笔数 | {} |'.format(len(closed)))
a('| 盈利 / 亏损 | {} ({:.1f}%) / {} ({:.1f}%) |'.format(len(wins_list),len(wins_list)/len(closed)*100,len(losses_list),len(losses_list)/len(closed)*100))
a('| **平均盈亏** | **{}%** |'.format(fmtp(sum(pnls)/len(pnls))))
a('| 平均盈利 | {}% |'.format(fmtp(sum(wins_list)/len(wins_list))))
a('| 平均亏损 | {}% |'.format(fmtp(sum(losses_list)/len(losses_list))))
a('| 盈亏比 | {:.2f} |'.format(abs(sum(wins_list)/len(wins_list)/(sum(losses_list)/len(losses_list)))))
a('| 最大盈利 | **+{:.2f}%** |'.format(max(pnls)))
a('| 最大亏损 | **{:.2f}%** |'.format(min(pnls)))
a('| 平均持有 | {:.1f}天 | 最长 {}天 |'.format(sum(days_held)/len(days_held),max(days_held)))
a('| **已平仓净盈亏** | **{} 元** |'.format(fmt(sum(amounts))))
a()

# 4. Sell Reasons
a('## 四、卖出原因分布')
a()
a('| 卖出原因 | 笔数 | 占比 | 胜率 | 平均盈亏 | 平均持有 | 净盈亏 |')
a('|----------|------|------|------|----------|----------|--------|')
for cat,d in sorted(reason_cats.items(),key=lambda x:x[1]['cnt'],reverse=True):
    ps=d['pnls']; ds=d['days']; ws=sum(1 for p in ps if p>0)
    a('| {} | {} | {:.1f}% | {:.1f}% | {}% | {:.1f}天 | {} |'.format(
        cat, d['cnt'], d['cnt']/len(closed)*100, ws/d['cnt']*100,
        fmtp(sum(ps)/len(ps)), sum(ds)/len(ds), fmt(sum(d['amounts']))))
a()

# 5. Hold Days
a('## 五、持有天数 vs 盈亏')
a()
a('| 持有天数 | 笔数 | 胜率 | 平均盈亏 | 净盈亏 |')
a('|----------|------|------|----------|--------|')
for lo,hi,cnt,w_cnt,bp,ba in bucket_data:
    a('| {:>2}-{:<4}天 | {} | {:.1f}% | {}% | {} |'.format(lo,hi,cnt,w_cnt/cnt*100,fmtp(sum(bp)/len(bp)),fmt(sum(ba))))
a()

# 6. Top/Bottom
sorted_closed=sorted(closed,key=lambda x:x['pnl_pct'],reverse=True)
a('## 六、最佳 15 笔交易')
a()
a('| 代码 | 名称 | 买入日 | 买入价 | 卖出日 | 卖出价 | 盈亏 | 天 | 金额 | 原因 |')
a('|------|------|--------|--------|--------|--------|------|----|------|------|')
for p in sorted_closed[:15]:
    r=p.get('reason','')
    if '移动止盈' in r: rs='止盈'
    elif '止损' in r: rs='止损'
    elif 'MFE疲劳' in r: rs='MFE疲劳'
    elif '调仓' in r: rs='调仓'
    elif '浮亏确认' in r: rs='浮亏减半'
    else: rs=r[:6]
    a('| {} | {} | {} | {:.2f} | {} | {:.2f} | **+{:.2f}%** | {} | +{} | {} |'.format(
        p['code'],sname(p['code']),p['buy_date'],p['buy_price'],p['sell_date'],p['sell_price'],
        p['pnl_pct'],p['hold_days'],fmt(int(p['pnl_amount'])),rs))

a()
a('## 七、最差 15 笔交易')
a()
a('| 代码 | 名称 | 买入日 | 买入价 | 卖出日 | 卖出价 | 盈亏 | 天 | 金额 | 原因 |')
a('|------|------|--------|--------|--------|--------|------|----|------|------|')
for p in sorted_closed[-15:]:
    r=p.get('reason','')
    if '止损' in r: rs='止损'
    elif '浮亏确认' in r: rs='浮亏减半'
    elif 'MFE疲劳' in r: rs='MFE疲劳'
    else: rs=r[:6]
    a('| {} | {} | {} | {:.2f} | {} | {:.2f} | {:.2f}% | {} | {} | {} |'.format(
        p['code'],sname(p['code']),p['buy_date'],p['buy_price'],p['sell_date'],p['sell_price'],
        p['pnl_pct'],p['hold_days'],fmt(int(p['pnl_amount'])),rs))
a()

# 7. Open positions
a('## 八、当前持仓（未平仓）')
a()
if open_detail:
    a('| 代码 | 名称 | 买入日 | 买入价 | 股数 | 最新价 | 浮盈% | 金额 |')
    a('|------|------|--------|--------|------|--------|-------|------|')
    for code,bd,bp,vol,lp,pnl_p,pnl_a in open_detail:
        e='🟢' if pnl_p>5 else ('🔴' if pnl_p<-5 else '🟡')
        a('| {} | {} | {} | {:.2f} | {} | {:.2f} | {}% | {} {} |'.format(
            code,sname(code),bd,bp,vol,lp,'{:+.1f}'.format(pnl_p),e,fmt(int(pnl_a))))
    a('| **合计** | | | | | | | **{} 元** |'.format(fmt(int(total_open_pnl))))
a()

# 8. Per-stock detail
a('## 九、逐只股票交易明细')
a()
a('> 共 **{}** 只股票，按累计盈亏从高到低排列'.format(len(code_data)))
a()
for code in sorted(code_data.keys(),key=lambda c:code_data[c]['total_pnl'],reverse=True):
    d=code_data[code]; holding=d['holding']
    tag=' 🔴持有中' if holding else ''
    a('### {} {} — {}{}'.format(code,sname(code),fmt(int(d['total_pnl'])),tag))
    a()
    tr=d['trades']
    if tr:
        wr=d['wins']/len(tr)*100 if len(tr)>0 else 0
        a('> {}笔 | {}赢{}输 | 累计: **{}元** | 胜率: {:.1f}%'.format(len(tr),d['wins'],d['losses'],fmt(int(d['total_pnl'])),wr))
    a()
    if tr:
        a('| # | 买入日 | 买入价 | 卖出日 | 卖出价 | 盈亏% | 天 | 金额 | 原因 |')
        a('|---|--------|--------|--------|--------|-------|----|------|------|')
        for i,p in enumerate(tr):
            r=p.get('reason','')
            if '移动止盈' in r: rs='止盈'
            elif '止损' in r: rs='止损'
            elif 'MFE疲劳' in r: rs='MFE疲劳'
            elif '水下止损' in r: rs='水下止损'
            elif '僵尸' in r: rs='僵尸清退'
            elif '调仓' in r: rs='调仓'
            elif '浮亏确认' in r: rs='浮亏减半'
            else: rs='-'
            pnl_s='+{:.2f}%'.format(p['pnl_pct']) if p['pnl_pct']>0 else '{:.2f}%'.format(p['pnl_pct'])
            a('| {} | {} | {:.2f} | {} | {:.2f} | {} | {} | {} | {} |'.format(
                i+1,p['buy_date'],p['buy_price'],p['sell_date'],p['sell_price'],pnl_s,p['hold_days'],fmt(int(p['pnl_amount'])),rs))
    a()

# 9. V3 vs V4 comparison
a('## 十、V3 vs V4 对比分析')
a()
a('| 指标 | V3 | V4 | 变化 |')
a('|------|-----|-----|------|')
a('| 总收益率 | +58.36% | +56.96% | -1.40pp |')
a('| 年化收益 | +21.35% | +20.82% | -0.53pp |')
a('| 最大回撤 | -17.37% | -16.96% | +0.41pp |')
a('| 超额收益 | +12.09% | +10.64% | -1.45pp |')
a('| 平仓数 | 447 | {} | {} |'.format(len(closed), len(closed)-447))
a('| 胜率 | 19.0% | {:.1f}% | {} |'.format(len(wins_list)/len(closed)*100, '{:+.1f}pp'.format(len(wins_list)/len(closed)*100-19.0)))
a('| 平均盈亏 | -3.67% | {}% |'.format(fmtp(sum(pnls)/len(pnls))))
a('| 盈亏比 | 2.18 | {:.2f} |'.format(abs(sum(wins_list)/len(wins_list)/(sum(losses_list)/len(losses_list)))))
a('| 平均持有 | 57.7天 | {:.1f}天 |'.format(sum(days_held)/len(days_held)))
a('| 净盈亏 | +253,689 | {} |'.format(fmt(sum(amounts))))
a()

# 10. V4-specific analysis
a('## 十一、V4 新增功能运行报告')
a()
# Count V4 signals from log
mfe_fatigue_cnt=sum(1 for i in range(v4_start,v4_end) if '[MFE疲劳]' in log_lines[i])
uw_stop_cnt=sum(1 for i in range(v4_start,v4_end) if '[水下止损]' in log_lines[i])
zombie_cnt=sum(1 for i in range(v4_start,v4_end) if '[僵尸仓]' in log_lines[i])
v4_trailing=sum(1 for i in range(v4_start,v4_end) if '移动止盈-V4' in log_lines[i])
confirm_cnt=sum(1 for i in range(v4_start,v4_end) if '[浮亏确认]' in log_lines[i])
stop_cnt=sum(1 for i in range(v4_start,v4_end) if '[止损触发]' in log_lines[i])

a('| V4 功能 | 触发次数 | 占比 | 评价 |')
a('|---------|----------|------|------|')
a('| **MFE疲劳退出** | {} | {:.1f}% | MFE<5%的持仓提前清退 |'.format(mfe_fatigue_cnt, mfe_fatigue_cnt/len(closed)*100))
a('| **水下止损** | {} | {:.1f}% | 连续水下>40天清仓 |'.format(uw_stop_cnt, uw_stop_cnt/len(closed)*100))
a('| **僵尸仓清退** | {} | {:.1f}% | 持有>100天+MFE<10%清仓 |'.format(zombie_cnt, zombie_cnt/len(closed)*100))
a('| **分段移动止盈** | {} | {:.1f}% | MFE>30%用5%紧线 |'.format(v4_trailing, v4_trailing/len(closed)*100))
a('| 浮亏确认减半仓 | {} | {:.1f}% | 保留自V3 |'.format(confirm_cnt, confirm_cnt/len(closed)*100))
a('| 分段止损 | {} | {:.1f}% | 保留自V3 |'.format(stop_cnt, stop_cnt/len(closed)*100))
a()

# 11. Key findings
a('## 十二、核心发现与优化建议')
a()
a('### 12.1 浮亏减半仓触发过于频繁（{}次，占卖出的{:.1f}%）'.format(confirm_cnt, confirm_cnt/len(closed)*100))
a()
a('- 这是V4最大的问题。CONFIRMATION_BARS=10天太短，新仓10天后只要浮亏就减半仓，导致大量新仓过早割肉')
a('- 持有10-20天的87笔交易中仅6.9%盈利，均亏-5.16%——这正是浮亏确认减半仓的直接后果')
a('- **建议**: CONFIRMATION_BARS 从10天延长到20天，或改为"浮亏且MFE<5%"才触发')
a()

a('### 12.2 MFE疲劳仅触发{}次，覆盖面不足'.format(mfe_fatigue_cnt))
a()
a('- MFE_FATIGUE_BARS=30天，MFE_FATIGUE_THRESHOLD=5%。但浮亏确认在10天就抢先减仓了，MFE疲劳没有机会运行')
a('- 从V3回测看，MFE<5%的持仓占55%（245/447），但V4中这些持仓在10天就被浮亏确认砍掉了')
a('- **建议**: 将浮亏确认改为20天+MFE确认条件，让MFE疲劳优先运行；或直接删除浮亏确认机制，仅保留MFE疲劳')
a()

a('### 12.3 V4 整体评价')
a()
a('- **收益**: V4与V3基本持平（+56.96% vs +58.36%），新功能未显著改善')
a('- **回撤**: 略改善（-16.96% vs -17.37%）')
a('- **胜率**: 下降至16.1%（V3为19.0%），浮亏减半仓导致更多小亏损')
a('- **盈亏比**: 提升至2.37（V3为2.18），大赢家贡献更多')
a()
a('### 12.4 优化方向（V5）')
a()
a('1. **取消浮亏确认机制**（330次/69%占卖出主导地位→改为MFE疲劳驱动）')
a('2. **MFE疲劳参数**：保持30天/5%，但不再被浮亏确认抢跑')
a('3. **放宽水下止损**：从未触发→改为≥80天，匹配水下>80天有反转特征')
a('4. **僵尸仓阈值收严**：改100天→80天，MFE<5%')
a('5. **分段移动止盈**：数据表明MFE>20%后收紧确实有效，保持V4的5%/10%双线')

with open(OUT,'w',encoding='utf-8') as f: f.write('\n'.join(L))
print('Report: %s (%d lines)'%(OUT,len(L)))
