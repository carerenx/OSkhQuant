# coding: utf-8
"""
策略说明：
- 策略名称：Alpha144 逆向选择持有策略（V7 — V5核心+组合安全网）
- 核心理念：
    1. Alpha#144 因子逆向选股（因子值最小→筹码最稳定）
    2. 纯MFE驱动退出 — V5验证的最优方案（胜率48%, +106%）
    3. 组合回撤安全网 — 60日峰值回落>20%→清仓（V6教训：不干预正常交易）

V7 改进（基于V5/V6对比分析）：
  V5成功要素（保留）:
    ✅ MFE疲劳退出（30天+MFE<5%）— V5核心，触发17次精准清退
    ✅ 分段止损 <50d:-25% / ≥50d:-15%
    ✅ 分段移动止盈 MFE>30%→5%紧线
    ✅ 大盘两级防御 MA60
    ✅ 波动率过滤 + 板块过滤

  V6失败教训（删除）:
    ❌ 月度动量防御（触发87次，导致2024-09踏空+30%大涨、收益腰斩）
    ❌ 多层MFE疲劳（3档未生效，不如V5单档30d<5%精准）
    ❌ 组合回撤25%（一次未触发，阈值过高）

  V7新增（精准改进）:
    🆕 组合回撤安全网：60日峰值回落>20%→清仓（V6阈值25%过宽）
    🆕 保留V5的一档MFE疲劳30d<5%

回测参数：
- 初始资金: 50万
- 基准: 000905.SH (中证500)
- 回测区间: 2024-01-01 ~ 至今
"""
from khQuantImport import *
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

# ── MFE疲劳退出（V5核心，保持不变） ──
MFE_FATIGUE_BARS     = 30
MFE_FATIGUE_THRESHOLD = 0.05  # MFE < 5% 则清仓

# ── 分段止损 ──
HARD_STOP_EARLY  = -0.25
HARD_STOP_LATE   = -0.15
STOP_TIGHTEN_BAR = 50

# ── 分段移动止盈 ──
TRAILING_PROFIT_PCT     = 0.20
TRAILING_DRAWDOWN_PCT   = 0.10
TRAILING_DRAWDOWN_TIGHT = 0.05

# ── 组合层面回撤安全网（V7：20%，比V6的25%更紧但仅在极端情况触发） ──
PORTFOLIO_DD_WINDOW    = 60
PORTFOLIO_DD_THRESHOLD = 0.20  # V7: 20%（V6的25%从未触发）

# ── 大盘过滤 ──
MA_MARKET              = 60
MARKET_FILTER_PCT      = 0.05
MARKET_FILTER_HARD_PCT = 0.10

# ── 波动率过滤 ──
MAX_ANNUAL_VOL = 0.60

# ── 板块限制 ──
SKIP_CHUANG_YE_BAN = True
SKIP_KE_CHUANG_BAN  = True
MAX_CHUANG_YE = 3

# ── 数据要求 ──
MIN_DAILY_AMOUNT = 3e7


# ╔══════════════════════════════════════════════════════════════╗
# ║              模块级全局状态                                  ║
# ╚══════════════════════════════════════════════════════════════╝

_STATE = {
    'rankings': {},
    'bottom_n': set(),
    'next_refresh_bar': 0,
    'bar_counter': 0,
    'pending_sells': [],
    'my_positions': {},
    'need_bars': 0,
    # V7: 组合风控
    'portfolio_peak': 0,
    'portfolio_dd_bars': [],
}


def init(stocks=None, data=None):
    g = _STATE
    g['rankings'] = {}
    g['bottom_n'] = set()
    g['next_refresh_bar'] = 0
    g['bar_counter'] = 0
    g['pending_sells'] = []
    g['my_positions'] = {}
    g['need_bars'] = max(FACTOR_WINDOW + 30, MA_MARKET + 10, PROTECTION_BARS + 10,
                         STOP_TIGHTEN_BAR + 10, PORTFOLIO_DD_WINDOW + 10)
    g['portfolio_peak'] = 0
    g['portfolio_dd_bars'] = []
    logging.info("[Alpha144-V7] V5核心+组合安全网 | 持仓=%d只 | MFE疲劳=%dd/<%.0f%% | 组合DD>%.0f%%清仓 | 分段止损+分段止盈" % (
        BOTTOM_N, MFE_FATIGUE_BARS, MFE_FATIGUE_THRESHOLD * 100,
        PORTFOLIO_DD_THRESHOLD * 100))


