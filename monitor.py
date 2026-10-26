# 宝宝巴士🚌上车就赚 - OKX 信号扫描 (1H/1D) - 纯净测试版
import os, time, json, random, requests, pandas as pd
from datetime import datetime, timezone, timedelta

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
            "Accept-Language":"zh-CN,zh;q=0.9,en;q=0.8","Connection":"keep-alive","Referer":"https://www.okx.com/"}

def now_bj():return datetime.now(BJ_TZ)
def log(*args):print(*args)

TF={"1H":("1H",500,73,6),"1D":("1D",300,72,4)}
MAX_HOLD={"1H":100,"1D":30}
CHASE_ATR={"1H":2.5,"1D":1.5}
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

cache={};btc_cache={};stop=False;last_req=[0.0]

def tf_run(tf):
    return datetime.now(timezone.utc).hour==0 if tf=="1D" else True

def throttle():
    wait=last_req[0]+.3+random.random()*.3-time.time()
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
            if r.status_code in (403,451):log(f"[OKX] HTTP {r.status_code} 停止本轮");stop=True;return
            if r.status_code==418:log("[OKX] 418 被封 等60s");time.sleep(60);continue
            if r.status_code==429 or r.status_code>=500:
                w=min(3*2**i,30)+random.random()*2;log(f"[OKX] HTTP {r.status_code} 等{w:.1f}s");time.sleep(w);continue
            x=r.json()
            if x.get("code")=="0":return x.get("data",[])
            if x.get("code")=="50011":
                w=min(3*2**i,30)+random.random()*2;log(f"[OKX] 50011 等{w:.1f}s");time.sleep(w);continue
            return
        except Exception as e:log(f"[OKX] {type(e).__name__}");time.sleep(min(2**i*2,20))

def coins():
    a=get("/api/v5/public/instruments",{"instType":"SWAP"})
    b=get("/api/v5/market/tickers",{"instType":"SWAP"})
    if not a or not b:return []
    # 源头过滤：强制只保留 instCategory=="1" (纯币圈)
    live={x["instId"] for x in a if x.get("settleCcy")=="USDT" and x.get("state")=="live" and x.get("instCategory")=="1"}
    vol={x["instId"]:float(x.get("volCcy24h",0) or 0)*float(x.get("last",0) or 0) for x in b}
    valid=[s for s in live if vol.get(s,0)>=MIN_VOL]
    r=sorted(valid,key=lambda x:vol.get(x,0),reverse=True)[:150]
    if r:log(f"[币种] 纯币圈 ≥{MIN_VOL/1e8:.2f}亿的共{len(valid)}个，取{len(r)}个")
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
        bar_ms=86400*1000 if bar=="1D" else (15*60*1000 if bar=="15m" else (5*60*1000 if bar=="5m" else (1*60*1000 if bar=="1m" else 3600*1000)))
        if d.ts.iloc[-1]+bar_ms>int(time.time()*1000):d=d.iloc[:-1].reset_index(drop=True)
        cache[k]=(time.time(),d);return d
    except Exception as e:log(f"[CANDLES] {sym} {bar} {type(e).__name__} {e}");return

def ema(s,n):return s.ewm(span=n,adjust=False).mean()
def atr(d):return pd.concat([d.h-d.l,(d.h-d.c.shift()).abs(),(d.l-d.c.shift()).abs()],axis=1).max(axis=1).rolling(14).mean()
def bars_since(d,ts):return max(int(len(d[d.ts>=ts]))-1,0)

