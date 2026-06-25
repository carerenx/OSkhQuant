#coding:gbk
"""
Alpha#144 逆向选择持有策略 V7 — QMT原生版
====================================================
基于Alpha144因子的中盘股长期持有策略。

核心理念:
  1. Alpha#144 = sum(|ret|/amount, ret<0, 20)
     因子值越小→下跌日流动性冲击越小→筹码最稳定→适合长期持有
  2. 逆向选择 — 每30天选Alpha144最低的12只，等权持有
  3. MFE疲劳退出（30d/MFE<5%）— V5验证的核心机制
  4. 分段止损（<50d:-25% / >=50d:-15%）
  5. 分段移动止盈（MFE>30%→5%紧线）
  6. 组合回撤安全网（60日峰值回落>20%→清仓）
  7. 大盘两级过滤（MA60软防御/硬防御）
  8. 剔除创业板(300)/科创板(688)

V7改进:
  - 保留V5核心（+106%的MFE单档退出）
  - 删除V6失败的月度动量防御（导致2024-09踏空+30%大涨）
  - 组合回撤25%→20%（更紧，但仅在极端行情触发）
  - 精简代码，去掉未触发的死代码

回测参数:
  - 初始资金: 50万
  - 基准: 000905.SH (中证500)
  - 回测区间: 2024-01-01 ~ 至今
  - 手续费: 佣金万2.5, 印花税千1(卖), 最低5元
"""
import numpy as np

# ╔══════════════════════════════════════════════════════════════╗
# ║              用户可调参数                                    ║
# ╚══════════════════════════════════════════════════════════════╝

BENCHMARK = '000905.SH'

# ── 因子参数 ──
FACTOR_WINDOW    = 20
BOTTOM_N         = 12
REFRESH_INTERVAL = 30

# ── 新仓保护 ──
PROTECTION_BARS  = 20

# ── MFE疲劳退出（V5核心） ──
MFE_FATIGUE_BARS      = 30
MFE_FATIGUE_THRESHOLD = 0.05

# ── 分段止损 ──
HARD_STOP_EARLY  = -0.25
HARD_STOP_LATE   = -0.15
STOP_TIGHTEN_BAR = 50

# ── 分段移动止盈 ──
TRAILING_PROFIT_PCT     = 0.20
TRAILING_DRAWDOWN_PCT   = 0.10
TRAILING_DRAWDOWN_TIGHT = 0.05

# ── 组合回撤安全网（V7: 20%） ──
PORTFOLIO_DD_WINDOW    = 60
PORTFOLIO_DD_THRESHOLD = 0.20

# ── 大盘过滤 ──
MA_MARKET              = 60
MARKET_FILTER_PCT      = 0.05
MARKET_FILTER_HARD_PCT = 0.10

# ── 选股过滤 ──
MAX_ANNUAL_VOL = 0.60
MIN_DAILY_AMOUNT = 3e7
SKIP_CHUANG_YE_BAN = True
SKIP_KE_CHUANG_BAN  = True

# ── 涨跌停 ──
LIMIT_DOWN_PCT = -0.098

# ── 数据要求 ──
MIN_HISTORY_BARS = 130


# ╔══════════════════════════════════════════════════════════════╗
# ║              全局状态                                        ║
# ╚══════════════════════════════════════════════════════════════╝

class State:
    stock_pool = []
    rankings = {}
    bottom_n = set()
    next_refresh_bar = 0
    bar_counter = 0
    last_barpos = -1
    positions = {}
    pending_sells = []
    acc_id = 'testS'
    capital = 500000
    cash = 0
    total_assets = 0
    need_bars = 0
    portfolio_peak = 0
    portfolio_dd_bars = []


# ╔══════════════════════════════════════════════════════════════╗
# ║              策略入口                                        ║
# ╚══════════════════════════════════════════════════════════════╝

