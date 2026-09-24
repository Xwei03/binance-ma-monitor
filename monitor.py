import os,time,json,random,requests,pandas as pd
from datetime import datetime,timezone,timedelta

BASE="https://fapi.binance.com"
WEBHOOK=os.getenv("FEISHU_WEBHOOK","")
GAP=.35
TF={
"15m":("15m",250,68,12),
"1H":("1h",250,70,48),
"4H":("4h",250,72,120),
"1D":("1d",250,74,336)
}
CN={"15m":"15分钟","1H":"1小时","4H":"4小时","1D":"1天"}
DIR={"LONG":"做多","SHORT":"做空"}
TYP={"TREND":"趋势","BREAKOUT":"突破"}

cache={}
blocked=False
err429=0

def get(path,p=None):
    global blocked,err429
    if blocked:return
    for i in range(4):
        time.sleep(GAP+random.random()*.15)
        try:
            r=requests.get(BASE+path,params=p,timeout=15)
            if r.status_code==451:
                print("[API] HTTP 451：当前运行环境无法访问 Binance Futures")
                blocked=True
                return
            if r.status_code in (403,418):
                print(f"[API] HTTP {r.status_code}")
                blocked=True
                return
            if r.status_code==429:
                err429+=1
                print(f"[API] HTTP 429 #{err429}")
                time.sleep(min(5*2**i,40)+random.random()*2)
                if err429>=3:
                    blocked=True
                    return
                continue
            if r.status_code>=500:
                time.sleep(2+i)
                continue
            r.raise_for_status()
            try:
                x=r.json()
            except ValueError:
                print(f"[API] 非JSON响应 HTTP {r.status_code}")
                continue
            err429=0
            return x
        except requests.RequestException as e:
            print("[API]",type(e).__name__)
            time.sleep(2+i)
    return

def coins(n=150):
    a=get("/fapi/v1/exchangeInfo")
    b=get("/fapi/v1/ticker/24hr")
    if not a or not b:return []
    ok={x["symbol"] for x in a["symbols"]
        if x.get("quoteAsset")=="USDT"
        and x.get("status")=="TRADING"
        and x.get("contractType")=="PERPETUAL"}
    v={x["symbol"]:float(x.get("quoteVolume",0) or 0) for x in b}
    return sorted(ok,key=lambda x:v.get(x,0),reverse=True)[:n]

def candles(sym,bar,limit=250):
    k=f"{sym}_{bar}"
    if k in cache and time.time()-cache[k][0]<30:return cache[k][1]
    x=get("/fapi/v1/klines",{"symbol":sym,"interval":bar,"limit":limit})
    if not x:return
    d=pd.DataFrame(
        [[int(a[0]),float(a[1]),float(a[2]),float(a[3]),float(a[4]),float(a[5])] for a in x],
        columns=["ts","o","h","l","c","v"])
    cache[k]=(time.time(),d)
    return d

def ema(s,n):return s.ewm(span=n,adjust=False).mean()

def atr(d,n=14):
    p=d.c.shift()
    return pd.concat([d.h-d.l,(d.h-p).abs(),(d.l-p).abs()],axis=1).max(axis=1).rolling(n).mean()

def btc(bar):
    d=candles("BTCUSDT",bar,220)
    if d is None:return 0
    return 15 if d.c.iloc[-1]>ema(d.c,200).iloc[-1] else -15

def signal(d,tf,sym):
    if d is None or len(d)<210:return
    p=d.c.iloc[-1]
    e20,e60,e120,e200=[ema(d.c,n).iloc[-1] for n in (20,60,120,200)]
    a=atr(d).iloc[-1]
    if pd.isna(a):return
    body=abs(d.c.iloc[-1]-d.o.iloc[-1])
    rng=max(d.h.iloc[-1]-d.l.iloc[-1],a*.01)
    vr=d.v.iloc[-1]/d.v.iloc[-21:-1].mean()
    bs=btc(TF[tf][0])
    aa=atr(d).iloc[-6:-1].mean()

    def sc(x):
        s=30 if vr>=2 else 15 if vr>=1.5 else 0
        s+=25 if body/rng>=.7 else 12 if body/rng>=.55 else 0
        s+=20 if a>aa*1.05 else 0
        s+=min(max(bs if x=="LONG" else -bs,0),15)
        s+=10 if (x=="LONG" and p>e20) or (x=="SHORT" and p<e20) else 0
        return s

    out=[]
    if e20>e60>e120 and p>e200:
        sl=p-2.5*a
        tp=min(d.h.iloc[-61:-1].max()*.995,p+3*a)
        rr=(tp-p)/(p-sl);s=sc("LONG")
        if rr>=1.8 and s>=TF[tf][2]:out.append(("LONG","TREND",s,sl,tp,rr))

    if e20<e60<e120 and p<e200:
        sl=p+2.5*a
        tp=max(d.l.iloc[-61:-1].min()*1.005,p-3*a)
        rr=(p-tp)/(sl-p);s=sc("SHORT")
        if rr>=1.8 and s>=TF[tf][2]:out.append(("SHORT","TREND",s,sl,tp,rr))

    hi,lo=d.h.iloc[-21:-1].max(),d.l.iloc[-21:-1].min()

    if p>hi and body/rng>=.55 and vr>=1.5:
        sl=p-2.5*a;tp=min(d.h.iloc[-61:-1].max()*.995,p+3*a)
        rr=(tp-p)/(p-sl);s=sc("LONG")
        if rr>=1.8 and s>=TF[tf][2]:out.append(("LONG","BREAKOUT",s,sl,tp,rr))

    if p<lo and body/rng>=.55 and vr>=1.5:
        sl=p+2.5*a;tp=max(d.l.iloc[-61:-1].min()*1.005,p-3*a)
        rr=(p-tp)/(sl-p);s=sc("SHORT")
        if rr>=1.8 and s>=TF[tf][2]:out.append(("SHORT","BREAKOUT",s,sl,tp,rr))

    if not out:return
    x=max(out,key=lambda z:z[2])
    return dict(sym=sym,tf=tf,dir=x[0],type=x[1],score=x[2],
                entry=p,sl=x[3],tp=x[4],rr=x[5])

