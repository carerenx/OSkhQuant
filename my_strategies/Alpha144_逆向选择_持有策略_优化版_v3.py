# coding: utf-8
"""
策略说明：
- 策略名称：Alpha144 逆向选择持有策略（V3）
- 核心理念：
    1. Alpha#144 因子 = sum(|ret|/amount, ret<0, 20)
       因子值越小 → 下跌日流动性冲击越小 → 筹码最稳定 → 适合长期持有
    2. 逆向选择 — 选择 Alpha144 最低的 12 只股票（剔除创业板300/科创板688），等权持有
    3. 低频调仓 — 每 30 个交易日重新排名
    4. 新仓保护 — 买入后 20 天内不参与调仓卖出 + 10日浮亏确认（浮亏减半仓）
    5. 分段止损 — 持有 <50天: -25%；持有 ≥50天: -15%（解决50-100天胜率低问题）
    6. 移动止盈 — 浮盈 >20% 后启用 10% 回撤止盈
    7. 大盘过滤（两级软防御）— MA60 软防御暂停买入，MA60×90% 硬防御清仓
    8. 波动率过滤 — 剔除近 20 日年化波动率 >60% 的高波动股
    9. 创业板限制 — 最多持有 3 只创业板股票

V3 改进（基于 V2 回测数据分析）：
  - 50-100天区间胜率仅 27.8%，唯一亏损区间 → 分段止损：≥50天收紧至 -15%
  - 2024-01 建仓即跌 -11.93% → 10日浮亏确认：新仓浮亏超 10 天则减半仓
  - BOTTOM_N 15→12，集中仓位让大赢家贡献更多
  - 剔除创业板(300)、科创板(688)，降低高波动误杀
  - 加入波动率过滤器，剔除年化波动 >60% 的标的
  - 限制创业板持仓上限 3 只

回测参数建议：
- 初始资金: 50万~1000万
- 基准: 000905.SH (中证500)
- 回测区间: 2020-01-01 ~ 2025-12-31
"""
from khQuantImport import *
import numpy as np

# ╔══════════════════════════════════════════════════════════════╗
# ║              用户可调参数                                    ║
# ╚══════════════════════════════════════════════════════════════╝

BENCHMARK = '000905.SH'       # 中证500

# ── 因子参数 ──
FACTOR_WINDOW    = 20         # Alpha144 因子计算窗口
BOTTOM_N         = 12         # 持仓数（V3：15→12，集中仓位）
REFRESH_INTERVAL = 30         # 调仓间隔

# ── 新仓保护 ──
PROTECTION_BARS  = 20         # 调仓保护期
CONFIRMATION_BARS = 10        # 浮亏确认期：持有超此天数且浮亏则减半仓（V3新增）

# ── 分段止损（V3 核心改进） ──
HARD_STOP_EARLY  = -0.25      # 持有 <50天: -25%
HARD_STOP_LATE   = -0.15      # 持有 ≥50天: -15%（解决50-100天胜率低问题）
STOP_TIGHTEN_BAR = 50         # 止损收紧触发天数

# ── 移动止盈 ──
TRAILING_PROFIT_PCT   = 0.20  # 触发线：浮盈 >20%
TRAILING_DRAWDOWN_PCT = 0.10  # 回撤线：从高点回落 10%

# ── 大盘过滤（两级软防御） ──
MA_MARKET              = 60   # 均线周期
MARKET_FILTER_PCT      = 0.05 # 软防御线：MA60×(1-5%)
MARKET_FILTER_HARD_PCT = 0.10 # 硬防御线：MA60×(1-10%)

# ── 波动率过滤（V3 新增） ──
MAX_ANNUAL_VOL = 0.60         # 剔除近20日年化波动率 >60% 的股票

# ── 板块限制（V3 新增） ──
SKIP_CHUANG_YE_BAN = True     # 剔除创业板（300开头）
SKIP_KE_CHUANG_BAN  = True    # 剔除科创板（688开头）
MAX_CHUANG_YE = 3             # 创业板最多持有数（若保留创业板则限制）