def init(ContextInfo):
    print("[Alpha144-V7] V5核心+组合安全网 QMT版")
    print("[init] 获取中证500成分股...")

    stocks = None
    for method_name, method_fn in [
        ('get_stock_list_in_sector("中证500")',
         lambda: ContextInfo.get_stock_list_in_sector('中证500')),
        ('get_sector("000905.SH")',
         lambda: ContextInfo.get_sector('000905.SH')),
    ]:
        try:
            raw = method_fn()
            if raw and len(raw) > 0:
                stocks = raw
                print("[init] %s 获取到 %d 只成分股" % (method_name, len(raw)))
                break
        except Exception:
            continue

    if not stocks:
        print("[init] API获取失败, 使用硬编码CSI500池")
        stocks = _get_fallback_csi500()

    valid = []
    for c in stocks:
        try:
            n = ContextInfo.get_stock_name(c)
            if n and len(n) > 0 and 'ST' not in n and '*' not in n:
                valid.append(c)
        except Exception:
            valid.append(c)

    State.stock_pool = valid
    print("[init] 有效股票池: %d 只" % len(State.stock_pool))

    universe = valid[:] + [BENCHMARK]
    ContextInfo.set_universe(list(set(universe)))

    for attr, val in [
        ('capital', State.capital),
        ('benchmark', BENCHMARK),
        ('start', '2024-01-01 09:30:00'),
        ('end', '2026-06-24 15:00:00'),
    ]:
        try: setattr(ContextInfo, attr, val)
        except: pass

    ContextInfo.set_slippage(1, 0.001)
    ContextInfo.set_commission(0, [0.00025, 0.00025, 0.001, 0.0, 0.0, 5.0])
    ContextInfo.set_account(State.acc_id)

    State.need_bars = max(FACTOR_WINDOW + 30, MA_MARKET + 10, PROTECTION_BARS + 10,
                          STOP_TIGHTEN_BAR + 10, PORTFOLIO_DD_WINDOW + 10)
    State.portfolio_peak = State.capital

    print("[init] V7 初始化完成 | 持仓=%d只 | MFE:%dd<%.0f%% | 组合DD>%.0f%%清仓 | 分段止损+分段止盈" % (
        BOTTOM_N, MFE_FATIGUE_BARS, MFE_FATIGUE_THRESHOLD*100, PORTFOLIO_DD_THRESHOLD*100))
    print("[init] 回测区间: 2024-01 ~ 至今 | 初始资金: %.0f万" % (State.capital / 10000))


