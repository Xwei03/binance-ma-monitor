# 宝宝巴士🚌上车就赚 - OKX 多周期信号扫描
# 15m / 1H / 4H / 1D | 埋伏 + 趋势 + 突破 | 飞书推送
import os,time,json,random,requests,pandas as pd,subprocess
from datetime import datetime,timezone,timedelta

BASE="https://openapi.okx.com"
WEBHOOK=os.getenv("FEISHU_WEBHOOK","")
BJ_TZ=timezone(timedelta(hours=8))

TF={
 "15m":("15m",250,60,12),
 "1H":("1H",250,62,48),
 "4H":("4H",250,65,120),
 "1D":("1D",250,68,336)
}
DIST={"15m":.02,"1H":.025,"4H":.03,"1D":.035}
CN={"15m":"15分钟","1H":"1小时","4H":"4小时","1D":"1天"}
DIR={"LONG":"做多","SHORT":"做空"}
TYP={"PREPARE":"启动前埋伏","TREND":"趋势","BREAKOUT":"突破"}

cache={};btc_cache={};stop=False

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
    if d is None:return 0
    btc_cache[bar]=15 if d.c.iloc[-1]>ema(d.c,200).iloc[-1] else -15
    return btc_cache[bar]

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
        max_dist=min(DIST[tf]*max(ar,.6),.06)
        if dist<=0 or dist>max_dist:continue

        if (
            (p>=hi if side=="LONG" else p<=lo)
            or vr>=1.5 or ar>1.05 or body>=.55
        ):
            continue

        struct=(
            lows[-1]>=lows[0] and lows[-1]>=lows[-2]
            if side=="LONG"
            else
            highs[-1]<=highs[0] and highs[-1]<=highs[-2]
        )

        weak=(
            lows[-1]>=lows[0]
            if side=="LONG"
            else
            highs[-1]<=highs[0]
        )

        trend=(
            e20.iloc[-1]>e60.iloc[-1] and
            p>e20.iloc[-1] and
            e20.iloc[-1]>e20.iloc[-4]
            if side=="LONG"
            else
            e20.iloc[-1]<e60.iloc[-1] and
            p<e20.iloc[-1] and
            e20.iloc[-1]<e20.iloc[-4]
        )

        score=0

        score+=20 if ar<=.85 else 15 if ar<=.95 else 10 if ar<=1 else 5
        score+=15 if dist<=.0075 else 12 if dist<=.012 else 8 if dist<=.018 else 4
        score+=15 if struct else 8 if weak else 0
        score+=10 if trend else 7 if (
            e20.iloc[-1]>e60.iloc[-1]
            if side=="LONG"
            else e20.iloc[-1]<e60.iloc[-1]
        ) else 3
        score+=10 if .7<=vr<1.5 and vr3>=1.05 else 6 if .6<=vr<1.5 and vr3>=1 else 0

        if side=="LONG":
            pressure=d.h.iloc[-61:-11].max()
            space=(pressure-hi)/hi if pressure>hi else 0
        else:
            support=d.l.iloc[-61:-11].min()
            space=(lo-support)/lo if support<lo else 0

        score+=10 if space>=.04 else 8 if space>=.025 else 6 if space>=.015 else 3 if space>=.008 else 0
        score+=10 if (bs>0 if side=="LONG" else bs<0) else 5 if bs==0 else 0
        score+=5 if body<.35 and ar<=1 else 3 if body<.45 else 0

        if side=="LONG":
            structure_sl=lows.min()
            sl=structure_sl-.5*av
            tp=pressure*.995
            rr=(tp-p)/(p-sl)
        else:
            structure_sl=highs.max()
            sl=structure_sl+.5*av
            tp=support*1.005
            rr=(p-tp)/(sl-p)

        if score>=70 and rr>=1.8:
            out.append({
                "sym":sym,"tf":tf,"dir":side,"type":"PREPARE",
                "score":score,"entry":p,"sl":sl,"tp":tp,"rr":rr,
                "anchor":int(d.ts.iloc[-11])
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
    out=[]

    def add(side,typ,sl,tp):
        rr=(tp-p)/(p-sl) if side=="LONG" else (p-tp)/(sl-p)
        s=(
            30 if vr>=2 else 15 if vr>=1.5 else 0
        )+(
            25 if body>=.6 else 12 if body>=.45 else 0
        )+(20 if a>aa*1.05 else 0)+min(max(bs if side=="LONG" else -bs,0),15)+(
            10 if side=="LONG" and p>e20 or side=="SHORT" and p<e20 else 0
        )
        if rr>=1.8 and s>=TF[tf][2]:
            out.append({
                "sym":sym,"tf":tf,"dir":side,"type":typ,
                "score":s,"entry":p,"sl":sl,"tp":tp,"rr":rr
            })

    if e20>e60>e120 and p>e200:
        add("LONG","TREND",p-2.5*a,min(d.h.iloc[-61:-1].max()*.995,p+3*a))

    if e20<e60<e120 and p<e200:
        add("SHORT","TREND",p+2.5*a,max(d.l.iloc[-61:-1].min()*1.005,p-3*a))

    hi=d.h.iloc[-11:-1].max()
    lo=d.l.iloc[-11:-1].min()

    if p>hi and body>=.55 and vr>=1.5:
        add("LONG","BREAKOUT",p-2.5*a,min(d.h.iloc[-61:-1].max()*.995,p+3*a))

    if p<lo and body>=.55 and vr>=1.5:
        add("SHORT","BREAKOUT",p+2.5*a,max(d.l.iloc[-61:-1].min()*1.005,p-3*a))

    return max(out,key=lambda x:x["score"]) if out else None

def signal(d,tf,sym):
    p=prepare(d,tf,sym)
    return p or normal(d,tf,sym)

def load(path,default):
    try:
        with open(path,encoding="utf8") as f:return json.load(f)
    except:return default

def save(path,data):
    with open(path+".tmp","w",encoding="utf8") as f:
        json.dump(data,f,ensure_ascii=False)
    os.replace(path+".tmp",path)

def send(s):
    if not WEBHOOK:return False

    title=(
        "🟡 启动前埋伏" if s["type"]=="PREPARE"
        else "🟢 突破启动" if s["type"]=="BREAKOUT"
        else "🔵 趋势信号"
    )

    status=(
        "尚未启动，提前埋伏，等待行情启动"
        if s["type"]=="PREPARE"
        else "行情已经启动"
    )

    text="\n".join([
        f"**🚨 警报**",
        f"**{title}**",
        f"**币种**：{s['sym']}",
        f"**周期**：{CN[s['tf']]}",
        f"**方向**：{DIR[s['dir']]}",
        f"**评分**：{s['score']}/100",
        f"**入场**：{s['entry']:.8g}",
        f"**止损**：{s['sl']:.8g}",
        f"**止盈**：{s['tp']:.8g}",
        f"**盈亏比**：{s['rr']:.2f}",
        f"**状态**：{status}",
        f"**时间**：{s['time']}"
    ])

    data={
        "msg_type":"interactive",
        "card":{
            "header":{
                "title":{
                    "tag":"lark_md",
                    "content":"**宝宝巴士🚌上车就赚 警报**"
                }
            },
            "elements":[
                {"tag":"div","text":{"tag":"lark_md","content":text}}
            ]
        }
    }

    time.sleep(2+random.random())

    for i in range(4):
        try:
            r=requests.post(WEBHOOK,json=data,timeout=12)
            try:
                res=r.json()
            except:
                res={}

            if r.status_code==200 and res.get("code")==0:
                print(f"[飞书] 已发送 {s['sym']} {s['tf']} {s['type']}")
                return True
            else:
                print(f"[飞书] 发送被拒! HTTP:{r.status_code} 返回:{r.text}")
                return False
        except Exception as e:
            print(f"[飞书] {type(e).__name__}")
            time.sleep(min(3*(i+1),15))

    return False

def evaluate(s):
    d=candles(s["sym"],TF[s["tf"]][0],300)
    if d is None:return

    unit={"15m":15,"1H":60,"4H":240,"1D":1440}[s["tf"]]
    bars=int(TF[s["tf"]][3]*60/unit)
    f=d[d.ts>s["ts"]].iloc[:bars]

    for _,r in f.iterrows():
        sl=r.l<=s["sl"] if s["dir"]=="LONG" else r.h>=s["sl"]
        tp=r.h>=s["tp"] if s["dir"]=="LONG" else r.l<=s["tp"]
        if tp:return "WIN"
        if sl:return "LOSS"

    return "EXPIRED" if len(f)>=bars else None

def git_save():
    try:
        subprocess.run(["git","config","user.name","github-actions"],capture_output=True)
        subprocess.run([
            "git","config","user.email",
            "41898282+github-actions[bot]@users.noreply.github.com"
        ],capture_output=True)
        subprocess.run([
            "git","add","sent_cache.json","signals_record.json"
        ],capture_output=True)

        x=subprocess.run(["git","diff","--cached","--quiet"],capture_output=True)

        if x.returncode!=0:
            subprocess.run(["git","commit","-m","auto: update signals"],capture_output=True)
            subprocess.run(["git","push"],capture_output=True)
            print("[Git] 已提交并推送")
    except:
        pass

def main():
    global btc_cache

    print("="*40)
    print(f"[开始] {datetime.now(BJ_TZ).strftime('%Y-%m-%d %H:%M:%S')} 北京时间")
    print("="*40)

    sent=load("sent_cache.json",{})
    records=load("signals_record.json",[])

    if isinstance(records,dict):
        records=list(records.values())

    cut=(datetime.now(timezone.utc)-timedelta(hours=48)).timestamp()
    sent={k:v for k,v in sent.items() if v>cut}

    syms=coins()
    if not syms:
        raise SystemExit("OKX无法获取永续合约")

    print(f"[启动] OKX {len(syms)} 个USDT永续")

    ids={x.get("id") for x in records}

    for tf,(bar,limit,_,_) in TF.items():

        now=datetime.now(timezone.utc)

        run=(
            tf=="15m" or
            tf=="1H" and now.minute<15 or
            tf=="4H" and now.hour%4==0 and 15<=now.minute<30 or
            tf=="1D" and now.hour==0 and 30<=now.minute<45
        )

        if not run:
            print(f"[{tf}] 未到触发时间，跳过")
            continue

        print(f"[扫描] {tf}")
        btc_cache={}
        ns=ne=0

        for sym in syms:

            if stop:
                print(f"[中断] 触发限流保护，停止扫描 {tf}")
                break

            d=candles(sym,bar,limit)
            if d is None:continue

            s=signal(d,tf,sym)
            if not s:continue

            s["ts"]=int(d.ts.iloc[-1])
            s["time"]=datetime.fromtimestamp(
                s["ts"]/1000,BJ_TZ
            ).strftime("%Y-%m-%d %H:%M")

            sid=(
                f"PREPARE|{sym}|{tf}|{s['dir']}|{s['anchor']}"
                if s["type"]=="PREPARE"
                else
                f"{sym}|{tf}|{s['dir']}|{s['type']}|{s['ts']}"
            )

            ns+=1
            print(f"→ {sym} {s['dir']} {s['type']} {s['score']}分")

            if sid not in ids:
                records.append({
                    "id":sid,**s,
                    "result":None,
                    "created":int(time.time())
                })
                ids.add(sid)

            if sid not in sent and send(s):
                sent[sid]=int(time.time())
                ne+=1

        if ns:
            print(f"[{tf}] 发现 {ns} 个信号，已发送 {ne} 条")
        else:
            print(f"[{tf}] 无信号")

        save("sent_cache.json",sent)
        save("signals_record.json",records)

    print("[评估] 检查历史信号结果...")
    ev=0
    for r in records:
        if not r.get("result"):
            x=evaluate(r)
            if x:
                r["result"]=x
                r["result_time"]=int(time.time())
                ev+=1
                print(f"  ← {r['sym']} {r['tf']} {x}")
    print(f"[评估] 更新 {ev} 条结果" if ev else "[评估] 无新结果")

    save("sent_cache.json",sent)
    save("signals_record.json",records)
    git_save()

    print("="*40)
    print(f"[完成] {datetime.now(BJ_TZ).strftime('%Y-%m-%d %H:%M:%S')} 北京时间")
    print(f"[统计] 监控币种 {len(syms)}，历史信号 {len(records)}")
    print("="*40)

if __name__=="__main__":
    main()
