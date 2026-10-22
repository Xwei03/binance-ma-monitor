# 宝宝巴士🚌上车就赚 - OKX 信号扫描 (1H/1D) - 纯净测试版
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
    return {"User-Agent":random.choice(UAS),"Accept":"application/json, text/plain, */*",
            "Accept-Language":"zh-CN,zh;q=0.9,en;q=0.8","Connection":"keep-alive",
            "Referer":"https://www.okx.com/"}

def now_bj():return datetime.now(BJ_TZ)
def log(*args):print(*args)

TF={"1H":("1H",500,73,6),"1D":("1D",300,72,4)}
MAX_HOLD={"1H":100,"1D":30}
CHASE_ATR={"1H":2.5,"1D":1.5}
GAP_ATR={"1H":2.5,"1D":2.5}
GAP_CAP={"1H":0.04,"1D":0.10}
PREPARE_GAP_ATR=1.5
PREPARE_GAP_CAP={"1H":0.02,"1D":0.06}
DEDUP_BARS,COOL_BARS=8,12
KEEP_SENT_DAYS,KEEP_RESULT_DAYS=110,7
MAX_SEND_PER_SCAN=20 
MAX_LIVE=20
MIN_VOL=10000000
MAX_SHADOW=50
SHADOW_KEEP_DAYS=3
SHADOW_COOL_KEEP_DAYS=30
CN={"1H":"1小时","1D":"1天"}
DIR={"LONG":"做多","SHORT":"做空"}
TYP={"PREPARE":"启动前埋伏","TREND":"趋势","BREAKOUT":"突破"}

cache={};btc_cache={};stop=False
last_req=[0.0]

def tf_run(tf):
    n=datetime.now(timezone.utc)
    if tf=="1D":return n.hour==0
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
                log(f"[OKX] HTTP {r.status_code} 停止本轮");stop=True;return
            if r.status_code==418:
                log("[OKX] 418 被封 等60s");time.sleep(60);continue
            if r.status_code==429 or r.status_code>=500:
                w=min(3*2**i,30)+random.random()*2
                log(f"[OKX] HTTP {r.status_code} 等{w:.1f}s");time.sleep(w);continue
            x=r.json()
            if x.get("code")=="0":return x.get("data",[])
            if x.get("code")=="50011":
                w=min(3*2**i,30)+random.random()*2
                log(f"[OKX] 50011 等{w:.1f}s");time.sleep(w);continue
            return
        except Exception as e:
            log(f"[OKX] {type(e).__name__}");time.sleep(min(2**i*2,20))

def coins():
    a=get("/api/v5/public/instruments",{"instType":"SWAP"})
    b=get("/api/v5/market/tickers",{"instType":"SWAP"})
    if not a or not b:return []
    live={x["instId"] for x in a if x.get("settleCcy")=="USDT" and x.get("state")=="live"}
    vol={}
    for x in b:
        v=float(x.get("volCcy24h",0) or 0);p=float(x.get("last",0) or 0);vol[x["instId"]]=v*p
    valid=[s for s in live if vol.get(s,0)>=MIN_VOL]
    r=sorted(valid,key=lambda x:vol.get(x,0),reverse=True)[:150]
    if r:log(f"[币种] ≥{MIN_VOL/1e8:.2f}亿的共{len(valid)}个，取{len(r)}个")
    return r

def candles(sym,bar,n=300):
    k=(sym,bar,n)
    if k in cache and time.time()-cache[k][0]<30:return cache[k][1]
    x=get("/api/v5/market/candles",{"instId":sym,"bar":bar,"limit":str(n)})
    if not x:return
    try:
        d=pd.DataFrame([[int(a[0]),float(a[1]),float(a[2]),float(a[3]),float(a[4]),float(a[5]),a[8]] for a in reversed(x)],
                       columns=["ts","o","h","l","c","v","ok"])
        d=d[d.ok=="1"].reset_index(drop=True)
        if len(d)<12:return
        bar_ms=3600*1000
        if bar=="1D":bar_ms=86400*1000
        elif bar=="15m":bar_ms=15*60*1000
        elif bar=="5m":bar_ms=5*60*1000
        elif bar=="1m":bar_ms=1*60*1000
        if len(d)>0 and (d.ts.iloc[-1]+bar_ms>int(time.time()*1000)):
            d=d.iloc[:-1].reset_index(drop=True)
        cache[k]=(time.time(),d);return d
    except Exception as e:
        log(f"[CANDLES] {sym} {bar} {type(e).__name__} {e}")
        return

def ema(s,n):return s.ewm(span=n,adjust=False).mean()

def atr(d):
    p=d.c.shift()
    return pd.concat([d.h-d.l,(d.h-p).abs(),(d.l-p).abs()],axis=1).max(axis=1).rolling(14).mean()