def handlebar(ContextInfo):
    bar = ContextInfo.barpos
    if bar < MIN_HISTORY_BARS: return
    if bar == State.last_barpos: return
    State.last_barpos = bar
    State.bar_counter += 1

    need_bars = State.need_bars
    hist_close  = ContextInfo.get_history_data(need_bars, '1d', 'close')
    hist_amount = ContextInfo.get_history_data(need_bars, '1d', 'amount')

    _update_account(ContextInfo)
    State.total_assets = State.cash + _calc_total_position_value(ContextInfo, hist_close)
    _sync_positions(ContextInfo)

    # ── V7: 组合回撤追踪 ──
    _update_portfolio_dd()

    # ── 大盘过滤 ──
    market_state = _check_market_v2(hist_close)

    # ── 日期日志 ──
    date_str = _log_time(ContextInfo)
    print("=" * 50)
    print("[%s] bar=%d cnt=%d 持仓=%d只 资产=%.0f万 市场=%s" % (
        date_str, bar, State.bar_counter, len(State.positions),
        State.total_assets / 10000, market_state))

    # ═══════════════════════════════════════════════════════════
    # 1. 跌停 pending
    # ═══════════════════════════════════════════════════════════
    _process_pending_sells(ContextInfo, hist_close)

    # ═══════════════════════════════════════════════════════════
    # 2. V7 组合回撤安全网（20%）— 最高优先级
    # ═══════════════════════════════════════════════════════════
    portfolio_dd = (State.total_assets / State.portfolio_peak - 1.0) if State.portfolio_peak > 0 else 0
    if portfolio_dd <= -PORTFOLIO_DD_THRESHOLD and len(State.positions) > 0:
        _liquidate_all(ContextInfo, hist_close, "组合回撤%.0f%%>%.0f%%" % (
            abs(portfolio_dd)*100, PORTFOLIO_DD_THRESHOLD*100))
        print("  [组合风控] 总资产从峰值%d回落%.1f%% > %.0f%% -> 全部清仓" % (
            int(State.portfolio_peak), abs(portfolio_dd)*100, PORTFOLIO_DD_THRESHOLD*100))

    # ═══════════════════════════════════════════════════════════
    # 3. MFE疲劳退出 + 分段止损 + 移动止盈
    # ═══════════════════════════════════════════════════════════
    _check_mfe_fatigue(ContextInfo, hist_close)
    _check_stop_loss(ContextInfo, hist_close)
    _check_trailing_stop(ContextInfo, hist_close)

    # ═══════════════════════════════════════════════════════════
    # 4. 因子排名刷新
    # ═══════════════════════════════════════════════════════════
    if State.bar_counter >= State.next_refresh_bar:
        State.rankings = _compute_factor_rankings(hist_close, hist_amount)
        sorted_codes = sorted(State.rankings.keys(), key=lambda c: State.rankings[c])
        State.bottom_n = set(sorted_codes[:BOTTOM_N])
        State.next_refresh_bar = State.bar_counter + REFRESH_INTERVAL
        print("[刷新] 候选=%d只 | 选中%d只 | 下次=bar %d" % (
            len(State.rankings), BOTTOM_N, State.next_refresh_bar))

    # ═══════════════════════════════════════════════════════════
    # 5. 硬防御清仓
    # ═══════════════════════════════════════════════════════════
    if market_state == 'hard_defense' and len(State.positions) > 0:
        _liquidate_all(ContextInfo, hist_close, "大盘硬防御清仓")

    # ═══════════════════════════════════════════════════════════
    # 6. 调仓 + 买入
    # ═══════════════════════════════════════════════════════════
    if market_state == 'ok' and State.bottom_n:
        _sell_removed_stocks(ContextInfo, hist_close)
        _buy_new_stocks(ContextInfo)
    elif market_state == 'soft_defense':
        print("[Alpha144-V7] 软防御：暂停买入/调仓")

    # ═══════════════════════════════════════════════════════════
    # 7. 更新持仓天数+最高价
    # ═══════════════════════════════════════════════════════════
    for code in list(State.positions.keys()):
        pos = State.positions[code]
        pos['bars_held'] = pos.get('bars_held', 0) + 1
        px = _get_price(ContextInfo, code, hist_close)
        if px > pos.get('highest_price', 0):
            pos['highest_price'] = px

    # ── 摘要 ──
    pos_codes = list(State.positions.keys())
    print("[摘要] 持仓=%d只 %s | 下次刷新=%d" % (
        len(State.positions),
        pos_codes[:5] if pos_codes else "空仓",
        State.next_refresh_bar - State.bar_counter))


# ╔══════════════════════════════════════════════════════════════╗
# ║              Alpha144 因子计算                                ║
# ╚══════════════════════════════════════════════════════════════╝

def _calc_alpha144(close_arr, amount_arr):
    arr_c = np.array(close_arr, dtype=float)
    arr_a = np.array(amount_arr, dtype=float)
    n = min(len(arr_c), len(arr_a))
    needed = FACTOR_WINDOW + 1
    if n < needed: return None
    recent_c = arr_c[-needed:]; recent_a = arr_a[-needed:]
    alpha = 0.0; neg_count = 0
    for i in range(1, len(recent_c)):
        if recent_c[i-1] <= 0: continue
        ret_i = (recent_c[i] - recent_c[i-1]) / recent_c[i-1]
        if ret_i < 0:
            amount_i = recent_a[i] if i < len(recent_a) else 0
            if amount_i > 0: alpha += abs(ret_i) / amount_i; neg_count += 1
    return 0.0 if neg_count == 0 else alpha