# ── 数据要求 ──
MIN_DAILY_AMOUNT = 3e7        # 最低日均成交额


# ╔══════════════════════════════════════════════════════════════╗
# ║              模块级全局状态                                  ║
# ╚══════════════════════════════════════════════════════════════╝

_STATE = {
    'rankings': {},
    'bottom_n': set(),
    'next_refresh_bar': 0,
    'bar_counter': 0,
    'pending_sells': [],
    'my_positions': {},          # {code: {entry_price, entry_bar, bars_held, highest_price, half_sold}}
    'need_bars': 0,
}


def init(stocks=None, data=None):
    g = _STATE
    g['rankings'] = {}
    g['bottom_n'] = set()
    g['next_refresh_bar'] = 0
    g['bar_counter'] = 0
    g['pending_sells'] = []
    g['my_positions'] = {}
    g['need_bars'] = max(FACTOR_WINDOW + 30, MA_MARKET + 10, PROTECTION_BARS + 10, STOP_TIGHTEN_BAR + 10)
    logging.info("[Alpha144-V3] 初始化 | 持仓=%d只 | 调仓=%dd | 保护=%dd | 浮亏确认=%dd | 止损: <50d=%.0f%% ≥50d=%.0f%% | 止盈触发=%.0f%%/回落=%.0f%% | 波动率<%.0f%% | 剔除创业板/科创板" % (
        BOTTOM_N, REFRESH_INTERVAL, PROTECTION_BARS, CONFIRMATION_BARS,
        abs(HARD_STOP_EARLY)*100, abs(HARD_STOP_LATE)*100,
        TRAILING_PROFIT_PCT*100, TRAILING_DRAWDOWN_PCT*100,
        MAX_ANNUAL_VOL*100))


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

    date_str = khGet(data, "date")
    logging.debug("[%s] bar=%d 持仓=%d只 资产=%.0f万" % (
        date_str, bar, len(g['my_positions']),
        khGet(data, "total_asset") / 10000))

    # ── 获取历史数据（需要波动率计算，多拉一些 bar） ──
    all_symbols = list(stocks)
    try:
        hist = khHistory(all_symbols, ["close", "amount"],
                         g['need_bars'], "1d", dn, fq="pre")
    except Exception as e:
        logging.error("[Alpha144-V3] 获取历史数据失败: %s" % str(e))
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
    # 3. 新仓浮亏确认（V3 新增：持仓超10天仍浮亏 → 减半仓）
    # ═══════════════════════════════════════════════════════════
    _check_confirmation(data, hist, signals)

    # ═══════════════════════════════════════════════════════════
    # 4. 分段止损 + 移动止盈
    # ═══════════════════════════════════════════════════════════
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
        logging.info("[Alpha144-V3] 排名刷新 | 候选=%d只 | 选中%d只 | 下次=bar %d" % (
            len(g['rankings']), BOTTOM_N, g['next_refresh_bar']))

    # ═══════════════════════════════════════════════════════════
    # 6. 硬防御清仓
    # ═══════════════════════════════════════════════════════════
    if market_state == 'hard_defense' and len(g['my_positions']) > 0:
        _liquidate_all(data, signals, "大盘硬防御清仓")

    # ═══════════════════════════════════════════════════════════
    # 7. 调仓 + 买入（仅市场 OK）
    # ═══════════════════════════════════════════════════════════
    if market_state == 'ok' and g['bottom_n']:
        _sell_removed_stocks(data, signals)
        _buy_new_stocks(data, signals)
    elif market_state == 'soft_defense':
        logging.debug("[Alpha144-V3] 软防御：暂停买入/调仓，持仓持有")

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
    if n < needed:
        return None
    recent_c = arr_c[-needed:]
    recent_a = arr_a[-needed:]
    alpha = 0.0
    neg_count = 0
    for i in range(1, len(recent_c)):
        if recent_c[i - 1] <= 0:
            continue
        ret_i = (recent_c[i] - recent_c[i - 1]) / recent_c[i - 1]
        if ret_i < 0:
            amount_i = recent_a[i] if i < len(recent_a) else 0
            if amount_i > 0:
                alpha += abs(ret_i) / amount_i
                neg_count += 1
    return 0.0 if neg_count == 0 else alpha


