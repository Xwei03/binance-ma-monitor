# 宝宝巴士🚌上车就赚 - OKX 信号扫描 (15m/1H/1D)
import os,time,json,random,requests,pandas as pd
from datetime import datetime,timezone,timedelta

BASE="https://openapi.okx.com"
WEBHOOK=os.getenv("FEISHU_WEBHOOK","")
PROXY=os.getenv("OKX_PROXY","")
BJ_TZ=timezone(timedelta(hours=8))
STATE="bus_state.json"

UAS=[
 "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
 "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
 "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
 "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
 "Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1",
]

def hdr():
    return {
        "User-Agent":random.choice(UAS),
        "Accept":"application/json, text/plain, */*",
        "Accept-Language":"zh-CN,zh;q=0.9,en;q=0.8",
        "Connection":"keep-alive",
        "Referer":"https://www.okx.com/",
    }

TF={"15m":("15m",250,75,4),"1H":("1H",250,75,6),"1D":("1D",250,72,4)}
DIST={"15m":.015,"1H":.025,"1D":.04}
MAX_HOLD={"15m":100,"1H":100,"1D":30}
TREND_SL={"15m":1.5,"1H":2.0,"1D":2.5}
DEDUP_BARS,COOL_BARS=8,12
KEEP_SENT_DAYS,KEEP_RESULT_DAYS=110,7
MAX_SEND_PER_SCAN,MAX_LIVE=20,200
MIN_VOL=10000000
CN={"15m":"15分钟","1H":"1小时","1D":"1天"}
DIR={"LONG":"做多","SHORT":"做空"}
TYP={"PREPARE":"启动前埋伏","TREND":"趋势","BREAKOUT":"突破"}

cache={};btc_cache={};stop=False
last_req=[0.0]

def now_bj():return datetime.now(BJ_TZ)

def tf_run(tf):
    n=datetime.now(timezone.utc)
    if tf=="15m":return True
    if tf=="1H":return n.minute<20
    if tf=="1D":return n.hour==0 and 10<=n.minute<30
    return True

def throttle():
    gap=.3+random.random()*.3
    wait=last_req[0]+gap-time.time()
    if wait>0:time.sleep(wait)
    last_req[0]=time.time()

def get(path,p):
    global stop
    if stop:return
    for i in range(5):
        try:
            throttle()
            kw={"params":p,"timeout":20,"headers":hdr()}
            if PROXY:kw["proxies"]={"http":PROXY,"https":PROXY}
            r=requests.get(BASE+path,**kw)
            if r.status_code in (403,451):
                print(f"[OKX] HTTP {r.status_code} 停止本轮");stop=True;return
            if r.status_code==418:
                print("[OKX] 418 被封 等60s");time.sleep(60);continue
            if r.status_code==429 or r.status_code>=500:
                w=min(3*2**i,30)+random.random()*2
                print(f"[OKX] HTTP {r.status_code} 等{w:.1f}s");time.sleep(w);continue
            x=r.json()
            if x.get("code")=="0":return x.get("data",[])
            if x.get("code")=="50011":
                w=min(3*2**i,30)+random.random()*2
                print(f"[OKX] 50011 等{w:.1f}s");time.sleep(w);continue
            return
        except Exception as e:
            print(f"[OKX] {type(e).__name__}");time.sleep(min(2**i*2,20))

def coins():
    a=get("/api/v5/public/instruments",{"instType":"SWAP"})
    b=get("/api/v5/market/tickers",{"instType":"SWAP"})
    if not a or not b:return []
    live={x["instId"] for x in a if x.get("settleCcy")=="USDT" and x.get("state")=="live"}
    vol={}
    for x in b:
        v=float(x.get("volCcy24h",0) or 0)
        p=float(x.get("last",0) or 0)
        vol[x["instId"]]=v*p
    valid=[s for s in live if vol.get(s,0)>=MIN_VOL]
    r=sorted(valid,key=lambda x:vol.get(x,0),reverse=True)[:150]
    if r:
        print(f"[币种] ≥{MIN_VOL/1e8:.2f}亿的共{len(valid)}个，取{len(r)}个")
    return r

