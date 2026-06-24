# coding: utf-8
"""
策略说明：
- 策略名称：Alpha144 逆向选择持有策略
- 核心理念：
    1. Alpha#144 因子 = sum(|ret|/amount, ret<0, 20)
       捕捉下跌日的流动性冲击：放量下跌 → 恐慌抛售筹码被吸收 → 后续反弹
       因子值越大 → 下跌日单位成交额的价格冲击越大 → 流动性越差
    2. 逆向选择 — 不同于原版选择 Alpha144 最高的股票做反弹，
       本策略选择 Alpha144 最低（最后排）的 10 只股票，代表流动性最稳定、
       下跌日价格冲击最小的标的，适合长期持有
    3. 持有策略 — 一直持有，不做择时出场，定期（每10个交易日）重新排名调仓
    4. 止损策略 — 单只股票浮亏超过 -10% 时无条件止损平仓
- 指标来源：原始 Alpha144 因子公式，使用 numpy 计算
- 适用场景：中证500 成分股，追求稳定持有 + 严格止损

回测参数建议：
- 初始资金: 1000万
- 基准: 000905.SH (中证500)
- 回测区间: 2020-01-01 ~ 2025-12-31
- 手续费: 佣金万2.5, 印花税千1(卖), 最低5元
"""
from khQuantImport import *  # 导入统一工具与指标
import numpy as np

# ╔══════════════════════════════════════════════════════════════╗
# ║              用户可调参数                                    ║
# ╚══════════════════════════════════════════════════════════════╝

# ── 基准 ──
BENCHMARK = '000905.SH'       # 中证500

# ── 因子参数 ──
FACTOR_WINDOW   = 20          # Alpha144 因子计算窗口（交易日）
BOTTOM_N        = 10          # 逆向选择：取排名最后的 N 只股票
REFRESH_INTERVAL = 10         # 选股刷新间隔（交易日），每10天重新排名调仓

# ── 止损参数 ──
HARD_STOP_PCT   = -0.10       # 硬止损: 浮亏超过 10% 无条件平仓

# ── 数据要求 ──
MIN_DAILY_AMOUNT = 3e7        # 最低日均成交额 (3000万, 过滤流动性极差股)

# ── 科创板过滤 ──
SKIP_KE_CHUANG_BAN = True     # 剔除科创板股票（688开头），波动大


# ╔══════════════════════════════════════════════════════════════╗
# ║              模块级全局状态（跨 Bar 持久化）                  ║
# ╚══════════════════════════════════════════════════════════════╝

_STATE = {
    'rankings': {},              # {code: factor_value}  最近一次因子排名（升序，全量）
    'bottom_10': set(),          # 当前选中的最后10只股票
    'next_refresh_bar': 0,       # 下次刷新排名的 bar 计数
    'bar_counter': 0,            # 自增 bar 计数
    'pending_sells': [],         # 跌停无法卖出、待重试的 code 列表
    'my_positions': {},          # {code: {entry_price, entry_bar, bars_held}}
    'need_bars': 0,              # 缓存：需要的 K 线数量
}


def init(stocks=None, data=None):
    """策略初始化 — 重置全局状态"""
    _STATE['rankings'] = {}
    _STATE['bottom_10'] = set()
    _STATE['next_refresh_bar'] = 0
    _STATE['bar_counter'] = 0
    _STATE['pending_sells'] = []
    _STATE['my_positions'] = {}
    _STATE['need_bars'] = FACTOR_WINDOW + 30
    logging.info("[Alpha144-逆向持有] 初始化完成 | 因子窗口=%d天 | 选股=最后%d只 | 刷新间隔=%d天 | 硬止损=%.0f%%" % (
        FACTOR_WINDOW, BOTTOM_N, REFRESH_INTERVAL, abs(HARD_STOP_PCT) * 100))


# ╔══════════════════════════════════════════════════════════════╗
# ║              主策略入口                                      ║
# ╚══════════════════════════════════════════════════════════════╝