def _calc_annual_vol(close_vals):
    if len(close_vals) < 21: return 999
    arr = np.array(close_vals[-21:], dtype=float)
    return np.std(np.diff(arr) / arr[:-1]) * np.sqrt(252)


def _compute_factor_rankings(hist_close, hist_amount):
    raw_scores = {}
    for code in State.stock_pool:
        if SKIP_CHUANG_YE_BAN and code.startswith('300'): continue
        if SKIP_KE_CHUANG_BAN and code.startswith('688'): continue
        close_arr = hist_close.get(code, [])
        amount_arr = hist_amount.get(code, [])
        if len(close_arr) < FACTOR_WINDOW + 1: continue
        if len(amount_arr) < FACTOR_WINDOW + 1: continue
        try:
            if np.mean(np.array(amount_arr[-FACTOR_WINDOW:], dtype=float)) < MIN_DAILY_AMOUNT: continue
        except Exception: continue
        if _calc_annual_vol(close_arr) > MAX_ANNUAL_VOL: continue
        val = _calc_alpha144(close_arr, amount_arr)
        if val is not None: raw_scores[code] = val
    return raw_scores


# ╔══════════════════════════════════════════════════════════════╗
# ║              MFE疲劳退出（V5核心）                              ║
# ╚══════════════════════════════════════════════════════════════╝

def _check_mfe_fatigue(ContextInfo, hist_close):
    to_sell = []
    for code, pos in State.positions.items():
        bars = pos.get('bars_held', 0)
        if bars < MFE_FATIGUE_BARS: continue
        entry = pos.get('entry_price', 0)
        if entry <= 0: continue
        highest = pos.get('highest_price', entry)
        mfe = (highest / entry - 1.0)
        if mfe < MFE_FATIGUE_THRESHOLD:
            px = _get_price(ContextInfo, code, hist_close)
            if px <= 0: continue
            pnl_pct = (px / entry - 1.0) * 100
            print("  [MFE疲劳] %s 持有%d天 MFE仅%.1f%% < %.0f%% -> 清仓 | entry=%.2f now=%.2f pnl=%.1f%%" % (
                code, bars, mfe * 100, MFE_FATIGUE_THRESHOLD * 100, entry, px, pnl_pct))
            to_sell.append((code, "MFE疲劳(持有%dd+MFE%.1f%%<%.0f%%)" % (
                bars, mfe * 100, MFE_FATIGUE_THRESHOLD * 100)))
    for code, reason in to_sell:
        _sell_position(ContextInfo, code, hist_close, reason)


# ╔══════════════════════════════════════════════════════════════╗
# ║              组合回撤追踪（V7）                                 ║
# ╚══════════════════════════════════════════════════════════════╝

def _update_portfolio_dd():
    bar = State.bar_counter
    ta = State.total_assets
    State.portfolio_dd_bars.append((bar, ta))
    while State.portfolio_dd_bars and (bar - State.portfolio_dd_bars[0][0]) > PORTFOLIO_DD_WINDOW:
        State.portfolio_dd_bars.pop(0)
    if ta > State.portfolio_peak:
        State.portfolio_peak = ta
    if State.portfolio_dd_bars:
        window_peak = max(v for _, v in State.portfolio_dd_bars)
        if window_peak > State.portfolio_peak:
            State.portfolio_peak = window_peak


# ╔══════════════════════════════════════════════════════════════╗
# ║              分段止损                                          ║
# ╚══════════════════════════════════════════════════════════════╝

def _check_stop_loss(ContextInfo, hist_close):
    to_sell = []
    for code, pos in State.positions.items():
        px = _get_price(ContextInfo, code, hist_close)
        if px <= 0: continue
        entry = pos.get('entry_price', 0)
        if entry <= 0: continue
        pnl_pct = (px / entry - 1.0)
        bars = pos.get('bars_held', 0)
        stop_line = HARD_STOP_LATE if bars >= STOP_TIGHTEN_BAR else HARD_STOP_EARLY
        if pnl_pct <= stop_line:
            print("  [止损触发] %s 浮亏 %.1f%% <= %.0f%% | entry=%.2f now=%.2f hold=%dd (%s)" % (
                code, pnl_pct * 100, stop_line * 100, entry, px, bars,
                "紧止损" if bars >= STOP_TIGHTEN_BAR else "宽止损"))
            to_sell.append((code, "止损%.0f%%(浮亏%.1f%%)" % (stop_line * 100, pnl_pct * 100)))
    for code, reason in to_sell:
        _sell_position(ContextInfo, code, hist_close, reason)


