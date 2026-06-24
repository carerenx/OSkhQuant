# coding: utf-8
"""
策略说明：
- 策略名称：Alpha144 流动性冲击择时策略（优化版）
- 核心理念：
    1. Alpha#144 因子 = sum(|ret|/amount, ret<0, 20)
       捕捉下跌日的流动性冲击：放量下跌 → 恐慌抛售筹码被吸收 → 后续反弹
       因子值越大 → 下跌日单位成交额的价格冲击越大 → 择时买入信号
    2. 突破确认 — Close > 5日最高价 + Close > MA20 趋势过滤 → 触发入场
    3. 持有到期 — 固定持有 15 天，硬止损 -10%，移动止盈 +10% 后回撤 5%
    4. 大盘过滤 — 基准指数 < MA20 时空仓防御（严格模式）
    5. 行业分散 — 同行业最多持有 2 只（"其他"行业最多 3 只），最多 4 只持仓

优化要点（vs 原版）:
  - 止损收紧: -18% → -10%（回撤主因：单只-18%×5只=-90%）
  - 持有期缩短: 20天 → 15天（减少慢速出血）
  - 新增移动止盈: +10%后回撤5%即止盈（锁定利润）
  - 增加趋势过滤: 突破还须Close>MA20（过滤下跌趋势假突破）
  - 成交量确认升级: 80%→120%均量（更强的放量确认）
  - 仓位降低: 5→4只（降低集中度风险）
  - 市场过滤严格化: 基准<MA20直接空仓（去掉3%容忍度）
- 指标来源：原始因子公式，使用 numpy 计算
- 适用场景：中证500 中小盘股，依赖微观结构因子
"""
from khQuantImport import *  # 导入统一工具与指标

# ╔══════════════════════════════════════════════════════════════╗
# ║              用户可调参数                                    ║
# ╚══════════════════════════════════════════════════════════════╝

# ── 基准 ──
BENCHMARK = '000300.SH'       # 基准指数，默认沪深300（与框架配置一致），可改为 000905.SH（中证500）

# ── 因子参数 ──
FACTOR_WINDOW   = 20          # 因子计算窗口（交易日）
FACTOR_TOP_PCT   = 0.15       # 选股比例: Top 15%
REFRESH_INTERVAL = 10         # 选股刷新间隔（交易日）

# ── 入场参数 ──
BREAKOUT_PERIOD = 5           # 突破周期: 5日最高价

# ── 出场参数 ──
MAX_HOLD_BARS = 15            # 最大持有天数（20→15，减少慢速出血）
HARD_STOP_PCT  = -0.10        # 硬止损: 浮亏超过 10% 无条件平仓（-18%→-10%）

# ── 移动止盈参数 ──
TRAILING_PROFIT_PCT = 0.10    # 浮盈超过 10% 启动移动止盈
TRAILING_DRAWDOWN_PCT = 0.05  # 从峰值回撤 5% 即止盈

# ── 大盘过滤 ──
MA_MARKET = 20                # 市场均线周期
MARKET_FILTER_PCT = 0.00      # 大盘低于MA20的容忍度（0%→严格模式：低于MA20即空仓）

# ── 仓位管理 ──
MAX_POSITIONS = 4             # 最多持仓数（5→4，降低集中度）
MAX_SECTOR_COUNT = 2          # 同行业最多持有数（已分类行业）
MAX_SECTOR_OTHER  = 2         # "其他"行业最多持有数（3→2，与已分类一致）

# ── 入场质量过滤 ──
TREND_FILTER_ENABLED = True   # 趋势过滤开关：买入时要求 Close > MA20
VOLUME_SURGE_RATIO = 1.2      # 成交量放大倍数（0.8→1.2，要求放量突破）

# ── 涨跌停约束 ──
LIMIT_UP_PCT   = 0.098        # 涨停阈值 (9.8% 留余量)
LIMIT_DOWN_PCT = -0.098       # 跌停阈值

# ── 数据要求 ──
MIN_HISTORY_BARS = 130        # 最少历史K线（保证因子+均线全可用）
MIN_DAILY_AMOUNT = 3e7        # 最低日均成交额 (3000万, 过滤流动性极差股)

