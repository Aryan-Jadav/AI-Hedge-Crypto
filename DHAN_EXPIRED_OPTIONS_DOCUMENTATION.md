# Dhan API - Expired Options Data Documentation

## Table of Contents

1. [Overview](#overview)
2. [Authentication](#authentication)
3. [API Endpoint](#api-endpoint)
4. [Request Parameters](#request-parameters)
5. [Response Structure](#response-structure)
6. [Available Securities](#available-securities)
7. [Usage Examples](#usage-examples)
8. [Data Fields Reference](#data-fields-reference)
9. [Best Practices](#best-practices)
10. [Error Handling](#error-handling)
11. [Limitations & Constraints](#limitations--constraints)
12. [FAQ](#faq)

---

## Overview

The Dhan API provides access to **expired options contract data** on a rolling basis for the **past 5 years** with **minute-level granularity**. This data is pre-processed and available for:

- **Index Options** (NIFTY, BANKNIFTY, FINNIFTY)
- **Stock Options** (F&O stocks like RELIANCE, HDFCBANK, TCS, etc.)

### Key Features

| Feature | Details |
|---------|---------|
| **Data History** | 5 years of historical data |
| **Granularity** | Minute-level candles (1, 5, 15, 25, 60 min) |
| **Strike Coverage** | ATM and up to 10 strikes above/below for indices, 3 for stocks |
| **Expiry Types** | Weekly (WEEK) and Monthly (MONTH) |
| **Option Types** | CALL and PUT |
| **Data Fields** | OHLCV, Open Interest, Implied Volatility, Spot Price, Strike Price |

---

## Authentication

### Required Credentials

```python
DHAN_CLIENT_ID = "your_client_id"
DHAN_ACCESS_TOKEN = "your_jwt_access_token"
```

### HTTP Headers

All API requests must include these headers:

```python
headers = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "access-token": "your_jwt_access_token",
    "client-id": "your_client_id"  # REQUIRED - added Feb 2025
}
```

**Important:** Both `access-token` AND `client-id` headers are required. Missing the `client-id` header will result in HTTP 401 Unauthorized errors.

### Getting Your Credentials

1. Log in to your Dhan account at [https://dhan.co](https://dhan.co)
2. Navigate to API section in settings
3. Generate an access token (valid for ~1 year)
4. Copy your Client ID and Access Token

---

## API Endpoint

### Endpoint URL

```
POST https://api.dhan.co/v2/charts/rollingoption
```

### Request Method

`POST` with JSON payload

### Content Type

`application/json`

---

## Request Parameters

### Complete Parameter Reference

| Parameter | Type | Required | Description | Valid Values |
|-----------|------|----------|-------------|--------------|
| `exchangeSegment` | string | Yes | Exchange segment | `NSE_FNO`, `BSE_FNO` |
| `securityId` | integer | Yes | Underlying security ID | See [Available Securities](#available-securities) |
| `instrument` | string | Yes | Instrument type | `OPTIDX` (Index Options), `OPTSTK` (Stock Options) |
| `expiryFlag` | string | Yes | Expiry type | `WEEK`, `MONTH` |
| `expiryCode` | integer | Yes | Expiries back from current | `1` = last expiry, `2` = 2nd last, etc. |
| `strike` | string | Yes | Strike selection | See [Strike Values](#strike-values) |
| `drvOptionType` | string | Yes | Option type | `CALL`, `PUT` |
| `interval` | string | Yes | Candle interval (minutes) | `1`, `5`, `15`, `25`, `60` |
| `requiredData` | array | No | Data fields to fetch | See [Data Fields](#data-fields-reference) |
| `fromDate` | string | Yes | Start date | `YYYY-MM-DD` format |
| `toDate` | string | Yes | End date | `YYYY-MM-DD` format |

### Strike Values

#### For Index Options (OPTIDX)

| Strike | Description |
|--------|-------------|
| `ATM` | At-The-Money strike |
| `ATM+1` to `ATM+10` | 1 to 10 strikes above ATM |
| `ATM-1` to `ATM-10` | 1 to 10 strikes below ATM |

#### For Stock Options (OPTSTK)

| Strike | Description |
|--------|-------------|
| `ATM` | At-The-Money strike |
| `ATM+1` to `ATM+3` | 1 to 3 strikes above ATM |
| `ATM-1` to `ATM-3` | 1 to 3 strikes below ATM |

### Interval Values

| Interval | Description |
|----------|-------------|
| `1` | 1-minute candles |
| `5` | 5-minute candles |
| `15` | 15-minute candles |
| `25` | 25-minute candles |
| `60` | 60-minute (1-hour) candles |

---

## Response Structure

### Successful Response

```json
{
    "data": {
        "ce": {
            "open": [180.5, 188.35, 189.0, ...],
            "high": [190.0, 195.5, 192.0, ...],
            "low": [178.0, 186.0, 188.5, ...],
            "close": [188.35, 189.0, 191.5, ...],
            "volume": [1000, 1500, 2000, ...],
            "oi": [50000, 51000, 52000, ...],
            "iv": [15.5, 15.8, 16.0, ...],
            "spot": [23500, 23510, 23520, ...],
            "strike": [23500, 23500, 23500, ...]
        },
        "pe": null
    }
}
```

### Response Keys

| Key | Description |
|-----|-------------|
| `ce` | Call option data (populated when `drvOptionType` = `CALL`) |
| `pe` | Put option data (populated when `drvOptionType` = `PUT`) |

**Note:** Only one of `ce` or `pe` will contain data based on your request. The other will be `null`.

---

## Available Securities

### Index Options (instrument: `OPTIDX`)

| Symbol | Security ID | Description |
|--------|-------------|-------------|
| NIFTY | `13` | Nifty 50 Index |
| BANKNIFTY | `25` | Bank Nifty Index |
| FINNIFTY | `27` | Fin Nifty Index |

### Stock Options (instrument: `OPTSTK`)

| Symbol | Security ID | Description |
|--------|-------------|-------------|
| HDFCLIFE | `467` | HDFC Life Insurance |
| RELIANCE | `2885` | Reliance Industries |
| TCS | `11536` | Tata Consultancy Services |
| INFY | `1594` | Infosys |
| HDFCBANK | `1333` | HDFC Bank |
| ICICIBANK | `4963` | ICICI Bank |
| SBIN | `3045` | State Bank of India |

### Finding Other Security IDs

To find security IDs for other F&O stocks, use the `fetch_security_list()` function or download the instrument list from Dhan:

```python
from dhan_expired_options import fetch_security_list

# Fetch complete instrument list
securities = fetch_security_list()
```

---

## Data Fields Reference

### Available Data Fields

| Field | Type | Description |
|-------|------|-------------|
| `open` | float[] | Opening price of each candle |
| `high` | float[] | Highest price in each candle |
| `low` | float[] | Lowest price in each candle |
| `close` | float[] | Closing price of each candle |
| `volume` | int[] | Trading volume in each candle |
| `oi` | int[] | Open Interest at each candle |
| `iv` | float[] | Implied Volatility (%) |
| `spot` | float[] | Underlying spot price |
| `strike` | float[] | Strike price of the option |

### Requesting Specific Fields

```python
# Request only OHLCV data
required_data = ["open", "high", "low", "close", "volume"]

# Request all available data
required_data = ["open", "high", "low", "close", "volume", "oi", "iv", "spot", "strike"]
```

---

## Usage Examples

### Example 1: Basic Usage - Fetch NIFTY CALL Options

```python
from dhan_expired_options import get_expired_options_by_symbol

# Fetch NIFTY ATM CALL options for January 2025
data = get_expired_options_by_symbol(
    symbol="NIFTY",
    option_type="CALL",
    from_date="2025-01-01",
    to_date="2025-01-31"
)

# Access the data
if data.get("status") == "success":
    ce_data = data["data"]["ce"]
    print(f"Data points: {len(ce_data['open'])}")
    print(f"Open prices: {ce_data['open'][:5]}")
```

### Example 2: Fetch PUT Options

```python
# Fetch BANKNIFTY PUT options
data = get_expired_options_by_symbol(
    symbol="BANKNIFTY",
    option_type="PUT",
    from_date="2024-12-01",
    to_date="2024-12-31"
)

if data.get("status") == "success":
    pe_data = data["data"]["pe"]
    print(f"Data points: {len(pe_data['open'])}")
```

### Example 3: Weekly vs Monthly Expiry

```python
# Weekly expiry
weekly_data = get_expired_options_by_symbol(
    symbol="NIFTY",
    option_type="CALL",
    expiry_flag="WEEK",  # Weekly expiry
    from_date="2024-06-01",
    to_date="2024-06-30"
)

# Monthly expiry
monthly_data = get_expired_options_by_symbol(
    symbol="NIFTY",
    option_type="CALL",
    expiry_flag="MONTH",  # Monthly expiry
    from_date="2024-06-01",
    to_date="2024-06-30"
)
```

### Example 4: Different Strike Levels

```python
# ATM (At-The-Money)
atm_data = get_expired_options_by_symbol(
    symbol="NIFTY",
    option_type="CALL",
    strike="ATM",
    from_date="2024-06-01",
    to_date="2024-06-30"
)

# 1 Strike Above ATM (OTM for CALL)
otm1_data = get_expired_options_by_symbol(
    symbol="NIFTY",
    option_type="CALL",
    strike="ATM+1",
    from_date="2024-06-01",
    to_date="2024-06-30"
)

# 2 Strikes Below ATM (ITM for CALL)
itm2_data = get_expired_options_by_symbol(
    symbol="NIFTY",
    option_type="CALL",
    strike="ATM-2",
    from_date="2024-06-01",
    to_date="2024-06-30"
)
```

### Example 5: Different Time Intervals

```python
# 1-minute candles (default)
data_1min = get_expired_options_by_symbol(
    symbol="NIFTY",
    option_type="CALL",
    interval="1",
    from_date="2024-06-01",
    to_date="2024-06-30"
)

# 5-minute candles
data_5min = get_expired_options_by_symbol(
    symbol="NIFTY",
    option_type="CALL",
    interval="5",
    from_date="2024-06-01",
    to_date="2024-06-30"
)

# 15-minute candles
data_15min = get_expired_options_by_symbol(
    symbol="NIFTY",
    option_type="CALL",
    interval="15",
    from_date="2024-06-01",
    to_date="2024-06-30"
)

# Hourly candles
data_hourly = get_expired_options_by_symbol(
    symbol="NIFTY",
    option_type="CALL",
    interval="60",
    from_date="2024-06-01",
    to_date="2024-06-30"
)
```

### Example 6: Historical Data (5 Years Back)

```python
# Fetch data from 2021 (proving 5-year availability)
historical_data = get_expired_options_by_symbol(
    symbol="NIFTY",
    option_type="CALL",
    from_date="2021-06-01",
    to_date="2021-06-30"
)

# Fetch data from 2022
data_2022 = get_expired_options_by_symbol(
    symbol="BANKNIFTY",
    option_type="PUT",
    from_date="2022-03-01",
    to_date="2022-03-31"
)
```

### Example 7: Stock Options

```python
# HDFCBANK stock options
hdfcbank_data = get_expired_options_by_symbol(
    symbol="HDFCBANK",
    option_type="CALL",
    strike="ATM",
    from_date="2024-06-01",
    to_date="2024-06-30"
)

# RELIANCE stock options
reliance_data = get_expired_options_by_symbol(
    symbol="RELIANCE",
    option_type="PUT",
    strike="ATM-1",
    from_date="2024-06-01",
    to_date="2024-06-30"
)
```

### Example 8: Advanced Usage with Direct API Function

```python
from dhan_expired_options import get_expired_options_data

# Full control over all parameters
data = get_expired_options_data(
    security_id=13,              # NIFTY
    instrument="OPTIDX",         # Index options
    exchange_segment="NSE_FNO",  # NSE F&O segment
    expiry_flag="WEEK",          # Weekly expiry
    expiry_code=1,               # Last expiry
    strike="ATM+2",              # 2 strikes above ATM
    option_type="CALL",
    interval="5",                # 5-minute candles
    required_data=["open", "high", "low", "close", "volume", "oi", "iv"],
    from_date="2024-06-01",
    to_date="2024-06-30"
)
```

### Example 9: Fetching Multiple Months (Handling 30-Day Limit)

```python
import pandas as pd
from datetime import datetime, timedelta
from dhan_expired_options import get_expired_options_by_symbol

def fetch_multiple_months(symbol, option_type, start_date, end_date):
    """Fetch data across multiple months by chunking into 30-day periods."""
    all_data = {
        "open": [], "high": [], "low": [], "close": [],
        "volume": [], "oi": [], "iv": [], "spot": []
    }

    current_start = datetime.strptime(start_date, "%Y-%m-%d")
    final_end = datetime.strptime(end_date, "%Y-%m-%d")

    while current_start < final_end:
        current_end = min(current_start + timedelta(days=30), final_end)

        data = get_expired_options_by_symbol(
            symbol=symbol,
            option_type=option_type,
            from_date=current_start.strftime("%Y-%m-%d"),
            to_date=current_end.strftime("%Y-%m-%d")
        )

        if data.get("status") == "success":
            key = "ce" if option_type == "CALL" else "pe"
            opt_data = data["data"][key]

            for field in all_data.keys():
                if field in opt_data:
                    all_data[field].extend(opt_data[field])

        current_start = current_end

    return all_data

# Fetch 3 months of data
data = fetch_multiple_months("NIFTY", "CALL", "2024-01-01", "2024-03-31")
print(f"Total data points: {len(data['open'])}")
```

### Example 10: Converting to Pandas DataFrame

```python
import pandas as pd
from dhan_expired_options import get_expired_options_by_symbol

# Fetch data
data = get_expired_options_by_symbol(
    symbol="NIFTY",
    option_type="CALL",
    from_date="2024-06-01",
    to_date="2024-06-30"
)

# Convert to DataFrame
if data.get("status") == "success":
    ce_data = data["data"]["ce"]
    df = pd.DataFrame({
        "open": ce_data.get("open", []),
        "high": ce_data.get("high", []),
        "low": ce_data.get("low", []),
        "close": ce_data.get("close", []),
        "volume": ce_data.get("volume", []),
        "oi": ce_data.get("oi", []),
        "iv": ce_data.get("iv", []),
        "spot": ce_data.get("spot", [])
    })

    print(df.head())
    print(f"\nDataFrame shape: {df.shape}")
    print(f"\nStatistics:\n{df.describe()}")
```

---

## Best Practices

### 1. Date Range Management

```python
# ✅ GOOD: Keep date ranges within 30 days
data = get_expired_options_by_symbol(
    symbol="NIFTY",
    option_type="CALL",
    from_date="2024-06-01",
    to_date="2024-06-30"  # 30 days or less
)

# ❌ BAD: Date range exceeds 30 days
data = get_expired_options_by_symbol(
    symbol="NIFTY",
    option_type="CALL",
    from_date="2024-01-01",
    to_date="2024-12-31"  # Too long!
)
```

### 2. Use Appropriate Intervals

```python
# For intraday analysis: 1 or 5 minute intervals
# For swing trading analysis: 15 or 60 minute intervals

# High-frequency data (more data points, larger response)
data = get_expired_options_by_symbol(symbol="NIFTY", interval="1", ...)

# Lower-frequency data (fewer data points, smaller response)
data = get_expired_options_by_symbol(symbol="NIFTY", interval="60", ...)
```

### 3. Error Handling

```python
from dhan_expired_options import get_expired_options_by_symbol

data = get_expired_options_by_symbol(
    symbol="NIFTY",
    option_type="CALL",
    from_date="2024-06-01",
    to_date="2024-06-30"
)

# Always check status before accessing data
if data.get("status") == "success":
    ce_data = data["data"]["ce"]
    if ce_data and ce_data.get("open"):
        # Process data
        print(f"Got {len(ce_data['open'])} data points")
    else:
        print("No data available for this period")
else:
    print(f"Error: {data.get('message', 'Unknown error')}")
```

### 4. Correct Instrument Type Selection

```python
# ✅ For Index Options (NIFTY, BANKNIFTY, FINNIFTY)
instrument = "OPTIDX"

# ✅ For Stock Options (RELIANCE, HDFCBANK, TCS, etc.)
instrument = "OPTSTK"

# ❌ DON'T mix them up - will return empty data!
```

### 5. Strike Selection for Different Strategies

```python
# For ATM strategies (straddles, strangles at ATM)
strike = "ATM"

# For OTM CALL spreads
strikes = ["ATM+1", "ATM+2", "ATM+3"]

# For OTM PUT spreads
strikes = ["ATM-1", "ATM-2", "ATM-3"]

# For iron condors (OTM on both sides)
call_strikes = ["ATM+2", "ATM+3"]
put_strikes = ["ATM-2", "ATM-3"]
```

---

## Error Handling

### Common Errors and Solutions

| Error | Cause | Solution |
|-------|-------|----------|
| HTTP 401 | Invalid/expired access token | Generate new access token from Dhan |
| HTTP 400 | Invalid parameters | Check parameter values against documentation |
| Empty data | Wrong instrument type | Use `OPTIDX` for indices, `OPTSTK` for stocks |
| Empty data | Date range too old | Data only available for past 5 years |
| Empty data | Invalid security ID | Verify security ID from instrument list |

### Error Response Format

```json
{
    "status": "error",
    "message": "Description of the error"
}
```

### Handling Network Errors

```python
from dhan_expired_options import get_expired_options_by_symbol
import time

def fetch_with_retry(symbol, option_type, from_date, to_date, max_retries=3):
    """Fetch data with automatic retry on failure."""
    for attempt in range(max_retries):
        try:
            data = get_expired_options_by_symbol(
                symbol=symbol,
                option_type=option_type,
                from_date=from_date,
                to_date=to_date
            )

            if data.get("status") == "success":
                return data
            else:
                print(f"Attempt {attempt + 1} failed: {data.get('message')}")

        except Exception as e:
            print(f"Attempt {attempt + 1} error: {e}")

        if attempt < max_retries - 1:
            time.sleep(2 ** attempt)  # Exponential backoff

    return {"status": "error", "message": "Max retries exceeded"}
```

---

## Limitations & Constraints

### API Limits

| Constraint | Value |
|------------|-------|
| Maximum date range per request | 30 days |
| Historical data availability | 5 years |
| Strike range (Index Options) | ATM ± 10 |
| Strike range (Stock Options) | ATM ± 3 |
| Minimum interval | 1 minute |
| Maximum interval | 60 minutes |

### Data Availability Notes

1. **Rolling Basis Data**: The data is pre-processed and available on a rolling basis based on ATM and surrounding strikes at the time of each candle.

2. **No Specific Strike Selection**: You cannot request a specific strike price (e.g., 23500). You must use relative notation (ATM, ATM+1, ATM-1, etc.).

3. **Single Option Type Per Request**: Each request returns either CALL or PUT data, not both. Make separate requests for each.

4. **Expiry Code**: The `expiryCode` parameter refers to how many expiries back from the current date. `1` = last expired, `2` = second last expired, etc.

---

## FAQ

### Q: How do I get data for a specific strike price like 23500?

**A:** The API uses relative strike notation (ATM, ATM+1, etc.) based on the spot price at each candle. You cannot request a specific strike price directly. The `strike` field in the response will show the actual strike prices used.

### Q: Why am I getting empty data?

**A:** Common causes:
- Wrong `instrument` type (`OPTIDX` vs `OPTSTK`)
- Date range is outside the 5-year window
- Invalid security ID
- Date range exceeds 30 days

### Q: Can I get both CALL and PUT data in one request?

**A:** No. Make separate requests with `drvOptionType="CALL"` and `drvOptionType="PUT"`.

### Q: What does `expiryCode` mean?

**A:** It represents how many expiries back from today:
- `expiryCode=1`: Last expired contract
- `expiryCode=2`: Second last expired contract
- And so on...

### Q: How do I know which security ID to use?

**A:** Use the `fetch_security_list()` function or download the instrument master from Dhan. Common IDs are pre-configured in the `SECURITY_IDS` dictionary.

### Q: Is the data adjusted for stock splits/bonuses?

**A:** The strike prices are based on the actual values at the time. For historical analysis, you may need to account for corporate actions manually.

### Q: What timezone is the data in?

**A:** All timestamps are in Indian Standard Time (IST, UTC+5:30).

### Q: How many data points can I expect per month?

**A:** Approximately 7,000-8,600 data points per month with 1-minute candles (varies based on trading days and market hours).

---

## Quick Reference Card

### Minimum Required Payload

```json
{
    "exchangeSegment": "NSE_FNO",
    "securityId": 13,
    "instrument": "OPTIDX",
    "expiryFlag": "MONTH",
    "expiryCode": 1,
    "strike": "ATM",
    "drvOptionType": "CALL",
    "interval": "1",
    "fromDate": "2024-06-01",
    "toDate": "2024-06-30"
}
```

### One-Liner Examples

```python
# NIFTY CALL ATM Monthly
get_expired_options_by_symbol("NIFTY", "CALL", from_date="2024-06-01", to_date="2024-06-30")

# BANKNIFTY PUT Weekly
get_expired_options_by_symbol("BANKNIFTY", "PUT", expiry_flag="WEEK", from_date="2024-06-01", to_date="2024-06-30")

# HDFCBANK CALL ATM+1
get_expired_options_by_symbol("HDFCBANK", "CALL", strike="ATM+1", from_date="2024-06-01", to_date="2024-06-30")

# NIFTY with 5-minute candles
get_expired_options_by_symbol("NIFTY", "CALL", interval="5", from_date="2024-06-01", to_date="2024-06-30")
```

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2025-02-04 | Initial documentation |

---

## Support

- **Dhan API Documentation**: [https://dhanhq.co/docs/v2/](https://dhanhq.co/docs/v2/)
- **DhanHQ Python Library**: [https://github.com/dhan-oss/dhanhq-py](https://github.com/dhan-oss/dhanhq-py)

