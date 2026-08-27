"""
main.py - Capital.com Dual-Timeframe SMC Strategy Engine
Maintains deep H4 historical context & live M15 execution structure via .env credentials 
with reliable plain-text Telegram startup and signal notifications.
"""
import os
import json
import time
import logging
import requests
import websocket
import threading
import pandas as pd
from typing import List
from dotenv import load_dotenv

# Load environment variables from the .env file (ignored by git, loaded securely on Railway)
load_dotenv()

from models import Candle
from resample import resample_candles
from swings import detect_swings
from structure import classify_swings, build_structure
from reversal import find_reversal_entries
from backtest_demo import extract_htf_zones_from_state

# --- CONFIGURATION ---
USE_DEMO = False  # Locked to True for your Demo account testing

DEMO_BASE_URL = "https://demo-api-capital.backend-capital.com/api/v1"
LIVE_BASE_URL = "https://api-capital.backend-capital.com/api/v1"

DEMO_WS_URL = "wss://demo-api-streaming-capital.backend-capital.com/connect"
LIVE_WS_URL = "wss://api-streaming-capital.backend-capital.com/connect"

# Securely load credentials from environment variables
API_KEY = os.getenv("CAPITAL_API_KEY")
IDENTIFIER = os.getenv("CAPITAL_IDENTIFIER")
PASSWORD = os.getenv("CAPITAL_PASSWORD")

# Telegram Config
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

REST_URL = DEMO_BASE_URL if USE_DEMO else LIVE_BASE_URL
WS_URL = DEMO_WS_URL if USE_DEMO else LIVE_WS_URL

EPIC_SYMBOL = "US30"

