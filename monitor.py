# 宝宝巴士🚌上车就赚 - OKX 信号扫描
# 只做 15m / 1H / 1D
# 15m 埋伏=快进快出（贴前高、近止盈、4根不破就撤）
# 1H 主力  1D 少量
import os,time,json,random,requests,pandas as pd
from datetime import datetime,timezone,timedelta

BASE="https://openapi.okx.com"
WEBHOOK=os.getenv("FEISHU_WEBHOOK","")
BJ_TZ=timezone(timedelta(hours=8))
STATE="bus_state.json"

TF={
 "15m":("15m",250,85,4),
 "1H":("1H",250,80,6),
 "1D":("1D",250,78,4)
}
DIST={"15m":.01,"1H":.018,"1D":.03}
EXPIRE={"15m":4,"1H":6,"1D":4}
DEDUP_BARS=8
COOL_BARS=12
CN={"15m":"15分钟","1H":"1小时","1D":"1天"}
DIR={"LONG":"做多","SHORT":"做空"}
TYP={"PREPARE":"启动前埋伏","TREND":"趋势","BREAKOUT":"突破"}

cache={};btc_cache={};stop=False

def now_bj():
    return datetime.now(BJ_TZ)

def get(path,p):
    global stop
    if stop:return
    for i in range(5):
        try:
            time.sleep(.15+random.random()*.15)
            r=requests.get(BASE+path,params=p,timeout=15)
            if r.status_code in (403,451):
                stop=True
                print("OKX HTTP",r.status_code)
                return
            if r.status_code==429 or r.status_code>=500:
                time.sleep(min(3*2**i,30)+random.random())
                continue
            x=r.json()
            if x.get("code")=="0":return x.get("data",[])
            if x.get("code")=="50011":
                time.sleep(min(3*2**i,30)+random.random())
                continue
            return
        except:
            time.sleep(min(2**i*2,20))

def coins():
    a=get("/api/v5/public/instruments",{"instType":"SWAP"})
    b=get("/api/v5/market/tickers",{"instType":"SWAP"})
    if not a or not b:return []
    live={x["instId"] for x in a if x.get("settleCcy")=="USDT" and x.get("state")=="live"}
    vol={x["instId"]:float(x.get("volCcy24h",0) or 0) for x in b}
    return sorted(live,key=lambda x:vol.get(x,0),reverse=True)[:150]

def candles(sym,bar,n=250):
    k=(sym,bar,n)
    if k in cache and time.time()-cache[k][0]<30:return cache[k][1]
    x=get("/api/v5/market/candles",{"instId":sym,"bar":bar,"limit":str(n)})
    if not x:return
    try:
        d=pd.DataFrame(
            [[int(a[0]),*map(float,a[1:6]),a[8]] for a in reversed(x)],
            columns=["ts","o","h","l","c","v","ok"]
        )
        d=d[d.ok=="1"].reset_index(drop=True)
        if len(d)<210:return
        cache[k]=(time.time(),d)
        return d
    except:
        return

def ema(s,n):
    return s.ewm(span=n,adjust=False).mean()

def atr(d):
    p=d.c.shift()
    return pd.concat([
        d.h-d.l,(d.h-p).abs(),(d.l-p).abs()
    ],axis=1).max(axis=1).rolling(14).mean()

def btc(bar):
    if bar in btc_cache:return btc_cache[bar]
    d=candles("BTC-USDT-SWAP",bar,220)
    if d is None:
        btc_cache[bar]=0
        return 0
    p=d.c.iloc[-1]
    e20=ema(d.c,20).iloc[-1]
    lo3=d.l.iloc[-3:].min()
    lo6=d.l.iloc[-6:-3].min()
    hi3=d.h.iloc[-3:].max()
    hi6=d.h.iloc[-6:-3].max()
    if p>e20 and lo3>=lo6: v=15
    elif p<e20 and hi3<=hi6: v=-15
    else: v=0
    btc_cache[bar]=v
    return v

def bars_since(d,ts):
    x=d[d.ts>=ts]
    return max(int(len(x))-1,0)

def load_state():
    try:
        with open(STATE,encoding="utf8") as f:return json.load(f)
    except:
        return {"sent":{},"cool":{},"live":[]}

def save_state(st):
    tmp=STATE+".tmp"
    with open(tmp,"w",encoding="utf8") as f:
        json.dump(st,f,ensure_ascii=False)
    os.replace(tmp,STATE)

def sdk(sym,tf,side):
    return f"{sym}|{tf}|{side}"

