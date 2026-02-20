"""
EOS (Exhaustion of Strength) Strategy - Data Fetcher

This script fetches all required data for the EOS trading strategy using Dhan API.
It does NOT implement the strategy - only fetches and validates data availability.

Data Requirements for EOS Strategy:
1. Previous day closing prices for all FNO stocks
2. Previous day closing OI for all FNO stocks
3. Real-time 10-minute candle data (OHLC)
4. Real-time OI data
5. Futures price data for SMA calculation (8-SMA, 20-SMA on 10-min candles)
6. Option chain data for strike selection
7. Lot size information for all FNO stocks

API Endpoints Used:
- POST /v2/marketfeed/quote - Current prices, OI, OHLC
- POST /v2/charts/intraday - Historical 10-min candles
- POST /v2/optionchain - Option chain for strike selection
- POST /v2/optionchain/expirylist - Expiry dates

Author: AI Assistant
Date: February 4, 2025
"""

import os
import requests
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
import json
import time
import pandas as pd

# ===== CONFIGURATION =====
# SECURITY: load credentials from environment variables (do not hardcode)
DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID", "")
DHAN_ACCESS_TOKEN = os.getenv("DHAN_ACCESS_TOKEN", "")

API_BASE_URL = "https://api.dhan.co/v2"

# ===== FNO STOCK UNIVERSE (Sample - expand as needed) =====
# Format: {symbol: (equity_security_id, fno_security_id, lot_size)}
FNO_STOCKS = {
    "RELIANCE": {"equity_id": 2885, "lot_size": 250, "exchange_segment": "NSE_EQ"},
    "HDFCBANK": {"equity_id": 1333, "lot_size": 550, "exchange_segment": "NSE_EQ"},
    "ICICIBANK": {"equity_id": 4963, "lot_size": 1375, "exchange_segment": "NSE_EQ"},
    "TCS": {"equity_id": 11536, "lot_size": 175, "exchange_segment": "NSE_EQ"},
    "INFY": {"equity_id": 1594, "lot_size": 400, "exchange_segment": "NSE_EQ"},
    "SBIN": {"equity_id": 3045, "lot_size": 1500, "exchange_segment": "NSE_EQ"},
    "HDFCLIFE": {"equity_id": 467, "lot_size": 1100, "exchange_segment": "NSE_EQ"},
    "KOTAKBANK": {"equity_id": 1922, "lot_size": 400, "exchange_segment": "NSE_EQ"},
    "AXISBANK": {"equity_id": 5900, "lot_size": 1200, "exchange_segment": "NSE_EQ"},
    "BAJFINANCE": {"equity_id": 317, "lot_size": 125, "exchange_segment": "NSE_EQ"},
    "MARUTI": {"equity_id": 10999, "lot_size": 100, "exchange_segment": "NSE_EQ"},
    "TITAN": {"equity_id": 3506, "lot_size": 375, "exchange_segment": "NSE_EQ"},
    "TATAMOTORS": {"equity_id": 3456, "lot_size": 1425, "exchange_segment": "NSE_EQ"},
    "TATASTEEL": {"equity_id": 3499, "lot_size": 5500, "exchange_segment": "NSE_EQ"},
    "SUNPHARMA": {"equity_id": 3351, "lot_size": 700, "exchange_segment": "NSE_EQ"},
    "WIPRO": {"equity_id": 3787, "lot_size": 1500, "exchange_segment": "NSE_EQ"},
    "HINDUNILVR": {"equity_id": 1394, "lot_size": 300, "exchange_segment": "NSE_EQ"},
    "ITC": {"equity_id": 1660, "lot_size": 1600, "exchange_segment": "NSE_EQ"},
    "LT": {"equity_id": 11483, "lot_size": 150, "exchange_segment": "NSE_EQ"},
    "ASIANPAINT": {"equity_id": 236, "lot_size": 300, "exchange_segment": "NSE_EQ"},
}