# Memory Depths
LTF_5M_MAX = 1000     # ~3.5 days of 5m base bars for M15 execution resampling
HTF_4H_MAX = 300      # ~50 days of 4H candles for robust structural bias/zones

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s [%(threadName)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("CapitalLiveFeed")

class CapitalEngine:
    def __init__(self):
        self.cst = None
        self.xst = None
        self.raw_5m_buffer: List[Candle] = []
        self.df_4h_buffer: List[Candle] = []
        self.last_bar_time = None
        self.latest_tick = {"bid": 0.0, "ask": 0.0}

    def authenticate(self):
        """Authenticates with Capital.com REST API using environment credentials."""
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
        """Sends signal alerts and system notifications to Telegram using plain text."""
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID or TELEGRAM_BOT_TOKEN == "your_telegram_bot_token_here":
            logger.warning("[!] Telegram credentials not configured in environment. Skipping notification.")
            return

        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message
        }
        try:
            res = requests.post(url, json=payload, timeout=5)
            if res.status_code != 200:
                logger.error(f"[-] Failed to send Telegram alert: {res.text}")
            else:
                logger.info("[+] Telegram notification delivered successfully!")
        except Exception as e:
            logger.error(f"[-] Telegram network error: {e}")

    def fetch_prices(self, resolution: str, max_bars: int) -> List[Candle]:
        """Generic helper to fetch historical candles at any Capital.com resolution."""
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

    def start_websocket(self):
        """Runs real-time WebSocket connection on a background thread."""
        def on_open(ws):
            logger.info(f"[WS] Connected ({'DEMO' if USE_DEMO else 'LIVE'}). Subscribing to tick feed for US30...")
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
                    self.latest_tick["bid"] = payload.get("bid", 0.0)
                    self.latest_tick["ask"] = payload.get("ofr", 0.0)
            except Exception as e:
                logger.debug(f"[WS Parse Warning] {e}")

        def on_error(ws, error):
            logger.error(f"[WS Error] {error}")

        def on_close(ws, status, msg):
            logger.warning(f"[WS Closed] {status} - {msg}")

        ws = websocket.WebSocketApp(
            WS_URL,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close
        )
        ws_thread = threading.Thread(target=ws.run_forever, name="WebSocketThread", daemon=True)
        ws_thread.start()

    def process_smc_structure(self):
        """Evaluates deep H4 Context memory and M15 Execution Setup."""
        logger.info(f"=== Evaluating SMC Structure on 15m Boundary ===")
        
        # 1. Resample LTF execution candles from 5m buffer to 15m
        ltf_candles = resample_candles(self.raw_5m_buffer, minutes=15)

        # 2. Build deep H4 Context directly from pulled H4 historical memory
        htf_swings = detect_swings(self.df_4h_buffer, window=2)
        classify_swings(self.df_4h_buffer, htf_swings, confirm_candles=1)
        htf_state = build_structure(htf_swings)
        htf_zones = extract_htf_zones_from_state(self.df_4h_buffer, htf_swings, htf_state)

        # 3. Build M15 Structure
        ltf_swings = detect_swings(ltf_candles, window=2)
        classify_swings(ltf_candles, ltf_swings, confirm_candles=1)
        ltf_state = build_structure(ltf_swings)

        # 4. Scan for Reversal Signals
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
            latest_signal = signals[-1]
            logger.info("=" * 70)
            logger.info("  !!! REAL-TIME SMC SIGNAL DETECTED !!!")
            logger.info(f"  Signal Details : {latest_signal}")
            logger.info(f"  Current Bid    : {self.latest_tick['bid']}")
            logger.info("=" * 70)

            # Safe price fallback if WebSocket hasn't ticked yet
            current_price = self.latest_tick['bid'] if self.latest_tick['bid'] > 0 else getattr(latest_signal, 'entry_price', 'N/A')

            # Format and fire plain-text Telegram alert
            alert_msg = (
                f"🚨 US30 SMC SIGNAL DETECTED 🚨\n\n"
                f"📊 Direction: {getattr(latest_signal, 'direction', 'N/A')}\n"
                f"🎯 Entry Price: {getattr(latest_signal, 'entry_price', 'N/A')}\n"
                f"🛑 Stop Loss: {getattr(latest_signal, 'stop_loss', 'N/A')}\n"
                f"💰 Take Profit: {getattr(latest_signal, 'take_profit', 'N/A')}\n"
                f"📈 Reference Price: {current_price}\n\n"
                f"⏱ Time: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            self.send_telegram_alert(alert_msg)
        else:
            logger.info("Scan complete: Market condition normal. No setup found.")

    def run(self):
        self.authenticate()
        
        # Warm up deep HTF context (4H bars) first
        logger.info(f"[1/2] Warming up deep HTF structural context ({HTF_4H_MAX} 4H candles ≈ 50 days)...")
        self.df_4h_buffer = self.fetch_prices(resolution="HOUR_4", max_bars=HTF_4H_MAX)
        logger.info(f"[1/2] HTF memory loaded: {len(self.df_4h_buffer)} bars.")
        if self.df_4h_buffer:
            logger.info(f"      ↳ [HTF Range] Start: {self.df_4h_buffer[0].timestamp} | End: {self.df_4h_buffer[-1].timestamp}")

        # Warm up LTF execution buffer (5m bars)
        logger.info(f"[2/2] Warming up LTF execution memory ({LTF_5M_MAX} 5m candles)...")
        self.raw_5m_buffer = self.fetch_prices(resolution="MINUTE_5", max_bars=LTF_5M_MAX)
        logger.info(f"[2/2] LTF memory loaded: {len(self.raw_5m_buffer)} bars.")
        if self.raw_5m_buffer:
            logger.info(f"      ↳ [LTF Range] Start: {self.raw_5m_buffer[0].timestamp} | End: {self.raw_5m_buffer[-1].timestamp}")

        self.start_websocket()

        # Send startup Telegram notification
        startup_msg = (
            f"🚀 Capital.com SMC Engine Started\n\n"
            f"⚙️ Status: Live monitoring active (Demo Mode)\n"
            f"📊 Instrument: {EPIC_SYMBOL}\n"
            f"⏱ Boot Time: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        self.send_telegram_alert(startup_msg)

        logger.info("[*] Live Polling Loop Started (5-Minute REST intervals)...")

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

                        logger.info(f"[{newest_bar.timestamp}] New 5m Candle | Close: {newest_bar.close} | Live Bid: {self.latest_tick['bid']}")

                        # Refresh H4 context memory periodically every 4 hours
                        if newest_bar.timestamp.minute == 0 and newest_bar.timestamp.hour % 4 == 0:
                            logger.info("[*] Refreshing deep H4 context memory...")
                            self.df_4h_buffer = self.fetch_prices(resolution="HOUR_4", max_bars=HTF_4H_MAX)

                        if newest_bar.timestamp.minute % 15 == 0:
                            self.process_smc_structure()

                time.sleep(300)

            except KeyboardInterrupt:
                logger.info("\n[-] Shutdown requested. Exiting...")
                break
            except Exception as e:
                logger.error(f"[-] Loop error: {e}. Retrying in 30 seconds...")
                time.sleep(30)

if __name__ == "__main__":
    engine = CapitalEngine()
    engine.run()