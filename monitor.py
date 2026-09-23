import os, time, datetime, requests, pandas as pd

FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK")
INTERVALS = ["15m", "30m", "1H", "4H", "1D"]
THRESHOLD_CONFIG = {"15m": 0.003, "30m": 0.004, "1H": 0.005, "4H": 0.015, "1D": 0.035}
SL_ATR_MULTIPLIER = 2.5
BE_ATR_MULTIPLIER = 0.5
SHADOW_ATR_MULTIPLIER = 2.0

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json", "Accept-Language": "en-US,en;q=0.9"
}

def is_time_to_check(interval):
    now = datetime.datetime.utcnow()
    if interval == "1D": return now.hour == 0
    elif interval == "4H": return now.hour % 4 == 0
    elif interval == "1H": return now.minute < 30
    elif interval == "30m": return now.minute < 15 or (30 <= now.minute < 45)
    return True

def get_btc_trend():
    try:
        url = "https://www.okx.com/api/v5/market/candles?instId=BTC-USDT-SWAP&bar=1H&limit=300"
        r = requests.get(url, headers=HEADERS, timeout=10).json()
        if r.get("code") == "0":
            df = pd.DataFrame(r["data"], columns=["ts","open","high","low","close","vol","volCcy","volCcyQuote","confirm"]).iloc[::-1]
            close = pd.to_numeric(df["close"])
            return close.iloc[-1] > close.ewm(span=200, adjust=False).mean().iloc[-1]
    except Exception: pass
    return True

def get_funding_rate(sym):
    try:
        url = f"https://www.okx.com/api/v5/public/funding-rate?instId={sym}"
        r = requests.get(url, headers=HEADERS, timeout=8).json()
        return float(r["data"][0]["fundingRate"]) if r.get("code") == "0" else 0.0
    except Exception: return 0.0

def get_coingecko_symbols():
    syms = []
    for p in range(1, 5):
        try:
            url = f"https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=250&page={p}&sparkline=false"
            r = requests.get(url, headers=HEADERS, timeout=15).json()
            if isinstance(r, list): syms.extend([i["symbol"].upper() for i in r if "symbol" in i])
        except Exception: pass
    return list(dict.fromkeys(syms))

def get_okx_swap_symbols():
    try:
        url = "https://www.okx.com/api/v5/public/instruments?instType=SWAP"
        r = requests.get(url, headers=HEADERS, timeout=15).json()
        return list(set([i["instId"].split("-")[0] for i in r.get("data", []) if i["instId"].endswith("-USDT-SWAP")])) if r.get("code") == "0" else []
    except Exception: return []

def send_feishu(msg):
    try: requests.post(FEISHU_WEBHOOK, json={"msg_type": "text", "content": {"text": msg}}, timeout=10)
    except Exception as e: print(f"发送失败: {e}")

def get_rr_tag(rr):
    if rr >= 3: return "🔥 极佳机会 (盈亏比≥3)"
    elif rr >= 2: return "✅ 优质机会 (盈亏比≥2)"
    elif rr >= 1.8: return "⚠️ 一般机会 (盈亏比≥1.8，谨慎)"
    else: return "❌ 盈亏比极差 (建议放弃)"

