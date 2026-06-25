# -*- coding: utf-8 -*-
import sys,io; sys.stdout=io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import csv,re
from collections import defaultdict
from datetime import datetime

RDIR = r'backtest_results\strategy_6ade13b2_20240101_20260624_20260624_205640'
LOG  = r'logs\log_20260624_211202.txt'

with open(RDIR+'/trades.csv','r',encoding='utf-8-sig') as f: trades=list(csv.DictReader(f))
with open(RDIR+'/daily_stats.csv','r',encoding='utf-8-sig') as f: daily=list(csv.DictReader(f))
with open(LOG,'r',encoding='utf-8',errors='replace') as f: log_lines=f.readlines()

v5_start=None
for i,l in enumerate(log_lines):
    if 'Alpha144-V5' in l: v5_start=i; break
v5_end=len(log_lines)-1
for i in range(v5_start,len(log_lines)):
    if '回测完成' in log_lines[i]: v5_end=i; break
print('V5 log: lines %d-%d (%d lines)'%(v5_start,v5_end,v5_end-v5_start))

# Count V5 specific signals
for term in ['MFE疲劳','水下止损','僵尸仓','移动止盈','止损触发']:
    cnt=sum(1 for i in range(v5_start,v5_end) if ('['+term+']') in log_lines[i])
    print('  [%s]: %d'%(term,cnt))

# Extract sell reasons
sell_map={}
for i in range(v5_start,v5_end):
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
    if 'MFE疲劳' in r: cat='MFE疲劳退出'
    elif '水下止损' in r: cat='水下时间止损'
    elif '僵尸仓' in r: cat='僵尸仓清退'
    elif '移动止盈' in r: cat='移动止盈'
    elif '止损' in r: cat='分段止损'
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

# Open
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

# ═════════════════════ PRINT ═════════════════════
def fmt(n): return '{:+,.0f}'.format(int(n))
def fmtp(n): return '{:+.2f}%'.format(n)

print()
print('='*60)
print('  Alpha144 V5 — 纯MFE驱动退出 回测分析')
print('='*60)
print()
print('期末资产: 1,032,201 | +106.44% | 年化+35.46% | 最大回撤-27.84%')
print('基准: '+fmtp(bm_ret)+' | 超额: +%.2f%%'%(106.44-bm_ret))
print('价格验证: %d OK / %d 偏差'%(price_ok,price_bad))
print()
print('平仓:%d | 盈利:%d(%.1f%%) | 亏损:%d(%.1f%%)'%(
    len(closed),len(wins_list),len(wins_list)/len(closed)*100,
    len(losses_list),len(losses_list)/len(closed)*100))
print('均盈亏:%s | 均盈:%s | 均亏:%s | 盈亏比:%.2f'%(
    fmtp(sum(pnls)/len(pnls)),fmtp(sum(wins_list)/len(wins_list)),
    fmtp(sum(losses_list)/len(losses_list)),
    abs(sum(wins_list)/len(wins_list)/(sum(losses_list)/len(losses_list)))))
print('最大赢:+%.2f%% | 最大亏:%.2f%% | 均持:%.1fd | 已平净额:%s'%(
    max(pnls),min(pnls),sum(days_held)/len(days_held),fmt(sum(amounts))))

print()
print('--- 卖出原因 ---')
for cat,d in sorted(reason_cats.items(),key=lambda x:x[1]['cnt'],reverse=True):
    ps=d['pnls']; ws=sum(1 for p in ps if p>0)
    print('%s: %d | 胜率=%.1f%% | 均盈=%s | 净=%s'%(
        cat,d['cnt'],ws/d['cnt']*100,fmtp(sum(ps)/len(ps)),fmt(sum(d['amounts']))))

print()
print('--- 持有天数 ---')
for lo,hi,cnt,w,bp,ba in bucket_data:
    print('%3d-%3dd: %4d | 胜率=%5.1f%% | 均盈=%s | 净=%s'%(
        lo,hi,cnt,w/cnt*100,fmtp(sum(bp)/len(bp)),fmt(sum(ba))))

print()
print('--- Top 10 Best ---')
for p in sorted(closed,key=lambda x:x['pnl_pct'],reverse=True)[:10]:
    print('%s %s@%.2f->%s@%.2f |+%.2f%%|%3dd|+%.0f'%(
        p['code'],p['buy_date'],p['buy_price'],p['sell_date'],p['sell_price'],
        p['pnl_pct'],p['hold_days'],p['pnl_amount']))
print('--- Top 10 Worst ---')
for p in sorted(closed,key=lambda x:x['pnl_pct'])[:10]:
    print('%s %s@%.2f->%s@%.2f |%.2f%%|%3dd|%.0f'%(
        p['code'],p['buy_date'],p['buy_price'],p['sell_date'],p['sell_price'],
        p['pnl_pct'],p['hold_days'],p['pnl_amount']))

print()
print('--- V5 vs V3/V4 ---')
print('V3: +58.36% | V4: +56.96% | V5: +106.44%')
print('Peak: %d @ %s -> DD: %.2f%% @ %s'%(int(max_ta),peak_d,min_dd,trough_d))

print()
print('--- Monthly ---')
for m in sorted(monthly.keys()):
    d=monthly[m]; ret=(d['last_ta']/d['first_ta']-1)*100
    bm_s='-'
    if d['first_bm'] and d['last_bm']:
        bm_s=fmtp((d['last_bm']/d['first_bm']-1)*100)
    flag=' !!' if ret<-3 else (' ++' if ret>8 else '')
    print('%s: %s%s (BM: %s)'%(m,fmtp(ret),flag,bm_s))
