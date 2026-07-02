import os
import csv
import json
import time
import math
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

try:
    from growwapi import GrowwAPI
except Exception:
    GrowwAPI = None


# ============================================================
# LOAD ENV
# ============================================================

if load_dotenv:
    load_dotenv()


# ============================================================
# CONFIG
# ============================================================

IST = ZoneInfo("Asia/Kolkata")

GROWW_TOKEN = os.getenv("GROWW_API_TOKEN", "")

PAPER_CAPITAL = float(os.getenv("PAPER_CAPITAL", "5000"))

SCAN_INTERVAL_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", "300"))

MIN_AMOUNT_PER_BUY = float(os.getenv("MIN_AMOUNT_PER_BUY", "500"))
MAX_AMOUNT_PER_BUY = float(os.getenv("MAX_AMOUNT_PER_BUY", "2000"))

MAX_SELECTED_BUYS_PER_CYCLE = int(os.getenv("MAX_SELECTED_BUYS_PER_CYCLE", "5"))

NORMAL_BUY_SCORE = int(os.getenv("NORMAL_BUY_SCORE", "75"))
MEDIUM_BUY_SCORE = int(os.getenv("MEDIUM_BUY_SCORE", "80"))
HIGH_RISK_BUY_SCORE = int(os.getenv("HIGH_RISK_BUY_SCORE", "88"))

SELL_SCORE = int(os.getenv("SELL_SCORE", "40"))

STOP_LOSS_PERCENT = float(os.getenv("STOP_LOSS_PERCENT", "0.8"))
TARGET_PERCENT = float(os.getenv("TARGET_PERCENT", "1.2"))

MARKET_START = dtime(9, 15)
FIRST_SCAN_AFTER = dtime(9, 25)
STOP_NEW_BUY_AFTER = dtime(14, 45)
FORCE_EXIT_AFTER = dtime(15, 15)
MARKET_END = dtime(15, 30)

DATA_DIR = "data"
SIGNAL_LOG = os.path.join(DATA_DIR, "signals_log.csv")
TRADE_LOG = os.path.join(DATA_DIR, "paper_trades.csv")
SUMMARY_LOG = os.path.join(DATA_DIR, "daily_summary.csv")
STATE_FILE = os.path.join(DATA_DIR, "paper_state.json")


# ============================================================
# WATCHLIST
# SAFE = lower risk
# MEDIUM = moderate risk
# HIGH = higher risk, allowed only with stronger score
# ============================================================

WATCHLIST = [
    # ETFs / stable instruments
    {"symbol": "NIFTYBEES", "risk": "SAFE"},
    {"symbol": "BANKBEES", "risk": "SAFE"},
    {"symbol": "JUNIORBEES", "risk": "SAFE"},
    {"symbol": "GOLDBEES", "risk": "SAFE"},

    # Large cap / relatively stable
    {"symbol": "RELIANCE", "risk": "SAFE"},
    {"symbol": "TCS", "risk": "SAFE"},
    {"symbol": "INFY", "risk": "SAFE"},
    {"symbol": "HDFCBANK", "risk": "SAFE"},
    {"symbol": "ICICIBANK", "risk": "SAFE"},
    {"symbol": "SBIN", "risk": "SAFE"},
    {"symbol": "LT", "risk": "SAFE"},
    {"symbol": "ITC", "risk": "SAFE"},

    # Medium risk / active stocks
    {"symbol": "TATAMOTORS", "risk": "MEDIUM"},
    {"symbol": "BEL", "risk": "MEDIUM"},
    {"symbol": "ADANIPORTS", "risk": "MEDIUM"},
    {"symbol": "JIOFIN", "risk": "MEDIUM"},
    {"symbol": "ZOMATO", "risk": "MEDIUM"},
    {"symbol": "AXISBANK", "risk": "MEDIUM"},
    {"symbol": "KOTAKBANK", "risk": "MEDIUM"},

    # Higher risk / momentum type
    {"symbol": "IRFC", "risk": "HIGH"},
    {"symbol": "RVNL", "risk": "HIGH"},
    {"symbol": "SUZLON", "risk": "HIGH"},
    {"symbol": "YESBANK", "risk": "HIGH"},
]


