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

def get_coingecko_symbols():
    """动态获取前500个币种作为候选池"""
    symbols = []
    try:
        url1 = "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=250&page=1&sparkline=false"
        resp1 = requests.get(url1, headers=HEADERS, timeout=15).json()
        if isinstance(resp1, list):
            symbols.extend([item["symbol"].upper() for item in resp1 if "symbol" in item])

        url2 = "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=250&page=2&sparkline=false"
        resp2 = requests.get(url2, headers=HEADERS, timeout=15).json()
        if isinstance(resp2, list):
            symbols.extend([item["symbol"].upper() for item in resp2 if "symbol" in item])
        return list(dict.fromkeys(symbols))
    except Exception as e:
        print(f"动态获取币种列表失败: {e}")
        return []

def get_okx_swap_symbols():
    """获取 OKX 所有 USDT 永续合约名单"""
    try:
        url = "https://www.okx.com/api/v5/public/instruments?instType=SWAP"
        resp = requests.get(url, headers=HEADERS, timeout=15).json()
        swap_list = []
        if resp.get("code") == "0":
            for item in resp.get("data", []):
                instId = item.get("instId", "")
                if instId.endswith("-USDT-SWAP"):
                    coin = instId.split("-")[0]
                    swap_list.append(coin)
        return list(set(swap_list))
    except Exception as e:
        print(f"获取 OKX 合约名单失败: {e}")
        return []

def get_binance_swap_symbols():
    """尝试获取币安所有 U 本位永续合约名单"""
    try:
        url = "https://fapi.binance.com/fapi/v1/exchangeInfo"
        resp = requests.get(url, headers=HEADERS, timeout=10).json()
        swap_list = []
        if "symbols" in resp:
            for item in resp["symbols"]:
                symbol = item.get("symbol", "")
                if symbol.endswith("USDT") and item.get("contractType") == "PERPETUAL":
                    coin = symbol.replace("USDT", "")
                    swap_list.append(coin)
        return list(set(swap_list))
    except Exception as e:
        print("⚠️ 获取币安合约名单失败(可能是IP被屏蔽)")
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
    """只查合约K线：优先币安合约，降级OKX合约"""
    okx_swap_symbol = f"{symbol}-USDT-SWAP"
    binance_swap_symbol = f"{symbol}USDT"
    
    kline_data = None
    data_source = "OKX合约"
    
    # 1. 尝试币安合约
    binance_url = f"https://fapi.binance.com/fapi/v1/klines?symbol={binance_swap_symbol}&interval={interval}&limit=150"
    try:
        resp = requests.get(binance_url, headers=HEADERS, timeout=8).json()
        if isinstance(resp, list) and len(resp) > 0:
            kline_data = resp
            data_source = "Binance合约"
    except Exception:
        pass
        
    # 2. 降级 OKX 合约
    if kline_data is None:
        okx_url = f"https://www.okx.com/api/v5/market/candles?instId={okx_swap_symbol}&bar={interval}&limit=150"
        try:
            resp = requests.get(okx_url, headers=HEADERS, timeout=10).json()
            if resp.get("code") == "0" and resp.get("data"):
                kline_data = resp["data"]
        except Exception:
            return

    if not kline_data:
        return

    try:
        if data_source == "Binance合约":
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
    
    if max_spread <= THRESHOLD:
        alert_list.append(f"{symbol} [{interval}] 六线差值:${diff_value:.4f} (价差:{max_spread:.2%}) 当前价:${price:.4f} ({data_source})")

if __name__ == "__main__":
    if not FEISHU_WEBHOOK:
        print("错误: 缺少飞书 Webhook")
    else:
        print("正在动态获取市值前 500 币种作为候选池...")
        all_symbols = get_coingecko_symbols()
        
        print("正在获取 OKX 永续合约名单...")
        okx_swap_coins = get_okx_swap_symbols()
        
        print("正在尝试获取币安永续合约名单...")
        binance_swap_coins = get_binance_swap_symbols()
        
        if not all_symbols or not okx_swap_coins:
            print("⚠️ 获取币种或 OKX 合约名单失败，跳过本次运行。")
        else:
            # 稳定币 + NFT/AINFT 黑名单
            excluded_symbols = ["USDC", "USD1", "USDG", "PYUSD", "RLUSD", "USDT", "USDS", "USDe", "DAI", "BUSD", "FDUSD", "TUSD", "USDP", "GUSD", "FRAX", "USDD", "USAT", "NFT", "AINFT"]
            
            final_monitored = []
            for sym in all_symbols:
                if sym in excluded_symbols:
                    continue
                
                # 必须要有 OKX 合约
                if sym not in okx_swap_coins:
                    continue
                
                # 【核心】如果拿到了币安名单，那么必须币安也有这个合约
                if binance_swap_coins and sym not in binance_swap_coins:
                    continue
                
                final_monitored.append(sym)
                if len(final_monitored) >= 200:
                    break
            
            print(f"候选池币种总数量: {len(all_symbols)}")
            print(f"OKX 合约总数量: {len(okx_swap_coins)}")
            print(f"币安 合约总数量: {len(binance_swap_coins)}")
            print(f"✅ 本次最终监控合约币种数量: {len(final_monitored)}")
            
            alert_list = []
            for sym in final_monitored:
                for iv in INTERVALS:
                    check_symbol(sym, iv, alert_list)
                    time.sleep(0.1)
            
            if alert_list:
                header = "🚨 六线粘合警报汇总(仅合约)!\n"
                body = "\n".join(alert_list)
                full_msg = header + body
                if len(full_msg) > 3000:
                    full_msg = full_msg[:3000] + "\n... (消息过长，已截断)"
                send_feishu(full_msg)
                print(f"本次共发送 {len(alert_list)} 个警报")
            else:
                print("本次没有满足粘合条件的合约币种。")