# ╔══════════════════════════════════════════════════════════════╗
# ║              分段移动止盈                                      ║
# ╚══════════════════════════════════════════════════════════════╝

def _check_trailing_stop(ContextInfo, hist_close):
    to_sell = []
    for code, pos in State.positions.items():
        px = _get_price(ContextInfo, code, hist_close)
        if px <= 0: continue
        entry = pos.get('entry_price', 0)
        if entry <= 0: continue
        highest = pos.get('highest_price', entry)
        if highest <= 0: continue
        mfe = (highest / entry - 1.0)
        if mfe < TRAILING_PROFIT_PCT: continue
        drawdown = (px / highest - 1.0)
        if mfe > 0.30:
            trigger = -TRAILING_DRAWDOWN_TIGHT; line_name = "5%%紧线"
        else:
            trigger = -TRAILING_DRAWDOWN_PCT; line_name = "10%%标准线"
        if drawdown <= trigger:
            print("  [移动止盈] %s 从高点 %.2f 回落 %.1f%% (MFE=%.1f%% %s) | entry=%.2f now=%.2f hold=%dd" % (
                code, highest, drawdown * 100, mfe * 100, line_name,
                entry, px, pos.get('bars_held', 0)))
            to_sell.append((code, "移动止盈(高点%.2f回落%.1f%%)" % (highest, drawdown * 100)))
    for code, reason in to_sell:
        _sell_position(ContextInfo, code, hist_close, reason)


# ╔══════════════════════════════════════════════════════════════╗
# ║              调仓 + 买入                                       ║
# ╚══════════════════════════════════════════════════════════════╝

def _sell_removed_stocks(ContextInfo, hist_close):
    if not State.bottom_n: return
    for code in list(State.positions.keys()):
        if code in State.bottom_n: continue
        pos = State.positions[code]
        if pos.get('bars_held', 0) < PROTECTION_BARS: continue
        print("  [调仓卖出] %s 不在 bottom_%d | hold=%dd" % (code, BOTTOM_N, pos.get('bars_held', 0)))
        _sell_position(ContextInfo, code, hist_close, "调仓(不在bottom%d)" % BOTTOM_N)


def _buy_new_stocks(ContextInfo):
    if not State.bottom_n: return
    held = set(State.positions.keys())
    to_buy = [c for c in State.bottom_n if c not in held]
    if not to_buy: return

    total_equity = State.total_assets if State.total_assets > 0 else State.capital
    allocation_per_stock = total_equity / BOTTOM_N
    slots_remaining = BOTTOM_N - len(State.positions)
    if slots_remaining <= 0: return

    bought = 0
    for code in to_buy:
        if bought >= slots_remaining: break
        px = _get_price(ContextInfo, code, {})
        if px <= 0:
            try:
                tick = ContextInfo.get_full_tick([code])
                if code in tick: px = tick[code].get('lastPrice', 0)
            except: pass
        if px <= 0: continue

        shares = int(allocation_per_stock * 0.998 / px / 100) * 100
        if shares < 100: continue

        need_cash = shares * px * 1.002
        if need_cash > State.cash:
            shares = int(State.cash * 0.98 / px / 100) * 100
            if shares < 100: continue

        factor_val = State.rankings.get(code, 0)
        try:
            passorder(23, 1101, State.acc_id, code, 5, -1, shares,
                      'Alpha144V7买入', 1, '', ContextInfo)
        except Exception as e:
            print("  [买入失败] %s 下单异常: %s" % (code, str(e)))
            continue

        State.positions[code] = {
            'shares': shares, 'entry_price': px,
            'entry_bar': State.last_barpos, 'bars_held': 0,
            'highest_price': px,
        }
        print(">>> [买入] %s x %d股 @ %.2f | 金额 %.0f | alpha144=%.2e" % (
            code, shares, px, shares * px, factor_val))
        bought += 1