def check_symbol(symbol, interval, btc_trend, alerts):
    okx_sym, bnb_sym = f"{symbol}-USDT-SWAP", f"{symbol}USDT"
    data, source = None, "OKX合约"
    
    try:
        r = requests.get(f"https://fapi.binance.com/fapi/v1/klines?symbol={bnb_sym}&interval={interval.lower()}&limit=300", headers=HEADERS, timeout=8).json()
        if isinstance(r, list) and len(r) > 0: data, source = r, "Binance合约"
    except Exception: pass
    if data is None:
        try:
            r = requests.get(f"https://www.okx.com/api/v5/market/candles?instId={okx_sym}&bar={interval}&limit=300", headers=HEADERS, timeout=10).json()
            if r.get("code") == "0" and r.get("data"): data = r["data"]
        except Exception: return
    if not data: return

    try:
        if source == "Binance合约":
            df = pd.DataFrame(data, columns=['time','open','high','low','close','volume','close_time','qav','num','tbbav','tbqav','ignore'])
            vol_s = pd.to_numeric(df['qav'])
        else:
            df = pd.DataFrame(data, columns=["ts","open","high","low","close","vol","volCcy","volCcyQuote","confirm"]).iloc[::-1].reset_index(drop=True)
            vol_s = pd.to_numeric(df['volCcyQuote'])
        for c in ["open","high","low","close"]: df[c] = pd.to_numeric(df[c])
    except Exception: return

    if vol_s.tail(96).sum() < 10000000: return

    close, high, low = df["close"].iloc[:-1], df['high'].iloc[:-1], df['low'].iloc[:-1]

    try:
        ratio = float(df['volume'].iloc[-2]) / float(df['volume'].iloc[-22:-2].mean()) if float(df['volume'].iloc[-22:-2].mean()) > 0 else 1.0
        if ratio >= 1.5:
            vol_tag = f"🔥 爆量 ({ratio:.1f}x) -> ✅ 主力进场，真突破概率大，可顺势入场"
            vol_state = "爆量"
        elif ratio <= 0.5:
            vol_tag = f"💤 缩量 ({ratio:.1f}x) -> ⚠️ 主力未进场，假突破概率大，建议放弃"
            vol_state = "缩量"
        else:
            vol_tag = f"➖ 平量 ({ratio:.1f}x) -> ⚠️ 资金分歧，需结合趋势谨慎操作"
            vol_state = "平量"
    except Exception:
        vol_tag, vol_state = "➖ 成交量未知", "未知"

    ema20, ema60, ema120 = [close.ewm(span=n, adjust=False).mean().iloc[-1] for n in (20, 60, 120)]
    sma20, sma60, sma120 = [close.rolling(n).mean().iloc[-1] for n in (20, 60, 120)]
    ema200 = close.ewm(span=200, adjust=False).mean().iloc[-1]
    mas = [ema20, ema60, ema120, sma20, sma60, sma120]
    price, diff = close.iloc[-1], max(mas) - min(mas)

    tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().iloc[-1]
    base = THRESHOLD_CONFIG.get(interval, 0.004)
    threshold = max(min((atr / price) * 0.4, base * 2), base * 0.5)

    if diff / price > threshold: return
    
    if (df['high'].iloc[-2] - max(df['open'].iloc[-2], df['close'].iloc[-2])) > SHADOW_ATR_MULTIPLIER * atr or \
       (min(df['open'].iloc[-2], df['close'].iloc[-2]) - df['low'].iloc[-2]) > SHADOW_ATR_MULTIPLIER * atr: return

    res, sup = high.tail(60).max(), low.tail(60).min()
    l_sl, l_tp = price - SL_ATR_MULTIPLIER * atr, res
    s_sl, s_tp = price + SL_ATR_MULTIPLIER * atr, sup
    l_rr = (l_tp - price) / (price - l_sl) if price > l_sl else 0
    s_rr = (price - s_tp) / (s_sl - price) if s_sl > price else 0
    l_tag, s_tag = get_rr_tag(l_rr), get_rr_tag(s_rr)
    
    l_be, s_be = price + BE_ATR_MULTIPLIER * atr, price - BE_ATR_MULTIPLIER * atr
    be_adv = f"🛡️ 保本(多): 涨至 ${l_be:.4f} 改止损至 ${price:.4f}\n🛡️ 保本(空): 跌至 ${s_be:.4f} 改止损至 ${price:.4f}"
    
    fr = get_funding_rate(okx_sym)
    fund = f"⚠️ 资金费率 {fr*100:.3f}% 多头拥挤，慎多" if fr > 0.001 else f"⚠️ 资金费率 {fr*100:.3f}% 空头拥挤，慎空" if fr < -0.001 else ""

    if price > ema200:
        t_desc, t_note = "📈 多头趋势 (价格 > EMA200)", "✅ 顺势，优先做多" if btc_trend else "⚠️ 大盘偏空，逆势做多风险大"
        b_note = "" if btc_trend else " (BTC警告)"
        is_trend_ok = btc_trend
    else:
        t_desc, t_note = "📉 空头趋势 (价格 < EMA200)", "✅ 顺势，优先做空" if not btc_trend else "⚠️ 大盘偏多，逆势做空风险大"
        b_note = "" if not btc_trend else " (BTC警告)"
        is_trend_ok = not btc_trend
    
    advice = f"📈 做多: 止损 ${l_sl:.4f} / 止盈 ${l_tp:.4f} (RR: {l_rr:.2f}) {l_tag}\n📉 做空: 止损 ${s_sl:.4f} / 止盈 ${s_tp:.4f} (RR: {s_rr:.2f}) {s_tag}"
    if fund: advice += f"\n💰 {fund}"
    
    if l_rr >= 1.8 and l_rr >= s_rr:
        primary = f"🎯 首选建议：做多 (RR {l_rr:.2f}) {l_tag}"
        rr_val, rr_ok = l_rr, (l_rr >= 2)
    elif s_rr >= 1.8 and s_rr > l_rr:
        primary = f"🎯 首选建议：做空 (RR {s_rr:.2f}) {s_tag}"
        rr_val, rr_ok = s_rr, (s_rr >= 2)
    else:
        primary = "⚠️ 方向不明确，盈亏比均较低，建议观望"
        rr_val, rr_ok = 0, False
    
    if rr_val == 0: summary = "❌ 不能做（方向不明确）"
    elif vol_state == "缩量": summary = "❌ 不能做（主力未进场，假突破）"
    elif vol_state == "爆量" and is_trend_ok and rr_ok: summary = "✅ 能做（正常仓位，1%风险）"
    elif vol_state == "平量" or not is_trend_ok or not rr_ok: summary = "⚠️ 能做（减半仓位，0.5%风险）"
    else: summary = "✅ 能做（正常仓位）"

    alerts.append(
        f"{symbol} [{interval}] 六线差值:${diff:.4f} (价差:{diff/price:.2%}) 当前价:${price:.4f} ({source})\n"
        f"📊 成交量: {vol_tag}\n"
        f"{primary}\n"
        f"📋 综合评级：{summary}\n\n"
        f"🔴 压力位: ${res:.4f} / 🟢 支撑位: ${sup:.4f}\n\n"
        f"🧭 趋势状态: {t_desc} ({t_note}){b_note}\n\n{advice}\n\n{be_adv}"
    )