# ── 行业分类（基于申万行业，未分类→"其他"） ──
# 用户可自行扩展此映射表
SECTOR_MAP = {
    # 交运 (6只)
    '000429.SZ': '交运', '600004.SH': '交运', '600350.SH': '交运', '600377.SH': '交运',
    '601156.SH': '交运', '603565.SH': '交运',
    # 传媒 (3只)
    '601019.SH': '传媒', '601098.SH': '传媒', '601928.SH': '传媒',
    # 军工 (3只)
    '002025.SZ': '军工', '600316.SH': '军工', '603885.SH': '军工',
    # 化工 (6只)
    '000683.SZ': '化工', '000830.SZ': '化工', '002064.SZ': '化工', '600486.SH': '化工',
    '601118.SH': '化工', '603049.SH': '化工',
    # 医药 (33只)
    '000739.SZ': '医药', '002007.SZ': '医药', '002223.SZ': '医药', '002262.SZ': '医药',
    '002432.SZ': '医药', '002603.SZ': '医药', '002773.SZ': '医药', '300003.SZ': '医药',
    '300142.SZ': '医药', '300558.SZ': '医药', '300677.SZ': '医药', '300888.SZ': '医药',
    '301301.SZ': '医药', '600161.SH': '医药', '600511.SH': '医药', '600521.SH': '医药',
    '600566.SH': '医药', '600763.SH': '医药', '600873.SH': '医药', '603077.SH': '医药',
    '603087.SH': '医药', '603658.SH': '医药', '603858.SH': '医药', '603939.SH': '医药',
    '688065.SH': '医药', '688166.SH': '医药', '688180.SH': '医药', '688192.SH': '医药',
    '688266.SH': '医药', '688278.SH': '医药', '688331.SH': '医药', '688363.SH': '医药',
    '688617.SH': '医药',
    # 家电 (5只)
    '000921.SZ': '家电', '002508.SZ': '家电', '603728.SH': '家电', '603816.SH': '家电',
    '603833.SH': '家电',
    # 建材 (2只)
    '000786.SZ': '建材', '600801.SH': '建材',
    # 新能源 (2只)
    '600995.SH': '新能源', '601016.SH': '新能源',
    # 有色 (11只)
    '000737.SZ': '有色', '000831.SZ': '有色', '000878.SZ': '有色', '001203.SZ': '有色',
    '002155.SZ': '有色', '002738.SZ': '有色', '600390.SH': '有色', '600711.SH': '有色',
    '600985.SH': '有色', '600988.SH': '有色', '601212.SH': '有色',
    # 机械 (1只)
    '600499.SH': '机械',
    # 汽车 (1只)
    '600166.SH': '汽车',
    # 消费 (1只)
    '300012.SZ': '消费',
    # 环保 (2只)
    '600008.SH': '环保', '603568.SH': '环保',
    # 电力 (5只)
    '000537.SZ': '电力', '000539.SZ': '电力', '600021.SH': '电力', '600578.SH': '电力',
    '601991.SH': '电力',
    # 电子 (14只)
    '002138.SZ': '电子', '002273.SZ': '电子', '003031.SZ': '电子', '300346.SZ': '电子',
    '300567.SZ': '电子', '300666.SZ': '电子', '600363.SH': '电子', '600563.SH': '电子',
    '600699.SH': '电子', '600879.SH': '电子', '603175.SH': '电子', '688188.SH': '电子',
    '688375.SH': '电子', '688538.SH': '电子',
    # 能源 (12只)
    '000027.SZ': '能源', '000703.SZ': '能源', '000723.SZ': '能源', '000883.SZ': '能源',
    '000937.SZ': '能源', '001286.SZ': '能源', '003035.SZ': '能源', '600157.SH': '能源',
    '600256.SH': '能源', '600688.SH': '能源', '600871.SH': '能源', '601139.SH': '能源',
    # 计算机 (9只)
    '002065.SZ': '计算机', '002153.SZ': '计算机', '002261.SZ': '计算机', '002335.SZ': '计算机',
    '300339.SZ': '计算机', '300857.SZ': '计算机', '600536.SH': '计算机', '688615.SH': '计算机',
    '688692.SH': '计算机',
    # 通信 (6只)
    '002465.SZ': '通信', '002517.SZ': '通信', '300136.SZ': '通信', '600498.SH': '通信',
    '688475.SH': '通信', '688702.SH': '通信',
    # 金融 (25只)
    '000728.SZ': '金融', '000750.SZ': '金融', '000783.SZ': '金融', '002500.SZ': '金融',
    '002670.SZ': '金融', '002673.SZ': '金融', '002926.SZ': '金融', '002939.SZ': '金融',
    '002945.SZ': '金融', '002966.SZ': '金融', '600109.SH': '金融', '600369.SH': '金融',
    '600906.SH': '金融', '600909.SH': '金融', '601108.SH': '金融', '601128.SH': '金融',
    '601162.SH': '金融', '601198.SH': '金融', '601236.SH': '金融', '601555.SH': '金融',
    '601577.SH': '金融', '601665.SH': '金融', '601696.SH': '金融', '601990.SH': '金融',
    '601997.SH': '金融',
    # 钢铁 (9只)
    '000709.SZ': '钢铁', '000825.SZ': '钢铁', '000898.SZ': '钢铁', '000932.SZ': '钢铁',
    '000959.SZ': '钢铁', '600126.SH': '钢铁', '600282.SH': '钢铁', '600808.SH': '钢铁',
    '688425.SH': '钢铁',
    # 食品 (5只)
    '000729.SZ': '食品', '002461.SZ': '食品', '600132.SH': '食品', '600754.SH': '食品',
    '603345.SH': '食品',
}


