"""
Dhan API Expired Options Data Pipeline

This script retrieves expired options data from the Dhan API for various stocks.
Data is available for the past 5 years with minute-level granularity.

API Endpoint: POST /v2/charts/rollingoption

Usage:
    python dhan_expired_options.py

Requirements:
    pip install requests
    pip install --pre dhanhq  # Optional: for other Dhan API features
"""

import requests
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
import json

# ===== CONFIGURATION =====
DHAN_CLIENT_ID = "1108815651"
DHAN_ACCESS_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiIiwiZXhwIjoxNzcwMzExNTA3LCJpYXQiOjE3NzAyMjUxMDcsInRva2VuQ29uc3VtZXJUeXBlIjoiU0VMRiIsIndlYmhvb2tVcmwiOiIiLCJkaGFuQ2xpZW50SWQiOiIxMTA4ODE1NjUxIn0.iZXpo6Z42WqVuMEPczXQSPDEXgP3y699_OK_hLdsugl8_dgnpC_7Akeh-IniThQ29xyd6F4buNYsotqfwFgexQ"

API_BASE_URL = "https://api.dhan.co/v2"

# Security IDs for popular stocks/indices (verified working)
SECURITY_IDS = {
    # Indices
    "NIFTY": (13, "OPTIDX"),
    "BANKNIFTY": (25, "OPTIDX"),
    "FINNIFTY": (27, "OPTIDX"),
    # Stocks
    "HDFCLIFE": (467, "OPTSTK"),
    "RELIANCE": (2885, "OPTSTK"),
    "TCS": (11536, "OPTSTK"),
    "INFY": (1594, "OPTSTK"),
    "HDFCBANK": (1333, "OPTSTK"),
    "ICICIBANK": (4963, "OPTSTK"),
    "SBIN": (3045, "OPTSTK"),
}


def get_api_headers() -> Dict[str, str]:
    """Return headers for Dhan API requests."""
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "access-token": DHAN_ACCESS_TOKEN
    }