def too_soon(st,sym,tf,side,d,bars):
    key=sdk(sym,tf,side)
    ts=st.get("cool",{}).get(key) or st.get("sent",{}).get(key)
    if not ts:return False
    return bars_since(d,int(ts))<bars

def mark(st,sym,tf,side,d,where="sent"):
    st.setdefault(where,{})[sdk(sym,tf,side)]=int(d.ts.iloc[-1])

def prepare(d,tf,sym):
    p=d.c.iloc[-1]
    a=atr(d)
    av=a.iloc[-1]
    base=a.iloc[-21:-1].mean()
    if pd.isna(av) or pd.isna(base):return

    e20=ema(d.c,20)
    e60=ema(d.c,60)
    hi=d.h.iloc[-11:-1].max()
    lo=d.l.iloc[-11:-1].min()
    vr=d.v.iloc[-1]/max(d.v.iloc[-21:-1].mean(),1e-12)
    vr3=d.v.iloc[-3:].mean()/max(d.v.iloc[-13:-3].mean(),1e-12)
    ar=av/base
    body=abs(p-d.o.iloc[-1])/max(d.h.iloc[-1]-d.l.iloc[-1],av*.01)
    bs=btc(TF[tf][0])
    lows=d.l.iloc[-6:-1].values
    highs=d.h.iloc[-6:-1].values
    out=[]

    for side in ("LONG","SHORT"):
        dist=(hi-p)/p if side=="LONG" else (p-lo)/p
        cap=.005 if tf=="15m" else DIST[tf]
        if dist<=0 or dist>cap:continue
        if (p>=hi if side=="LONG" else p<=lo) or vr>=1.5 or ar>1.05 or body>=.55:
            continue
        if tf=="15m":
            if (side=="LONG" and bs<=0) or (side=="SHORT" and bs>=0):
                continue
        else:
            if (side=="LONG" and bs<0) or (side=="SHORT" and bs>0):
                continue
        if side=="LONG" and e20.iloc[-1]<=e60.iloc[-1]:continue
        if side=="SHORT" and e20.iloc[-1]>=e60.iloc[-1]:continue
        if side=="LONG" and p<e20.iloc[-1]:continue
        if side=="SHORT" and p>e20.iloc[-1]:continue

        struct=(
            lows[-1]>=lows[0] and lows[-1]>=lows[-2]
            if side=="LONG" else
            highs[-1]<=highs[0] and highs[-1]<=highs[-2]
        )
        if not struct:continue

        trend=(
            e20.iloc[-1]>e60.iloc[-1] and p>e20.iloc[-1] and e20.iloc[-1]>e20.iloc[-4]
            if side=="LONG" else
            e20.iloc[-1]<e60.iloc[-1] and p<e20.iloc[-1] and e20.iloc[-1]<e20.iloc[-4]
        )

        score=0
        score+=18 if ar<=.90 else 12 if ar<=1.0 else 6 if ar<=1.05 else 2
        if tf=="15m":
            score+=16 if dist<=.003 else 12 if dist<=.005 else 0
        else:
            score+=16 if dist<=.0075 else 10 if dist<=.012 else 5 if dist<=.018 else 2
        score+=15
        score+=14 if trend else 8
        score+=14 if .7<=vr<1.5 and vr3>=1.05 else 8 if .6<=vr<1.5 and vr3>=1 else 0
        score+=8 if (bs>0 if side=="LONG" else bs<0) else 4 if bs==0 else 0
        score+=5 if body<.35 and ar<=1 else 3 if body<.45 else 0
        if side=="LONG" and e20.iloc[-1]<e20.iloc[-5]:score-=10
        if side=="SHORT" and e20.iloc[-1]>e20.iloc[-5]:score-=10

        if side=="LONG":
            structure_sl=float(min(lows))
            sl=structure_sl-1.0*av
            if p-sl<0.8*av:sl=p-0.8*av
            risk=p-sl
            if risk<=0:continue
            if tf=="15m":
                tp=p+1.8*risk
                tag=hi*1.002
                if tag>p:
                    r=(tag-p)/risk
                    if 1.5<=r<=2.0:tp=tag
            elif tf=="1D":
                sl=min(sl,p-2*av)
                risk=p-sl
                tp=p+2*risk
            else:
                pressure=d.h.iloc[-61:-11].max()
                tp=min(pressure*.99,p+2.2*risk)
            rr=(tp-p)/risk
        else:
            structure_sl=float(max(highs))
            sl=structure_sl+1.0*av
            if sl-p<0.8*av:sl=p+0.8*av
            risk=sl-p
            if risk<=0:continue
            if tf=="15m":
                tp=p-1.8*risk
                tag=lo*.998
                if tag<p:
                    r=(p-tag)/risk
                    if 1.5<=r<=2.0:tp=tag
            elif tf=="1D":
                sl=max(sl,p+2*av)
                risk=sl-p
                tp=p-2*risk
            else:
                support=d.l.iloc[-61:-11].min()
                tp=max(support*1.01,p-2.2*risk)
            rr=(p-tp)/risk

        need=85 if tf=="15m" else 80 if tf=="1H" else 78
        need_rr=1.8
        if score>=need and rr>=need_rr:
            out.append({
                "sym":sym,"tf":tf,"dir":side,"type":"PREPARE",
                "score":int(score),"entry":p,"sl":sl,"tp":tp,"rr":rr,
                "anchor":int(d.ts.iloc[-11]),
                "expire":EXPIRE[tf],"hi":float(hi),"lo":float(lo)
            })
    return max(out,key=lambda x:x["score"]) if out else None