# ╔══════════════════════════════════════════════════════════════╗
# ║              模块级全局状态（跨 Bar 持久化）                  ║
# ╚══════════════════════════════════════════════════════════════╝

# 使用模块级变量在 khHandlebar 调用之间持久化状态
# 框架在回测期间保持策略模块不重新加载，因此模块变量可以跨 bar 保留

_STATE = {
    'rankings': {},              # {code: factor_value}  最近一次因子排名
    'next_refresh_bar': 0,       # 下次刷新排名的 bar 计数
    'bar_counter': 0,            # 自增 bar 计数
    'pending_sells': [],         # 跌停无法卖出、待重试的 code 列表
    'my_positions': {},          # {code: {entry_price, entry_bar, bars_held, peak_price}}
    'need_bars': 0,              # 缓存：需要的 K 线数量
}


def init(stocks=None, data=None):
    """策略初始化 — 重置全局状态 + 预热数据缓存"""
    _STATE['rankings'] = {}
    _STATE['next_refresh_bar'] = 0
    _STATE['bar_counter'] = 0
    _STATE['pending_sells'] = []
    _STATE['my_positions'] = {}
    _STATE['need_bars'] = max(FACTOR_WINDOW + 30, MA_MARKET + 10, BREAKOUT_PERIOD + 10)
    logging.info("[Alpha144-优化版] 初始化完成 | 刷新间隔=%d天 | 最大持仓=%d只 | 持有期=%d天 | 硬止损=%.0f%% | 移动止盈=+%.0f%%/-%.0f%%" % (
        REFRESH_INTERVAL, MAX_POSITIONS, MAX_HOLD_BARS, abs(HARD_STOP_PCT) * 100,
        TRAILING_PROFIT_PCT * 100, TRAILING_DRAWDOWN_PCT * 100))

    # ── 预热数据：下载基准全区间历史（后续 bar 内不再需要 force_download） ──
    try:
        from xtquant import xtdata
        start_dn = khGet(data, "date_num") if data else None
        xtdata.download_history_data(
            stock_code=BENCHMARK, period="1d",
            start_time=start_dn or '', end_time='')
        logging.info("[Alpha144] 基准指数数据预热完成: %s" % BENCHMARK)
    except Exception:
        pass  # 预热失败不阻断，单个 bar 会以 fallback 模式获取