def candles(sym,bar,n=250):
    k=(sym,bar,n)
    if k in cache and time.time()-cache[k][0]<30:return cache[k][1]
    x=get("/api/v5/market/candles",{"instId":sym,"bar":bar,"limit":str(n)})
    if not x:return
    try:
        d=pd.DataFrame([[int(a[0]),*map(float,a[1:6]),a[8]] for a in reversed(x)],
                       columns=["ts","o","h","l","c","v","ok"])
        d=d[d.ok=="1"].reset_index(drop=True)
        if len(d)<10:return
        cache[k]=(time.time(),d);return d
    except:return

def ema(s,n):return s.ewm(span=n,adjust=False).mean()

def atr(d):
    p=d.c.shift()
    return pd.concat([d.h-d.l,(d.h-p).abs(),(d.l-p).abs()],axis=1).max(axis=1).rolling(14).mean()

def near_res(d,p):
    h=d.h.iloc[-201:-1];a=h[h>p]
    return float(a.min()) if len(a) else None

def near_sup(d,p):
    l=d.l.iloc[-201:-1];b=l[l<p]
    return float(b.max()) if len(b) else None

def btc(bar):
    if bar in btc_cache:return btc_cache[bar]
    d=candles("BTC-USDT-SWAP",bar,220)
    if d is None:btc_cache[bar]=0;return 0
    p=d.c.iloc[-1]
    e20=ema(d.c,20).iloc[-1]
    e60=ema(d.c,60).iloc[-1]
    if e20>e60 and p>e20:
        v=15
    elif e20<e60 and p<e20:
        v=-15
    else:
        v=0
    btc_cache[bar]=v
    return v

def bars_since(d,ts):return max(int(len(d[d.ts>=ts]))-1,0)

def load_state():
    try:
        with open(STATE,encoding="utf8") as f:return json.load(f)
    except:return {"sent":{},"cool":{},"live":[],"results":[],"last_report":""}

def save_state(st):
    with open(STATE+".tmp","w",encoding="utf8") as f:json.dump(st,f,ensure_ascii=False)
    os.replace(STATE+".tmp",STATE)

def sdk(sym,tf,side,typ):
    return f"{sym}|{tf}|{side}|{typ}"

def too_soon(st,sym,tf,side,typ,d,bars):
    k=sdk(sym,tf,side,typ)
    ts=st.get("cool",{}).get(k) or st.get("sent",{}).get(k)
    return ts and bars_since(d,int(ts))<bars

def mark(st,sym,tf,side,typ,d,where="sent"):
    st.setdefault(where,{})[sdk(sym,tf,side,typ)]=int(d.ts.iloc[-1])

def rec(st,s,r):
    st.setdefault("results",[]).append({"sym":s["sym"],"tf":s["tf"],"dir":s["dir"],
                                        "type":s["type"],"result":r,"ts":int(time.time())})

def post(txt):
    if not WEBHOOK:return False
    for i in range(3):
        try:
            r=requests.post(WEBHOOK,json={"msg_type":"text","content":{"text":txt}},timeout=15)
            try:res=r.json()
            except:res={}
            if r.status_code==200 and res.get("code")==0:return True
            if r.status_code==429:
                print(f"[飞书] 限流 重试{i+1}/3");time.sleep(min(5*2**i,30));continue
            print(f"[飞书] 被拒 HTTP:{r.status_code} {r.text[:150]}");return False
        except Exception as e:
            print(f"[飞书] {type(e).__name__}");time.sleep(2*(i+1))
    return False

def clean(st):
    c=(int(time.time())-KEEP_SENT_DAYS*86400)*1000
    st["sent"]={k:v for k,v in st.get("sent",{}).items() if v>=c}
    st["cool"]={k:v for k,v in st.get("cool",{}).items() if v>=c}
    kc=int(time.time())-KEEP_RESULT_DAYS*86400
    st["results"]=[r for r in st.get("results",[]) if r["ts"]>=kc]

