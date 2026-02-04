"""
EOS AI Validator - Claude-powered trade validation

Uses Claude Sonnet 4.5 via OpenRouter for intelligent trade validation.
All communication is in JSON for minimal tokens and fast responses.

IMPORTANT: This validator can only APPROVE or REJECT signals.
It CANNOT modify the strategy, entry conditions, or exit rules.
"""

import json
import requests
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional, Tuple, List
from enum import Enum

from .eos_validator_cache import EOSValidatorCache
from .eos_market_context import EOSMarketContext, MarketContext
from .config import EOS_CONFIG, FNO_STOCKS


class ValidationResult(Enum):
    """Possible validation outcomes."""
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    ERROR = "ERROR"


@dataclass
class ValidationResponse:
    """Response from AI validator."""
    result: ValidationResult
    confidence: float  # 0.0 to 1.0
    reason: str
    latency_ms: int
    tokens_used: int
    tier_used: str  # "TIER1_RULES", "TIER2_AI"
    
    def to_dict(self) -> Dict:
        return {
            "result": self.result.value,
            "confidence": self.confidence,
            "reason": self.reason,
            "latency_ms": self.latency_ms,
            "tokens_used": self.tokens_used,
            "tier": self.tier_used
        }


@dataclass 
class SignalData:
    """Signal data for validation."""
    symbol: str
    signal_type: str  # "PUT" or "CALL"
    entry_price: float
    stop_loss: float
    price_change_pct: float
    oi_change_pct: float
    entry_date: str
    entry_time: str
    lot_size: int
    
    def to_dict(self) -> Dict:
        return {
            "sym": self.symbol,
            "type": self.signal_type,
            "entry": self.entry_price,
            "sl": self.stop_loss,
            "price_chg": round(self.price_change_pct, 2),
            "oi_chg": round(self.oi_change_pct, 2),
            "date": self.entry_date,
            "time": self.entry_time,
            "lot": self.lot_size
        }


