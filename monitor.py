# 宝宝巴士🚌上车就赚 - OKX 多周期信号扫描
# 15m / 1H / 4H / 1D | 100分制评分 | 飞书推送
import os,time,json,random,requests,pandas as pd
from datetime import datetime,timezone,timedelta

BASE="https://openapi.okx.com"
WEBHOOK=os.getenv("FEISHU_WEBHOOK","")
TF={"15m":("15m",250,60,12),"1H":("1H",250,62,48),
    "4H":("4H",250,65,120),"1D":("1D",250,68,336)}
CN={"15m":"15分钟","1H":"1小时","4H":"4小时","1D":"1天"}
DIR={"LONG":"做多","SHORT":"做空"}
TYP={"TREND":"趋势","BREAKOUT":"突破"}
cache={}
btc_cache={}
stop=False


def get(path,p):
    global stop
    if stop:return
    for i in range(5):
        try:
            time.sleep(.15+random.random()*.15)
            r=requests.get(BASE+path,params=p,timeout=15)

            if r.status_code==429 or r.status_code>=500:
                w=min(2**i*3,30)+random.random()
                print(f"[OKX] HTTP {r.status_code}，等待{w:.1f}s")
                time.sleep(w)
                continue

            if r.status_code in (403,451):
                print(f"[OKX] HTTP {r.status_code}，停止本轮")
                stop=True
                return

            r.raise_for_status()
            x=r.json()

            if x.get("code")=="0":
                return x.get("data",[])

            code=x.get("code")
            msg=x.get("msg","")

            if code=="50011":
                w=min(2**i*3,30)+random.random()
                print(f"[OKX] 50011限速，等待{w:.1f}s")
                time.sleep(w)
                continue

            print(f"[OKX] {code}: {msg}")

            if code in ("50004","50013","50026"):
                time.sleep(min(2**i*2,20))
                continue
            return

        except (requests.exceptions.Timeout,
                requests.exceptions.ConnectionError) as e:
            print(f"[OKX] {type(e).__name__}")
            time.sleep(min(2**i*2,20))
        except Exception as e:
            print(f"[OKX] {type(e).__name__}")
            time.sleep(min(2**i*2,20))

    print(f"[OKX] 重试失败: {path}")


def coins():
    print("[币种] 获取永续合约列表...")
    a=get("/api/v5/public/instruments",{"instType":"SWAP"})
    b=get("/api/v5/market/tickers",{"instType":"SWAP"})
    if not a or not b:
        print("[币种] 获取失败")
        return []

    live={x["instId"] for x in a
          if x.get("settleCcy")=="USDT" and x.get("state")=="live"}
    vol={x["instId"]:float(x.get("volCcy24h",0) or 0) for x in b}

    r=sorted(live,key=lambda x:vol.get(x,0),reverse=True)[:150]
    print(f"[币种] 共获取 {len(r)} 个")
    return r


def candles(sym,bar,n=250):
    k=(sym,bar,n)
    if k in cache and time.time()-cache[k][0]<30:
        return cache[k][1]

    x=get("/api/v5/market/candles",
         {"instId":sym,"bar":bar,"limit":str(n)})
    if not x:return

    try:
        d=pd.DataFrame(
            [[int(a[0]),*map(float,a[1:6]),a[8]] for a in reversed(x)],
            columns=["ts","o","h","l","c","v","ok"])
        d=d[d.ok=="1"].reset_index(drop=True)
        if len(d)<10:return
        cache[k]=(time.time(),d)
        return d
    except Exception as e:
        print(f"[K线] {sym} {bar}: {type(e).__name__}")


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
    if d is None or len(d)<205:return 0
    btc_cache[bar]=15 if d.c.iloc[-1]>ema(d.c,200).iloc[-1] else -15
    return btc_cache[bar]


