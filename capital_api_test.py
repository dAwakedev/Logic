#!/usr/bin/env python3
"""
capital_diagnostic.py — Hardcoded credential test for Capital.com API.
Tests both DEMO and LIVE endpoints, plus email vs account ID identifiers.
"""
import json
import sys

import requests

# === HARDCODED CREDENTIALS ===
API_KEY = "emuH86rfqQSJc4yW"
IDENTIFIER_EMAIL = "awoleyegoodness5@gmail.com"
PASSWORD = "Smartbott25@"
# =============================

ENDPOINTS = {
    "demo": "https://demo-api-capital.backend-capital.com/api/v1",
    "live": "https://api-capital.backend-capital.com/api/v1",
}


def test_session(base_url: str, identifier: str, label: str):
    url = f"{base_url}/session"
    headers = {
        "X-CAP-API-KEY": API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "identifier": identifier,
        "password": PASSWORD,
        "encryptedPassword": False,
    }

    print(f"\n{'='*60}")
    print(f"TEST: {label}")
    print(f"URL:  {url}")
    print(f"ID:   {identifier}")
    print(f"{'='*60}")

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        print(f"Status: {resp.status_code}")

        if resp.status_code == 200:
            cst = resp.headers.get("CST", "MISSING")
            xst = resp.headers.get("X-SECURITY-TOKEN", "MISSING")
            print("✅ SUCCESS")
            print(f"   CST:              {cst[:30]}...")
            print(f"   X-SECURITY-TOKEN: {xst[:30]}...")
            return cst, xst
        else:
            print("❌ FAILED")
            try:
                body = resp.json()
                print(json.dumps(body, indent=2))
            except Exception:
                print(resp.text[:500])
            return None, None
    except Exception as e:
        print(f"❌ EXCEPTION: {e}")
        return None, None


def test_market_search(base_url: str, cst: str, xst: str, term: str):
    url = f"{base_url}/markets"
    headers = {
        "X-CAP-API-KEY": API_KEY,
        "CST": cst,
        "X-SECURITY-TOKEN": xst,
    }
    params = {"searchTerm": term}

    print(f"\n[GET] {url}?searchTerm={term}")
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        print(f"Status: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            markets = data.get("markets", [])
            print(f"Found {len(markets)} market(s):")
            for m in markets:
                print(f"   epic={m.get('epic')!r}  name={m.get('instrumentName')!r}")
            return markets
        else:
            try:
                print(json.dumps(resp.json(), indent=2))
            except Exception:
                print(resp.text[:500])
            return None
    except Exception as e:
        print(f"❌ EXCEPTION: {e}")
        return None


def main():
    print("=" * 60)
    print("  CAPITAL.COM API DIAGNOSTIC")
    print("  Tests all endpoint + identifier combinations")
    print("=" * 60)

    results = []

    for env_name, base_url in ENDPOINTS.items():
        # Test 1: Email as identifier
        cst, xst = test_session(base_url, IDENTIFIER_EMAIL, f"{env_name.upper()} + Email")
        if cst:
            results.append((env_name, "email", cst, xst))
            test_market_search(base_url, cst, xst, "US30")
            test_market_search(base_url, cst, xst, "XAUUSD")

    print("\n" + "=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    if not results:
        print("❌ All authentication attempts failed.")
        print("\nMost likely causes:")
        print("  1. You need a separate API password (not your trading password).")
        print("     Go to Capital.com → Settings → API Access → Create API Password")
        print("  2. Your API key is for LIVE but you're hitting DEMO (or vice versa).")
        print("  3. Your account type doesn't have API access enabled.")
        print("\nNext step: Try the MT5 ZeroMQ bridge instead (see mt5_bridge.py).")
    else:
        for env, id_type, cst, xst in results:
            print(f"✅ {env.upper()} + {id_type}: WORKING")
            print(f"   Use base_url = '{ENDPOINTS[env]}'")

    print("=" * 60)


if __name__ == "__main__":
    main()