if __name__ == "__main__":
    if not FEISHU_WEBHOOK: print("缺少飞书 Webhook")
    else:
        btc_trend = get_btc_trend()
        syms = get_coingecko_symbols()
        swaps = get_okx_swap_symbols()
        if not syms or not swaps: print("获取失败，跳过")
        else:
            exc = ["USDC","USD1","USDG","PYUSD","RLUSD","USDT","USDS","USDe","DAI","BUSD","FDUSD","TUSD","USDP","GUSD","FRAX","USDD","USAT","NFT","AINFT"]
            final = [s for s in syms if s not in exc and s in swaps][:200]
            print(f"✅ 本次最终监控合约币种数量: {len(final)}")
            bj_time = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
            for iv in INTERVALS:
                if not is_time_to_check(iv): continue
                print(f"正在检查周期: {iv}...")
                batch = []
                for s in final:
                    check_symbol(s, iv, btc_trend, batch); time.sleep(0.15)
                if batch:
                    header = f"🚨 {iv} 周期六线粘合警报! (北京时间: {bj_time})\n\n"
                    msg = header + "\n\n".join(batch)
                    send_feishu(msg[:3000] + "\n... (过长已截断)" if len(msg) > 3000 else msg)
                    print(f"✅ {iv} 周期已发送 {len(batch)} 个警报")
            print("本次所有周期检查完毕。")
