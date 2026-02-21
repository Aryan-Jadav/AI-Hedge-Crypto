"""
CRYPTO AI Validator
Mirrors EOS/eos_ai_validator.py structure exactly.

Two-tier validation:
  Tier 1: Rule-based checks (<10ms, free)
    - Price change threshold not met
    - Outside Asia session window
    - No secondary confirmation (no funding extreme, no volume spike)
    - Entry price is zero

  Tier 2: Claude AI via OpenRouter (~2-4s, ~300-500 tokens)
    - Same JSON-based prompt format as EOS
    - Returns APPROVE/REJECT + confidence + reason
    - Falls back to APPROVE on error (same as EOS)
"""

import json
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Tuple

import requests
import pytz

from .config import CRYPTO_CONFIG, OPENROUTER_API_KEY
from .market_context import CryptoMarketContextData, CryptoMarketContext

IST = pytz.timezone("Asia/Kolkata")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL  = "anthropic/claude-sonnet-4-6"


# =============================================================================
# ENUMS / DATACLASSES (mirrors EOS ValidationResult, ValidationResponse, SignalData)
# =============================================================================

class CryptoValidationResult(Enum):
    APPROVE = "APPROVE"
    REJECT  = "REJECT"
    ERROR   = "ERROR"


@dataclass
class CryptoValidationResponse:
    """Mirrors EOS ValidationResponse exactly."""
    result: CryptoValidationResult
    confidence: float        # 0.0 - 1.0
    reason: str
    latency_ms: int
    tokens_used: int
    tier_used: str           # "TIER1_RULES" or "TIER2_AI"

    def to_dict(self) -> Dict:
        return {
            "result":      self.result.value,
            "confidence":  self.confidence,
            "reason":      self.reason,
            "latency_ms":  self.latency_ms,
            "tokens_used": self.tokens_used,
            "tier_used":   self.tier_used,
        }


@dataclass
class CryptoSignalData:
    """
    Signal data for AI validation. Mirrors EOS SignalData.
    Compact representation to minimize token usage.
    """
    symbol: str
    signal_type: str          # "LONG" or "SHORT"
    entry_price: float
    stop_loss: float
    price_change_pct: float
    funding_rate: float       # decimal (e.g., 0.0003)
    funding_rate_extreme: bool
    volume_spike: bool
    entry_date: str
    entry_time: str           # IST HH:MM
    quantity: float

    def to_dict(self) -> Dict:
        """Compact dict for AI prompt — minimizes token usage."""
        return {
            "sym":          self.symbol,
            "type":         self.signal_type,
            "entry":        round(self.entry_price, 4),
            "sl":           round(self.stop_loss, 4),
            "price_chg":    round(self.price_change_pct, 2),
            "funding":      round(self.funding_rate * 100, 4),  # as percentage
            "funding_ext":  self.funding_rate_extreme,
            "vol_spike":    self.volume_spike,
            "date":         self.entry_date,
            "time":         self.entry_time,
            "qty":          self.quantity,
        }


# =============================================================================
# AI VALIDATOR
# =============================================================================