def find_swing_levels(d,tf,p,side):
    n_l=5
    n_r=2 if tf=="1H" else 3
    look=500 if tf=="1H" else 300
    arr_h=d.h.iloc[-look:-1].values
    arr_l=d.l.iloc[-look:-1].values
    arr_v=d.v.iloc[-look:-1].values
    m=len(arr_h)
    if m<n_l+n_r+1:return []
    avg_vol=d.v.iloc[-21:-1].mean()
    if pd.isna(avg_vol) or avg_vol<=0:avg_vol=1
    cands=[]
    if side=="res":
        for i in range(n_l,m-n_r):
            if arr_h[i]>=arr_h[i-n_l:i].max() and arr_h[i]>=arr_h[i+1:i+n_r+1].max() and arr_h[i]>p:
                price=arr_h[i]
                tol=price*0.005
                hits=int(((arr_h>price-tol)&(arr_h<price+tol)).sum())
                vol_ratio=arr_v[i]/avg_vol
                time_weight=1+(i/m)
                cands.append((price,vol_ratio*time_weight+hits*0.5))
    else:
        for i in range(n_l,m-n_r):
            if arr_l[i]<=arr_l[i-n_l:i].min() and arr_l[i]<=arr_l[i+1:i+n_r+1].min() and arr_l[i]<p:
                price=arr_l[i]
                tol=price*0.005
                hits=int(((arr_l>price-tol)&(arr_l<price+tol)).sum())
                vol_ratio=arr_v[i]/avg_vol
                time_weight=1+(i/m)
                cands.append((price,vol_ratio*time_weight+hits*0.5))
    if not cands:return []
    cands.sort(key=lambda x:-x[1])
    used=[False]*len(cands)
    merged=[]
    for i in range(len(cands)):
        if used[i]:continue
        price=cands[i][0];score=cands[i][1]
        group=[price];used[i]=True
        for j in range(i+1,len(cands)):
            if used[j]:continue
            p2=cands[j][0]
            if abs(p2-price)/price<0.003:
                group.append(p2);used[j]=True
        merged.append((sum(group)/len(group),score))
    merged=[x for x in merged if x[1]>=0.5]
    if not merged:return []
    merged.sort(key=lambda x:abs(x[0]-p))
    return [(float(price),float(score)) for price,score in merged]

def calc_sl_struct(d,tf,p,side,a):
    sl_cap = 2.0*a if tf=="1H" else 2.5*a
    buf = 1.0*a
    if side=="LONG":
        sup_list=find_swing_levels(d,tf,p,"sup")
        sup=sup_list[0][0] if sup_list else None
        if sup is None:return p - sl_cap
        sl_c = sup - buf
        return p - sl_cap if (p - sl_c) > sl_cap else sl_c
    else:
        res_list=find_swing_levels(d,tf,p,"res")
        res=res_list[0][0] if res_list else None
        if res is None:return p + sl_cap
        sl_c = res + buf
        return p + sl_cap if (sl_c - p) > sl_cap else sl_c

def btc(bar):
    if bar in btc_cache:return btc_cache[bar]
    d=candles("BTC-USDT-SWAP",bar,300)
    if d is None:btc_cache[bar]=0;return 0
    p=d.c.iloc[-1];e20=ema(d.c,20).iloc[-1];e60=ema(d.c,60).iloc[-1]
    v=15 if (e20>e60 and p>e20) else -15 if (e20<e60 and p<e20) else 0
    btc_cache[bar]=v;return v

def h4_filter(sym):
    d4=candles(sym,"4H",300)
    if d4 is None or len(d4)<15:return 0
    a4=atr(d4).iloc[-1]
    if pd.isna(a4) or a4<=0:return 0
    o=d4.o.iloc[-1];c=d4.c.iloc[-1];amp=d4.h.iloc[-1]-d4.l.iloc[-1]
    if amp > 1.5*a4:
        return 1 if c>o else -1 if c<o else 0
    return 0

def bars_since(d,ts):return max(int(len(d[d.ts>=ts]))-1,0)

def load_state():
    try:
        with open(STATE,encoding="utf8") as f:return json.load(f)
    except:return {"sent":{},"cool":{},"live":[],"results":[],"last_report":""}

def save_state(st):
    with open(STATE+".tmp","w",encoding="utf8") as f:json.dump(st,f,ensure_ascii=False)
    os.replace(STATE+".tmp",STATE)

def sdk(sym,tf,side,typ):return f"{sym}|{tf}|{side}|{typ}"

def too_soon(st,sym,tf,side,typ,d,bars):
    ts=st.get("cool",{}).get(sdk(sym,tf,side,typ)) or st.get("sent",{}).get(sdk(sym,tf,side,typ))
    return ts and bars_since(d,int(ts))<bars

def mark(st,sym,tf,side,typ,d,where="sent"):
    st.setdefault(where,{})[sdk(sym,tf,side,typ)]=int(d.ts.iloc[-1])

def rec(st,s,r,mfe=None,mfe_pct=None):
    e={"sym":s["sym"],"tf":s["tf"],"dir":s["dir"],"type":s["type"],"result":r,"ts":int(time.time()),
       "rec_ts":int(time.time()*1000),"score":s.get("score",0)}
    if s.get("alert_ts"):e["alert_ts"]=int(s["alert_ts"])
    if s.get("sent_ts"):e["sig_ts"]=int(s["sent_ts"])
    if s.get("entry"):e["entry"]=float(s["entry"])
    if s.get("sl"):e["sl"]=float(s["sl"])
    if s.get("tp"):e["tp"]=float(s["tp"])
    if mfe is not None:e["mfe"]=round(float(mfe)*100,2)
    if mfe_pct is not None:e["mfe_pct"]=round(min(float(mfe_pct),1.0)*100,1)
    st.setdefault("results",[]).append(e)

def rec_shadow(st,sym,tf,side,typ,p,sl,tp,reasons,d,score=0):
    for lv in st.get("live",[]):
        if lv.get("sym")==sym and lv.get("tf")==tf:return
    if len(st.get("shadow",[]))>=MAX_SHADOW:
        log("SHADOW_FULL skip",sym,tf,side,typ)
        return
    key=f"{sym}|{tf}|{side}|{','.join(reasons)}"
    cool=st.setdefault("shadow_cool",{})
    prev=cool.get(key)
    if prev and bars_since(d,int(prev))<COOL_BARS:return
    if abs(p-sl)<1e-12 or abs(p-tp)<1e-12:return
    st.setdefault("shadow",[]).append({
        "sym":sym,"tf":tf,"dir":side,"type":typ,
        "reasons":list(reasons),
        "entry":float(p),"sl":float(sl),"tp":float(tp),
        "score":int(score),
        "ts":int(time.time()),
        "sent_ts":int(d.ts.iloc[-1]),
    })
    cool[key]=int(d.ts.iloc[-1])
    log("SHADOW_REC",sym,tf,side,typ,"+".join(reasons),score)

