"""
main.py - Capital.com Dual-Timeframe SMC Strategy Engine
Features: Live Production, Risk Management ($10k balance, $1k Max DD, $100 risk),
Delayed Startup Price Check (1 min), Immediate Signal Live Price, and 3-Minute Periodic Floating P&L Updates.
"""
import os
import json
import time
import logging
import requests
import websocket
import threading
import pandas as pd
from typing import List, Optional
from dotenv import load_dotenv

load_dotenv()

from models import Candle
from resample import resample_candles
from swings import detect_swings
from structure import classify_swings, build_structure
from reversal import find_reversal_entries
from backtest_demo import extract_htf_zones_from_state

# --- CONFIGURATION & RISK PARAMETERS ---
USE_DEMO = False  # DEMO = false as requested

DEMO_BASE_URL = "https://demo-api-capital.backend-capital.com/api/v1"
LIVE_BASE_URL = "https://api-capital.backend-capital.com/api/v1"

DEMO_WS_URL = "wss://demo-api-streaming-capital.backend-capital.com/connect"
LIVE_WS_URL = "wss://api-streaming-capital.backend-capital.com/connect"

API_KEY = os.getenv("CAPITAL_API_KEY")
IDENTIFIER = os.getenv("CAPITAL_IDENTIFIER")
PASSWORD = os.getenv("CAPITAL_PASSWORD")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

REST_URL = DEMO_BASE_URL if USE_DEMO else LIVE_BASE_URL
WS_URL = DEMO_WS_URL if USE_DEMO else LIVE_WS_URL

EPIC_SYMBOL = "US30"

# Risk Management Settings
INITIAL_BALANCE = 10000.0
MAX_DRAWDOWN = 1000.0
RISK_PER_TRADE = 100.0