def daily_report(st):
    n=datetime.now(timezone.utc);today=n.strftime("%Y-%m-%d")
    if n.hour!=0 or st.get("last_report")==today:return
    cut=int(time.time())-86400
    rs=[r for r in st.get("results",[]) if r["ts"]>=cut]
    L=["宝宝巴士🚌上车就赚","📊 每日统计（警报）"];tw=tl=te=0
    combos=[
        ("15m","PREPARE","15m埋伏"),
        ("15m","BREAKOUT","15m突破"),
        ("1H","PREPARE","1H埋伏"),
        ("1H","TREND","1H趋势"),
        ("1H","BREAKOUT","1H突破"),
        ("1D","PREPARE","1D埋伏"),
        ("1D","TREND","1D趋势"),
        ("1D","BREAKOUT","1D突破"),
    ]
    lv=st.get("live",[])
    for tf,typ,lb in combos:
        sub=[r for r in rs if r.get("tf")==tf and r.get("type")==typ]
        w=sum(1 for r in sub if r["result"]=="WIN");l=sum(1 for r in sub if r["result"]=="LOSS");e=sum(1 for r in sub if r["result"]=="ERROR")
        p=sum(1 for x in lv if x.get("tf")==tf and x.get("type")==typ)
        t=w+l;tw+=w;tl+=l;te+=e
        L.append(f"警报 {lb}：止盈{w} 止损{l} 错误{e} 在追{p} 胜率{(w/t*100) if t else 0:.0f}%")
    tt=tw+tl
    L.append(f"警报 合计：止盈{tw} 止损{tl} 错误{te} 在追{len(lv)} 胜率{(tw/tt*100) if tt else 0:.0f}%")
    L.append(f"警报 统计时间：{now_bj().strftime('%Y-%m-%d %H:%M:%S')}")
    if post("\n".join(L)):
        st["last_report"]=today;print(f"DAILY_REPORT",tw,tl,te,len(lv))

def try_res(sym,bar,lim,side,sl,tp,ts0,ts1):
    d=candles(sym,bar,lim)
    if d is None:return None
    f=d[(d.ts>ts0)&(d.ts<=ts1)]
    if len(f)==0:return None
    for _,r in f.iterrows():
        if side=="LONG":hs,ht=r.l<=sl,r.h>=tp
        else:hs,ht=r.h>=sl,r.l<=tp
        if hs and ht:return "LOSS"
        if ht:return "WIN"
        if hs:return "LOSS"
    return None

def resolve(sym,side,sl,tp,ts0,ts1,tf):
    if tf=="1D":
        r=try_res(sym,"5m",300,side,sl,tp,ts0,ts1)
        return r if r is not None else try_res(sym,"1H",300,side,sl,tp,ts0,ts1)
    r=try_res(sym,"1m",300,side,sl,tp,ts0,ts1)
    return r if r is not None else try_res(sym,"5m",300,side,sl,tp,ts0,ts1)

