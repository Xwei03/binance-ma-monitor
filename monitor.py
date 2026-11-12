import os, time, json, random, requests, pandas as pd
from datetime import datetime, timezone, timedelta

BASE = "https://openapi.okx.com"
WEBHOOK = os.getenv("FEISHU_WEBHOOK", "")
PROXY = os.getenv("OKX_PROXY", "")
BJ_TZ = timezone(timedelta(hours=8))
STATE = "bus_state.json"

UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (X11; Linux x86_64) Chrome/119.0.0.0 Safari/537.36"
]

TF = {"1H": ("1H", 500, 73, 6), "1D": ("1D", 300, 72, 4)}
MAX_HOLD = {"1H": 100, "1D": 30}
CHASE_ATR = {"1H": 2.5, "1D": 1.5}
GAP_ATR = {"1H": 2.5, "1D": 2.5}
GAP_CAP = {"1H": 0.04, "1D": 0.10}
DEDUP_BARS, COOL_BARS = 8, 12
KEEP_SENT_DAYS, KEEP_RESULT_DAYS = 110, 7
MAX_SEND_PER_SCAN, MAX_LIVE, MIN_VOL, MAX_SHADOW = 20, 20, 10000000, 100

CN = {"1H": "1小时", "1D": "1天"}
DIR = {"LONG": "做多", "SHORT": "做空"}
TYP = {
    "PREPARE_R": "埋伏确认", 
    "PREPARE_C": "埋伏候选",
    "TREND_R": "趋势确认", 
    "TREND_C": "趋势候选",
    "BREAKOUT": "突破"
}
cache, btc_cache, stop, last_req = {}, {}, False, [0.0]

def hdr(): return {"User-Agent": random.choice(UAS), "Accept": "application/json", "Referer": "https://www.okx.com/"}
def now_bj(): return datetime.now(BJ_TZ)
def log(*args): print(*args)

def tf_run(tf):
    return datetime.now(timezone.utc).hour == 0 if tf == "1D" else True

def throttle():
    wait = last_req[0] + (0.3 + random.random() * 0.3) - time.time()
    if wait > 0: time.sleep(wait)
    last_req[0] = time.time()

def get(path, p):
    global stop
    if stop: return
    for i in range(5):
        try:
            throttle()
            kw = {"params": p, "timeout": 20, "headers": hdr()}
            if PROXY: kw["proxies"] = {"http": PROXY, "https": PROXY}
            r = requests.get(BASE + path, **kw)
            if r.status_code in (403, 451): stop = True; return
            if r.status_code == 418: time.sleep(60); continue
            if r.status_code == 429 or r.status_code >= 500: time.sleep(min(3 * 2**i, 30)); continue
            x = r.json()
            if x.get("code") == "0": return x.get("data", [])
            if x.get("code") == "50011": time.sleep(min(3 * 2**i, 30)); continue
            return
        except Exception: time.sleep(min(2**i * 2, 20))

def coins():
    a, b = get("/api/v5/public/instruments", {"instType": "SWAP"}), get("/api/v5/market/tickers", {"instType": "SWAP"})
    if not a or not b: return []
    live = {x["instId"] for x in a if x.get("settleCcy") == "USDT" and x.get("state") == "live" and x.get("instCategory") == "1"}
    vol = {x["instId"]: float(x.get("volCcy24h", 0) or 0) * float(x.get("last", 0) or 0) for x in b}
    valid = [s for s in live if vol.get(s, 0) >= MIN_VOL]
    return sorted(valid, key=lambda x: vol.get(x, 0), reverse=True)[:150]

def candles(sym, bar, n=300):
    k = (sym, bar, n)
    if k in cache and time.time() - cache[k][0] < 30: return cache[k][1]
    x = get("/api/v5/market/candles", {"instId": sym, "bar": bar, "limit": str(n)})
    if not x: return
    try:
        d = pd.DataFrame([[int(a[0]), float(a[1]), float(a[2]), float(a[3]), float(a[4]), float(a[5]), a[8]] for a in reversed(x)], columns=["ts","o","h","l","c","v","ok"])
        d = d[d.ok == "1"].reset_index(drop=True)
        if len(d) < 12: return
        
        if bar == "4H":
            if len(d) > 0 and (d.ts.iloc[-1] + 3600000 > int(time.time() * 1000)):
                d = d.iloc[:-1].reset_index(drop=True)
        else:
            bar_ms = 86400000 if bar == "1D" else (3600000 if bar == "1H" else (15 if bar == "15m" else (5 if bar == "5m" else 1)) * 60000)
            if len(d) > 0 and (d.ts.iloc[-1] + bar_ms > int(time.time() * 1000)): d = d.iloc[:-1].reset_index(drop=True)
            
        cache[k] = (time.time(), d)
        return d
    except Exception: return

def ema(s, n): return s.ewm(span=n, adjust=False).mean()

def atr(d):
    p = d.c.shift()
    return pd.concat([d.h - d.l, (d.h - p).abs(), (d.l - p).abs()], axis=1).max(axis=1).rolling(14).mean()