def signal(d,tf,sym):
    if d is None or len(d)<210:return

    p=d.c.iloc[-1]
    e20,e60,e120,e200=[ema(d.c,n).iloc[-1] for n in (20,60,120,200)]
    a=atr(d).iloc[-1]
    aa=atr(d).iloc[-6:-1].mean()

    if pd.isna(a) or pd.isna(aa):return

    body=abs(p-d.o.iloc[-1])
    rng=max(d.h.iloc[-1]-d.l.iloc[-1],a*.01)
    vr=d.v.iloc[-1]/max(d.v.iloc[-21:-1].mean(),1e-12)
    bs=btc(TF[tf][0])
    need=TF[tf][2]
    out=[]

    def score(x):
        s=30 if vr>=2 else 15 if vr>=1.5 else 0
        s+=25 if body/rng>=.6 else 12 if body/rng>=.45 else 0
        s+=20 if a>aa*1.05 else 0
        s+=min(max(bs if x=="LONG" else -bs,0),15)
        s+=10 if (x=="LONG" and p>e20) or (x=="SHORT" and p<e20) else 0
        return s

    def add(x,t,sl,tp):
        rr=(tp-p)/(p-sl) if x=="LONG" else (p-tp)/(sl-p)
        s=score(x)
        if rr>=1.8 and s>=need:
            out.append((x,t,s,sl,tp,rr))

    if e20>e60>e120 and p>e200:
        add("LONG","TREND",p-2.5*a,
            min(d.h.iloc[-61:-1].max()*.995,p+3*a))

    if e20<e60<e120 and p<e200:
        add("SHORT","TREND",p+2.5*a,
            max(d.l.iloc[-61:-1].min()*1.005,p-3*a))

    hi=d.h.iloc[-11:-1].max()
    lo=d.l.iloc[-11:-1].min()

    if p>hi and body/rng>=.55 and vr>=1.5:
        add("LONG","BREAKOUT",p-2.5*a,
            min(d.h.iloc[-61:-1].max()*.995,p+3*a))

    if p<lo and body/rng>=.55 and vr>=1.5:
        add("SHORT","BREAKOUT",p+2.5*a,
            max(d.l.iloc[-61:-1].min()*1.005,p-3*a))

    if not out:return

    x=max(out,key=lambda z:z[2])
    return dict(
        sym=sym,tf=tf,dir=x[0],type=x[1],score=x[2],
        entry=p,sl=x[3],tp=x[4],rr=x[5]
    )


def load(p,d):
    try:
        with open(p,encoding="utf8") as f:return json.load(f)
    except:return d


def save(p,d):
    with open(p+".tmp","w",encoding="utf8") as f:
        json.dump(d,f,ensure_ascii=False)
    os.replace(p+".tmp",p)


def send(s):
    if not WEBHOOK:
        print("[飞书] 未设置WEBHOOK")
        return False

    text="\n".join([
        f"**币种**：{s['sym']}",
        f"**周期**：{CN[s['tf']]}",
        f"**类型**：{TYP[s['type']]}",
        f"**方向**：{DIR[s['dir']]}",
        f"**评分**：{s['score']}",
        f"**入场**：{s['entry']:.8g}",
        f"**止损**：{s['sl']:.8g}",
        f"**止盈**：{s['tp']:.8g}",
        f"**盈亏比**：{s['rr']:.2f}",
        f"**时间**：{s['time']}"
    ])

    data={
        "msg_type":"interactive",
        "card":{
            "header":{
                "title":{
                    "tag":"lark_md",
                    "content":"**宝宝巴士🚌上车就赚**"
                }
            },
            "elements":[
                {
                    "tag":"div",
                    "text":{
                        "tag":"lark_md",
                        "content":text
                    }
                }
            ]
        }
    }

    time.sleep(2+random.random())

    for i in range(4):
        try:
            r=requests.post(WEBHOOK,json=data,timeout=12)

            if r.ok:
                print(f"[飞书] 发送成功 {s['sym']} {s['tf']}")
                return True

            print(f"[飞书] HTTP {r.status_code}: {r.text[:300]}")

            if r.status_code==429 or r.status_code>=500:
                time.sleep(min(5*2**i,30)+random.random())
                continue

            return False

        except Exception as e:
            print(f"[飞书] {type(e).__name__}")
            time.sleep(min(3*(i+1),15))

    print(f"[飞书] 最终发送失败 {s['sym']} {s['tf']}")
    return False