def prepare(d,tf,sym):
    if d is None or len(d)<210:return
    p=d.c.iloc[-1]
    a=atr(d);av=a.iloc[-1];base=a.iloc[-21:-1].mean()
    if pd.isna(av) or pd.isna(base):return
    e20=ema(d.c,20);e60=ema(d.c,60)
    hi=d.h.iloc[-11:-1].max();lo=d.l.iloc[-11:-1].min()
    vr=d.v.iloc[-1]/max(d.v.iloc[-21:-1].mean(),1e-12)
    vr3=d.v.iloc[-3:].mean()/max(d.v.iloc[-13:-3].mean(),1e-12)
    ar=av/base
    body=abs(p-d.o.iloc[-1])/max(d.h.iloc[-1]-d.l.iloc[-1],av*.01)
    bs=btc(TF[tf][0])
    lows=d.l.iloc[-6:-1].values;highs=d.h.iloc[-6:-1].values
    out=[]

    # 动态距离窗口：ATR 压缩时不收紧，ATR 放大时放宽，上限为基准1.5倍
    dist_base=DIST[tf]
    dist_cap=min(dist_base*max(ar,1.0), dist_base*1.5)

    for side in ("LONG","SHORT"):
        dist=(hi-p)/p if side=="LONG" else (p-lo)/p
        if dist<=0 or dist>dist_cap:continue
        if (p>=hi if side=="LONG" else p<=lo) or vr>=1.5 or ar>1.5 or body>=.55:continue
        if tf=="15m":
            if (side=="LONG" and bs<=0) or (side=="SHORT" and bs>=0):continue
        else:
            if (side=="LONG" and bs<0) or (side=="SHORT" and bs>0):continue
        if side=="LONG" and (p<e20.iloc[-1] or e20.iloc[-1]<e20.iloc[-4]):continue
        if side=="SHORT" and (p>e20.iloc[-1] or e20.iloc[-1]>e20.iloc[-4]):continue
        trend=(e20.iloc[-1]>e60.iloc[-1] and p>e20.iloc[-1] and e20.iloc[-1]>e20.iloc[-4]) if side=="LONG" else (e20.iloc[-1]<e60.iloc[-1] and p<e20.iloc[-1] and e20.iloc[-1]<e20.iloc[-4])
        s=0
        s+=18 if ar<=.90 else 12 if ar<=1.0 else 6 if ar<=1.05 else 2
        if tf=="15m":s+=22 if dist<=.003 else 18 if dist<=.010 else 11 if dist<=.015 else 0
        elif tf=="1H":s+=22 if dist<=.0075 else 16 if dist<=.012 else 11 if dist<=.025 else 2
        else:s+=22 if dist<=.0075 else 16 if dist<=.012 else 11 if dist<=.040 else 2
        s+=15
        s+=8
        s+=14 if (.7<=vr<1.5 and vr3>=1.05) else 8 if (.6<=vr<1.5 and vr3>=1) else 0
        s+=8 if ((bs>0) if side=="LONG" else (bs<0)) else 4 if bs==0 else 0
        s+=5 if (body<.35 and ar<=1) else 3 if body<.45 else 0
        if side=="LONG" and e20.iloc[-1]<e20.iloc[-5]:s-=10
        if side=="SHORT" and e20.iloc[-1]>e20.iloc[-5]:s-=10

        if side=="LONG":
            stsl=float(min(lows));sl=stsl-1.0*av
            if p-sl<0.8*av:sl=p-0.8*av
            risk=p-sl
            if risk<=0:continue
            if tf=="1D":
                sl=min(sl,p-2*av);risk=p-sl
            pr=near_res(d,p)
            if pr is not None:
                cd=pr*0.985
                if cd<=p:continue
                tp=cd
            else:
                tp=p+2.0*risk if tf!="1H" else p+2.2*risk
            rr=(tp-p)/risk
        else:
            stsl=float(max(highs));sl=stsl+1.0*av
            if sl-p<0.8*av:sl=p+0.8*av
            risk=sl-p
            if risk<=0:continue
            if tf=="1D":
                sl=max(sl,p+2*av);risk=sl-p
            sp=near_sup(d,p)
            if sp is not None:
                cd=sp*1.015
                if cd>=p:continue
                tp=cd
            else:
                tp=p-2.0*risk if tf!="1H" else p-2.2*risk
            rr=(p-tp)/risk

        need=75 if tf in ("15m","1H") else 72
        need_rr=1.8 if tf in ("15m","1H") else 2.0
        if s>=need and rr>=need_rr:
            out.append({"sym":sym,"tf":tf,"dir":side,"type":"PREPARE","score":int(s),
                        "entry":p,"sl":sl,"tp":tp,"rr":rr,"anchor":int(d.ts.iloc[-11]),
                        "hi":float(hi),"lo":float(lo)})
    return max(out,key=lambda x:x["score"]) if out else None