def normal(d,tf,sym):
    p=d.c.iloc[-1]
    e20,e60,e120,e200=[ema(d.c,n).iloc[-1] for n in (20,60,120,200)]
    a=atr(d).iloc[-1]
    aa=atr(d).iloc[-6:-1].mean()
    vr=d.v.iloc[-1]/max(d.v.iloc[-21:-1].mean(),1e-12)
    body=abs(p-d.o.iloc[-1])/max(d.h.iloc[-1]-d.l.iloc[-1],a*.01)
    bs=btc(TF[tf][0])
    if pd.isna(a) or pd.isna(aa):return
    out=[]

    def add(side,typ,sl,tp):
        if sl==p or tp==p:return
        rr=(tp-p)/(p-sl) if side=="LONG" else (p-tp)/(sl-p)
        if rr<=0:return
        s=(
            30 if vr>=2 else 15 if vr>=1.5 else 0
        )+(
            25 if body>=.6 else 12 if body>=.45 else 0
        )+(20 if a>aa*1.05 else 0)+min(max(bs if side=="LONG" else -bs,0),15)
        if side=="LONG" and p>e20:s+=10
        if side=="SHORT" and p<e20:s+=10
        need=TF[tf][2]
        need_rr=1.8
        if rr>=need_rr and s>=need:
            out.append({
                "sym":sym,"tf":tf,"dir":side,"type":typ,
                "score":int(s),"entry":p,"sl":sl,"tp":tp,"rr":rr,
                "expire":EXPIRE[tf]
            })

    if tf!="15m":
        if e20>e60>e120 and p>e200:
            add("LONG","TREND",p-2.5*a,min(d.h.iloc[-61:-1].max()*.99,p+3*a))
        if e20<e60<e120 and p<e200:
            add("SHORT","TREND",p+2.5*a,max(d.l.iloc[-61:-1].min()*1.01,p-3*a))

    hi=d.h.iloc[-12:-2].max()
    lo=d.l.iloc[-12:-2].min()
    if d.c.iloc[-2]>hi and d.l.iloc[-1]>hi and d.c.iloc[-1]>hi and vr>=1.3:
        sl=d.l.iloc[-2]-.3*a
        risk=p-sl
        if risk>0:
            add("LONG","BREAKOUT",sl,p+1.5*risk)
    if d.c.iloc[-2]<lo and d.h.iloc[-1]<lo and d.c.iloc[-1]<lo and vr>=1.3:
        sl=d.h.iloc[-2]+.3*a
        risk=sl-p
        if risk>0:
            add("SHORT","BREAKOUT",sl,p-1.5*risk)
    return max(out,key=lambda x:x["score"]) if out else None

def signal(d,tf,sym):
    p=prepare(d,tf,sym)
    n=None if (tf=="15m" and p) else normal(d,tf,sym)
    if p and n:return p if p["score"]>=n["score"] else n
    return p or n

