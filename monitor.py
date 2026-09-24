import os,time,json,random,requests,pandas as pd,threading,websocket
from datetime import datetime,timezone,timedelta

BASES=["https://fapi.binance.com","https://fapi1.binance.com","https://fapi2.binance.com","https://fapi3.binance.com","https://fapi4.binance.com"]
WS="wss://fstream.binance.com/market"
WEBHOOK=os.getenv("FEISHU_WEBHOOK","")
API_GAP=.45;FEISHU_GAP=2;MAX429=3
caches,btc_cache,ws_data={},{},{}
ws_symbols=[];ws_conn=None;ws_thread=rank_thread=None
ws_lock=threading.Lock();breaker=ws_stop=False;con429=0

TF={"15m":{"bar":"15m","limit":250,"score":68,"exp":12},"1H":{"bar":"1h","limit":250,"score":70,"exp":48},"4H":{"bar":"4h","limit":250,"score":72,"exp":120},"1D":{"bar":"1d","limit":250,"score":74,"exp":336}}
NAMES={"15m":"15分钟","1H":"1小时","4H":"4小时","1D":"1天"}
TYPES={"TREND":"趋势","BREAKOUT":"突破"};DIRS={"LONG":"做多","SHORT":"做空"}

def ok_t(tf):
    n=datetime.now(timezone.utc)
    return (tf=="1D" and n.hour==0 and 30<=n.minute<45) or (tf=="4H" and n.hour%4==0 and 15<=n.minute<30) or (tf=="1H" and n.minute<15) or tf=="15m"

def get(path,p=None,retry=3):
    global breaker,con429
    if breaker:return
    for base in BASES:
        for i in range(retry):
            time.sleep(API_GAP+random.uniform(.08,.25))
            try:
                r=requests.get(base+path,params=p,timeout=15,headers={"User-Agent":"Mozilla/5.0"})
                if r.status_code in (403,418):
                    print(f"[API] {base} HTTP {r.status_code}");breaker=True;return
                if r.status_code==451:
                    print(f"[API] {base} HTTP 451");break
                if r.status_code==429:
                    con429+=1;print(f"[API] {base} HTTP 429 #{con429}")
                    if con429>=MAX429:breaker=True;return
                    time.sleep(min(20*2**i,60)+random.uniform(1,4));continue
                if r.status_code>=500:
                    time.sleep(3*(i+1));continue
                r.raise_for_status();con429=0;return r.json()
            except requests.RequestException as e:
                print(f"[API] {base} {type(e).__name__}: {e}")
                if i<retry-1:time.sleep(2*(i+1))
    print(f"[API] 全部失败: {path}")

def coins(n=150):
    a=get("/fapi/v1/exchangeInfo")
    if not a:return []
    s=[x["symbol"] for x in a.get("symbols",[]) if x.get("quoteAsset")=="USDT" and x.get("status")=="TRADING" and x.get("contractType")=="PERPETUAL"]
    b=get("/fapi/v1/ticker/24hr")
    if not b:return s[:n]
    v={x["symbol"]:float(x.get("quoteVolume",0) or 0) for x in b}
    return sorted(s,key=lambda x:v.get(x,0),reverse=True)[:n]

def streams(s):return [f"{x.lower()}@kline_{v['bar']}" for x in s for v in TF.values()]

def ws_msg(_,msg):
    try:
        d=json.loads(msg).get("data",{});k=d.get("k",{})
        if d.get("e")!="kline" or not k:return
        key=f"{d['s']}_{k['i']}";a=ws_data.setdefault(key,[])
        row=[int(k["t"]),float(k["o"]),float(k["h"]),float(k["l"]),float(k["c"]),float(k["v"])]
        if a and a[-1][0]==row[0]:a[-1]=row
        else:a.append(row)
        if len(a)>320:del a[:-320]
    except:pass