def normal(d,tf,sym):
    if d is None or len(d)<210:return
    p=d.c.iloc[-1]
    E=[ema(d.c,n).iloc[-1] for n in (20,60,120,200)]
    e20,e60,e120,e200=E
    a=atr(d).iloc[-1];aa=atr(d).iloc[-6:-1].mean()
    if pd.isna(a) or pd.isna(aa):return
    vr=d.v.iloc[-1]/max(d.v.iloc[-21:-1].mean(),1e-12)
    body=abs(p-d.o.iloc[-1])/max(d.h.iloc[-1]-d.l.iloc[-1],a*.01)
    bs=btc(TF[tf][0]);out=[]
    sl_mult=TREND_SL.get(tf,2.0)

    def add(side,typ,sl,tp,need_rr):
        if side=="LONG" and bs<=0:return
        if side=="SHORT" and bs>=0:return
        if sl==p or tp==p:return
        rr=(tp-p)/(p-sl) if side=="LONG" else (p-tp)/(sl-p)
        if rr<=0:return
        s=(30 if vr>=2 else 15 if vr>=1.5 else 0)+(25 if body>=.6 else 12 if body>=.45 else 0)+(20 if a>aa*1.05 else 0)+min(max(bs if side=="LONG" else -bs,0),15)
        if side=="LONG" and p>e20:s+=10
        if side=="SHORT" and p<e20:s+=10
        if rr>=need_rr and s>=TF[tf][2]:
            out.append({"sym":sym,"tf":tf,"dir":side,"type":typ,"score":int(s),
                        "entry":p,"sl":sl,"tp":tp,"rr":rr})

    if tf!="15m":
        if e20>e60>e120 and p>e200:
            add("LONG","TREND",p-sl_mult*a,min(d.h.iloc[-61:-1].max()*0.985,p+3*a),1.2)
        if e20<e60<e120 and p<e200:
            add("SHORT","TREND",p+sl_mult*a,max(d.l.iloc[-61:-1].min()*1.015,p-3*a),1.2)

    hi=d.h.iloc[-12:-2].max();lo=d.l.iloc[-12:-2].min()
    if d.c.iloc[-2]>hi and d.l.iloc[-1]>hi and d.c.iloc[-1]>hi and vr>=1.3:
        sl=d.l.iloc[-2]-.3*a;risk=p-sl
        if risk>0:
            base_tp=p+1.5*risk
            pr=near_res(d,p)
            if pr is not None:
                cd=pr*0.985
                if cd>p:
                    add("LONG","BREAKOUT",sl,min(cd,base_tp),1.5)
            else:
                add("LONG","BREAKOUT",sl,base_tp,1.5)
    if d.c.iloc[-2]<lo and d.h.iloc[-1]<lo and d.c.iloc[-1]<lo and vr>=1.3:
        sl=d.h.iloc[-2]+.3*a;risk=sl-p
        if risk>0:
            base_tp=p-1.5*risk
            sp=near_sup(d,p)
            if sp is not None:
                cd=sp*1.015
                if cd<p:
                    add("SHORT","BREAKOUT",sl,max(cd,base_tp),1.5)
            else:
                add("SHORT","BREAKOUT",sl,base_tp,1.5)
    return max(out,key=lambda x:x["score"]) if out else None

def signal(d,tf,sym):
    p=prepare(d,tf,sym)
    n=normal(d,tf,sym)
    if p and n:return p if p["score"]>=n["score"] else n
    return p or n