def find_swing_levels(d,tf,p,side):
    n_l, n_r, look = 5, (2 if tf=="1H" else 3), (500 if tf=="1H" else 300)
    arr_h, arr_l, arr_v = d.h.iloc[-look:-1].values, d.l.iloc[-look:-1].values, d.v.iloc[-look:-1].values
    m=len(arr_h)
    if m<n_l+n_r+1:return []
    avg_vol=d.v.iloc[-21:-1].mean()
    if pd.isna(avg_vol) or avg_vol<=0:avg_vol=1
    cands=[]
    for i in range(n_l,m-n_r):
        if side=="res" and arr_h[i]>=arr_h[i-n_l:i].max() and arr_h[i]>=arr_h[i+1:i+n_r+1].max() and arr_h[i]>p:
            price=arr_h[i]; tol=price*0.005; hits=int(((arr_h>price-tol)&(arr_h<price+tol)).sum())
            cands.append((price,(arr_v[i]/avg_vol)*(1+(i/m))+hits*0.5))
        elif side=="sup" and arr_l[i]<=arr_l[i-n_l:i].min() and arr_l[i]<=arr_l[i+1:i+n_r+1].min() and arr_l[i]<p:
            price=arr_l[i]; tol=price*0.005; hits=int(((arr_l>price-tol)&(arr_l<price+tol)).sum())
            cands.append((price,(arr_v[i]/avg_vol)*(1+(i/m))+hits*0.5))
    if not cands:return []
    cands.sort(key=lambda x:-x[1])
    used=[False]*len(cands); merged=[]
    for i in range(len(cands)):
        if used[i]:continue
        price, score = cands[i]; group=[price]; used[i]=True
        for j in range(i+1,len(cands)):
            if not used[j] and abs(cands[j][0]-price)/price<0.003:group.append(cands[j][0]);used[j]=True
        merged.append((sum(group)/len(group),score))
    merged=[x for x in merged if x[1]>=0.5]
    if not merged:return []
    merged.sort(key=lambda x:abs(x[0]-p))
    return merged

def calc_sl_struct(d,tf,p,side,a):
    sl_cap, buf = (2.0 if tf=="1H" else 2.5)*a, 1.0*a
    if side=="LONG":
        sup_list=find_swing_levels(d,tf,p,"sup")
        if not sup_list:return p - sl_cap
        sl_c = sup_list[0][0] - buf
        return p - sl_cap if (p - sl_c) > sl_cap else sl_c
    else:
        res_list=find_swing_levels(d,tf,p,"res")
        if not res_list:return p + sl_cap
        sl_c = res_list[0][0] + buf
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
    o,c,amp=d4.o.iloc[-1],d4.c.iloc[-1],d4.h.iloc[-1]-d4.l.iloc[-1]
    if amp > 1.5*a4:return 1 if c>o else -1 if c<o else 0
    return 0

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

def rec(st,s,r,mfe,mfe_pct,mae,mae_pct):
    e={"sym":s["sym"],"tf":s["tf"],"dir":s["dir"],"type":s["type"],"result":r,"ts":int(time.time()),
       "rec_ts":int(time.time()*1000),"score":s.get("score",0),"mfe":round(mfe*100,2),
       "mfe_pct":round(min(mfe_pct,1.0)*100,1),"mae":round(mae*100,2),"mae_pct":round(min(mae_pct,1.0)*100,1)}
    for k in ("alert_ts","sent_ts","entry","sl","tp"):
        if s.get(k):e["sig_ts" if k=="sent_ts" else k]=s[k]
    st.setdefault("results",[]).append(e)

def rec_shadow(st,sym,tf,side,typ,p,sl,tp,reasons,d,score):
    if any(lv.get("sym")==sym and lv.get("tf")==tf for lv in st.get("live",[])):return
    if len(st.get("shadow",[]))>=MAX_SHADOW:return log("SHADOW_FULL skip",sym)
    key=f"{sym}|{tf}|{side}|{','.join(reasons)}"; cool=st.setdefault("shadow_cool",{})
    if key in cool and bars_since(d,cool[key])<COOL_BARS:return
    if abs(p-sl)<1e-12 or abs(p-tp)<1e-12:return
    st.setdefault("shadow",[]).append({"sym":sym,"tf":tf,"dir":side,"type":typ,"reasons":list(reasons),
        "entry":float(p),"sl":float(sl),"tp":float(tp),"score":int(score),"ts":int(time.time()),"sent_ts":int(d.ts.iloc[-1])})
    cool[key]=int(d.ts.iloc[-1])
    log("SHADOW_REC",sym,tf,side,typ,"+".join(reasons),score)

def post(txt):
    if not WEBHOOK:return False
    for i in range(3):
        try:
            r=requests.post(WEBHOOK,json={"msg_type":"text","content":{"text":txt}},timeout=15)
            if r.status_code==200 and r.json().get("code")==0:return True
            if r.status_code==429:time.sleep(min(5*2**i,30));continue
            return False
        except:time.sleep(2*(i+1))
    return False