def post(txt):
    if not WEBHOOK:return False
    for i in range(3):
        try:
            r=requests.post(WEBHOOK,json={"msg_type":"text","content":{"text":txt}},timeout=15)
            try:res=r.json()
            except:res={}
            if r.status_code==200 and res.get("code")==0:return True
            if r.status_code==429:
                time.sleep(min(5*2**i,30));continue
            return False
        except Exception as e:
            time.sleep(2*(i+1))
    return False

def post_long(lines,max_len=1500,gap=2.0):
    batches=[];cur=[];cur_len=0
    for line in lines:
        l=len(line)+1
        if cur_len+l>max_len and cur:
            batches.append(cur);cur=[];cur_len=0
        cur.append(line);cur_len+=l
    if cur:batches.append(cur)
    total=len(batches);ok=True
    for idx,batch in enumerate(batches,1):
        header=f"🚨 警报 日报（第{idx}/{total}批）\n" if total>1 else ""
        if not post(header+"\n".join(batch)):ok=False
        if idx<total:time.sleep(gap)
    return ok

def clean(st):
    c=(int(time.time())-KEEP_SENT_DAYS*86400)*1000
    st["sent"]={k:v for k,v in st.get("sent",{}).items() if v>=c}
    st["cool"]={k:v for k,v in st.get("cool",{}).items() if v>=c}
    sc_keep=(int(time.time())-SHADOW_COOL_KEEP_DAYS*86400)*1000
    st["shadow_cool"]={k:v for k,v in st.get("shadow_cool",{}).items() if v>=sc_keep}
    kc_actual=int(time.time())-KEEP_RESULT_DAYS*86400
    kc_shadow=int(time.time())-SHADOW_KEEP_DAYS*86400
    new_results=[]
    for r in st.get("results",[]):
        if r.get("shadow"):
            if r["ts"]>=kc_shadow:new_results.append(r)
        else:
            if r["ts"]>=kc_actual:new_results.append(r)
    st["results"]=new_results

def _shadow_line(sub):
    w=sum(1 for r in sub if r["result"]=="WIN")
    l=sum(1 for r in sub if r["result"]=="LOSS")
    to=sum(1 for r in sub if r["result"]=="TIMEOUT")
    t=w+l
    return f"止盈{w} 止损{l} 超时{to} 胜率{(w/t*100) if t else 0:.0f}%"

