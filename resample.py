"""
resample.py - Dynamic Timeframe Aggregator & Resampler

Resamples fine-grained candles (e.g. M1, M5) into higher-timeframe OHLC bars
(M15, H1, H4, D1...) by aggregating open/high/low/close.
"""
from datetime import datetime
from typing import List, Optional
from models import Candle

_KNOWN_FORMATS = [
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y.%m.%d %H:%M:%S",
    "%Y.%m.%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",
]


def _parse_timestamp(raw) -> Optional[datetime]:
    if isinstance(raw, datetime):
        return raw
    s = str(raw).strip()
    for fmt in _KNOWN_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def detect_base_timeframe(candles: List[Candle]) -> int:
    """Calculates the native candle interval in minutes from loaded data."""
    if len(candles) < 2:
        return 1
    
    dt0 = _parse_timestamp(candles[0].timestamp)
    dt1 = _parse_timestamp(candles[1].timestamp)
    
    if dt0 and dt1:
        delta = (dt1 - dt0).total_seconds() / 60.0
        return max(1, int(delta))
    return 1


def _bucket_start(dt: datetime, minutes: int) -> datetime:
    total_minutes = dt.hour * 60 + dt.minute
    bucket_minutes = (total_minutes // minutes) * minutes
    return dt.replace(hour=0, minute=0, second=0, microsecond=0).replace(
        hour=bucket_minutes // 60, minute=bucket_minutes % 60
    )


def resample_candles(candles: List[Candle], minutes: int) -> List[Candle]:
    """
    minutes: target bar size in minutes (15 = M15, 60 = H1, 240 = H4, 1440 = D1)
    """
    if not candles:
        return []

    # Safeguard: Validate that base resolution isn't higher than target
    base_min = detect_base_timeframe(candles)
    if base_min > minutes:
        raise ValueError(
            f"Cannot resample base data ({base_min}m) down to lower target timeframe ({minutes}m). "
            f"Provide a CSV with a base timeframe <= {minutes}m."
        )
    if base_min == minutes:
        return candles

    parsed_first = _parse_timestamp(candles[0].timestamp)
    use_time_buckets = parsed_first is not None

    resampled: List[Candle] = []

    if use_time_buckets:
        current_bucket = None
        bucket_candles: List[Candle] = []

        def flush(idx):
            if not bucket_candles:
                return
            c = Candle(
                index=idx,
                timestamp=bucket_candles[0].timestamp,
                open=bucket_candles[0].open,
                high=max(item.high for item in bucket_candles),
                low=min(item.low for item in bucket_candles),
                close=bucket_candles[-1].close,
            )
            c.volume = sum(getattr(item, 'volume', 0.0) for item in bucket_candles)
            resampled.append(c)

        for c in candles:
            dt = _parse_timestamp(c.timestamp)
            bucket = _bucket_start(dt, minutes) if dt else None
            if bucket != current_bucket:
                flush(len(resampled))
                bucket_candles = []
                current_bucket = bucket
            bucket_candles.append(c)
        flush(len(resampled))
    else:
        print("WARNING: could not parse timestamps for time-based resampling - "
              "falling back to positional grouping (every N raw candles = 1 bar). "
              "This assumes no gaps in the source data (weekends will misalign it). "
              "Consider fixing the timestamp format for accurate resampling.")
        group_size = max(1, int(minutes / base_min))
        for i in range(0, len(candles), group_size):
            chunk = candles[i:i + group_size]
            if not chunk:
                continue
            c = Candle(
                index=len(resampled),
                timestamp=chunk[0].timestamp,
                open=chunk[0].open,
                high=max(item.high for item in chunk),
                low=min(item.low for item in chunk),
                close=chunk[-1].close,
            )
            c.volume = sum(getattr(item, 'volume', 0.0) for item in chunk)
            resampled.append(c)

    # Re-index sequentially (structure/swings code relies on index order, not values)
    for i, c in enumerate(resampled):
        c.index = i

    return resampled