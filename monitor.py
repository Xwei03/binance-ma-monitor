import os
import time
import requests
import pandas as pd

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

INTERVALS = ["15m", "30m", "1h", "4h", "1d"]
THRESHOLD = 0.01

# 伪装浏览器请求头，尽力绕过币安对 GitHub IP 的拦截
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}

def get_top_100_symbols():
    """纯动态获取币安成交额排名前100的 USDT 交易对（不写死任何币种）"""
    try:
        url_ticker = "https://api.binance.com/api/v3/ticker/24hr"
        resp_ticker = requests.get(url_ticker, headers=HEADERS, timeout=15).json()

        if not isinstance(resp_ticker, list):
            print(f"币安返回异常数据: {resp_ticker}")
            return []

        # 过滤：必须是 USDT 交易对，且排除杠杆代币（UP/DOWN/BULL/BEAR）
        valid_tickers = [
            item for item in resp_ticker
            if item['symbol'].endswith('USDT')
            and 'UP' not in item['symbol']
            and 'DOWN' not in item['symbol']
            and 'BULL' not in item['symbol']
            and 'BEAR' not in item['symbol']
        ]

        # 按24小时成交额（quoteVolume）从大到小排序
        valid_tickers.sort(key=lambda x: float(x['quoteVolume']), reverse=True)

        # 取前100名
        top_100 = [item['symbol'] for item in valid_tickers[:100]]
        return top_100

    except Exception as e:
        print(f"动态获取币种列表失败(可能是币安IP拦截): {e}")
        return []

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
        resp = requests.get(url, headers=HEADERS, timeout=10).json()
        if not isinstance(resp, list): return
        df = pd.DataFrame(resp, columns=[
            'time','open','high','low','close','volume','close_time',
            'qav','num','tbbav','tbqav','ignore'
        ])
        df['close'] = pd.to_numeric(df['close'])
        close = df['close']
    except Exception as e:
        return

    ema20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
    ema60 = close.ewm(span=60, adjust=False).mean().iloc[-1]
    ema120 = close.ewm(span=120, adjust=False).mean().iloc[-1]
    sma20 = close.rolling(20).mean().iloc[-1]
    sma60 = close.rolling(60).mean().iloc[-1]
    sma120 = close.rolling(120).mean().iloc[-1]

    mas = [ema20, ema60, ema120, sma20, sma60, sma120]
    price = close.iloc[-1]
    max_spread = (max(mas) - min(mas)) / price

    if max_spread <= THRESHOLD:
        msg = f"🚨 六线粘合警报!\n币种: {symbol}\n周期: {interval}\n价差: {max_spread:.4%}\n当前价: {price}"
        send_telegram(msg)
        print(f"发送警报: {symbol} {interval}")

if __name__ == "__main__":
    if not TOKEN or not CHAT_ID:
        print("错误: 缺少 Bot Token 或 Chat ID")
    else:
        print("正在动态获取币安前100活跃币种...")
        symbols = get_top_100_symbols()
        
        if not symbols:
            print("⚠️ 动态获取失败（被币安拦截），本次运行跳过，不使用备用列表。")
        else:
            print(f"本次监控币种数量: {len(symbols)}")
            for sym in symbols:
                for iv in INTERVALS:
                    check_symbol(sym, iv)
                    time.sleep(0.1)