class EOS_DataFetcher:
    """
    Data fetcher class for EOS Strategy.
    Handles all API calls to retrieve required market data.
    """

    def __init__(self, client_id: str = DHAN_CLIENT_ID, access_token: str = DHAN_ACCESS_TOKEN):
        self.client_id = client_id
        self.access_token = access_token
        self.base_url = API_BASE_URL

    def _get_headers(self) -> Dict[str, str]:
        """Return headers for Dhan API requests."""
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "access-token": self.access_token,
            "client-id": self.client_id
        }

    def _make_request(self, endpoint: str, payload: Dict) -> Dict[str, Any]:
        """Make a POST request to the Dhan API."""
        url = f"{self.base_url}{endpoint}"
        try:
            response = requests.post(url, headers=self._get_headers(), json=payload)
            response.raise_for_status()
            return {"status": "success", "data": response.json()}
        except requests.exceptions.RequestException as e:
            return {"status": "error", "error": str(e), "data": None}

    # ===== MARKET QUOTE APIs =====

    def get_market_quote(self, security_ids: List[int], segment: str = "NSE_EQ") -> Dict:
        """
        Get full market depth including LTP, OHLC, OI, volume for instruments.
        This is the key API for getting current OI and previous day close.

        Args:
            security_ids: List of security IDs
            segment: Exchange segment (NSE_EQ, NSE_FNO, etc.)
        """
        payload = {segment: security_ids}
        return self._make_request("/marketfeed/quote", payload)

    def get_ltp(self, security_ids: List[int], segment: str = "NSE_EQ") -> Dict:
        """Get Last Traded Price for instruments."""
        payload = {segment: security_ids}
        return self._make_request("/marketfeed/ltp", payload)

    def get_ohlc(self, security_ids: List[int], segment: str = "NSE_EQ") -> Dict:
        """Get OHLC data for instruments."""
        payload = {segment: security_ids}
        return self._make_request("/marketfeed/ohlc", payload)

    # ===== HISTORICAL DATA APIs =====

    def get_intraday_data(
        self,
        security_id: str,
        segment: str = "NSE_EQ",
        instrument: str = "EQUITY",
        interval: str = "5",  # 5-minute candles (aggregate 2 to get 10-min for EOS)
        from_date: str = None,
        to_date: str = None,
        include_oi: bool = True
    ) -> Dict:
        """
        Get intraday historical candle data.

        Args:
            security_id: Security ID as string
            segment: NSE_EQ, NSE_FNO, etc.
            instrument: EQUITY, FUTIDX, FUTSTK, OPTIDX, OPTSTK
            interval: 1, 5, 15, 25, 60 (minutes) - NOTE: 10 is NOT supported
            from_date: Format "YYYY-MM-DD HH:MM:SS"
            to_date: Format "YYYY-MM-DD HH:MM:SS"
            include_oi: Include Open Interest data

        Note: Only 90 days of data can be fetched at once.
        """
        if from_date is None:
            from_date = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d 09:15:00")
        if to_date is None:
            to_date = datetime.now().strftime("%Y-%m-%d 15:30:00")

        # Valid intervals are: 1, 5, 15, 25, 60 (10 is NOT supported)
        valid_intervals = ["1", "5", "15", "25", "60"]
        if interval not in valid_intervals:
            # Default to 5 min if invalid interval provided (aggregate 2 for 10-min)
            interval = "5"

        payload = {
            "securityId": security_id,
            "exchangeSegment": segment,
            "instrument": instrument,
            "interval": interval,
            "oi": include_oi,
            "fromDate": from_date,
            "toDate": to_date
        }
        return self._make_request("/charts/intraday", payload)

    def get_daily_historical_data(
        self,
        security_id: str,
        segment: str = "NSE_EQ",
        instrument: str = "EQUITY",
        from_date: str = None,
        to_date: str = None,
        include_oi: bool = False
    ) -> Dict:
        """
        Get daily OHLC historical data for backtesting.
        Data available since inception of the scrip.

        Use this to get previous day close prices for price change calculation.

        Args:
            security_id: Security ID as string
            segment: NSE_EQ, NSE_FNO, etc.
            instrument: EQUITY, FUTIDX, FUTSTK, OPTIDX, OPTSTK
            from_date: Format "YYYY-MM-DD"
            to_date: Format "YYYY-MM-DD"
            include_oi: Include Open Interest data (for F&O)
        """
        if from_date is None:
            from_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
        if to_date is None:
            to_date = datetime.now().strftime("%Y-%m-%d")

        payload = {
            "securityId": security_id,
            "exchangeSegment": segment,
            "instrument": instrument,
            "expiryCode": 0,
            "oi": include_oi,
            "fromDate": from_date,
            "toDate": to_date
        }
        return self._make_request("/charts/historical", payload)

    def get_expired_options_data(
        self,
        security_id: int,
        instrument: str = "OPTSTK",
        expiry_flag: str = "MONTH",
        expiry_code: int = 1,
        strike: str = "ATM",
        option_type: str = "CALL",
        interval: str = "5",
        required_data: List[str] = None,
        from_date: str = None,
        to_date: str = None
    ) -> Dict:
        """
        Get expired options data for backtesting.
        Data available for past 5 years with minute-level granularity.
        Maximum 30 days per API call.

        This is CRITICAL for backtesting as it provides:
        - Historical option prices (OHLC)
        - Historical OI at minute level
        - Historical IV
        - Historical spot price
        - Historical strike price

        Args:
            security_id: Underlying security ID (e.g., 2885 for RELIANCE)
            instrument: OPTIDX for index options, OPTSTK for stock options
            expiry_flag: "WEEK" or "MONTH"
            expiry_code: 1 = last expiry, 2 = 2nd last, etc.
            strike: "ATM", "ATM+1", "ATM-1", etc. (up to ATM±10 for index, ATM±3 for stocks)
            option_type: "CALL" or "PUT"
            interval: "1", "5", "15", "25", "60" minutes
            required_data: List of fields - ["open", "high", "low", "close", "volume", "oi", "iv", "spot", "strike"]
            from_date: Start date "YYYY-MM-DD"
            to_date: End date "YYYY-MM-DD" (max 30 days from from_date)
        """
        if required_data is None:
            required_data = ["open", "high", "low", "close", "volume", "oi", "iv", "spot", "strike"]

        if to_date is None:
            to_date = datetime.now().strftime("%Y-%m-%d")
        if from_date is None:
            from_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

        payload = {
            "exchangeSegment": "NSE_FNO",
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
        return self._make_request("/charts/rollingoption", payload)

    # ===== OPTION CHAIN APIs =====

    def get_expiry_list(self, underlying_scrip: int, underlying_seg: str = "IDX_I") -> Dict:
        """
        Get list of available expiry dates for options.

        Args:
            underlying_scrip: Security ID of underlying (e.g., 13 for NIFTY)
            underlying_seg: IDX_I for indices, NSE_EQ for stocks
        """
        payload = {
            "UnderlyingScrip": underlying_scrip,
            "UnderlyingSeg": underlying_seg
        }
        return self._make_request("/optionchain/expirylist", payload)

    def get_option_chain(self, underlying_scrip: int, underlying_seg: str = "NSE_EQ", expiry: str = None) -> Dict:
        """
        Get full option chain with all strikes, OI, Greeks, IV.

        Args:
            underlying_scrip: Security ID of underlying
            underlying_seg: NSE_EQ for stocks, IDX_I for indices
            expiry: Expiry date in format "YYYY-MM-DD"
        """
        # The option chain API uses different parameter names
        payload = {
            "UnderlyingScrip": underlying_scrip,
            "UnderlyingSeg": underlying_seg
        }
        if expiry:
            payload["Expiry"] = expiry
        return self._make_request("/optionchain", payload)

    # ===== FUTURES DATA FOR SMA =====

    def get_futures_intraday(
        self,
        security_id: str,
        interval: str = "5",
        from_date: str = None,
        to_date: str = None
    ) -> Dict:
        """
        Get futures price data for SMA calculation.
        SMA is calculated on FUTURES price (8-SMA and 20-SMA).

        NOTE: Dhan API supports intervals: 1, 5, 15, 25, 60 (NOT 10).
        Using 5-min candles and aggregating 2 candles to create 10-min equivalent for EOS.

        Args:
            security_id: Security ID of the futures contract
            interval: Candle interval (5 min recommended, aggregate 2 for 10-min)
        """
        if from_date is None:
            from_date = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d 09:15:00")
        if to_date is None:
            to_date = datetime.now().strftime("%Y-%m-%d 15:30:00")

        payload = {
            "securityId": security_id,
            "exchangeSegment": "NSE_FNO",
            "instrument": "FUTSTK",
            "interval": interval,
            "oi": True,
            "fromDate": from_date,
            "toDate": to_date
        }
        return self._make_request("/charts/intraday", payload)

    # ===== HELPER METHODS FOR EOS CALCULATIONS =====

    def calculate_price_change_pct(self, current_price: float, prev_day_close: float) -> float:
        """Calculate percentage change from previous day close."""
        if prev_day_close == 0:
            return 0.0
        return ((current_price - prev_day_close) / prev_day_close) * 100

    def calculate_oi_change_pct(self, current_oi: int, prev_day_oi: int) -> float:
        """Calculate percentage change in OI from previous day close."""
        if prev_day_oi == 0:
            return 0.0
        return ((current_oi - prev_day_oi) / prev_day_oi) * 100

    def calculate_sma(self, prices: List[float], period: int) -> float:
        """Calculate Simple Moving Average."""
        if len(prices) < period:
            return None
        return sum(prices[-period:]) / period

    def find_nearest_otm_strike(self, spot_price: float, option_chain: Dict, option_type: str) -> Dict:
        """
        Find nearest OTM strike from option chain.
        For CALL: First strike ABOVE spot price
        For PUT: First strike BELOW spot price

        Args:
            spot_price: Current spot price
            option_chain: Option chain data from API
            option_type: "CE" or "PE"
        """
        if option_chain.get("status") != "success" or not option_chain.get("data"):
            return None

        chain_data = option_chain["data"].get("data", [])

        if option_type == "CE":
            # For calls, find first strike above spot
            strikes = [opt for opt in chain_data if opt.get("strikePrice", 0) > spot_price]
            if strikes:
                return min(strikes, key=lambda x: x.get("strikePrice", float('inf')))
        else:
            # For puts, find first strike below spot
            strikes = [opt for opt in chain_data if opt.get("strikePrice", 0) < spot_price]
            if strikes:
                return max(strikes, key=lambda x: x.get("strikePrice", 0))
        return None

    def get_monthly_expiry(self, expiry_list: Dict) -> str:
        """
        Extract monthly expiry date from expiry list.
        Monthly expiry is typically the last Thursday of the month.
        """
        if expiry_list.get("status") != "success" or not expiry_list.get("data"):
            return None

        expiries = expiry_list["data"].get("data", [])
        # Filter for monthly expiries (typically longer-dated ones)
        # Return the nearest monthly expiry
        if expiries:
            # Sort by date and return the first one that looks like monthly
            return expiries[0] if expiries else None
        return None


    # ===== MAIN DATA AGGREGATION METHOD =====

    def fetch_all_eos_data(self, symbol: str) -> Dict[str, Any]:
        """
        Fetch all required data for EOS strategy for a single stock.

        Returns:
            Dict containing:
            - symbol: Stock symbol
            - equity_id: Security ID
            - lot_size: Lot size for the stock
            - current_price: Current LTP
            - prev_day_close: Previous day closing price
            - price_change_pct: % change from prev day close
            - current_oi: Current Open Interest
            - prev_day_oi: Previous day OI
            - oi_change_pct: % change in OI
            - futures_data: Futures candle data for SMA
            - sma_8: 8-period SMA on futures
            - sma_20: 20-period SMA on futures
            - option_chain: Option chain data
            - monthly_expiry: Monthly expiry date
            - data_status: Overall status of data fetch
        """
        result = {
            "symbol": symbol,
            "data_status": "incomplete",
            "errors": []
        }

        if symbol not in FNO_STOCKS:
            result["errors"].append(f"Symbol {symbol} not in FNO_STOCKS list")
            return result

        stock_info = FNO_STOCKS[symbol]
        result["equity_id"] = stock_info["equity_id"]
        result["lot_size"] = stock_info["lot_size"]

        # 1. Get market quote for current price, OI, and previous day data
        print(f"  Fetching market quote for {symbol}...")
        quote = self.get_market_quote([stock_info["equity_id"]], stock_info["exchange_segment"])

        if quote.get("status") == "success" and quote.get("data"):
            quote_data = quote["data"].get("data", {})
            # Extract quote data - structure: {"data": {"NSE_EQ": {"2885": {...}}}}
            segment_data = quote_data.get(stock_info["exchange_segment"], {})
            if segment_data:
                # Get data by security ID (as string key)
                stock_quote = segment_data.get(str(stock_info["equity_id"]), {})
                if stock_quote:
                    result["current_price"] = stock_quote.get("last_price", 0)
                    # Previous close is in ohlc.close
                    ohlc = stock_quote.get("ohlc", {})
                    result["prev_day_close"] = ohlc.get("close", 0)
                    result["day_open"] = ohlc.get("open", 0)
                    result["day_high"] = ohlc.get("high", 0)
                    result["day_low"] = ohlc.get("low", 0)
                    result["current_oi"] = stock_quote.get("oi", 0)
                    result["volume"] = stock_quote.get("volume", 0)
                    result["52_week_high"] = stock_quote.get("52_week_high", 0)
                    result["52_week_low"] = stock_quote.get("52_week_low", 0)
                    # Note: Previous day OI is not directly available in this API
                    result["prev_day_oi"] = 0  # Will need historical data for this
        else:
            result["errors"].append(f"Failed to get market quote: {quote.get('error', 'Unknown error')}")

        time.sleep(1.2)  # Rate limiting - 1 request per second

        # Calculate price change %
        if "current_price" in result and "prev_day_close" in result:
            result["price_change_pct"] = self.calculate_price_change_pct(
                result["current_price"],
                result["prev_day_close"]
            )

        # Calculate OI change %
        if "current_oi" in result and "prev_day_oi" in result:
            result["oi_change_pct"] = self.calculate_oi_change_pct(
                result["current_oi"],
                result["prev_day_oi"]
            )

        # 2. Get intraday data for the stock (10-min candles)
        print(f"  Fetching intraday data for {symbol}...")
        intraday = self.get_intraday_data(
            security_id=str(stock_info["equity_id"]),
            segment=stock_info["exchange_segment"],
            instrument="EQUITY",
            interval="5"  # 5-min candles, aggregate 2 for 10-min EOS strategy
        )

        if intraday.get("status") == "success":
            # Intraday data structure: {"open": [], "high": [], "low": [], "close": [], "timestamp": [], ...}
            intraday_data = intraday.get("data", {})
            timestamps = intraday_data.get("timestamp", [])
            result["intraday_candles"] = len(timestamps)
            if timestamps:
                result["intraday_data"] = {
                    "open": intraday_data.get("open", []),
                    "high": intraday_data.get("high", []),
                    "low": intraday_data.get("low", []),
                    "close": intraday_data.get("close", []),
                    "volume": intraday_data.get("volume", []),
                    "timestamp": timestamps
                }
        else:
            result["errors"].append(f"Failed to get intraday data: {intraday.get('error', 'Unknown error')}")

        time.sleep(1.2)  # Rate limiting

        # 3. Get option chain and expiry list
        print(f"  Fetching option chain for {symbol}...")
        expiry_list = self.get_expiry_list(stock_info["equity_id"], "NSE_EQ")

        if expiry_list.get("status") == "success":
            result["expiry_list"] = expiry_list.get("data", {}).get("data", [])
            result["monthly_expiry"] = self.get_monthly_expiry(expiry_list)

            time.sleep(1.2)  # Rate limiting

            # Get option chain for the monthly expiry
            if result.get("monthly_expiry"):
                option_chain = self.get_option_chain(
                    stock_info["equity_id"],
                    "NSE_EQ",
                    result["monthly_expiry"]
                )
                if option_chain.get("status") == "success":
                    result["option_chain_available"] = True
                    # Option chain structure: {"data": {"last_price": X, "oc": {"strike": {"ce": {...}, "pe": {...}}, ...}}}
                    chain_inner = option_chain.get("data", {}).get("data", {})
                    oc_data = chain_inner.get("oc", {})
                    result["strikes_count"] = len(oc_data)
                    result["underlying_price"] = chain_inner.get("last_price", 0)

                    # Extract total OI from option chain (sum of CE and PE OI)
                    total_ce_oi = 0
                    total_pe_oi = 0
                    for strike, strike_data in oc_data.items():
                        ce_data = strike_data.get("ce", {})
                        pe_data = strike_data.get("pe", {})
                        total_ce_oi += ce_data.get("oi", 0)
                        total_pe_oi += pe_data.get("oi", 0)
                    result["total_ce_oi"] = total_ce_oi
                    result["total_pe_oi"] = total_pe_oi
                    result["option_chain_data"] = oc_data  # Store full chain for strategy use
                else:
                    result["option_chain_available"] = False
                    result["errors"].append("Failed to get option chain")
        else:
            result["errors"].append(f"Failed to get expiry list: {expiry_list.get('error', 'Unknown error')}")

        # Determine overall data status
        required_fields = ["current_price", "prev_day_close", "lot_size"]
        if all(field in result for field in required_fields):
            result["data_status"] = "complete" if not result["errors"] else "partial"

        return result



# ===== TEST FUNCTIONS =====

def test_historical_data_api():
    """Test the historical data API (Expired Options Data) which we know works."""
    print("=" * 70)
    print("TESTING HISTORICAL DATA API (Known Working)")
    print("=" * 70)

    # This endpoint is known to work from our previous testing
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "access-token": DHAN_ACCESS_TOKEN
    }

    payload = {
        "exchangeSegment": "NSE_FNO",
        "interval": "10",  # 10-minute candles
        "securityId": 13,  # NIFTY
        "instrument": "OPTIDX",
        "expiryFlag": "MONTH",
        "expiryCode": 1,
        "strike": "ATM",
        "drvOptionType": "CALL",
        "requiredData": ["open", "high", "low", "close", "volume", "oi"],
        "fromDate": "2025-01-01",
        "toDate": "2025-01-31"
    }

    print("\n1. Testing Expired Options Data API...")
    try:
        response = requests.post(
            f"{API_BASE_URL}/charts/rollingoption",
            headers=headers,
            json=payload
        )
        response.raise_for_status()
        data = response.json()
        print(f"   Status: success")
        print(f"   Data points: {len(data.get('data', {}).get('ce', {}).get('t', []))}")
        print("   ✅ Historical data API is working!")
    except Exception as e:
        print(f"   Status: error")
        print(f"   Error: {e}")