def post_long(lines):
    batches=[];cur=[];cur_len=0
    for line in lines:
        if cur_len+len(line)+1>1500 and cur:batches.append(cur);cur=[];cur_len=0
        cur.append(line);cur_len+=len(line)+1
    if cur:batches.append(cur)
    total=len(batches);ok=True
    for idx,batch in enumerate(batches,1):
        header=f"🚨 警报 日报（第{idx}/{total}批）\n" if total>1 else ""
        if not post(header+"\n".join(batch)):ok=False
        if idx<total:time.sleep(2.0)
    return ok

def clean(st):
    c=(int(time.time())-KEEP_SENT_DAYS*86400)*1000
    st["sent"]={k:v for k,v in st.get("sent",{}).items() if v>=c}
    st["cool"]={k:v for k,v in st.get("cool",{}).items() if v>=c}
    st["shadow_cool"]={k:v for k,v in st.get("shadow_cool",{}).items() if v>=(int(time.time())-SHADOW_COOL_KEEP_DAYS*86400)*1000}
    kc_a, kc_s = int(time.time())-KEEP_RESULT_DAYS*86400, int(time.time())-SHADOW_KEEP_DAYS*86400
    st["results"]=[r for r in st.get("results",[]) if (r["ts"]>=kc_s if r.get("shadow") else r["ts"]>=kc_a)]

def fmt_time(ts):
    return datetime.fromtimestamp(ts/1000,BJ_TZ).strftime("%m-%d %H:%M") if ts else ""

def fmt_line(r):
    sym=r["sym"].replace("-USDT-SWAP","")
    t_open=fmt_time(r.get("alert_ts") or r.get("sig_ts"))
    t_close=fmt_time(r.get("rec_ts"))
    mae=f" 险情{r.get('mae_pct',0):.0f}%" if "mae_pct" in r else ""
    close_tag=f"平:{t_close}" if r["result"]!="TIMEOUT" else f"超时:{t_close}"
    return f"  {r['tf']}{TYP[r['type']]} {sym} {DIR.get(r.get('dir',''),'?')} 分{r.get('score',0)} 入{r.get('entry',0):.8g} 损{r.get('sl',0):.8g} 盈{r.get('tp',0):.8g} MFE={r['mfe']}% 进度{r.get('mfe_pct',0):.0f}%{mae} 开:{t_open} {close_tag}"

