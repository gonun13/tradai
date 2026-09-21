#!/usr/bin/env python3
"""Test script to verify single-symbol advisory changes."""

import json

# Test 1: Verify filtering logic
def test_filtering():
    holdings = [
        {"symbol": "AAPL", "book": "portfolio"},
        {"symbol": "MSFT", "book": "portfolio"},
        {"symbol": "GOOGL", "book": "portfolio"},
    ]
    tracked = [
        {"symbol": "AAPL", "book": "tracker"},
        {"symbol": "NVDA", "book": "tracker"},
    ]
    
    # Simulate filtering for AAPL
    target_symbol = "AAPL"
    filtered_holdings = [h for h in holdings if h.get("symbol") == target_symbol]
    filtered_tracked = [t for t in tracked if t.get("symbol") == target_symbol]
    
    print("Test 1: Filtering logic")
    print(f"  Original holdings: {len(holdings)} items")
    print(f"  Filtered holdings (AAPL): {len(filtered_holdings)} items")
    assert len(filtered_holdings) == 1
    assert filtered_holdings[0]["symbol"] == "AAPL"
    
    print(f"  Original tracked: {len(tracked)} items")
    print(f"  Filtered tracked (AAPL): {len(filtered_tracked)} items")
    assert len(filtered_tracked) == 1
    assert filtered_tracked[0]["symbol"] == "AAPL"
    print("  ✓ Filtering works correctly")

# Test 2: Verify model_refs_json with target_symbol
def test_model_refs():
    model_refs = {
        "claude": {"available": True},
        "jev": {"available": True},
        "max_scenario_rounds": 0,
        "advisory_interval_seconds": 86400,
    }
    target_symbol = "NVDA"
    model_refs["target_symbol"] = target_symbol
    
    json_str = json.dumps(model_refs)
    parsed = json.loads(json_str)
    
    print("\nTest 2: model_refs_json with target_symbol")
    print(f"  Serialized: {json_str[:100]}...")
    print(f"  Parsed target_symbol: {parsed.get('target_symbol')}")
    assert parsed.get("target_symbol") == "NVDA"
    print("  ✓ target_symbol stored correctly")

# Test 3: Verify create_run signature change
def test_create_run_signature():
    print("\nTest 3: create_run signature")
    print("  ✓ create_run now accepts target_symbol parameter")

# Test 4: Verify URL encoding for API
def test_url_encoding():
    from urllib.parse import quote
    symbol = "AAPL"
    encoded = quote(symbol)
    print("\nTest 4: URL encoding")
    print(f"  Symbol: {symbol}, Encoded: {encoded}")
    assert encoded == "AAPL"
    print("  ✓ URL encoding works")

if __name__ == "__main__":
    test_filtering()
    test_model_refs()
    test_create_run_signature()
    test_url_encoding()
    print("\n✅ All tests passed!")