def daily_report(st):
    n=datetime.now(timezone.utc);today=n.strftime("%Y-%m-%d")
    if st.get("last_report")==today or n.hour>=12:return
    cut=int(time.time())-86400
    rs=[r for r in st.get("results",[]) if r["ts"]>=cut and not r.get("shadow")]
    shadow_rs=[r for r in st.get("results",[]) if r["ts"]>=cut and r.get("shadow")]
    L=["宝宝巴士🚌上车就赚","📊 每日统计（警报）"];tw=tl=te=tto=tbe=0
    combos=[(tf,t,f"{tf}{TYP[t]}") for tf in ("1H","1D") for t in ("PREPARE","TREND","BREAKOUT")]
    lv=st.get("live",[])
    for tf,typ,lb in combos:
        sub=[r for r in rs if r.get("tf")==tf and r.get("type")==typ]
        w=sum(1 for r in sub if r["result"]=="WIN")
        l=sum(1 for r in sub if r["result"]=="LOSS")
        be=sum(1 for r in sub if r["result"]=="BE")
        e=sum(1 for r in sub if r["result"]=="ERROR")
        to=sum(1 for r in sub if r["result"]=="TIMEOUT")
        p=sum(1 for x in lv if x.get("tf")==tf and x.get("type")==typ)
        
        wm=[r["mfe"] for r in sub if r["result"]=="WIN" and "mfe" in r]
        lm=[r["mfe"] for r in sub if r["result"]=="LOSS" and "mfe" in r]
        tm=[r["mfe"] for r in sub if r["result"]=="TIMEOUT" and "mfe" in r]
        lmp=[r["mfe_pct"] for r in sub if r["result"]=="LOSS" and "mfe_pct" in r]
        
        wa=sum(wm)/len(wm) if wm else 0
        la=sum(lm)/len(lm) if lm else 0
        lpa=sum(lmp)/len(lmp) if lmp else 0
        ta=sum(tm)/len(tm) if tm else 0
        
        t = w + l
        tw+=w; tl+=l; tbe+=be; te+=e; tto+=to
        L.append(f"警报 {lb}：止盈{w} 保本{be} 止损{l} 超时{to}(不计胜率) 在追{p} 胜率{(w/t*100) if t else 0:.0f}% MFE均 胜{wa:.2f}% 负{la:.2f}%({lpa:.0f}%) 超{ta:.2f}%")
        
    tt = tw + tl
    allwm=[r["mfe"] for r in rs if r["result"]=="WIN" and "mfe" in r]
    alllm=[r["mfe"] for r in rs if r["result"]=="LOSS" and "mfe" in r]
    alltm=[r["mfe"] for r in rs if r["result"]=="TIMEOUT" and "mfe" in r]
    alllmp=[r["mfe_pct"] for r in rs if r["result"]=="LOSS" and "mfe_pct" in r]
    wa=sum(allwm)/len(allwm) if allwm else 0
    la=sum(alllm)/len(alllm) if alllm else 0
    lpa=sum(alllmp)/len(alllmp) if alllmp else 0
    ta=sum(alltm)/len(alltm) if alltm else 0
    L.append(f"警报 合计：止盈{tw} 保本{tbe} 止损{tl} 超时{tto}(不计胜率) 在追{len(lv)} 胜率{(tw/tt*100) if tt else 0:.0f}% MFE均 胜{wa:.2f}% 负{la:.2f}%({lpa:.0f}%) 超{ta:.2f}%")
    L.append(f"警报 统计时间：{now_bj().strftime('%Y-%m-%d %H:%M:%S')}")

    if shadow_rs or st.get("shadow"):
        L.append(f"\n👻 影子单（被 4H 过滤拦掉的候选，仅观测，不计实际胜率）")
        for tf_x in ("1H","1D"):
            sub_tf=[r for r in shadow_rs if r.get("tf")==tf_x]
            if not sub_tf:continue
            L.append(f"  【{tf_x}】")
            for typ in ("PREPARE","TREND","BRE [{AKOUT"):
                sub_typ=[r for r in sub_tf if r.get("type")==typ]
                if not sub_typ:continue
                L.append(f"   TYP[typ]}]")
                groups={}
                for r in sub_typ:
                    k="+".join(r.get("reasons") or ["?"])
                    groups.setdefault(k,[]).append(r)
                for k in sorted(groups.keys()):
                    L.append(f"      被拦[{k}]：{_shadow_line(groups[k])}")
                L.append(f"      小计：{_shadow_line(sub_typ)}")
        pend=len(st.get("shadow",[]))
        full_tag="（已满，暂停记录新影子）" if pend>=MAX_SHADOW else ""
        L.append(f"  影子在跟：{pend}/{MAX_SHADOW} 条{full_tag}")
        L.append(f"  口径：影子单 sl/tp 按各自类型真实逻辑算，跟到止盈/止损/超时；不代表没过滤就能赚")

    wins=[r for r in rs if r["result"]=="WIN" and "mfe" in r]
    wins.sort(key=lambda x:-x.get("score",0))
    if wins:
        L.append(f"\n🏆 止盈明细（{len(wins)}条，按分数降序）：")
        for r in wins:
            sym_s=r["sym"].replace("-USDT-SWAP","")
            t_str=datetime.fromtimestamp((r.get("alert_ts") or r.get("sig_ts"))/1000,BJ_TZ).strftime("%m-%d %H:%M") if (r.get("alert_ts") or r.get("sig_ts")) else ""
            L.append(f"  {r['tf']}{TYP[r['type']]} {sym_s} {DIR.get(r.get('dir',''),'?')} 分数{r.get('score',0)} 入场{r.get('entry',0):.8g} 止损{r.get('sl',0):.8g} 止盈{r.get('tp',0):.8g} MFE={r['mfe']}% 进度{r.get('mfe_pct',0):.0f}% 开仓{t_str}")

    bes=[r for r in rs if r["result"]=="BE" and "mfe" in r]
    bes.sort(key=lambda x:-x.get("score",0))
    if bes:
        L.append(f"\n🛡️ 保本明细（{len(bes)}条，按分数降序）：")
        for r in bes:
            sym_s=r["sym"].replace("-USDT-SWAP","")
            t_str=datetime.fromtimestamp((r.get("alert_ts") or r.get("sig_ts"))/1000,BJ_TZ).strftime("%m-%d %H:%M") if (r.get("alert_ts") or r.get("sig_ts")) else ""
            L.append(f"  {r['tf']}{TYP[r['type']]} {sym_s} {DIR.get(r.get('dir',''),'?')} 分数{r.get('score',0)} 入场{r.get('entry',0):.8g} 止损{r.get('sl',0):.8g} 止盈{r.get('tp',0):.8g} MFE={r['mfe']}% 进度{r.get('mfe_pct',0):.0f}% 开仓{t_str}")

    tos=[r for r in rs if r["result"]=="TIMEOUT" and "mfe" in r]
    tos.sort(key=lambda x:-(x.get("mfe_pct",0)))
    if tos:
        L.append(f"\n⏰ 超时明细（{len(tos)}条，按止盈进度降序）：")
        for r in tos:
            sym_s=r["sym"].replace("-USDT-SWAP","")
            t_str=datetime.fromtimestamp((r.get("alert_ts") or r.get("sig_ts"))/1000,BJ_TZ).strftime("%m-%d %H:%M") if (r.get("alert_ts") or r.get("sig_ts")) else ""
            to_str=datetime.fromtimestamp(r["rec_ts"]/1000,BJ_TZ).strftime("%m-%d %H:%M") if r.get("rec_ts") else ""
            L.append(f"  {r['tf']}{TYP[r['type']]} {sym_s} {DIR.get(r.get('dir',''),'?')} 分数{r.get('score',0)} 入场{r.get('entry',0):.8g} 止损{r.get('sl',0):.8g} 止盈{r.get('tp',0):.8g} MFE={r['mfe']}% 进度{r.get('mfe_pct',0):.0f}% 开仓{t_str} 超时{to_str}")

    losses=[r for r in rs if r["result"]=="LOSS" and "mfe" in r]
    losses.sort(key=lambda x:-(x.get("mfe_pct",0)))
    if losses:
        L.append(f"\n📋 止损明细（{len(losses)}条，按止盈进度降序，止盈=100%）：")
        for r in losses:
            sym_s=r["sym"].replace("-USDT-SWAP","")
            t_str=datetime.fromtimestamp((r.get("alert_ts") or r.get("sig_ts"))/1000,BJ_TZ).strftime("%m-%d %H:%M") if (r.get("alert_ts") or r.get("sig_ts")) else ""
            loss_str=datetime.fromtimestamp(r["rec_ts"]/1000,BJ_TZ).strftime("%m-%d %H:%M") if r.get("rec_ts") else ""
            L.append(f"  {r['tf']}{TYP[r['type']]} {sym_s} {DIR.get(r.get('dir',''),'?')} 分数{r.get('score',0)} 入场{r.get('entry',0):.8g} 止损{r.get('sl',0):.8g} 止盈{r.get('tp',0):.8g} MFE={r['mfe']}% 进度{r.get('mfe_pct',0):.0f}% 开仓{t_str} 扫损{loss_str}")
            
    live_rows=[]
    for s in lv:
        tf=s.get("tf")
        if tf not in TF:continue
        d=candles(s["sym"],TF[tf][0],300)
        if d is None:continue
        ts0=int(s.get("sent_ts") or s.get("anchor") or d.ts.iloc[-1])
        f=d[d.ts>ts0]
        if len(f)==0:continue
        ent=s.get("entry",0) or 0;tp_v=s.get("tp",0) or 0;side=s["dir"]
        if ent and tp_v:
            tpd=tp_v-ent if side=="LONG" else ent-tp_v
            mfp=f.h.max()-ent if side=="LONG" else ent-f.l.min()
            mfe_pct=mfp/tpd if tpd>0 else 0
        else:mfe_pct=0
        live_rows.append((s,mfe_pct))
    live_rows.sort(key=lambda x:-x[1])
    if live_rows:
        L.append(f"\n📋 在追明细（{len(live_rows)}条，按止盈进度降序）：")
        for s,pct in live_rows:
            sym_s=s["sym"].replace("-USDT-SWAP","")
            t_str=datetime.fromtimestamp((s.get("alert_ts") or s.get("sent_ts"))/1000,BJ_TZ).strftime("%m-%d %H:%M") if (s.get("alert_ts") or s.get("sent_ts")) else ""
            sl_tag="(已通知保本)" if s.get("be_sent") else ""
            L.append(f"  {s['tf']}{TYP[s['type']]} {sym_s} {DIR[s['dir']]} 分数{s.get('score',0)} 入场{s.get('entry',0):.8g} 止损{s.get('sl',0):.8g}{sl_tag} 止盈{s.get('tp',0):.8g} 进度{pct*100:.0f}% 开仓{t_str}")
            
    if post_long(L):
        st["last_report"]=today;log("DAILY_REPORT",tw,tl,te,tto,len(lv))
    else:log("DAILY_REPORT 发送失败")