def daily_report(st):
    n=datetime.now(timezone.utc);today=n.strftime("%Y-%m-%d")
    if st.get("last_report")==today or n.hour>=12:return
    cut=int(time.time())-86400
    rs=[r for r in st.get("results",[]) if r["ts"]>=cut and not r.get("shadow") and r.get("type")!="BREAKOUT"]
    sh=[r for r in st.get("results",[]) if r["ts"]>=cut and r.get("shadow") and r.get("type")!="BREAKOUT"]
    L=["宝宝巴士🚌上车就赚","📊 每日统计（警报）"]; tw=tl=tbe=tto=0; lv=[x for x in st.get("live",[]) if x.get("type")!="BREAKOUT"]
    
    for tf in ("1H","1D"):
        for typ in ("PREPARE","TREND"):
            sub=[r for r in rs if r.get("tf")==tf and r.get("type")==typ]
            w,l,be,to=[sum(1 for r in sub if r["result"]==k) for k in ("WIN","LOSS","BE","TIMEOUT")]
            p=sum(1 for x in lv if x.get("tf")==tf and x.get("type")==typ)
            tw+=w;tl+=l;tbe+=be;tto+=to
            wm=[r["mfe"] for r in sub if r["result"]=="WIN" and "mfe" in r]; lm=[r["mfe"] for r in sub if r["result"]=="LOSS" and "mfe" in r]
            wa=sum(wm)/len(wm) if wm else 0; la=sum(lm)/len(lm) if lm else 0
            L.append(f"警报 {tf}{TYP[typ]}：止盈{w} 保本{be} 止损{l} 超时{to} 在追{p} 胜率{(w/(w+l)*100) if (w+l) else 0:.0f}% MFE均 胜{wa:.2f}% 负{la:.2f}%")
    
    L.append(f"警报 合计：止盈{tw} 保本{tbe} 止损{tl} 超时{tto} 在追{len(lv)} 胜率{(tw/(tw+tl)*100) if (tw+tl) else 0:.0f}%")
    L.append(f"警报 统计时间：{now_bj().strftime('%Y-%m-%d %H:%M:%S')}")

    if sh or st.get("shadow"):
        L.append(f"\n👻 影子单（被 4H 过滤拦掉的候选，仅观测）")
        for tf in ("1H","1D"):
            for typ in ("PREPARE","TREND"):
                sub=[r for r in sh if r.get("tf")==tf and r.get("type")==typ]
                if not sub:continue
                L.append(f"  【{tf} {TYP[typ]}】")
                groups={}
                for r in sub: groups.setdefault("+".join(r.get("reasons") or ["?"]),[]).append(r)
                for k,sb in sorted(groups.items()):
                    w,l=sum(1 for r in sb if r["result"]=="WIN"),sum(1 for r in sb if r["result"]=="LOSS")
                    mfes=[r["mfe"] for r in sb if "mfe" in r]; ma=sum(mfes)/len(mfes) if mfes else 0
                    L.append(f"    被拦[{k}]：盈{w} 损{l} 胜率{(w/(w+l)*100) if (w+l) else 0:.0f}% MFE均{ma:.2f}%")
                    for r in sb:
                        sym_s=r["sym"].replace("-USDT-SWAP","")
                        t_open=fmt_time(r.get("alert_ts") or r.get("sig_ts"))
                        t_close=fmt_time(r.get("rec_ts"))
                        mae=f" 险情{r.get('mae_pct',0):.0f}%" if "mae_pct" in r else ""
                        close_tag=f"平:{t_close}" if r["result"]!="TIMEOUT" else f"超时:{t_close}"
                        L.append(f"      {DIR.get(r.get('dir',''),'?')} {sym_s} 入{r.get('entry',0):.8g} 损{r.get('sl',0):.8g} 盈{r.get('tp',0):.8g} MFE={r['mfe']}% 进度{r.get('mfe_pct',0):.0f}%{mae} 开:{t_open} {close_tag}")

        sh_live=[x for x in st.get("shadow",[]) if x.get("type")!="BREAKOUT"]
        if sh_live:
            L.append(f"\n👻 影子在跟明细（{len(sh_live)}条）：")
            for s in sh_live:
                L.append(f"  {s['tf']}{TYP[s['type']]} {s['sym'].replace('-USDT-SWAP','')} {DIR[s['dir']]} 被拦[{'+'.join(s.get('reasons',[]))}] 入{s.get('entry',0):.8g} 损{s.get('sl',0):.8g} 盈{s.get('tp',0):.8g}")
        L.append(f"  影子在跟：{len(sh_live)}/{MAX_SHADOW} 条")
        L.append(f"  口径：影子单 sl/tp 按真实逻辑算，跟到止盈/止损/超时")

    for title, key in [("🏆 止盈明细","WIN"),("🛡️ 保本明细","BE"),("⏰ 超时明细","TIMEOUT"),("📋 止损明细","LOSS")]:
        sub=[r for r in rs if r["result"]==key and "mfe" in r]
        sub.sort(key=lambda x:-(x.get("mfe_pct",0)) if key in ("TIMEOUT","LOSS") else -x.get("score",0))
        if sub:
            L.append(f"\n{title}（{len(sub)}条）：")
            L.extend(fmt_line(r) for r in sub)
            
    if lv:
        L.append(f"\n📋 在追明细（{len(lv)}条）：")
        for s in lv:
            L.append(f"  {s['tf']}{TYP[s['type']]} {s['sym'].replace('-USDT-SWAP','')} {DIR[s['dir']]} 分{s.get('score',0)} 入{s.get('entry',0):.8g} 损{s.get('sl',0):.8g} 盈{s.get('tp',0):.8g} 开:{fmt_time(s.get('alert_ts'))}")
            
    if post_long(L):st["last_report"]=today;log("DAILY_REPORT",tw,tl,tto,len(lv))
    else:log("DAILY_REPORT 发送失败")

def try_res(sym,bar,side,sl,tp,ts0,ts1):
    need = int((ts1 - ts0) / ({"1m":60000,"5m":300000,"15m":900000,"1H":3600000}[bar])) + 10
    d=candles(sym,bar,min(need, 300))
    if d is None:return None
    f=d[(d.ts>ts0)&(d.ts<=ts1)]
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

def check_hit(f,side,sl,tp,sym,tf):
    for _, r in f.iterrows():
        hs = (r.l <= sl) if side == "LONG" else (r.h >= sl)
        ht = (r.h >= tp) if side == "LONG" else (r.l <= tp)
        if hs and ht:return "LOSS" if resolve(sym,side,sl,tp,int(r.ts)-3600000,int(r.ts),tf)=="LOSS" else "WIN"
        elif hs:return "LOSS"
        elif ht:return "WIN"
    return None