# ============================================================
# BASIC FUNCTIONS
# ============================================================

def ensure_data_dir():
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)


def now_ist():
    return datetime.now(IST)


def today_key():
    return now_ist().strftime("%Y-%m-%d")


def market_time_status():
    t = now_ist().time()

    if MARKET_START <= t <= MARKET_END:
        return "OPEN"

    return "CLOSED"


def is_market_open():
    return market_time_status() == "OPEN"


def is_scan_allowed():
    t = now_ist().time()
    return FIRST_SCAN_AFTER <= t <= MARKET_END


def is_new_buy_allowed():
    t = now_ist().time()
    return FIRST_SCAN_AFTER <= t <= STOP_NEW_BUY_AFTER


def is_force_exit_time():
    t = now_ist().time()
    return t >= FORCE_EXIT_AFTER


def safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


# ============================================================
# STATE MANAGEMENT
# ============================================================

def default_state():
    return {
        "date": today_key(),
        "starting_cash": PAPER_CAPITAL,
        "cash": PAPER_CAPITAL,
        "open_positions": {},
        "closed_trades": []
    }


def load_state():
    ensure_data_dir()

    if not os.path.exists(STATE_FILE):
        state = default_state()
        save_state(state)
        return state

    with open(STATE_FILE, "r") as f:
        state = json.load(f)

    # Reset paper account daily
    if state.get("date") != today_key():
        state = default_state()
        save_state(state)

    return state


def save_state(state):
    ensure_data_dir()

    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ============================================================
# LOGGING
# ============================================================

def append_csv(path, header, row):
    ensure_data_dir()
    file_exists = os.path.exists(path)

    with open(path, "a", newline="") as f:
        writer = csv.writer(f)

        if not file_exists:
            writer.writerow(header)

        writer.writerow(row)


def log_signal(result):
    header = [
        "timestamp",
        "symbol",
        "risk",
        "action",
        "score",
        "price",
        "ema9",
        "ema21",
        "rsi",
        "vwap",
        "volume",
        "avg_volume",
        "news_risk_count",
        "news_positive_count",
        "reason"
    ]

    row = [
        now_ist().strftime("%Y-%m-%d %H:%M:%S"),
        result.get("symbol"),
        result.get("risk"),
        result.get("action"),
        result.get("score"),
        result.get("price"),
        result.get("ema9"),
        result.get("ema21"),
        result.get("rsi"),
        result.get("vwap"),
        result.get("volume"),
        result.get("avg_volume"),
        result.get("news_risk_count"),
        result.get("news_positive_count"),
        result.get("reason")
    ]

    append_csv(SIGNAL_LOG, header, row)


def log_trade(trade):
    header = [
        "date",
        "symbol",
        "risk",
        "buy_time",
        "buy_price",
        "sell_time",
        "sell_price",
        "quantity",
        "used_amount",
        "hold_minutes",
        "profit_loss_amount",
        "profit_loss_percent",
        "buy_score",
        "sell_score",
        "buy_reason",
        "sell_reason",
        "result"
    ]

    row = [
        trade.get("date"),
        trade.get("symbol"),
        trade.get("risk"),
        trade.get("buy_time"),
        trade.get("buy_price"),
        trade.get("sell_time"),
        trade.get("sell_price"),
        trade.get("quantity"),
        trade.get("used_amount"),
        trade.get("hold_minutes"),
        trade.get("profit_loss_amount"),
        trade.get("profit_loss_percent"),
        trade.get("buy_score"),
        trade.get("sell_score"),
        trade.get("buy_reason"),
        trade.get("sell_reason"),
        trade.get("result")
    ]

    append_csv(TRADE_LOG, header, row)