def try_res(sym,bar,side,sl,tp,ts0,ts1):
    bar_ms = {"1m": 60*1000, "5m": 5*60*1000, "15m": 15*60*1000, "1H": 3600*1000}
    need = int((ts1 - ts0) / bar_ms[bar]) + 10
    d=candles(sym,bar,min(need, 300))
    if d is None:return None
    f=d[(d.ts>ts0)&(d.ts<=ts1)]
    if len(f)==0:return None
    for _,r in f.iterrows():
        hs,ht=(r.l<=sl,r.h>=tp) if side=="LONG" else (r.h>=sl,r.l<=tp)
        if hs and ht:return "LOSS"
        if ht:return "WIN"
        if hs:return "LOSS"
    return None

def resolve(sym,side,sl,tp,ts0,ts1,tf):
    for b in ["1m","5m","15m"]:
        r=try_res(sym,b,side,sl,tp,ts0,ts1)
        if r is not None: return r

def prepare(d,tf,sym,st):
    if d is None or len(d)<210:return
    p=d.c.iloc[-1]
    a=atr(d);av=a.iloc[-1];base=a.iloc[-21:-1].mean()
    if pd.isna(av) or pd.isna(base):return
    e20=ema(d.c,20);e60=ema(d.c,60)
    vr=d.v.iloc[-1]/max(d.v.iloc[-21:-1].mean(),1e-12)
    vr3=d.v.iloc[-3:].mean()/max(d.v.iloc[-13:-3].mean(),1e-12)
    ar=av/base
    body=abs(p-d.o.iloc[-1])/max(d.h.iloc[-1]-d.l.iloc[-1],av*.01)
    bs=btc(TF[tf][0])
    h4_bias=h4_filter(sym)
    out=[]

    d4 = candles(sym, "4H", 300)
    d4_e20, d4_e60 = 0, 0
    if d4 is not None and len(d4) >= 60:
        d4_e20 = ema(d4.c, 20).iloc[-1]
        d4_e60 = ema(d4.c, 60).iloc[-1]

    gap_max = min(PREPARE_GAP_ATR * av / p, PREPARE_GAP_CAP.get(tf,0.02))
    disc_l = 0.97 if tf=="1D" else 0.985
    disc_s = 1.03 if tf=="1D" else 1.015

    for side in ("LONG","SHORT"):
        if side=="LONG" and p<=d.o.iloc[-1]:continue
        if side=="SHORT" and p>=d.o.iloc[-1]:continue

        if side=="LONG":
            d_e20=(p-e20.iloc[-1])/p
            if d_e20<0 or d_e20>gap_max:continue
        else:
            d_e20=(e20.iloc[-1]-p)/p
            if d_e20<0 or d_e20>gap_max:continue
        if abs(e20.iloc[-1]-e60.iloc[-1]) > 1.0*av:continue
        if side=="LONG" and e20.iloc[-1]<=e60.iloc[-1]:continue
        if side=="SHORT" and e20.iloc[-1]>=e60.iloc[-1]:continue
        if side=="LONG" and (p - e60.iloc[-1]) < 0.5*av:continue
        if side=="SHORT" and (e60.iloc[-1] - p) < 0.5*av:continue
        if vr>=2.0 or ar>1.6 or body>=.65:continue
        if (side=="LONG" and bs<0) or (side=="SHORT" and bs>0):continue
        if side=="LONG" and (p<e20.iloc[-1] or e20.iloc[-1]<e20.iloc[-6]):continue
        if side=="SHORT" and (p>e20.iloc[-1] or e20.iloc[-1]>e20.iloc[-6]):continue
        
        s=18 if ar<=1.0 else 14 if ar<=1.2 else 10 if ar<=1.5 else 6
        if tf=="1H":
            s+=22 if d_e20<=.005 else 18 if d_e20<=.010 else 12 if d_e20<=.020 else 4
        else:
            s+=22 if d_e20<=.015 else 18 if d_e20<=.030 else 12 if d_e20<=.060 else 4
        s+=23 + (14 if (.7<=vr<2.0 and vr3>=1.0) else 8 if (.6<=vr<2.0 and vr3>=0.9) else 4)
        s+=8 if ((bs>0) if side=="LONG" else (bs<0)) else 4 if bs==0 else 0
        s+=5 if (body<.35 and ar<=1) else 3 if body<.45 else 0

        need=73 if tf=="1H" else 72
        need_rr=1.5 if tf=="1H" else 2.0

        if side=="LONG":
            sl=calc_sl_struct(d,tf,p,"LONG",av);risk=p-sl
            if risk<=0:continue
            cands=find_swing_levels(d,tf,p,"res");tp=None
            for price,_ in cands:
                cd=price*disc_l
                if cd>p and (cd-p)/risk>=need_rr:
                    tp=cd;break
            if tp is None:tp=p+2.0*risk if tf!="1H" else p+2.2*risk
            rr=(tp-p)/risk
        else:
            sl=calc_sl_struct(d,tf,p,"SHORT",av);risk=sl-p
            if risk<=0:continue
            cands=find_swing_levels(d,tf,p,"sup");tp=None
            for price,_ in cands:
                cd=price*disc_s
                if cd<p and (p-cd)/risk>=need_rr:
                    tp=cd;break
            if tp is None:tp=p-2.0*risk if tf!="1H" else p-2.2*risk
            rr=(p-tp)/risk

        if s>=need and rr>=need_rr:
            h4_blocked = (side=="LONG" and h4_bias==-1) or (side=="SHORT" and h4_bias==1)
            ema4h_blocked = False
            if tf == "1H" and d4_e20 > 0:
                if side == "LONG" and d4_e20 <= d4_e60: ema4h_blocked = True
                if side == "SHORT" and d4_e20 >= d4_e60: ema4h_blocked = True
            if h4_blocked or ema4h_blocked:
                reasons=[]
                if h4_blocked: reasons.append("h4_bias")
                if ema4h_blocked: reasons.append("ema4h")
                rec_shadow(st,sym,tf,side,"PREPARE",p,sl,tp,reasons,d,int(s))
                continue
            out.append({"sym":sym,"tf":tf,"dir":side,"type":"PREPARE","score":int(s),"entry":p,"sl":sl,"tp":tp,"rr":rr,"anchor":int(d.ts.iloc[-11]),"hi":0.0,"lo":0.0})
    return max(out,key=lambda x:x["score"]) if out else None