def calc_mfe_mae(f,side,ent,sl,tp):
    hi,lo=f.h.max(),f.l.min()
    mfe=(hi-ent)/ent if (side=="LONG" and ent) else (ent-lo)/ent if ent else 0
    tpd,tpl=tp-ent if side=="LONG" else ent-tp, hi-ent if side=="LONG" else ent-lo
    mfe_pct=tpl/tpd if tpd>0 else 0
    risk=abs(ent-sl) if sl else 0
    mae=max((ent-lo) if side=="LONG" else (hi-ent),0)
    return mfe, mfe_pct, mae, (mae/risk if risk>0 else 0)

def prepare(d,tf,sym,st):
    if d is None or len(d)<210:return
    p,a,av,base=d.c.iloc[-1],atr(d),atr(d).iloc[-1],atr(d).iloc[-21:-1].mean()
    if pd.isna(av) or pd.isna(base):return
    e20,e60=ema(d.c,20),ema(d.c,60)
    vr=d.v.iloc[-1]/max(d.v.iloc[-21:-1].mean(),1e-12); vr3=d.v.iloc[-3:].mean()/max(d.v.iloc[-13:-3].mean(),1e-12)
    ar,body,bs=av/base,abs(p-d.o.iloc[-1])/max(d.h.iloc[-1]-d.l.iloc[-1],av*.01),btc(TF[tf][0])
    h4_bias=h4_filter(sym)
    out=[]
    gap_max=min(PREPARE_GAP_ATR*av/p,PREPARE_GAP_CAP.get(tf,0.02))
    for side in ("LONG","SHORT"):
        if (side=="LONG" and p<=d.o.iloc[-1]) or (side=="SHORT" and p>=d.o.iloc[-1]):continue
        d_e20=(p-e20.iloc[-1])/p if side=="LONG" else (e20.iloc[-1]-p)/p
        if d_e20<0 or d_e20>gap_max:continue
        
        # 均线粘合过滤：间距必须大于0.3 ATR
        if abs(e20.iloc[-1]-e60.iloc[-1]) < 0.3*av: continue

        if (side=="LONG" and e20.iloc[-1]<=e60.iloc[-1]) or (side=="SHORT" and e20.iloc[-1]>=e60.iloc[-1]):continue
        if (side=="LONG" and (p-e60.iloc[-1])<0.5*av) or (side=="SHORT" and (e60.iloc[-1]-p)<0.5*av):continue
        if vr>=2.0 or ar>1.6 or body>=.65:continue
        if (side=="LONG" and bs<0) or (side=="SHORT" and bs>0):continue
        if (side=="LONG" and (p<e20.iloc[-1] or e20.iloc[-1]<e20.iloc[-6])) or (side=="SHORT" and (p>e20.iloc[-1] or e20.iloc[-1]>e20.iloc[-6])):continue
        s=18 if ar<=1.0 else 14 if ar<=1.2 else 10 if ar<=1.5 else 6
        s+=22 if d_e20<=(.005 if tf=="1H" else .015) else 18 if d_e20<=(.010 if tf=="1H" else .030) else 12 if d_e20<=(.020 if tf=="1H" else .060) else 4
        s+=23+(14 if (.7<=vr<2.0 and vr3>=1.0) else 8 if (.6<=vr<2.0 and vr3>=0.9) else 4)
        s+=8 if ((bs>0) if side=="LONG" else (bs<0)) else 4 if bs==0 else 0
        s+=5 if (body<.35 and ar<=1) else 3 if body<.45 else 0
        need,need_rr=73 if tf=="1H" else 72,1.5 if tf=="1H" else 2.0
        sl=calc_sl_struct(d,tf,p,side,av); risk=(p-sl) if side=="LONG" else (sl-p)
        if risk<=0:continue
        cands=find_swing_levels(d,tf,p,"res" if side=="LONG" else "sup"); tp=None
        for price,_ in cands:
            cd=price*(0.97 if tf=="1D" else 0.985) if side=="LONG" else price*(1.03 if tf=="1D" else 1.015)
            if (side=="LONG" and cd>p and (cd-p)/risk>=need_rr) or (side=="SHORT" and cd<p and (p-cd)/risk>=need_rr):
                tp=cd;break
        if tp is None:tp=p+(2.0 if tf!="1H" else 2.2)*risk if side=="LONG" else p-(2.0 if tf!="1H" else 2.2)*risk
        rr=((tp-p)/risk) if side=="LONG" else ((p-tp)/risk)
        if s>=need and rr>=need_rr:
            h4b=(side=="LONG" and h4_bias==-1) or (side=="SHORT" and h4_bias==1)
            if h4b:
                rec_shadow(st,sym,tf,side,"PREPARE",p,sl,tp,["h4_bias"],d,int(s));continue
            out.append({"sym":sym,"tf":tf,"dir":side,"type":"PREPARE","score":int(s),"entry":p,"sl":sl,"tp":tp,"rr":rr,"anchor":int(d.ts.iloc[-11]),"hi":0.0,"lo":0.0})
    return max(out,key=lambda x:x["score"]) if out else None

