import os
import time
import requests
import pandas as pd

# 从 GitHub Secrets 读取密钥
FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK")

# 监控的周期（OKX支持这些周期）
INTERVALS = ["15m", "30m", "1H", "4H", "1D"]
# 粘合阈值 (0.003 相当于 99.7% 重合度)
THRESHOLD = 0.003

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}

def get_top_200_symbols():
    """通过 CoinGecko 免费 API 动态获取市值前 200 的币种"""
    try:
        url = (
            "https://api.coingecko.com/api/v3/coins/markets"
            "?vs_currency=usd&order=market_cap_desc&per_page=200&page=1&sparkline=false"
        )
        resp = requests.get(url, headers=HEADERS, timeout=15)
        data = resp.json()
        if not isinstance(data, list):
            return []
        symbols = [item["symbol"].upper() for item in data if "symbol" in item]
        symbols = list(dict.fromkeys(symbols))
        # 确保黄金 PAXG 和 XAU 必定在监控列表里
        if "PAXG" not in symbols: symbols.append("PAXG")
        if "XAU" not in symbols: symbols.append("XAU")
        return symbols
    except Exception as e:
        print(f"动态获取币种列表失败: {e}")
        return []

def send_feishu(msg):
    """发送飞书消息"""
    if not FEISHU_WEBHOOK:
        print("错误: 没有配置飞书 Webhook")
        return
    payload = {"msg_type": "text", "content": {"text": msg}}
    try:
        resp = requests.post(FEISHU_WEBHOOK, json=payload, timeout=10)
        if resp.status_code != 200:
            print(f"飞书接口返回错误: {resp.text}")
        else:
            print("飞书消息发送成功")
    except Exception as e:
        print(f"飞书发送失败: {e}")

def check_symbol(symbol, interval, alert_list):
    """拿具体币种去查K线，实时计算差值"""
    okx_symbol = f"{symbol}-USDT"
    binance_symbol = f"{symbol}USDT"
    
    kline_data = None
    data_source = "OKX"
    
    # 1. 尝试币安
    binance_url = f"https://api.binance.com/api/v3/klines?symbol={binance_symbol}&interval={interval}&limit=150"
    try:
        resp = requests.get(binance_url, headers=HEADERS, timeout=8).json()
        if isinstance(resp, list):
            kline_data = resp
            data_source = "Binance"
    except Exception:
        pass
        
    # 2. 降级 OKX
    if kline_data is None:
        okx_url = f"https://www.okx.com/api/v5/market/candles?instId={okx_symbol}&bar={interval}&limit=150"
        try:
            resp = requests.get(okx_url, headers=HEADERS, timeout=10).json()
            if resp.get("code") == "0" and resp.get("data"):
                kline_data = resp["data"]
        except Exception:
            return

    if not kline_data:
        return

    try:
        if data_source == "Binance":
            df = pd.DataFrame(kline_data, columns=['time','open','high','low','close','volume','close_time','qav','num','tbbav','tbqav','ignore'])
        else:
            df = pd.DataFrame(kline_data, columns=["ts", "open", "high", "low", "close", "vol", "volCcy", "volCcyQuote", "confirm"])
            df = df.iloc[::-1].reset_index(drop=True)
        for col in ["open", "high", "low", "close"]:
            df[col] = pd.to_numeric(df[col])
        close = df["close"]
    except Exception:
        return

    ema20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
    ema60 = close.ewm(span=60, adjust=False).mean().iloc[-1]
    ema120 = close.ewm(span=120, adjust=False).mean().iloc[-1]
    sma20 = close.rolling(20).mean().iloc[-1]
    sma60 = close.rolling(60).mean().iloc[-1]
    sma120 = close.rolling(120).mean().iloc[-1]

    mas = [ema20, ema60, ema120, sma20, sma60, sma120]
    price = close.iloc[-1]
    
    diff_value = max(mas) - min(mas)
    max_spread = diff_value / price
    
    # 只要触发阈值，警报里必定包含“六线差值（美金）”、“价差百分比”、“当前价”
    if max_spread <= THRESHOLD:
        alert_list.append(f"{symbol} [{interval}] 六线差值:${diff_value:.4f} (价差:{max_spread:.2%}) 当前价:${price:.4f} ({data_source})")

if __name__ == "__main__":
    if not FEISHU_WEBHOOK:
        print("错误: 缺少飞书 Webhook")
    else:
        print("正在通过 CoinGecko 动态获取市值前 200 币种...")
        symbols = get_top_200_symbols()
        if not symbols:
            print("⚠️ 动态获取失败，跳过本次运行。")
        else:
            # 屏蔽稳定币 + NFT/AINFT黑名单
            excluded_symbols = ["USDC", "USD1", "USDG", "PYUSD", "RLUSD", "USDT", "USDS", "USDe", "DAI", "BUSD", "FDUSD", "TUSD", "USDP", "GUSD", "FRAX", "USDD", "USAT", "NFT", "AINFT"]
            symbols = [sym for sym in symbols if sym not in excluded_symbols]
            
            print(f"本次监控币种数量: {len(symbols)}")
            alert_list = []
            for sym in symbols:
                for iv in INTERVALS:
                    check_symbol(sym, iv, alert_list)
                    time.sleep(0.1)
            
            if alert_list:
                header = "🚨 六线粘合警报汇总!\n"
                body = "\n".join(alert_list)
                full_msg = header + body
                if len(full_msg) > 3000:
                    full_msg = full_msg[:3000] + "\n... (消息过长，已截断)"
                send_feishu(full_msg)
                print(f"本次共发送 {len(alert_list)} 个警报")
            else:
                print("本次没有满足粘合条件的币种。")