def find_swing_levels(d, tf, p, side, use_close=True, av=None):
    n_l, n_r = 5, (2 if tf == "1H" else 3)
    look = 500 if tf == "1H" else 90 
    
    arr_h, arr_l, arr_o, arr_c, arr_v = d.h.iloc[-look:-1].values, d.l.iloc[-look:-1].values, d.o.iloc[-look:-1].values, d.c.iloc[-look:-1].values, d.v.iloc[-look:-1].values
    m = len(arr_h)
    if m < n_l + n_r + 1: return []
    avg_vol = d.v.iloc[-21:-1].mean() or 1
    cands = []
    for i in range(n_l, m - n_r):
        if side == "res" and arr_h[i] >= arr_h[i-n_l:i].max() and arr_h[i] >= arr_h[i+1:i+n_r+1].max() and arr_h[i] > p:
            if arr_c[i+1:].max() > arr_h[i]: continue
            if use_close and av is not None:
                wick, body = arr_h[i] - max(arr_o[i], arr_c[i]), abs(arr_c[i] - arr_o[i])
                if wick > 2.0 * body and wick > 0.8 * av: continue
            price = arr_h[i]
            hits = int(((arr_h > price - price*0.005) & (arr_h < price + price*0.005)).sum())
            cands.append((price, (arr_v[i] / avg_vol) * (1 + (i/m)) + hits * 0.5, i))
        elif side == "sup" and arr_l[i] <= arr_l[i-n_l:i].min() and arr_l[i] <= arr_l[i+1:i+n_r+1].min() and arr_l[i] < p:
            if arr_c[i+1:].min() < arr_l[i]: continue
            if use_close and av is not None:
                wick, body = min(arr_o[i], arr_c[i]) - arr_l[i], abs(arr_c[i] - arr_o[i])
                if wick > 2.0 * body and wick > 0.8 * av: continue
            price = arr_l[i]
            hits = int(((arr_l > price - price*0.005) & (arr_l < price + price*0.005)).sum())
            cands.append((price, (arr_v[i] / avg_vol) * (1 + (i/m)) + hits * 0.5, i))
            
    if not cands: return []
    cands.sort(key=lambda x: -x[1])
    used, merged = [False] * len(cands), []
    for i in range(len(cands)):
        if used[i]: continue
        price, score, idx = cands[i][0], cands[i][1], cands[i][2]
        group, max_idx = [price], idx
        used[i] = True
        for j in range(i + 1, len(cands)):
            if not used[j] and abs(cands[j][0] - price) / price < 0.003:
                group.append(cands[j][0]); used[j] = True
                max_idx = max(max_idx, cands[j][2])
        merged.append((sum(group)/len(group), score, max_idx))
        
    merged = [x for x in merged if x[1] >= 0.5]
    merged.sort(key=lambda x: -x[2])
    return [(float(price), float(score)) for price, score, _ in merged]

def calc_sl(d, tf, p, side, a, sl_type="wall"):
    if sl_type == "2k":
        return min(d.l.iloc[-1], d.l.iloc[-2]) - a if side == "LONG" else max(d.h.iloc[-1], d.h.iloc[-2]) + a
    else:
        if side == "LONG":
            sup = find_swing_levels(d, tf, p, "sup", use_close=False)
            if not sup: return p - 2.0 * a
            return sup[0][0] - a 
        else:
            res = find_swing_levels(d, tf, p, "res", use_close=False)
            if not res: return p + 2.0 * a
            return res[0][0] + a 

def btc(bar):
    if bar in btc_cache: return btc_cache[bar]
    d = candles("BTC-USDT-SWAP", bar, 300)
    if d is None: return 0
    p, e20, e60 = d.c.iloc[-1], ema(d.c, 20).iloc[-1], ema(d.c, 60).iloc[-1]
    v = 15 if (e20 > e60 and p > e20) else -15 if (e20 < e60 and p < e20) else 0
    btc_cache[bar] = v
    return v

def h4_filter(sym):
    d4 = candles(sym, "4H", 30)
    if d4 is None or len(d4) < 15: return 0
    a4 = atr(d4).iloc[-1]
    if pd.isna(a4) or a4 <= 0: return 0
    o, c, amp = d4.o.iloc[-1], d4.c.iloc[-1], d4.h.iloc[-1] - d4.l.iloc[-1]
    
    body = abs(c - o)
    if body < 0.5 * a4:
        return 0
        
    if amp > 1.5 * a4:
        return 1 if c > o else -1 if c < o else 0
    return 0

def bars_since(d, ts): return max(int(len(d[d.ts >= ts])) - 1, 0)

def load_state():
    try:
        with open(STATE, encoding="utf8") as f: return json.load(f)
    except Exception: return {"sent":{}, "cool":{}, "live":[], "results":[], "shadow":[], "shadow_cool":{}, "last_report":""}

def save_state(st):
    with open(STATE + ".tmp", "w", encoding="utf8") as f: json.dump(st, f, ensure_ascii=False)
    os.replace(STATE + ".tmp", STATE)

def sdk(sym, tf, side, typ): return f"{sym}|{tf}|{side}|{typ}"

def too_soon(st, sym, tf, side, typ, d, bars):
    raw = st.get("cool", {}).get(sdk(sym, tf, side, typ)) or st.get("sent", {}).get(sdk(sym, tf, side, typ))
    if not raw: return False
    ts = raw.get("ts", 0) if isinstance(raw, dict) else raw
    return bars_since(d, int(ts)) < bars

def mark(st, sym, tf, side, typ, d, where="sent"):
    st.setdefault(where, {})[sdk(sym, tf, side, typ)] = int(d.ts.iloc[-1])

