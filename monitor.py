import os,time,json,random,requests,pandas as pd
from datetime import datetime,timezone,timedelta

BASE="https://www.okx.com"
WEBHOOK=os.getenv("FEISHU_WEBHOOK","")
API_GAP=.45
FEISHU_GAP=2
MAX429=3
caches={}
btc_cache={}
breaker=False
con429=0

HDR={"User-Agent":"Mozilla/5.0","Accept":"application/json"}

TF={
    "15m":{"bar":"15m","limit":250,"score":68,"exp":12},
    "30m":{"bar":"30m","limit":250,"score":68,"exp":18},
    "1H":{"bar":"1H","limit":250,"score":70,"exp":48},
    "4H":{"bar":"4H","limit":250,"score":72,"exp":120},
    "1D":{"bar":"1D","limit":250,"score":74,"exp":336}
}

def sleep_api():
    time.sleep(API_GAP+random.uniform(.08,.25))

def get(path,params=None,retry=3):
    global breaker,con429
    if breaker:
        return None
    for i in range(retry):
        sleep_api()
        try:
            r=requests.get(BASE+path,params=params,timeout=15,headers=HDR)
            if r.status_code in (403,418):
                breaker=True
                return None
            if r.status_code==429:
                con429+=1
                if con429>=MAX429:
                    breaker=True
                    return None
                time.sleep(min(20*(2**i),60)+random.uniform(1,4))
                continue
            if r.status_code>=500:
                time.sleep(3*(i+1))
                continue
            r.raise_for_status()
            con429=0
            return r.json()
        except requests.RequestException:
            if i<retry-1:
                time.sleep(2*(i+1))
    return None

def candles(sym,bar,limit=250):
    k=f"{sym}_{bar}"
    now=time.time()
    if k in caches and now-caches[k][0]<35:
        return caches[k][1]
    x=get("/api/v5/market/candles",{
        "instId":sym,
        "bar":bar,
        "limit":str(limit)
    })
    if not x or x.get("code")!="0":
        return None
    rows=[]
    for z in reversed(x["data"]):
        rows.append([
            int(z[0]),float(z[1]),float(z[2]),float(z[3]),
            float(z[4]),float(z[5])
        ])
    df=pd.DataFrame(rows,columns=["ts","o","h","l","c","v"])
    caches[k]=(now,df)
    return df

def ema(s,n):
    return s.ewm(span=n,adjust=False).mean()

def atr(df,n=14):
    pc=df.c.shift()
    tr=pd.concat([
        df.h-df.l,
        (df.h-pc).abs(),
        (df.l-pc).abs()
    ],axis=1).max(axis=1)
    return tr.rolling(n).mean()

def btc(bar):
    if bar in btc_cache and time.time()-btc_cache[bar][0]<60:
        return btc_cache[bar][1]
    df=candles("BTC-USDT-SWAP",bar,250)
    if df is None or len(df)<205:
        btc_cache[bar]=(time.time(),0)
        return 0
    e=ema(df.c,200).iloc[-1]
    p=df.c.iloc[-1]
    s=6 if p>e else -6
    btc_cache[bar]=(time.time(),s)
    return s

def funding(sym):
    x=get("/api/v5/public/funding-rate",{"instId":sym})
    if not x or x.get("code")!="0" or not x.get("data"):
        return None
    try:
        return float(x["data"][0]["fundingRate"])*100
    except:
        return None

def coins(n=150):
    a=get("/api/v5/public/instruments",{"instType":"SWAP"})
    if not a or a.get("code")!="0":
        return []
    syms=[
        x["instId"] for x in a["data"]
        if x.get("settleCcy")=="USDT"
        and x.get("state")=="live"
        and x.get("ctType")=="linear"
    ]
    b=get("/api/v5/market/tickers",{"instType":"SWAP"})
    if not b or b.get("code")!="0":
        return syms[:n]
    mp={
        x["instId"]:float(x.get("volCcy24h","0") or 0)
        for x in b["data"]
    }
    return sorted(syms,key=lambda x:mp.get(x,0),reverse=True)[:n]

def resonance(sym,tf,direction,signals):
    adj={"15m":"30m","30m":"1H","1H":"4H","4H":"1D"}
    rev={v:k for k,v in adj.items()}
    tf_adj={adj.get(tf),rev.get(tf)}-{None}
    has_adj=any(
        x["sym"]==sym and x["tf"] in tf_adj and x["dir"]==direction
        for x in signals
    )
    if has_adj:
        return "相邻周期共振"
    has_any=any(
        x["sym"]==sym and x["dir"]==direction and x["tf"]!=tf
        for x in signals
    )
    return "跨周期共振" if has_any else "无"

