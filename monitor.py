import os, time, datetime, requests, pandas as pd

WH = os.environ.get("FEISHU_WEBHOOK")
IVS = ["15m", "30m", "1H", "4H", "1D"]
# 粘合阈值适度放宽，提高频率
TC = {"15m": 0.0055, "30m": 0.007, "1H": 0.009, "4H": 0.028, "1D": 0.05}
SLM, BEM, SHM = 2.3, 0.55, 1.9
H = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

def ok_t(iv):
    n = datetime.datetime.utcnow()
    return (n.hour == 0 and n.minute < 30) if iv == "1D" else (n.hour % 4 == 0 and n.minute < 30) if iv == "4H" else (n.minute < 15) if iv == "1H" else (n.minute < 15 or 30 <= n.minute < 45) if iv == "30m" else True

def btc_t():
    try:
        r = requests.get("https://www.okx.com/api/v5/market/candles?instId=BTC-USDT-SWAP&bar=1H&limit=300", headers=H, timeout=10).json()
        if r.get("code") == "0":
            c = pd.to_numeric(pd.DataFrame(r["data"], columns=["t","o","h","l","c","v","vc","vq","x"])["c"])
            return c.iloc[-1] > c.ewm(span=200, adjust=False).mean().iloc[-1]
    except: pass
    return True

def fund(s):
    try: return float(requests.get(f"https://www.okx.com/api/v5/public/funding-rate?instId={s}", headers=H, timeout=8).json()["data"][0]["fundingRate"])
    except: return 0

def cg():
    s = []
    for p in range(1, 5):
        try:
            r = requests.get(f"https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=250&page={p}&sparkline=false", headers=H, timeout=15).json()
            if isinstance(r, list): s += [i["symbol"].upper() for i in r if "symbol" in i]
        except: pass
    return list(dict.fromkeys(s))

def okx():
    try:
        r = requests.get("https://www.okx.com/api/v5/public/instruments?instType=SWAP", headers=H, timeout=15).json()
        return list(set(i["instId"].split("-")[0] for i in r.get("data", []) if i["instId"].endswith("-USDT-SWAP")))
    except: return []

def send(m):
    try: requests.post(WH, json={"msg_type": "text", "content": {"text": m}}, timeout=10)
    except: pass

def rt(r):
    return "🔥 极佳机会 (盈亏比≥3)" if r >= 3 else "✅ 优质机会 (盈亏比≥2)" if r >= 2 else "⚠️ 一般机会 (盈亏比≥1.8，谨慎)" if r >= 1.8 else "❌ 盈亏比极差 (建议放弃)"