# ╔══════════════════════════════════════════════════════════════╗
# ║              主策略入口                                      ║
# ╚══════════════════════════════════════════════════════════════╝

def khHandlebar(data: Dict) -> List[Dict]:
    g = _STATE
    signals = []
    stocks = khGet(data, "stocks")
    dn = khGet(data, "date_num")

    g['bar_counter'] += 1
    bar = g['bar_counter']

    _sync_positions_from_framework(data)
    total_asset = khGet(data, "total_asset")

    date_str = khGet(data, "date")
    logging.debug("[%s] bar=%d 持仓=%d只 资产=%.0f万" % (
        date_str, bar, len(g['my_positions']),
        total_asset / 10000))

    # ── V7: 组合回撤追踪 ──
    _update_portfolio_dd(data, g)

    # ── 获取历史数据 ──
    all_symbols = list(stocks)
    try:
        hist = khHistory(all_symbols, ["close", "amount"],
                         g['need_bars'], "1d", dn, fq="pre")
    except Exception as e:
        logging.error("[Alpha144-V7] 数据获取失败: %s" % str(e))
        return signals

    # ── 基准数据 ──
    bm_hist = {}
    try:
        bm_hist = khHistory([BENCHMARK], ["close"], MA_MARKET + 10, "1d", dn, fq="pre")
    except Exception:
        pass

    # ═══════════════════════════════════════════════════════════
    # 1. 大盘两级过滤
    # ═══════════════════════════════════════════════════════════
    market_state = _check_market_v2(bm_hist)

    # ═══════════════════════════════════════════════════════════
    # 2. 跌停 pending 单
    # ═══════════════════════════════════════════════════════════
    _process_pending_sells(data, hist, signals)

    # ═══════════════════════════════════════════════════════════
    # 3. V7 组合回撤安全网（20%——优先于所有退出）
    # ═══════════════════════════════════════════════════════════
    portfolio_dd = (total_asset / g['portfolio_peak'] - 1.0) if g['portfolio_peak'] > 0 else 0
    if portfolio_dd <= -PORTFOLIO_DD_THRESHOLD and len(g['my_positions']) > 0:
        _liquidate_all(data, signals, "组合回撤%.1f%%>%.0f%%清仓" % (
            abs(portfolio_dd)*100, PORTFOLIO_DD_THRESHOLD*100))
        logging.warning("  [组合风控] 总资产从峰值%d回落%.1f%% > %.0f%% → 全部清仓" % (
            int(g['portfolio_peak']), abs(portfolio_dd)*100, PORTFOLIO_DD_THRESHOLD*100))

    # ═══════════════════════════════════════════════════════════
    # 4. MFE疲劳退出（V5核心） + 分段止损 + 分段止盈
    # ═══════════════════════════════════════════════════════════
    _check_mfe_fatigue(data, hist, signals)
    _check_stop_loss(data, hist, signals)
    _check_trailing_stop(data, hist, signals)

    # ═══════════════════════════════════════════════════════════
    # 5. 因子排名刷新
    # ═══════════════════════════════════════════════════════════
    if bar >= g['next_refresh_bar']:
        g['rankings'] = _compute_factor_rankings_asc(stocks, hist, dn)
        sorted_codes = sorted(g['rankings'].keys(), key=lambda c: g['rankings'][c])
        g['bottom_n'] = set(sorted_codes[:BOTTOM_N])
        g['next_refresh_bar'] = bar + REFRESH_INTERVAL
        logging.info("[Alpha144-V7] 排名刷新 | 候选=%d只 | 选中%d只 | 下次=bar %d" % (
            len(g['rankings']), BOTTOM_N, g['next_refresh_bar']))

    # ═══════════════════════════════════════════════════════════
    # 6. 硬防御清仓
    # ═══════════════════════════════════════════════════════════
    if market_state == 'hard_defense' and len(g['my_positions']) > 0:
        _liquidate_all(data, signals, "大盘硬防御清仓")

    # ═══════════════════════════════════════════════════════════
    # 7. 调仓 + 买入
    # ═══════════════════════════════════════════════════════════
    if market_state == 'ok' and g['bottom_n']:
        _sell_removed_stocks(data, signals)
        _buy_new_stocks(data, signals)
    elif market_state == 'soft_defense':
        logging.debug("[Alpha144-V7] 软防御：暂停买入/调仓")

    # ═══════════════════════════════════════════════════════════
    # 8. 更新状态
    # ═══════════════════════════════════════════════════════════
    _apply_signals_to_state(signals, g)

    for code in list(g['my_positions'].keys()):
        pos = g['my_positions'][code]
        pos['bars_held'] += 1
        px = khPrice(data, code, "close")
        if px > pos.get('highest_price', 0):
            pos['highest_price'] = px

    return signals