def load(p,d):
    try:
        with open(p,encoding="utf8") as f:return json.load(f)
    except:return d

def save(p,d):
    with open(p+".tmp","w",encoding="utf8") as f:
        json.dump(d,f,ensure_ascii=False,indent=2)
    os.replace(p+".tmp",p)

def send(s):
    if not WEBHOOK:return False
    text=(
        f"**币种**：{s['sym']}\n"
        f"**周期**：{CN[s['tf']]}\n"
        f"**类型**：{TYP[s['type']]}\n"
        f"**方向**：{DIR[s['dir']]}\n"
        f"**评分**：{s['score']}\n"
        f"**入场**：{s['entry']:.8g}\n"
        f"**止损**：{s['sl']:.8g}\n"
        f"**止盈**：{s['tp']:.8g}\n"
        f"**盈亏比**：{s['rr']:.2f}\n"
        f"**时间**：{s['time']}"
    )
    p={"msg_type":"interactive","card":{
        "header":{"title":{"tag":"lark_md","content":"**宝宝巴士🚌快上车**"}},
        "elements":[{"tag":"div","text":{"tag":"lark_md","content":text}}]}}
    time.sleep(2+random.random())
    for i in range(3):
        try:
            r=requests.post(WEBHOOK,json=p,timeout=12)
            if r.status_code==429:
                time.sleep(min(5*2**i,30));continue
            return r.ok
        except:
            time.sleep(2+i)
    return False

def evaluate(s):
    d=candles(s["sym"],TF[s["tf"]][0],300)
    if d is None:return
    unit={"15m":15,"1H":60,"4H":240,"1D":1440}[s["tf"]]
    bars=max(1,int(TF[s["tf"]][3]*60/unit))
    f=d[d.ts>s["ts"]].iloc[:bars]
    for _,r in f.iterrows():
        sl,tp=(r.l<=s["sl"],r.h>=s["tp"]) if s["dir"]=="LONG" else (r.h>=s["sl"],r.l<=s["tp"])
        if sl:return "LOSS"
        if tp:return "WIN"
    return "EXPIRED" if len(f)>=bars else None

def main():
    if blocked:return
    sent=load("sent_cache.json",{})
    records=load("signals_record.json",[])
    cut=(datetime.now(timezone.utc)-timedelta(hours=48)).timestamp()
    sent={k:v for k,v in sent.items() if v>cut}

    syms=coins()
    if not syms:
        print("[错误] Binance Futures API 无法访问")
        raise SystemExit(1)

    print(f"[启动] {len(syms)}个币种")
    ids={x.get("id") for x in records}

    for tf,(bar,limit,need,_) in TF.items():
        if blocked:break
        now=datetime.now(timezone.utc)
        if tf=="1D" and not(now.hour==0 and 30<=now.minute<45):continue
        if tf=="4H" and not(now.hour%4==0 and 15<=now.minute<30):continue
        if tf=="1H" and now.minute>=15:continue

        print(f"[扫描] {tf}")
        for sym in syms:
            if blocked:break
            d=candles(sym,bar,limit)
            s=signal(d,tf,sym)
            if not s:continue

            s["ts"]=int(d.ts.iloc[-1])
            s["time"]=datetime.fromtimestamp(s["ts"]/1000,timezone.utc).strftime("%Y-%m-%d %H:%M")
            sid=f"{sym}|{tf}|{s['dir']}|{s['type']}|{s['ts']}"

            print(f"→ {sym} {s['dir']} {s['type']} {s['score']}分")

            if sid not in ids:
                records.append({"id":sid,**s,"result":None,"created":int(time.time())})
                ids.add(sid)

            if sid not in sent and send(s):
                sent[sid]=int(time.time())

        save("sent_cache.json",sent)
        save("signals_record.json",records)

    for r in records:
        if not r.get("result"):
            x=evaluate(r)
            if x:
                r["result"]=x
                r["result_time"]=int(time.time())

    save("sent_cache.json",sent)
    save("signals_record.json",records)
    print("[完成] 本轮扫描结束")

if __name__=="__main__":
    main()