def khHandlebar(data: Dict) -> List[Dict]:
    """主策略函数 — 每根 K 线执行一次

    性能优化要点:
      1. 因子排名仅每 REFRESH_INTERVAL 天执行一次，且仅那几天需要长窗口+amount
      2. 非刷新日只拉 close、只需要 MA_MARKET+2 根 bar
      3. 基准数据无 force_download（首次 init 已预热，之后用本地缓存）
    """
    g = _STATE
    signals = []
    stocks = khGet(data, "stocks")
    dn = khGet(data, "date_num")

    # ── Bar 计数 ──
    g['bar_counter'] += 1
    bar = g['bar_counter']

    # ── 数据不足时跳过 ──
    if bar < MIN_HISTORY_BARS:
        return signals

    # ── 同步持仓（框架 → 本地状态） ──
    _sync_positions_from_framework(data)

    # ── 日期日志 ──
    date_str = khGet(data, "date")
    logging.debug("=" * 50)
    logging.debug("[%s] bar=%d 持仓=%d只 资产=%.0f万 现金=%.0f万" % (
        date_str, bar, len(g['my_positions']),
        khGet(data, "total_asset") / 10000,
        khGet(data, "cash") / 10000))

    # ── 判断是否需要因子刷新 ──
    is_refresh_bar = (bar >= g['next_refresh_bar'])

    # ═══════════════════════════════════════════════════════════
    # 获取历史数据（按需分档 + 分批，避免 BSON 1MB 限制）
    # ═══════════════════════════════════════════════════════════
    all_symbols = list(stocks)
    BATCH_SIZE = 50
    hist = {}
    try:
        if is_refresh_bar:
            bars_needed = g['need_bars']
            fields = ["close", "amount"]
        else:
            bars_needed = MA_MARKET + BREAKOUT_PERIOD + 5
            fields = ["close"]
        for i in range(0, len(all_symbols), BATCH_SIZE):
            batch = all_symbols[i:i + BATCH_SIZE]
            batch_hist = khHistory(batch, fields,
                                   bars_needed, "1d", dn, fq="pre")
            if batch_hist:
                hist.update(batch_hist)
    except Exception as e:
        logging.error("[Alpha144] 获取历史数据失败: %s" % str(e))
        return signals

    # ── 获取基准指数数据（无 force_download，用本地缓存） ──
    bm_hist = {}
    try:
        bm_hist = khHistory([BENCHMARK], ["close"],
                            MA_MARKET + 5, "1d", dn, fq="pre")
    except Exception:
        pass  # 基准数据获取失败 → 默认可交易

    # ═══════════════════════════════════════════════════════════
    # 1. 大盘过滤
    # ═══════════════════════════════════════════════════════════
    market_ok = _check_market(bm_hist)

    # ═══════════════════════════════════════════════════════════
    # 2. 处理跌停无法卖出的 pending 单
    # ═══════════════════════════════════════════════════════════
    _process_pending_sells(data, hist, signals)

    # ═══════════════════════════════════════════════════════════
    # 3. 检查持仓出场（持有到期 / 硬止损）
    # ═══════════════════════════════════════════════════════════
    _check_exits(data, hist, signals)

    # ═══════════════════════════════════════════════════════════
    # 4. 因子排名刷新（每 10 天）
    # ═══════════════════════════════════════════════════════════
    if bar >= g['next_refresh_bar']:
        g['rankings'] = _compute_factor_rankings(stocks, hist, dn)
        g['next_refresh_bar'] = bar + REFRESH_INTERVAL
        logging.info("[Alpha144] 因子排名刷新完成: %d 只有效股票 | 下次刷新=bar %d" % (
            len(g['rankings']), g['next_refresh_bar']))

    # ═══════════════════════════════════════════════════════════
    # 5. 入场检查（突破 5 日新高）
    # ═══════════════════════════════════════════════════════════
    if market_ok and len(g['my_positions']) < MAX_POSITIONS:
        _check_entry_breakout(data, hist, signals)

    # ═══════════════════════════════════════════════════════════
    # 6. 大盘防御 → 清仓
    # ═══════════════════════════════════════════════════════════
    if not market_ok and len(g['my_positions']) > 0:
        _liquidate_all(data, signals, "大盘防御清仓")

    # ═══════════════════════════════════════════════════════════
    # 7. 乐观更新本地持仓状态（模拟 QMT 即时成交）
    # ═══════════════════════════════════════════════════════════
    _apply_signals_to_state(signals, g)

    # ═══════════════════════════════════════════════════════════
    # 8. 更新持仓天数
    # ═══════════════════════════════════════════════════════════
    for code in list(g['my_positions'].keys()):
        g['my_positions'][code]['bars_held'] += 1

    # ── 摘要日志 ──
    pos_codes = list(g['my_positions'].keys())
    if pos_codes:
        hold_days = [str(g['my_positions'][c]['bars_held']) for c in pos_codes]
        logging.debug("[摘要] 持仓=%d只 %s | 持有天数=%s" % (
            len(pos_codes), pos_codes, hold_days))

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
      - 因子值越大 → 下跌时流动性越差 → 后续反弹潜力越大
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