def rec(st, s, r, mfe=None, mfe_pct=None, mae=None, mae_pct=None):
    e = {"sym": s["sym"], "tf": s["tf"], "dir": s["dir"], "type": s["type"], "result": r, "ts": int(time.time()), "rec_ts": int(time.time()*1000), "score": s.get("score", 0)}
    for k_src, k_dst in [("alert_ts", "alert_ts"), ("sent_ts", "sig_ts"), ("entry", "entry"), ("sl", "sl"), ("tp", "tp")]:
        if s.get(k_src): e[k_dst] = float(s[k_src]) if "entry" in k_src or "sl" in k_src or "tp" in k_src else int(s[k_src])
    if mfe is not None: e["mfe"] = round(float(mfe) * 100, 2)
    if mfe_pct is not None: e["mfe_pct"] = round(min(float(mfe_pct), 1.0) * 100, 1)
    if mae is not None: e["mae"] = round(float(mae) * 100, 2)
    if mae_pct is not None: e["mae_pct"] = round(min(float(mae_pct), 1.0) * 100, 1)
    st.setdefault("results", []).append(e)

def rec_shadow(st, sym, tf, side, typ, p, sl, tp, reasons, d, score):
    if len(st.get("shadow", [])) >= MAX_SHADOW: return
    key = f"{sym}|{tf}|{side}|{typ}|{','.join(sorted(reasons))}"
    cool = st.setdefault("shadow_cool", {})
    if key in cool and bars_since(d, int(cool[key])) < 12: return
    if abs(p - sl) < 1e-12 or abs(p - tp) < 1e-12: return
    a_now = atr(d).iloc[-1]
    st.setdefault("shadow", []).append({"sym": sym, "tf": tf, "dir": side, "type": typ, "reasons": list(reasons), "entry": float(p), "sl": float(sl), "tp": float(tp), "orig_sl": float(sl), "score": int(score), "ts": int(time.time()), "sent_ts": int(d.ts.iloc[-1]), "a": float(a_now) if not pd.isna(a_now) else 0.0})
    cool[key] = int(d.ts.iloc[-1])

def post(txt):
    if not WEBHOOK: return False
    for i in range(3):
        try:
            r = requests.post(WEBHOOK, json={"msg_type": "text", "content": {"text": txt}}, timeout=15)
            try: res = r.json()
            except Exception: res = {}
            if r.status_code == 200 and res.get("code") == 0: return True
            if r.status_code == 429: time.sleep(min(5 * 2**i, 30)); continue
            return False
        except Exception: time.sleep(2 * (i + 1))
    return False

def post_long(lines, max_len=1500, gap=2.0):
    batches, cur, cur_len = [], [], 0
    for line in lines:
        if cur_len + len(line) + 1 > max_len and cur: batches.append(cur); cur, cur_len = [], 0
        cur.append(line); cur_len += len(line) + 1
    if cur: batches.append(cur)
    total, ok = len(batches), True
    for idx, batch in enumerate(batches, 1):
        if not post(f"🚨 警报 日报（第{idx}/{total}批）\n" + "\n".join(batch) if total > 1 else "\n".join(batch)): ok = False
        if idx < total: time.sleep(gap)
    return ok

def clean(st):
    c = (int(time.time()) - KEEP_SENT_DAYS * 86400) * 1000
    is_keep = lambda v: v.get("ts", 0) >= c if isinstance(v, dict) else v >= c
    for k in ["sent", "cool", "shadow_cool"]: st[k] = {k2: v for k2, v in st.get(k, {}).items() if is_keep(v)}
    kc = int(time.time()) - KEEP_RESULT_DAYS * 86400
    st["results"] = [r for r in st.get("results", []) if r.get("ts", 0) >= kc]