# ╔══════════════════════════════════════════════════════════════╗
# ║              Alpha#144 因子计算                               ║
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


def _compute_factor_rankings_asc(stocks, hist, dn):
    raw_scores = {}
    for code in stocks:
        if SKIP_CHUANG_YE_BAN and code.startswith('300'): continue
        if SKIP_KE_CHUANG_BAN and code.startswith('688'): continue
        if code not in hist: continue
        df = hist[code]
        if df is None or len(df) < FACTOR_WINDOW + 1: continue
        try:
            close_vals = df['close'].values
            amount_vals = df['amount'].values if 'amount' in df.columns else None
        except Exception: continue
        if amount_vals is None or len(amount_vals) < FACTOR_WINDOW + 1: continue
        try:
            if np.mean(np.array(amount_vals[-FACTOR_WINDOW:], dtype=float)) < MIN_DAILY_AMOUNT: continue
        except Exception: continue
        if _calc_annual_vol(close_vals) > MAX_ANNUAL_VOL: continue
        val = _calc_alpha144(close_vals, amount_vals)
        if val is not None: raw_scores[code] = val
    return raw_scores


# ╔══════════════════════════════════════════════════════════════╗
# ║              MFE疲劳退出（V5核心，一档30d<5%）                   ║
# ╚══════════════════════════════════════════════════════════════╝

def _check_mfe_fatigue(data, hist, signals):
    g = _STATE
    to_sell = []
    for code, pos in g['my_positions'].items():
        bars = pos.get('bars_held', 0)
        if bars < MFE_FATIGUE_BARS: continue
        entry = pos.get('entry_price', 0)
        if entry <= 0: continue
        highest = pos.get('highest_price', entry)
        mfe = (highest / entry - 1.0)
        if mfe < MFE_FATIGUE_THRESHOLD:
            px = khPrice(data, code, "close")
            if px <= 0:
                px = _get_price_from_hist(code, hist)
                if px <= 0: continue
            pnl_pct = (px / entry - 1.0) * 100
            logging.info("  [MFE疲劳] %s 持有%d天 MFE仅%.1f%% < %.0f%% → 清仓 | entry=%.2f now=%.2f pnl=%.1f%%" % (
                code[:6], bars, mfe * 100, MFE_FATIGUE_THRESHOLD * 100, entry, px, pnl_pct))
            to_sell.append((code, "MFE疲劳(持有%dd+MFE%.1f%%<%.0f%%)" % (
                bars, mfe * 100, MFE_FATIGUE_THRESHOLD * 100)))
    for code, reason in to_sell:
        _sell_position(data, code, hist, reason, signals)


# ╔══════════════════════════════════════════════════════════════╗
# ║              组合回撤追踪（V7）                                 ║
# ╚══════════════════════════════════════════════════════════════╝

def _update_portfolio_dd(data, g):
    bar = g['bar_counter']
    ta = khGet(data, "total_asset")
    g['portfolio_dd_bars'].append((bar, ta))
    while g['portfolio_dd_bars'] and (bar - g['portfolio_dd_bars'][0][0]) > PORTFOLIO_DD_WINDOW:
        g['portfolio_dd_bars'].pop(0)
    if ta > g['portfolio_peak']:
        g['portfolio_peak'] = ta
    if g['portfolio_dd_bars']:
        window_peak = max(v for _, v in g['portfolio_dd_bars'])
        if window_peak > g['portfolio_peak']:
            g['portfolio_peak'] = window_peak


# ╔══════════════════════════════════════════════════════════════╗
# ║              分段止损                                          ║
# ╚══════════════════════════════════════════════════════════════╝