def khHandlebar(data: Dict) -> List[Dict]:
    """主策略函数 — 每根 K 线执行一次"""
    g = _STATE
    signals = []
    stocks = khGet(data, "stocks")
    dn = khGet(data, "date_num")

    # ── Bar 计数 ──
    g['bar_counter'] += 1
    bar = g['bar_counter']

    # ── 同步持仓（框架 → 本地状态） ──
    _sync_positions_from_framework(data)

    # ── 日期日志 ──
    date_str = khGet(data, "date")
    logging.debug("=" * 50)
    logging.debug("[%s] bar=%d 持仓=%d只 资产=%.0f万 现金=%.0f万" % (
        date_str, bar, len(g['my_positions']),
        khGet(data, "total_asset") / 10000,
        khGet(data, "cash") / 10000))

    # ── 获取历史数据（close + amount） ──
    all_symbols = list(stocks)
    try:
        hist = khHistory(all_symbols, ["close", "amount"],
                         g['need_bars'], "1d", dn, fq="pre")
    except Exception as e:
        logging.error("[Alpha144-逆向持有] 获取历史数据失败: %s" % str(e))
        return signals

    # ═══════════════════════════════════════════════════════════
    # 1. 处理跌停无法卖出的 pending 单
    # ═══════════════════════════════════════════════════════════
    _process_pending_sells(data, hist, signals)

    # ═══════════════════════════════════════════════════════════
    # 2. 止损检查 — 每根 bar 都检查
    # ═══════════════════════════════════════════════════════════
    _check_stop_loss(data, hist, signals)

    # ═══════════════════════════════════════════════════════════
    # 3. 因子排名刷新 — 每 REFRESH_INTERVAL 天
    # ═══════════════════════════════════════════════════════════
    if bar >= g['next_refresh_bar']:
        g['rankings'] = _compute_factor_rankings_asc(stocks, hist, dn)
        # 取最后 BOTTOM_N 只（因子值最小的）
        sorted_codes = sorted(g['rankings'].keys(), key=lambda c: g['rankings'][c])
        g['bottom_10'] = set(sorted_codes[:BOTTOM_N])
        g['next_refresh_bar'] = bar + REFRESH_INTERVAL
        logging.info("[Alpha144-逆向持有] 因子排名刷新完成 | 候选=%d只 | 选中最后%d只=%s | 下次刷新=bar %d" % (
            len(g['rankings']), BOTTOM_N,
            [c[:6] for c in sorted_codes[:BOTTOM_N]],
            g['next_refresh_bar']))

    # ═══════════════════════════════════════════════════════════
    # 4. 调仓 — 卖出不再属于 bottom_10 的持仓，买入新的 bottom_10
    # ═══════════════════════════════════════════════════════════
    if g['bottom_10']:
        # 4a. 卖出不在 bottom_10 中的持仓
        _sell_removed_stocks(data, signals)

        # 4b. 买入 bottom_10 中未持仓的股票
        _buy_new_stocks(data, signals)

    # ═══════════════════════════════════════════════════════════
    # 5. 乐观更新本地持仓状态
    # ═══════════════════════════════════════════════════════════
    _apply_signals_to_state(signals, g)

    # ═══════════════════════════════════════════════════════════
    # 6. 更新持仓天数
    # ═══════════════════════════════════════════════════════════
    for code in list(g['my_positions'].keys()):
        g['my_positions'][code]['bars_held'] += 1

    # ── 摘要日志 ──
    pos_codes = list(g['my_positions'].keys())
    if pos_codes:
        hold_days = [str(g['my_positions'][c]['bars_held']) for c in pos_codes]
        logging.debug("[摘要] 持仓=%d只 %s | 持有天数=%s | bottom10=%s" % (
            len(pos_codes), [c[:6] for c in pos_codes], hold_days,
            [c[:6] for c in sorted(g['bottom_10'])]))
    else:
        logging.debug("[摘要] 空仓 | bottom10=%s" % [c[:6] for c in sorted(g['bottom_10'])])

    return signals


# ╔══════════════════════════════════════════════════════════════╗
# ║              Alpha#144 因子计算                               ║
# ╚══════════════════════════════════════════════════════════════╝

def _calc_alpha144(close_arr, amount_arr):
    """
    计算 Alpha#144 因子值。

    公式:
      alpha_144 = Σ(|ret_i| / amount_i)  for ret_i < 0, over last 20 periods

    含义:
      - ret_i: 当日涨跌幅（小数，如 0.02 = +2%）
      - amount_i: 当日成交额（元）
      - |ret_i| / amount_i: 每元成交额带来的价格变动 → "价格冲击成本"的代理变量
      - 只对下跌日求和 → 捕捉恐慌抛售时的流动性冲击
      - 因子值越大 → 下跌时流动性越差 → 恐慌抛售越严重

    逆向选择逻辑:
      - 因子值最小 → 下跌日流动性冲击最小 → 筹码稳定 → 适合长期持有
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

    if neg_count == 0:
        return 0.0

    return alpha


def _compute_factor_rankings_asc(stocks, hist, dn):
    """
    计算全股票池的 Alpha#144 因子，返回升序排名（最小的在前）。

    流程:
      1. 对每只股票计算 alpha_144
      2. 流动性过滤：近20日日均成交额 >= MIN_DAILY_AMOUNT
      3. 科创板过滤（可选）
      4. 返回 {code: factor_value, ...}

    注意: 与 _compute_factor_rankings 不同，这里不截断 Top N，
          而是返回全部有效股票，让调用方取最后 N 只。
    """
    raw_scores = {}

    for code in stocks:
        # ── 科创板过滤 ──
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

        # 流动性过滤：近20日日均成交额
        try:
            recent_amt = np.array(amount_vals[-FACTOR_WINDOW:], dtype=float)
            avg_amount = np.mean(recent_amt)
            if avg_amount < MIN_DAILY_AMOUNT:
                continue
        except Exception:
            continue

        val = _calc_alpha144(close_vals, amount_vals)
        if val is not None:
            raw_scores[code] = val

    return raw_scores


# ╔══════════════════════════════════════════════════════════════╗
# ║              止损检查 — 硬止损 -10%                            ║
# ╚══════════════════════════════════════════════════════════════╝

def _check_stop_loss(data, hist, signals):
    """
    检查每只持仓的浮亏，触发 -10% 硬止损。

    止损逻辑:
      - 当前价 / 入场价 - 1 <= -10% → 无条件卖出
      - 价格统一使用 khPrice（与框架同源）
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

        # 硬止损: 浮亏 <= -10%
        if pnl_pct <= HARD_STOP_PCT:
            logging.info("  [止损触发] %s 浮亏 %.1f%% <= %.0f%% | entry=%.2f current=%.2f" % (
                code, pnl_pct * 100, HARD_STOP_PCT * 100, entry, px))
            to_sell.append((code, "止损%.0f%%(浮亏%.1f%%)" % (HARD_STOP_PCT * 100, pnl_pct * 100)))

    for code, reason in to_sell:
        _sell_position(data, code, hist, reason, signals)


