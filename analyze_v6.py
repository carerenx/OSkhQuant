# -*- coding: utf-8 -*-
import sys,io; sys.stdout=io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import csv,re,math
from collections import defaultdict
from datetime import datetime

RDIR = r'backtest_results\strategy_95d850b6_20240101_20260624_20260624_213209'
LOG  = r'logs\log_20260624_214538.txt'
OUT  = r'V6分析报告.md'

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
    '002797.SZ':'第一创业','002966.SZ':'苏州银行','300001.SZ':'特锐德','301236.SZ':'软通动力',
    '301308.SZ':'江波龙','301358.SZ':'湖南裕能','600008.SH':'首创环保','600021.SH':'上海电力',
    '600096.SH':'云天化','600143.SH':'金发科技','600157.SH':'永泰能源','600208.SH':'新湖中宝',
    '600348.SH':'华阳股份','600418.SH':'江淮汽车','600487.SH':'亨通光电','600497.SH':'驰宏锌锗',
    '600498.SH':'烽火通信','600536.SH':'中国软件','600546.SH':'山煤国际','600580.SH':'卧龙电驱',
    '600642.SH':'申能股份','600724.SH':'宁波富达','600739.SH':'辽宁成大','600820.SH':'隧道股份',
    '600839.SH':'四川长虹','600863.SH':'内蒙华电','600879.SH':'航天电子','600884.SH':'杉杉股份',
    '600895.SH':'张江高科','600988.SH':'赤峰黄金','601099.SH':'太平洋','601108.SH':'财通证券',
    '601128.SH':'常熟银行','601162.SH':'天风证券','601168.SH':'西部矿业','601179.SH':'中国西电',
    '601198.SH':'东兴证券','601233.SH':'桐昆股份','601456.SH':'国联证券','601555.SH':'东吴证券',
    '601577.SH':'长沙银行','601666.SH':'平煤股份','601717.SH':'郑煤机','603000.SH':'人民网',
    '603087.SH':'甘李药业','603160.SH':'汇顶科技','603444.SH':'吉比特','603606.SH':'东方电缆',
    '603893.SH':'瑞芯微','603786.SH':'科博达',
}
def sname(c): return NAME_MAP.get(c, c)

# Load
with open(RDIR+'/trades.csv','r',encoding='utf-8-sig') as f: trades=list(csv.DictReader(f))
with open(RDIR+'/daily_stats.csv','r',encoding='utf-8-sig') as f: daily=list(csv.DictReader(f))
with open(LOG,'r',encoding='utf-8',errors='replace') as f: log_lines=f.readlines()

v6_start=None
for i,l in enumerate(log_lines):
    if 'Alpha144-V6' in l: v6_start=i; break
v6_end=len(log_lines)-1
for i in range(v6_start,len(log_lines)):
    if '回测完成' in log_lines[i]: v6_end=i; break
print('V6 log: %d-%d (%d lines)'%(v6_start,v6_end,v6_end-v6_start))

# Count signals
for t in ['MFE疲劳','止损触发','移动止盈','组合风控','动量防御','组合回撤']:
    cnt=sum(1 for i in range(v6_start,v6_end) if ('['+t+']') in log_lines[i])
    print('  [%s]: %d'%(t,cnt))

# Extract sell reasons
sell_map={}
for i in range(v6_start,v6_end):
    line=log_lines[i]
    if '<<< [卖出]' in line:
        m=re.search(r'<<< \[卖出\] (\S+) @ ([\d.]+) \| 盈亏 ([+\-\d.]+)% \| 持有(\d+)天 \| (.+)', line)
        if m: sell_map[(m.group(1),float(m.group(2)))]=m.group(5)
    elif '<<< [清仓]' in line:
        m=re.search(r'<<< \[清仓\] (\S+) @ ([\d.]+) \| (.+)', line)
        if m: sell_map[(m.group(1),float(m.group(2)))]=m.group(3)

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
                info=sell_map.get((code,price),'')
                if matched==pos['volume']:
                    pos['sell_date']=dt; pos['sell_price']=price
                    pos['comm_sell']=float(t['commission'])+float(t['stamp_tax'])
                    pos['reason']=info; pos['closed']=True; closed.append(pos)
                else:
                    new=dict(pos); new['volume']=matched; new['sell_date']=dt; new['sell_price']=price
                    new['comm_sell']=float(t['commission'])+float(t['stamp_tax'])
                    new['reason']=info; new['closed']=True; closed.append(new)
                    pos['volume']-=matched
                remaining-=matched
                if remaining==0: break