def evaluate(s):
    d=candles(s["sym"],TF[s["tf"]][0],300)
    if d is None:return

    unit={"15m":15,"1H":60,"4H":240,"1D":1440}[s["tf"]]
    bars=int(TF[s["tf"]][3]*60/unit)
    f=d[d.ts>s["ts"]].iloc[:bars]

    for _,r in f.iterrows():
        if s["dir"]=="LONG":
            sl=r.l<=s["sl"]
            tp=r.h>=s["tp"]
        else:
            sl=r.h>=s["sl"]
            tp=r.l<=s["tp"]

        if sl and tp:return "LOSS"
        if tp:return "WIN"
        if sl:return "LOSS"

    return "EXPIRED" if len(f)>=bars else None


def main():
    global stop,btc_cache

    print("="*40)
    print(f"[开始] {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print("="*40)

    sent=load("sent_cache.json",{})
    records=load("signals_record.json",[])

    cut=(datetime.now(timezone.utc)-timedelta(hours=48)).timestamp()
    sent={k:v for k,v in sent.items() if v>cut}

    syms=coins()

    if not syms:
        print("[错误] OKX无法获取永续合约")
        raise SystemExit(1)

    print(f"[启动] OKX {len(syms)}个USDT永续")

    ids={x.get("id") for x in records}

    for tf,(bar,limit,_,_) in TF.items():
        if stop:
            print(f"[中断] 触发限流保护，跳过 {tf}")
            break

        now=datetime.now(timezone.utc)

        if tf=="1D" and not(now.hour==0 and 30<=now.minute<45):
            print(f"[{tf}] 未到触发时间，跳过")
            continue

        if tf=="4H" and not(now.hour%4==0 and 15<=now.minute<30):
            print(f"[{tf}] 未到触发时间，跳过")
            continue

        if tf=="1H" and now.minute>=15:
            print(f"[{tf}] 未到触发时间，跳过")
            continue

        print(f"[扫描] {tf}")
        btc_cache={}
        cnt_sig=0
        cnt_sent=0

        for sym in syms:
            if stop:
                print(f"[中断] 触发限流保护，停止扫描 {tf}")
                break

            d=candles(sym,bar,limit)
            s=signal(d,tf,sym)

            if not s:continue

            s["ts"]=int(d.ts.iloc[-1])
            s["time"]=datetime.fromtimestamp(
                s["ts"]/1000,timezone.utc
            ).strftime("%Y-%m-%d %H:%M")

            sid=f"{sym}|{tf}|{s['dir']}|{s['type']}|{s['ts']}"

            cnt_sig+=1
            print(f"→ {sym} {s['dir']} {s['type']} {s['score']}分")

            if sid not in ids:
                records.append({
                    "id":sid,
                    **s,
                    "result":None,
                    "created":int(time.time())
                })
                ids.add(sid)

            if sid not in sent and send(s):
                sent[sid]=int(time.time())
                cnt_sent+=1

        if cnt_sig:
            print(f"[{tf}] 发现 {cnt_sig} 个信号，已发送 {cnt_sent} 条")
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
                print(f"  ← {r['sym']} {r['tf']} 结果：{x}")
    if ev:
        print(f"[评估] 更新 {ev} 条结果")
    else:
        print("[评估] 无新结果")

    save("sent_cache.json",sent)
    save("signals_record.json",records)

    print("="*40)
    print(f"[完成] 本轮扫描结束 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"[统计] 信号记录总数：{len(records)}，已发送缓存：{len(sent)}")
    print("="*40)


if __name__=="__main__":
    main()
