"""
Brute-force optimizer for IMC Prosperity manual trading.
Sweeps all (price, quantity) combinations for each product and reports
the order that maximizes profit. Includes prices above the sell price
to test aggressive priority bidding.
"""

import time
import sys
from Manual import AuctionExchange

# ---------------------------------------------------------------------------
# Order book definitions
# ---------------------------------------------------------------------------

FLAX_BIDS = [(30, 30_000), (29, 5_000), (28, 12_000), (27, 28_000)]
FLAX_ASKS = [(28, 40_000), (31, 20_000), (32, 20_000), (33, 30_000)]
FLAX_SELL_PRICE = 30
FLAX_FEE = 0.0
FLAX_MAX_SUPPLY = 110_000  # 40k+20k+20k+30k

MUSHROOM_BIDS = [
    (20, 43_000), (19, 17_000), (18, 6_000), (17, 5_000),
    (16, 10_000), (15, 5_000), (14, 10_000), (13, 7_000),
]
MUSHROOM_ASKS = [
    (12, 20_000), (13, 25_000), (14, 35_000), (15, 6_000),
    (16, 5_000), (17, 0), (18, 10_000), (19, 12_000),
]
MUSHROOM_SELL_PRICE = 20
MUSHROOM_FEE = 0.10
MUSHROOM_MAX_SUPPLY = 113_000  # 20k+25k+35k+6k+5k+0+10k+12k


def compute_profit(existing_bids, existing_asks, my_price, my_qty, sell_price, fee):
    ex = AuctionExchange()
    for p, v in existing_bids:
        ex.add_bid(p, v)
    for p, v in existing_asks:
        ex.add_ask(p, v)
    ex.add_bid(my_price, my_qty, "ME")

    result = ex.clear()
    my_fill = next((f for f in result.bid_fills if f.label == "ME"), None)
    if my_fill is None or my_fill.volume_filled == 0:
        return 0.0, 0, result.clearing_price
    qty = my_fill.volume_filled
    profit = qty * (sell_price - result.clearing_price - fee)
    return profit, qty, result.clearing_price


def optimize_product(name, existing_bids, existing_asks, sell_price, fee, price_range, qty_range):
    total_evals = len(price_range) * len(qty_range)
    print(f"\n  {name}: {total_evals:,} evaluations to run")
    print(f"  Prices {price_range.start}..{price_range.stop - 1}, "
          f"Qty {qty_range.start:,}..{qty_range.stop - 1:,} (step={qty_range.step})")

    best_profit = 0.0
    best_params = None
    evals_done = 0
    t0 = time.time()
    last_report = 0

    for price in price_range:
        for qty in qty_range:
            profit, fill, clearing = compute_profit(
                existing_bids, existing_asks, price, qty, sell_price, fee
            )
            if profit > best_profit:
                best_profit = profit
                best_params = (price, qty, fill, clearing, profit)

            evals_done += 1
            if evals_done - last_report >= 100_000:
                elapsed = time.time() - t0
                rate = evals_done / elapsed
                remaining = (total_evals - evals_done) / rate
                cur_best = f"profit={best_params[4]:,.2f} @ price={best_params[0]} qty={best_params[1]:,}" if best_params else "none yet"
                print(f"    [{evals_done:>10,} / {total_evals:,}]  "
                      f"{evals_done*100/total_evals:5.1f}%  "
                      f"ETA {remaining:5.0f}s  "
                      f"best so far: {cur_best}",
                      flush=True)
                last_report = evals_done

    elapsed = time.time() - t0

    print(f"\n{'='*65}")
    print(f"  {name} — RESULT  ({elapsed:.1f}s, {total_evals:,} evals)")
    print(f"{'='*65}")
    if best_params is None:
        print("  No profitable order found.")
    else:
        price, qty, fill, clearing, profit = best_params
        print(f"  Submit BUY  : price={price}, quantity={qty:,}")
        print(f"  Clearing    : {clearing}")
        print(f"  Your fill   : {fill:,}")
        print(f"  Profit/unit : {sell_price} - {clearing} - {fee} = {sell_price - clearing - fee:.2f}")
        print(f"  NET PROFIT  : {profit:,.2f} XIRECs")

    return best_profit, best_params


if __name__ == "__main__":
    print("=" * 65)
    print("  IMC Prosperity Manual Trading — Full Brute-Force Optimizer")
    print("  Testing bid prices ABOVE sell price for priority advantage")
    print("=" * 65)

    # ---- Dryland Flax ----
    # Prices 27..40 (well above sell price of 30), qty 1..110k
    flax_prices = range(27, 41)
    flax_qtys = range(1, FLAX_MAX_SUPPLY + 1)
    flax_total = len(flax_prices) * len(flax_qtys)

    # ---- Ember Mushroom ----
    # Prices 12..30 (well above sell price of 20), qty 1..113k
    mush_prices = range(12, 31)
    mush_qtys = range(1, MUSHROOM_MAX_SUPPLY + 1)
    mush_total = len(mush_prices) * len(mush_qtys)

    grand_total = flax_total + mush_total
    print(f"\n  Flax:     {flax_total:>12,} evals  (prices {flax_prices.start}-{flax_prices.stop-1}, qty 1-{FLAX_MAX_SUPPLY:,})")
    print(f"  Mushroom: {mush_total:>12,} evals  (prices {mush_prices.start}-{mush_prices.stop-1}, qty 1-{MUSHROOM_MAX_SUPPLY:,})")
    print(f"  Total:    {grand_total:>12,} evals")
    print(f"  Est. time: ~{grand_total / 5500 / 60:.0f} min (based on ~5.5k evals/sec)\n")

    p1, params1 = optimize_product(
        "Dryland Flax", FLAX_BIDS, FLAX_ASKS,
        FLAX_SELL_PRICE, FLAX_FEE, flax_prices, flax_qtys,
    )

    p2, params2 = optimize_product(
        "Ember Mushroom", MUSHROOM_BIDS, MUSHROOM_ASKS,
        MUSHROOM_SELL_PRICE, MUSHROOM_FEE, mush_prices, mush_qtys,
    )

    print(f"\n{'='*65}")
    print(f"  COMBINED OPTIMAL PROFIT: {p1 + p2:,.2f} XIRECs")
    print(f"{'='*65}")