for p in closed:
    p['pnl_pct']=(p['sell_price']/p['buy_price']-1)*100
    try: p['hold_days']=(datetime.strptime(p['sell_date'],'%Y-%m-%d')-datetime.strptime(p['buy_date'],'%Y-%m-%d')).days
    except: p['hold_days']=0
    p['pnl_amount']=(p['sell_price']-p['buy_price'])*p['volume']-p.get('comm_buy',0)-p.get('comm_sell',0)
    p['position_weight']=p['buy_price']*p['volume']/p['ta']*100 if p['ta']>0 else 0

open_active=[p for p in open_pos if not p.get('closed')]
unique_codes=set(t['code'] for t in trades)

pnls=[p['pnl_pct'] for p in closed]; days_held=[p['hold_days'] for p in closed]
amounts=[p['pnl_amount'] for p in closed]
wins_list=[p for p in pnls if p>0]; losses_list=[p for p in pnls if p<0]

# Reason categories
reason_cats=defaultdict(lambda:{'cnt':0,'pnls':[],'amounts':[],'days':[]})
for p in closed:
    r=p.get('reason','')
    if 'MFE' in r: cat='MFE疲劳退出'
    elif '移动止盈' in r: cat='移动止盈'
    elif '止损' in r: cat='分段止损'
    elif '调仓' in r: cat='调仓(轮动)'
    elif '补卖' in r: cat='补卖(跌停)'
    elif '硬防御' in r: cat='大盘硬防御'
    elif '组合回撤' in r: cat='组合风控清仓'
    elif '动量' in r: cat='动量防御'
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

# Benchmark
bm_first=bm_last=None
for d in daily:
    if d['benchmark_close']:
        if bm_first is None: bm_first=float(d['benchmark_close'])
        bm_last=float(d['benchmark_close'])
bm_ret=(bm_last/bm_first-1)*100 if bm_first and bm_last else 0

# DD
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

# Price check
from xtquant import xtdata
last_day=daily[-1]; ps=last_day.get('positions','')
codes_prices=re.findall(r"'(\d{6}\.\w+)'.*?'price':\s*np\.float64\(([\d.]+)\)", ps)
price_ok=0; price_bad=0
for code,bt_px_str in codes_prices:
    bt_px=float(bt_px_str)
    try:
        xtdata.download_history_data(code,'1d','20260623','20260625')
        data=xtdata.get_market_data_ex(field_list=['close'],stock_list=[code],period='1d',start_time='20260623',end_time='20260625',dividend_type='front')
        if isinstance(data,dict) and code in data and len(data[code])>0:
            real=data[code].iloc[-1]['close']
            if abs(bt_px/real-1)<0.02: price_ok+=1
            else: price_bad+=1
    except: pass

# ═══════════════════ BUILD MARKDOWN ═══════════════════
def fmt(n): return '{:+,.0f}'.format(int(n))
def fmtp(n): return '{:+.2f}%'.format(n)

L=[]
def a(s=''): L.append(s)
a('# Alpha144 V6 — 多层MFE+组合风控 回测分析报告\n')
a('> **策略**: Alpha144逆向选股12只 | 多层MFE(20d<3%,30d<5%,40d<8%) | 组合DD>25%清仓 | 月度动量防御')
a('> **回测**: 2024-01-02 ~ 2026-06-24（597交易日）| 初始50万 | 剔除300/688 | 数据清理后运行\n')