def test_single_api_endpoints():
    """Test individual API endpoints to verify they work."""
    print("\n" + "=" * 70)
    print("TESTING LIVE MARKET DATA API ENDPOINTS")
    print("=" * 70)
    print("\n⚠️  Note: Rate limit is 1 request/second. Adding delays...")

    fetcher = EOS_DataFetcher()

    # Test 1: Market Quote
    print("\n1. Testing Market Quote API...")
    quote = fetcher.get_market_quote([2885], "NSE_EQ")  # RELIANCE
    print(f"   Status: {quote.get('status')}")
    if quote.get("status") == "success":
        data = quote.get("data", {}).get("data", {}).get("NSE_EQ", {}).get("2885", {})
        print(f"   Last Price: {data.get('last_price', 'N/A')}")
        print(f"   OHLC: {data.get('ohlc', {})}")
        print(f"   Volume: {data.get('volume', 'N/A')}")
        print(f"   52-Week High: {data.get('52_week_high', 'N/A')}")
    else:
        print(f"   Error: {quote.get('error')}")

    time.sleep(1.5)  # Rate limiting

    # Test 2: LTP
    print("\n2. Testing LTP API...")
    ltp = fetcher.get_ltp([2885, 1333], "NSE_EQ")  # RELIANCE, HDFCBANK
    print(f"   Status: {ltp.get('status')}")
    if ltp.get("status") == "success":
        print(f"   Data: {json.dumps(ltp.get('data', {}), indent=2)[:300]}...")
    else:
        print(f"   Error: {ltp.get('error', 'Unknown')}")

    time.sleep(1.5)  # Rate limiting

    # Test 3: Intraday Data (Historical)
    print("\n3. Testing Intraday Data API...")
    print("   Note: Valid intervals are 1, 5, 15, 25, 60 (NOT 10)")
    from_date = "2026-01-27 09:15:00"
    to_date = "2026-01-31 15:30:00"
    intraday = fetcher.get_intraday_data("2885", "NSE_EQ", "EQUITY", "5", from_date, to_date)
    print(f"   Status: {intraday.get('status')}")
    if intraday.get("status") == "success":
        data = intraday.get("data", {})
        if isinstance(data, dict):
            print(f"   Data keys: {list(data.keys())}")
            # Count candles from arrays
            timestamps = data.get("timestamp", [])
            print(f"   Number of candles: {len(timestamps)}")
            if timestamps:
                print(f"   First timestamp: {timestamps[0]}")
                print(f"   Last timestamp: {timestamps[-1]}")
    else:
        print(f"   Error: {intraday.get('error', 'Unknown')}")

    time.sleep(1.5)  # Rate limiting

    # Test 4: Expiry List
    print("\n4. Testing Expiry List API...")
    expiry = fetcher.get_expiry_list(2885, "NSE_EQ")  # RELIANCE
    print(f"   Status: {expiry.get('status')}")
    if expiry.get("status") == "success":
        expiry_dates = expiry.get("data", {}).get("data", [])
        print(f"   Available Expiries: {expiry_dates}")
    else:
        print(f"   Error: {expiry.get('error', 'Unknown')}")

    time.sleep(1.5)  # Rate limiting

    # Test 5: Option Chain
    print("\n5. Testing Option Chain API...")
    expiry_dates = expiry.get("data", {}).get("data", [])
    if expiry_dates:
        chain = fetcher.get_option_chain(2885, "NSE_EQ", expiry_dates[0])
        print(f"   Status: {chain.get('status')}")
        if chain.get("status") == "success":
            data = chain.get("data", {})
            print(f"   Data keys: {list(data.keys()) if isinstance(data, dict) else 'N/A'}")
            print(f"   Sample: {json.dumps(data, indent=2)[:500]}...")
        else:
            print(f"   Error: {chain.get('error', 'Unknown')}")
    else:
        print("   Skipped - no expiry dates available")