def daily_report(st):
    n = datetime.now(timezone.utc); today = n.strftime("%Y-%m-%d")
    if st.get("last_report") == today or n.hour >= 12: return
    cut = int(time.time()) - 86400
    rs = [r for r in st.get("results", []) if r["ts"] >= cut and not r.get("shadow")]
    sh = [r for r in st.get("results", []) if r["ts"] >= cut and r.get("shadow")]
    L = ["宝宝巴士🚌上车就赚", "📊 每日统计（警报）"]
    tw, tl, te, tto, tbe = 0, 0, 0, 0, 0
    combos = [(tf, t, f"{tf}{TYP[t]}") for tf in ("1H", "1D") for t in ("PREPARE_R", "PREPARE_C", "TREND_R", "TREND_C", "BREAKOUT")]
    lv = st.get("live", [])
    
    for tf, typ, lb in combos:
        sub = [r for r in rs if r.get("tf") == tf and r.get("type") == typ]
        w, l, be, e, to = [sum(1 for r in sub if r["result"] == x) for x in ["WIN", "LOSS", "BE", "ERROR", "TIMEOUT"]]
        p = sum(1 for x in lv if x.get("tf") == tf and x.get("type") == typ)
        wm, lm, tm, lmp = [[r["mfe" if i != 3 else "mfe_pct"] for r in sub if r["result"] == res and ("mfe" in r if i != 3 else "mfe_pct" in r)] for i, res in enumerate(["WIN", "LOSS", "TIMEOUT", "LOSS"])]
        wa, la, lpa, ta = [sum(x)/len(x) if x else 0 for x in [wm, lm, lmp, tm]]
        t = w + l; tw += w; tl += l; tbe += be; te += e; tto += to
        L.append(f"警报 {lb}：止盈{w} 保本{be} 止损{l} 超时{to}(不计胜率) 在追{p} 胜率{(w/t*100) if t else 0:.0f}% MFE均 胜{wa:.2f}% 负{la:.2f}%({lpa:.0f}%) 超{ta:.2f}%")
        
    allwm, alllm, alltm, alllmp = [[r["mfe" if i != 3 else "mfe_pct"] for r in rs if r["result"] == res and ("mfe" in r if i != 3 else "mfe_pct" in r)] for i, res in enumerate(["WIN", "LOSS", "TIMEOUT", "LOSS"])]
    wa, la, lpa, ta = [sum(x)/len(x) if x else 0 for x in [allwm, alllm, alllmp, alltm]]
    L.append(f"警报 合计：止盈{tw} 保本{tbe} 止损{tl} 超时{tto}(不计胜率) 在追{len(lv)} 胜率{(tw/(tw+tl)*100) if (tw+tl) else 0:.0f}% MFE均 胜{wa:.2f}% 负{la:.2f}%({lpa:.0f}%) 超{ta:.2f}%")
    L.append(f"警报 统计时间：{now_bj().strftime('%Y-%m-%d %H:%M:%S')}")

    if sh or st.get("shadow"):
        L.append(f"\n👻 影子单（防偏科测试组：只记录不偏科的单子）")
        groups = {}
        for r in sh:
            for reason in r.get("reasons", ["?"]): groups.setdefault(reason, []).append(r)
        for k, sb in sorted(groups.items()):
            w, l = sum(1 for r in sb if r["result"] == "WIN"), sum(1 for r in sb if r["result"] == "LOSS")
            ma = sum([r["mfe"] for r in sb if "mfe" in r]) / max(sum(1 for r in sb if "mfe" in r), 1)
            L.append(f"  【被拦[{k}]：盈{w} 损{l} 胜率{(w/(w+l)*100) if (w+l) else 0:.0f}% MFE均{ma:.2f}%】")
            for r in sb:
                t_open = datetime.fromtimestamp((r.get("alert_ts") or r.get("sig_ts"))/1000, BJ_TZ).strftime("%m-%d %H:%M") if (r.get("alert_ts") or r.get("sig_ts")) else ""
                t_close = datetime.fromtimestamp(r["rec_ts"]/1000, BJ_TZ).strftime("%m-%d %H:%M") if r.get("rec_ts") else ""
                mae = f" 险情{r.get('mae_pct',0):.0f}%" if "mae_pct" in r else ""
                L.append(f"    {r['tf']}{TYP.get(r['type'],r['type'])} {r['sym'].replace('-USDT-SWAP','')} {DIR.get(r.get('dir',''),'?')} 分{r.get('score',0)} 入{r.get('entry',0):.8g} 损{r.get('orig_sl', r.get('sl',0)):.8g} 盈{r.get('tp',0):.8g} 被拦[{k}] MFE={r['mfe']}% 进度{r.get('mfe_pct',0):.0f}%{mae} 开:{t_open} {'超时:' if r['result']=='TIMEOUT' else '平:'}{t_close}")

        sh_live = st.get("shadow", [])
        if sh_live:
            L.append(f"\n👻 影子在跟明细（{len(sh_live)}条）：")
            for s in sh_live:
                t_open = datetime.fromtimestamp((s.get("alert_ts") or s.get("sent_ts"))/1000, BJ_TZ).strftime("%m-%d %H:%M") if (s.get("alert_ts") or s.get("sent_ts")) else ""
                L.append(f"  {s['tf']}{TYP.get(s['type'],s['type'])} {s['sym'].replace('-USDT-SWAP','')} {DIR[s['dir']]} 分{s.get('score',0)} 入{s.get('entry',0):.8g} 损{s.get('orig_sl', s.get('sl',0)):.8g} 盈{s.get('tp',0):.8g} 被拦[{'+'.join(s.get('reasons',[]))}] 开:{t_open}")
        L.append(f"  影子在跟：{len(sh_live)}/{MAX_SHADOW} 条")

    for title, sub_list, sort_key in [("🏆 止盈明细（{}条，按分数降序）：", [r for r in rs if r["result"]=="WIN" and "mfe" in r], lambda x:-x.get("score",0)), 
                                      ("⏰ 超时明细（{}条，按止盈进度降序）：", [r for r in rs if r["result"]=="TIMEOUT" and "mfe" in r], lambda x:-x.get("mfe_pct",0)),
                                      ("📋 止损明细（{}条，按止盈进度降序，止盈=100%）：", [r for r in rs if r["result"]=="LOSS" and "mfe" in r], lambda x:-x.get("mfe_pct",0))]:
        if sub_list:
            sub_list.sort(key=sort_key)
            L.append(f"\n" + title.format(len(sub_list)))
            for r in sub_list:
                t_str = datetime.fromtimestamp((r.get("alert_ts") or r.get("sig_ts"))/1000, BJ_TZ).strftime("%m-%d %H:%M") if (r.get("alert_ts") or r.get("sig_ts")) else ""
                tail = f"扫损{datetime.fromtimestamp(r['rec_ts']/1000,BJ_TZ).strftime('%m-%d %H:%M')}" if r["result"]=="LOSS" and r.get("rec_ts") else (f"超时{datetime.fromtimestamp(r['rec_ts']/1000,BJ_TZ).strftime('%m-%d %H:%M')}" if r["result"]=="TIMEOUT" and r.get("rec_ts") else "")
                mae_str = f" 险情{r.get('mae_pct',0):.0f}%" if "mae_pct" in r else ""
                L.append(f"  {r['tf']}{TYP.get(r['type'],r['type'])} {r['sym'].replace('-USDT-SWAP','')} {DIR.get(r.get('dir',''),'?')} 分数{r.get('score',0)} 入场{r.get('entry',0):.8g} 止损{r.get('sl',0):.8g} 止盈{r.get('tp',0):.8g} MFE={r['mfe']}% 进度{r.get('mfe_pct',0):.0f}%{mae_str} 开仓{t_str} {tail}")
            
    live_rows = []
    for s in lv:
        tf = s.get("tf")
        if tf not in TF: continue
        d = candles(s["sym"], TF[tf][0], 300)
        if d is None: continue
        f = d[d.ts > int(s.get("sent_ts") or s.get("anchor") or d.ts.iloc[-1])]
        if len(f) == 0: continue
        ent, tp_v, side = s.get("entry", 0) or 0, s.get("tp", 0) or 0, s["dir"]
        mfe_pct = (f.h.max()-ent if side=="LONG" else ent-f.l.min()) / (tp_v-ent if side=="LONG" else ent-tp_v) if (ent and tp_v and tp_v!=ent) else 0
        live_rows.append((s, mfe_pct))
    if live_rows:
        live_rows.sort(key=lambda x: -x[1])
        L.append(f"\n📋 在追明细（{len(live_rows)}条，按止盈进度降序）：")
        for s, pct in live_rows:
            t_str = datetime.fromtimestamp((s.get("alert_ts") or s.get("sent_ts"))/1000, BJ_TZ).strftime("%m-%d %H:%M") if (s.get("alert_ts") or s.get("sent_ts")) else ""
            L.append(f"  {s['tf']}{TYP.get(s['type'],s['type'])} {s['sym'].replace('-USDT-SWAP','')} {DIR[s['dir']]} 分数{s.get('score',0)} 入场{s.get('entry',0):.8g} 止损{s.get('sl',0):.8g}{'(已通知保本)' if s.get('be_sent') else ''} 止盈{s.get('tp',0):.8g} 进度{pct*100:.0f}% 开仓{t_str}")
            
    if post_long(L): st["last_report"] = today; log("DAILY_REPORT", tw, tl, te, tto, len(lv))

