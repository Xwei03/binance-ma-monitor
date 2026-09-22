import os
import time
import requests
import pandas as pd

# 从 GitHub Secrets 读取密钥
FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK")

# 监控的周期
INTERVALS = ["15m", "30m", "1H", "4H", "1D"]

# 【基础备用阈值】（动态自适应会在此范围内浮动）
THRESHOLD_CONFIG = {
    "15m": 0.003, # 15分钟用 0.3%
    "30m": 0.003, # 30分钟用 0.3%
    "1H": 0.005,  # 1小时用 0.5%
    "4H": 0.015,  # 4小时用 1.5%
    "1D": 0.032   # 1天用 3.2%
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}

def get_coingecko_symbols():
    """动态获取前1000个币种作为候选池"""
    symbols = []
    for page in range(1, 5):
        try:
            url = f"https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=250&page={page}&sparkline=false"
            resp = requests.get(url, headers=HEADERS, timeout=15).json()
            if isinstance(resp, list):
                symbols.extend([item["symbol"].upper() for item in resp if "symbol" in item])
        except Exception as e:
            print(f"获取第{page}页失败: {e}")
    return list(dict.fromkeys(symbols))

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
    binance_interval = interval.lower()
    binance_url = f"https://fapi.binance.com/fapi/v1/klines?symbol={binance_swap_symbol}&interval={binance_interval}&limit=300"
    try:
        resp = requests.get(binance_url, headers=HEADERS, timeout=8).json()
        if isinstance(resp, list) and len(resp) > 0:
            kline_data = resp
            data_source = "Binance合约"
    except Exception:
        pass
        
    # 2. 降级 OKX 合约
    if kline_data is None:
        okx_url = f"https://www.okx.com/api/v5/market/candles?instId={okx_swap_symbol}&bar={interval}&limit=300"
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
            vol_series = pd.to_numeric(df['qav'])
        else:
            df = pd.DataFrame(kline_data, columns=["ts", "open", "high", "low", "close", "vol", "volCcy", "volCcyQuote", "confirm"])
            df = df.iloc[::-1].reset_index(drop=True)
            vol_series = pd.to_numeric(df['volCcyQuote'])
            
        for col in ["open", "high", "low", "close"]:
            df[col] = pd.to_numeric(df[col])
    except Exception as e:
        print(f"数据解析失败: {e}")
        return

    # 流动性过滤（低于1000万USDT直接跳过）
    try:
        recent_24h_vol = vol_series.tail(96).sum()
        if recent_24h_vol < 10000000:
            return
    except Exception as e:
        pass

    # 剔除最新一根未收盘K线
    close = df["close"].iloc[:-1]
    high_series = df['high'].iloc[:-1]
    low_series = df['low'].iloc[:-1]

    # 计算各条均线
    ema20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
    ema60 = close.ewm(span=60, adjust=False).mean().iloc[-1]
    ema120 = close.ewm(span=120, adjust=False).mean().iloc[-1]
    sma20 = close.rolling(20).mean().iloc[-1]
    sma60 = close.rolling(60).mean().iloc[-1]
    sma120 = close.rolling(120).mean().iloc[-1]
    
    # 计算 EMA200 大趋势线
    ema200 = close.ewm(span=200, adjust=False).mean().iloc[-1]

    mas = [ema20, ema60, ema120, sma20, sma60, sma120]
    price = close.iloc[-1]
    
    diff_value = max(mas) - min(mas)
    max_spread = diff_value / price
    
    # 动态自适应阈值计算
    try:
        prev_close = df['close'].shift(1).iloc[:-1]
        tr = pd.concat([high_series - low_series, (high_series - prev_close).abs(), (low_series - prev_close).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean().iloc[-1]
        
        dynamic_threshold = (atr / price) * 0.4
        base_threshold = THRESHOLD_CONFIG.get(interval, 0.004)
        threshold = max(min(dynamic_threshold, base_threshold * 2), base_threshold * 0.5)
    except Exception as e:
        atr = diff_value
        threshold = THRESHOLD_CONFIG.get(interval, 0.004)

    if max_spread <= threshold:
        # 计算止损止盈建议
        try:
            long_stop_loss = min(mas) - 0.5 * atr
            long_take_profit = price + 2 * (price - long_stop_loss)
            short_stop_loss = max(mas) + 0.5 * atr
            short_take_profit = price - 2 * (short_stop_loss - price)
            
            trade_advice = (
                f"📈 做多建议: 止损 ${long_stop_loss:.4f} / 止盈 ${long_take_profit:.4f}\n"
                f"📉 做空建议: 止损 ${short_stop_loss:.4f} / 止盈 ${short_take_profit:.4f}"
            )
        except Exception as e:
            trade_advice = "⚠️ 止损止盈计算失败"
            
        # 趋势过滤标签
        if price > ema200:
            trend_desc = f"📈 多头趋势 (价格 > EMA200)"
            trade_note = "✅ 顺势，优先考虑做多"
        else:
            trend_desc = f"📉 空头趋势 (价格 < EMA200)"
            trade_note = "⚠️ 逆势，注意风险，优先考虑做空"
            
        alert_list.append(
            f"{symbol} [{interval}] 六线差值:${diff_value:.4f} (价差:{max_spread:.2%}) 当前价:${price:.4f} ({data_source})\n"
            f"🧭 趋势状态: {trend_desc} ({trade_note})\n"
            f"{trade_advice}"
        )

if __name__ == "__main__":
    if not FEISHU_WEBHOOK:
        print("错误: 缺少飞书 Webhook")
    else:
        print("正在动态获取市值前 1000 币种作为候选池...")
        all_symbols = get_coingecko_symbols()
        
        print("正在获取 OKX 永续合约名单...")
        okx_swap_coins = get_okx_swap_symbols()
        
        if not all_symbols or not okx_swap_coins:
            print("⚠️ 获取币种或 OKX 合约名单失败，跳过本次运行。")
        else:
            excluded_symbols = ["USDC", "USD1", "USDG", "PYUSD", "RLUSD", "USDT", "USDS", "USDe", "DAI", "BUSD", "FDUSD", "TUSD", "USDP", "GUSD", "FRAX", "USDD", "USAT", "NFT", "AINFT"]
            
            final_monitored = []
            for sym in all_symbols:
                if sym in excluded_symbols: continue
                if sym not in okx_swap_coins: continue
                final_monitored.append(sym)
                if len(final_monitored) >= 200:
                    break
            
            print(f"✅ 本次最终监控合约币种数量: {len(final_monitored)}")
            
            alert_list = []
            for sym in final_monitored:
                for iv in INTERVALS:
                    check_symbol(sym, iv, alert_list)
                    time.sleep(0.1)
            
            if alert_list:
                header = "🚨 六线粘合警报汇总(带趋势过滤)!\n"
                body = "\n\n".join(alert_list)
                full_msg = header + body
                if len(full_msg) > 3000:
                    full_msg = full_msg[:3000] + "\n... (消息过长，已截断)"
                send_feishu(full_msg)
                print(f"本次共发送 {len(alert_list)} 个警报")
            else:
                print("本次没有满足粘合条件的合约币种。")