def log_daily_summary(state):
    closed = state.get("closed_trades", [])

    total = len(closed)
    winners = len([t for t in closed if t.get("profit_loss_amount", 0) > 0])
    losers = len([t for t in closed if t.get("profit_loss_amount", 0) < 0])

    total_pl = round(sum(t.get("profit_loss_amount", 0) for t in closed), 2)

    win_rate = 0
    if total > 0:
        win_rate = round((winners / total) * 100, 2)

    open_count = len(state.get("open_positions", {}))
    cash = round(state.get("cash", 0), 2)

    header = [
        "timestamp",
        "date",
        "total_closed_trades",
        "winning_trades",
        "losing_trades",
        "win_rate_percent",
        "total_profit_loss",
        "open_positions",
        "cash"
    ]

    row = [
        now_ist().strftime("%Y-%m-%d %H:%M:%S"),
        state.get("date"),
        total,
        winners,
        losers,
        win_rate,
        total_pl,
        open_count,
        cash
    ]

    append_csv(SUMMARY_LOG, header, row)


# ============================================================
# INDICATORS
# ============================================================

def ema(values, period):
    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)
    ema_value = sum(values[:period]) / period

    for price in values[period:]:
        ema_value = (price - ema_value) * multiplier + ema_value

    return ema_value


def rsi(prices, period=14):
    if len(prices) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(-period, 0):
        change = prices[i] - prices[i - 1]

        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def calculate_vwap(candles):
    total_pv = 0
    total_vol = 0

    for c in candles:
        high = c["high"]
        low = c["low"]
        close = c["close"]
        volume = c["volume"]

        typical_price = (high + low + close) / 3
        total_pv += typical_price * volume
        total_vol += volume

    if total_vol <= 0:
        return None

    return total_pv / total_vol


def average_volume(candles, period=10):
    if len(candles) < period:
        return None

    volumes = [c["volume"] for c in candles[-period:]]
    return sum(volumes) / len(volumes)


# ============================================================
# NEWS RISK CHECK
# ============================================================

def fetch_news_titles(query, limit=8):
    try:
        encoded = urllib.parse.quote(query)
        url = f"https://news.google.com/rss/search?q={encoded}&hl=en-IN&gl=IN&ceid=IN:en"

        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0"}
        )

        with urllib.request.urlopen(req, timeout=10) as response:
            data = response.read()

        root = ET.fromstring(data)

        titles = []
        for item in root.findall(".//item"):
            title = item.findtext("title")
            if title:
                titles.append(title)

            if len(titles) >= limit:
                break

        return titles

    except Exception as e:
        return [f"NEWS_ERROR: {e}"]


def analyze_news(symbol):
    risk_words = [
        "war",
        "conflict",
        "sanction",
        "crude",
        "oil price",
        "rbi",
        "rate hike",
        "policy",
        "tariff",
        "rupee",
        "usd inr",
        "fraud",
        "probe",
        "penalty",
        "selloff",
        "inflation"
    ]

    positive_words = [
        "profit rises",
        "beats estimates",
        "strong results",
        "growth",
        "order win",
        "approval",
        "expansion",
        "upgrade",
        "record high"
    ]

    query = f"{symbol} India stock market results RBI policy crude rupee war"
    titles = fetch_news_titles(query)

    risk_count = 0
    positive_count = 0

    for title in titles:
        lower = title.lower()

        if any(word in lower for word in risk_words):
            risk_count += 1

        if any(word in lower for word in positive_words):
            positive_count += 1

    return {
        "risk_count": risk_count,
        "positive_count": positive_count,
        "titles": titles
    }


# ============================================================
# GROWW DATA
# ============================================================

def init_groww():
    if GrowwAPI is None:
        raise Exception("growwapi package is not installed. Run: pip install -r requirements.txt")

    if not GROWW_TOKEN:
        raise Exception("GROWW_API_TOKEN is missing. Add it in .env file on AWS server.")

    return GrowwAPI(GROWW_TOKEN)