def try_res(sym, bar, side, sl, tp, ts0, ts1):
    bar_ms = {"1m":60000, "5m":300000, "15m":900000, "1H":3600000}
    d = candles(sym, bar, min(int((ts1 - ts0) / bar_ms[bar]) + 10, 300))
    if d is None: return "UNKNOWN"
    f = d[(d.ts > ts0) & (d.ts <= ts1)]
    if len(f) == 0: return "UNKNOWN"
    for _, r in f.iterrows():
        hs, ht = (r.l <= sl, r.h >= tp) if side == "LONG" else (r.h >= sl, r.l <= tp)
        if hs and ht: return "UNKNOWN"
        if ht: return "WIN"
        if hs: return "LOSS"
    return "UNKNOWN"

def resolve(sym, side, sl, tp, ts0, ts1, tf):
    for b in (["1H"] if tf == "1D" else ["1m", "5m", "15m"]):
        r = try_res(sym, b, side, sl, tp, ts0, ts1)
        if r in ("WIN", "LOSS"): return r
    return "UNKNOWN"

def prepare(d, tf, sym, st, sl_type="wall"):
    if d is None or len(d) < 210: return
    p = d.c.iloc[-1]
    a = atr(d); av = a.iloc[-1]; base = a.iloc[-21:-1].mean()
    if pd.isna(av) or pd.isna(base): return
    e20, e60, e120 = ema(d.c, 20), ema(d.c, 60), ema(d.c, 120)
    vr, vr3, ar = d.v.iloc[-1] / max(d.v.iloc[-21:-1].mean(), 1e-12), d.v.iloc[-3:].mean() / max(d.v.iloc[-13:-3].mean(), 1e-12), av / base
    body = abs(p - d.o.iloc[-1]) / max(d.h.iloc[-1] - d.l.iloc[-1], av * .01)
    bs, h4_bias = btc(TF[tf][0]), h4_filter(sym)
    out = []

    gap_max = min(1.5 * av / p, (0.02 if tf == "1H" else 0.06))
    disc_l, disc_s = (0.985 if tf == "1H" else 0.97), (1.015 if tf == "1H" else 1.03)

    for side in ("LONG", "SHORT"):
        if (side == "LONG" and p <= d.o.iloc[-1]) or (side == "SHORT" and p >= d.o.iloc[-1]): continue
        if (side == "LONG" and h4_bias == -1) or (side == "SHORT" and h4_bias == 1): continue

        d_e20 = (p - e20.iloc[-1]) / p if side == "LONG" else (e20.iloc[-1] - p) / p
        if d_e20 < 0 or d_e20 > gap_max: continue
        if abs(e20.iloc[-1] - e60.iloc[-1]) > 1.0 * av or abs(e20.iloc[-1] - e60.iloc[-1]) < 0.2 * av: continue
        if (side == "LONG" and e20.iloc[-1] <= e60.iloc[-1]) or (side == "SHORT" and e20.iloc[-1] >= e60.iloc[-1]): continue
        if vr >= 2.0 or ar > 1.6 or body >= .65: continue
        if (side == "LONG" and bs < 0) or (side == "SHORT" and bs > 0): continue
        if (side == "LONG" and (p < e20.iloc[-1] or (e20.iloc[-1] - e20.iloc[-4]) < 0.05 * av)) or (side == "SHORT" and (p > e20.iloc[-1] or (e20.iloc[-4] - e20.iloc[-1]) < 0.05 * av)): continue
        
        s = (18 if ar <= 1.0 else 14 if ar <= 1.2 else 10 if ar <= 1.5 else 6)
        s += (22 if d_e20 <= (.005 if tf=="1H" else .015) else 18 if d_e20 <= (.010 if tf=="1H" else .030) else 12 if d_e20 <= (.020 if tf=="1H" else .060) else 4)
        s += 23 + (14 if (.7 <= vr < 2.0 and vr3 >= 1.0) else 8 if (.6 <= vr < 2.0 and vr3 >= 0.9) else 4)
        s += 8 if ((bs > 0) if side=="LONG" else (bs < 0)) else 4 if bs == 0 else 0
        s += 5 if (body < .35 and ar <= 1) else 3 if body < .45 else 0

        need, need_rr = (73 if tf == "1H" else 72), (1.5 if tf == "1H" else 2.0)
        sl = calc_sl(d, tf, p, side, av, sl_type)
        if sl is None: continue
        
        risk = (p - sl) if side == "LONG" else (sl - p)
        if risk <= 0: continue
        
        cands = find_swing_levels(d, tf, p, "res" if side=="LONG" else "sup", use_close=False, av=av)
        has_wall = bool(cands)
        
        if cands and s >= need:
            price1, _ = cands[0]
            cd1 = price1 * (disc_l if side=="LONG" else disc_s)
            tp_r = None
            if (cd1 > p and side=="LONG") or (cd1 < p and side=="SHORT"):
                rr_r = (cd1 - p) / risk if side == "LONG" else (p - cd1) / risk
                if rr_r >= need_rr:
                    tp_r = cd1
                    out.append({"sym": sym, "tf": tf, "dir": side, "type": "PREPARE_R", "score": int(s), "entry": p, "sl": sl, "tp": cd1, "rr": rr_r, "anchor": int(d.ts.iloc[-11]), "hi": 0.0, "lo": 0.0, "vr": vr})
            
            if tp_r is None and len(cands) > 1:
                for price_x, _ in cands[1:]:
                    cdx = price_x * (disc_l if side=="LONG" else disc_s)
                    if (cdx > p and side=="LONG") or (cdx < p and side=="SHORT"):
                        rr_c = (cdx - p) / risk if side == "LONG" else (p - cdx) / risk
                        if rr_c >= need_rr:
                            out.append({"sym": sym, "tf": tf, "dir": side, "type": "PREPARE_C", "score": int(s), "entry": p, "sl": sl, "tp": cdx, "rr": rr_c, "anchor": int(d.ts.iloc[-11]), "hi": 0.0, "lo": 0.0, "vr": vr})
                            break 
                        
        elif not has_wall and s >= need:
            tp = p + (2.0 if tf!="1H" else 2.2)*risk if side=="LONG" else p - (2.0 if tf!="1H" else 2.2)*risk
            rr = (tp - p) / risk if side == "LONG" else (p - tp) / risk
            if rr >= need_rr:
                out.append({"sym": sym, "tf": tf, "dir": side, "type": "PREPARE_R", "score": int(s), "entry": p, "sl": sl, "tp": tp, "rr": rr, "anchor": int(d.ts.iloc[-11]), "hi": 0.0, "lo": 0.0, "vr": vr})
            
    return max(out, key=lambda x: x["score"]) if out else None