# ╔══════════════════════════════════════════════════════════════╗
# ║              卖出执行 + 大盘防御 + 辅助函数                      ║
# ╚══════════════════════════════════════════════════════════════╝

def _process_pending_sells(ContextInfo, hist_close):
    if not State.pending_sells: return
    retry_list = list(State.pending_sells); State.pending_sells = []
    for code in retry_list:
        if code not in State.positions: continue
        _sell_position(ContextInfo, code, hist_close, "补卖(昨日跌停)")


def _sell_position(ContextInfo, code, hist_close, reason):
    if code not in State.positions: return
    pos = State.positions[code]
    shares = pos.get('shares', 0)
    if shares <= 0: del State.positions[code]; return

    px = _get_price(ContextInfo, code, hist_close)

    close_arr = hist_close.get(code, [])
    if len(close_arr) >= 2:
        arr = np.array(close_arr, dtype=float)
        if arr[-2] > 0:
            daily_ret = (arr[-1] - arr[-2]) / arr[-2]
            if daily_ret <= LIMIT_DOWN_PCT:
                print("  [卖出延迟] %s 跌停 (%.1f%%), 延至次日" % (code, daily_ret * 100))
                if code not in State.pending_sells:
                    State.pending_sells.append(code)
                return

    try:
        passorder(24, 1101, State.acc_id, code, 5, -1, shares,
                  'Alpha144V7卖出', 1, '', ContextInfo)
    except Exception as e:
        print("  [卖出失败] %s 下单异常: %s" % (code, str(e)))
        return

    entry_price = pos.get('entry_price', 0)
    pnl_pct = (px / entry_price - 1) * 100 if entry_price > 0 else 0
    bars = pos.get('bars_held', 0)
    print("<<< [卖出] %s x %d股 @ %.2f | 盈亏 %+.1f%% | 持有%d天 | %s" % (
        code, shares, px, pnl_pct, bars, reason))
    del State.positions[code]
    if code in State.pending_sells:
        State.pending_sells.remove(code)


def _check_market_v2(hist_close):
    if BENCHMARK not in hist_close: return 'ok'
    arr = hist_close[BENCHMARK]
    if len(arr) < MA_MARKET + 1: return 'ok'
    try:
        close_arr = np.array(arr, dtype=float)
        current = close_arr[-1]
        if np.isnan(current): return 'ok'
        ma = np.mean(close_arr[-MA_MARKET:])
        if np.isnan(ma) or ma <= 0: return 'ok'
        soft = ma * (1.0 - MARKET_FILTER_PCT)
        hard = ma * (1.0 - MARKET_FILTER_HARD_PCT)
        if current < hard:
            print("[市场] %s=%.2f < MA%d x %.0f%%=%.2f -> 硬防御清仓" % (
                BENCHMARK, current, MA_MARKET, (1-MARKET_FILTER_HARD_PCT)*100, hard))
            return 'hard_defense'
        if current < soft:
            print("[市场] %s=%.2f < MA%d x %.0f%%=%.2f -> 软防御" % (
                BENCHMARK, current, MA_MARKET, (1-MARKET_FILTER_PCT)*100, soft))
            return 'soft_defense'
        return 'ok'
    except Exception: return 'ok'


def _liquidate_all(ContextInfo, hist_close, reason):
    for code in list(State.positions.keys()):
        _sell_position(ContextInfo, code, hist_close, reason)


def _get_price(ContextInfo, code, hist_close):
    try:
        t = ContextInfo.get_full_tick([code])
        if code in t:
            lp = t[code].get('lastPrice', 0)
            if lp > 0: return lp
    except Exception: pass
    if code in hist_close and len(hist_close[code]) > 0:
        return float(hist_close[code][-1])
    return 0