def parse_candles(raw_candles):
    candles = []

    for item in raw_candles:
        try:
            if isinstance(item, dict):
                candles.append({
                    "open": safe_float(item.get("open")),
                    "high": safe_float(item.get("high")),
                    "low": safe_float(item.get("low")),
                    "close": safe_float(item.get("close")),
                    "volume": safe_float(item.get("volume", 0))
                })

            elif isinstance(item, list) and len(item) >= 6:
                candles.append({
                    "open": safe_float(item[1]),
                    "high": safe_float(item[2]),
                    "low": safe_float(item[3]),
                    "close": safe_float(item[4]),
                    "volume": safe_float(item[5])
                })

        except Exception:
            continue

    return [c for c in candles if c["close"] > 0]


def get_intraday_candles(groww, symbol):
    """
    If your Groww SDK method/response is different, only update this function.

    Required output format:
    [
      {"open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 12345}
    ]
    """

    try:
        response = groww.get_historical_candle_data(
            segment=groww.SEGMENT_CASH,
            trading_symbol=symbol,
            interval="5m",
            duration="1d"
        )

        raw_candles = response.get("candles", [])
        return parse_candles(raw_candles)

    except Exception as e:
        print(f"[DATA ERROR] {symbol}: {e}")
        return []


# ============================================================
# AI SCORING ENGINE
# ============================================================

def risk_buy_threshold(risk):
    if risk == "HIGH":
        return HIGH_RISK_BUY_SCORE

    if risk == "MEDIUM":
        return MEDIUM_BUY_SCORE

    return NORMAL_BUY_SCORE


def ai_score_symbol(symbol, risk, candles, news):
    if len(candles) < 25:
        return {
            "symbol": symbol,
            "risk": risk,
            "action": "HOLD",
            "score": 0,
            "price": None,
            "reason": "Not enough candle data"
        }

    closes = [c["close"] for c in candles]

    price = closes[-1]
    previous_price = closes[-2]

    ema9 = ema(closes, 9)
    ema21 = ema(closes, 21)
    current_rsi = rsi(closes, 14)
    vwap = calculate_vwap(candles)
    avg_vol = average_volume(candles, 10)
    current_vol = candles[-1]["volume"]

    score = 50
    reasons = []

    if ema9 and ema21:
        if ema9 > ema21:
            score += 20
            reasons.append("EMA9 above EMA21")
        else:
            score -= 20
            reasons.append("EMA9 below EMA21")

    if vwap:
        if price > vwap:
            score += 15
            reasons.append("Price above VWAP")
        else:
            score -= 15
            reasons.append("Price below VWAP")

    if current_rsi is not None:
        if 45 <= current_rsi <= 65:
            score += 10
            reasons.append(f"RSI healthy {current_rsi:.2f}")
        elif current_rsi > 75:
            score -= 15
            reasons.append(f"RSI overbought {current_rsi:.2f}")
        elif current_rsi < 35:
            score -= 10
            reasons.append(f"RSI weak {current_rsi:.2f}")
        else:
            reasons.append(f"RSI neutral {current_rsi:.2f}")

    if avg_vol and current_vol > avg_vol * 1.3:
        score += 10
        reasons.append("Volume spike")
    elif avg_vol:
        reasons.append("Normal volume")

    if price > previous_price:
        score += 5
        reasons.append("Latest candle positive")
    else:
        score -= 5
        reasons.append("Latest candle negative")

    if news.get("risk_count", 0) >= 3:
        score -= 15
        reasons.append("High news risk")
    elif news.get("risk_count", 0) > 0:
        score -= 5
        reasons.append("Some news risk")

    if news.get("positive_count", 0) > 0:
        score += 5
        reasons.append("Positive news found")

    score = max(0, min(100, score))

    threshold = risk_buy_threshold(risk)

    if is_force_exit_time():
        action = "EXIT_CHECK"
        reasons.append("Force exit time reached")

    elif score >= threshold and is_new_buy_allowed():
        action = "BUY_SIGNAL"

    elif score <= SELL_SCORE:
        action = "SELL_SIGNAL"

    else:
        action = "HOLD"

    return {
        "symbol": symbol,
        "risk": risk,
        "action": action,
        "score": score,
        "price": round(price, 2),
        "ema9": round(ema9, 2) if ema9 else None,
        "ema21": round(ema21, 2) if ema21 else None,
        "rsi": round(current_rsi, 2) if current_rsi else None,
        "vwap": round(vwap, 2) if vwap else None,
        "volume": round(current_vol, 2),
        "avg_volume": round(avg_vol, 2) if avg_vol else None,
        "news_risk_count": news.get("risk_count", 0),
        "news_positive_count": news.get("positive_count", 0),
        "reason": " | ".join(reasons)
    }


