import mplfinance as mpf
import os
import asyncio
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import datetime
import pandas as pd
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from twelvedata import TDClient

TOKEN = "8908283357:AAE8d5eD7tGcgoHw32qntEV_2wsm5-YlZTs"
TWELVE_KEY = os.getenv("TWELVE_DATA_API_KEY")
td = TDClient(apikey=TWELVE_KEY)

WATCHLIST = ["GBP/USD", "USD/JPY", "XAU/USD", "BTC/USD"]
CHAT_IDS = set()

def is_market_open(symbol: str = "") -> bool:
    if "BTC" in symbol or "ETH" in symbol:
        return True
    today = datetime.datetime.now(datetime.timezone.utc).weekday()
    if today in [5, 6]:
        return False
    return True

def fetch_data(symbol: str, interval: str, outputsize: int = 50) -> pd.DataFrame:
    try:
        ts = td.time_series(symbol=symbol, interval=interval, outputsize=outputsize)
        df = ts.as_pandas()
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.iloc[::-1].reset_index()
        for col in ['open', 'high', 'low', 'close']:
            df[col] = df[col].astype(float)
        return df
    except Exception as e:
        print(f"Error fetching data for {symbol} ({interval}): {e}")
        return pd.DataFrame()

def detect_technique_3(df_15m: pd.DataFrame, htf_high: float, htf_low: float):
    if len(df_15m) < 3:
        return None

    candle = df_15m.iloc[-1]
    prev_candle = df_15m.iloc[-2]

    open_p, close_p = candle['open'], candle['close']
    high_p, low_p = candle['high'], candle['low']
    total_range = high_p - low_p

    if total_range == 0:
        return None

    if abs(low_p - htf_low) / htf_low < 0.0025:
        lower_wick = min(open_p, close_p) - low_p
        if lower_wick / total_range >= 0.50:
            return "BULLISH", "Rejection Wick at HTF Liquidity Low", low_p, close_p

        if prev_candle['close'] < prev_candle['open'] and close_p > open_p and close_p > prev_candle['high']:
            return "BULLISH", "Bullish Engulfing at Liquidity Level", low_p, close_p

        if abs(low_p - prev_candle['low']) / low_p < 0.0003 and close_p > open_p:
            return "BULLISH", "Tweezer Bottom Formation", low_p, close_p

    elif abs(high_p - htf_high) / htf_high < 0.0025:
        upper_wick = high_p - max(open_p, close_p)
        if upper_wick / total_range >= 0.50:
            return "BEARISH", "Rejection Wick at HTF Liquidity High", high_p, close_p

        if prev_candle['close'] > prev_candle['open'] and close_p < open_p and close_p < prev_candle['low']:
            return "BEARISH", "Bearish Engulfing at Liquidity Level", high_p, close_p

    return None

def analyze_symbol(symbol: str):
    if not is_market_open(symbol):
        return None

    df_1d = fetch_data(symbol, "1day", 10)
    df_4h = fetch_data(symbol, "4h", 20)
    df_15m = fetch_data(symbol, "15min", 30)

    if df_1d.empty or df_4h.empty or df_15m.empty:
        return None

    htf_high = max(df_1d.iloc[-2]['high'], df_4h.iloc[-2]['high'])
    htf_low = min(df_1d.iloc[-2]['low'], df_4h.iloc[-2]['low'])

    signal = detect_technique_3(df_15m, htf_high, htf_low)
    if not signal:
        return None

    direction, pattern, stop_level, entry_price = signal

    if "BTC" in symbol:
        min_sl_distance = 150.0
    elif "JPY" in symbol:
        min_sl_distance = 0.0015
    elif "XAU" in symbol:
        min_sl_distance = 1.50
    else:
        min_sl_distance = 0.0010

    raw_risk = abs(entry_price - stop_level)
    risk = max(raw_risk, min_sl_distance)

    if direction == "BULLISH":
        stop_level = entry_price - risk
        tp_price = entry_price + (risk * 3)
    else:
        stop_level = entry_price + risk
        tp_price = entry_price - (risk * 3)

    return {
        "symbol": symbol,
        "direction": direction,
        "pattern": pattern,
        "entry": entry_price,
        "sl": stop_level,
        "tp": tp_price,
        "htf_high": htf_high,
        "htf_low": htf_low
    }

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    CHAT_IDS.add(update.effective_chat.id)
    await update.message.reply_text(
        "🟢 Rozay SMC Strategy Bot Active!\n\n"
        "Watchlist: GBP/USD, USD/JPY, XAU/USD, BTC/USD\n"
        "Timeframes: 1D (Bias), 4H (POI), 15m (Execution)\n\n"
        "Commands:\n"
        "/scan - Run immediate SMC setup scan\n"
        "/ping - Check bot response\n"
        "/price [SYMBOL] - Real-time market quote",
        parse_mode="Markdown"
    )

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Pong! 🟢")