# ╔══════════════════════════════════════════════════════════════╗
# ║              调仓逻辑                                        ║
# ╚══════════════════════════════════════════════════════════════╝

def _sell_removed_stocks(data, signals):
    """
    卖出不在 bottom_10 中的持仓。

    在每次排名刷新后，原先持有的股票如果不再属于 bottom_10，
    则卖出。只有当 bottom_10 非空时才执行。
    """
    g = _STATE
    if not g['bottom_10']:
        return

    for code in list(g['my_positions'].keys()):
        if code not in g['bottom_10']:
            logging.info("  [调仓卖出] %s 不再属于 bottom_10，卖出" % code[:6])
            _sell_position(data, code, {}, "调仓(不再属于bottom_10)", signals)


def _buy_new_stocks(data, signals):
    """
    买入 bottom_10 中未持仓的股票，等权分配。

    资金分配: 总资产 / BOTTOM_N，每只股票等权买入。
    """
    g = _STATE
    if not g['bottom_10']:
        return

    held = set(g['my_positions'].keys())
    to_buy = [c for c in g['bottom_10'] if c not in held]

    if not to_buy:
        return

    total_equity = khGet(data, "total_asset")
    if total_equity <= 0:
        return

    # 等权分配：总资产 / BOTTOM_N
    allocation_per_stock = total_equity / BOTTOM_N

    for code in to_buy:
        px = khPrice(data, code, "close")
        if px <= 0:
            logging.warning("  [买入跳过] %s 无法获取价格" % code[:6])
            continue

        # 计算股数（整百股），预留 0.2% 佣金缓冲
        shares = int(allocation_per_stock * 0.998 / px / 100) * 100
        if shares < 100:
            logging.warning("  [买入跳过] %s 资金不足: alloc=%.0f px=%.2f shares=%d" % (
                code[:6], allocation_per_stock, px, shares))
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
    """处理之前因跌停未能卖出的持仓，今日重试"""
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
    """
    卖出单只股票。价格统一使用 khPrice（与框架同源）。

    跌停约束:
      - 检查当日是否跌停（今日涨跌幅 <= -9.8%）
      - 如果跌停 → 不卖，加入 pending_sells 次日再试
    """
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

    # ── 跌停检查 ──
    if code in hist:
        df = hist[code]
        if df is not None and len(df) >= 2:
            try:
                arr_c = np.array(df['close'].values, dtype=float)
                if len(arr_c) >= 2 and arr_c[-2] > 0:
                    daily_ret = (arr_c[-1] - arr_c[-2]) / arr_c[-2]
                    if daily_ret <= LIMIT_DOWN_PCT:
                        logging.info("  [卖出延迟] %s 跌停 (跌幅%.1f%%), 延至次日" % (
                            code, daily_ret * 100))
                        if code not in g['pending_sells']:
                            g['pending_sells'].append(code)
                        return
            except Exception:
                pass

    # 生成卖出信号
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
# ║              辅助函数                                        ║
# ╚══════════════════════════════════════════════════════════════╝

def _get_price_from_hist(code, hist):
    """从历史数据中获取最新收盘价"""
    if code in hist:
        df = hist[code]
        if df is not None and len(df) > 0:
            try:
                return float(df['close'].values[-1])
            except Exception:
                pass
    return 0


def _sync_positions_from_framework(data):
    """同步框架持仓到本地 my_positions"""
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
            }
            logging.debug("[同步] 新持仓 %s vol=%d avg_price=%.2f" % (
                code, vol, pos.get("avg_price", 0)))

    for code in list(g['my_positions'].keys()):
        if code not in fw_positions or fw_positions[code].get("volume", 0) <= 0:
            del g['my_positions'][code]
            logging.debug("[同步] 移除持仓 %s" % code)


def _apply_signals_to_state(signals, g):
    """乐观更新本地持仓状态"""
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
            }