def normal(d, tf, sym, st, sl_type="wall"):
    if d is None or len(d) < 210: return
    p = d.c.iloc[-1]
    e20s, e60s, e120s = ema(d.c, 20), ema(d.c, 60), ema(d.c, 120)
    e20, e60, e120 = e20s.iloc[-1], e60s.iloc[-1], e120s.iloc[-1]
    a, aa = atr(d).iloc[-1], atr(d).iloc[-6:-1].mean()
    if pd.isna(a) or pd.isna(aa): return
    vr = d.v.iloc[-1] / max(d.v.iloc[-21:-1].mean(), 1e-12)
    body = abs(p - d.o.iloc[-1]) / max(d.h.iloc[-1] - d.l.iloc[-1], a * .01)
    bs, h4_bias = btc(TF[tf][0]), h4_filter(sym)
    out, gap_max, chase_atr = [], min(2.5 * a / p, (0.04 if tf == "1H" else 0.10)), (2.5 if tf == "1H" else 1.5)
    disc_l, disc_s = (0.985 if tf == "1H" else 0.97), (1.015 if tf == "1H" else 1.03)

    def add(side, typ, sl, tp, need_rr):
        if (side == "LONG" and p <= d.o.iloc[-1]) or (side == "SHORT" and p >= d.o.iloc[-1]): return
        if (side == "LONG" and h4_bias == -1) or (side == "SHORT" and h4_bias == 1): return
        if (side == "LONG" and bs < 0) or (side == "SHORT" and bs > 0): return
        if sl == p or tp == p: return
        rr = (tp - p) / (p - sl) if side == "LONG" else (p - tp) / (sl - p)
        if rr <= 0: return
        v = (5 if vr >= 5 else 15 if vr >= 4 else 25 if vr >= 2.5 else 30 if vr >= 1.5 else 15 if vr >= 1.0 else 5) if "TREND" in typ else (30 if 2 <= vr < 2.5 else 25 if 2.5 <= vr < 3 else 15 if vr >= 3 else 20 if vr >= 1.5 else 10 if vr >= 1.2 else 0)
        s = v + (25 if body >= .6 else 12 if body >= .45 else 0) + (20 if a > aa * 1.05 else 0) + (6 if bs == 0 else min(max(bs if side == "LONG" else -bs, 0), 15))
        if (side == "LONG" and p > e20) or (side == "SHORT" and p < e20): s += 10
        if rr >= need_rr and s >= TF[tf][2]:
            out.append({"sym": sym, "tf": tf, "dir": side, "type": typ, "score": int(s), "entry": p, "sl": sl, "tp": tp, "rr": rr, "vr": vr})

    for side, cond, disc in [("LONG", p > e20 and p > e120 and e20 > e60 and abs(e20-e60) >= 0.1*a and e20s.iloc[-1] > e20s.iloc[-4] and (p - e20)/p < gap_max, disc_l),
                             ("SHORT", p < e20 and p < e120 and e20 < e60 and abs(e20-e60) >= 0.1*a and e20s.iloc[-1] < e20s.iloc[-4] and (e20 - p)/p < gap_max, disc_s)]:
        if cond:
            sl_c = calc_sl(d, tf, p, side, a, sl_type)
            if sl_c is not None:
                risk = (p - sl_c) if side == "LONG" else (sl_c - p)
                if risk > 0:
                    cands = find_swing_levels(d, tf, p, "res" if side == "LONG" else "sup", use_close=False, av=a)
                    has_wall = bool(cands)
                    
                    tp_r = None
                    if cands:
                        price1, _ = cands[0]
                        tp_temp = min(price1 * disc, p + 5 * a) if side == "LONG" else max(price1 * disc, p - 5 * a)
                        if (tp_temp > p and side == "LONG") or (tp_temp < p and side == "SHORT"):
                            if (tp_temp - p if side == "LONG" else p - tp_temp) / risk >= 1.5:
                                tp_r = tp_temp
                                add(side, "TREND_R", sl_c, tp_r, 1.5)
                    
                    if tp_r is None and len(cands) > 1:
                        for price_x, _ in cands[1:]:
                            tp_temp = min(price_x * disc, p + 5 * a) if side == "LONG" else max(price_x * disc, p - 5 * a)
                            if (tp_temp > p and side == "LONG") or (tp_temp < p and side == "SHORT"):
                                if (tp_temp - p if side == "LONG" else p - tp_temp) / risk >= 1.5:
                                    add(side, "TREND_C", sl_c, tp_temp, 1.5)
                                    break
                                    
                    if not has_wall:
                        tp_c = p + 5 * a if side == "LONG" else p - 5 * a
                        add(side, "TREND_R", sl_c, tp_c, 1.5)

    hi, lo = d.h.iloc[-12:-2].max(), d.l.iloc[-12:-2].min()
    real_body = abs(p - d.o.iloc[-1]); real_body_safe = max(real_body, a * 0.01)
    uw, lw = d.h.iloc[-1] - max(p, d.o.iloc[-1]), min(p, d.o.iloc[-1]) - d.l.iloc[-1]

    if d.c.iloc[-2] > hi and d.l.iloc[-1] > hi and d.c.iloc[-1] > hi and d.c.iloc[-1] <= hi + chase_atr * a and 1.5 <= vr < 8.0 and uw < real_body_safe * 0.5:
        if sl_type == "2k":
            sl_c = min(d.l.iloc[-1], d.l.iloc[-2]) - a
        else:
            sl_c = hi - 0.2 * a
        if p > sl_c: add("LONG", "BREAKOUT", sl_c, p + 2.0 * (p - sl_c), 2.0)
    if d.c.iloc[-2] < lo and d.h.iloc[-1] < lo and d.c.iloc[-1] < lo and d.c.iloc[-1] >= lo - chase_atr * a and 1.5 <= vr < 8.0 and lw < real_body_safe * 0.5:
        if sl_type == "2k":
            sl_c = max(d.h.iloc[-1], d.h.iloc[-2]) + a
        else:
            sl_c = lo + 0.2 * a
        if sl_c > p: add("SHORT", "BREAKOUT", sl_c, p - 2.0 * (sl_c - p), 2.0)

    return max(out, key=lambda x: x["score"]) if out else None