def get_expired_options_data(
    security_id: int,
    instrument: str = "OPTIDX",
    exchange_segment: str = "NSE_FNO",
    expiry_flag: str = "MONTH",
    expiry_code: int = 1,
    strike: str = "ATM",
    option_type: str = "CALL",
    interval: str = "1",
    required_data: Optional[List[str]] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Fetch expired options data for a given security using direct API call.

    Data is available for the past 5 years with minute-level granularity.
    Maximum 30 days of data can be fetched per API call.

    Args:
        security_id: The security ID of the underlying (e.g., 13 for Nifty)
        instrument: Instrument type - OPTIDX for index options, OPTSTK for stock options
        exchange_segment: Exchange segment (NSE_FNO, BSE_FNO)
        expiry_flag: Expiry type - "WEEK" or "MONTH"
        expiry_code: Number of expiries back (1 = last expiry, 2 = 2nd last, etc.)
        strike: Strike selection - "ATM", "ATM+1", "ATM+2", "ATM-1", "ATM-2", etc.
                (Up to ATM+10/ATM-10 for Index Options, ATM+3/ATM-3 for stocks)
        option_type: Option type - "CALL" or "PUT"
        interval: Minute interval - "1", "5", "15", "25", "60"
        required_data: List of data fields to fetch:
                      ["open", "high", "low", "close", "volume", "oi", "iv", "spot", "strike"]
        from_date: Start date in YYYY-MM-DD format
        to_date: End date in YYYY-MM-DD format (non-inclusive, max 30 days from from_date)

    Returns:
        dict: API response containing expired options data with structure:
              {"data": {"ce": {...}, "pe": null}} for CALL
              {"data": {"ce": null, "pe": {...}}} for PUT
    """
    if required_data is None:
        required_data = ["open", "high", "low", "close", "volume", "oi", "iv", "spot"]

    # Default date range: last 30 days
    if to_date is None:
        to_date = datetime.now().strftime("%Y-%m-%d")
    if from_date is None:
        from_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

    payload = {
        "exchangeSegment": exchange_segment,
        "interval": interval,
        "securityId": security_id,
        "instrument": instrument,
        "expiryFlag": expiry_flag,
        "expiryCode": expiry_code,
        "strike": strike,
        "drvOptionType": option_type,
        "requiredData": required_data,
        "fromDate": from_date,
        "toDate": to_date
    }

    try:
        response = requests.post(
            f"{API_BASE_URL}/charts/rollingoption",
            headers=get_api_headers(),
            json=payload
        )
        response.raise_for_status()
        return {"status": "success", **response.json()}
    except requests.exceptions.RequestException as e:
        return {"status": "error", "message": str(e)}


def get_expired_options_by_symbol(
    symbol: str,
    option_type: str = "CALL",
    expiry_flag: str = "MONTH",
    expiry_code: int = 1,
    strike: str = "ATM",
    interval: str = "1",
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Convenience function to get expired options data by stock symbol name.

    Args:
        symbol: Stock symbol (e.g., "NIFTY", "BANKNIFTY", "HDFCLIFE", "RELIANCE")
        option_type: "CALL" or "PUT"
        expiry_flag: "WEEK" or "MONTH"
        expiry_code: Number of expiries back (1 = last expiry)
        strike: Strike selection - "ATM", "ATM+1", "ATM-1", etc.
        interval: Minute interval - "1", "5", "15", "25", "60"
        from_date: Start date (YYYY-MM-DD)
        to_date: End date (YYYY-MM-DD)

    Returns:
        dict: API response with expired options data
    """
    symbol_upper = symbol.upper()
    if symbol_upper not in SECURITY_IDS:
        return {
            "status": "error",
            "message": f"Unknown symbol: {symbol}. Available: {list(SECURITY_IDS.keys())}",
        }

    security_id, instrument = SECURITY_IDS[symbol_upper]

    return get_expired_options_data(
        security_id=security_id,
        instrument=instrument,
        option_type=option_type,
        expiry_flag=expiry_flag,
        expiry_code=expiry_code,
        strike=strike,
        interval=interval,
        from_date=from_date,
        to_date=to_date,
    )


def fetch_security_list() -> Dict[str, Any]:
    """Fetch the complete security/instrument list from Dhan."""
    try:
        from dhanhq import DhanContext, dhanhq
        dhan_context = DhanContext(DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN)
        dhan = dhanhq(dhan_context)
        return dhan.fetch_security_list("compact")
    except ImportError:
        return {"status": "error", "message": "dhanhq library not installed. Run: pip install --pre dhanhq"}


def get_expiry_list(security_id: int = 13, exchange_segment: str = "IDX_I") -> Dict[str, Any]:
    """
    Get the list of available expiries for an underlying security.

    Args:
        security_id: Security ID (e.g., 13 for Nifty, 25 for BankNifty)
        exchange_segment: Exchange segment (IDX_I for indices, NSE_EQ for equity)

    Returns:
        dict: API response with expiry dates
    """
    try:
        from dhanhq import DhanContext, dhanhq
        dhan_context = DhanContext(DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN)
        dhan = dhanhq(dhan_context)
        return dhan.expiry_list(
            under_security_id=security_id,
            under_exchange_segment=exchange_segment
        )
    except ImportError:
        return {"status": "error", "message": "dhanhq library not installed"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ===== MAIN EXECUTION =====
if __name__ == "__main__":
    print("=" * 70)
    print("Dhan API - Expired Options Data Pipeline")
    print("=" * 70)

    # Example 1: Fetch NIFTY expired CALL options
    print("\n1. Fetching NIFTY expired CALL options (Jan 2025, monthly expiry)...")
    nifty_data = get_expired_options_by_symbol(
        symbol="NIFTY",
        option_type="CALL",
        expiry_flag="MONTH",
        strike="ATM",
        from_date="2025-01-01",
        to_date="2025-01-31",
    )
    if nifty_data.get("status") == "success":
        ce_data = nifty_data.get("data", {}).get("ce", {})
        data_points = len(ce_data.get("open", []))
        print(f"✓ Retrieved {data_points:,} data points")
        if data_points > 0:
            print(f"  Sample open prices: {ce_data.get('open', [])[:5]}")
    else:
        print(f"✗ Error: {nifty_data.get('message', 'Unknown error')}")

    # Example 2: Fetch BANKNIFTY expired PUT options
    print("\n2. Fetching BANKNIFTY expired PUT options (Dec 2024)...")
    banknifty_data = get_expired_options_by_symbol(
        symbol="BANKNIFTY",
        option_type="PUT",
        from_date="2024-12-01",
        to_date="2024-12-31",
    )
    if banknifty_data.get("status") == "success":
        pe_data = banknifty_data.get("data", {}).get("pe", {})
        data_points = len(pe_data.get("open", []))
        print(f"✓ Retrieved {data_points:,} data points")
    else:
        print(f"✗ Error: {banknifty_data.get('message', 'Unknown error')}")

    # Example 3: Fetch historical data from 2021
    print("\n3. Fetching NIFTY data from 2021 (testing 5-year history)...")
    historical_data = get_expired_options_data(
        security_id=13,
        instrument="OPTIDX",
        expiry_flag="MONTH",
        strike="ATM",
        option_type="CALL",
        from_date="2021-06-01",
        to_date="2021-06-30",
    )
    if historical_data.get("status") == "success":
        ce_data = historical_data.get("data", {}).get("ce", {})
        data_points = len(ce_data.get("open", []))
        print(f"✓ Retrieved {data_points:,} data points from June 2021")
    else:
        print(f"✗ Error: {historical_data.get('message', 'Unknown error')}")

    # Example 4: Fetch stock options (HDFCBANK)
    print("\n4. Fetching HDFCBANK stock options (Jun 2024)...")
    stock_data = get_expired_options_by_symbol(
        symbol="HDFCBANK",
        option_type="CALL",
        from_date="2024-06-01",
        to_date="2024-06-30",
    )
    if stock_data.get("status") == "success":
        ce_data = stock_data.get("data", {}).get("ce", {})
        data_points = len(ce_data.get("open", []))
        print(f"✓ Retrieved {data_points:,} data points")
    else:
        print(f"✗ Error: {stock_data.get('message', 'Unknown error')}")

    print("\n" + "=" * 70)
    print("Pipeline execution complete!")
    print("\n📖 Usage Examples:")
    print("  # Simple usage by symbol name:")
    print("  data = get_expired_options_by_symbol('NIFTY', 'CALL', from_date='2024-01-01', to_date='2024-01-31')")
    print("")
    print("  # Advanced usage with all parameters:")
    print("  data = get_expired_options_data(")
    print("      security_id=13,        # NIFTY")
    print("      instrument='OPTIDX',   # Index options")
    print("      option_type='CALL',")
    print("      strike='ATM+1',        # One strike above ATM")
    print("      expiry_flag='WEEK',    # Weekly expiry")
    print("      interval='5',          # 5-minute candles")
    print("      from_date='2024-06-01',")
    print("      to_date='2024-06-30'")
    print("  )")
    print("")
    print("📊 Available symbols:", list(SECURITY_IDS.keys()))

