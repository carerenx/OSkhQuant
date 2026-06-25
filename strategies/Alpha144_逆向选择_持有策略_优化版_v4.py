# coding: utf-8
"""
策略说明：
- 策略名称：Alpha144 逆向选择持有策略（V4 — MFE智能退出）
- 核心理念：
    1. Alpha#144 因子逆向选股（因子值最小→筹码最稳定）
    2. **MFE驱动的智能退出系统**（基于447笔交易的技术面归因分析）
       - MFE与终盈相关性 +0.872 —— 是最强预测指标
       - MFE<5% 持有>30天 → 仅1.6%最终盈利 → 提前清仓止损
       - MFE>20% → 移动止盈收紧至5%（减少平均-15%的回吐）
       - 连续水下>40天 → 胜率仅14.3% → 触发止损
    3. 其他保留V3功能：分段止损、波动率过滤、大盘两级防御、板块过滤

V4 改进（基于技术面归因分析）：
  核心数据：
    - MFE<5% 的 245 笔（55%交易）：胜率 1.6%，均亏 -9.92% → MFE疲劳退出
    - MFE>20% 的 51 笔：MFE 均值 +43.94%，终盈仅 +28.77%（回吐-15.17%）→ 收紧止盈
    - 水下>40天的 70 笔：胜率 14.3%，均亏 -9.05% → 水下时间止损
    - 仓位 6-8%：胜率 100%，均盈 +26.10% → 保持当前等权
    - 持有 20-50 天是唯一正收益区间 → 保护此区间

  对比 V3：
    - 新增 MFE疲劳退出（持有≥30天且MFE<5%→清仓）
    - 新增 分段移动止盈（MFE>20%→回撤5%；否则回撤10%）
    - 新增 水下时间止损（连续水下>40天→清仓）
    - 新增 持有≥100天+MFE<10%的僵尸仓清退
    - 保留 V3 的分段止损、浮亏确认、波动率过滤、大盘防御

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

BENCHMARK = '000905.SH'

# ── 因子参数 ──
FACTOR_WINDOW    = 20
BOTTOM_N         = 12
REFRESH_INTERVAL = 30

# ── 新仓保护 ──
PROTECTION_BARS  = 20
CONFIRMATION_BARS = 10

# ── MFE疲劳退出（V4 新增：基于MFE<5%的245笔仅1.6%胜率） ──
MFE_FATIGUE_BARS     = 30   # 持有超过此天数触发检查
MFE_FATIGUE_THRESHOLD = 0.05  # MFE < 5% 则清仓

# ── MFE僵尸仓清退（V4 新增：超长期持有+低MFE） ──
ZOMBIE_BARS          = 100   # 持有超此天数
ZOMBIE_MFE_THRESHOLD  = 0.10  # MFE < 10% 则清仓

# ── 水下时间止损（V4 新增：基于水下>40天仅14.3%胜率） ──
UNDERWATER_STOP_DAYS = 40    # 连续水下天数超过此值触发止损

# ── 分段止损（保留V3） ──
HARD_STOP_EARLY  = -0.25
HARD_STOP_LATE   = -0.15
STOP_TIGHTEN_BAR = 50

# ── 分段移动止盈（V4 改进：MFE>20%时收紧至5%回撤） ──
TRAILING_PROFIT_PCT    = 0.20  # 触发线：浮盈 >20%
TRAILING_DRAWDOWN_PCT  = 0.10  # 默认回撤线：10%
TRAILING_DRAWDOWN_TIGHT = 0.05 # MFE>20%时收紧回撤线：5%

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
    # V4: 新增 underwater_days, highest_price 已有
    'my_positions': {},  # {code: {entry_price, entry_bar, bars_held, highest_price, half_sold, underwater_days}}
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
    g['need_bars'] = max(FACTOR_WINDOW + 30, MA_MARKET + 10, PROTECTION_BARS + 10,
                         STOP_TIGHTEN_BAR + 10, UNDERWATER_STOP_DAYS + 10, ZOMBIE_BARS + 10)
    logging.info("[Alpha144-V4] 初始化 | 持仓=%d只 | MFE疲劳=%dd/MFE<%.0f%% | 水下止损=%dd | 分段止损<50d=%.0f%%/>=50d=%.0f%% | 止盈触发%.0f%%/回落%.0f%%(收紧%.0f%%) | 僵尸仓>%dd+MFE<%.0f%%" % (
        BOTTOM_N, MFE_FATIGUE_BARS, MFE_FATIGUE_THRESHOLD * 100,
        UNDERWATER_STOP_DAYS,
        abs(HARD_STOP_EARLY) * 100, abs(HARD_STOP_LATE) * 100,
        TRAILING_PROFIT_PCT * 100, TRAILING_DRAWDOWN_PCT * 100, TRAILING_DRAWDOWN_TIGHT * 100,
        ZOMBIE_BARS, ZOMBIE_MFE_THRESHOLD * 100))


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

    # ── 获取历史数据 ──
    all_symbols = list(stocks)
    try:
        hist = khHistory(all_symbols, ["close", "amount"],
                         g['need_bars'], "1d", dn, fq="pre")
    except Exception as e:
        logging.error("[Alpha144-V4] 获取历史数据失败: %s" % str(e))
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
    # 3. 浮亏确认减半仓（保留V3）
    # ═══════════════════════════════════════════════════════════
    _check_confirmation(data, hist, signals)

    # ═══════════════════════════════════════════════════════════
    # 4. MFE疲劳退出（V4 核心新增）
    #    + 水下时间止损（V4 新增）
    #    + 分段止损（保留V3）
    #    + 移动止盈（V4 改进：分段回撤）
    # ═══════════════════════════════════════════════════════════
    _check_mfe_fatigue(data, hist, signals)     # V4: MFE<5%+持有>30天→清仓
    _check_underwater_stop(data, hist, signals)  # V4: 水下>40天→清仓
    _check_zombie(data, hist, signals)           # V4: 僵尸仓>100天+MFE<10%→清仓
    _check_stop_loss(data, hist, signals)        # V3: 分段止损
    _check_trailing_stop_v4(data, hist, signals) # V4: 分段移动止盈

    # ═══════════════════════════════════════════════════════════
    # 5. 因子排名刷新
    # ═══════════════════════════════════════════════════════════
    if bar >= g['next_refresh_bar']:
        g['rankings'] = _compute_factor_rankings_asc(stocks, hist, dn)
        sorted_codes = sorted(g['rankings'].keys(), key=lambda c: g['rankings'][c])
        g['bottom_n'] = set(sorted_codes[:BOTTOM_N])
        g['next_refresh_bar'] = bar + REFRESH_INTERVAL
        logging.info("[Alpha144-V4] 排名刷新 | 候选=%d只 | 选中%d只 | 下次=bar %d" % (
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
        logging.debug("[Alpha144-V4] 软防御：暂停买入/调仓")

    # ═══════════════════════════════════════════════════════════
    # 8. 更新状态（V4: 增加水下天数追踪）
    # ═══════════════════════════════════════════════════════════
    _apply_signals_to_state(signals, g)

    for code in list(g['my_positions'].keys()):
        pos = g['my_positions'][code]
        pos['bars_held'] += 1
        px = khPrice(data, code, "close")
        if px <= 0:
            continue
        # MFE 追踪
        if px > pos.get('highest_price', 0):
            pos['highest_price'] = px
        # 水下天数追踪（V4 新增）
        entry = pos.get('entry_price', 0)
        if entry > 0 and px < entry:
            pos['underwater_days'] = pos.get('underwater_days', 0) + 1
        else:
            pos['underwater_days'] = 0  # 回到水上则重置

    return signals


# ╔══════════════════════════════════════════════════════════════╗
# ║              Alpha#144 因子计算（不变）                       ║
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
    if len(close_vals) < 21:
        return 999
    arr = np.array(close_vals[-21:], dtype=float)
    rets = np.diff(arr) / arr[:-1]
    return np.std(rets) * np.sqrt(252)


def _compute_factor_rankings_asc(stocks, hist, dn):
    raw_scores = {}
    for code in stocks:
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
        try:
            recent_amt = np.array(amount_vals[-FACTOR_WINDOW:], dtype=float)
            if np.mean(recent_amt) < MIN_DAILY_AMOUNT:
                continue
        except Exception:
            continue
        ann_vol = _calc_annual_vol(close_vals)
        if ann_vol > MAX_ANNUAL_VOL:
            continue
        val = _calc_alpha144(close_vals, amount_vals)
        if val is not None:
            raw_scores[code] = val
    return raw_scores


# ╔══════════════════════════════════════════════════════════════╗
# ║              MFE疲劳退出（V4 核心新增）                        ║
# ╚══════════════════════════════════════════════════════════════╝

def _check_mfe_fatigue(data, hist, signals):
    """
    MFE疲劳退出：持有 >30天 且 MFE <5% → 清仓。

    依据：MFE<5%的245笔（占总交易55%），胜率仅 **1.6%**，均亏 -9.92%。
         这是策略最大的失血源。MFE 与终盈相关性 +0.872，是最强预测指标。
         持有超30天仍未浮盈超过5%的持仓，最终几乎不可能盈利。
    """
    g = _STATE
    to_sell = []

    for code, pos in g['my_positions'].items():
        bars = pos.get('bars_held', 0)
        if bars < MFE_FATIGUE_BARS:
            continue

        entry = pos.get('entry_price', 0)
        if entry <= 0:
            continue

        highest = pos.get('highest_price', entry)
        mfe = (highest / entry - 1.0)

        if mfe < MFE_FATIGUE_THRESHOLD:
            px = khPrice(data, code, "close")
            if px <= 0:
                px = _get_price_from_hist(code, hist)
                if px <= 0:
                    continue
            pnl_pct = (px / entry - 1.0) * 100
            logging.info("  [MFE疲劳] %s 持有%d天 MFE仅%.1f%% < %.0f%% → 清仓 | entry=%.2f now=%.2f pnl=%.1f%%" % (
                code[:6], bars, mfe * 100, MFE_FATIGUE_THRESHOLD * 100,
                entry, px, pnl_pct))
            to_sell.append((code, "MFE疲劳(持有%dd+MFE%.1f%%<%.0f%%)" % (
                bars, mfe * 100, MFE_FATIGUE_THRESHOLD * 100)))

    for code, reason in to_sell:
        _sell_position(data, code, hist, reason, signals)


# ╔══════════════════════════════════════════════════════════════╗
# ║              水下时间止损（V4 新增）                           ║
# ╚══════════════════════════════════════════════════════════════╝

def _check_underwater_stop(data, hist, signals):
    """
    水下时间止损：连续水下天数 >40天 → 清仓。

    依据：水下>40天的70笔，胜率仅 **14.3%**，均亏 -9.05%。
         与此对比，水下<10天的138笔胜率 40.6%，均盈 +6.61%。
         长时间的持续浮亏不会自行反转。
    """
    g = _STATE
    to_sell = []

    for code, pos in g['my_positions'].items():
        uw_days = pos.get('underwater_days', 0)
        if uw_days < UNDERWATER_STOP_DAYS:
            continue

        entry = pos.get('entry_price', 0)
        if entry <= 0:
            continue

        px = khPrice(data, code, "close")
        if px <= 0:
            px = _get_price_from_hist(code, hist)
            if px <= 0:
                continue
        pnl_pct = (px / entry - 1.0) * 100

        logging.info("  [水下止损] %s 连续水下%d天 >= %d天 → 清仓 | entry=%.2f now=%.2f pnl=%.1f%%" % (
            code[:6], uw_days, UNDERWATER_STOP_DAYS,
            entry, px, pnl_pct))
        to_sell.append((code, "水下止损(连续%dd>=%dd)" % (uw_days, UNDERWATER_STOP_DAYS)))

    for code, reason in to_sell:
        _sell_position(data, code, hist, reason, signals)


# ╔══════════════════════════════════════════════════════════════╗
# ║              僵尸仓清退（V4 新增）                              ║
# ╚══════════════════════════════════════════════════════════════╝

def _check_zombie(data, hist, signals):
    """
    僵尸仓清退：持有 >100天 且 MFE <10% → 清仓。

    依据：持有100+天的73笔，胜率仅 20.5%，均亏 -5.61%。
         超长期持仓 + 从未有像样反弹 = 被深度套牢的僵尸仓。
    """
    g = _STATE
    to_sell = []

    for code, pos in g['my_positions'].items():
        bars = pos.get('bars_held', 0)
        if bars < ZOMBIE_BARS:
            continue

        entry = pos.get('entry_price', 0)
        if entry <= 0:
            continue

        highest = pos.get('highest_price', entry)
        mfe = (highest / entry - 1.0)

        if mfe < ZOMBIE_MFE_THRESHOLD:
            px = khPrice(data, code, "close")
            if px <= 0:
                px = _get_price_from_hist(code, hist)
                if px <= 0:
                    continue
            pnl_pct = (px / entry - 1.0) * 100
            logging.info("  [僵尸仓] %s 持有%d天 MFE仅%.1f%% < %.0f%% → 清仓 | entry=%.2f now=%.2f pnl=%.1f%%" % (
                code[:6], bars, mfe * 100, ZOMBIE_MFE_THRESHOLD * 100,
                entry, px, pnl_pct))
            to_sell.append((code, "僵尸仓(持有%dd+MFE%.1f%%<%.0f%%)" % (
                bars, mfe * 100, ZOMBIE_MFE_THRESHOLD * 100)))

    for code, reason in to_sell:
        _sell_position(data, code, hist, reason, signals)


# ╔══════════════════════════════════════════════════════════════╗
# ║              浮亏确认（保留V3）                                 ║
# ╚══════════════════════════════════════════════════════════════╝

def _check_confirmation(data, hist, signals):
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
    g = _STATE
    if code not in g['my_positions']:
        return
    pos = g['my_positions'][code]
    px = khPrice(data, code, "close")
    if px <= 0:
        px = _get_price_from_hist(code, hist)
        if px <= 0:
            return
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
# ║              分段止损（保留V3）                                 ║
# ╚══════════════════════════════════════════════════════════════╝

def _check_stop_loss(data, hist, signals):
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
# ║              分段移动止盈（V4 改进）                           ║
# ╚══════════════════════════════════════════════════════════════╝

def _check_trailing_stop_v4(data, hist, signals):
    """
    分段移动止盈（V4改进）：
      - MFE > 20% 且回撤 > 10% → 触发（默认线）
      - MFE > 20% 且回撤 > 5%  → 触发（收紧线，用于MFE曾经很高的持仓）

    依据：MFE>20%的51笔，MFE均值 +43.94%，但终盈仅 +28.77%（回吐 -15.17%）。
         收紧回撤可以从当前 -15% 回吐中锁定更多利润。
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

        mfe = (highest / entry - 1.0)
        if mfe < TRAILING_PROFIT_PCT:
            continue  # 未触发止盈线

        drawdown = (px / highest - 1.0)

        # 分段回撤：MFE曾经很高的用紧线
        if mfe > 0.30:
            trigger = -TRAILING_DRAWDOWN_TIGHT  # 5%
            line_name = "5%%紧线"
        else:
            trigger = -TRAILING_DRAWDOWN_PCT    # 10%
            line_name = "10%%标准线"

        if drawdown <= trigger:
            logging.info("  [移动止盈-V4] %s 从高点 %.2f 回落 %.1f%% (MFE=%.1f%% %s) | entry=%.2f now=%.2f hold=%dd" % (
                code, highest, drawdown * 100, mfe * 100, line_name,
                entry, px, pos.get('bars_held', 0)))
            to_sell.append((code, "移动止盈(高点%.2f回落%.1f%% MFE%.1f%% %s)" % (
                highest, drawdown * 100, mfe * 100, line_name)))

    for code, reason in to_sell:
        _sell_position(data, code, hist, reason, signals)


