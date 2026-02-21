# Last updated: 2026-02-21
"""
EOS Option Chain Manager
Handles option chain fetching, ATM strike identification, and option price management.
Uses Dhan Option Chain API for real option data.
Uses api-scrip-master.csv for option security IDs (for WebSocket subscription).
"""

import requests
import time
import csv
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass

from .config import (
    DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN, API_BASE_URL,
    FNO_STOCKS
)

# Path to the scrip master CSV file
SCRIP_MASTER_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "api-scrip-master.csv")


@dataclass
class OptionData:
    """Data class for option instrument."""
    symbol: str
    strike_price: float
    option_type: str  # "CE" or "PE"
    security_id: int
    ltp: float
    oi: int
    prev_close: float
    prev_oi: int
    volume: int
    bid_price: float
    ask_price: float
    iv: float
    delta: float
    expiry: str


@dataclass
class ATMOption:
    """ATM option pair for a stock."""
    symbol: str
    spot_price: float
    atm_strike: float
    call: Optional[OptionData]
    put: Optional[OptionData]
    expiry: str


class EOSOptionChainManager:
    """
    Manages option chain data for EOS strategy.
    Fetches real option prices and identifies ATM strikes.
    Loads option security IDs from scrip master CSV for WebSocket subscription.
    """

    def __init__(self, load_scrip_master: bool = True):
        self.headers = {
            "Content-Type": "application/json",
            "access-token": DHAN_ACCESS_TOKEN,
            "client-id": DHAN_CLIENT_ID
        }
        self.rate_limit_delay = 3.0  # Option chain API: 1 request per 3 seconds
        self.last_request_time = 0

        # Cache for option chain data
        self._option_cache: Dict[str, Dict] = {}  # symbol -> option chain data
        self._atm_options: Dict[str, ATMOption] = {}  # symbol -> ATM option pair
        self._expiry_cache: Dict[str, List[str]] = {}  # symbol -> list of expiries

        # Option security ID lookup from scrip master CSV
        # Key: (symbol, strike, option_type, expiry_date) -> security_id
        self._option_security_ids: Dict[Tuple[str, float, str, str], int] = {}
        self._scrip_master_loaded = False

        if load_scrip_master:
            self._load_scrip_master()

    def _rate_limit(self):
        """Enforce rate limiting between API calls."""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.rate_limit_delay:
            time.sleep(self.rate_limit_delay - elapsed)
        self.last_request_time = time.time()

    def _load_scrip_master(self) -> bool:
        """
        Load option security IDs from api-scrip-master.csv.

        CSV Structure:
        - Column 0 (SEM_EXM_EXCH_ID): Exchange (NSE, BSE)
        - Column 1 (SEM_SEGMENT): Segment (D for derivatives)
        - Column 2 (SEM_SMST_SECURITY_ID): Security ID for WebSocket
        - Column 3 (SEM_INSTRUMENT_NAME): Instrument type (OPTSTK, FUTIDX, etc.)
        - Column 5 (SEM_TRADING_SYMBOL): Trading symbol (e.g., RELIANCE-Feb2026-1440-CE)
        - Column 8 (SEM_EXPIRY_DATE): Expiry date (e.g., 2026-02-24 14:30:00)
        - Column 9 (SEM_STRIKE_PRICE): Strike price (e.g., 1440.00000)
        - Column 10 (SEM_OPTION_TYPE): Option type (CE or PE)
        - Column 15 (SM_SYMBOL_NAME): Underlying symbol name
        """
        if self._scrip_master_loaded:
            return True

        if not os.path.exists(SCRIP_MASTER_PATH):
            print(f"[OptionChain] Scrip master file not found: {SCRIP_MASTER_PATH}")
            return False

        print(f"[OptionChain] Loading scrip master from {SCRIP_MASTER_PATH}...")
        start_time = time.time()
        count = 0

        try:
            with open(SCRIP_MASTER_PATH, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                next(reader)  # Skip header row

                for row in reader:
                    if len(row) < 16:
                        continue

                    exchange = row[0]
                    segment = row[1]
                    instrument_type = row[3]

                    # Only process NSE stock options (OPTSTK)
                    if exchange != "NSE" or segment != "D" or instrument_type != "OPTSTK":
                        continue

                    try:
                        security_id = int(row[2])
                        trading_symbol = row[5]  # e.g., RELIANCE-Feb2026-1440-CE
                        expiry_str = row[8]  # e.g., 2026-02-24 14:30:00
                        strike_price = float(row[9])
                        option_type = row[10]  # CE or PE

                        # Extract underlying symbol from trading symbol
                        # Format: SYMBOL-MonthYear-Strike-CE/PE (e.g., RELIANCE-Feb2026-1440-CE)
                        # The symbol is everything before the first dash followed by month
                        parts = trading_symbol.split("-")
                        if len(parts) < 4:
                            continue
                        underlying = parts[0]  # e.g., RELIANCE

                        # Parse expiry date to YYYY-MM-DD format
                        expiry_date = expiry_str.split(" ")[0]  # Get just the date part

                        # Create lookup key: (symbol, strike, option_type, expiry)
                        key = (underlying, strike_price, option_type, expiry_date)
                        self._option_security_ids[key] = security_id
                        count += 1

                    except (ValueError, IndexError) as e:
                        continue  # Skip malformed rows

            self._scrip_master_loaded = True
            elapsed = time.time() - start_time
            print(f"[OptionChain] Loaded {count:,} option security IDs in {elapsed:.2f}s")
            return True

        except Exception as e:
            print(f"[OptionChain] Error loading scrip master: {e}")
            return False

    def get_option_security_id(self, symbol: str, strike: float,
                                option_type: str, expiry: str) -> Optional[int]:
        """
        Get the security ID for an option instrument.

        Args:
            symbol: Underlying symbol (e.g., "RELIANCE")
            strike: Strike price (e.g., 1440.0)
            option_type: "CE" or "PE"
            expiry: Expiry date in YYYY-MM-DD format

        Returns:
            Security ID for WebSocket subscription, or None if not found
        """
        if not self._scrip_master_loaded:
            self._load_scrip_master()

        # Normalize option type
        opt_type = "CE" if option_type.upper() in ["CE", "CALL"] else "PE"

        # Try exact match
        key = (symbol, strike, opt_type, expiry)
        security_id = self._option_security_ids.get(key)

        if security_id:
            return security_id

        # Try with different strike formatting (handle floating point precision)
        for cached_key, sec_id in self._option_security_ids.items():
            if (cached_key[0] == symbol and
                abs(cached_key[1] - strike) < 0.01 and
                cached_key[2] == opt_type and
                cached_key[3] == expiry):
                return sec_id

        return None

    def get_atm_security_ids(self, symbol: str, spot_price: float = None,
                             expiry: str = None) -> Dict[str, int]:
        """
        Get security IDs for ATM call and put options.

        Args:
            symbol: Underlying symbol
            spot_price: Current spot price (optional)
            expiry: Expiry date (optional, uses nearest monthly if not provided)

        Returns:
            Dict with 'call' and 'put' security IDs
        """
        result = {"call": None, "put": None}

        # Get ATM options (fetches from API if needed)
        atm = self.get_atm_options(symbol, spot_price=spot_price)
        if not atm:
            return result

        if not expiry:
            expiry = atm.expiry

        # Get security IDs
        if atm.call:
            result["call"] = self.get_option_security_id(
                symbol, atm.atm_strike, "CE", expiry
            )
            if result["call"]:
                atm.call.security_id = result["call"]

        if atm.put:
            result["put"] = self.get_option_security_id(
                symbol, atm.atm_strike, "PE", expiry
            )
            if result["put"]:
                atm.put.security_id = result["put"]

        return result

    def _make_request(self, endpoint: str, payload: Dict) -> Dict:
        """Make API request with error handling and rate limiting."""
        self._rate_limit()
        try:
            response = requests.post(
                f"{API_BASE_URL}{endpoint}",
                headers=self.headers,
                json=payload,
                timeout=30
            )
            if response.status_code == 200:
                return {"status": "success", "data": response.json()}
            else:
                return {"status": "error", "error": f"{response.status_code}: {response.text}"}
        except requests.exceptions.RequestException as e:
            return {"status": "error", "error": str(e)}

    def get_expiry_list(self, symbol: str) -> List[str]:
        """Get list of available expiry dates for a stock's options."""
        if symbol in self._expiry_cache:
            return self._expiry_cache[symbol]

        stock_info = FNO_STOCKS.get(symbol)
        if not stock_info:
            print(f"[OptionChain] Symbol {symbol} not found in FNO_STOCKS")
            return []

        payload = {
            "UnderlyingScrip": stock_info["equity_id"],
            "UnderlyingSeg": "NSE_EQ"
        }

        result = self._make_request("/optionchain/expirylist", payload)

        if result["status"] == "success" and "data" in result["data"]:
            expiries = result["data"]["data"]
            self._expiry_cache[symbol] = expiries
            return expiries
        else:
            print(f"[OptionChain] Failed to get expiry list for {symbol}: {result.get('error', 'Unknown error')}")
            return []

    def get_nearest_monthly_expiry(self, symbol: str) -> Optional[str]:
        """Get the nearest monthly expiry date (last Thursday of month)."""
        expiries = self.get_expiry_list(symbol)
        if not expiries:
            return None

        # Monthly expiries are typically the last Thursday of each month
        # They are usually further apart than weekly expiries
        today = datetime.now().date()

        for expiry in sorted(expiries):
            expiry_date = datetime.strptime(expiry, "%Y-%m-%d").date()
            if expiry_date >= today:
                # Check if it's likely a monthly expiry (day > 20)
                if expiry_date.day >= 20:
                    return expiry

        # Fallback to first available expiry
        return expiries[0] if expiries else None

    def fetch_option_chain(self, symbol: str, expiry: str = None) -> Dict:
        """
        Fetch full option chain for a symbol.

        Args:
            symbol: Stock symbol (e.g., "RELIANCE")
            expiry: Expiry date in YYYY-MM-DD format (default: nearest monthly)

        Returns:
            Option chain data with all strikes
        """
        stock_info = FNO_STOCKS.get(symbol)
        if not stock_info:
            print(f"[OptionChain] Symbol {symbol} not found in FNO_STOCKS")
            return {}

        if not expiry:
            expiry = self.get_nearest_monthly_expiry(symbol)
            if not expiry:
                print(f"[OptionChain] No expiry found for {symbol}")
                return {}

        payload = {
            "UnderlyingScrip": stock_info["equity_id"],
            "UnderlyingSeg": "NSE_EQ",
            "Expiry": expiry
        }

        print(f"[OptionChain] Fetching option chain for {symbol}, expiry: {expiry}")
        result = self._make_request("/optionchain", payload)

        if result["status"] == "success" and "data" in result["data"]:
            chain_data = result["data"]["data"]
            self._option_cache[symbol] = {
                "expiry": expiry,
                "spot_price": chain_data.get("last_price", 0),
                "chain": chain_data.get("oc", {}),
                "timestamp": datetime.now()
            }
            return self._option_cache[symbol]
        else:
            print(f"[OptionChain] Failed to fetch chain for {symbol}: {result.get('error', 'Unknown error')}")
            return {}

    def identify_atm_strike(self, symbol: str, spot_price: float = None) -> Optional[float]:
        """
        Identify the ATM (At-The-Money) strike for a symbol.

        Args:
            symbol: Stock symbol
            spot_price: Current spot price (if not provided, uses cached data)

        Returns:
            ATM strike price
        """
        if symbol not in self._option_cache:
            self.fetch_option_chain(symbol)

        cache = self._option_cache.get(symbol, {})
        if not cache:
            return None

        if spot_price is None:
            spot_price = cache.get("spot_price", 0)

        if spot_price == 0:
            return None

        chain = cache.get("chain", {})
        if not chain:
            return None

        # Find the strike closest to spot price
        strikes = [float(s) for s in chain.keys()]
        if not strikes:
            return None

        atm_strike = min(strikes, key=lambda x: abs(x - spot_price))
        return atm_strike

    def get_atm_options(self, symbol: str, spot_price: float = None,
                        refresh: bool = False) -> Optional[ATMOption]:
        """
        Get ATM call and put options for a symbol.

        Args:
            symbol: Stock symbol
            spot_price: Current spot price
            refresh: Force refresh option chain data

        Returns:
            ATMOption with call and put data
        """
        if refresh or symbol not in self._option_cache:
            self.fetch_option_chain(symbol)

        cache = self._option_cache.get(symbol, {})
        if not cache:
            return None

        if spot_price is None:
            spot_price = cache.get("spot_price", 0)

        atm_strike = self.identify_atm_strike(symbol, spot_price)
        if atm_strike is None:
            return None

        chain = cache.get("chain", {})
        strike_data = chain.get(str(atm_strike)) or chain.get(f"{atm_strike:.6f}")

        if not strike_data:
            # Try to find the strike with different formatting
            for key in chain.keys():
                if abs(float(key) - atm_strike) < 0.01:
                    strike_data = chain[key]
                    break

        if not strike_data:
            print(f"[OptionChain] Strike {atm_strike} not found in chain for {symbol}")
            return None

        call_data = strike_data.get("ce", {})
        put_data = strike_data.get("pe", {})

        call_option = OptionData(
            symbol=symbol,
            strike_price=atm_strike,
            option_type="CE",
            security_id=0,  # Will be fetched from instrument list
            ltp=call_data.get("last_price", 0),
            oi=call_data.get("oi", 0),
            prev_close=call_data.get("previous_close_price", 0),
            prev_oi=call_data.get("previous_oi", 0),
            volume=call_data.get("volume", 0),
            bid_price=call_data.get("top_bid_price", 0),
            ask_price=call_data.get("top_ask_price", 0),
            iv=call_data.get("implied_volatility", 0),
            delta=call_data.get("greeks", {}).get("delta", 0),
            expiry=cache.get("expiry", "")
        ) if call_data else None

        put_option = OptionData(
            symbol=symbol,
            strike_price=atm_strike,
            option_type="PE",
            security_id=0,
            ltp=put_data.get("last_price", 0),
            oi=put_data.get("oi", 0),
            prev_close=put_data.get("previous_close_price", 0),
            prev_oi=put_data.get("previous_oi", 0),
            volume=put_data.get("volume", 0),
            bid_price=put_data.get("top_bid_price", 0),
            ask_price=put_data.get("top_ask_price", 0),
            iv=put_data.get("implied_volatility", 0),
            delta=put_data.get("greeks", {}).get("delta", 0),
            expiry=cache.get("expiry", "")
        ) if put_data else None

        atm_option = ATMOption(
            symbol=symbol,
            spot_price=spot_price,
            atm_strike=atm_strike,
            call=call_option,
            put=put_option,
            expiry=cache.get("expiry", "")
        )

        self._atm_options[symbol] = atm_option
        return atm_option

    def get_option_price(self, symbol: str, option_type: str,
                         refresh: bool = False) -> Optional[float]:
        """
        Get the current price of ATM option.

        Args:
            symbol: Stock symbol
            option_type: "CALL" or "PUT"
            refresh: Force refresh data

        Returns:
            Option LTP or None
        """
        atm = self.get_atm_options(symbol, refresh=refresh)
        if not atm:
            return None

        if option_type.upper() in ["CALL", "CE"]:
            return atm.call.ltp if atm.call else None
        else:
            return atm.put.ltp if atm.put else None

    def get_cached_atm(self, symbol: str) -> Optional[ATMOption]:
        """Get cached ATM option data without API call."""
        return self._atm_options.get(symbol)

    def print_atm_options(self, symbol: str):
        """Print ATM option details for a symbol."""
        atm = self._atm_options.get(symbol)
        if not atm:
            print(f"[OptionChain] No ATM data cached for {symbol}")
            return

        print(f"\n{'='*50}")
        print(f"ATM OPTIONS: {symbol}")
        print(f"{'='*50}")
        print(f"Spot Price: ₹{atm.spot_price:.2f}")
        print(f"ATM Strike: ₹{atm.atm_strike:.2f}")
        print(f"Expiry: {atm.expiry}")

        if atm.call:
            print(f"\nCALL (CE):")
            print(f"  LTP: ₹{atm.call.ltp:.2f}")
            print(f"  Bid/Ask: ₹{atm.call.bid_price:.2f} / ₹{atm.call.ask_price:.2f}")
            print(f"  OI: {atm.call.oi:,}")
            print(f"  IV: {atm.call.iv:.2f}%")
            print(f"  Delta: {atm.call.delta:.4f}")

        if atm.put:
            print(f"\nPUT (PE):")
            print(f"  LTP: ₹{atm.put.ltp:.2f}")
            print(f"  Bid/Ask: ₹{atm.put.bid_price:.2f} / ₹{atm.put.ask_price:.2f}")
            print(f"  OI: {atm.put.oi:,}")
            print(f"  IV: {atm.put.iv:.2f}%")
            print(f"  Delta: {atm.put.delta:.4f}")

        print(f"{'='*50}")
