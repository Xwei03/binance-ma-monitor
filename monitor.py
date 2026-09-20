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
# 监控成交额排名前多少的币种（这里已改为100）
TOP_LIMIT = 100

def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": msg}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"发送失败: {e}")

# 自动获取币安成交额排名前 N 的 USDT 币种
def get_top_symbols(limit):
    url = "https://api.binance.com/api/v3/ticker/24hr"
    try:
        resp = requests.get(url, timeout=10).json()
        if not isinstance(resp, list): return []
        # 过滤 USDT 交易对，并排除杠杆代币
        valid_pairs = [
            item for item in resp 
            if item['symbol'].endswith('USDT') 
            and 'UP' not in item['symbol'] 
            and 'DOWN' not in item['symbol']
            and 'BULL' not in item['symbol']
            and 'BEAR' not in item['symbol']
        ]
        # 按成交额从大到小排序
        valid_pairs.sort(key=lambda x: float(x['quoteVolume']), reverse=True)
        return [item['symbol'] for item in valid_pairs[:limit]]
    except Exception as e:
        print(f"获取币种列表失败: {e}")
        return []

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
        symbols = get_top_symbols(TOP_LIMIT)
        print(f"本次监控币种数量: {len(symbols)}")
        
        for sym in symbols:
            for iv in INTERVALS:
                check_symbol(sym, iv)
                time.sleep(0.2) # 防封号延时