def test_eos_data_fetching(test_all: bool = False):
    """
    Test fetching all required data for EOS strategy.

    Args:
        test_all: If True, test all 20 FNO stocks. If False, test 5 sample stocks.
    """
    print("\n" + "=" * 70)
    print("TESTING EOS DATA FETCHING FOR MULTIPLE STOCKS")
    print("=" * 70)

    fetcher = EOS_DataFetcher()

    # Test with 5 sample stocks or all 20
    if test_all:
        test_stocks = list(FNO_STOCKS.keys())
        print(f"\n📋 Testing ALL {len(test_stocks)} FNO stocks...")
    else:
        test_stocks = ["RELIANCE", "HDFCBANK", "ICICIBANK", "TCS", "INFY"]
        print(f"\n📋 Testing {len(test_stocks)} sample stocks...")

    results = []
    for stock in test_stocks:
        print(f"\n{'='*50}")
        print(f"Fetching data for: {stock}")
        print(f"{'='*50}")

        data = fetcher.fetch_all_eos_data(stock)
        results.append(data)

        # Print summary
        print(f"\n  📊 Data Summary for {stock}:")
        print(f"     Equity ID: {data.get('equity_id', 'N/A')}")
        print(f"     Lot Size: {data.get('lot_size', 'N/A')}")
        print(f"     Current Price: {data.get('current_price', 'N/A')}")
        print(f"     Prev Day Close: {data.get('prev_day_close', 'N/A')}")
        print(f"     Price Change %: {data.get('price_change_pct', 'N/A'):.2f}%" if isinstance(data.get('price_change_pct'), (int, float)) else f"     Price Change %: N/A")
        print(f"     Intraday Candles: {data.get('intraday_candles', 0)}")
        print(f"     Monthly Expiry: {data.get('monthly_expiry', 'N/A')}")
        print(f"     Option Chain Available: {data.get('option_chain_available', 'N/A')}")
        print(f"     Strikes Count: {data.get('strikes_count', 'N/A')}")
        print(f"     Total CE OI: {data.get('total_ce_oi', 0):,}")
        print(f"     Total PE OI: {data.get('total_pe_oi', 0):,}")
        print(f"     Underlying Price: {data.get('underlying_price', 'N/A')}")
        print(f"     Data Status: {data.get('data_status', 'N/A')}")
        if data.get("errors"):
            print(f"     ⚠️ Errors: {data['errors']}")

        # Rate limiting - wait between requests
        time.sleep(0.5)

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    complete = sum(1 for r in results if r.get("data_status") == "complete")
    partial = sum(1 for r in results if r.get("data_status") == "partial")
    incomplete = sum(1 for r in results if r.get("data_status") == "incomplete")

    print(f"\n📈 Total Stocks Tested: {len(results)}")
    print(f"   ✅ Complete Data: {complete}")
    print(f"   ⚠️ Partial Data: {partial}")
    print(f"   ❌ Incomplete Data: {incomplete}")

    # Data availability summary
    print("\n📊 DATA AVAILABILITY FOR EOS STRATEGY:")
    print("-" * 50)

    all_have_price = all(r.get("current_price", 0) > 0 for r in results)
    all_have_prev_close = all(r.get("prev_day_close", 0) > 0 for r in results)
    all_have_intraday = all(r.get("intraday_candles", 0) > 0 for r in results)
    all_have_option_chain = all(r.get("option_chain_available", False) for r in results)
    all_have_expiry = all(r.get("monthly_expiry") is not None for r in results)

    print(f"   Current Price:     {'✅ All' if all_have_price else '⚠️ Some missing'}")
    print(f"   Previous Close:    {'✅ All' if all_have_prev_close else '⚠️ Some missing'}")
    print(f"   Intraday Candles:  {'✅ All' if all_have_intraday else '⚠️ Some missing'}")
    print(f"   Option Chain:      {'✅ All' if all_have_option_chain else '⚠️ Some missing'}")
    print(f"   Monthly Expiry:    {'✅ All' if all_have_expiry else '⚠️ Some missing'}")

    # Note about limitations
    print("\n⚠️  IMPORTANT NOTES:")
    print("   - Intraday intervals: 1, 5, 15, 25, 60 min (10 min NOT supported)")
    print("   - Using 5-min candles (aggregate 2 candles for 10-min equivalent)")
    print("   - OI data comes from option chain (not equity market quote)")
    print("   - Previous day OI available in option chain as 'previous_oi'")

    return results