# ╔══════════════════════════════════════════════════════════════╗
# ║              调仓逻辑（保留V3）                                 ║
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
    chuang_ye_count = sum(1 for c in held if c.startswith('300'))
    chuang_ye_pending = sum(1 for c in pending_buys if c.startswith('300'))

    to_buy = []
    for c in g['bottom_n']:
        if c in held or c in pending_buys:
            continue
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
        reason = "%s Alpha144V4 entry=%.2f val=%.2e" % (code[:6], px, factor_val)
        sigs = generate_signal(data, code, px, shares, 'buy', reason)
        if sigs:
            signals.extend(sigs)
            bought += 1
            logging.info(">>> [买入] %s × %d股 @ %.2f | 金额=%.0f | alpha144=%.2e" % (
                code, shares, px, shares * px, factor_val))


# ╔══════════════════════════════════════════════════════════════╗
# ║              卖出执行（保留V3）                                 ║
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
# ║              大盘防御（保留V3）                                 ║
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
                BENCHMARK, current, MA_MARKET, (1 - MARKET_FILTER_HARD_PCT) * 100, hard))
            return 'hard_defense'
        if current < soft:
            logging.info("[市场] %s=%.2f < MA%d×%.0f%%=%.2f → 软防御" % (
                BENCHMARK, current, MA_MARKET, (1 - MARKET_FILTER_PCT) * 100, soft))
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
                'underwater_days': 0,  # V4 新增
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
                'underwater_days': 0,  # V4 新增
            }