def _calc_total_position_value(ContextInfo, hist_close):
    total = 0.0
    for code, pos in State.positions.items():
        shares = pos.get('shares', 0)
        px = _get_price(ContextInfo, code, hist_close)
        total += shares * px
    return total


def _update_account(ContextInfo):
    try:
        a = get_trade_detail_data(State.acc_id, 'stock', 'account')
        if a:
            State.cash = a[0].m_dAvailable
            State.total_assets = a[0].m_dBalance
            return
    except Exception: pass
    try:
        State.cash = ContextInfo.cash
        State.total_assets = ContextInfo.capital
    except Exception: pass


def _sync_positions(ContextInfo):
    try:
        ps = get_trade_detail_data(State.acc_id, 'stock', 'position')
        remote = {}
        for p in ps:
            code = p.m_strInstrumentID + '.' + p.m_strExchangeID
            vol = p.m_nVolume
            if vol <= 0: continue
            if code in State.positions:
                old = State.positions[code]
                old['shares'] = vol
                remote[code] = old
            else:
                remote[code] = {
                    'shares': vol, 'entry_price': p.m_dOpenPrice,
                    'entry_bar': State.last_barpos, 'bars_held': 0,
                    'highest_price': p.m_dOpenPrice,
                }
        for code in list(State.positions.keys()):
            if code not in remote: del State.positions[code]
        for code, pos in remote.items():
            if code not in State.positions: State.positions[code] = pos
    except Exception: pass


def _log_time(ContextInfo):
    try:
        t = ContextInfo.get_bar_timetag(ContextInfo.barpos)
        return timetag_to_datetime(t, '%Y-%m-%d')
    except Exception: return str(ContextInfo.barpos)


# ╔══════════════════════════════════════════════════════════════╗
# ║              Fallback: 中证500成分股 (硬编码)                 ║
# ╚══════════════════════════════════════════════════════════════╝