def _compute_factor_rankings(stocks, hist, dn):
    """
    计算全股票池的 Alpha#144 因子排名。

    流程:
      1. 对每只股票计算 alpha_144
      2. 流动性过滤：近20日日均成交额 >= MIN_DAILY_AMOUNT
      3. 按因子值降序排列（越大越好）
      4. 取 Top 15%

    返回: {code: factor_value, ...}
    """
    raw_scores = {}

    for code in stocks:
        if code not in hist:
            continue

        df = hist[code]
        if df is None or len(df) < FACTOR_WINDOW + 1:
            continue

        # 提取 close 和 amount 序列
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

    if not raw_scores:
        return {}

    # 降序排列（因子值越大越好）
    sorted_codes = sorted(raw_scores.keys(), key=lambda c: raw_scores[c], reverse=True)

    # Top 15%
    top_n = max(1, int(len(sorted_codes) * FACTOR_TOP_PCT))

    rankings = {}
    for code in sorted_codes[:top_n]:
        rankings[code] = raw_scores[code]

    logging.info("[因子] 有效候选池=%d只 → Top15%%=%d只" % (len(raw_scores), len(rankings)))
    return rankings


# ╔══════════════════════════════════════════════════════════════╗
# ║              入场判断 — 突破 5 日新高                         ║
# ╚══════════════════════════════════════════════════════════════╝