def signal(d, tf, sym, st, sl_type="wall"):
    p, n = prepare(d, tf, sym, st, sl_type), normal(d, tf, sym, st, sl_type)
    if p and n: return p if p["score"] >= n["score"] else n
    return p or n

def send_signal(st, s):
    t = TYP.get(s["type"], s["type"])
    txt = f"宝宝巴士🚌上车就赚\n🚨 警报 {t}  {CN[s['tf']]}  {s['sym']}\n{DIR[s['dir']]}  {TYP.get(s['type'], s['type'])}  分数{s['score']}  盈亏比{s['rr']:.2f}\n入场{s['entry']:.8g}  止损{s['sl']:.8g}  止盈{s['tp']:.8g}\n{now_bj().strftime('%Y-%m-%d %H:%M:%S')}"
    if post(txt):
        key = sdk(s["sym"], s["tf"], s["dir"], s["type"])
        st.setdefault("sent", {})[key] = int(s["sent_ts"])
        s["alert_ts"], s["be_sent"] = int(time.time() * 1000), False
        if key not in {sdk(x.get("sym",""), x.get("tf",""), x.get("dir",""), x.get("type","")) for x in st.get("live", [])}:
            st.setdefault("live", []).append({k: s[k] for k in ("sym","tf","dir","type","entry","sl","tp","score","rr","hi","lo","sent_ts","alert_ts","be_sent") if k in s})
        return True
    return False

