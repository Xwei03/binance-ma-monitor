import os
import time
import requests
import pandas as pd

# 从 GitHub Secrets 读取密钥
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# 监控的周期
INTERVALS = ["15m", "30m", "1h", "4h", "1d"]
# 粘合阈值 (1% 即 0.01)
THRESHOLD = 0.01

# 币安活跃前100币种 + 黄金PAXG (直接写死，100%稳定不报错)
SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT",
    "MATICUSDT", "LTCUSDT", "SHIBUSDT", "TRXUSDT", "UNIUSDT",
    "ATOMUSDT", "ETCUSDT", "XLMUSDT", "BCHUSDT", "FILUSDT",
    "APTUSDT", "ARBUSDT", "OPUSDT", "INJUSDT", "SUIUSDT",
    "NEARUSDT", "AAVEUSDT", "MKRUSDT", "GRTUSDT", "SANDUSDT",
    "MANAUSDT", "EOSUSDT", "FTMUSDT", "ALGOUSDT", "EGLDUSDT",
    "THETAUSDT", "AXSUSDT", "GALAUSDT", "APEUSDT", "CHZUSDT",
    "CRVUSDT", "SNXUSDT", "LDOUSDT", "RNDRUSDT", "IMXUSDT",
    "SEIUSDT", "TIAUSDT", "STXUSDT", "WLDUSDT", "ORDIUSDT",
    "PEPEUSDT", "FLOKIUSDT", "BONKUSDT", "WIFUSDT", "JUPUSDT",
    "PYTHUSDT", "MEMEUSDT", "RONINUSDT", "CFXUSDT", "KAVAUSDT",
    "ZILUSDT", "1INCHUSDT", "ENJUSDT", "BATUSDT", "ZECUSDT",
    "DASHUSDT", "COMPUSDT", "YFIUSDT", "DYDXUSDT", "GMXUSDT",
    "FETUSDT", "AGIXUSDT", "OCEANUSDT", "ARUSDT", "LPTUSDT",
    "API3USDT", "BANDUSDT", "UMAUSDT", "KSMUSDT", "ICPUSDT",
    "HBARUSDT", "VETUSDT", "ONEUSDT", "ZENUSDT", "KDAUSDT",
    "RVNUSDT", "WAVESUSDT", "CELOUSDT", "IOTXUSDT", "ANKRUSDT",
    "COTIUSDT", "OGNUSDT", "ROSEUSDT", "SKLUSDT", "STORJUSDT",
    "AUDIOUSDT", "C98USDT", "MASKUSDT", "ENSUSDT", "PEOPLEUSDT",
    "PAXGUSDT"
]

def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": msg}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"发送失败: {e}")

def check_symbol(symbol, interval):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit=150"
    try:
        resp = requests.get(url, timeout=10).json()
        if not isinstance(resp, list): return
        df = pd.DataFrame(resp, columns=[
            'time','open','high','low','close','volume','close_time',
            'qav','num','tbbav','tbqav','ignore'
        ])
        df['close'] = pd.to_numeric(df['close'])
        close = df['close']
    except Exception as e:
        print(f"获取 {symbol} {interval} 失败: {e}")
        return

    # 计算 3 条 EMA 和 3 条 SMA
    ema20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
    ema60 = close.ewm(span=60, adjust=False).mean().iloc[-1]
    ema120 = close.ewm(span=120, adjust=False).mean().iloc[-1]
    sma20 = close.rolling(20).mean().iloc[-1]
    sma60 = close.rolling(60).mean().iloc[-1]
    sma120 = close.rolling(120).mean().iloc[-1]

    mas = [ema20, ema60, ema120, sma20, sma60, sma120]
    price = close.iloc[-1]
    
    # 计算最大价差百分比
    max_spread = (max(mas) - min(mas)) / price

    if max_spread <= THRESHOLD:
        msg = f"🚨 六线粘合警报!\n币种: {symbol}\n周期: {interval}\n价差: {max_spread:.4%}\n当前价: {price}"
        send_telegram(msg)
        print(f"发送警报: {symbol} {interval}")

if __name__ == "__main__":
    if not TOKEN or not CHAT_ID:
        print("错误: 缺少 Bot Token 或 Chat ID")
    else:
        print(f"本次监控币种数量: {len(SYMBOLS)}")
        for sym in SYMBOLS:
            for iv in INTERVALS:
                check_symbol(sym, iv)
                time.sleep(0.1) # 防封号延时