def normal(d,tf,sym,st):
    if d is None or len(d)<210:return
    p=d.c.iloc[-1]
    e20s=ema(d.c,20);e60s=ema(d.c,60);e120s=ema(d.c,120)
    e20=e20s.iloc[-1];e60=e60s.iloc[-1];e120=e120s.iloc[-1]
    a=atr(d).iloc[-1];aa=atr(d).iloc[-6:-1].mean()
    if pd.isna(a) or pd.isna(aa):return
    vr=d.v.iloc[-1]/max(d.v.iloc[-21:-1].mean(),1e-12)
    body=abs(p-d.o.iloc[-1])/max(d.h.iloc[-1]-d.l.iloc[-1],a*.01)
    body_prev=abs(d.c.iloc[-2]-d.o.iloc[-2])/max(d.h.iloc[-2]-d.l.iloc[-2],a*.01)
    bs=btc(TF[tf][0]);h4_bias=h4_filter(sym)
    out=[]

    d4 = candles(sym, "4H", 300)
    d4_e20, d4_e60 = 0, 0
    if d4 is not None and len(d4) >= 60:
        d4_e20 = ema(d4.c, 20).iloc[-1]
        d4_e60 = ema(d4.c, 60).iloc[-1]

    disc_l = 0.97 if tf=="1D" else 0.985
    disc_s = 1.03 if tf=="1D" else 1.015
    gap_max = min(GAP_ATR.get(tf, 2.5) * a / p, GAP_CAP.get(tf,0.04))
    chase_atr = CHASE_ATR.get(tf,2.5)

    def add(side,typ,sl,tp,need_rr):
        if side=="LONG" and p<=d.o.iloc[-1]:return
        if side=="SHORT" and p>=d.o.iloc[-1]:return

        if (side=="LONG" and bs<0) or (side=="SHORT" and bs>0):return
        if sl==p or tp==p:return
        rr=(tp-p)/(p-sl) if side=="LONG" else (p-tp)/(sl-p)
        if rr<=0:return
        if typ=="TREND": v=5 if vr>=5 else 15 if vr>=4 else 25 if vr>=2.5 else 30 if vr>=1.5 else 15 if vr>=1.0 else 5
        else: v=30 if 2<=vr<2.5 else 25 if 2.5<=vr<3 else 15 if vr>=3 else 20 if vr>=1.5 else 10 if vr>=1.2 else 0
        s=v+(25 if body>=.6 else 12 if body>=.45 else 0)+(20 if a>aa*1.05 else 0)+(6 if bs==0 else min(max(bs if side=="LONG" else -bs,0),15))
        if (side=="LONG" and p>e20) or (side=="SHORT" and p<e20):s+=10
        if rr>=need_rr and s>=TF[tf][2]:
            h4_blocked = (side=="LONG" and h4_bias==-1) or (side=="SHORT" and h4_bias==1)
            ema4h_blocked = False
            if tf == "1H" and d4_e20 > 0:
                if side == "LONG" and d4_e20 <= d4_e60: ema4h_blocked = True
                if side == "SHORT" and d4_e20 >= d4_e60: ema4h_blocked = True
            if h4_blocked or ema4h_blocked:
                reasons=[]
                if h4_blocked: reasons.append("h4_bias")
                if ema4h_blocked: reasons.append("ema4h")
                rec_shadow(st,sym,tf,side,typ,p,sl,tp,reasons,d,int(s))
                return
            out.append({"sym":sym,"tf":tf,"dir":side,"type":typ,"score":int(s),"entry":p,"sl":sl,"tp":tp,"rr":rr})

    tr_rr = 1.5

    if p>e120 and p>e20 and e20>e60*1.001 and e20s.iloc[-1]>e20s.iloc[-4] and (p-e20)/p<gap_max:
        sl_c=calc_sl_struct(d,tf,p,"LONG",a);risk=p-sl_c
        if risk>0:
            cands=find_swing_levels(d,tf,p,"res")
            if cands:
                for price,_ in cands:
                    if price<=p:continue
                    tp_c=min(price*disc_l, p+5*a)
                    if (tp_c-p)/risk>=tr_rr:
                        add("LONG","TREND",sl_c,tp_c,tr_rr);break
            else: add("LONG","TREND",sl_c,p+5*a,tr_rr)
            
    if p<e120 and p<e20 and e20<e60*0.999 and e20s.iloc[-1]<e20s.iloc[-4] and (e20-p)/p<gap_max:
        sl_c=calc_sl_struct(d,tf,p,"SHORT",a);risk=sl_c-p
        if risk>0:
            cands=find_swing_levels(d,tf,p,"sup")
            if cands:
                for price,_ in cands:
                    if price>=p:continue
                    tp_c=max(price*disc_s, p-5*a)
                    if (p-tp_c)/risk>=tr_rr:
                        add("SHORT","TREND",sl_c,tp_c,tr_rr);break
            else: add("SHORT","TREND",sl_c,p-5*a,tr_rr)

    hi=d.h.iloc[-12:-2].max();lo=d.l.iloc[-12:-2].min()
    
    real_body = abs(p - d.o.iloc[-1])
    real_body_safe = max(real_body, a * 0.01)
    upper_wick = d.h.iloc[-1] - max(p, d.o.iloc[-1])
    lower_wick = min(p, d.o.iloc[-1]) - d.l.iloc[-1]

    if d.c.iloc[-2]>hi and d.l.iloc[-1]>hi and d.c.iloc[-1]>hi and d.c.iloc[-1]<=hi+chase_atr*a and 1.5<=vr<8.0 and upper_wick < real_body_safe * 0.5:
        sl_c = hi - 0.2 * a
        risk = p - sl_c
        if risk > 0:
            tp_c = p + 2.0 * risk
            add("LONG","BREAKOUT",sl_c,tp_c,2.0)
            
    if d.c.iloc[-2]<lo and d.h.iloc[-1]<lo and d.c.iloc[-1]<lo and d.c.iloc[-1]>=lo-chase_atr*a and 1.5<=vr<8.0 and lower_wick < real_body_safe * 0.5:
        sl_c = lo + 0.2 * a
        risk = sl_c - p
        if risk > 0:
            tp_c = p - 2.0 * risk
            add("SHORT","BREAKOUT",sl_c,tp_c,2.0)
            
    return max(out,key=lambda x:x["score"]) if out else None

