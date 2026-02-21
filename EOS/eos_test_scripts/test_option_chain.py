# Last updated: 2026-02-21
"""
Test script for EOS Option Chain Manager.
Tests real option chain data fetching from Dhan API.

Usage:
    python -m EOS.eos_test_scripts.test_option_chain
"""

from ..eos_option_chain import EOSOptionChainManager, OptionData, ATMOption
from ..config import FNO_STOCKS


def test_option_chain_manager():
    """Test the option chain manager with real API calls."""
    print("=" * 60)
    print("EOS OPTION CHAIN MANAGER TEST")
    print("=" * 60)

    manager = EOSOptionChainManager()

    # Test with a few stocks
    test_symbols = ["RELIANCE", "HDFCBANK", "TCS"]

    for symbol in test_symbols:
        print(f"\n{'='*50}")
        print(f"Testing: {symbol}")
        print(f"{'='*50}")

        # Test 1: Get expiry list
        print(f"\n[1] Fetching expiry list for {symbol}...")
        expiries = manager.get_expiry_list(symbol)
        if expiries:
            print(f"   ✅ Found {len(expiries)} expiry dates")
            print(f"   First 3: {expiries[:3]}")
        else:
            print(f"   ❌ No expiries found")
            continue

        # Test 2: Get nearest monthly expiry
        print(f"\n[2] Getting nearest monthly expiry...")
        monthly_expiry = manager.get_nearest_monthly_expiry(symbol)
        if monthly_expiry:
            print(f"   ✅ Monthly expiry: {monthly_expiry}")
        else:
            print(f"   ❌ Could not determine monthly expiry")
            continue

        # Test 3: Fetch option chain
        print(f"\n[3] Fetching option chain...")
        chain_data = manager.fetch_option_chain(symbol, monthly_expiry)
        if chain_data:
            print(f"   ✅ Got option chain")
            print(f"   Spot price: ₹{chain_data.get('spot_price', 0):.2f}")
            strikes = list(chain_data.get('chain', {}).keys())
            print(f"   Number of strikes: {len(strikes)}")
            if strikes:
                print(f"   Sample strikes: {strikes[:5]}...")
        else:
            print(f"   ❌ Failed to fetch option chain")
            continue

        # Test 4: Get ATM options
        print(f"\n[4] Getting ATM options...")
        atm = manager.get_atm_options(symbol)
        if atm:
            print(f"   ✅ ATM Strike: ₹{atm.atm_strike:.2f}")
            if atm.call:
                print(f"   CALL LTP: ₹{atm.call.ltp:.2f} | IV: {atm.call.iv:.2f}%")
            if atm.put:
                print(f"   PUT LTP: ₹{atm.put.ltp:.2f} | IV: {atm.put.iv:.2f}%")
        else:
            print(f"   ❌ Failed to get ATM options")
            continue

        # Test 5: Get option prices
        print(f"\n[5] Testing get_option_price()...")
        call_price = manager.get_option_price(symbol, "CALL")
        put_price = manager.get_option_price(symbol, "PUT")
        print(f"   CALL price: ₹{call_price:.2f}" if call_price else "   CALL: N/A")
        print(f"   PUT price: ₹{put_price:.2f}" if put_price else "   PUT: N/A")

        # Test 6: Get security IDs for ATM options
        print(f"\n[6] Testing get_atm_security_ids()...")
        sec_ids = manager.get_atm_security_ids(symbol)
        if sec_ids["call"]:
            print(f"   ✅ CALL Security ID: {sec_ids['call']}")
        else:
            print(f"   ❌ CALL Security ID not found")
        if sec_ids["put"]:
            print(f"   ✅ PUT Security ID: {sec_ids['put']}")
        else:
            print(f"   ❌ PUT Security ID not found")

        # Test 7: Verify security ID lookup directly
        if atm:
            print(f"\n[7] Testing direct security ID lookup...")
            call_sid = manager.get_option_security_id(
                symbol, atm.atm_strike, "CE", atm.expiry
            )
            put_sid = manager.get_option_security_id(
                symbol, atm.atm_strike, "PE", atm.expiry
            )
            print(f"   Strike: {atm.atm_strike}, Expiry: {atm.expiry}")
            print(f"   CE Security ID: {call_sid}")
            print(f"   PE Security ID: {put_sid}")

        # Print detailed ATM options
        manager.print_atm_options(symbol)

        print(f"\n✅ {symbol} test PASSED")

    print("\n" + "=" * 60)
    print("ALL TESTS COMPLETED")
    print("=" * 60)


if __name__ == "__main__":
    test_option_chain_manager()

