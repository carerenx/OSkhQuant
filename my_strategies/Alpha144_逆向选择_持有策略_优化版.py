# coding: utf-8
"""
策略说明：
- 策略名称：Alpha144 逆向选择持有策略（优化版）
- 核心理念：
    1. Alpha#144 因子 = sum(|ret|/amount, ret<0, 20)
       因子值越小 → 下跌日流动性冲击越小 → 筹码最稳定 → 适合长期持有
    2. 逆向选择 — 选择 Alpha144 最低的 15 只股票，等权持有
    3. 低频调仓 — 每 30 个交易日重新排名，降低换手率
    4. 新仓保护 — 买入后 20 天内不参与调仓卖出，给 alpha 释放时间
    5. 宽止损 — -20% 硬止损（适配中证500日常波动）
    6. 移动止盈 — 浮盈 >20% 后启用 10% 回撤止盈
    7. 大盘过滤 — 基准指数 < MA20 时空仓防御

优化要点（基于回测数据分析）：
  - 原版 -10% 止损触发 97 次，占卖出 30%，均亏 -12.28%，过紧
     → 放宽至 -20%，减少误杀
  - 原版 10 天调仓 221 笔，48.4% 亏损，持有 <20 天几乎全亏
     → 调仓间隔延长至 30 天，加 20 天新仓保护期
  - 原版最大回撤 -39.64%，-37% 集中在 2024 年 1-9 月
     → 加入大盘 MA20 过滤，下行市空仓
  - 原版 10 只持仓集中度偏高
     → 扩大至 15 只，分散风险
  - 持有 >50 天胜率 64.7%，>100 天胜率 100%
     → 减少调仓频率，让盈利奔跑

回测参数建议：
- 初始资金: 50万~1000万
- 基准: 000905.SH (中证500) 或 000300.SH (沪深300)
- 回测区间: 2020-01-01 ~ 2025-12-31
- 手续费: 佣金万2.5, 印花税千1(卖), 最低5元
"""
from khQuantImport import *
import numpy as np

# ╔══════════════════════════════════════════════════════════════╗
# ║              用户可调参数                                    ║
# ╚══════════════════════════════════════════════════════════════╝

# ── 基准 ──
BENCHMARK = '000905.SH'       # 中证500

# ── 因子参数 ──
FACTOR_WINDOW   = 20          # Alpha144 因子计算窗口（交易日）
BOTTOM_N        = 15          # 逆向选择：持仓数（原10→15，分散风险）
REFRESH_INTERVAL = 30         # 调仓间隔（原10→30天，减少换手）

# ── 新仓保护期 ──
PROTECTION_BARS = 20          # 买入后 20 天内不参与调仓卖出（给 alpha 释放时间）

# ── 止损止盈 ──
HARD_STOP_PCT   = -0.20       # 硬止损 -20%（原-10%→放宽至-20%，适配中证500波动）
TRAILING_PROFIT_PCT = 0.20    # 浮动止盈触发线：浮盈 >20% 后启用
TRAILING_DRAWDOWN_PCT = 0.10  # 浮动止盈回撤：从最高点回落 10% 止盈

# ── 大盘过滤 ──
MA_MARKET = 20                # 市场均线周期
MARKET_FILTER_PCT = 0.03      # 大盘低于 MA20×(1-3%) 时空仓

# ── 数据要求 ──
MIN_DAILY_AMOUNT = 3e7        # 最低日均成交额 (3000万)

# ── 科创板过滤 ──
SKIP_KE_CHUANG_BAN = True     # 剔除科创板（688开头）


# ╔══════════════════════════════════════════════════════════════╗
# ║              模块级全局状态                                  ║
# ╚══════════════════════════════════════════════════════════════╝

_STATE = {
    'rankings': {},              # {code: factor_value}  全量因子排名（升序）
    'bottom_n': set(),           # 当前选中的最后 N 只股票
    'next_refresh_bar': 0,       # 下次刷新排名的 bar 计数
    'bar_counter': 0,            # 自增 bar 计数
    'pending_sells': [],         # 跌停无法卖出、待重试的 code 列表
    'my_positions': {},          # {code: {entry_price, entry_bar, bars_held, highest_price}}
    'need_bars': 0,              # 缓存：需要的 K 线数量
}