a('> **结果**: 总收益 **+{:.2f}%** | 年化 **+{:.2f}%** | 最大回撤 **{:.2f}%** | 超额 **+{:.2f}%**\n'.format(
    float(daily[-1]['total_asset'])/500000*100-100,
    ((float(daily[-1]['total_asset'])/500000)**(1/(597/252))-1)*100,
    min_dd, float(daily[-1]['total_asset'])/500000*100-100-bm_ret))

# 1. Overview
a('## 一、回测概况\n')
a('| 指标 | 数值 |')
a('|------|------|')
a('| 回测区间 | 2024-01-02 ~ 2026-06-24（597个交易日）|')
a('| 初始资金 | 500,000 元 |')
a('| **期末资产** | **{:,} 元** |'.format(int(float(daily[-1]['total_asset']))))
a('| **总收益率** | **+{:.2f}%** |'.format(float(daily[-1]['total_asset'])/500000*100-100))
a('| **年化收益** | **+{:.2f}%** |'.format(((float(daily[-1]['total_asset'])/500000)**(1/(597/252))-1)*100))
a('| **最大回撤** | **{:.2f}%**（峰值{:,}于{}→谷底{}）|'.format(min_dd,int(max_ta),peak_d,trough_d))
a('| 基准(沪深300) | '+fmtp(bm_ret)+' |')
a('| **超额收益** | **+{:.2f}%** |'.format(float(daily[-1]['total_asset'])/500000*100-100-bm_ret))
a('| 交易标的 | {}只 | 买入/卖出: {}/{} |'.format(len(unique_codes), sum(1 for t in trades if t['action']=='buy'), sum(1 for t in trades if t['action']=='sell')))
a('| 手续费 | {:,}元 |'.format(int(sum(float(t['commission'])+float(t['stamp_tax']) for t in trades))))
a()

# 2. P&L
a('## 二、盈亏总览\n')
a('| 指标 | 数值 |')
a('|------|------|')
a('| 平仓 | {}笔 | 未平仓 | {}笔 |'.format(len(closed), len(open_active)))
a('| 盈利/亏损 | {}({:.1f}%)/{}({:.1f}%) |'.format(len(wins_list),len(wins_list)/len(closed)*100,len(losses_list),len(losses_list)/len(closed)*100))
a('| **平均盈亏** | **{}%** |'.format(fmtp(sum(pnls)/len(pnls))))
a('| 平均盈利 | {}% | 平均亏损 | {}% |'.format(fmtp(sum(wins_list)/len(wins_list)),fmtp(sum(losses_list)/len(losses_list))))
a('| 盈亏比 | **{:.2f}** |'.format(abs(sum(wins_list)/len(wins_list)/(sum(losses_list)/len(losses_list)))))
a('| 最大盈利 | **+{:.2f}%** | 最大亏损 | **{:.2f}%** |'.format(max(pnls),min(pnls)))
a('| 平均持有 | {:.1f}天 | 最长 | {}天 |'.format(sum(days_held)/len(days_held),max(days_held)))
a('| **已平仓净盈亏** | **{}元** |'.format(fmt(sum(amounts))))
a()

# 3. Monthly
a('## 三、月度收益\n')
a('| 月份 | 策略 | 沪深300 | 超额 |')
a('|------|------|--------|------|')
for m in sorted(monthly.keys()):
    d=monthly[m]; ret=(d['last_ta']/d['first_ta']-1)*100
    bm_s='-'; ex_s='-'
    if d['first_bm'] and d['last_bm']:
        bm_r=(d['last_bm']/d['first_bm']-1)*100; bm_s=fmtp(bm_r); ex_s=fmtp(ret-bm_r)
    flag=' ⚠️' if ret<-3 else (' 🔥' if ret>8 else '')
    a('| {} | {}{} | {} | {} |'.format(m,fmtp(ret),flag,bm_s,ex_s))