def ws_send(w,m,a,start):
    for i in range(0,len(a),200):
        try:w.send(json.dumps({"method":m,"params":a[i:i+200],"id":start+i//200}));time.sleep(.5)
        except:break

def ws_open(w):
    global ws_conn
    with ws_lock:ws_conn=w;s=list(ws_symbols)
    ws_send(w,"SUBSCRIBE",streams(s),1);print(f"[WS] {len(s)}币/{len(streams(s))}流")

def ws_close(*_):
    global ws_conn
    with ws_lock:ws_conn=None

def ws_loop():
    d=3
    while not ws_stop:
        try:
            websocket.WebSocketApp(WS,on_open=ws_open,on_message=ws_msg,on_close=ws_close).run_forever(ping_interval=None,ping_timeout=None)
        except:pass
        if not ws_stop:time.sleep(d+random.uniform(.5,2));d=min(d*2,60)

def update_symbols(ns):
    global ws_symbols
    ns=list(dict.fromkeys(ns))
    if not ns:return
    with ws_lock:old=set(ws_symbols);new=set(ns);w=ws_conn;ws_symbols=ns
    add,rem=new-old,old-new
    for s in rem:
        for v in TF.values():
            ws_data.pop(f"{s}_{v['bar']}",None);caches.pop(f"{s}_{v['bar']}",None)
    if w:
        if rem:ws_send(w,"UNSUBSCRIBE",streams(rem),1000)
        if add:ws_send(w,"SUBSCRIBE",streams(add),2000)
    print(f"[排名] +{len(add)} -{len(rem)} 当前{len(ns)}")

def rank_loop():
    while not ws_stop and not breaker:
        time.sleep(3600)
        if ws_stop or breaker:break
        try:
            s=coins(150)
            if s:update_symbols(s)
        except:pass

def start_ws(s):
    global ws_symbols,ws_thread,rank_thread
    with ws_lock:ws_symbols=list(s)
    if not ws_thread or not ws_thread.is_alive():
        ws_thread=threading.Thread(target=ws_loop,daemon=True);ws_thread.start()
    if not rank_thread or not rank_thread.is_alive():
        rank_thread=threading.Thread(target=rank_loop,daemon=True);rank_thread.start()

def current_symbols():
    with ws_lock:return list(ws_symbols)

def candles(sym,bar,limit=250):
    k=f"{sym}_{bar}";now=time.time();a=ws_data.get(k)
    if a and len(a)>=limit:
        df=pd.DataFrame(a[-limit:],columns=["ts","o","h","l","c","v"]);caches[k]=(now,df);return df
    if k in caches and now-caches[k][0]<35:return caches[k][1]
    x=get("/fapi/v1/klines",{"symbol":sym,"interval":bar,"limit":limit})
    if not x:return caches[k][1] if k in caches else None
    rows=[[int(z[0]),float(z[1]),float(z[2]),float(z[3]),float(z[4]),float(z[5])] for z in x]
    df=pd.DataFrame(rows,columns=["ts","o","h","l","c","v"]);caches[k]=(now,df);ws_data[k]=rows[-320:];return df

def ema(s,n):return s.ewm(span=n,adjust=False).mean()

def atr(df,n=14):
    p=df.c.shift()
    return pd.concat([df.h-df.l,(df.h-p).abs(),(df.l-p).abs()],axis=1).max(axis=1).rolling(n).mean()

def btc(bar):
    if bar in btc_cache and time.time()-btc_cache[bar][0]<60:return btc_cache[bar][1]
    df=candles("BTCUSDT",bar,250)
    if df is None or len(df)<205:btc_cache[bar]=(time.time(),0);return 0
    s=15 if df.c.iloc[-1]>ema(df.c,200).iloc[-1] else -15;btc_cache[bar]=(time.time(),s);return s

def signal(df,tf,sym):
    if df is None or len(df)<210:return
    p=df.c.iloc[-1];e20,e60,e120,e200=[ema(df.c,n).iloc[-1] for n in (20,60,120,200)];a=atr(df).iloc[-1]
    if not a:return
    body=abs(df.c.iloc[-1]-df.o.iloc[-1]);rng=max(df.h.iloc[-1]-df.l.iloc[-1],a*.01);vr=df.v.iloc[-1]/df.v.iloc[-21:-1].mean();bs=btc(TF[tf]["bar"]);aa=atr(df).iloc[-6:-1].mean()
    def score(d):
        s=30 if vr>=2 else 15 if vr>=1.5 else 0
        s+=25 if body/rng>=.7 else 12 if body/rng>=.55 else 0
        s+=20 if a>aa*1.05 else 0
        s+=min(max(bs if d=="LONG" else -bs,0),15)
        s+=10 if (d=="LONG" and p>e20) or (d=="SHORT" and p<e20) else 0
        return s
    c=[]
    if e20>e60>e120 and p>e200:
        sl=p-2.5*a;tp=min(df.h.iloc[-61:-1].max()*.995,p+3*a);rr=(tp-p)/(p-sl);s=score("LONG")
        if rr>=1.8 and s>=TF[tf]["score"]:c.append(("LONG","TREND",s,sl,tp,rr))
    if e20<e60<e120 and p<e200:
        sl=p+2.5*a;tp=max(df.l.iloc[-61:-1].min()*1.005,p-3*a);rr=(p-tp)/(sl-p);s=score("SHORT")
        if rr>=1.8 and s>=TF[tf]["score"]:c.append(("SHORT","TREND",s,sl,tp,rr))
    hi,lo=df.h.iloc[-21:-1].max(),df.l.iloc[-21:-1].min()
    if p>hi and body/rng>=.55 and vr>=1.5:
        sl=p-2.5*a;tp=min(df.h.iloc[-61:-1].max()*.995,p+3*a);rr=(tp-p)/(p-sl);s=score("LONG")
        if rr>=1.8 and s>=TF[tf]["score"]:c.append(("LONG","BREAKOUT",s,sl,tp,rr))
    if p<lo and body/rng>=.55 and vr>=1.5:
        sl=p+2.5*a;tp=max(df.l.iloc[-61:-1].min()*1.005,p-3*a);rr=(p-tp)/(sl-p);s=score("SHORT")
        if rr>=1.8 and s>=TF[tf]["score"]:c.append(("SHORT","BREAKOUT",s,sl,tp,rr))
    if not c:return
    d,t,s,sl,tp,rr=max(c,key=lambda x:x[2])
    return {"sym":sym,"tf":tf,"dir":d,"type":t,"score":s,"entry":p,"sl":sl,"tp":tp,"rr":rr}

def load(p,d):
    try:
        with open(p,encoding="utf-8") as f:return json.load(f)
    except:return d

def save(p,d):
    with open(p+".tmp","w",encoding="utf-8") as f:json.dump(d,f,ensure_ascii=False,indent=2)
    os.replace(p+".tmp",p)

def send(s):
    if not WEBHOOK:return False
    text=f"**币种**：{s['sym']}\n**周期**：{NAMES[s['tf']]}\n**类型**：{TYPES[s['type']]}\n**方向**：{DIRS[s['dir']]}\n**评分**：{s['score']}\n**入场**：{s['entry']:.8g}\n**止损**：{s['sl']:.8g}\n**止盈**：{s['tp']:.8g}\n**盈亏比**：{s['rr']:.2f}\n**时间**：{s['time']}"
    p={"msg_type":"interactive","card":{"header":{"title":{"tag":"lark_md","content":"**宝宝巴士🚌快上车**"}},"elements":[{"tag":"div","text":{"tag":"lark_md","content":text}}]}}
    time.sleep(FEISHU_GAP+random.uniform(.1,.5))
    for i in range(3):
        try:
            r=requests.post(WEBHOOK,json=p,timeout=12)
            if r.status_code==429:time.sleep(min(5*2**i,30));continue
            return r.ok
        except:time.sleep(2*(i+1))
    return False

def evaluate(s):
    df=candles(s["sym"],TF[s["tf"]]["bar"],300)
    if df is None:return
    future=df[df.ts>s["ts"]];bars=max(1,int(TF[s["tf"]]["exp"]*60/{"15m":15,"1H":60,"4H":240,"1D":1440}[s["tf"]]))
    for _,r in future.iloc[:bars].iterrows():
        sl,tp=(r.l<=s["sl"],r.h>=s["tp"]) if s["dir"]=="LONG" else (r.h>=s["sl"],r.l<=s["tp"])
        if sl and tp:return "LOSS"
        if tp:return "WIN"
        if sl:return "LOSS"
    return "EXPIRED" if len(future)>=bars else None

def main():
    global breaker
    sent=load("sent_cache.json",{});records=load("signals_record.json",[])
    cutoff=(datetime.now(timezone.utc)-timedelta(hours=48)).timestamp()
    sent={k:v for k,v in sent.items() if v>cutoff}
    syms=coins(150)
    if not syms:
        print("[错误] 未获取到币种列表");return
    print(f"[启动] {len(syms)}个币种");start_ws(syms);time.sleep(2)

    for tf in TF:
        if breaker:print("[中断] API保护");break
        if not ok_t(tf):print(f"[{tf}] 未到触发时间");continue
        ids={x.get("id") for x in records};cs=ss=0;print(f"扫描周期 {tf}...")

        for sym in current_symbols():
            if breaker:break
            df=candles(sym,TF[tf]["bar"],TF[tf]["limit"]);s=signal(df,tf,sym)
            if not s:continue
            s["ts"]=int(df.ts.iloc[-1]);s["time"]=datetime.fromtimestamp(s["ts"]/1000,timezone.utc).strftime("%Y-%m-%d %H:%M")
            sid=f"{sym}|{tf}|{s['dir']}|{s['type']}|{s['ts']}";cs+=1
            print(f" → {sym} {s['dir']} {s['type']} 评分{s['score']} 入场{s['entry']:.6g}")
            if sid not in ids:records.append({"id":sid,**s,"result":None,"created":int(time.time())});ids.add(sid)
            if sid not in sent and send(s):sent[sid]=int(time.time());ss+=1

        print(f"[{tf}] 信号{cs} 已发送{ss}");save("sent_cache.json",sent);save("signals_record.json",records)

    for r in records:
        if not r.get("result"):
            x=evaluate(r)
            if x:r["result"]=x;r["result_time"]=int(time.time())

    save("sent_cache.json",sent);save("signals_record.json",records)
    print("本轮扫描结束")

if __name__=="__main__":main()