def init(stocks=None, data=None):
    """策略初始化"""
    g = _STATE
    g['rankings'] = {}
    g['bottom_n'] = set()
    g['next_refresh_bar'] = 0
    g['bar_counter'] = 0
    g['pending_sells'] = []
    g['my_positions'] = {}
    g['need_bars'] = max(FACTOR_WINDOW + 30, MA_MARKET + 10, PROTECTION_BARS + 10)
    logging.info("[Alpha144-优化版] 初始化 | 因子窗口=%d天 | 持仓=%d只 | 调仓间隔=%d天 | 新仓保护=%d天 | 止损=%.0f%% | 移动止盈触发=%.0f%%/回撤=%.0f%%" % (
        FACTOR_WINDOW, BOTTOM_N, REFRESH_INTERVAL, PROTECTION_BARS,
        abs(HARD_STOP_PCT) * 100, TRAILING_PROFIT_PCT * 100, TRAILING_DRAWDOWN_PCT * 100))


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
    logging.debug("=" * 50)
    logging.debug("[%s] bar=%d 持仓=%d只 资产=%.0f万 现金=%.0f万" % (
        date_str, bar, len(g['my_positions']),
        khGet(data, "total_asset") / 10000,
        khGet(data, "cash") / 10000))

    # ── 获取历史数据 ──
    all_symbols = list(stocks)
    try:
        hist = khHistory(all_symbols, ["close", "amount"],
                         g['need_bars'], "1d", dn, fq="pre")
    except Exception as e:
        logging.error("[Alpha144-优化版] 获取历史数据失败: %s" % str(e))
        return signals

    # ── 获取基准指数数据 ──
    bm_hist = {}
    try:
        bm_hist = khHistory([BENCHMARK], ["close"],
                            MA_MARKET + 10, "1d", dn, fq="pre")
    except Exception:
        pass

    # ═══════════════════════════════════════════════════════════
    # 1. 大盘过滤（新增：解决 -37% 回撤问题）
    # ═══════════════════════════════════════════════════════════
    market_ok = _check_market(bm_hist)

    # ═══════════════════════════════════════════════════════════
    # 2. 处理跌停 pending 单
    # ═══════════════════════════════════════════════════════════
    _process_pending_sells(data, hist, signals)

    # ═══════════════════════════════════════════════════════════
    # 3. 止损检查 + 移动止盈检查
    # ═══════════════════════════════════════════════════════════
    _check_stop_loss(data, hist, signals)
    _check_trailing_stop(data, hist, signals)

    # ═══════════════════════════════════════════════════════════
    # 4. 因子排名刷新 — 每 30 天
    # ═══════════════════════════════════════════════════════════
    if bar >= g['next_refresh_bar']:
        g['rankings'] = _compute_factor_rankings_asc(stocks, hist, dn)
        sorted_codes = sorted(g['rankings'].keys(), key=lambda c: g['rankings'][c])
        g['bottom_n'] = set(sorted_codes[:BOTTOM_N])
        g['next_refresh_bar'] = bar + REFRESH_INTERVAL
        logging.info("[Alpha144-优化版] 排名刷新 | 候选=%d只 | 选中最后%d只 | 下次刷新=bar %d" % (
            len(g['rankings']), BOTTOM_N, g['next_refresh_bar']))

    # ═══════════════════════════════════════════════════════════
    # 5. 调仓（仅在大盘 OK 时）
    #    - 卖出不在 bottom_n 中且已过保护期的持仓
    #    - 买入 bottom_n 中未持仓的股票
    # ═══════════════════════════════════════════════════════════
    if market_ok and g['bottom_n']:
        _sell_removed_stocks(data, signals)
        _buy_new_stocks(data, signals)

    # ═══════════════════════════════════════════════════════════
    # 6. 大盘防御清仓
    # ═══════════════════════════════════════════════════════════
    if not market_ok and len(g['my_positions']) > 0:
        _liquidate_all(data, signals, "大盘防御清仓")

    # ═══════════════════════════════════════════════════════════
    # 7. 更新状态
    # ═══════════════════════════════════════════════════════════
    _apply_signals_to_state(signals, g)

    for code in list(g['my_positions'].keys()):
        pos = g['my_positions'][code]
        pos['bars_held'] += 1
        # 更新持仓期间最高价（用于移动止盈）
        px = khPrice(data, code, "close")
        if px > pos.get('highest_price', 0):
            pos['highest_price'] = px

    # ── 摘要 ──
    pos_codes = list(g['my_positions'].keys())
    if pos_codes:
        logging.debug("[摘要] 持仓=%d只 %s | 天数=%s | 市场=%s" % (
            len(pos_codes), [c[:6] for c in pos_codes],
            [str(g['my_positions'][c]['bars_held']) for c in pos_codes],
            "可交易" if market_ok else "防御"))
    else:
        logging.debug("[摘要] 空仓 | 市场=%s" % ("可交易" if market_ok else "防御"))

    return signals


