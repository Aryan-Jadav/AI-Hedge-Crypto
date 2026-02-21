# Last updated: 2026-02-21
"""
EOS Data Fetcher Module
Handles all data fetching from Dhan API for the EOS strategy.
Supports both live trading and backtesting data requirements.
"""

import requests
import time
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

from .config import (
    DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN, API_BASE_URL,
    FNO_STOCKS, EOS_CONFIG
)


class EOSDataFetcher:
    """
    Data fetcher for EOS strategy.
    Provides methods for live market data and historical data for backtesting.
    """
    
    def __init__(self):
        self.headers = {
            "Content-Type": "application/json",
            "access-token": DHAN_ACCESS_TOKEN,
            "client-id": DHAN_CLIENT_ID
        }
        self.rate_limit_delay = 0.25  # 250ms between requests
        self.last_request_time = 0
    
    def _rate_limit(self):
        """Enforce rate limiting between API calls."""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.rate_limit_delay:
            time.sleep(self.rate_limit_delay - elapsed)
        self.last_request_time = time.time()
    
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
            response.raise_for_status()
            return {"status": "success", **response.json()}
        except requests.exceptions.RequestException as e:
            return {"status": "error", "error": str(e), "data": None}
    
    # ===== LIVE MARKET DATA APIs =====
    
    def get_market_quote(self, security_ids: List[int], segment: str = "NSE_EQ") -> Dict:
        """Get full market quote including LTP, OHLC, OI, volume."""
        payload = {segment: security_ids}
        return self._make_request("/marketfeed/quote", payload)
    
    def get_ltp(self, security_ids: List[int], segment: str = "NSE_EQ") -> Dict:
        """Get Last Traded Price for instruments."""
        payload = {segment: security_ids}
        return self._make_request("/marketfeed/ltp", payload)
    
    def get_option_chain(self, underlying_scrip: int, underlying_seg: str = "NSE_EQ", 
                         expiry: str = None) -> Dict:
        """Get full option chain with all strikes, OI, Greeks, IV."""
        payload = {
            "UnderlyingScrip": underlying_scrip,
            "UnderlyingSeg": underlying_seg
        }
        if expiry:
            payload["Expiry"] = expiry
        return self._make_request("/optionchain", payload)
    
    def get_expiry_list(self, underlying_scrip: int, underlying_seg: str = "NSE_EQ") -> Dict:
        """Get list of available expiry dates for options."""
        payload = {
            "UnderlyingScrip": underlying_scrip,
            "UnderlyingSeg": underlying_seg
        }
        return self._make_request("/optionchain/expirylist", payload)
    
    # ===== HISTORICAL DATA APIs (for live trading) =====
    
    def get_intraday_data(self, security_id: str, segment: str = "NSE_EQ",
                          instrument: str = "EQUITY", interval: str = "5",
                          from_date: str = None, to_date: str = None) -> Dict:
        """Get intraday OHLC candles for live trading."""
        if from_date is None:
            from_date = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d 09:15:00")
        if to_date is None:
            to_date = datetime.now().strftime("%Y-%m-%d 15:30:00")
        
        payload = {
            "securityId": security_id,
            "exchangeSegment": segment,
            "instrument": instrument,
            "interval": interval,
            "oi": True,
            "fromDate": from_date,
            "toDate": to_date
        }
        return self._make_request("/charts/intraday", payload)
    
    def get_futures_intraday(self, futures_id: int, interval: str = "5",
                             from_date: str = None, to_date: str = None) -> Dict:
        """Get futures intraday data for SMA calculation."""
        if from_date is None:
            from_date = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d 09:15:00")
        if to_date is None:
            to_date = datetime.now().strftime("%Y-%m-%d 15:30:00")
        
        payload = {
            "securityId": str(futures_id),
            "exchangeSegment": "NSE_FNO",
            "instrument": "FUTSTK",
            "interval": interval,
            "oi": True,
            "fromDate": from_date,
            "toDate": to_date
        }
        return self._make_request("/charts/intraday", payload)
    
    # ===== HISTORICAL DATA APIs (for backtesting) =====
    
    def get_daily_historical(self, security_id: str, segment: str = "NSE_EQ",
                             instrument: str = "EQUITY", from_date: str = None,
                             to_date: str = None) -> Dict:
        """Get daily OHLC data for backtesting (previous day close)."""
        if from_date is None:
            from_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
        if to_date is None:
            to_date = datetime.now().strftime("%Y-%m-%d")
        
        payload = {
            "securityId": security_id,
            "exchangeSegment": segment,
            "instrument": instrument,
            "expiryCode": 0,
            "oi": False,
            "fromDate": from_date,
            "toDate": to_date
        }
        return self._make_request("/charts/historical", payload)
    
    def get_expired_options_data(self, security_id: int, instrument: str = "OPTSTK",
                                  expiry_flag: str = "MONTH", expiry_code: int = 1,
                                  strike: str = "ATM", option_type: str = "CALL",
                                  interval: str = "5", required_data: List[str] = None,
                                  from_date: str = None, to_date: str = None) -> Dict:
        """
        Get expired options data for backtesting.
        Returns minute-level OHLC, OI, IV, spot, strike data.
        Max 30 days per API call, 5 years of data available.
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

    # ===== HELPER METHODS FOR CALCULATIONS =====

    @staticmethod
    def calculate_sma(prices: List[float], period: int) -> Optional[float]:
        """Calculate Simple Moving Average for a list of prices."""
        if len(prices) < period:
            return None
        return sum(prices[-period:]) / period

    @staticmethod
    def calculate_price_change_pct(current_price: float, prev_close: float) -> float:
        """Calculate percentage price change from previous close."""
        if prev_close == 0:
            return 0.0
        return ((current_price - prev_close) / prev_close) * 100

    @staticmethod
    def calculate_oi_change_pct(current_oi: int, prev_oi: int) -> float:
        """Calculate percentage OI change from previous close."""
        if prev_oi == 0:
            return 0.0
        return ((current_oi - prev_oi) / prev_oi) * 100

    @staticmethod
    def aggregate_candles(candles: List[Dict], num_candles: int = 2) -> List[Dict]:
        """
        Aggregate multiple candles into larger timeframe candles.
        E.g., aggregate 2x 5-min candles into 10-min candles.

        Args:
            candles: List of candles with open, high, low, close, volume, oi
            num_candles: Number of candles to aggregate (default 2)

        Returns:
            List of aggregated candles
        """
        if not candles or len(candles) < num_candles:
            return candles

        aggregated = []
        for i in range(0, len(candles) - num_candles + 1, num_candles):
            group = candles[i:i + num_candles]
            agg_candle = {
                "timestamp": group[0].get("timestamp") or group[0].get("start_Time"),
                "open": group[0].get("open"),
                "high": max(c.get("high", 0) for c in group),
                "low": min(c.get("low", float("inf")) for c in group),
                "close": group[-1].get("close"),
                "volume": sum(c.get("volume", 0) for c in group),
                "oi": group[-1].get("oi", 0)
            }
            aggregated.append(agg_candle)
        return aggregated

    def find_nearest_otm_strike(self, spot_price: float, option_chain: Dict,
                                 option_type: str = "CALL") -> Optional[Dict]:
        """
        Find the nearest OTM (Out-of-The-Money) strike.
        For CALL: First strike > spot price
        For PUT: First strike < spot price

        Args:
            spot_price: Current spot/underlying price
            option_chain: Option chain data from get_option_chain()
            option_type: "CALL" or "PUT"

        Returns:
            Strike data dict or None if not found
        """
        if option_chain.get("status") != "success" or "data" not in option_chain:
            return None

        chain_data = option_chain.get("data", {})
        strikes = []

        # Extract all strikes from option chain
        for item in chain_data:
            strike_price = item.get("strikePrice", 0)
            if option_type.upper() == "CALL" and strike_price > spot_price:
                strikes.append(item)
            elif option_type.upper() == "PUT" and strike_price < spot_price:
                strikes.append(item)

        if not strikes:
            return None

        # Sort and get nearest OTM
        if option_type.upper() == "CALL":
            strikes.sort(key=lambda x: x.get("strikePrice", 0))
            return strikes[0] if strikes else None
        else:
            strikes.sort(key=lambda x: x.get("strikePrice", 0), reverse=True)
            return strikes[0] if strikes else None

    def get_monthly_expiry(self, expiry_list: Dict) -> Optional[str]:
        """
        Get the current month's expiry date from expiry list.

        Args:
            expiry_list: Response from get_expiry_list()

        Returns:
            Expiry date string or None
        """
        if expiry_list.get("status") != "success" or "data" not in expiry_list:
            return None

        expiries = expiry_list.get("data", [])
        if not expiries:
            return None

        # Filter for monthly expiries (usually last Thursday of month)
        # Return the first (nearest) monthly expiry
        for expiry in expiries:
            expiry_date = expiry.get("expiryDate", "")
            expiry_type = expiry.get("expiryType", "")
            if expiry_type.upper() == "MONTH" or "MONTH" in str(expiry).upper():
                return expiry_date

        # Fallback to first expiry if no monthly found
        return expiries[0].get("expiryDate") if expiries else None

    def get_stock_data_for_screening(self, symbol: str) -> Dict:
        """
        Get all required data for screening a stock for EOS entry conditions.

        Args:
            symbol: Stock symbol (e.g., "RELIANCE")

        Returns:
            Dict with current_price, prev_close, price_change_pct,
            option_chain, futures_data, etc.
        """
        stock_info = FNO_STOCKS.get(symbol)
        if not stock_info:
            return {"status": "error", "error": f"Symbol {symbol} not in FNO_STOCKS"}

        equity_id = stock_info["equity_id"]
        futures_id = stock_info["futures_id"]

        # Get current market quote
        quote = self.get_market_quote([equity_id], "NSE_EQ")

        # Get option chain
        option_chain = self.get_option_chain(equity_id, "NSE_EQ")

        # Get expiry list
        expiry_list = self.get_expiry_list(equity_id, "NSE_EQ")

        # Get futures intraday for SMA
        futures_data = self.get_futures_intraday(futures_id)

        return {
            "status": "success",
            "symbol": symbol,
            "equity_id": equity_id,
            "futures_id": futures_id,
            "lot_size": stock_info["lot_size"],
            "quote": quote,
            "option_chain": option_chain,
            "expiry_list": expiry_list,
            "futures_data": futures_data
        }

