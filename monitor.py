import os, time, random, datetime, requests, pandas as pd

WH = os.environ.get("FEISHU_WEBHOOK", "")
IVS = ["15m", "30m", "1H", "4H", "1D"]
MAX_COINS = 150
API_GAP, FEISHU_GAP = 0.3, 1.5
last_api, last_msg = 0, 0
fund_cache, sent = {}, {}

H = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
S = requests.Session(); S.headers.update(H)

SLM, BEM = 2.5, 0.5
MIN_SCORE = {"15m": 75, "30m": 75, "1H": 76, "4H": 78, "1D": 80}
EXCLUDE = {"USDT","USDC","USD1","USDG","PYUSD","RLUSD","USDS","USDE","DAI","BUSD","FDUSD","TUSD","USDP","GUSD","FRAX","USDD","USAT","NFT","AINFT"}

def wait_api():
    global last_api
    d = API_GAP - (time.time() - last_api)
    if d > 0: time.sleep(d)
    time.sleep(random.uniform(.05, .15))
    last_api = time.time()

def get(url, params=None, timeout=12, retry=2):
    for n in range(retry + 1):
        wait_api()
        try:
            r = S.get(url, params=params, timeout=timeout)
            if r.status_code == 200: return r.json()
            if r.status_code == 429: time.sleep(10 * (n + 1)); continue
            if r.status_code in (403, 418): return None
            if r.status_code >= 500: time.sleep(3 * (n + 1))
        except requests.RequestException: time.sleep(2 * (n + 1))
    return None

def okx_symbols():
    d = get("https://www.okx.com/api/v5/public/instruments", {"instType": "SWAP"}, 15)
    if not d: return []
    return list(dict.fromkeys(x["instId"].split("-")[0] for x in d.get("data", []) 
        if x.get("instId","").endswith("-USDT-SWAP") and x.get("state") == "live" and x["instId"].split("-")[0] not in EXCLUDE))

def coins():
    ss = okx_symbols()
    if not ss: return []
    d = get("https://www.okx.com/api/v5/market/tickers", {"instType": "SWAP"}, 15)
    if not d: return ss[:MAX_COINS]
    a = []
    for x in d.get("data", []):
        ins = x.get("instId","")
        if not ins.endswith("-USDT-SWAP"): continue
        s = ins.split("-")[0]
        if s in ss:
            try: a.append((s, float(x.get("volCcy24h", 0))))
            except: pass
    a.sort(key=lambda x:x[1], reverse=True)
    return [x[0] for x in a[:MAX_COINS]]

def ok_t(iv):
    n = datetime.datetime.utcnow()
    return (n.hour == 0 and n.minute < 30) if iv == "1D" else (n.hour % 4 == 0 and n.minute < 30) if iv == "4H" else (n.minute < 30) if iv == "1H" else (n.minute < 15 or 30 <= n.minute < 45) if iv == "30m" else True

def candles(sym, iv):
    d = get(f"https://www.okx.com/api/v5/market/candles", {"instId": f"{sym}-USDT-SWAP", "bar": iv, "limit": 230}, 12)
    if not d or not d.get("data"): return None
    try:
        df = pd.DataFrame(d["data"], columns=["t","o","h","l","c","v","vc","vq","confirm"])
        for c in ["o","h","l","c","v","t"]: df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna().sort_values("t").reset_index(drop=True)
        if str(df["confirm"].iloc[-1]) != "1": df = df.iloc[:-1]
        return df if len(df) >= 205 else None
    except: return None

def funding(sym):
    if sym in fund_cache: return fund_cache[sym]
    d = get("https://www.okx.com/api/v5/public/funding-rate", {"instId": f"{sym}-USDT-SWAP"}, 8, 1)
    try: x = float(d["data"][0]["fundingRate"])
    except: x = 0
    fund_cache[sym] = x
    return x

def btc_trend():
    df = candles("BTC", "1H")
    if df is None: return True
    c = df["c"]
    return c.iloc[-1] >= c.ewm(span=200, adjust=False).mean().iloc[-1]