class EOSAIValidator:
    """
    AI-powered trade validator using Claude Sonnet 4.5.
    
    Validation Flow:
    1. TIER 1: Quick rule-based checks (FREE, <10ms)
    2. TIER 2: Claude AI validation (if Tier 1 passes)
    
    The validator CANNOT:
    - Change entry/exit conditions
    - Modify stop loss or trailing SL
    - Change signal direction
    - Create new signals
    
    It can ONLY:
    - APPROVE a valid signal
    - REJECT a valid signal (skip the trade)
    """
    
    # OpenRouter API endpoint
    OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
    
    def __init__(self, api_key: str, model: str = "anthropic/claude-sonnet-4"):
        self.api_key = api_key
        self.model = model
        self.cache = EOSValidatorCache()
        self.market_context = EOSMarketContext(self.cache)
        
        # Statistics
        self.stats = {
            "total_validations": 0,
            "tier1_rejections": 0,
            "tier2_approvals": 0,
            "tier2_rejections": 0,
            "errors": 0,
            "total_tokens": 0,
            "total_latency_ms": 0
        }
        
        # Validation history for learning
        self.validation_history: List[Dict] = []
        
        # Backtest mode flag
        self._backtest_mode = False
    
    def set_backtest_mode(self, enabled: bool = True):
        """Enable backtest mode."""
        self._backtest_mode = enabled
    
    # ===== TIER 1: Quick Rule-Based Checks =====

    def _tier1_validate(self, signal: SignalData,
                        market: Optional[MarketContext]) -> Tuple[bool, Optional[str]]:
        """
        Quick rule-based validation (no AI call).

        Returns:
            Tuple of (passed, rejection_reason)
        """
        # Rule 1: Reject if VIX > 25 (too volatile) - only if VIX data available
        if market and market.vix is not None and market.vix > 25:
            return False, f"VIX too high ({market.vix:.1f} > 25)"

        # Rule 2: Reject trades in first 5 minutes (9:15-9:20)
        hour, minute = map(int, signal.entry_time.split(":")[:2])
        if hour == 9 and minute < 20:
            return False, "Too early (first 5 min of market)"

        # Rule 3: Reject if signal strength is weak
        if abs(signal.price_change_pct) < 2.0 or abs(signal.oi_change_pct) < 1.75:
            return False, "Signal below thresholds"

        # Rule 4: Reject very low premium options (< ₹1)
        if signal.entry_price < 1.0:
            return False, f"Premium too low (₹{signal.entry_price:.2f})"

        # All Tier 1 rules passed
        return True, None

    # ===== TIER 2: Claude AI Validation =====

    def _build_prompt(self, signal: SignalData, market: MarketContext,
                      stock_context: Dict) -> Dict:
        """Build minimal JSON prompt for Claude."""
        return {
            "signal": signal.to_dict(),
            "market": market.to_dict(),
            "stock": stock_context,
            "strategy": {
                "name": "EOS",
                "logic": "Contrarian - UP triggers PUT, DOWN triggers CALL",
                "thresholds": {"price": 2.0, "oi": 1.75},
                "sl": "30%"
            },
            "task": "Validate trade. APPROVE if high probability. REJECT if risky. Reply JSON only: {\"result\":\"APPROVE\"|\"REJECT\",\"confidence\":0.0-1.0,\"reason\":\"brief\"}"
        }

    def _tier2_ai_validate(self, signal: SignalData, market: MarketContext,
                           stock_context: Dict) -> ValidationResponse:
        """
        AI validation using Claude Sonnet 4.5 via OpenRouter.
        Uses JSON for minimal token usage.
        """
        start_time = time.time()

        try:
            prompt = self._build_prompt(signal, market, stock_context)

            response = requests.post(
                self.OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://eos-trading-system.local",
                    "X-Title": "EOS AI Validator"
                },
                json={
                    "model": self.model,
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are a trade validator. Respond ONLY with valid JSON. No markdown, no explanation outside JSON."
                        },
                        {
                            "role": "user",
                            "content": json.dumps(prompt)
                        }
                    ],
                    "temperature": 0.1,
                    "max_tokens": 100
                },
                timeout=10
            )

            latency_ms = int((time.time() - start_time) * 1000)

            if response.status_code != 200:
                return ValidationResponse(
                    result=ValidationResult.ERROR,
                    confidence=0.0,
                    reason=f"API error: {response.status_code}",
                    latency_ms=latency_ms,
                    tokens_used=0,
                    tier_used="TIER2_AI"
                )

            data = response.json()
            tokens_used = data.get("usage", {}).get("total_tokens", 0)

            # Parse AI response
            ai_content = data["choices"][0]["message"]["content"]

            # Clean potential markdown formatting
            ai_content = ai_content.strip()
            if ai_content.startswith("```"):
                ai_content = ai_content.split("```")[1]
                if ai_content.startswith("json"):
                    ai_content = ai_content[4:]
            ai_content = ai_content.strip()

            ai_response = json.loads(ai_content)

            result = ValidationResult.APPROVE if ai_response.get("result") == "APPROVE" else ValidationResult.REJECT

            return ValidationResponse(
                result=result,
                confidence=float(ai_response.get("confidence", 0.5)),
                reason=ai_response.get("reason", "No reason provided"),
                latency_ms=latency_ms,
                tokens_used=tokens_used,
                tier_used="TIER2_AI"
            )

        except json.JSONDecodeError as e:
            latency_ms = int((time.time() - start_time) * 1000)
            return ValidationResponse(
                result=ValidationResult.APPROVE,  # Default to approve on parse error
                confidence=0.5,
                reason=f"JSON parse error - defaulting to approve: {str(e)[:50]}",
                latency_ms=latency_ms,
                tokens_used=0,
                tier_used="TIER2_AI"
            )
        except requests.exceptions.Timeout:
            latency_ms = int((time.time() - start_time) * 1000)
            return ValidationResponse(
                result=ValidationResult.APPROVE,  # Default to approve on timeout
                confidence=0.5,
                reason="API timeout - defaulting to approve",
                latency_ms=latency_ms,
                tokens_used=0,
                tier_used="TIER2_AI"
            )
        except Exception as e:
            latency_ms = int((time.time() - start_time) * 1000)
            return ValidationResponse(
                result=ValidationResult.ERROR,
                confidence=0.0,
                reason=f"Error: {str(e)[:100]}",
                latency_ms=latency_ms,
                tokens_used=0,
                tier_used="TIER2_AI"
            )

    # ===== MAIN VALIDATION METHOD =====

    def validate(self, signal: SignalData,
                 market_context: Optional[MarketContext] = None,
                 skip_ai: bool = False) -> ValidationResponse:
        """
        Main validation method.

        Args:
            signal: SignalData object with trade details
            market_context: Optional MarketContext with REAL market data
                           If None, only time-based context will be used
            skip_ai: Skip Tier 2 AI validation (for testing)

        Returns:
            ValidationResponse with APPROVE/REJECT decision
        """
        start_time = time.time()
        self.stats["total_validations"] += 1

        # Get market context - use provided real data or get minimal time-based context
        if market_context:
            market = market_context
        else:
            market = self.market_context.get_context(signal.entry_date, signal.entry_time)

        # TIER 1: Quick rule-based validation
        tier1_passed, tier1_reason = self._tier1_validate(signal, market)

        if not tier1_passed:
            self.stats["tier1_rejections"] += 1
            latency_ms = int((time.time() - start_time) * 1000)
            self.stats["total_latency_ms"] += latency_ms

            response = ValidationResponse(
                result=ValidationResult.REJECT,
                confidence=1.0,
                reason=f"TIER1: {tier1_reason}",
                latency_ms=latency_ms,
                tokens_used=0,
                tier_used="TIER1_RULES"
            )
            self._record_validation(signal, response)
            return response

        # If skip_ai flag is set, approve without AI
        if skip_ai:
            latency_ms = int((time.time() - start_time) * 1000)
            return ValidationResponse(
                result=ValidationResult.APPROVE,
                confidence=0.8,
                reason="TIER1 passed (AI skipped)",
                latency_ms=latency_ms,
                tokens_used=0,
                tier_used="TIER1_RULES"
            )

        # TIER 2: AI validation
        stock_context = self.market_context.get_stock_context(signal.symbol)
        response = self._tier2_ai_validate(signal, market, stock_context)

        # Update stats
        if response.result == ValidationResult.APPROVE:
            self.stats["tier2_approvals"] += 1
        elif response.result == ValidationResult.REJECT:
            self.stats["tier2_rejections"] += 1
        else:
            self.stats["errors"] += 1

        self.stats["total_tokens"] += response.tokens_used
        self.stats["total_latency_ms"] += response.latency_ms

        self._record_validation(signal, response)
        return response

    def _record_validation(self, signal: SignalData, response: ValidationResponse):
        """Record validation for history/learning."""
        self.validation_history.append({
            "timestamp": datetime.now().isoformat(),
            "signal": signal.to_dict(),
            "response": response.to_dict()
        })

    # ===== STATISTICS & REPORTING =====

    def get_stats(self) -> Dict:
        """Get validation statistics."""
        total = self.stats["total_validations"]
        if total == 0:
            return self.stats

        return {
            **self.stats,
            "tier1_rejection_rate": f"{self.stats['tier1_rejections'] / total * 100:.1f}%",
            "tier2_approval_rate": f"{self.stats['tier2_approvals'] / max(1, total - self.stats['tier1_rejections']) * 100:.1f}%",
            "avg_latency_ms": self.stats["total_latency_ms"] // max(1, total),
            "avg_tokens_per_call": self.stats["total_tokens"] // max(1, total - self.stats["tier1_rejections"]),
            "cache_stats": self.cache.get_stats()
        }

    def print_stats(self):
        """Print validation statistics."""
        stats = self.get_stats()
        print("\n" + "=" * 60)
        print("EOS AI VALIDATOR STATISTICS")
        print("=" * 60)
        print(f"Total Validations:    {stats['total_validations']}")
        print(f"Tier 1 Rejections:    {stats['tier1_rejections']} ({stats.get('tier1_rejection_rate', '0%')})")
        print(f"Tier 2 Approvals:     {stats['tier2_approvals']}")
        print(f"Tier 2 Rejections:    {stats['tier2_rejections']}")
        print(f"Errors:               {stats['errors']}")
        print("-" * 60)
        print(f"Total Tokens Used:    {stats['total_tokens']}")
        print(f"Avg Latency:          {stats.get('avg_latency_ms', 0)}ms")
        print(f"Avg Tokens/Call:      {stats.get('avg_tokens_per_call', 0)}")
        print("=" * 60)

    def reset_stats(self):
        """Reset statistics."""
        self.stats = {
            "total_validations": 0,
            "tier1_rejections": 0,
            "tier2_approvals": 0,
            "tier2_rejections": 0,
            "errors": 0,
            "total_tokens": 0,
            "total_latency_ms": 0
        }
        self.validation_history.clear()