def signal(df,tf,sym):
    if df is None or len(df)<210:
        return None

    p=df.c.iloc[-1]
    e20=ema(df.c,20).iloc[-1]
    e60=ema(df.c,60).iloc[-1]
    e120=ema(df.c,120).iloc[-1]
    e200=ema(df.c,200).iloc[-1]
    a=atr(df).iloc[-1]

    if not a or a<=0:
        return None

    body=abs(df.c.iloc[-1]-df.o.iloc[-1])
    rng=max(df.h.iloc[-1]-df.l.iloc[-1],a*.01)
    vol=df.v.iloc[-1]
    vmean=df.v.iloc[-21:-1].mean()
    vr=vol/vmean if vmean>0 else 0
    spread=abs(e20-e60)/p*100
    btc_s=btc(TF[tf]["bar"])

    candidates=[]

    if e20>e60>e120 and p>e200:
        sc=28
        sc+=10 if spread>=.25 else 5 if spread>=.15 else 0
        sc+=10 if vr>=1.25 else 5 if vr>=1 else 0
        sc+=8 if body/rng>=.55 else 3 if body/rng>=.4 else 0
        sc+=min(max(btc_s,0),18)
        sc+=8 if p>e20 else 0
        f=funding(sym)
        if f is not None:
            sc-=5 if f>.08 else 3 if f>.05 else 0
        if sc>=TF[tf]["score"]:
            sl=p-2.5*a
            tp=min(
                df.h.iloc[-61:-1].max()*.995,
                p+3*a
            )
            rr=(tp-p)/(p-sl) if p>sl else 0
            if rr>=1.8:
                candidates.append({
                    "sym":sym,"tf":tf,"dir":"LONG",
                    "type":"TREND","score":round(sc),
                    "entry":p,"sl":sl,"tp":tp,"rr":rr
                })

    if e20<e60<e120 and p<e200:
        sc=28
        sc+=10 if spread>=.25 else 5 if spread>=.15 else 0
        sc+=10 if vr>=1.25 else 5 if vr>=1 else 0
        sc+=8 if body/rng>=.55 else 3 if body/rng>=.4 else 0
        sc+=min(max(-btc_s,0),18)
        sc+=8 if p<e20 else 0
        f=funding(sym)
        if f is not None:
            sc-=5 if f<-.08 else 3 if f<-.05 else 0
        if sc>=TF[tf]["score"]:
            sl=p+2.5*a
            tp=max(
                df.l.iloc[-61:-1].min()*1.005,
                p-3*a
            )
            rr=(p-tp)/(sl-p) if p<sl else 0
            if rr>=1.8:
                candidates.append({
                    "sym":sym,"tf":tf,"dir":"SHORT",
                    "type":"TREND","score":round(sc),
                    "entry":p,"sl":sl,"tp":tp,"rr":rr
                })

    hi=df.h.iloc[-21:-1].max()
    lo=df.l.iloc[-21:-1].min()

    if p>hi and body/rng>=.55 and vr>=1.5:
        sc=35
        sc+=12 if vr>=2 else 6
        sc+=10 if body/rng>=.7 else 5
        sc+=8 if a>atr(df).iloc[-6:-1].mean()*1.05 else 0
        sc+=min(max(btc_s,0),18)
        sc+=5 if p>e20 else 0
        if sc>=TF[tf]["score"]:
            sl=p-2.5*a
            tp=min(
                df.h.iloc[-61:-1].max()*.995,
                p+3*a
            )
            rr=(tp-p)/(p-sl) if p>sl else 0
            if rr>=1.8:
                candidates.append({
                    "sym":sym,"tf":tf,"dir":"LONG",
                    "type":"BREAKOUT","score":round(sc),
                    "entry":p,"sl":sl,"tp":tp,"rr":rr
                })

    if p<lo and body/rng>=.55 and vr>=1.5:
        sc=35
        sc+=12 if vr>=2 else 6
        sc+=10 if body/rng>=.7 else 5
        sc+=8 if a>atr(df).iloc[-6:-1].mean()*1.05 else 0
        sc+=min(max(-btc_s,0),18)
        sc+=5 if p<e20 else 0
        if sc>=TF[tf]["score"]:
            sl=p+2.5*a
            tp=max(
                df.l.iloc[-61:-1].min()*1.005,
                p-3*a
            )
            rr=(p-tp)/(sl-p) if p<sl else 0
            if rr>=1.8:
                candidates.append({
                    "sym":sym,"tf":tf,"dir":"SHORT",
                    "type":"BREAKOUT","score":round(sc),
                    "entry":p,"sl":sl,"tp":tp,"rr":rr
                })

    if not candidates:
        return None

    return max(candidates,key=lambda x:x["score"])