def _check_stop_loss(data, hist, signals):
    g = _STATE
    to_sell = []
    for code, pos in g['my_positions'].items():
        px = khPrice(data, code, "close")
        if px <= 0:
            px = _get_price_from_hist(code, hist)
            if px <= 0: continue
        entry = pos['entry_price']
        if entry <= 0: continue
        pnl_pct = (px / entry - 1.0)
        bars = pos.get('bars_held', 0)
        stop_line = HARD_STOP_LATE if bars >= STOP_TIGHTEN_BAR else HARD_STOP_EARLY
        if pnl_pct <= stop_line:
            logging.info("  [止损触发] %s 浮亏 %.1f%% <= %.0f%% | entry=%.2f now=%.2f hold=%dd (%s)" % (
                code, pnl_pct * 100, stop_line * 100, entry, px, bars,
                "紧止损" if bars >= STOP_TIGHTEN_BAR else "宽止损"))
            to_sell.append((code, "止损%.0f%%(浮亏%.1f%% hold=%dd)" % (
                stop_line * 100, pnl_pct * 100, bars)))
    for code, reason in to_sell:
        _sell_position(data, code, hist, reason, signals)


# ╔══════════════════════════════════════════════════════════════╗
# ║              分段移动止盈                                      ║
# ╚══════════════════════════════════════════════════════════════╝

def _check_trailing_stop(data, hist, signals):
    g = _STATE
    to_sell = []
    for code, pos in g['my_positions'].items():
        px = khPrice(data, code, "close")
        if px <= 0: continue
        entry = pos['entry_price']
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
            logging.info("  [移动止盈] %s 从高点 %.2f 回落 %.1f%% (MFE=%.1f%% %s) | entry=%.2f now=%.2f hold=%dd" % (
                code, highest, drawdown * 100, mfe * 100, line_name, entry, px, pos.get('bars_held', 0)))
            to_sell.append((code, "移动止盈(高点%.2f回落%.1f%% MFE%.1f%% %s)" % (
                highest, drawdown * 100, mfe * 100, line_name)))
    for code, reason in to_sell:
        _sell_position(data, code, hist, reason, signals)


# ╔══════════════════════════════════════════════════════════════╗
# ║              调仓逻辑                                          ║
# ╚══════════════════════════════════════════════════════════════╝

def _sell_removed_stocks(data, signals):
    g = _STATE
    if not g['bottom_n']: return
    for code in list(g['my_positions'].keys()):
        if code in g['bottom_n']: continue
        pos = g['my_positions'][code]
        if pos.get('bars_held', 0) < PROTECTION_BARS: continue
        logging.info("  [调仓卖出] %s 不在 bottom_%d | hold=%dd" % (code[:6], BOTTOM_N, pos.get('bars_held', 0)))
        _sell_position(data, code, {}, "调仓(不在bottom%d)" % BOTTOM_N, signals)


def _buy_new_stocks(data, signals):
    g = _STATE
    if not g['bottom_n']: return
    held = set(g['my_positions'].keys())
    pending_buys = set(sig.get('code', '') for sig in signals if sig.get('action') == 'buy')
    chuang_ye_count = sum(1 for c in held if c.startswith('300'))
    chuang_ye_pending = sum(1 for c in pending_buys if c.startswith('300'))
    to_buy = []
    for c in g['bottom_n']:
        if c in held or c in pending_buys: continue
        if c.startswith('300') and (chuang_ye_count + chuang_ye_pending) >= MAX_CHUANG_YE: continue
        to_buy.append(c)
        if c.startswith('300'): chuang_ye_pending += 1
    if not to_buy: return
    total_equity = khGet(data, "total_asset")
    if total_equity <= 0: return
    allocation_per_stock = total_equity / BOTTOM_N
    slots_remaining = BOTTOM_N - len(held) - len(pending_buys)
    if slots_remaining <= 0: return
    bought = 0
    for code in to_buy:
        if bought >= slots_remaining: break
        px = khPrice(data, code, "close")
        if px <= 0: continue
        shares = int(allocation_per_stock * 0.998 / px / 100) * 100
        if shares < 100: continue
        factor_val = g['rankings'].get(code, 0)
        reason = "%s Alpha144V7 entry=%.2f val=%.2e" % (code[:6], px, factor_val)
        sigs = generate_signal(data, code, px, shares, 'buy', reason)
        if sigs: signals.extend(sigs); bought += 1