def _calc_annual_vol(close_vals):
    """计算近 20 日年化波动率（V3 新增）"""
    if len(close_vals) < 21:
        return 999
    arr = np.array(close_vals[-21:], dtype=float)
    rets = np.diff(arr) / arr[:-1]
    daily_vol = np.std(rets)
    return daily_vol * np.sqrt(252)


def _compute_factor_rankings_asc(stocks, hist, dn):
    """计算全股票池 Alpha144 因子，含板块过滤和波动率过滤"""
    raw_scores = {}
    for code in stocks:
        # ── 板块过滤（V3：剔除创业板300 + 科创板688） ──
        if SKIP_CHUANG_YE_BAN and code.startswith('300'):
            continue
        if SKIP_KE_CHUANG_BAN and code.startswith('688'):
            continue

        if code not in hist:
            continue
        df = hist[code]
        if df is None or len(df) < FACTOR_WINDOW + 1:
            continue
        try:
            close_vals = df['close'].values
            amount_vals = df['amount'].values if 'amount' in df.columns else None
        except Exception:
            continue
        if amount_vals is None or len(amount_vals) < FACTOR_WINDOW + 1:
            continue

        # ── 流动性过滤 ──
        try:
            recent_amt = np.array(amount_vals[-FACTOR_WINDOW:], dtype=float)
            if np.mean(recent_amt) < MIN_DAILY_AMOUNT:
                continue
        except Exception:
            continue

        # ── 波动率过滤（V3 新增） ──
        ann_vol = _calc_annual_vol(close_vals)
        if ann_vol > MAX_ANNUAL_VOL:
            continue

        val = _calc_alpha144(close_vals, amount_vals)
        if val is not None:
            raw_scores[code] = val

    return raw_scores


# ╔══════════════════════════════════════════════════════════════╗
# ║              新仓浮亏确认（V3 新增）                           ║
# ╚══════════════════════════════════════════════════════════════╝

def _check_confirmation(data, hist, signals):
    """
    新仓浮亏确认：持有超过 CONFIRMATION_BARS(10) 天且仍浮亏 → 减半仓。

    逻辑：
      - 只对未触发过 half_sold 的持仓检查
      - bars_held >= CONFIRMATION_BARS 且浮亏 → 卖出一半
      - 标记 half_sold=True，不再二次触发
    """
    g = _STATE
    to_half = []

    for code, pos in g['my_positions'].items():
        if pos.get('half_sold'):
            continue
        if pos.get('bars_held', 0) < CONFIRMATION_BARS:
            continue

        px = khPrice(data, code, "close")
        if px <= 0:
            px = _get_price_from_hist(code, hist)
            if px <= 0:
                continue

        entry = pos['entry_price']
        if entry <= 0:
            continue
        pnl_pct = (px / entry - 1.0)

        if pnl_pct < 0:
            logging.info("  [浮亏确认] %s 持有%d天仍浮亏 %.1f%% → 减半仓 | entry=%.2f now=%.2f" % (
                code[:6], pos['bars_held'], pnl_pct * 100, entry, px))
            to_half.append((code, "浮亏确认减半仓(持有%d天浮亏%.1f%%)" % (pos['bars_held'], pnl_pct * 100)))

    for code, reason in to_half:
        _sell_half_position(data, code, hist, reason, signals)