def load_json(path,default):
    try:
        with open(path,"r",encoding="utf-8") as f:
            return json.load(f)
    except:
        return default

def save_json(path,data):
    tmp=path+".tmp"
    with open(tmp,"w",encoding="utf-8") as f:
        json.dump(data,f,ensure_ascii=False,indent=2)
    os.replace(tmp,path)

def send(msg):
    if not WEBHOOK:
        return False
    time.sleep(FEISHU_GAP+random.uniform(.1,.5))
    for i in range(3):
        try:
            r=requests.post(
                WEBHOOK,
                json={"msg_type":"text","content":{"text":msg}},
                timeout=12
            )
            if r.status_code==429:
                time.sleep(min(5*(2**i),30))
                continue
            return r.ok
        except requests.RequestException:
            time.sleep(2*(i+1))
    return False

def fmt(x):
    return f"{x:.8g}"

def signal_msg(s):
    return (
        "OKX信号\n"
        f"币种：{s['sym']}\n"
        f"周期：{s['tf']}\n"
        f"类型：{s['type']}\n"
        f"方向：{s['dir']}\n"
        f"评分：{s['score']}\n"
        f"入场：{fmt(s['entry'])}\n"
        f"止损：{fmt(s['sl'])}\n"
        f"止盈：{fmt(s['tp'])}\n"
        f"RR：{s['rr']:.2f}\n"
        f"共振：{s.get('res','无')}\n"
        f"UTC：{s['time']}"
    )

def key(s):
    return f"{s['sym']}|{s['tf']}|{s['dir']}|{s['type']}|{s['ts']}"

def evaluate(s):
    exp=TF[s["tf"]]["exp"]
    df=candles(s["sym"],TF[s["tf"]]["bar"],300)
    if df is None:
        return None

    start=s["ts"]
    future=df[df.ts>start]
    if future.empty:
        return None

    maxbars=max(1,int(exp*60/{"15m":15,"30m":30,"1H":60,"4H":240,"1D":1440}[s["tf"]]))

    future=future.iloc[:maxbars]

    for _,r in future.iterrows():
        if s["dir"]=="LONG":
            hit_sl=r.l<=s["sl"]
            hit_tp=r.h>=s["tp"]
        else:
            hit_sl=r.h>=s["sl"]
            hit_tp=r.l<=s["tp"]

        if hit_sl and hit_tp:
            return "LOSS"
        if hit_tp:
            return "WIN"
        if hit_sl:
            return "LOSS"

    if len(future)>=maxbars:
        return "EXPIRED"
    return None

def main():
    global breaker

    signals=[]
    sent=load_json("sent_cache.json",{})
    records=load_json("signals_record.json",[])

    now=datetime.now(timezone.utc)
    cutoff=(now-timedelta(hours=48)).timestamp()
    sent={k:v for k,v in sent.items() if v>cutoff}

    syms=coins(150)

    for sym in syms:
        if breaker:
            break

        for tf in TF:
            if breaker:
                break

            df=candles(sym,TF[tf]["bar"],TF[tf]["limit"])
            s=signal(df,tf,sym)

            if s:
                s["ts"]=int(df.ts.iloc[-1])
                s["time"]=datetime.fromtimestamp(
                    s["ts"]/1000,timezone.utc
                ).strftime("%Y-%m-%d %H:%M")
                signals.append(s)

    for s in signals:
        s["res"]=resonance(
            s["sym"],s["tf"],s["dir"],signals
        )

    record_ids={x.get("id") for x in records}

    for s in signals:
        sid=key(s)

        if sid not in record_ids:
            records.append({
                "id":sid,
                **s,
                "result":None,
                "created":int(time.time())
            })
            record_ids.add(sid)

        if sid not in sent:
            if send(signal_msg(s)):
                sent[sid]=int(time.time())

    for r in records:
        if not r.get("result"):
            result=evaluate(r)
            if result:
                r["result"]=result
                r["result_time"]=int(time.time())

    save_json("sent_cache.json",sent)
    save_json("signals_record.json",records)

if __name__=="__main__":
    main()