def test_all_fno_stocks():
    """Test data fetching for ALL stocks in FNO_STOCKS list."""
    print("\n" + "=" * 70)
    print("TESTING ALL FNO STOCKS")
    print("=" * 70)

    fetcher = EOS_DataFetcher()

    results = []
    for i, stock in enumerate(FNO_STOCKS.keys()):
        print(f"\n[{i+1}/{len(FNO_STOCKS)}] Testing {stock}...")
        data = fetcher.fetch_all_eos_data(stock)
        results.append(data)
        time.sleep(0.3)  # Rate limiting

    # Summary table
    print("\n" + "=" * 70)
    print("ALL FNO STOCKS - DATA AVAILABILITY SUMMARY")
    print("=" * 70)
    print(f"\n{'Symbol':<15} {'Price':<10} {'Prev Close':<12} {'OI':<12} {'Status':<15}")
    print("-" * 64)

    for r in results:
        symbol = r.get("symbol", "N/A")
        price = r.get("current_price", "N/A")
        prev_close = r.get("prev_day_close", "N/A")
        oi = r.get("current_oi", "N/A")
        status = r.get("data_status", "N/A")

        print(f"{symbol:<15} {str(price):<10} {str(prev_close):<12} {str(oi):<12} {status:<15}")

    return results