def _sell_half_position(data, code, hist, reason, signals):
    """卖出半仓（V3 新增），标记 half_sold"""
    g = _STATE
    if code not in g['my_positions']:
        return
    pos = g['my_positions'][code]
    px = khPrice(data, code, "close")
    if px <= 0:
        px = _get_price_from_hist(code, hist)
        if px <= 0:
            return

    # 减半仓：ratio=0.5
    entry_price = pos['entry_price']
    pnl_pct = (px / entry_price - 1) * 100 if entry_price > 0 else 0
    bars = pos.get('bars_held', 0)
    sell_reason = "%s %s pnl=%.1f%% hold=%dd" % (code[:6], reason, pnl_pct, bars)

    sigs = generate_signal(data, code, px, 0.5, 'sell', sell_reason)
    if sigs:
        signals.extend(sigs)
        pos['half_sold'] = True
        logging.info("<<< [减半仓] %s @ %.2f | 盈亏 %+.1f%% | 持有%d天 | %s" % (
            code, px, pnl_pct, bars, reason))


# ╔══════════════════════════════════════════════════════════════╗
# ║              分段止损（V3 核心改进）                           ║
# ╚══════════════════════════════════════════════════════════════╝

def _check_stop_loss(data, hist, signals):
    """
    分段止损：
      - 持有 < 50天: 止损线 -25%（宽，给新仓喘息空间）
      - 持有 ≥ 50天: 止损线 -15%（紧，解决50-100天胜率低问题）
    """
    g = _STATE
    to_sell = []

    for code, pos in g['my_positions'].items():
        px = khPrice(data, code, "close")
        if px <= 0:
            px = _get_price_from_hist(code, hist)
            if px <= 0:
                continue
        entry = pos['entry_price']
        if entry <= 0:
            continue
        pnl_pct = (px / entry - 1.0)
        bars = pos.get('bars_held', 0)

        stop_line = HARD_STOP_LATE if bars >= STOP_TIGHTEN_BAR else HARD_STOP_EARLY

        if pnl_pct <= stop_line:
            logging.info("  [止损触发] %s 浮亏 %.1f%% <= %.0f%% | entry=%.2f now=%.2f hold=%dd (%s)" % (
                code, pnl_pct * 100, stop_line * 100, entry, px, bars,
                "紧止损" if bars >= STOP_TIGHTEN_BAR else "宽止损"))
            to_sell.append((code, "止损%.0f%%(浮亏%.1f%% hold=%dd)" % (stop_line * 100, pnl_pct * 100, bars)))

    for code, reason in to_sell:
        _sell_position(data, code, hist, reason, signals)


# ╔══════════════════════════════════════════════════════════════╗
# ║              移动止盈                                        ║
# ╚══════════════════════════════════════════════════════════════╝

def _check_trailing_stop(data, hist, signals):
    g = _STATE
    to_sell = []
    for code, pos in g['my_positions'].items():
        px = khPrice(data, code, "close")
        if px <= 0:
            continue
        entry = pos['entry_price']
        if entry <= 0:
            continue
        highest = pos.get('highest_price', entry)
        if highest <= 0:
            continue
        profit_from_entry = (highest / entry - 1.0)
        if profit_from_entry >= TRAILING_PROFIT_PCT:
            drawdown_from_peak = (px / highest - 1.0)
            if drawdown_from_peak <= -TRAILING_DRAWDOWN_PCT:
                logging.info("  [移动止盈] %s 从高点 %.2f 回落 %.1f%% | entry=%.2f high=%.2f now=%.2f hold=%dd" % (
                    code, highest, drawdown_from_peak * 100, entry, highest, px,
                    pos.get('bars_held', 0)))
                to_sell.append((code, "移动止盈(高点%.2f回落%.1f%%)" % (highest, drawdown_from_peak * 100)))
    for code, reason in to_sell:
        _sell_position(data, code, hist, reason, signals)


# ╔══════════════════════════════════════════════════════════════╗
# ║              调仓逻辑                                        ║
# ╚══════════════════════════════════════════════════════════════╝

def _sell_removed_stocks(data, signals):
    g = _STATE
    if not g['bottom_n']:
        return
    for code in list(g['my_positions'].keys()):
        if code in g['bottom_n']:
            continue
        pos = g['my_positions'][code]
        if pos.get('bars_held', 0) < PROTECTION_BARS:
            continue
        logging.info("  [调仓卖出] %s 不在 bottom_%d | hold=%dd" % (code[:6], BOTTOM_N, pos.get('bars_held', 0)))
        _sell_position(data, code, {}, "调仓(不在bottom%d)" % BOTTOM_N, signals)