# ╔══════════════════════════════════════════════════════════════╗
# ║              卖出执行 + 大盘防御 + 辅助函数                      ║
# ╚══════════════════════════════════════════════════════════════╝

def _process_pending_sells(data, hist, signals):
    g = _STATE
    if not g['pending_sells']: return
    retry_list = list(g['pending_sells']); g['pending_sells'] = []
    for code in retry_list:
        if code not in g['my_positions']: continue
        _sell_position(data, code, hist, "补卖(昨日跌停)", signals)


def _sell_position(data, code, hist, reason, signals):
    LIMIT_DOWN_PCT = -0.098
    g = _STATE
    if code not in g['my_positions']: return
    pos = g['my_positions'][code]
    px = khPrice(data, code, "close")
    if px <= 0: px = _get_price_from_hist(code, hist)
    if px <= 0: return
    if code in hist:
        df = hist[code]
        if df is not None and len(df) >= 2:
            try:
                arr_c = np.array(df['close'].values, dtype=float)
                if len(arr_c) >= 2 and arr_c[-2] > 0:
                    if (arr_c[-1] - arr_c[-2]) / arr_c[-2] <= LIMIT_DOWN_PCT:
                        logging.info("  [卖出延迟] %s 跌停, 延至次日" % code[:6])
                        if code not in g['pending_sells']: g['pending_sells'].append(code)
                        return
            except Exception: pass
    entry_price = pos['entry_price']
    pnl_pct = (px / entry_price - 1) * 100 if entry_price > 0 else 0
    bars = pos.get('bars_held', 0)
    sell_reason = "%s %s pnl=%.1f%% hold=%dd" % (code[:6], reason, pnl_pct, bars)
    sigs = generate_signal(data, code, px, 1.0, 'sell', sell_reason)
    if sigs: signals.extend(sigs)


def _check_market_v2(bm_hist):
    if not bm_hist or BENCHMARK not in bm_hist: return 'ok'
    df = bm_hist[BENCHMARK]
    if df is None or len(df) < MA_MARKET + 1: return 'ok'
    try:
        close_arr = np.array(df['close'].values, dtype=float)
        current = close_arr[-1]
        if np.isnan(current): return 'ok'
        ma = np.mean(close_arr[-MA_MARKET:])
        if np.isnan(ma) or ma <= 0: return 'ok'
        soft = ma * (1.0 - MARKET_FILTER_PCT); hard = ma * (1.0 - MARKET_FILTER_HARD_PCT)
        if current < hard: return 'hard_defense'
        if current < soft: return 'soft_defense'
        return 'ok'
    except Exception: return 'ok'


def _liquidate_all(data, signals, reason):
    g = _STATE
    for code in list(g['my_positions'].keys()):
        px = khPrice(data, code, "close")
        if px <= 0: continue
        pos = g['my_positions'][code]; bars = pos.get('bars_held', 0)
        sell_reason = "%s %s hold=%dd" % (code[:6], reason, bars)
        sigs = generate_signal(data, code, px, 1.0, 'sell', sell_reason)
        if sigs: signals.extend(sigs)


def _get_price_from_hist(code, hist):
    if code in hist:
        df = hist[code]
        if df is not None and len(df) > 0:
            try: return float(df['close'].values[-1])
            except Exception: pass
    return 0


def _sync_positions_from_framework(data):
    g = _STATE
    fw_positions = khGet(data, "positions")
    for code, pos in fw_positions.items():
        vol = pos.get("volume", 0)
        if vol <= 0: continue
        if code not in g['my_positions']:
            g['my_positions'][code] = {
                'entry_price': pos.get("avg_price", 0),
                'entry_bar': g['bar_counter'], 'bars_held': 0,
                'highest_price': pos.get("avg_price", 0),
            }
    for code in list(g['my_positions'].keys()):
        if code not in fw_positions or fw_positions[code].get("volume", 0) <= 0:
            del g['my_positions'][code]


def _apply_signals_to_state(signals, g):
    for sig in signals:
        code = sig.get('code', '')
        action = sig.get('action', '')
        price = sig.get('price', 0)
        if action == 'sell':
            if code in g['my_positions']: del g['my_positions'][code]
            if code in g['pending_sells']: g['pending_sells'].remove(code)
        elif action == 'buy':
            g['my_positions'][code] = {
                'entry_price': price, 'entry_bar': g['bar_counter'],
                'bars_held': 0, 'highest_price': price,
            }