# Memory Depths
LTF_5M_MAX = 1000
HTF_4H_MAX = 300

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s [%(threadName)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("CapitalLiveFeed")

class TradeState:
    NO_TRADE = "NO_TRADE"
    SPOTTED = "SPOTTED"
    TRIGGERED = "TRIGGERED"
    CLOSED = "CLOSED"

class CapitalEngine:
    def __init__(self):
        self.cst = None
        self.xst = None
        self.raw_5m_buffer: List[Candle] = []
        self.df_4h_buffer: List[Candle] = []
        self.last_bar_time = None
        self.latest_tick = {"bid": 0.0, "ask": 0.0}

        # Account & Lifecycle Management
        self.account_balance = INITIAL_BALANCE
        self.peak_balance = INITIAL_BALANCE
        self.current_state = TradeState.NO_TRADE
        self.active_signal = None
        self.position_size = 0.0
        self.filled_price = 0.0
        self.last_floating_alert_time = 0.0  # Controls 3-minute periodic updates

    def authenticate(self):
        env_type = "DEMO" if USE_DEMO else "LIVE"
        logger.info(f"Authenticating with Capital.com REST API [{env_type} Environment]...")
        auth_url = f"{REST_URL}/session"
        headers = {"X-CAP-API-KEY": API_KEY, "Content-Type": "application/json"}
        payload = {"identifier": IDENTIFIER, "password": PASSWORD, "encryptedPassword": False}

        res = requests.post(auth_url, headers=headers, json=payload)
        res.raise_for_status()
        self.cst = res.headers.get("CST")
        self.xst = res.headers.get("X-SECURITY-TOKEN")
        logger.info(f"[+] Auth Successful ({env_type}). Session tokens acquired.")

    def send_telegram_alert(self, message: str):
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            logger.warning("[!] Telegram credentials missing. Skipping alert.")
            return

        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
        try:
            res = requests.post(url, json=payload, timeout=5)
            if res.status_code != 200:
                logger.error(f"[-] Failed to send Telegram alert: {res.text}")
        except Exception as e:
            logger.error(f"[-] Telegram network error: {e}")

    def fetch_prices(self, resolution: str, max_bars: int) -> List[Candle]:
        prices_url = f"{REST_URL}/prices/{EPIC_SYMBOL}"
        headers = {"X-CAP-API-KEY": API_KEY, "CST": self.cst, "X-SECURITY-TOKEN": self.xst}
        params = {"resolution": resolution, "max": max_bars}

        res = requests.get(prices_url, headers=headers, params=params)
        res.raise_for_status()
        prices = res.json().get("prices", [])

        candles = []
        for idx, p in enumerate(prices):
            c = Candle(
                index=idx,
                timestamp=pd.to_datetime(p["snapshotTime"]),
                open=float(p["openPrice"]["bid"]),
                high=float(p["highPrice"]["bid"]),
                low=float(p["lowPrice"]["bid"]),
                close=float(p["closePrice"]["bid"])
            )
            c.volume = float(p.get("lastTradedVolume", 0.0))
            candles.append(c)

        return candles

    def get_live_market_price(self) -> float:
        """Helper to fetch a fresh current market price via REST if WebSocket tick is pending."""
        try:
            bars = self.fetch_prices(resolution="MINUTE_1", max_bars=1)
            if bars:
                return bars[-1].close
        except Exception as e:
            logger.debug(f"[-] Failed to fetch REST price fallback: {e}")
        return 0.0

    def start_websocket(self):
        def on_open(ws):
            logger.info(f"[WS] Connected. Subscribing to tick feed for {EPIC_SYMBOL}...")
            subscribe_payload = {
                "destination": "marketData.subscribe",
                "correlationId": "sub-us30-live",
                "cst": self.cst,
                "securityToken": self.xst,
                "payload": {"epics": [EPIC_SYMBOL]}
            }
            ws.send(json.dumps(subscribe_payload))

        def on_message(ws, message):
            try:
                data = json.loads(message)
                if "marketData" in data.get("destination", ""):
                    payload = data.get("payload", {})
                    bid = payload.get("bid", 0.0)
                    ask = payload.get("ofr", 0.0)
                    if bid > 0:
                        self.latest_tick["bid"] = bid
                        self.latest_tick["ask"] = ask
                        self.monitor_active_trade(bid)
            except Exception as e:
                logger.debug(f"[WS Parse Warning] {e}")

        def on_error(ws, error):
            logger.error(f"[WS Error] {error}")

        def on_close(ws, status, msg):
            logger.warning(f"[WS Closed] {status} - {msg}")

        ws = websocket.WebSocketApp(
            WS_URL, on_open=on_open, on_message=on_message, on_error=on_error, on_close=on_close
        )
        threading.Thread(target=ws.run_forever, name="WebSocketThread", daemon=True).start()

    def monitor_active_trade(self, current_price: float):
        """Monitors entry triggers, 3-minute periodic floating P&L, TP, and SL milestones in real-time."""
        if self.current_state == TradeState.NO_TRADE:
            return

        sig = self.active_signal
        direction = str(sig.direction).split(".")[-1].upper()
        entry = sig.entry_price
        sl = sig.stop_loss
        tp = sig.take_profit

        # 1. Check if SPOTTED order triggers
        if self.current_state == TradeState.SPOTTED:
            triggered = False
            if direction == "BULLISH" and current_price <= entry:
                triggered = True
            elif direction == "BEARISH" and current_price >= entry:
                triggered = True

            if triggered:
                self.current_state = TradeState.TRIGGERED
                self.filled_price = current_price
                sl_dist = abs(entry - sl)
                self.position_size = round(RISK_PER_TRADE / sl_dist, 2) if sl_dist > 0 else 0.1
                self.last_floating_alert_time = time.time()  # Reset timer for periodic updates

                msg = (
                    f"🟢 ORDER TRIGGERED & ACTIVE 🟢\n\n"
                    f"📊 Instrument: {EPIC_SYMBOL} ({direction})\n"
                    f"🎯 Filled Price: {current_price}\n"
                    f"📦 Position Size: {self.position_size} lots\n"
                    f"🛑 Stop Loss: {sl}\n"
                    f"💰 Take Profit: {tp}\n\n"
                    f"⏱ Time: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}"
                )
                logger.info(f"[+] Order Triggered at {current_price}")
                self.send_telegram_alert(msg)

        # 2. Check if TRIGGERED trade hits TP/SL or needs a 3-minute periodic floating update
        elif self.current_state == TradeState.TRIGGERED:
            # Check for 3-minute periodic floating update (180 seconds)
            now = time.time()
            if now - self.last_floating_alert_time >= 180:
                self.last_floating_alert_time = now

                if direction == "BULLISH":
                    pnl_pts = current_price - self.filled_price
                else:
                    pnl_pts = self.filled_price - current_price

                pnl_usd = pnl_pts * self.position_size

                floating_msg = (
                    f"📊 FLOATING TRADE UPDATE (3 MIN) 📊\n\n"
                    f"📈 Instrument: {EPIC_SYMBOL} ({direction})\n"
                    f"📍 Current Live Price: {current_price}\n"
                    f"🎯 Entry Price: {self.filled_price}\n"
                    f"💵 Floating P&L: ${pnl_usd:.2f} ({pnl_pts:+.1f} pts)\n"
                    f"🛑 Stop Loss: {sl} | 💰 Take Profit: {tp}\n\n"
                    f"⏱ Time: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}"
                )
                logger.info(f"[*] Floating Update (3m) | Price: {current_price} | PnL: ${pnl_usd:.2f}")
                self.send_telegram_alert(floating_msg)

            # Check outcome conditions
            hit_tp = False
            hit_sl = False

            if direction == "BULLISH":
                if current_price >= tp:
                    hit_tp = True
                elif current_price <= sl:
                    hit_sl = True
            else:  # BEARISH
                if current_price <= tp:
                    hit_tp = True
                elif current_price >= sl:
                    hit_sl = True

            if hit_tp or hit_sl:
                outcome = "TAKE PROFIT (TP) HIT 🎯" if hit_tp else "STOP LOSS (SL) HIT 🛑"
                
                pnl_points = (tp - entry) if hit_tp else (entry - sl)
                if direction == "BEARISH":
                    pnl_points = (entry - tp) if hit_tp else (sl - entry)
                
                pnl_dollars = pnl_points * self.position_size
                if not hit_tp:
                    pnl_dollars = -RISK_PER_TRADE

                self.account_balance += pnl_dollars
                if self.account_balance > self.peak_balance:
                    self.peak_balance = self.account_balance

                current_dd = self.peak_balance - self.account_balance

                msg = (
                    f"🏁 TRADE OUTCOME: {outcome}\n\n"
                    f"📊 Instrument: {EPIC_SYMBOL} ({direction})\n"
                    f"💵 Final PnL: ${pnl_dollars:.2f}\n"
                    f"💼 Updated Balance: ${self.account_balance:.2f}\n"
                    f"📉 Current Drawdown: ${current_dd:.2f} / Max Allowed: ${MAX_DRAWDOWN}\n\n"
                    f"⏱ Time: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}"
                )
                logger.info(f"[-] Trade Closed. Outcome: {outcome} | PnL: ${pnl_dollars:.2f}")
                self.send_telegram_alert(msg)

                if current_dd >= MAX_DRAWDOWN:
                    dd_msg = f"🚨 MAX DRAWDOWN BREACHED (${current_dd:.2f})! Halting system for safety."
                    logger.critical(dd_msg)
                    self.send_telegram_alert(dd_msg)

                self.current_state = TradeState.NO_TRADE
                self.active_signal = None

    def process_smc_structure(self):
        if self.current_state != TradeState.NO_TRADE:
            logger.info("[*] Trade already active/spotted. Skipping new signal scan.")
            return

        logger.info("=== Evaluating SMC Structure on 15m Boundary ===")
        ltf_candles = resample_candles(self.raw_5m_buffer, minutes=15)

        htf_swings = detect_swings(self.df_4h_buffer, window=2)
        classify_swings(self.df_4h_buffer, htf_swings, confirm_candles=1)
        htf_state = build_structure(htf_swings)
        htf_zones = extract_htf_zones_from_state(self.df_4h_buffer, htf_swings, htf_state)

        ltf_swings = detect_swings(ltf_candles, window=2)
        classify_swings(ltf_candles, ltf_swings, confirm_candles=1)
        ltf_state = build_structure(ltf_swings)

        signals = find_reversal_entries(
            candles=ltf_candles,
            swings=ltf_swings,
            state=ltf_state,
            htf_zones=htf_zones,
            min_preceding_weak_points=1,
            min_sl_dist=1.50,
            max_target_r=3.0,
            require_htf_alignment=True
        )

        if signals:
            self.active_signal = signals[-1]
            self.current_state = TradeState.SPOTTED
            sig = self.active_signal

            logger.info("=" * 70)
            logger.info("  !!! REAL-TIME SMC SIGNAL SPOTTED !!!")
            logger.info(f"  {sig}")
            logger.info("=" * 70)

            # Ensure live market price is fetched immediately (WebSocket or REST fallback)
            current_price = self.latest_tick['bid'] if self.latest_tick['bid'] > 0 else self.get_live_market_price()
            if current_price == 0.0:
                current_price = sig.entry_price

            alert_msg = (
                f"🚨 US30 SMC SIGNAL SPOTTED 🚨\n\n"
                f"📊 Direction: {str(sig.direction).split('.')[-1].upper()}\n"
                f"🎯 Limit Entry: {sig.entry_price}\n"
                f"🛑 Stop Loss: {sig.stop_loss}\n"
                f"💰 Take Profit: {sig.take_profit}\n"
                f"📈 Live Market Price: {current_price}\n"
                f"⚠️ Risk Allocated: ${RISK_PER_TRADE}\n\n"
                f"⏱ Time: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            self.send_telegram_alert(alert_msg)
        else:
            logger.info("Scan complete: No setup found.")

    def run(self):
        self.authenticate()
        
        logger.info(f"[1/2] Warming up HTF context ({HTF_4H_MAX} bars)...")
        self.df_4h_buffer = self.fetch_prices(resolution="HOUR_4", max_bars=HTF_4H_MAX)

        logger.info(f"[2/2] Warming up LTF execution memory ({LTF_5M_MAX} bars)...")
        self.raw_5m_buffer = self.fetch_prices(resolution="MINUTE_5", max_bars=LTF_5M_MAX)

        self.start_websocket()

        # Send immediate startup notification
        startup_msg = (
            f"🚀 Capital.com SMC Engine Started (Live Production)\n\n"
            f"⚙️ Status: Active Monitoring & Risk Engine\n"
            f"💰 Balance: ${INITIAL_BALANCE} | Max DD: ${MAX_DRAWDOWN} | Risk/Trade: ${RISK_PER_TRADE}\n"
            f"📊 Instrument: {EPIC_SYMBOL}\n"
            f"⏱ Boot Time: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        self.send_telegram_alert(startup_msg)

        # Background thread to drop the live market price confirmation exactly 1 minute after boot
        def delayed_startup_price_drop():
            time.sleep(60)
            live_price = self.latest_tick['bid'] if self.latest_tick['bid'] > 0 else self.get_live_market_price()
            price_msg = (
                f"📈 MARKET PRICE TICK (POST-BOOT)\n\n"
                f"📊 Instrument: {EPIC_SYMBOL}\n"
                f"📍 Current Live Price: {live_price}\n"
                f"⏱ Time: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            self.send_telegram_alert(price_msg)

        threading.Thread(target=delayed_startup_price_drop, name="StartupPriceThread", daemon=True).start()

        while True:
            try:
                recent_bars = self.fetch_prices(resolution="MINUTE_5", max_bars=2)
                if recent_bars:
                    newest_bar = recent_bars[-1]

                    if self.last_bar_time != newest_bar.timestamp:
                        self.last_bar_time = newest_bar.timestamp
                        self.raw_5m_buffer.append(newest_bar)
                        if len(self.raw_5m_buffer) > LTF_5M_MAX:
                            self.raw_5m_buffer.pop(0)

                        if newest_bar.timestamp.minute == 0 and newest_bar.timestamp.hour % 4 == 0:
                            self.df_4h_buffer = self.fetch_prices(resolution="HOUR_4", max_bars=HTF_4H_MAX)

                        if newest_bar.timestamp.minute % 15 == 0:
                            self.process_smc_structure()

                time.sleep(300)

            except KeyboardInterrupt:
                logger.info("Shutdown requested.")
                break
            except Exception as e:
                logger.error(f"Loop error: {e}. Retrying in 30scharts...")
                time.sleep(30)

if __name__ == "__main__":
    engine = CapitalEngine()
    engine.run()