def signal(d,tf,sym,st):
    p=prepare(d,tf,sym,st);n=normal(d,tf,sym,st)
    if p and n:return p if p["score"]>=n["score"] else n
    return p or n

def send_signal(st,s):
    t="🟡 启动前埋伏" if s["type"]=="PREPARE" else "🟢 突破启动" if s["type"]=="BREAKOUT" else "🔵 趋势信号"
    stt="贴均线埋伏" if (s["type"]=="PREPARE" and s["tf"]=="1H") else "启动前埋伏" if s["type"]=="PREPARE" else "突破已站稳" if s["type"]=="BREAKOUT" else "趋势跟随"
    txt=(f"宝宝巴士🚌上车就赚\n🚨 警报 {t}  {CN[s['tf']]}  {s['sym']}\n"
         f"{DIR[s['dir']]}  {TYP[s['type']]}  分数{s['score']}  盈亏比{s['rr']:.2f}\n"
         f"入场{s['entry']:.8g}  止损{s['sl']:.8g}  止盈{s['tp']:.8g}\n{stt}\n"
         f"{now_bj().strftime('%Y-%m-%d %H:%M:%S')}")
    if post(txt):
        key=sdk(s["sym"],s["tf"],s["dir"],s["type"])
        st.setdefault("sent",{})[key]=int(s["sent_ts"])
        s["alert_ts"]=int(time.time()*1000)
        s["be_sent"]=False
        if key not in {sdk(x.get("sym",""),x.get("tf",""),x.get("dir",""),x.get("type","")) for x in st.get("live",[])}:
            st.setdefault("live",[]).append({k:s[k] for k in ("sym","tf","dir","type","entry","sl","tp","score","rr","hi","lo","sent_ts","alert_ts","be_sent") if k in s})
        log("SEND",s["tf"],s["sym"],s["dir"],s["type"],s["score"])
        return True
    return False