def _buy_new_stocks(data, signals):
    g = _STATE
    if not g['bottom_n']:
        return

    held = set(g['my_positions'].keys())
    pending_buys = set(sig.get('code', '') for sig in signals if sig.get('action') == 'buy')

    # ── 创业板持仓限制（V3 新增） ──
    chuang_ye_count = sum(1 for c in held if c.startswith('300'))
    chuang_ye_pending = sum(1 for c in pending_buys if c.startswith('300'))

    to_buy = []
    for c in g['bottom_n']:
        if c in held or c in pending_buys:
            continue
        # 创业板限制
        if c.startswith('300') and (chuang_ye_count + chuang_ye_pending) >= MAX_CHUANG_YE:
            continue
        to_buy.append(c)
        if c.startswith('300'):
            chuang_ye_pending += 1

    if not to_buy:
        return

    total_equity = khGet(data, "total_asset")
    if total_equity <= 0:
        return

    allocation_per_stock = total_equity / BOTTOM_N
    slots_remaining = BOTTOM_N - len(held) - len(pending_buys)
    if slots_remaining <= 0:
        return

    bought = 0
    for code in to_buy:
        if bought >= slots_remaining:
            break
        px = khPrice(data, code, "close")
        if px <= 0:
            continue
        shares = int(allocation_per_stock * 0.998 / px / 100) * 100
        if shares < 100:
            continue
        factor_val = g['rankings'].get(code, 0)
        reason = "%s Alpha144V3 entry=%.2f val=%.2e" % (code[:6], px, factor_val)
        sigs = generate_signal(data, code, px, shares, 'buy', reason)
        if sigs:
            signals.extend(sigs)
            bought += 1
            logging.info(">>> [买入] %s × %d股 @ %.2f | 金额=%.0f | alpha144=%.2e" % (
                code, shares, px, shares * px, factor_val))


# ╔══════════════════════════════════════════════════════════════╗
# ║              卖出执行                                        ║
# ╚══════════════════════════════════════════════════════════════╝

def _process_pending_sells(data, hist, signals):
    g = _STATE
    if not g['pending_sells']:
        return
    retry_list = list(g['pending_sells'])
    g['pending_sells'] = []
    for code in retry_list:
        if code not in g['my_positions']:
            continue
        _sell_position(data, code, hist, "补卖(昨日跌停)", signals)


def _sell_position(data, code, hist, reason, signals):
    LIMIT_DOWN_PCT = -0.098
    g = _STATE
    if code not in g['my_positions']:
        return
    pos = g['my_positions'][code]
    px = khPrice(data, code, "close")
    if px <= 0:
        px = _get_price_from_hist(code, hist)
        if px <= 0:
            return
    # 跌停检查
    if code in hist:
        df = hist[code]
        if df is not None and len(df) >= 2:
            try:
                arr_c = np.array(df['close'].values, dtype=float)
                if len(arr_c) >= 2 and arr_c[-2] > 0:
                    if (arr_c[-1] - arr_c[-2]) / arr_c[-2] <= LIMIT_DOWN_PCT:
                        logging.info("  [卖出延迟] %s 跌停, 延至次日" % code[:6])
                        if code not in g['pending_sells']:
                            g['pending_sells'].append(code)
                        return
            except Exception:
                pass
    entry_price = pos['entry_price']
    pnl_pct = (px / entry_price - 1) * 100 if entry_price > 0 else 0
    bars = pos.get('bars_held', 0)
    sell_reason = "%s %s pnl=%.1f%% hold=%dd" % (code[:6], reason, pnl_pct, bars)
    sigs = generate_signal(data, code, px, 1.0, 'sell', sell_reason)
    if sigs:
        signals.extend(sigs)
        logging.info("<<< [卖出] %s @ %.2f | 盈亏 %+.1f%% | 持有%d天 | %s" % (
            code, px, pnl_pct, bars, reason))


