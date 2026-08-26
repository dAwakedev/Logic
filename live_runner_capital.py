"""
live_runner_capital.py - Capital.com 5m SMC Strategy Polling & Execution Engine
Maintains H4/M15 SMC structure using Capital.com REST API & WebSocket ticks.
"""
import json
import time
import requests
import websocket
import threading
import pandas as pd
from typing import List

from models import Candle
from resample import resample_candles
from swings import detect_swings
from structure import classify_swings, build_structure
from reversal import find_reversal_entries
from backtest_demo import extract_htf_zones_from_state

# --- CONFIGURATION ---
DEMO_BASE_URL = "https://demo-api-capital.backend-capital.com/api/v1"
LIVE_BASE_URL = "https://api-capital.backend-capital.com/api/v1"

DEMO_WS_URL = "wss://demo-api-streaming-capital.backend-capital.com/connect"
LIVE_WS_URL = "wss://api-streaming-capital.backend-capital.com/connect"

# Account Credentials
API_KEY = "emuH86rfqQSJc4yW"
IDENTIFIER = "awoleyegoodness5@gmail.com"
PASSWORD = "Smartbott25@"

EPIC_SYMBOL = "US30"       # Target Instrument
HISTORICAL_MAX = 1000      # Cold start warm-up bars
HTF_TF = 240                # 4-Hour Context
LTF_TF = 15                 # 15-Minute Execution

# Use Live URLs
REST_URL = LIVE_BASE_URL
WS_URL = LIVE_WS_URL

class CapitalEngine:
    def __init__(self):
        self.cst = None
        self.xst = None
        self.raw_5m_buffer: List[Candle] = []
        self.last_bar_time = None
        self.latest_tick = {"bid": 0.0, "ask": 0.0}

    def authenticate(self):
        """Authenticates with Capital.com REST API to acquire CST and X-SECURITY-TOKEN."""
        auth_url = f"{REST_URL}/session"
        headers = {"X-CAP-API-KEY": API_KEY, "Content-Type": "application/json"}
        payload = {"identifier": IDENTIFIER, "password": PASSWORD, "encryptedPassword": False}

        res = requests.post(auth_url, headers=headers, json=payload)
        res.raise_for_status()
        self.cst = res.headers.get("CST")
        self.xst = res.headers.get("X-SECURITY-TOKEN")
        print("[+] Auth Successful. Session tokens acquired.")

    def fetch_historical_5m(self, max_bars: int = 1000) -> List[Candle]:
        """Fetches historical 5-minute candles from Capital.com."""
        prices_url = f"{REST_URL}/prices/{EPIC_SYMBOL}"
        headers = {"X-CAP-API-KEY": API_KEY, "CST": self.cst, "X-SECURITY-TOKEN": self.xst}
        params = {"resolution": "MINUTE_5", "max": max_bars}

        res = requests.get(prices_url, headers=headers, params=params)
        res.raise_for_status()
        prices = res.json().get("prices", [])

        candles = []
        for idx, p in enumerate(prices):
            # Parse OHLC from Capital.com nested structure
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
            print("[WS] Connected. Subscribing to real-time tick feed for US30...")
            subscribe_payload = {
                "destination": "marketData.subscribe",
                "correlationId": "sub-us30-live",
                "cst": self.cst,
                "securityToken": self.xst,
                "payload": {"epics": [EPIC_SYMBOL]}
            }
            ws.send(json.dumps(subscribe_payload))

        def on_message(ws, message):
            data = json.loads(message)
            if "marketData" in data.get("destination", ""):
                payload = data.get("payload", {})
                self.latest_tick["bid"] = payload.get("bid", 0.0)
                self.latest_tick["ask"] = payload.get("ofr", 0.0)

        def on_error(ws, error):
            print(f"[WS Error] {error}")

        def on_close(ws, status, msg):
            print(f"[WS Closed] {status} - {msg}")

        ws = websocket.WebSocketApp(
            WS_URL,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close
        )
        ws_thread = threading.Thread(target=ws.run_forever, daemon=True)
        ws_thread.start()

    def process_smc_structure(self):
        """Evaluates SMC H4 Context and M15 Execution Setup."""
        print(f"\n[{pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}] Evaluating SMC Structure on 15m Boundary...")
        
        # Resample 5m raw buffer to 15m and 4H
        htf_candles = resample_candles(self.raw_5m_buffer, minutes=HTF_TF)
        ltf_candles = resample_candles(self.raw_5m_buffer, minutes=LTF_TF)

        # Build H4 Context
        htf_swings = detect_swings(htf_candles, window=2)
        classify_swings(htf_candles, htf_swings, confirm_candles=1)
        htf_state = build_structure(htf_swings)
        htf_zones = extract_htf_zones_from_state(htf_candles, htf_swings, htf_state)

        # Build M15 Structure
        ltf_swings = detect_swings(ltf_candles, window=2)
        classify_swings(ltf_candles, ltf_swings, confirm_candles=1)
        ltf_state = build_structure(ltf_swings)

        # Scan for Reversal Signals
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
            if latest_signal.candle_idx >= len(ltf_candles) - 2:
                print("=" * 70)
                print("   !!! REAL-TIME SMC SIGNAL DETECTED !!!")
                print(f"   Direction    : {latest_signal.direction}")
                print(f"   Entry Price  : {latest_signal.entry_price}")
                print(f"   Stop Loss    : {latest_signal.stop_loss}")
                print(f"   Take Profit  : {latest_signal.take_profit}")
                print(f"   Current Bid  : {self.latest_tick['bid']}")
                print("=" * 70)
        else:
            print("[+] Scan complete. No active setup conditions met.")

    def run(self):
        # 1. Cold Start Auth & Memory Warm-Up
        self.authenticate()
        print(f"[1/2] Warming up structural memory ({HISTORICAL_MAX} 5m candles)...")
        self.raw_5m_buffer = self.fetch_historical_5m(max_bars=HISTORICAL_MAX)
        print(f"[1/2] Memory loaded: {len(self.raw_5m_buffer)} base 5m bars.")

        # 2. Start WebSocket background listener
        self.start_websocket()

        print("[2/2] Live Polling Loop Started (5-Minute REST intervals)...")

        # 3. Execution Loop
        while True:
            try:
                # Fetch latest 2 candles to check if a new 5m bar closed
                recent_bars = self.fetch_historical_5m(max_bars=2)
                newest_bar = recent_bars[-1]

                if self.last_bar_time != newest_bar.timestamp:
                    self.last_bar_time = newest_bar.timestamp
                    self.raw_5m_buffer.append(newest_bar)
                    if len(self.raw_5m_buffer) > HISTORICAL_MAX:
                        self.raw_5m_buffer.pop(0)

                    print(f"[{newest_bar.timestamp}] New 5m Candle | Close: {newest_bar.close} | Live Bid: {self.latest_tick['bid']}")

                    # Check structure on 15m candle closes
                    if newest_bar.timestamp.minute % 15 == 0:
                        self.process_smc_structure()

                # Sleep 300 seconds (5 minutes)
                time.sleep(300)

            except KeyboardInterrupt:
                print("\n[-] Shutdown requested. Exiting...")
                break
            except Exception as e:
                print(f"[-] Loop error: {e}. Retrying in 30 seconds...")
                time.sleep(30)

if __name__ == "__main__":
    engine = CapitalEngine()
    engine.run()