def chk(sym, iv, bt, al):
    ok = f"{sym}-USDT-SWAP"
    d, src = None, "OKX合约"
    
    try:
        r = requests.get(f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}USDT&interval={iv.lower()}&limit=300", headers=H, timeout=8).json()
        if isinstance(r, list) and r: d, src = r, "Binance合约"
    except: pass
    if d is None:
        try:
            r = requests.get(f"https://www.okx.com/api/v5/market/candles?instId={ok}&bar={iv}&limit=300", headers=H, timeout=10).json()
            if r.get("code") == "0" and r.get("data"): d = r["data"]
        except: return
    if not d: return

    try:
        if src == "Binance合约":
            df = pd.DataFrame(d, columns=['t','o','h','l','c','v','ct','q','n','tb','tq','i'])
            vs = pd.to_numeric(df['q'])
        else:
            df = pd.DataFrame(d, columns=["t","o","h","l","c","v","vc","vq","x"]).iloc[::-1].reset_index(drop=True)
            vs = pd.to_numeric(df['vq'])
        for c in "ohlc": df[c] = pd.to_numeric(df[c])
    except: return

    if vs.tail(96).sum() < 8e6: return  # 稍微放宽流动性要求，提高频率

    cl, hi, lo = df["c"].iloc[:-1], df['h'].iloc[:-1], df['l'].iloc[:-1]
    p = cl.iloc[-1]

    # ========== 量能（高胜率核心，缩量直接杀） ==========
    try:
        vol_ma = float(df['v'].iloc[-22:-2].mean())
        rr_ = float(df['v'].iloc[-2]) / vol_ma if vol_ma > 0 else 1
        if rr_ >= 1.7:
            vt, vst = f"🔥 爆量 ({rr_:.1f}x) -> ✅ 主力进场", "爆量"
        elif rr_ >= 1.05:
            vt, vst = f"➖ 平量 ({rr_:.1f}x) -> ⚠️ 需结合趋势", "平量"
        else:
            return  # 缩量直接放弃，保胜率
    except:
        vt, vst = "➖ 成交量未知", "未知"

    # ========== 均线粘合 ==========
    e20, e60, e120 = [cl.ewm(span=n, adjust=False).mean().iloc[-1] for n in (20, 60, 120)]
    s20, s60, s120 = [cl.rolling(n).mean().iloc[-1] for n in (20, 60, 120)]
    e200 = cl.ewm(span=200, adjust=False).mean().iloc[-1]
    df_ = max([e20,e60,e120,s20,s60,s120]) - min([e20,e60,e120,s20,s60,s120])

    tr = pd.concat([hi-lo, (hi-cl.shift(1)).abs(), (lo-cl.shift(1)).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().iloc[-1]
    base = TC.get(iv, 0.006)
    thr = max(min((atr/p)*0.5, base*2.3), base*0.55)  # 放宽粘合

    if df_/p > thr: return

    # 影线过滤
    last_h, last_l = df['h'].iloc[-2], df['l'].iloc[-2]
    last_o, last_c = df['o'].iloc[-2], df['c'].iloc[-2]
    if (last_h - max(last_o, last_c)) > SHM*atr or (min(last_o, last_c) - last_l) > SHM*atr:
        return

    # ========== 止盈止损（提高实际盈亏比） ==========
    rs, sp = hi.tail(55).max(), lo.tail(55).min()
    lsl, ssl = p - SLM*atr, p + SLM*atr

    ltp = max(rs * 0.995, p + 2.6 * atr)
    stp = min(sp * 1.005, p - 2.6 * atr)

    lrr = (ltp - p) / (p - lsl) if p > lsl else 0
    srr = (p - stp) / (ssl - p) if ssl > p else 0
    lt, st = rt(lrr), rt(srr)

    be = (f"🛡️ 保本提示 (做多): 涨至 ${p + BEM*atr:.4f} 时，止损移至开仓价 ${p:.4f}\n"
          f"🛡️ 保本提示 (做空): 跌至 ${p - BEM*atr:.4f} 时，止损移至开仓价 ${p:.4f}")

    fr = fund(ok)
    fn = f"⚠️ 资金费率 {fr*100:.3f}% 多头拥挤，慎多" if fr > 0.001 else f"⚠️ 资金费率 {fr*100:.3f}% 空头拥挤，慎空" if fr < -0.001 else ""

    # ========== 趋势 ==========
    if p > e200:
        td = "📈 多头趋势 (价格 > EMA200)"
        tn = "✅ 顺势优先做多" if bt else "⚠️ 大盘偏空，逆势需谨慎"
        bn = "" if bt else " (BTC警告)"
        itok = bt
    else:
        td = "📉 空头趋势 (价格 < EMA200)"
        tn = "✅ 顺势优先做空" if not bt else "⚠️ 大盘偏多，逆势需谨慎"
        bn = "" if not bt else " (BTC警告)"
        itok = not bt

    adv = (f"📈 做多: 止损 ${lsl:.4f} / 止盈 ${ltp:.4f} (RR: {lrr:.2f}) {lt}\n"
           f"📉 做空: 止损 ${ssl:.4f} / 止盈 ${stp:.4f} (RR: {srr:.2f}) {st}")
    if fn: adv += f"\n💰 {fn}"

    # 方向选择（逆势只允许高RR）
    if lrr >= 1.8 and lrr >= srr and (itok or lrr >= 2.8):
        pri, rv, rok = f"🎯 首选建议：做多 (RR {lrr:.2f}) {lt}", lrr, lrr >= 2.0
    elif srr >= 1.8 and srr > lrr and (itok or srr >= 2.8):
        pri, rv, rok = f"🎯 首选建议：做空 (RR {srr:.2f}) {st}", srr, srr >= 2.0
    else:
        return  # 方向不明确直接不推，保质量

    # ========== 综合评级 ==========
    if vst == "爆量" and itok and rok:
        sm = "✅ 能做（正常仓位，1%风险）"
    elif vst == "爆量" and rok:
        sm = "⚠️ 能做（减半仓位，0.5%风险）- 逆势谨慎"
    elif vst == "平量" and itok and rok:
        sm = "⚠️ 能做（减半仓位，0.5%风险）"
    else:
        return  # 条件不够的不推送

    al.append(
        f"{sym} [{iv}] ({src})\n"
        f"💰 当前价: ${p:.4f}\n"
        f"📉 六线差值: ${df_:.4f} (价差:{df_/p:.2%})\n"
        f"📊 成交量: {vt}\n"
        f"{pri}\n"
        f"📋 综合评级：{sm}\n\n"
        f"🔴 压力位: ${rs:.4f}\n"
        f"🟢 支撑位: ${sp:.4f}\n\n"
        f"🧭 趋势状态: {td} ({tn}){bn}\n\n"
        f"{adv}\n\n"
        f"{be}"
    )

if __name__ == "__main__":
    if not WH:
        print("缺少Webhook")
    else:
        bt = btc_t()
        ss = cg()
        sw = okx()
        if not ss or not sw:
            print("获取失败")
        else:
            exc = ["USDC","USD1","USDG","PYUSD","RLUSD","USDT","USDS","USDe","DAI","BUSD","FDUSD","TUSD","USDP","GUSD","FRAX","USDD","USAT","NFT","AINFT"]
            fin = [s for s in ss if s not in exc and s in sw][:220]  # 稍微多扫一点
            print(f"✅ 本次最终监控合约币种数量: {len(fin)}")
            bjt = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
            for iv in IVS:
                if not ok_t(iv): continue
                print(f"正在检查周期: {iv}...")
                b = []
                for s in fin:
                    chk(s, iv, bt, b)
                    time.sleep(0.11)
                if b:
                    for i in range(0, len(b), 5):
                        send(f"🚨 {iv} 周期六线粘合警报! (北京时间: {bjt})\n\n" + "\n\n".join(b[i:i+5]))
                    print(f"✅ {iv} 周期已发送 {len(b)} 个警报")
                else:
                    print(f"ℹ️ {iv} 周期无符合条件信号")
            print("本次所有周期检查完毕。")