def check_live(st, is_shadow=False):
    pool_key, keep = ("shadow" if is_shadow else "live"), []
    for s in st.get(pool_key) or []:
        tf = s.get("tf")
        if tf not in TF: continue
        d = candles(s["sym"], TF[tf][0], 300)
        if d is None: keep.append(s); continue
        f = d[d.ts > int(s.get("sent_ts") or s.get("anchor") or d.ts.iloc[-1])]
        if len(f) == 0: keep.append(s); continue
        
        sl, tp, side, typ, ent, tp_v = s["sl"], s["tp"], s["dir"], s.get("type",""), s.get("entry",0) or 0, s.get("tp",0) or 0
        res_status = None
        for _, r in f.iterrows():
            hs, ht = (r.l <= sl) if side == "LONG" else (r.h >= sl), (r.h >= tp) if side == "LONG" else (r.l <= tp)
            if hs and ht: res_status = {"LOSS":"LOSS", "WIN":"WIN"}.get(resolve(s["sym"], side, sl, tp, int(r.ts), int(r.ts) + (86400000 if tf=="1D" else 3600000), tf), "UNKNOWN"); break
            elif hs: res_status = "LOSS"; break
            elif ht: res_status = "WIN"; break
                
        if res_status == "UNKNOWN": keep.append(s); continue
        
        mfe = ((f.h.max() - ent) / ent) if (side == "LONG" and ent) else ((ent - f.l.min()) / ent) if ent else 0
        mfe_pct = ((f.h.max() - ent) if side=="LONG" else (ent - f.l.min())) / ((tp_v - ent) if side=="LONG" else (ent - tp_v)) if (ent and tp_v and tp_v!=ent) else 0
            
        if res_status in ("LOSS", "WIN") or bars_since(d, int(s.get("sent_ts") or s.get("anchor") or d.ts.iloc[-1])) >= MAX_HOLD.get(tf, 100):
            if not is_shadow:
                if res_status in ("LOSS", "WIN"): mark(st, s["sym"], tf, side, typ, d, "cool")
                
                _risk = abs(ent - sl) if ent and sl else 0
                _mae_p = (max(0.0, ent - f.l.min()) if side == "LONG" else max(0.0, f.h.max() - ent)) if _risk > 0 else 0
                mae = _mae_p / ent if ent else 0
                mae_pct = _mae_p / _risk if _risk > 0 else 0
                
                rec(st, s, res_status if res_status in ("LOSS", "WIN") else "TIMEOUT", mfe, mfe_pct, mae, mae_pct)
            else:
                _risk = abs(ent - s.get("orig_sl", s.get("sl", 0))) if ent else 0
                _mae_p = (max(0.0, ent - f.l.min()) if side == "LONG" else max(0.0, f.h.max() - ent)) if _risk > 0 else 0
                st.setdefault("results", []).append({
                    "sym": s["sym"], "tf": tf, "dir": side, "type": typ, "result": res_status if res_status in ("LOSS", "WIN") else "TIMEOUT",
                    "ts": int(time.time()), "rec_ts": int(time.time()*1000), "score": s.get("score",0), "alert_ts": s.get("alert_ts"), "sig_ts": s.get("sent_ts"),
                    "entry": s.get("entry",0), "sl": s.get("sl",0), "orig_sl": s.get("orig_sl", s.get("sl",0)), "tp": tp, "mfe": round(mfe*100,2),
                    "mfe_pct": round(min(mfe_pct,1.0)*100,1), "mae": round((_mae_p / ent if ent else 0)*100,2), "mae_pct": round(min(_mae_p / _risk, 1.0)*100,1) if _risk>0 else 0,
                    "shadow": True, "reasons": s.get("reasons", [])
                })
            continue
        keep.append(s)
    st[pool_key] = keep[-MAX_LIVE:]

def scan():
    btc_cache.clear()
    st = load_state(); clean(st)
    check_live(st, False); check_live(st, True)
    save_state(st); daily_report(st)
    ss = coins()
    if not ss: save_state(st); return

    for tf, (bar, n, _, _) in TF.items():
        if stop or not tf_run(tf): continue
        hits = []
        for sym in ss:
            if stop: break
            try:
                dd = candles(sym, bar, n)
                
                # 1. 实盘系统：用历史墙
                s_live = signal(dd, tf, sym, st, sl_type="wall")
                if s_live:
                    if not (too_soon(st, s_live["sym"], tf, s_live["dir"], s_live["type"], dd, DEDUP_BARS) or any(lv.get("sym")==s_live["sym"] and lv.get("tf")==tf for lv in st.get("live", []))):
                        key = sdk(s_live["sym"], tf, s_live["dir"], s_live["type"])
                        if not (key in st.get("cool", {}) and bars_since(dd, int(st["cool"][key])) < COOL_BARS):
                            s_live["sent_ts"] = int(dd.ts.iloc[-1])
                            hits.append(s_live)

                # 2. 影子系统：用两K止损，独立跑，条件跟实盘一样
                s_shadow = signal(dd, tf, sym, st, sl_type="2k")
                if s_shadow:
                    s_key = sdk(s_shadow["sym"], tf, s_shadow["dir"], s_shadow["type"])
                    shadow_already_live = any(x.get("sym")==s_shadow["sym"] and x.get("tf")==tf for x in st.get("shadow", []))
                    shadow_cool_ok = True
                    if s_key in st.get("shadow_cool", {}) and bars_since(dd, int(st["shadow_cool"][s_key])) < COOL_BARS:
                        shadow_cool_ok = False
                        
                    if not shadow_already_live and shadow_cool_ok:
                        s_shadow["sent_ts"] = int(dd.ts.iloc[-1])
                        rec_shadow(st, sym, tf, s_shadow["dir"], s_shadow["type"], s_shadow["entry"], s_shadow["sl"], s_shadow["tp"], ["两K止损"], dd, s_shadow["score"])

            except Exception as e: log(f"[SCAN] {sym} {tf} {type(e).__name__} {e}")
        
        hits.sort(key=lambda x: -x["score"])
        
        cnt = 0
        for s in hits[:MAX_SEND_PER_SCAN]:
            if send_signal(st, s):
                cnt += 1
            time.sleep(.3) 
            
        save_state(st)
        log(f"[{tf}] hits {len(hits)} sent {cnt}")

    print(now_bj().strftime("%H:%M:%S"), "完成")

if __name__ == "__main__": scan()