def normal(d,tf,sym,st):
    if d is None or len(d)<210:return
    p,a,aa=d.c.iloc[-1],atr(d).iloc[-1],atr(d).iloc[-6:-1].mean()
    if pd.isna(a) or pd.isna(aa):return
    e20s,e60s,e120s=ema(d.c,20),ema(d.c,60),ema(d.c,120)
    e20,e60,e120=e20s.iloc[-1],e60s.iloc[-1],e120s.iloc[-1]
    vr,body,bs=d.v.iloc[-1]/max(d.v.iloc[-21:-1].mean(),1e-12),abs(p-d.o.iloc[-1])/max(d.h.iloc[-1]-d.l.iloc[-1],a*.01),btc(TF[tf][0])
    h4_bias=h4_filter(sym)
    chase=CHASE_ATR.get(tf,2.5)
    out=[]
    def add(side,typ,sl,tp,need_rr):
        if (side=="LONG" and p<=d.o.iloc[-1]) or (side=="SHORT" and p>=d.o.iloc[-1]) or (side=="LONG" and bs<0) or (side=="SHORT" and bs>0):return
        if sl==p or tp==p:return
        rr=((tp-p)/(p-sl)) if side=="LONG" else ((p-tp)/(sl-p))
        if rr<=0:return
        
        # 核心改进：1.0 ATR 偏离限制，仅针对趋势单 (TREND)
        if typ=="TREND":
            if side=="LONG" and (p-e20) > 1.0*a: return
            if side=="SHORT" and (e20-p) > 1.0*a: return
            
        # 均线粘合过滤：间距必须大于0.3 ATR
        if abs(e20-e60) < 0.3*a: return

        v=5 if vr>=5 else 15 if vr>=4 else 25 if vr>=2.5 else 30 if vr>=1.5 else 15 if vr>=1.0 else 5
        if typ!="TREND":v=30 if 2<=vr<2.5 else 25 if 2.5<=vr<3 else 15 if vr>=3 else 20 if vr>=1.5 else 10 if vr>=1.2 else 0
        s=v+(25 if body>=.6 else 12 if body>=.45 else 0)+(20 if a>aa*1.05 else 0)+(6 if bs==0 else min(max(bs if side=="LONG" else -bs,0),15))
        if (side=="LONG" and p>e20) or (side=="SHORT" and p<e20):s+=10
        if rr>=need_rr and s>=TF[tf][2]:
            h4b=(side=="LONG" and h4_bias==-1) or (side=="SHORT" and h4_bias==1)
            if h4b:
                rec_shadow(st,sym,tf,side,typ,p,sl,tp,["h4_bias"],d,int(s));return
            out.append({"sym":sym,"tf":tf,"dir":side,"type":typ,"score":int(s),"entry":p,"sl":sl,"tp":tp,"rr":rr})
    tr_rr=1.5
    if p>e120 and p>e20 and e20>e60*1.001 and e20s.iloc[-1]>e20s.iloc[-4]:
        sl_c=calc_sl_struct(d,tf,p,"LONG",a);risk=p-sl_c
        if risk>0:
            cands=find_swing_levels(d,tf,p,"res");done=False
            for price,_ in cands:
                tp_c=min(price*(0.97 if tf=="1D" else 0.985),p+5*a)
                if price>p and (tp_c-p)/risk>=tr_rr:add("LONG","TREND",sl_c,tp_c,tr_rr);done=True;break
            if not done:add("LONG","TREND",sl_c,p+5*a,tr_rr)
    if p<e120 and p<e20 and e20<e60*0.999 and e20s.iloc[-1]<e20s.iloc[-4]:
        sl_c=calc_sl_struct(d,tf,p,"SHORT",a);risk=sl_c-p
        if risk>0:
            cands=find_swing_levels(d,tf,p,"sup");done=False
            for price,_ in cands:
                tp_c=max(price*(1.03 if tf=="1D" else 1.015),p-5*a)
                if price<p and (p-tp_c)/risk>=tr_rr:add("SHORT","TREND",sl_c,tp_c,tr_rr);done=True;break
            if not done:add("SHORT","TREND",sl_c,p-5*a,tr_rr)
    hi,lo,real_body=d.h.iloc[-12:-2].max(),d.l.iloc[-12:-2].min(),abs(p-d.o.iloc[-1])
    if d.c.iloc[-2]>hi and d.l.iloc[-1]>hi and d.c.iloc[-1]>hi and d.c.iloc[-1]<=hi+chase*a and 1.5<=vr<8.0 and (d.h.iloc[-1]-max(p,d.o.iloc[-1]))<max(real_body,a*0.01)*0.5:
        sl_c=hi-0.2*a;risk=p-sl_c
        if risk>0:add("LONG","BREAKOUT",sl_c,p+2.0*risk,2.0)
    if d.c.iloc[-2]<lo and d.h.iloc[-1]<lo and d.c.iloc[-1]<lo and d.c.iloc[-1]>=lo-chase*a and 1.5<=vr<8.0 and (min(p,d.o.iloc[-1])-d.l.iloc[-1])<max(real_body,a*0.01)*0.5:
        sl_c=lo+0.2*a;risk=sl_c-p
        if risk>0:add("SHORT","BREAKOUT",sl_c,p-2.0*risk,2.0)
    return max(out,key=lambda x:x["score"]) if out else None