class CryptoAIValidator:
    """
    CRYPTO AI Validator. Mirrors EOSAIValidator exactly.

    Tier 1: Fast rule-based rejection (same spirit as EOS Tier 1).
    Tier 2: Claude AI validation via OpenRouter (same JSON format).
    """

    def __init__(
        self,
        api_key: str = None,
        model: str = DEFAULT_MODEL,
    ) -> None:
        self.api_key = api_key or OPENROUTER_API_KEY
        self.model = model
        self.market_context_mgr = CryptoMarketContext()
        self.validation_history: List[Dict] = []

        # Stats tracking (mirrors EOS validator stats)
        self.stats = {
            "total_validations":   0,
            "tier1_rejections":    0,
            "tier2_approvals":     0,
            "tier2_rejections":    0,
            "tier2_errors":        0,
            "total_latency_ms":    0,
            "total_tokens_used":   0,
        }

    # -------------------------------------------------------------------------
    # Tier 1: Rule-based validation (free, fast)
    # -------------------------------------------------------------------------

    def _tier1_validate(
        self,
        signal: CryptoSignalData,
        market: Optional[CryptoMarketContextData],
    ) -> Tuple[bool, Optional[str]]:
        """
        Fast rule-based rejection. Mirrors EOSAIValidator._tier1_validate().
        Returns (should_reject: bool, reason: str | None).
        """
        # 1. Entry price sanity check
        if signal.entry_price <= 0:
            return True, "Entry price is zero or negative"

        # 2. Price change threshold
        price_threshold = CRYPTO_CONFIG["price_change_threshold"]
        if abs(signal.price_change_pct) < price_threshold:
            return True, (
                f"Price change {signal.price_change_pct:.2f}% < "
                f"threshold {price_threshold:.1f}%"
            )

        # 3. At least one secondary confirmation required
        if not signal.funding_rate_extreme and not signal.volume_spike:
            return True, "No secondary confirmation: neither funding extreme nor volume spike"

        # 4. Session window check
        if market and market.trading_session not in ("ASIA", "EUROPE", "US"):
            return True, f"Not in an active trading session: {market.trading_session}"

        return False, None

    # -------------------------------------------------------------------------
    # Tier 2: Claude AI validation
    # -------------------------------------------------------------------------

    def _build_prompt(
        self,
        signal: CryptoSignalData,
        market: CryptoMarketContextData,
        pair_context: Dict,
    ) -> Dict:
        """
        Build the Claude API prompt. Mirrors EOSAIValidator._build_prompt().
        Kept minimal for token efficiency.
        """
        system_content = (
            "You are a crypto trading risk manager for CRYPTO-EOS strategy. "
            "CRYPTO-EOS is a contrarian perpetual futures strategy: "
            "price drops >4% → LONG perp (expect bounce); "
            "price spikes >4% → SHORT perp (expect retrace). "
            "Secondary confirmation: funding rate extreme (abs>0.01%) OR volume spike (>1.5x avg). "
            "Stop loss: 3% from entry. Trading session: Asia (05:30-14:00 IST). "
            "Respond with JSON only: {\"decision\":\"APPROVE\" or \"REJECT\", "
            "\"confidence\":0.0-1.0, \"reason\":\"one sentence\"}"
        )

        user_content = (
            f"Validate this CRYPTO-EOS trade:\n"
            f"Signal: {json.dumps(signal.to_dict())}\n"
            f"Market: {json.dumps(market.to_dict() if market else {})}\n"
            f"Pair: {json.dumps(pair_context)}\n"
            f"Strategy thresholds: price>4%, funding>0.01%, sl=3%. "
            f"APPROVE if high probability reversal. REJECT if too risky."
        )

        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_content},
                {"role": "user",   "content": user_content},
            ],
            "max_tokens":  100,
            "temperature": 0.1,
        }

    def _tier2_ai_validate(
        self,
        signal: CryptoSignalData,
        market: CryptoMarketContextData,
        pair_context: Dict,
    ) -> CryptoValidationResponse:
        """
        Claude AI validation. Mirrors EOSAIValidator._tier2_ai_validate().
        Falls back to APPROVE on error (same as EOS).
        """
        start_time = time.time()

        if not self.api_key:
            return CryptoValidationResponse(
                result=CryptoValidationResult.APPROVE,
                confidence=0.5,
                reason="No OpenRouter API key - defaulting to APPROVE",
                latency_ms=0,
                tokens_used=0,
                tier_used="TIER2_AI",
            )

        try:
            payload = self._build_prompt(signal, market, pair_context)
            response = requests.post(
                OPENROUTER_URL,
                headers={
                    "Authorization":  f"Bearer {self.api_key}",
                    "Content-Type":   "application/json",
                    "HTTP-Referer":   "https://ai-hedgefund.local",
                    "X-Title":        "AI-Hedgefund-Crypto",
                },
                json=payload,
                timeout=15,
            )
            response.raise_for_status()
            resp_data = response.json()

            latency_ms = int((time.time() - start_time) * 1000)
            tokens_used = resp_data.get("usage", {}).get("total_tokens", 0)

            content = resp_data["choices"][0]["message"]["content"].strip()

            # Parse JSON response from Claude
            # Handle markdown code blocks if present
            if "```" in content:
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]

            parsed = json.loads(content)
            decision = parsed.get("decision", "APPROVE").upper()
            confidence = float(parsed.get("confidence", 0.7))
            reason = parsed.get("reason", "AI validation")

            result = (
                CryptoValidationResult.APPROVE
                if decision == "APPROVE"
                else CryptoValidationResult.REJECT
            )

            return CryptoValidationResponse(
                result=result,
                confidence=confidence,
                reason=reason,
                latency_ms=latency_ms,
                tokens_used=tokens_used,
                tier_used="TIER2_AI",
            )

        except (json.JSONDecodeError, KeyError, IndexError):
            # Parse error → default APPROVE (same as EOS)
            latency_ms = int((time.time() - start_time) * 1000)
            self.stats["tier2_errors"] += 1
            return CryptoValidationResponse(
                result=CryptoValidationResult.APPROVE,
                confidence=0.5,
                reason="AI response parse error - defaulting to APPROVE",
                latency_ms=latency_ms,
                tokens_used=0,
                tier_used="TIER2_AI",
            )
        except requests.exceptions.Timeout:
            latency_ms = int((time.time() - start_time) * 1000)
            self.stats["tier2_errors"] += 1
            return CryptoValidationResponse(
                result=CryptoValidationResult.APPROVE,
                confidence=0.5,
                reason="AI validation timeout - defaulting to APPROVE",
                latency_ms=latency_ms,
                tokens_used=0,
                tier_used="TIER2_AI",
            )
        except Exception as e:
            latency_ms = int((time.time() - start_time) * 1000)
            self.stats["tier2_errors"] += 1
            return CryptoValidationResponse(
                result=CryptoValidationResult.APPROVE,
                confidence=0.5,
                reason=f"AI error ({str(e)[:50]}) - defaulting to APPROVE",
                latency_ms=latency_ms,
                tokens_used=0,
                tier_used="TIER2_AI",
            )

    # -------------------------------------------------------------------------
    # Main validate method (mirrors EOSAIValidator.validate())
    # -------------------------------------------------------------------------

    def validate(
        self,
        signal: CryptoSignalData,
        market_context: Optional[CryptoMarketContextData] = None,
        skip_ai: bool = False,
    ) -> CryptoValidationResponse:
        """
        Validate a crypto signal. Mirrors EOSAIValidator.validate().

        1. Run Tier 1 (always)
        2. If Tier 1 passes and not skip_ai: run Tier 2
        3. Update stats
        """
        start_time = time.time()
        self.stats["total_validations"] += 1

        # Tier 1
        should_reject, reject_reason = self._tier1_validate(signal, market_context)
        if should_reject:
            self.stats["tier1_rejections"] += 1
            response = CryptoValidationResponse(
                result=CryptoValidationResult.REJECT,
                confidence=1.0,
                reason=f"[Tier1] {reject_reason}",
                latency_ms=int((time.time() - start_time) * 1000),
                tokens_used=0,
                tier_used="TIER1_RULES",
            )
            self._record_validation(signal, response)
            return response

        # Tier 2
        if skip_ai:
            response = CryptoValidationResponse(
                result=CryptoValidationResult.APPROVE,
                confidence=0.75,
                reason="[Tier1] Rules passed; AI skipped",
                latency_ms=int((time.time() - start_time) * 1000),
                tokens_used=0,
                tier_used="TIER1_RULES",
            )
        else:
            pair_ctx = CryptoMarketContext.get_pair_context(signal.symbol)
            if market_context is None:
                market_context = self.market_context_mgr.build_context()
            response = self._tier2_ai_validate(signal, market_context, pair_ctx)

            if response.result == CryptoValidationResult.APPROVE:
                self.stats["tier2_approvals"] += 1
            elif response.result == CryptoValidationResult.REJECT:
                self.stats["tier2_rejections"] += 1

        self.stats["total_latency_ms"] += response.latency_ms
        self.stats["total_tokens_used"] += response.tokens_used
        self._record_validation(signal, response)
        return response

    # -------------------------------------------------------------------------
    # Record / stats
    # -------------------------------------------------------------------------

    def _record_validation(
        self,
        signal: CryptoSignalData,
        response: CryptoValidationResponse,
    ) -> None:
        """Store validation in history. Mirrors EOSAIValidator._record_validation()."""
        self.validation_history.append({
            "timestamp":   datetime.now(IST).isoformat(),
            "symbol":      signal.symbol,
            "signal_type": signal.signal_type,
            "result":      response.result.value,
            "confidence":  response.confidence,
            "reason":      response.reason,
            "tier":        response.tier_used,
            "latency_ms":  response.latency_ms,
        })
        # Keep last 100 in memory
        if len(self.validation_history) > 100:
            self.validation_history = self.validation_history[-100:]

    def get_stats(self) -> Dict:
        """Return validation statistics. Mirrors EOSAIValidator.get_stats()."""
        total = self.stats["total_validations"]
        avg_latency = (
            round(self.stats["total_latency_ms"] / total)
            if total else 0
        )
        return {
            **self.stats,
            "avg_latency_ms": avg_latency,
            "approval_rate":  round(
                self.stats["tier2_approvals"] / max(1, total) * 100, 1
            ),
        }

    def print_stats(self) -> None:
        """Print formatted stats. Mirrors EOSAIValidator.print_stats()."""
        s = self.get_stats()
        print(f"\n{'='*50}")
        print(f"  CRYPTO AI VALIDATOR STATS")
        print(f"{'='*50}")
        print(f"  Total validations : {s['total_validations']}")
        print(f"  Tier1 rejections  : {s['tier1_rejections']}")
        print(f"  Tier2 approvals   : {s['tier2_approvals']}")
        print(f"  Tier2 rejections  : {s['tier2_rejections']}")
        print(f"  Tier2 errors      : {s['tier2_errors']}")
        print(f"  Avg latency       : {s['avg_latency_ms']}ms")
        print(f"  Total tokens      : {s['total_tokens_used']}")
        print(f"  Approval rate     : {s['approval_rate']}%")
        print(f"{'='*50}\n")

    def reset_stats(self) -> None:
        """Reset all stats. Mirrors EOSAIValidator.reset_stats()."""
        for key in self.stats:
            self.stats[key] = 0
        self.validation_history = []