def calc(df):
    c, h, l = df["c"], df["h"], df["l"]
    e20, e60, e120 = [c.ewm(span=n, adjust=False).mean().iloc[-1] for n in (20, 60, 120)]
    e200 = c.ewm(span=200, adjust=False).mean().iloc[-1]
    m20, m60, m120 = [c.rolling(n).mean().iloc[-1] for n in (20, 60, 120)]
    pc = c.shift(1)
    tr = pd.concat([h-l, (h-pc).abs(), (l-pc).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().iloc[-1]
    p = c.iloc[-1]
    six = [e20, e60, e120, m20, m60, m120]
    return {"p": p, "atr": atr, "e200": e200, "spread": (max(six)-min(six))/p, 
            "vr": df["v"].iloc[-1] / max(df["v"].iloc[-22:-2].mean(), 1e-12)}

def score(df, iv, direction, btc):
    x = calc(df)
    p, s, reasons = x["p"], 0, []
    if p <= 0 or x["atr"] <= 0: return None
    if x["vr"] < 0.5: return None

    if direction == "LONG":
        if p > x["e200"]: s += 15; reasons.append("EMA200多头")
        if btc: s += 10
        else: s -= 7
    else:
        if p < x["e200"]: s += 15; reasons.append("EMA200空头")
        if not btc: s += 10
        else: s -= 7

    lim = {"15m":.012, "30m":.014, "1H":.017, "4H":.025, "1D":.040}[iv]
    if x["spread"] <= lim: s += 15; reasons.append("六线粘合")
    elif x["spread"] <= lim*1.35: s += 8

    if x["vr"] >= 1.8: s += 12; reasons.append("爆量")
    elif x["vr"] >= 1.35: s += 9; reasons.append("放量")
    elif x["vr"] >= .8: s += 5

    ph, pl = df["h"].iloc[-21:-1].max(), df["l"].iloc[-21:-1].min()
    if direction == "LONG" and p > ph: s += 15; reasons.append("突破前高")
    elif direction == "SHORT" and p < pl: s += 15; reasons.append("跌破前低")

    row = df.iloc[-1]
    rng, body = row["h"] - row["l"], abs(row["c"] - row["o"])
    if rng > 0:
        br = body/rng
        if br >= .6: s += 5
        elif br >= .4: s += 3

    atr_pct = x["atr"]/p
    if .001 <= atr_pct <= .15: s += 5
    else: s -= 5

    return max(0, min(100, int(s))), x, reasons

def signal(sym, iv, btc):
    df = candles(sym, iv)
    if df is None: return None
    a, b = score(df, iv, "LONG", btc), score(df, iv, "SHORT", btc)
    if not a or not b: return None
    direction, result = ("LONG", a) if a[0] >= b[0] else ("SHORT", b)
    base_score, x, reasons = result
    if base_score < MIN_SCORE[iv] - 8: return None

    fr = funding(sym)
    if direction == "LONG" and fr > .001: base_score -= 5
    elif direction == "SHORT" and fr < -.001: base_score -= 5
    if base_score < MIN_SCORE[iv]: return None

    p, atr = x["p"], x["atr"]
    hi, lo = df["h"].iloc[-60:].max(), df["l"].iloc[-60:].min()
    
    if direction == "LONG":
        sl = p - SLM*atr
        tp = hi * 0.995
        be = p + BEM*atr
        rr = (tp - p) / (p - sl) if p > sl else 0
        itok = p > x["e200"]
    else:
        sl = p + SLM*atr
        tp = lo * 1.005
        be = p - BEM*atr
        rr = (p - tp) / (sl - p) if sl > p else 0
        itok = p < x["e200"]

    if itok:
        if rr < 1.8: return None
    else:
        if rr < 2.5: return None

    return {"sym":sym, "iv":iv, "direction":direction, "score":base_score,
            "p":p, "sl":sl, "tp":tp, "rr":rr, "be":be, "fr":fr, "vr":x["vr"], "spread":x["spread"], "reasons":reasons, "itok":itok}

def send(text):
    global last_msg
    if not WH: return False
    gap = FEISHU_GAP-(time.time()-last_msg)
    if gap > 0: time.sleep(gap)
    for n in range(3):
        try:
            r = S.post(WH, json={"msg_type":"text","content":{"text":text}}, timeout=10)
            last_msg = time.time()
            if r.status_code == 200: return True
            if r.status_code == 429: time.sleep(8*(n+1)); continue
            return False
        except: time.sleep(3*(n+1))
    return False

def format_msg(x, btc):
    vr = x["vr"]
    vol = f"🔥 爆量 {vr:.2f}x" if vr >= 1.8 else f"📈 放量 {vr:.2f}x" if vr >= 1.35 else f"📊 成交量 {vr:.2f}x"
    reason = "、".join(x["reasons"][:5]) or "综合条件"
    dir_emoji = "🟢 做多" if x["direction"]=="LONG" else "🔴 做空"
    dir_text = "做多" if x["direction"]=="LONG" else "做空"
    trend_tag = "✅ 顺势" if x["itok"] else "⚠️ 逆势"

    return (
        f"🚨 {x['iv']} 周期信号\n{x['sym']} (OKX)\n\n"
        f"{dir_emoji}｜{trend_tag}\n"
        f"🎯 综合评分：{x['score']}/100\n"
        f"💰 当前价：${x['p']:.6f}\n"
        f"{vol}\n"
        f"📐 均线差：{x['spread']:.2%}\n"
        f"🔎 条件：{reason}\n"
        f"₿ BTC：{'偏多' if btc else '偏空'}\n"
        f"💵 资金费率：{x['fr']*100:.4f}%\n\n"
        f"📈 {dir_text}计划:\n"
        f"🛑 止损：${x['sl']:.6f}\n"
        f"🎯 止盈(保守)：${x['tp']:.6f}\n"
        f"📊 RR：{x['rr']:.2f}\n"
        f"🛡️ 保本：${x['be']:.6f}"
    )

def main():
    if not WH: print("⛔ 缺少 FEISHU_WEBHOOK"); return
    print("🚀 终极平衡完美版启动")
    symbols = coins()
    if not symbols: print("⛔ 获取币种失败"); return
    print(f"✅ 监控 {len(symbols)} 个高流动性合约")
    btc = btc_trend()
    print("₿ BTC：", "偏多" if btc else "偏空")

    now_ts = time.time()
    for iv in IVS:
        if not ok_t(iv): continue
        print(f"\n========== {iv} ==========")
        results = []
        for sym in symbols:
            try:
                x = signal(sym, iv, btc)
                if not x: continue
                key = (x["sym"], x["iv"], x["direction"])
                if key in sent and now_ts - sent[key] < 1800:
                    continue
                sent[key] = now_ts
                results.append(x)
                if iv == "15m":
                    if send(format_msg(x, btc)): print(f"📨 发送 {sym} {x['direction']} {x['score']}")
            except Exception as e: print(f"⚠️ {sym}: {e}")

        if iv != "15m" and results:
            results.sort(key=lambda x:x["score"], reverse=True)
            for x in results[:10]:
                if send(format_msg(x, btc)): print(f"📨 发送 {x['sym']} {x['direction']} {x['score']}")
        print(f"✅ {iv}完成：{len(results)} 个信号")
    print("🏁 全部周期检查完成")

if __name__ == "__main__":
    main()
