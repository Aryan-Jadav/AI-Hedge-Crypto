"""
CRYPTO Data Fetcher - Bybit REST API v5 Wrapper
Mirrors EOS/data_fetcher.py structure exactly.

Handles:
- Public endpoints (no auth): market data, klines, tickers, OI, funding rates
- Private endpoints (HMAC-SHA256 signed): wallet, positions, orders
- Rate limiting: 100ms between requests (Bybit allows 10 req/s)
"""

import time
import hmac
import hashlib
import json
import requests
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime

from .config import (
    BYBIT_API_KEY, BYBIT_API_SECRET, API_BASE_URL,
    CRYPTO_CONFIG, CRYPTO_PAIRS
)


class CryptoDataFetcher:
    """
    Bybit REST API v5 wrapper for CRYPTO strategy.
    Mirrors EOSDataFetcher structure exactly.

    Public methods need no auth.
    Private methods require BYBIT_API_KEY and BYBIT_API_SECRET.
    """

    def __init__(self) -> None:
        self.api_key: str = BYBIT_API_KEY
        self.api_secret: str = BYBIT_API_SECRET
        self.base_url: str = API_BASE_URL
        self.rate_limit_delay: float = 0.1    # 100ms between requests
        self.last_request_time: float = 0.0
        self._session: requests.Session = requests.Session()
        self._session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    # =========================================================================
    # AUTH / SIGNING
    # =========================================================================

    def _generate_signature(self, timestamp: str, recv_window: str, params_str: str) -> str:
        """
        HMAC-SHA256 signature for Bybit private endpoints.
        param_str = timestamp + api_key + recv_window + params_str
        """
        param_str = timestamp + self.api_key + recv_window + params_str
        return hmac.new(
            self.api_secret.encode("utf-8"),
            param_str.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

    def _get_auth_headers(self, params_str: str = "", recv_window: str = "5000") -> Dict[str, str]:
        """Build signed headers dict for private endpoints."""
        timestamp = str(int(time.time() * 1000))
        signature = self._generate_signature(timestamp, recv_window, params_str)
        return {
            "X-BAPI-API-KEY":     self.api_key,
            "X-BAPI-TIMESTAMP":   timestamp,
            "X-BAPI-SIGN":        signature,
            "X-BAPI-RECV-WINDOW": recv_window,
            "Content-Type":       "application/json",
        }

    # =========================================================================
    # REQUEST HELPERS
    # =========================================================================

    # Retry configuration
    MAX_RETRIES:   int   = 3
    BASE_DELAY:    float = 1.0    # seconds
    MAX_DELAY:     float = 30.0   # seconds

    # Errors that should NOT be retried (auth / bad params / geo-block)
    _NO_RETRY_KEYWORDS = ("auth", "invalid", "permission", "apikey",
                          "signature", "param", "not supported",
                          "forbidden", "403")

    def _rate_limit(self) -> None:
        """Enforce minimum delay between requests. Mirrors EOS pattern."""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.rate_limit_delay:
            time.sleep(self.rate_limit_delay - elapsed)
        self.last_request_time = time.time()

    @staticmethod
    def _is_retryable_error(error_msg: str) -> bool:
        """Return True if the error is transient and worth retrying."""
        lower = error_msg.lower()
        for kw in CryptoDataFetcher._NO_RETRY_KEYWORDS:
            if kw in lower:
                return False
        return True

    def _get_raw(self, endpoint: str, params: Dict = None,
                 private: bool = False) -> Dict:
        """Single-attempt GET request (no retry)."""
        self._rate_limit()
        url = f"{self.base_url}{endpoint}"
        params = params or {}

        try:
            if private:
                params_str = "&".join(
                    f"{k}={v}" for k, v in sorted(params.items()))
                headers = self._get_auth_headers(params_str)
                response = self._session.get(
                    url, params=params, headers=headers, timeout=10)
            else:
                response = self._session.get(url, params=params, timeout=10)

            response.raise_for_status()
            data = response.json()

            if data.get("retCode", -1) != 0:
                return {"error": data.get("retMsg", "Unknown Bybit error"),
                        "data": None}
            return {"data": data.get("result", {}), "error": None}

        except requests.exceptions.Timeout:
            return {"error": "Request timeout", "data": None}
        except requests.exceptions.RequestException as e:
            return {"error": str(e), "data": None}
        except Exception as e:
            return {"error": f"Unexpected error: {e}", "data": None}

    def _post_raw(self, endpoint: str, payload: Dict = None,
                  private: bool = True) -> Dict:
        """Single-attempt POST request (no retry)."""
        self._rate_limit()
        url = f"{self.base_url}{endpoint}"
        payload = payload or {}
        payload_str = json.dumps(payload)

        try:
            headers = self._get_auth_headers(payload_str)
            response = self._session.post(
                url, data=payload_str, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()

            if data.get("retCode", -1) != 0:
                return {"error": data.get("retMsg", "Unknown Bybit error"),
                        "data": None}
            return {"data": data.get("result", {}), "error": None}

        except requests.exceptions.Timeout:
            return {"error": "Request timeout", "data": None}
        except requests.exceptions.RequestException as e:
            return {"error": str(e), "data": None}
        except Exception as e:
            return {"error": f"Unexpected error: {e}", "data": None}

    def _request_with_retry(self, request_func, label: str = "") -> Dict:
        """
        Wrap a single-attempt request function with exponential backoff.
        Retries on transient failures (timeouts, 429, network errors).
        Does NOT retry auth / invalid-param errors.
        """
        last_result: Dict = {"error": "No attempt made", "data": None}

        for attempt in range(self.MAX_RETRIES + 1):
            result = request_func()

            # Success → return immediately
            if result.get("error") is None:
                return result

            last_result = result
            error_msg = result.get("error", "")

            # Non-retryable error → bail
            if not self._is_retryable_error(error_msg):
                return result

            # Last attempt → return the error
            if attempt == self.MAX_RETRIES:
                return result

            # Calculate exponential backoff with jitter
            delay = min(self.BASE_DELAY * (2 ** attempt), self.MAX_DELAY)
            print(f"[DataFetcher] {label} retry {attempt+1}/{self.MAX_RETRIES}"
                  f" in {delay:.1f}s: {error_msg}")
            time.sleep(delay)

        return last_result

    def _get(self, endpoint: str, params: Dict = None,
             private: bool = False) -> Dict:
        """GET with automatic retry on transient errors."""
        return self._request_with_retry(
            lambda: self._get_raw(endpoint, params, private),
            label=f"GET {endpoint}",
        )

    def _post(self, endpoint: str, payload: Dict = None,
              private: bool = True) -> Dict:
        """POST with automatic retry on transient errors."""
        return self._request_with_retry(
            lambda: self._post_raw(endpoint, payload, private),
            label=f"POST {endpoint}",
        )

    # =========================================================================
    # PUBLIC MARKET DATA
    # =========================================================================

    def get_kline(
        self,
        symbol: str,
        interval: str = "5",
        limit: int = 200,
        start: int = None,
        end: int = None
    ) -> Dict:
        """
        GET /v5/market/kline - OHLCV candles.
        interval: "1","3","5","15","30","60","120","240","360","720","D","W","M"
        Returns list of [startTime, open, high, low, close, volume, turnover]
        """
        params: Dict[str, Any] = {
            "category": "linear",
            "symbol":   symbol,
            "interval": interval,
            "limit":    limit,
        }
        if start:
            params["start"] = start
        if end:
            params["end"] = end

        return self._get("/market/kline", params)

    def get_ticker(self, symbol: str, category: str = "linear") -> Dict:
        """
        GET /v5/market/tickers - Single symbol ticker.
        Returns: lastPrice, prevPrice24h, price24hPcnt, volume24h,
                 fundingRate, nextFundingTime, openInterest, etc.
        """
        return self._get("/market/tickers", {"category": category, "symbol": symbol})

    def get_all_tickers(self, category: str = "linear") -> Dict:
        """
        GET /v5/market/tickers - All tickers for a category.
        Useful for batch scanning all pairs.
        """
        return self._get("/market/tickers", {"category": category})

    def get_orderbook(self, symbol: str, category: str = "linear", limit: int = 25) -> Dict:
        """
        GET /v5/market/orderbook - Order book depth.
        Returns top bids/asks for spread calculation.
        """
        return self._get("/market/orderbook", {
            "category": category,
            "symbol":   symbol,
            "limit":    limit,
        })

    def get_funding_rate_history(
        self,
        symbol: str,
        category: str = "linear",
        limit: int = 10
    ) -> Dict:
        """
        GET /v5/market/funding/history - Historical funding rates.
        Returns list of {symbol, fundingRate, fundingRateTimestamp}
        """
        return self._get("/market/funding/history", {
            "category": category,
            "symbol":   symbol,
            "limit":    limit,
        })

    def get_open_interest(
        self,
        symbol: str,
        interval_time: str = "5min",
        category: str = "linear",
        limit: int = 50
    ) -> Dict:
        """
        GET /v5/market/open-interest - Historical OI.
        intervalTime: "5min","15min","30min","1h","4h","1d"
        Returns list of {openInterest, timestamp}
        """
        return self._get("/market/open-interest", {
            "category":     category,
            "symbol":       symbol,
            "intervalTime": interval_time,
            "limit":        limit,
        })

    # =========================================================================
    # PRIVATE ACCOUNT DATA
    # =========================================================================

    def get_wallet_balance(self, account_type: str = "UNIFIED") -> Dict:
        """
        GET /v5/account/wallet-balance (private).
        Returns USDT balance, equity, available margin, etc.
        """
        return self._get("/account/wallet-balance", {
            "accountType": account_type,
            "coin":        "USDT",
        }, private=True)

    def get_positions(self, symbol: str = None, category: str = "linear") -> Dict:
        """
        GET /v5/position/list (private).
        Returns open perpetual positions.
        """
        params: Dict[str, Any] = {"category": category, "settleCoin": "USDT"}
        if symbol:
            params["symbol"] = symbol
        return self._get("/position/list", params, private=True)

    def get_open_orders(self, symbol: str = None, category: str = "linear") -> Dict:
        """GET /v5/order/realtime - Open/active orders."""
        params: Dict[str, Any] = {"category": category}
        if symbol:
            params["symbol"] = symbol
        return self._get("/order/realtime", params, private=True)

    # =========================================================================
    # ORDER MANAGEMENT
    # =========================================================================

    def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        qty: float,
        price: float = None,
        reduce_only: bool = False,
        category: str = "linear"
    ) -> Dict:
        """
        POST /v5/order/create (private).
        side: "Buy" (for LONG entry or SHORT exit) | "Sell" (for SHORT entry or LONG exit)
        order_type: "Market" | "Limit"
        """
        payload: Dict[str, Any] = {
            "category":   category,
            "symbol":     symbol,
            "side":       side,
            "orderType":  order_type,
            "qty":        str(qty),
            "reduceOnly": reduce_only,
            "timeInForce": "GTC",
        }
        if price and order_type == "Limit":
            payload["price"] = str(price)

        return self._post("/order/create", payload)

    def cancel_order(self, symbol: str, order_id: str, category: str = "linear") -> Dict:
        """POST /v5/order/cancel (private)."""
        return self._post("/order/cancel", {
            "category": category,
            "symbol":   symbol,
            "orderId":  order_id,
        })

    def get_order_detail(self, symbol: str, order_id: str,
                         category: str = "linear") -> Dict:
        """
        GET /v5/order/realtime - Query a single order by orderId.
        Returns order status, avgPrice, cumExecQty, orderStatus, etc.
        Used for fill confirmation after place_order().
        """
        return self._get("/order/realtime", {
            "category": category,
            "symbol":   symbol,
            "orderId":  order_id,
        }, private=True)

    def get_execution_list(self, symbol: str, order_id: str = None,
                           category: str = "linear",
                           limit: int = 50) -> Dict:
        """
        GET /v5/execution/list - Query trade execution / fill history.
        Returns list of executions with execPrice, execQty, execFee, etc.
        """
        params: Dict[str, Any] = {
            "category": category,
            "symbol":   symbol,
            "limit":    limit,
        }
        if order_id:
            params["orderId"] = order_id
        return self._get("/execution/list", params, private=True)

    def set_leverage(
        self,
        symbol: str,
        buy_leverage: str,
        sell_leverage: str,
        category: str = "linear"
    ) -> Dict:
        """POST /v5/position/set-leverage (private). Sets cross-margin leverage."""
        return self._post("/position/set-leverage", {
            "category":     category,
            "symbol":       symbol,
            "buyLeverage":  buy_leverage,
            "sellLeverage": sell_leverage,
        })

    def set_trading_stop(
        self,
        symbol: str,
        stop_loss: float = None,
        take_profit: float = None,
        category: str = "linear"
    ) -> Dict:
        """POST /v5/position/trading-stop - Set SL/TP on open position (private)."""
        payload: Dict[str, Any] = {
            "category":  category,
            "symbol":    symbol,
            "positionIdx": 0,  # 0 = one-way mode
        }
        if stop_loss is not None:
            payload["stopLoss"] = str(stop_loss)
        if take_profit is not None:
            payload["takeProfit"] = str(take_profit)
        return self._post("/position/trading-stop", payload)

    # =========================================================================
    # STATIC CALCULATION HELPERS (mirrors EOSDataFetcher)
    # =========================================================================

    @staticmethod
    def calculate_sma(prices: List[float], period: int) -> Optional[float]:
        """Calculate Simple Moving Average. Returns None if insufficient data."""
        if not prices or len(prices) < period:
            return None
        return sum(prices[-period:]) / period

    @staticmethod
    def calculate_price_change_pct(current: float, reference: float) -> float:
        """% change from reference price."""
        if reference == 0:
            return 0.0
        return ((current - reference) / reference) * 100.0

    @staticmethod
    def calculate_funding_rate_extreme(
        funding_rate: float,
        threshold: float = None
    ) -> bool:
        """Returns True if abs(funding_rate) > threshold (default from config)."""
        if threshold is None:
            threshold = CRYPTO_CONFIG["funding_rate_threshold"]
        return abs(funding_rate) > threshold

    @staticmethod
    def calculate_volume_spike(
        current_volume: float,
        avg_volume: float,
        multiplier: float = None
    ) -> bool:
        """Returns True if current_volume > avg_volume * multiplier."""
        if multiplier is None:
            multiplier = CRYPTO_CONFIG["volume_spike_multiplier"]
        if avg_volume == 0:
            return False
        return current_volume > (avg_volume * multiplier)

    @staticmethod
    def calculate_avg_volume(volumes: List[float], periods: int = 20) -> float:
        """Average volume over N most recent periods."""
        if not volumes:
            return 0.0
        recent = volumes[-periods:]
        return sum(recent) / len(recent)

    # =========================================================================
    # COMPOSITE SCREENING (mirrors get_stock_data_for_screening)
    # =========================================================================

    def get_pair_data_for_screening(self, symbol: str) -> Dict:
        """
        Fetch all data needed to screen a pair for CRYPTO-EOS entry conditions.
        Mirrors EOSDataFetcher.get_stock_data_for_screening().

        Returns dict with:
            current_price, price_change_pct, funding_rate,
            funding_rate_extreme, volume_24h, avg_volume, volume_spike,
            kline_closes, sma_fast, sma_slow, error
        """
        result: Dict[str, Any] = {
            "symbol":              symbol,
            "current_price":       0.0,
            "price_change_pct":    0.0,
            "funding_rate":        0.0,
            "funding_rate_extreme": False,
            "volume_24h":          0.0,
            "avg_volume":          0.0,
            "volume_spike":        False,
            "kline_closes":        [],
            "sma_fast":            None,
            "sma_slow":            None,
            "error":               None,
        }

        # 1. Get current ticker (price, funding rate, 24h volume)
        ticker_resp = self.get_ticker(symbol)
        if ticker_resp.get("error") or not ticker_resp.get("data"):
            result["error"] = f"Ticker fetch failed: {ticker_resp.get('error')}"
            return result

        ticker_list = ticker_resp["data"].get("list", [])
        if not ticker_list:
            result["error"] = "Empty ticker response"
            return result

        ticker = ticker_list[0]
        try:
            current_price = float(ticker.get("lastPrice", 0))
            prev_price_24h = float(ticker.get("prevPrice24h", 0))
            volume_24h = float(ticker.get("volume24h", 0))
            funding_rate = float(ticker.get("fundingRate", 0))
        except (ValueError, TypeError) as e:
            result["error"] = f"Ticker parse error: {e}"
            return result

        result["current_price"] = current_price
        result["funding_rate"] = funding_rate
        result["volume_24h"] = volume_24h
        result["price_change_pct"] = self.calculate_price_change_pct(current_price, prev_price_24h)
        result["funding_rate_extreme"] = self.calculate_funding_rate_extreme(funding_rate)

        # 2. Get klines for SMA calculation
        kline_resp = self.get_kline(
            symbol,
            interval=CRYPTO_CONFIG["candle_interval"],
            limit=50
        )
        if kline_resp.get("error") or not kline_resp.get("data"):
            result["error"] = f"Kline fetch failed: {kline_resp.get('error')}"
            return result

        kline_list = kline_resp["data"].get("list", [])
        # Bybit returns klines newest-first; reverse for chronological order
        kline_list = list(reversed(kline_list))
        closes = []
        volumes = []
        for candle in kline_list:
            try:
                closes.append(float(candle[4]))   # close price
                volumes.append(float(candle[5]))  # volume
            except (IndexError, ValueError):
                continue

        result["kline_closes"] = closes

        # 3. Calculate average volume and volume spike
        avg_volume = self.calculate_avg_volume(volumes)
        result["avg_volume"] = avg_volume
        result["volume_spike"] = self.calculate_volume_spike(volume_24h, avg_volume)

        # 4. Calculate SMAs
        fast_period = CRYPTO_CONFIG["sma_fast"]
        slow_period = CRYPTO_CONFIG["sma_slow"]
        result["sma_fast"] = self.calculate_sma(closes, fast_period)
        result["sma_slow"] = self.calculate_sma(closes, slow_period)

        return result

    def get_current_price(self, symbol: str) -> Optional[float]:
        """Quick last price fetch for a single symbol."""
        resp = self.get_ticker(symbol)
        if resp.get("error") or not resp.get("data"):
            return None
        ticker_list = resp["data"].get("list", [])
        if not ticker_list:
            return None
        try:
            return float(ticker_list[0].get("lastPrice", 0))
        except (ValueError, TypeError):
            return None

    def get_usdt_balance(self) -> Tuple[float, str]:
        """
        Fetch available USDT balance from Bybit wallet.
        Returns (balance_usdt, error_string).
        """
        resp = self.get_wallet_balance()
        if resp.get("error"):
            return 0.0, resp["error"]
        try:
            coins = resp["data"].get("list", [{}])[0].get("coin", [])
            for coin in coins:
                if coin.get("coin") == "USDT":
                    return float(coin.get("availableToWithdraw", 0)), ""
            return 0.0, "USDT coin not found in wallet"
        except (KeyError, IndexError, ValueError, TypeError) as e:
            return 0.0, str(e)