def check_live(st):
    keep=[]
    for s in st.get("live") or []:
        tf=s.get("tf")
        if tf not in TF:continue
        d=candles(s["sym"],TF[tf][0],300)
        if d is None:keep.append(s);continue
        ts0=int(s.get("sent_ts") or s.get("anchor") or d.ts.iloc[-1])
        f=d[d.ts>ts0]
        if len(f)==0:keep.append(s);continue
        
        sl,tp,side=s["sl"],s["tp"],s["dir"];typ=s.get("type","")
        ent=s.get("entry",0) or 0;tp_v=s.get("tp",0) or 0
        
        res_status = None
        
        for _, r in f.iterrows():
            hs = (r.l <= sl) if side == "LONG" else (r.h >= sl)
            ht = (r.h >= tp) if side == "LONG" else (r.l <= tp)
            
            if hs and ht:
                r_res = resolve(s["sym"], side, sl, tp, int(r.ts)-3600*1000, int(r.ts), tf)
                if r_res == "LOSS":
                    res_status = "LOSS"
                    break
                else:
                    res_status = "WIN"
                    break
            elif hs:
                res_status = "LOSS"
                break
            elif ht:
                res_status = "WIN"
                break
                
        hi=f.h.max();lo=f.l.min()
        mfe=(hi-ent)/ent if (side=="LONG" and ent) else (ent-lo)/ent if ent else 0
        if ent and tp_v:
            tpd=tp_v-ent if side=="LONG" else ent-tp_v
            mfp=hi-ent if side=="LONG" else ent-lo
            mfe_pct=mfp/tpd if tpd>0 else 0
        else:mfe_pct=0
            
        if res_status == "LOSS":
            mark(st,s["sym"],tf,side,typ,d,"cool");rec(st,s,"LOSS",mfe,mfe_pct)
            log("SL",s["sym"],tf,f"MFE={mfe*100:.2f}% 进度{min(mfe_pct,1)*100:.0f}%");continue
        if res_status == "WIN":
            mark(st,s["sym"],tf,side,typ,d,"cool");rec(st,s,"WIN",mfe,mfe_pct)
            log("TP",s["sym"],tf,f"MFE={mfe*100:.2f}% 进度{min(mfe_pct,1)*100:.0f}%");continue
            
        if bars_since(d,ts0)>=MAX_HOLD.get(tf,100):
            rec(st,s,"TIMEOUT",mfe,mfe_pct);log("TIMEOUT",s["sym"],tf,f"MFE={mfe*100:.2f}% 进度{min(mfe_pct,1)*100:.0f}%");continue
        keep.append(s)
    st["live"]=keep[-MAX_LIVE:]

def check_shadow(st):
    keep=[]
    for s in st.get("shadow") or []:
        tf=s.get("tf")
        if tf not in TF:continue
        d=candles(s["sym"],TF[tf][0],300)
        if d is None:keep.append(s);continue
        ts0=int(s.get("sent_ts") or d.ts.iloc[-1])
        f=d[d.ts>ts0]
        if len(f)==0:keep.append(s);continue

        sl,tp,side=s["sl"],s["tp"],s["dir"]
        ent=s.get("entry",0) or 0
        res_status=None
        for _,r in f.iterrows():
            hs=(r.l<=sl) if side=="LONG" else (r.h>=sl)
            ht=(r.h>=tp) if side=="LONG" else (r.l<=tp)
            if hs and ht:
                r_res=resolve(s["sym"],side,sl,tp,int(r.ts)-3600*1000,int(r.ts),tf)
                res_status="LOSS" if r_res=="LOSS" else "WIN"
                break
            elif hs:
                res_status="LOSS";break
            elif ht:
                res_status="WIN";break

        if res_status is None:
            if bars_since(d,ts0)>=MAX_HOLD.get(tf,100):
                res_status="TIMEOUT"
            else:
                keep.append(s);continue

        hi=f.h.max();lo=f.l.min()
        mfe=(hi-ent)/ent if (side=="LONG" and ent) else (ent-lo)/ent if ent else 0
        tp_d=tp-ent if side=="LONG" else ent-tp
        mfp=hi-ent if side=="LONG" else ent-lo
        mfe_pct=(mfp/tp_d) if tp_d>0 else 0

        st.setdefault("results",[]).append({
            "sym":s["sym"],"tf":tf,"dir":side,"type":s.get("type","PREPARE"),
            "result":res_status,"ts":int(time.time()),
            "rec_ts":int(time.time()*1000),
            "score":s.get("score",0),
            "alert_ts":s.get("alert_ts"),
            "sig_ts":s.get("sent_ts"),
            "entry":ent,"sl":sl,"tp":tp,
            "mfe":round(mfe*100,2),
            "mfe_pct":round(min(mfe_pct,1.0)*100,1),
            "shadow":True,
            "reasons":s.get("reasons",[]),
        })
        log("SHADOW",res_status,s["sym"],tf,"+".join(s.get("reasons",[])),f"MFE={mfe*100:.2f}%")
    st["shadow"]=keep[-MAX_SHADOW:]

def scan():
    btc_cache.clear()
    st=load_state();clean(st)
    check_live(st)
    check_shadow(st)
    save_state(st)
    daily_report(st)
    ss=coins()
    if not ss:log("no coins");save_state(st);return

    for tf,(bar,n,_,_) in TF.items():
        if stop:break
        if not tf_run(tf):continue
        log(f"[扫描] {tf}")
        hits=[]
        for sym in ss:
            if stop:break
            try:
                dd=candles(sym,bar,n)
                s=signal(dd,tf,sym,st)
                if not s:continue
                if too_soon(st,s["sym"],tf,s["dir"],s["type"],dd,DEDUP_BARS):continue
                
                in_live = False
                for lv_item in st.get("live", []):
                    if lv_item.get("sym") == s["sym"] and lv_item.get("tf") == tf:
                        in_live = True
                        break
                if in_live: continue

                key=sdk(s["sym"],tf,s["dir"],s["type"])
                if key in st.get("cool",{}) and bars_since(dd,int(st["cool"][key]))<COOL_BARS:continue
                s["sent_ts"]=int(dd.ts.iloc[-1])
                hits.append(s)
            except Exception as e:
                log(f"[SCAN] {sym} {tf} {type(e).__name__} {e}")
                continue
        hits.sort(key=lambda x:-x["score"])
        cnt=0
        for s in hits[:MAX_SEND_PER_SCAN]:
            if send_signal(st,s):cnt+=1
            time.sleep(.3)
        save_state(st)
        log(f"[{tf}] hits {len(hits)} sent {cnt}")

    print(now_bj().strftime("%H:%M:%S"),"完成")

if __name__=="__main__":
    scan()