def _check_entry_breakout(data, hist, signals):
    """
    检查 Top 15% 选股池中突破 5 日新高的股票，触发买入。

    条件:
      (1) 股票在最新因子排名的 Top 15% 中
      (2) 今日收盘 > 过去 5 日最高价（突破）
      (3) 趋势过滤: Close > MA20（过滤下跌趋势中的假突破）
      (4) 未涨停（今日涨幅 < 9.8%）
      (5) 未持仓
      (6) 持仓数 < MAX_POSITIONS
      (7) 成交量放量确认（今日成交额 > 5日均量的120%）
    """
    g = _STATE
    if not g['rankings']:
        return

    held = set(g['my_positions'].keys())
    slots_available = MAX_POSITIONS - len(g['my_positions'])
    if slots_available <= 0:
        return

    # 按因子值降序遍历排名池
    ranked_list = sorted(g['rankings'].keys(),
                         key=lambda c: g['rankings'][c], reverse=True)

    # 统计当前行业分布
    sector_counts = {}
    for code in held:
        sec = _get_sector(code)
        sector_counts[sec] = sector_counts.get(sec, 0) + 1

    candidates = []
    for code in ranked_list:
        if code in held:
            continue
        if code not in hist:
            continue

        df = hist[code]
        if df is None or len(df) < max(BREAKOUT_PERIOD + 2, MA_MARKET + 2):
            continue

        try:
            close_vals = df['close'].values
            amount_vals = df['amount'].values if 'amount' in df.columns else None
        except Exception:
            continue

        arr_c = np.array(close_vals, dtype=float)
        if len(arr_c) < max(BREAKOUT_PERIOD + 2, MA_MARKET + 2):
            continue

        current_close = arr_c[-1]
        prev_close = arr_c[-2] if len(arr_c) >= 2 else current_close

        # ── 涨跌停检查 ──
        if prev_close > 0:
            daily_ret = (current_close - prev_close) / prev_close
            if daily_ret >= LIMIT_UP_PCT:
                logging.debug("  [入场跳过] %s 涨停 (涨幅%.1f%%)" % (code, daily_ret * 100))
                continue
            # 当日跌幅超 5% 不买（卖压太强）
            if daily_ret <= -0.05:
                logging.debug("  [入场跳过] %s 当日跌幅%.1f%% > 5%%" % (code, daily_ret * 100))
                continue

        # ── 趋势过滤: Close > MA20 ──
        if TREND_FILTER_ENABLED:
            ma20 = np.mean(arr_c[-MA_MARKET:])
            if current_close <= ma20:
                logging.debug("  [入场跳过] %s close=%.2f <= MA20=%.2f" % (code, current_close, ma20))
                continue

        # ── 突破检查：今日收盘 > 过去5日最高价（不含今日） ──
        past_5_high = np.max(arr_c[-(BREAKOUT_PERIOD + 1):-1])
        if current_close <= past_5_high:
            continue

        # ── 成交量放量确认 ──
        vol_ok = True
        try:
            if amount_vals is not None and len(amount_vals) >= 6:
                arr_a = np.array(amount_vals, dtype=float)
                today_amt = arr_a[-1]
                avg_amt_5 = np.mean(arr_a[-6:-1])
                if avg_amt_5 > 0:
                    vol_ok = today_amt > avg_amt_5 * VOLUME_SURGE_RATIO
                    if not vol_ok:
                        logging.debug("  [入场跳过] %s 成交量不足 (今日%.0f vs 5日均%.0f)" % (
                            code, today_amt, avg_amt_5))
        except Exception:
            vol_ok = True

        # ── 行业限制 ──
        sec = _get_sector(code)
        sec_limit = MAX_SECTOR_OTHER if sec == '其他' else MAX_SECTOR_COUNT
        if sector_counts.get(sec, 0) >= sec_limit:
            logging.debug("  [入场跳过] %s 行业=%s 已满%d只" % (code, sec, sec_limit))
            continue

        factor_val = g['rankings'].get(code, 0)
        # 使用 khPrice 作为委托价，与框架 mark-to-market 口径一致
        order_price = khPrice(data, code, "close")
        if order_price <= 0:
            order_price = current_close  # fallback
        candidates.append((code, order_price, factor_val, sec))
        logging.info("  [信号] %s 突破5日新高! close=%.2f high5=%.2f alpha144=%.2e" % (
            code, current_close, past_5_high, factor_val))

        if len(candidates) >= slots_available:
            break

    # ── 等权买入 ──
    if candidates:
        _buy_candidates(data, candidates, signals)


def _buy_candidates(data, candidates, signals):
    """
    对突破候选现金等权买入。

    资金分配: 可用现金 / 剩余空位 = 每只股票分配金额（不再依赖总资产）
    """
    available_cash = khGet(data, "cash")
    current_positions_count = len(_STATE['my_positions'])

    if available_cash <= 0:
        return

    # 已通过 process_signals 买入的数量（同一bar内的买入信号）
    pending_buys = len([s for s in signals if s.get('action') == 'buy'])
    slots_remaining = MAX_POSITIONS - current_positions_count - pending_buys

    if slots_remaining <= 0:
        return

    # 现金等权：剩余现金 / 剩余空位
    allocation_per_stock = available_cash / slots_remaining

    sector_counts = {}
    for code in _STATE['my_positions'].keys():
        sec = _get_sector(code)
        sector_counts[sec] = sector_counts.get(sec, 0) + 1

    bought = 0
    for code, price, factor_val, sec in candidates:
        if code in _STATE['my_positions']:
            continue
        if bought >= slots_remaining:
            break

        # 行业限制
        sec = _get_sector(code)
        sec_limit = MAX_SECTOR_OTHER if sec == '其他' else MAX_SECTOR_COUNT
        if sector_counts.get(sec, 0) >= sec_limit:
            logging.debug("  [买入跳过] %s 行业=%s 已满%d只" % (code, sec, sec_limit))
            continue

        # 计算股数（整百股），预留 0.2% 佣金余量
        shares = int(allocation_per_stock * 0.998 / price / 100) * 100
        if shares < 100:
            continue

        # 生成买入信号
        reason = "%s Alpha144突破 entry=%.2f val=%.2e" % (code[:6], price, factor_val)
        sigs = generate_signal(data, code, price, shares, 'buy', reason)
        if sigs:
            signals.extend(sigs)
            sector_counts[sec] = sector_counts.get(sec, 0) + 1
            bought += 1
            logging.info(">>> [买入] %s × %d股 @ %.2f | 金额=%.0f | alpha144=%.2e" % (
                code, shares, price, shares * price, factor_val))
        else:
            logging.warning("  [买入失败] %s 生成信号失败" % code)