# ╔══════════════════════════════════════════════════════════════╗
# ║              大盘防御                                        ║
# ╚══════════════════════════════════════════════════════════════╝

def _check_market_v2(bm_hist):
    if not bm_hist or BENCHMARK not in bm_hist:
        return 'ok'
    df = bm_hist[BENCHMARK]
    if df is None or len(df) < MA_MARKET + 1:
        return 'ok'
    try:
        close_arr = np.array(df['close'].values, dtype=float)
        current = close_arr[-1]
        if np.isnan(current):
            return 'ok'
        ma = np.mean(close_arr[-MA_MARKET:])
        if np.isnan(ma) or ma <= 0:
            return 'ok'
        soft = ma * (1.0 - MARKET_FILTER_PCT)
        hard = ma * (1.0 - MARKET_FILTER_HARD_PCT)
        if current < hard:
            logging.info("[市场] %s=%.2f < MA%d×%.0f%%=%.2f → 硬防御清仓" % (
                BENCHMARK, current, MA_MARKET, (1-MARKET_FILTER_HARD_PCT)*100, hard))
            return 'hard_defense'
        if current < soft:
            logging.info("[市场] %s=%.2f < MA%d×%.0f%%=%.2f → 软防御" % (
                BENCHMARK, current, MA_MARKET, (1-MARKET_FILTER_PCT)*100, soft))
            return 'soft_defense'
        return 'ok'
    except Exception:
        return 'ok'


def _liquidate_all(data, signals, reason):
    g = _STATE
    for code in list(g['my_positions'].keys()):
        px = khPrice(data, code, "close")
        if px <= 0:
            continue
        pos = g['my_positions'][code]
        bars = pos.get('bars_held', 0)
        sell_reason = "%s %s hold=%dd" % (code[:6], reason, bars)
        sigs = generate_signal(data, code, px, 1.0, 'sell', sell_reason)
        if sigs:
            signals.extend(sigs)
            logging.info("<<< [清仓] %s @ %.2f | %s" % (code, px, reason))


# ╔══════════════════════════════════════════════════════════════╗
# ║              辅助函数                                        ║
# ╚══════════════════════════════════════════════════════════════╝

def _get_price_from_hist(code, hist):
    if code in hist:
        df = hist[code]
        if df is not None and len(df) > 0:
            try:
                return float(df['close'].values[-1])
            except Exception:
                pass
    return 0


def _sync_positions_from_framework(data):
    g = _STATE
    fw_positions = khGet(data, "positions")
    for code, pos in fw_positions.items():
        vol = pos.get("volume", 0)
        if vol <= 0:
            continue
        if code not in g['my_positions']:
            g['my_positions'][code] = {
                'entry_price': pos.get("avg_price", 0),
                'entry_bar': g['bar_counter'],
                'bars_held': 0,
                'highest_price': pos.get("avg_price", 0),
                'half_sold': False,
            }
    for code in list(g['my_positions'].keys()):
        if code not in fw_positions or fw_positions[code].get("volume", 0) <= 0:
            del g['my_positions'][code]


def _apply_signals_to_state(signals, g):
    for sig in signals:
        code = sig.get('code', '')
        action = sig.get('action', '')
        price = sig.get('price', 0)
        ratio = sig.get('ratio', 1.0)
        if action == 'sell':
            if code in g['my_positions']:
                if ratio < 1.0:
                    # 部分卖出，标记 half_sold
                    g['my_positions'][code]['half_sold'] = True
                else:
                    del g['my_positions'][code]
            if code in g['pending_sells']:
                g['pending_sells'].remove(code)
        elif action == 'buy':
            g['my_positions'][code] = {
                'entry_price': price,
                'entry_bar': g['bar_counter'],
                'bars_held': 0,
                'highest_price': price,
                'half_sold': False,
            }