# ============================================================
# CAPITAL ALLOCATION — SCRIPT DECIDES HOW MANY TO SELECT
# ============================================================

def allocate_buy_candidates(state, scored_results):
    cash = state.get("cash", 0)
    open_positions = state.get("open_positions", {})

    candidates = []

    for item in scored_results:
        symbol = item.get("symbol")

        if symbol in open_positions:
            continue

        if item.get("action") != "BUY_SIGNAL":
            continue

        price = item.get("price")

        if not price or price <= 0:
            continue

        risk = item.get("risk")
        threshold = risk_buy_threshold(risk)

        if item.get("score", 0) < threshold:
            continue

        candidates.append(item)

    candidates = sorted(candidates, key=lambda x: x.get("score", 0), reverse=True)

    selected = []

    for item in candidates:
        if len(selected) >= MAX_SELECTED_BUYS_PER_CYCLE:
            break

        if cash < MIN_AMOUNT_PER_BUY:
            break

        price = item["price"]

        allocation = min(MAX_AMOUNT_PER_BUY, cash)

        quantity = int(allocation // price)

        if quantity <= 0:
            continue

        used_amount = round(quantity * price, 2)

        selected.append({
            "symbol": item["symbol"],
            "risk": item["risk"],
            "score": item["score"],
            "price": price,
            "quantity": quantity,
            "used_amount": used_amount,
            "reason": item["reason"]
        })

        cash -= used_amount

    return selected


# ============================================================
# PAPER BUY / SELL
# ============================================================

def paper_buy(state, buy_item):
    symbol = buy_item["symbol"]

    if symbol in state["open_positions"]:
        return

    if state["cash"] < buy_item["used_amount"]:
        return

    position = {
        "date": today_key(),
        "symbol": symbol,
        "risk": buy_item["risk"],
        "buy_time": now_ist().strftime("%Y-%m-%d %H:%M:%S"),
        "buy_price": buy_item["price"],
        "quantity": buy_item["quantity"],
        "used_amount": buy_item["used_amount"],
        "buy_score": buy_item["score"],
        "buy_reason": buy_item["reason"]
    }

    state["cash"] = round(state["cash"] - buy_item["used_amount"], 2)
    state["open_positions"][symbol] = position

    print(f"[PAPER BUY] {symbol} Qty={buy_item['quantity']} Price={buy_item['price']} Used={buy_item['used_amount']}")


def should_sell_position(position, result):
    if result.get("price") is None:
        return False, "No latest price"

    buy_price = position["buy_price"]
    latest_price = result["price"]

    profit_percent = ((latest_price - buy_price) / buy_price) * 100

    if is_force_exit_time():
        return True, "Market close approaching"

    if profit_percent >= TARGET_PERCENT:
        return True, f"Target reached {profit_percent:.2f}%"

    if profit_percent <= -STOP_LOSS_PERCENT:
        return True, f"Stop loss hit {profit_percent:.2f}%"

    if result.get("action") == "SELL_SIGNAL":
        return True, "AI sell signal"

    return False, "Hold condition"


def paper_sell(state, position, result, sell_reason):
    symbol = position["symbol"]

    sell_price = result["price"]
    quantity = position["quantity"]

    sell_value = round(quantity * sell_price, 2)
    buy_value = position["used_amount"]

    pl_amount = round(sell_value - buy_value, 2)
    pl_percent = round((pl_amount / buy_value) * 100, 2) if buy_value > 0 else 0

    buy_dt = datetime.strptime(position["buy_time"], "%Y-%m-%d %H:%M:%S")
    buy_dt = buy_dt.replace(tzinfo=IST)

    hold_minutes = round((now_ist() - buy_dt).total_seconds() / 60, 2)

    result_text = "PROFIT" if pl_amount > 0 else "LOSS" if pl_amount < 0 else "NO_PROFIT_NO_LOSS"

    trade = {
        "date": today_key(),
        "symbol": symbol,
        "risk": position["risk"],
        "buy_time": position["buy_time"],
        "buy_price": position["buy_price"],
        "sell_time": now_ist().strftime("%Y-%m-%d %H:%M:%S"),
        "sell_price": sell_price,
        "quantity": quantity,
        "used_amount": buy_value,
        "hold_minutes": hold_minutes,
        "profit_loss_amount": pl_amount,
        "profit_loss_percent": pl_percent,
        "buy_score": position.get("buy_score"),
        "sell_score": result.get("score"),
        "buy_reason": position.get("buy_reason"),
        "sell_reason": sell_reason,
        "result": result_text
    }

    state["cash"] = round(state["cash"] + sell_value, 2)
    state["closed_trades"].append(trade)

    if symbol in state["open_positions"]:
        del state["open_positions"][symbol]

    log_trade(trade)

    print(f"[PAPER SELL] {symbol} Qty={quantity} Sell={sell_price} P/L={pl_amount} ({pl_percent}%) Reason={sell_reason}")


# ============================================================
# MAIN LOOP
# ============================================================

def run_bot():
    ensure_data_dir()

    groww = init_groww()
    state = load_state()

    print("========================================")
    print("Groww AI Observation Bot Started")
    print("Mode: PAPER / OBSERVATION ONLY")
    print("No real order will be placed")
    print(f"Paper Capital: ₹{PAPER_CAPITAL}")
    print("========================================")

    while True:
        try:
            if not is_market_open() or not is_scan_allowed():
                print(f"{now_ist().strftime('%Y-%m-%d %H:%M:%S')} Market not active for scan. Waiting...")
                time.sleep(SCAN_INTERVAL_SECONDS)
                continue

            print(f"\n{now_ist().strftime('%Y-%m-%d %H:%M:%S')} Scanning market...")

            scored_results = []

            for item in WATCHLIST:
                symbol = item["symbol"]
                risk = item["risk"]

                candles = get_intraday_candles(groww, symbol)
                news = analyze_news(symbol)
                result = ai_score_symbol(symbol, risk, candles, news)

                scored_results.append(result)
                log_signal(result)

                print(
                    f"{symbol:12} Risk={risk:6} "
                    f"Action={result.get('action'):12} "
                    f"Score={result.get('score'):3} "
                    f"Price={result.get('price')} "
                    f"Reason={result.get('reason')}"
                )

            # Sell check for existing paper positions
            for symbol in list(state["open_positions"].keys()):
                position = state["open_positions"][symbol]

                matching_result = None
                for r in scored_results:
                    if r.get("symbol") == symbol:
                        matching_result = r
                        break

                if not matching_result:
                    continue

                sell_now, sell_reason = should_sell_position(position, matching_result)

                if sell_now:
                    paper_sell(state, position, matching_result, sell_reason)

            # Buy allocation — script decides how many based on capital and score
            selected_buys = allocate_buy_candidates(state, scored_results)

            for buy_item in selected_buys:
                paper_buy(state, buy_item)

            save_state(state)
            log_daily_summary(state)

            print("\nCurrent Paper Status:")
            print(f"Cash: ₹{state.get('cash')}")
            print(f"Open Positions: {len(state.get('open_positions', {}))}")
            print(f"Closed Trades Today: {len(state.get('closed_trades', []))}")

            if state.get("open_positions"):
                print("Open Symbols:", ", ".join(state["open_positions"].keys()))

            time.sleep(SCAN_INTERVAL_SECONDS)

        except KeyboardInterrupt:
            print("Bot stopped manually.")
            save_state(state)
            break

        except Exception as e:
            print(f"[ERROR] {e}")
            save_state(state)
            time.sleep(SCAN_INTERVAL_SECONDS)


if __name__ == "__main__":
    run_bot()