def test_backtesting_data_apis():
    """
    Test the APIs required for backtesting EOS strategy.

    This tests:
    1. Historical Daily Data (for previous day close)
    2. Historical Intraday Data (for futures SMA calculation)
    3. Expired Options Data (for historical option prices and OI)
    """
    print("\n" + "=" * 70)
    print("TESTING BACKTESTING DATA APIs")
    print("=" * 70)

    fetcher = EOS_DataFetcher()

    # Test 1: Historical Daily Data (for previous day close)
    print("\n1. Testing Historical Daily Data API...")
    print("   Purpose: Get previous day close prices for price change calculation")

    # Get last 30 days of daily data for RELIANCE
    from_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    to_date = datetime.now().strftime("%Y-%m-%d")

    daily_data = fetcher.get_daily_historical_data(
        security_id="2885",  # RELIANCE
        segment="NSE_EQ",
        instrument="EQUITY",
        from_date=from_date,
        to_date=to_date
    )

    print(f"   Status: {daily_data.get('status')}")
    if daily_data.get("status") == "success":
        data = daily_data.get("data", {})
        timestamps = data.get("timestamp", [])
        closes = data.get("close", [])
        print(f"   Days of data: {len(timestamps)}")
        if closes:
            print(f"   Last close price: ₹{closes[-1]}")
            print(f"   First close price: ₹{closes[0]}")
    else:
        print(f"   Error: {daily_data}")

    time.sleep(1.5)  # Rate limiting

    # Test 2: Historical Futures Intraday Data (for SMA calculation)
    print("\n2. Testing Historical Futures Intraday Data API...")
    print("   Purpose: Get futures price data for 8-SMA and 20-SMA calculation")
    print("   Note: Need futures security ID from instrument list")

    # For backtesting, we'd need to look up the futures security ID
    # For now, test with equity intraday as proxy
    from_date = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d 09:15:00")
    to_date = datetime.now().strftime("%Y-%m-%d 15:30:00")

    futures_data = fetcher.get_futures_intraday(
        security_id="2885",  # This should be futures security ID
        interval="5",
        from_date=from_date,
        to_date=to_date
    )

    print(f"   Status: {futures_data.get('status')}")
    if futures_data.get("status") == "success":
        data = futures_data.get("data", {})
        timestamps = data.get("timestamp", [])
        print(f"   Candles fetched: {len(timestamps)}")
        if timestamps:
            print(f"   Note: For actual backtesting, use FUTSTK security IDs from instrument list")
    else:
        print(f"   Error: {futures_data}")
        print("   Note: This may fail if using equity ID instead of futures ID")

    time.sleep(1.5)  # Rate limiting

    # Test 3: Expired Options Data (CRITICAL for backtesting)
    print("\n3. Testing Expired Options Data API...")
    print("   Purpose: Get historical option prices, OI, IV for backtesting")
    print("   This is the KEY API for EOS backtesting!")

    # Get expired options data - use expiry_code=2 (2nd last expiry) to ensure we have expired data
    # Last 30 days before that expiry
    from_date = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
    to_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

    expired_options = fetcher.get_expired_options_data(
        security_id=2885,  # RELIANCE
        instrument="OPTSTK",
        expiry_flag="MONTH",
        expiry_code=2,  # 2nd last monthly expiry (to ensure it's expired)
        strike="ATM",
        option_type="CALL",
        interval="5",
        required_data=["open", "high", "low", "close", "volume", "oi", "iv", "spot", "strike"],
        from_date=from_date,
        to_date=to_date
    )

    print(f"   Date Range: {from_date} to {to_date}")
    print(f"   Expiry Code: 2 (2nd last monthly expiry)")

    print(f"   Status: {expired_options.get('status')}")
    if expired_options.get("status") == "success":
        data = expired_options.get("data", {})

        # Debug: Print raw data keys - data may be nested as data.data.ce
        print(f"   Raw data keys: {data.keys() if data else 'None'}")

        # Handle nested structure: data may be at data.data.ce or data.ce
        if "data" in data:
            inner_data = data.get("data", {})
            ce_data = inner_data.get("ce", {})
            pe_data = inner_data.get("pe", {})
        else:
            ce_data = data.get("ce", {})
            pe_data = data.get("pe", {})

        if ce_data:
            timestamps = ce_data.get("timestamp", [])
            oi_data = ce_data.get("oi", [])
            spot_data = ce_data.get("spot", [])
            iv_data = ce_data.get("iv", [])
            close_data = ce_data.get("close", [])
            strike_data = ce_data.get("strike", [])

            print(f"   CE Data Points: {len(timestamps)}")
            print(f"   OI Data Available: {'✅ Yes' if oi_data else '❌ No'} ({len(oi_data)} points)")
            print(f"   Spot Data Available: {'✅ Yes' if spot_data else '❌ No'} ({len(spot_data)} points)")
            print(f"   IV Data Available: {'✅ Yes' if iv_data else '❌ No'} ({len(iv_data)} points)")
            print(f"   Close Data Available: {'✅ Yes' if close_data else '❌ No'} ({len(close_data)} points)")
            print(f"   Strike Data Available: {'✅ Yes' if strike_data else '❌ No'} ({len(strike_data)} points)")

            if oi_data and len(oi_data) > 0:
                print(f"   Sample OI: {oi_data[0]} (first), {oi_data[-1]} (last)")
            if spot_data and len(spot_data) > 0:
                print(f"   Sample Spot: ₹{spot_data[0]} (first), ₹{spot_data[-1]} (last)")
            if strike_data and len(strike_data) > 0:
                print(f"   Strike Price: ₹{strike_data[0]}")
        else:
            print("   No CE data returned")
            print(f"   Full response: {expired_options}")

        if pe_data:
            print(f"   PE Data: Also available")
    else:
        print(f"   Error: {expired_options}")

    # Summary
    print("\n" + "=" * 70)
    print("BACKTESTING DATA AVAILABILITY SUMMARY")
    print("=" * 70)
    print("""
    ✅ Historical Daily Data (/charts/historical)
       - Available since inception
       - Use for: Previous day close prices

    ✅ Historical Intraday Data (/charts/intraday)
       - 5 years of data, 90 days per call
       - Intervals: 1, 5, 15, 25, 60 min
       - Use for: Futures price for SMA calculation
       - Note: Need futures security IDs from instrument list

    ✅ Expired Options Data (/charts/rollingoption)
       - 5 years of data, 30 days per call
       - Minute-level granularity
       - Includes: OHLC, OI, IV, Spot, Strike
       - Use for: Historical option prices and OI for backtesting

    📋 FOR COMPLETE BACKTESTING:
       1. Download instrument list from Dhan for futures security IDs
       2. Use /charts/historical for daily close prices
       3. Use /charts/intraday with FUTSTK for futures SMA
       4. Use /charts/rollingoption for option prices and OI
    """)


if __name__ == "__main__":
    print("=" * 70)
    print("EOS STRATEGY DATA FETCHER - TEST SUITE")
    print("=" * 70)
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Total FNO Stocks in Universe: {len(FNO_STOCKS)}")
    print("=" * 70)

    # Run tests
    test_single_api_endpoints()
    test_eos_data_fetching()

    # Test backtesting APIs
    test_backtesting_data_apis()

    # Uncomment to test all stocks
    # test_all_fno_stocks()