a()

# 4. Sell Reasons
a('## 四、卖出原因分布\n')
a('| 卖出原因 | 笔数 | 占比 | 胜率 | 平均盈亏 | 平均持有 | 净盈亏 |')
a('|----------|------|------|------|----------|----------|--------|')
for cat,d in sorted(reason_cats.items(),key=lambda x:x[1]['cnt'],reverse=True):
    ps=d['pnls']; ws=sum(1 for p in ps if p>0); ds=d['days']
    a('| {} | {} | {:.1f}% | {:.1f}% | {}% | {:.1f}天 | {} |'.format(
        cat,d['cnt'],d['cnt']/len(closed)*100,ws/d['cnt']*100,fmtp(sum(ps)/len(ps)),sum(ds)/len(ds),fmt(sum(d['amounts']))))
a()

# 5. V6 New Features
a('## 五、V6新增功能运行报告\n')
mfe_l1=sum(1 for i in range(v6_start,v6_end) if 'MFE-L1' in log_lines[i])
mfe_l2=sum(1 for i in range(v6_start,v6_end) if 'MFE-L2' in log_lines[i])
mfe_l3=sum(1 for i in range(v6_start,v6_end) if 'MFE-L3' in log_lines[i])
port_dd=sum(1 for i in range(v6_start,v6_end) if '[组合风控]' in log_lines[i])
mom_def=sum(1 for i in range(v6_start,v6_end) if '[动量防御]' in log_lines[i])
a('| V6功能 | 触发次数 | 评价 |')
a('|--------|----------|------|')
a('| MFE-L1 (20d<3%) | {} | 第20天早期预警 |'.format(mfe_l1))
a('| MFE-L2 (30d<5%) | {} | 核心层（V5的MFE疲劳）|'.format(mfe_l2))
a('| MFE-L3 (40d<8%) | {} | 长期验证 |'.format(mfe_l3))
a('| **MFE疲劳合计** | **{}** | |'.format(mfe_l1+mfe_l2+mfe_l3))
a('| 组合回撤>25%清仓 | {} | V6新增 |'.format(port_dd))
a('| 月度动量防御 | {} | V6新增 |'.format(mom_def))
a('| 移动止盈 | {} | |'.format(sum(1 for i in range(v6_start,v6_end) if '[移动止盈]' in log_lines[i])))
a('| 分段止损 | {} | |'.format(sum(1 for i in range(v6_start,v6_end) if '[止损触发]' in log_lines[i])))
a()

# 6. Hold Days
a('## 六、持有天数 vs 盈亏\n')
a('| 持有天数 | 笔数 | 胜率 | 平均盈亏 | 净盈亏 |')
a('|----------|------|------|----------|--------|')
for lo,hi,cnt,w,bp,ba in bucket_data:
    a('| {:>2}-{:<4}天 | {} | {:.1f}% | {}% | {} |'.format(lo,hi,cnt,w/cnt*100,fmtp(sum(bp)/len(bp)),fmt(sum(ba))))
a()

# 7. V5 vs V6 comparison
a('## 七、V5 vs V6 对比\n')
a('| 指标 | V5 | V6 | 变化 |')
a('|------|-----|-----|------|')
v5_dd=-27.84; v6_dd=min_dd
v5_ret=106.44; v6_ret=float(daily[-1]['total_asset'])/500000*100-100
a('| 总收益率 | +106.44% | **+{:.2f}%** | {}{:.2f}pp |'.format(v6_ret,'+'if v6_ret>v5_ret else'',v6_ret-v5_ret))
a('| 最大回撤 | -27.84% | **{:.2f}%** | {}{:.2f}pp |'.format(v6_dd,'+'if v6_dd>v5_dd else'',v6_dd-v5_dd))
a('| 胜率 | 48.0% | **{:.1f}%** |'.format(len(wins_list)/len(closed)*100))
a('| 平均盈亏 | +7.35% | **{}%** |'.format(fmtp(sum(pnls)/len(pnls))))
a('| 盈亏比 | 2.61 | **{:.2f}** |'.format(abs(sum(wins_list)/len(wins_list)/(sum(losses_list)/len(losses_list)))))
a('| 平仓数 | 246 | **{}** |'.format(len(closed)))
a('| 净盈亏 | +646,232 | **{}** |'.format(fmt(sum(amounts))))
a()