# ╔══════════════════════════════════════════════════════════════╗
# ║              出场判断 — 持有到期 + 硬止损                     ║
# ╚══════════════════════════════════════════════════════════════╝

def _check_exits(data, hist, signals):
    """
    检查出场条件（按优先级）:
      (1) 硬止损: 浮亏超过 10% 无条件平仓
      (2) 移动止盈: 浮盈超 10% 后从峰值回撤 5% 即止盈
      (3) 持有到期: max_hold = 15 天

    价格统一使用 khPrice（与框架同源），保证 P&L 计算一致
    """
    g = _STATE
    to_sell = []

    for code, pos in g['my_positions'].items():
        # 获取当前价格（框架口径，与 mark-to-market 一致）
        px = khPrice(data, code, "close")
        if px <= 0:
            # fallback: 尝试从 hist 获取
            px = _get_price_from_hist(code, hist)
            if px <= 0:
                continue

        entry = pos['entry_price']
        if entry <= 0:
            continue

        pnl_pct = (px / entry - 1.0)

        # ── 更新峰值价格 ──
        peak = pos.get('peak_price', entry)
        if px > peak:
            pos['peak_price'] = px

        # ── 计算从峰值的回撤 ──
        peak_pnl = (peak / entry - 1.0) if entry > 0 else 0
        from_peak = (px / peak - 1.0) if peak > 0 else 0

        # (1) 硬止损: 浮亏 >= 10%
        if pnl_pct <= HARD_STOP_PCT:
            logging.info("  [止损触发] %s 浮亏 %.1f%% <= %.0f%%" % (
                code, pnl_pct * 100, HARD_STOP_PCT * 100))
            to_sell.append((code, "硬止损%.0f%%(浮亏%.1f%%)" % (HARD_STOP_PCT * 100, pnl_pct * 100)))
            continue

        # (2) 移动止盈: 浮盈曾超 10% 且从峰值回撤超 5%
        if peak_pnl >= TRAILING_PROFIT_PCT and from_peak <= -TRAILING_DRAWDOWN_PCT:
            logging.info("  [止盈触发] %s 峰值+%.1f%% → 当前+%.1f%% (回撤%.1f%%)" % (
                code, peak_pnl * 100, pnl_pct * 100, abs(from_peak) * 100))
            to_sell.append((code, "移动止盈(峰值+%.1f%%→当前+%.1f%%)" % (peak_pnl * 100, pnl_pct * 100)))
            continue

        # (3) 持有到期
        if pos['bars_held'] >= MAX_HOLD_BARS:
            to_sell.append((code, "持有%d天到期" % MAX_HOLD_BARS))

    for code, reason in to_sell:
        _sell_position(data, code, hist, reason, signals)


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
    卖出单只股票。

    价格统一使用 khPrice（与框架同源），保证 P&L 计算一致

    跌停约束:
      - 检查当日是否跌停（今日涨跌幅 <= -9.8%）
      - 如果跌停 → 不卖，加入 pending_sells 次日再试
    """
    g = _STATE
    if code not in g['my_positions']:
        return

    pos = g['my_positions'][code]

    # 获取当前价格（框架口径）
    px = khPrice(data, code, "close")
    if px <= 0:
        px = _get_price_from_hist(code, hist)
        if px <= 0:
            logging.warning("  [卖出跳过] %s 无法获取价格" % code)
            return

    # ── 跌停检查 ──
    px_prev = khPrice(data, code, "prev_close")
    if px_prev <= 0 and code in hist:
        df = hist[code]
        if df is not None and len(df) >= 2:
            try:
                arr_c = np.array(df['close'].values, dtype=float)
                if len(arr_c) >= 2:
                    px_prev = arr_c[-2]
            except Exception:
                pass

    if px_prev > 0:
        daily_ret = (px - px_prev) / px_prev
        if daily_ret <= LIMIT_DOWN_PCT:
            logging.info("  [卖出延迟] %s 跌停 (跌幅%.1f%%), 延至次日" % (
                code, daily_ret * 100))
            if code not in g['pending_sells']:
                g['pending_sells'].append(code)
            return

    if px <= 0:
        logging.warning("  [卖出跳过] %s 无法获取价格" % code)
        return

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
        logging.warning("  [卖出失败] %s 生成信号失败" % code)


# ╔══════════════════════════════════════════════════════════════╗
# ║              大盘防御                                        ║
# ╚══════════════════════════════════════════════════════════════╝

def _liquidate_all(data, signals, reason):
    """清空所有持仓（大盘触发防御）"""
    g = _STATE
    for code in list(g['my_positions'].keys()):
        # 构建一个空 hist 用于跌停检查，这里传递 None 表示不检查跌停
        px = khPrice(data, code, "close")
        if px <= 0:
            continue
        if code not in g['my_positions']:
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

def _check_market(bm_hist):
    """
    大盘过滤器: 基准指数 >= MA20

    返回 True = 可交易, False = 空仓防御

    基准数据不可用或含 NaN 时 → 默认可交易（大盘过滤是辅助手段，核心风控在止损/止盈）
    """
    if not bm_hist or BENCHMARK not in bm_hist:
        return True

    df = bm_hist[BENCHMARK]
    if df is None or len(df) < MA_MARKET + 1:
        return True

    try:
        close_arr = np.array(df['close'].values, dtype=float)
        current = close_arr[-1]

        # NaN 保护：基准数据异常时默认可交易
        if np.isnan(current):
            return True

        ma = np.mean(close_arr[-MA_MARKET:])
        if np.isnan(ma) or ma <= 0:
            return True

        ok = current >= ma
        if not ok:
            logging.info("[市场] %s=%.2f < MA%d=%.2f → 空仓防御" % (
                BENCHMARK, current, MA_MARKET, ma))
        return ok
    except Exception:
        return True


def _get_sector(code):
    """查询股票所属行业，未分类返回'其他'"""
    return SECTOR_MAP.get(code, '其他')


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
    """
    同步框架持仓到本地 my_positions。
    - 框架有、本地无 → 新买入（上一 bar 的信号已执行）
    - 框架无、本地有 → 已卖出（上一 bar 的卖出信号已执行）
    """
    g = _STATE
    fw_positions = khGet(data, "positions")

    # 1. 框架有、本地无 → 新增
    for code, pos in fw_positions.items():
        vol = pos.get("volume", 0)
        if vol <= 0:
            continue
        if code not in g['my_positions']:
            price = pos.get("avg_price", 0)
            g['my_positions'][code] = {
                'entry_price': price,
                'entry_bar': g['bar_counter'],
                'bars_held': 0,
                'peak_price': price,  # 移动止盈峰值追踪
            }
            logging.debug("[同步] 新持仓 %s vol=%d avg_price=%.2f" % (
                code, vol, pos.get("avg_price", 0)))

    # 2. 本地有、框架无 → 移除
    for code in list(g['my_positions'].keys()):
        if code not in fw_positions or fw_positions[code].get("volume", 0) <= 0:
            del g['my_positions'][code]
            logging.debug("[同步] 移除持仓 %s" % code)


def _apply_signals_to_state(signals, g):
    """
    乐观更新本地持仓状态 — 模拟 QMT 即时成交效果。
    这样同一 bar 内后续逻辑能感知到已做出的交易决策。
    """
    for sig in signals:
        code = sig.get('code', '')
        action = sig.get('action', '')
        price = sig.get('price', 0)
        volume = sig.get('volume', 0)

        if action == 'sell':
            # 全仓卖出 → 移除
            if code in g['my_positions']:
                del g['my_positions'][code]
            # 从 pending 中移除（如果存在）
            if code in g['pending_sells']:
                g['pending_sells'].remove(code)

        elif action == 'buy':
            # 新建持仓
            g['my_positions'][code] = {
                'entry_price': price,
                'entry_bar': g['bar_counter'],
                'bars_held': 0,
                'peak_price': price,  # 移动止盈峰值追踪
            }