# ╔══════════════════════════════════════════════════════════════╗
# ║              Alpha#144 因子计算                               ║
# ╚══════════════════════════════════════════════════════════════╝

def _calc_alpha144(close_arr, amount_arr):
    """
    alpha_144 = Σ(|ret_i| / amount_i)  for ret_i < 0, over last 20 periods
    因子值越小 → 下跌日流动性冲击越小 → 筹码稳定 → 适合持有
    """
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
        prev_close = recent_c[i - 1]
        curr_close = recent_c[i]
        if prev_close <= 0:
            continue
        ret_i = (curr_close - prev_close) / prev_close
        if ret_i < 0:
            amount_i = recent_a[i] if i < len(recent_a) else 0
            if amount_i > 0:
                alpha += abs(ret_i) / amount_i
                neg_count += 1

    return 0.0 if neg_count == 0 else alpha


def _compute_factor_rankings_asc(stocks, hist, dn):
    """计算全股票池 Alpha144 因子，返回全部有效股票的因子值映射"""
    raw_scores = {}
    for code in stocks:
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
        try:
            recent_amt = np.array(amount_vals[-FACTOR_WINDOW:], dtype=float)
            if np.mean(recent_amt) < MIN_DAILY_AMOUNT:
                continue
        except Exception:
            continue
        val = _calc_alpha144(close_vals, amount_vals)
        if val is not None:
            raw_scores[code] = val
    return raw_scores


# ╔══════════════════════════════════════════════════════════════╗
# ║              止损 — 硬止损 -20%（放宽后）                       ║
# ╚══════════════════════════════════════════════════════════════╝

def _check_stop_loss(data, hist, signals):
    """硬止损：浮亏 <= -20% 无条件平仓"""
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
        if pnl_pct <= HARD_STOP_PCT:
            logging.info("  [止损触发] %s 浮亏 %.1f%% <= %.0f%% | entry=%.2f current=%.2f hold=%dd" % (
                code, pnl_pct * 100, HARD_STOP_PCT * 100, entry, px, pos.get('bars_held', 0)))
            to_sell.append((code, "止损%.0f%%(浮亏%.1f%%)" % (HARD_STOP_PCT * 100, pnl_pct * 100)))

    for code, reason in to_sell:
        _sell_position(data, code, hist, reason, signals)


# ╔══════════════════════════════════════════════════════════════╗
# ║              移动止盈（新增）                                   ║
# ╚══════════════════════════════════════════════════════════════╝

def _check_trailing_stop(data, hist, signals):
    """
    移动止盈：浮盈 >20% 后，从最高点回撤 10% 即止盈。

    逻辑：
      - 记录持仓期间最高价 highest_price
      - 当 highest_price / entry - 1 >= 20%（触发线）
      - 且 当前价 / highest_price - 1 <= -10%（回撤线）→ 卖出
    """
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

        # 检查是否触发移动止盈线
        profit_from_entry = (highest / entry - 1.0)
        if profit_from_entry >= TRAILING_PROFIT_PCT:
            # 已触发，检查是否从最高点回撤
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
    """
    卖出不在 bottom_n 中且已过保护期的持仓。

    新仓保护期（PROTECTION_BARS=20天）：买入后 20 天内不因调仓卖出。
    """
    g = _STATE
    if not g['bottom_n']:
        return

    for code in list(g['my_positions'].keys()):
        if code in g['bottom_n']:
            continue  # 仍在选中池中，持有

        pos = g['my_positions'][code]
        bars = pos.get('bars_held', 0)

        # 新仓保护：买入后 20 天内不因调仓卖出
        if bars < PROTECTION_BARS:
            logging.debug("  [调仓保护] %s 持有仅%d天 < %d天保护期，暂不卖出" % (
                code[:6], bars, PROTECTION_BARS))
            continue

        logging.info("  [调仓卖出] %s 不在 bottom_%d 中 | 持有=%dd | 盈亏=%+.1f%%" % (
            code[:6], BOTTOM_N, bars,
            (khPrice(data, code, "close") / pos['entry_price'] - 1) * 100 if pos['entry_price'] > 0 else 0))
        _sell_position(data, code, {}, "调仓(不在bottom%d)" % BOTTOM_N, signals)