def send(s,tag="信号"):
    if not WEBHOOK:return False
    if s.get("cancel"):
        txt=(
            f"宝宝巴士🚌上车就赚\n"
            f"⚪️ 警报·超时撤销 {CN[s['tf']]} {s['sym']}\n"
            f"{DIR[s['dir']]} 埋伏 {EXPIRE[s['tf']]}根内未破前高，撤单"
        )
    else:
        title=(
            "🟡 启动前埋伏" if s["type"]=="PREPARE"
            else "🟢 突破启动" if s["type"]=="BREAKOUT"
            else "🔵 趋势信号"
        )
        if s["type"]=="PREPARE" and s["tf"]=="15m":
            status=f"快进快出，贴前高埋伏，{s.get('expire',4)}根内不破前高撤单"
        elif s["type"]=="PREPARE":
            status=f"启动前埋伏，{s.get('expire',6)}根内不启动就撤"
        elif s["type"]=="BREAKOUT":
            status="突破已站稳"
        else:
            status="趋势跟随"
        txt=(
            f"宝宝巴士🚌上车就赚\n"
            f"🚨 警报 {title}  {CN[s['tf']]}  {s['sym']}\n"
            f"{DIR[s['dir']]}  {TYP[s['type']]}  分数{s['score']}  盈亏比{s['rr']:.2f}\n"
            f"入场{s['entry']:.8g}  止损{s['sl']:.8g}  止盈{s['tp']:.8g}\n"
            f"{status}\n"
            f"{now_bj().strftime('%Y-%m-%d %H:%M:%S')}"
        )
    try:
        r=requests.post(WEBHOOK,json={"msg_type":"text","content":{"text":txt}},timeout=15)
        return r.status_code==200
    except:
        return False

def rank(s):
    pri={"1H":0,"1D":1,"15m":2}[s["tf"]]
    return (pri,-s["score"])

def manage_live(st,syms):
    keep=[]
    for s in st.get("live") or []:
        tf=s.get("tf")
        if tf not in TF:continue
        d=candles(s["sym"],TF[tf][0],250)
        if d is None:
            keep.append(s)
            continue
        n=bars_since(d,int(s.get("sent_ts") or s.get("anchor") or d.ts.iloc[-1]))
        p=d.c.iloc[-1]
        hi=d.h.iloc[d.ts>=int(s.get("sent_ts",d.ts.iloc[-1]))].max() if len(d) else p
        lo=d.l.iloc[d.ts>=int(s.get("sent_ts",d.ts.iloc[-1]))].min() if len(d) else p
        sl,tp,side=s["sl"],s["tp"],s["dir"]
        hit_sl=(lo<=sl) if side=="LONG" else (hi>=sl)
        hit_tp=(hi>=tp) if side=="LONG" else (lo<=tp)
        if hit_sl:
            mark(st,s["sym"],tf,side,d,"cool")
            print("SL",s["sym"],tf)
            continue
        if hit_tp:
            print("TP",s["sym"],tf)
            continue
        if s["type"]=="PREPARE" and n>=s.get("expire",EXPIRE[tf]):
            broken=(hi>=s.get("hi",tp) if side=="LONG" else lo<=s.get("lo",tp))
            if not broken:
                s=dict(s);s["cancel"]=True
                send(s)
                mark(st,s["sym"],tf,side,d,"cool")
                print("EXPIRE",s["sym"],tf)
                continue
        keep.append(s)
    st["live"]=keep

def scan():
    st=load_state()
    ss=coins()
    if not ss:
        print("no coins")
        return
    manage_live(st,ss)
    hits=[]
    for tf,(bar,n,_,_) in TF.items():
        for sym in ss:
            if stop:break
            d=candles(sym,bar,n)
            if d is None:continue
            s=signal(d,tf,sym)
            if not s:continue
            if too_soon(st,s["sym"],tf,s["dir"],d,DEDUP_BARS):continue
            if too_soon(st,s["sym"],tf,s["dir"],d,COOL_BARS) and sdk(s["sym"],tf,s["dir"]) in st.get("cool",{}):
                if bars_since(d,int(st["cool"][sdk(s["sym"],tf,s["dir"])]))<COOL_BARS:
                    continue
            s["sent_ts"]=int(d.ts.iloc[-1])
            hits.append(s)
        if stop:break

    hits.sort(key=rank)
    picked=hits

    for s in picked:
        if send(s):
            d=candles(s["sym"],TF[s["tf"]][0],220)
            if d is not None:mark(st,s["sym"],s["tf"],s["dir"],d,"sent")
            st.setdefault("live",[]).append({
                k:s[k] for k in ("sym","tf","dir","type","entry","sl","tp","score","rr","expire","hi","lo","sent_ts") if k in s
            })
            print("SEND",s["tf"],s["sym"],s["dir"],s["type"],s["score"])
        time.sleep(.2)
    save_state(st)
    print(now_bj().strftime("%H:%M:%S"),"hits",len(hits),"sent",len(picked))

if __name__=="__main__":
    scan()