def _get_fallback_csi500():
    return [
        '000021.SZ','000034.SZ','000060.SZ','000066.SZ','000400.SZ','000401.SZ',
        '000423.SZ','000425.SZ','000426.SZ','000528.SZ','000537.SZ','000538.SZ',
        '000547.SZ','000553.SZ','000559.SZ','000581.SZ','000623.SZ','000625.SZ',
        '000629.SZ','000630.SZ','000636.SZ','000656.SZ','000661.SZ','000683.SZ',
        '000703.SZ','000708.SZ','000709.SZ','000723.SZ','000728.SZ','000729.SZ',
        '000733.SZ','000737.SZ','000738.SZ','000739.SZ','000750.SZ','000776.SZ',
        '000778.SZ','000783.SZ','000786.SZ','000800.SZ','000807.SZ','000825.SZ',
        '000826.SZ','000830.SZ','000831.SZ','000860.SZ','000869.SZ','000878.SZ',
        '000883.SZ','000887.SZ','000895.SZ','000898.SZ','000902.SZ','000903.SZ',
        '000912.SZ','000915.SZ','000921.SZ','000927.SZ','000930.SZ','000932.SZ',
        '000937.SZ','000938.SZ','000950.SZ','000951.SZ','000957.SZ','000959.SZ',
        '000961.SZ','000962.SZ','000963.SZ','000966.SZ','000967.SZ','000968.SZ',
        '000969.SZ','000970.SZ','000975.SZ','000988.SZ','001203.SZ','001286.SZ',
        '002001.SZ','002007.SZ','002008.SZ','002013.SZ','002019.SZ','002020.SZ',
        '002022.SZ','002025.SZ','002049.SZ','002050.SZ','002064.SZ','002065.SZ',
        '002074.SZ','002080.SZ','002085.SZ','002092.SZ','002097.SZ','002108.SZ',
        '002120.SZ','002121.SZ','002129.SZ','002138.SZ','002155.SZ','002156.SZ',
        '002185.SZ','002195.SZ','002202.SZ','002203.SZ','002230.SZ','002240.SZ',
        '002245.SZ','002250.SZ','002258.SZ','002261.SZ','002273.SZ','002281.SZ',
        '002304.SZ','002326.SZ','002340.SZ','002352.SZ','002368.SZ','002371.SZ',
        '002373.SZ','002384.SZ','002405.SZ','002407.SZ','002408.SZ','002409.SZ',
        '002410.SZ','002436.SZ','002440.SZ','002456.SZ','002459.SZ','002460.SZ',
        '002463.SZ','002465.SZ','002468.SZ','002475.SZ','002532.SZ','002555.SZ',
        '002558.SZ','002602.SZ','002624.SZ','002625.SZ','002673.SZ','002714.SZ',
        '002738.SZ','002739.SZ','002797.SZ','002966.SZ','003031.SZ','003035.SZ',
        '300001.SZ','300003.SZ','300009.SZ','300012.SZ','300014.SZ','300015.SZ',
        '300024.SZ','300026.SZ','300033.SZ','300036.SZ','300037.SZ','300039.SZ',
        '300058.SZ','300059.SZ','300068.SZ','300073.SZ','300118.SZ','300124.SZ',
        '300133.SZ','300136.SZ','300142.SZ','300168.SZ','300251.SZ','300253.SZ',
        '300274.SZ','300316.SZ','300339.SZ','300346.SZ','300413.SZ','300418.SZ',
        '300450.SZ','300498.SZ','300558.SZ','300567.SZ','300595.SZ','300601.SZ',
        '300633.SZ','300666.SZ','300676.SZ','300677.SZ','300725.SZ','300765.SZ',
        '300803.SZ','300857.SZ','300888.SZ','301236.SZ','301267.SZ','301301.SZ',
        '301308.SZ','301358.SZ','301498.SZ','600004.SH','600007.SH','600008.SH',
        '600021.SH','600026.SH','600029.SH','600032.SH','600079.SH','600085.SH',
        '600096.SH','600109.SH','600126.SH','600132.SH','600143.SH','600157.SH',
        '600161.SH','600166.SH','600196.SH','600208.SH','600256.SH','600276.SH',
        '600282.SH','600316.SH','600348.SH','600350.SH','600361.SH','600363.SH',
        '600369.SH','600377.SH','600378.SH','600380.SH','600390.SH','600418.SH',
        '600486.SH','600487.SH','600497.SH','600498.SH','600499.SH','600511.SH',
        '600521.SH','600536.SH','600546.SH','600563.SH','600566.SH','600578.SH',
        '600580.SH','600642.SH','600655.SH','600673.SH','600688.SH','600699.SH',
        '600711.SH','600720.SH','600724.SH','600739.SH','600754.SH','600763.SH',
        '600764.SH','600801.SH','600808.SH','600816.SH','600820.SH','600839.SH',
        '600863.SH','600871.SH','600873.SH','600879.SH','600884.SH','600895.SH',
        '600906.SH','600909.SH','600985.SH','600988.SH','600995.SH','601016.SH',
        '601019.SH','601098.SH','601099.SH','601106.SH','601108.SH','601118.SH',
        '601128.SH','601139.SH','601156.SH','601162.SH','601168.SH','601179.SH',
        '601198.SH','601212.SH','601233.SH','601236.SH','601456.SH','601555.SH',
        '601577.SH','601665.SH','601666.SH','601696.SH','601717.SH','601928.SH',
        '601990.SH','601991.SH','601997.SH','603000.SH','603049.SH','603077.SH',
        '603087.SH','603160.SH','603175.SH','603444.SH','603501.SH','603529.SH',
        '603565.SH','603568.SH','603606.SH','603658.SH','603707.SH','603728.SH',
        '603786.SH','603816.SH','603826.SH','603833.SH','603858.SH','603868.SH',
        '603885.SH','603893.SH','603939.SH','603986.SH','688008.SH',
    ]