def send_signal(st,s):
    t=("🟡 启动前埋伏" if s["type"]=="PREPARE" else "🟢 突破启动" if s["type"]=="BREAKOUT" else "🔵 趋势信号")
    stt="贴前高埋伏" if (s["type"]=="PREPARE" and s["tf"]=="15m") else "启动前埋伏" if s["type"]=="PREPARE" else "突破已站稳" if s["type"]=="BREAKOUT" else "趋势跟随"
    txt=(f"宝宝巴士🚌上车就赚\n🚨 警报 {t}  {CN[s['tf']]}  {s['sym']}\n"
         f"{DIR[s['dir']]}  {TYP[s['type']]}  分数{s['score']}  盈亏比{s['rr']:.2f}\n"
         f"入场{s['entry']:.8g}  止损{s['sl']:.8g}  止盈{s['tp']:.8g}\n{stt}\n"
         f"{now_bj().strftime('%Y-%m-%d %H:%M:%S')}")
    if post(txt):
        key=sdk(s["sym"],s["tf"],s["dir"],s["type"])
        st.setdefault("sent",{})[key]=int(s["sent_ts"])
        live_keys={sdk(x.get("sym",""),x.get("tf",""),x.get("dir",""),x.get("type","")) for x in st.get("live",[])}
        if key not in live_keys:
            st.setdefault("live",[]).append({k:s[k] for k in ("sym","tf","dir","type","entry","sl","tp","score","rr","hi","lo","sent_ts") if k in s})
        print("SEND",s["tf"],s["sym"],s["dir"],s["type"],s["score"])
        return True
    return False

def check_live(st):
    keep=[]
    for s in st.get("live") or []:
        tf=s.get("tf")
        if tf not in TF:continue
        d=candles(s["sym"],TF[tf][0],250)
        if d is None:keep.append(s);continue
        ts0=int(s.get("sent_ts") or s.get("anchor") or d.ts.iloc[-1])
        f=d[d.ts>ts0]
        if len(f)==0:keep.append(s);continue
        hi=f.h.max();lo=f.l.min()
        sl,tp,side=s["sl"],s["tp"],s["dir"];typ=s.get("type","")
        hs=(lo<=sl) if side=="LONG" else (hi>=sl)
        ht=(hi>=tp) if side=="LONG" else (lo<=tp)
        if hs and ht:
            r=resolve(s["sym"],side,sl,tp,ts0,int(time.time()*1000),tf)
            mark(st,s["sym"],tf,side,typ,d,"cool")
            if r is None:
                rec(st,s,"ERROR");print("ERR",s["sym"],tf)
            else:
                rec(st,s,r);print("TPsub" if r=="WIN" else "SLsub",s["sym"],tf)
            continue
        if ht:mark(st,s["sym"],tf,side,typ,d,"cool");rec(st,s,"WIN");print("TP",s["sym"],tf);continue
        if hs:mark(st,s["sym"],tf,side,typ,d,"cool");rec(st,s,"LOSS");print("SL",s["sym"],tf);continue
        if bars_since(d,ts0)>=MAX_HOLD.get(tf,100):print("TIMEOUT",s["sym"],tf);continue
        keep.append(s)
    st["live"]=keep[-MAX_LIVE:]

def scan():
    btc_cache.clear()
    st=load_state();clean(st);daily_report(st)
    ss=coins()
    if not ss:print("no coins");save_state(st);return
    check_live(st)

    for tf,(bar,n,_,_) in TF.items():
        if stop:break
        if not tf_run(tf):
            print(f"[{tf}] 跳过")
            continue
        print(f"[扫描] {tf}")
        hits=[]
        for sym in ss:
            if stop:break
            dd=candles(sym,bar,n)
            s=signal(dd,tf,sym)
            if not s:continue
            if too_soon(st,s["sym"],tf,s["dir"],s["type"],dd,DEDUP_BARS):continue
            key=sdk(s["sym"],tf,s["dir"],s["type"])
            if key in st.get("cool",{}) and bars_since(dd,int(st["cool"][key]))<COOL_BARS:continue
            s["sent_ts"]=int(dd.ts.iloc[-1])
            hits.append(s)
        hits.sort(key=lambda x:-x["score"])
        picked=hits[:MAX_SEND_PER_SCAN]
        cnt=0
        for s in picked:
            if send_signal(st,s):cnt+=1
            time.sleep(.3)
        save_state(st)
        print(f"[{tf}] hits {len(hits)} sent {cnt}")

    print(now_bj().strftime("%H:%M:%S"),"完成")

if __name__=="__main__":
    scan()