async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Scanning GBP/USD, USD/JPY, XAU/USD, BTC/USD across 1D/4H/15m...")
    found = False

    for symbol in WATCHLIST:
        if not is_market_open(symbol):
            continue

        alert = analyze_symbol(symbol)
        if alert:
            found = True
            decimals = 2 if ("BTC" in symbol or "XAU" in symbol) else 5
            msg = (
                f"🚨 ROZAY ENTRY SIGNAL DETECTED 🚨\n\n"
                f"Pair: {alert['symbol']}\n"
                f"Direction: {alert['direction']}\n"
                f"Pattern: Technique #3 ({alert['pattern']})\n\n"
                f"🎯 Entry: {alert['entry']:.{decimals}f}\n"
                f"🛑 SL: {alert['sl']:.{decimals}f}\n"
                f"🏆 1:3 TP: {alert['tp']:.{decimals}f}\n\n"
                f"📍 HTF High: {alert['htf_high']:.{decimals}f} | Low: {alert['htf_low']:.{decimals}f}"
            )
            await update.message.reply_text(msg, parse_mode="Markdown")

    if not found:
        await update.message.reply_text("✅ Scan complete. No active signals meeting 1:3 RR at liquidity levels.")

async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /price BTC/USD")
        return
    symbol = context.args[0].upper()
    try:
        data = td.price(symbol=symbol).as_json()
        p = data.get("price")
        if p:
            await update.message.reply_text(f"Price for {symbol}: ${float(p):.2f}", parse_mode="Markdown")
        else:
            await update.message.reply_text("Symbol not found.")
    except Exception:
        await update.message.reply_text("Error fetching price.")

async def background_scanner(app: Application):
    while True:
        await asyncio.sleep(900)
        for symbol in WATCHLIST:
            if not is_market_open(symbol):
                continue

            alert = analyze_symbol(symbol)
            if alert:
                decimals = 2 if ("BTC" in symbol or "XAU" in symbol) else 5
                msg = (
                    f"🚨 AUTOMATED SIGNAL ALERT 🚨\n\n"
                    f"Pair: {alert['symbol']}\n"
                    f"Direction: {alert['direction']}\n"
                    f"Setup: {alert['pattern']}\n\n"
                    f"🎯 Entry: {alert['entry']:.{decimals}f}\n"
                    f"🛑 SL: {alert['sl']:.{decimals}f}\n"
                    f"🏆 1:3 TP: {alert['tp']:.{decimals}f}"
                )
                for chat_id in CHAT_IDS:
                    await app.bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")

async def post_init(app: Application):
    asyncio.create_task(background_scanner(app))

def main():
    app = Application.builder().token(TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ping", ping))
    app.add_handler(CommandHandler("scan", scan))
    app.add_handler(CommandHandler("price", price))
    app.add_handler(CommandHandler("chart", chart_command))
    print("Rozay SMC Strategy Bot is running...")
    app.run_polling()

class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")

def run_server():
    server = HTTPServer(('0.0.0.0', 10000), SimpleHandler)
    server.serve_forever()

async def chart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Fetching market data and drawing chart...")
    try:
        ts = td.time_series(symbol="BTC/USD", interval="1day", outputsize=30)
        df = ts.as_pandas()

        df = df[['open', 'high', 'low', 'close']].astype(float)
        df.columns = ['Open', 'High', 'Low', 'Close']

        chart_path = 'btc_chart.png'

        mc = mpf.make_marketcolors(up='green', down='red', wick='inherit', edge='inherit')
        s = mpf.make_mpf_style(base_mpl_style='dark_background', marketcolors=mc)

        mpf.plot(
            df,
            type='candle',
            style=s,
            title='Zephyr SMC - Live Market Chart (BTC/USD)',
            ylabel='Price (USD)',
            savefig=chart_path
        )

        with open(chart_path, 'rb') as photo:
            await update.message.reply_photo(photo=photo, caption="Here is your live market chart!")
            
    except Exception as e:
        await update.message.reply_text(f"Could not generate chart: {str(e)}")
if __name__ == "__main__":
    threading.Thread(target=run_server, daemon=True).start()
    main()
# cache refresh