def _buy_new_stocks(data, signals):
    """
    买入 bottom_n 中未持仓的股票，等权分配。
    资金分配：总资产 / BOTTOM_N，整数百股。
    """
    g = _STATE
    if not g['bottom_n']:
        return

    held = set(g['my_positions'].keys())
    # 还需要考虑已发出的买入信号（本 bar 内尚未更新本地状态）
    pending_buys = set()
    for sig in signals:
        if sig.get('action') == 'buy':
            pending_buys.add(sig.get('code', ''))

    to_buy = [c for c in g['bottom_n'] if c not in held and c not in pending_buys]
    if not to_buy:
        return

    total_equity = khGet(data, "total_asset")
    if total_equity <= 0:
        return

    # 等权分配：总资产 / BOTTOM_N
    allocation_per_stock = total_equity / BOTTOM_N

    slots_remaining = BOTTOM_N - len(held) - len(pending_buys)
    if slots_remaining <= 0:
        return

    for code in to_buy[:slots_remaining]:
        px = khPrice(data, code, "close")
        if px <= 0:
            logging.warning("  [买入跳过] %s 无法获取价格" % code[:6])
            continue

        shares = int(allocation_per_stock * 0.998 / px / 100) * 100
        if shares < 100:
            logging.warning("  [买入跳过] %s 资金不足: alloc=%.0f px=%.2f" % (
                code[:6], allocation_per_stock, px))
            continue

        factor_val = g['rankings'].get(code, 0)
        reason = "%s Alpha144逆向 entry=%.2f val=%.2e" % (code[:6], px, factor_val)
        sigs = generate_signal(data, code, px, shares, 'buy', reason)
        if sigs:
            signals.extend(sigs)
            logging.info(">>> [买入] %s × %d股 @ %.2f | 金额=%.0f | alpha144=%.2e" % (
                code, shares, px, shares * px, factor_val))
        else:
            logging.warning("  [买入失败] %s 生成信号失败" % code[:6])


# ╔══════════════════════════════════════════════════════════════╗
# ║              卖出执行                                        ║
# ╚══════════════════════════════════════════════════════════════╝

def _process_pending_sells(data, hist, signals):
    """处理之前因跌停未能卖出的持仓"""
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
    """卖出单只股票，含跌停约束"""
    LIMIT_DOWN_PCT = -0.098
    g = _STATE

    if code not in g['my_positions']:
        return

    pos = g['my_positions'][code]
    px = khPrice(data, code, "close")
    if px <= 0:
        px = _get_price_from_hist(code, hist)
        if px <= 0:
            logging.warning("  [卖出跳过] %s 无法获取价格" % code[:6])
            return

    # 跌停检查
    if code in hist:
        df = hist[code]
        if df is not None and len(df) >= 2:
            try:
                arr_c = np.array(df['close'].values, dtype=float)
                if len(arr_c) >= 2 and arr_c[-2] > 0:
                    daily_ret = (arr_c[-1] - arr_c[-2]) / arr_c[-2]
                    if daily_ret <= LIMIT_DOWN_PCT:
                        logging.info("  [卖出延迟] %s 跌停 (%.1f%%), 延至次日" % (
                            code, daily_ret * 100))
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
    else:
        logging.warning("  [卖出失败] %s 生成信号失败" % code[:6])


# ╔══════════════════════════════════════════════════════════════╗
# ║              大盘防御                                        ║
# ╚══════════════════════════════════════════════════════════════╝

def _liquidate_all(data, signals, reason):
    """大盘触发防御，清空所有持仓"""
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


def _check_market(bm_hist):
    """大盘过滤器：基准 >= MA20 × (1 - 3%) → 可交易"""
    if not bm_hist or BENCHMARK not in bm_hist:
        return True
    df = bm_hist[BENCHMARK]
    if df is None or len(df) < MA_MARKET + 1:
        return True
    try:
        close_arr = np.array(df['close'].values, dtype=float)
        current = close_arr[-1]
        if np.isnan(current):
            return True
        ma = np.mean(close_arr[-MA_MARKET:])
        if np.isnan(ma) or ma <= 0:
            return True
        threshold = ma * (1.0 - MARKET_FILTER_PCT)
        ok = current >= threshold
        if not ok:
            logging.info("[市场] %s=%.2f < MA%d×%.0f%%=%.2f → 空仓防御" % (
                BENCHMARK, current, MA_MARKET, (1 - MARKET_FILTER_PCT) * 100, threshold))
        return ok
    except Exception:
        return True


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
            if code in g['my_positions']:
                del g['my_positions'][code]
            if code in g['pending_sells']:
                g['pending_sells'].remove(code)
        elif action == 'buy':
            g['my_positions'][code] = {
                'entry_price': price,
                'entry_bar': g['bar_counter'],
                'bars_held': 0,
                'highest_price': price,
            }