def signal(d,tf,sym,st):
    p,n=prepare(d,tf,sym,st),normal(d,tf,sym,st)
    return p if p and n and p["score"]>=n["score"] else n if p and n else p or n

def send_signal(st,s):
    t="🟡 启动前埋伏" if s["type"]=="PREPARE" else "🟢 突破启动" if s["type"]=="BREAKOUT" else "🔵 趋势信号"
    stt="贴均线埋伏" if (s["type"]=="PREPARE" and s["tf"]=="1H") else "启动前埋伏" if s["type"]=="PREPARE" else "突破已站稳" if s["type"]=="BREAKOUT" else "趋势跟随"
    txt=f"宝宝巴士🚌上车就赚\n🚨 警报 {t}  {CN[s['tf']]}  {s['sym']}\n{DIR[s['dir']]}  {TYP[s['type']]}  分数{s['score']}  盈亏比{s['rr']:.2f}\n入场{s['entry']:.8g}  止损{s['sl']:.8g}  止盈{s['tp']:.8g}\n{stt}\n{now_bj().strftime('%Y-%m-%d %H:%M:%S')}"
    if post(txt):
        key=sdk(s["sym"],s["tf"],s["dir"],s["type"]); st.setdefault("sent",{})[key]=int(s["sent_ts"]); s["alert_ts"]=int(time.time()*1000); s["be_sent"]=False
        if key not in {sdk(x.get("sym",""),x.get("tf",""),x.get("dir",""),x.get("type","")) for x in st.get("live",[])}:
            st.setdefault("live",[]).append({k:s[k] for k in ("sym","tf","dir","type","entry","sl","tp","score","rr","hi","lo","sent_ts","alert_ts","be_sent") if k in s})
        log("SEND",s["tf"],s["sym"],s["dir"],s["type"],s["score"]);return True
    return False