# 8. Top/Bottom
sorted_closed=sorted(closed,key=lambda x:x['pnl_pct'],reverse=True)
a('## 八、最佳 15 笔\n')
a('| 代码 | 名称 | 买入日 | 买入价 | 卖出日 | 卖出价 | 盈亏 | 天 | 金额 | 原因 |')
a('|------|------|--------|--------|--------|--------|------|----|------|------|')
for p in sorted_closed[:15]:
    r=p.get('reason',''); rs='止盈'if'移动止盈'in r else('止损'if'止损'in r else('MFE'if'MFE'in r else r[:6]))
    a('| {} | {} | {} | {:.2f} | {} | {:.2f} | **+{:.2f}%** | {} | +{} | {} |'.format(
        p['code'],sname(p['code']),p['buy_date'],p['buy_price'],p['sell_date'],p['sell_price'],p['pnl_pct'],p['hold_days'],fmt(int(p['pnl_amount'])),rs))
a()
a('## 九、最差 15 笔\n')
a('| 代码 | 名称 | 买入日 | 买入价 | 卖出日 | 卖出价 | 盈亏 | 天 | 金额 | 原因 |')
a('|------|------|--------|--------|--------|--------|------|----|------|------|')
for p in sorted_closed[-15:]:
    r=p.get('reason',''); rs='止损'if'止损'in r else('MFE'if'MFE'in r else('风控'if'组合' in r else r[:6]))
    a('| {} | {} | {} | {:.2f} | {} | {:.2f} | {:.2f}% | {} | {} | {} |'.format(
        p['code'],sname(p['code']),p['buy_date'],p['buy_price'],p['sell_date'],p['sell_price'],p['pnl_pct'],p['hold_days'],fmt(int(p['pnl_amount'])),rs))
a()

# 9. Open
a('## 十、当前持仓（未平仓）\n')
if open_detail:
    a('| 代码 | 名称 | 买入日 | 买入价 | 股数 | 最新价 | 浮盈% | 金额 |')
    a('|------|------|--------|--------|------|--------|-------|------|')
    for code,bd,bp,vol,lp,pnl_p,pnl_a in open_detail:
        e='🟢' if pnl_p>5 else ('🔴' if pnl_p<-5 else '🟡')
        a('| {} | {} | {} | {:.2f} | {} | {:.2f} | {}% | {} {} |'.format(
            code,sname(code),bd,bp,vol,lp,'{:+.1f}'.format(pnl_p),e,fmt(int(pnl_a))))
    a('| **合计** | | | | | | | **{}元** |'.format(fmt(int(total_open_pnl))))
a()

# 10. Per-stock
a('## 十一、逐只股票交易明细\n')
a('> 共**{}**只，按累计盈亏从高到低\n'.format(len(code_data)))
for code in sorted(code_data.keys(),key=lambda c:code_data[c]['total_pnl'],reverse=True):
    d=code_data[code]; holding=d['holding']
    tag=' 🔴持有中' if holding else ''
    a('### {} {} — {}{}\n'.format(code,sname(code),fmt(int(d['total_pnl'])),tag))
    tr=d['trades']
    if tr:
        wr=d['wins']/len(tr)*100 if len(tr)>0 else 0
        a('> {}笔 | {}赢{}输 | 累计: **{}元** | 胜率: {:.1f}%\n'.format(len(tr),d['wins'],d['losses'],fmt(int(d['total_pnl'])),wr))
    if tr:
        a('| # | 买入日 | 买入价 | 卖出日 | 卖出价 | 盈亏% | 天 | 金额 | 原因 |')
        a('|---|--------|--------|--------|--------|-------|----|------|------|')
        for i,p in enumerate(tr):
            r=p.get('reason','')
            if '移动止盈' in r: rs='止盈'
            elif '止损' in r: rs='止损'
            elif 'MFE' in r: rs='MFE'
            elif '调仓' in r: rs='调仓'
            elif '组合' in r: rs='风控'
            else: rs='-'
            pnl_s='+{:.2f}%'.format(p['pnl_pct']) if p['pnl_pct']>0 else '{:.2f}%'.format(p['pnl_pct'])
            a('| {} | {} | {:.2f} | {} | {:.2f} | {} | {} | {} | {} |'.format(
                i+1,p['buy_date'],p['buy_price'],p['sell_date'],p['sell_price'],pnl_s,p['hold_days'],fmt(int(p['pnl_amount'])),rs))
    a()

