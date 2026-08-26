"""
data_loader.py - Universal Multi-Format CSV Parser
Handles standard headers, tab-delimited MetaTrader exports, and raw OHLCV files.
"""
import pandas as pd
from typing import List
from models import Candle

def load_candles_from_csv(filepath: str) -> List[Candle]:
    # 1. Detect delimiter (handles tab-separated MT4/MT5 exports)
    with open(filepath, 'r') as f:
        first_line = f.readline()
    sep = '\t' if '\t' in first_line else ','

    df = pd.read_csv(filepath, sep=sep)
    
    # Strip whitespace and lowercase all headers
    df.columns = [c.strip().lower() for c in df.columns]
    
    # 2. Parse timestamps across standard, combined datetime, MT4/MT5, and raw formats
    if '<date>' in df.columns and '<time>' in df.columns:
        df['timestamp'] = pd.to_datetime(df['<date>'].astype(str) + ' ' + df['<time>'].astype(str))
    elif 'date' in df.columns and 'time' in df.columns:
        df['timestamp'] = pd.to_datetime(df['date'].astype(str) + ' ' + df['time'].astype(str))
    elif 'datetime' in df.columns:
        df['timestamp'] = pd.to_datetime(df['datetime'])
    elif 'timestamp' in df.columns:
        df['timestamp'] = pd.to_datetime(df['timestamp'])
    elif 'time' in df.columns:
        df['timestamp'] = pd.to_datetime(df['time'])
    else:
        raise ValueError(f"Could not identify date/time columns in {filepath}. Columns found: {list(df.columns)}")
    
    # 3. Map price and volume columns dynamically
    rename_map = {}
    for col in df.columns:
        if 'open' in col: 
            rename_map[col] = 'open'
        elif 'high' in col: 
            rename_map[col] = 'high'
        elif 'low' in col: 
            rename_map[col] = 'low'
        elif 'close' in col: 
            rename_map[col] = 'close'
        elif 'tickvol' in col or 'vol' in col or 'volume' in col:
            if 'volume' not in rename_map.values():
                rename_map[col] = 'volume'
                
    df = df.rename(columns=rename_map)
    
    # Fallback if volume is missing
    if 'volume' not in df.columns:
        df['volume'] = 0.0

    # Convert to Candle objects safely
    candles = []
    for idx, row in df.iterrows():
        c = Candle(
            index=int(idx),
            timestamp=pd.to_datetime(row['timestamp']),
            open=float(row['open']),
            high=float(row['high']),
            low=float(row['low']),
            close=float(row['close'])
        )
        c.volume = float(row.get('volume', 0.0))
        candles.append(c)

    return candles