def check_live(st):
    keep=[]
    for s in st.get("live") or []:
        tf=s.get("tf")
        if tf not in TF:keep.append(s);continue
        d=candles(s["sym"],TF[tf][0],300)
        if d is None:keep.append(s);continue
        ts0=int(s.get("sent_ts") or s.get("anchor") or d.ts.iloc[-1]); f=d[d.ts>ts0]
        if len(f)==0:keep.append(s);continue
        rs=check_hit(f,s["dir"],s["sl"],s["tp"],s["sym"],tf)
        if rs is None and bars_since(d,ts0)>=MAX_HOLD.get(tf,100):rs="TIMEOUT"
        if rs:
            mfe,mfp,mae,map_=calc_mfe_mae(f,s["dir"],s.get("entry",0),s["sl"],s["tp"])
            if rs in ("LOSS","WIN"):mark(st,s["sym"],tf,s["dir"],s.get("type",""),d,"cool")
            rec(st,s,rs,mfe,mfp,mae,map_); log(rs,s["sym"],tf,f"MFE={mfe*100:.2f}%");continue
        keep.append(s)
    st["live"]=keep[-MAX_LIVE:]

def check_shadow(st):
    keep=[]
    for s in st.get("shadow") or []:
        tf=s.get("tf")
        if tf not in TF:keep.append(s);continue
        d=candles(s["sym"],TF[tf][0],300)
        if d is None:keep.append(s);continue
        ts0=int(s.get("sent_ts") or d.ts.iloc[-1]); f=d[d.ts>ts0]
        if len(f)==0:keep.append(s);continue
        rs=check_hit(f,s["dir"],s["sl"],s["tp"],s["sym"],tf)
        if rs is None and bars_since(d,ts0)>=MAX_HOLD.get(tf,100):rs="TIMEOUT"
        if rs:
            mfe,mfp,mae,map_=calc_mfe_mae(f,s["dir"],s.get("entry",0),s["sl"],s["tp"])
            st.setdefault("results",[]).append({"sym":s["sym"],"tf":tf,"dir":s["dir"],"type":s.get("type","PREPARE"),"result":rs,"ts":int(time.time()),"rec_ts":int(time.time()*1000),"score":s.get("score",0),"sig_ts":s.get("sent_ts"),"entry":s.get("entry",0),"sl":s["sl"],"tp":s["tp"],"mfe":round(mfe*100,2),"mfe_pct":round(min(mfp,1.0)*100,1),"mae":round(mae*100,2),"mae_pct":round(min(map_,1.0)*100,1),"shadow":True,"reasons":s.get("reasons",[])})
            log("SHADOW",rs,s["sym"],tf,f"MFE={mfe*100:.2f}%");continue
        keep.append(s)
    st["shadow"]=keep[-MAX_SHADOW:]

def scan():
    btc_cache.clear(); st=load_state(); clean(st)
    check_live(st); check_shadow(st); save_state(st); daily_report(st)
    ss=coins()
    if not ss:log("no coins");save_state(st);return
    for tf,(bar,n,_,_) in TF.items():
        if stop or not tf_run(tf):continue
        log(f"[扫描] {tf}"); hits=[]
        for sym in ss:
            if stop:break
            try:
                dd=candles(sym,bar,n); s=signal(dd,tf,sym,st)
                if not s or too_soon(st,s["sym"],tf,s["dir"],s["type"],dd,DEDUP_BARS):continue
                if any(lv.get("sym")==s["sym"] and lv.get("tf")==tf for lv in st.get("live",[])):continue
                if sdk(s["sym"],tf,s["dir"],s["type"]) in st.get("cool",{}) and bars_since(dd,int(st["cool"][sdk(s["sym"],tf,s["dir"],s["type"])]))<COOL_BARS:continue
                s["sent_ts"]=int(dd.ts.iloc[-1]); hits.append(s)
            except Exception as e:log(f"[SCAN] {sym} {tf} {type(e).__name__} {e}")
        
        hits.sort(key=lambda x:-x["score"]); cnt=0
        for s in hits[:MAX_SEND_PER_SCAN]:
            # 单方向持仓限制：同方向最多发3单
            long_cnt = sum(1 for x in st.get("live",[]) if x.get("dir")=="LONG")
            short_cnt = sum(1 for x in st.get("live",[]) if x.get("dir")=="SHORT")
            if s["dir"]=="LONG" and long_cnt >= 3:
                log("LIMIT_LONG skip", s["sym"])
                continue
            if s["dir"]=="SHORT" and short_cnt >= 3:
                log("LIMIT_SHORT skip", s["sym"])
                continue
                
            if send_signal(st,s):cnt+=1
            time.sleep(.3)
        save_state(st); log(f"[{tf}] hits {len(hits)} sent {cnt}")
    print(now_bj().strftime("%H:%M:%S"),"完成")

if __name__=="__main__":
    scan()