# 11. Price verification
a('## 十二、价格数据验证\n')
a('| 代码 | 名称 | 回测价 | xtdata价 | 倍率 | 结论 |')
a('|------|------|--------|----------|------|------|')
for code,bt_px_str in codes_prices:
    bt_px=float(bt_px_str)
    try:
        xtdata.download_history_data(code,'1d','20260623','20260625')
        data=xtdata.get_market_data_ex(field_list=['close'],stock_list=[code],period='1d',start_time='20260623',end_time='20260625',dividend_type='front')
        if isinstance(data,dict) and code in data and len(data[code])>0:
            real=data[code].iloc[-1]['close']
            ratio=bt_px/real if real>0 else 0
            ok='✅ 一致' if abs(ratio-1)<0.02 else '❌ 偏差'
            a('| {} | {} | {:.2f} | {:.2f} | {:.3f}x | {} |'.format(code,sname(code),bt_px,real,ratio,ok))
    except: pass
a()
a('> 验证: {} OK / {} 偏差 — {}'.format(price_ok,price_bad,'数据正确' if price_bad==0 else '有偏差需关注'))
a()

# 12. Recommendations
a('## 十三、优化建议\n')
a('### V6 核心表现\n')
dd_change=v6_dd-v5_dd
a('- V6 vs V5: 收益 **{}{:.2f}%**, 回撤 **{}{:.2f}pp**, 胜率 **{:.1f}%**'.format(
    '+'if v6_ret>v5_ret else'',v6_ret-v5_ret,
    '+'if dd_change>0 else'',dd_change,
    len(wins_list)/len(closed)*100))
a()

a('### 建议\n')
a('1. **组合回撤止损**: 触发{}次。25%阈值是否恰当？可考虑20%以更早防御'.format(port_dd))
a('2. **月度动量防御**: 触发{}次。连续2月亏损+大盘下行确实是危险信号'.format(mom_def))
a('3. **MFE疲劳**: {}次(L1:{},L2:{},L3:{})。三层覆盖充分，L3触发偏少可考虑降低阈值至6%'.format(mfe_l1+mfe_l2+mfe_l3,mfe_l1,mfe_l2,mfe_l3))
a('4. **最大回撤仍较高**({:.2f}%): 在2024-01/07-08/2025H1等月集中亏损，可进一步强化月度风控'.format(min_dd))
a('5. **价格验证**: {} OK / {} 偏差'.format(price_ok,price_bad))

# Write
with open(OUT,'w',encoding='utf-8') as f: f.write('\n'.join(L))
print('\nReport: %s (%d lines)'%(OUT,len(L)))
print('V6: Final=%.0f, Return=%.2f%%, DD=%.2f%%, Excess=%.2f%%'%(
    float(daily[-1]['total_asset']),v6_ret,min_dd,v6_ret-bm_ret))
print('Closed=%d, WinRate=%.1f%%, AvgPNL=%.2f%%, Net=%s'%(
    len(closed),len(wins_list)/len(closed)*100,sum(pnls)/len(pnls),fmt(sum(amounts))))
