from dataclasses import dataclass, field


@dataclass
class Order:
    price: float
    volume: int
    label: str
    time_priority: int  # lower = earlier


@dataclass
class Fill:
    label: str
    price: float
    volume_requested: int
    volume_filled: int


@dataclass
class ClearingResult:
    clearing_price: float
    total_volume: int
    bid_fills: list[Fill]
    ask_fills: list[Fill]


class AuctionExchange:
    def __init__(self):
        self._bids: list[Order] = []
        self._asks: list[Order] = []
        self._time_counter = 0

    def add_bid(self, price: float, volume: int, label: str = ""):
        self._bids.append(Order(price, volume, label, self._time_counter))
        self._time_counter += 1

    def add_ask(self, price: float, volume: int, label: str = ""):
        self._asks.append(Order(price, volume, label, self._time_counter))
        self._time_counter += 1

    def clear(self) -> ClearingResult:
        price_levels = sorted(
            {o.price for o in self._bids} | {o.price for o in self._asks}
        )

        best_price = None
        best_volume = -1

        for p in price_levels:
            cum_demand = sum(o.volume for o in self._bids if o.price >= p)
            cum_supply = sum(o.volume for o in self._asks if o.price <= p)
            tradeable = min(cum_demand, cum_supply)
            # Max volume, tie-break to higher price (>= since we iterate ascending)
            if tradeable >= best_volume:
                best_volume = tradeable
                best_price = p

        if best_price is None or best_volume <= 0:
            return ClearingResult(0, 0, [], [])

        return self._allocate_fills(best_price, best_volume)

    def _allocate_fills(self, clearing_price: float, total_volume: int) -> ClearingResult:
        qualifying_bids = [o for o in self._bids if o.price >= clearing_price]
        qualifying_asks = [o for o in self._asks if o.price <= clearing_price]

        cum_demand = sum(o.volume for o in qualifying_bids)
        cum_supply = sum(o.volume for o in qualifying_asks)

        # Bids: best price = highest price first, then earliest time
        sorted_bids = sorted(qualifying_bids, key=lambda o: (-o.price, o.time_priority))
        # Asks: best price = lowest price first, then earliest time
        sorted_asks = sorted(qualifying_asks, key=lambda o: (o.price, o.time_priority))

        bid_fills = self._fill_side(sorted_bids, total_volume)
        ask_fills = self._fill_side(sorted_asks, total_volume)

        return ClearingResult(clearing_price, total_volume, bid_fills, ask_fills)

    @staticmethod
    def _fill_side(sorted_orders: list[Order], remaining: int) -> list[Fill]:
        fills = []
        for o in sorted_orders:
            filled = min(o.volume, remaining)
            fills.append(Fill(o.label, o.price, o.volume, filled))
            remaining -= filled
            if remaining <= 0:
                break
        return fills


# ---------------------------------------------------------------------------
# Product analysis helper
# ---------------------------------------------------------------------------

def analyze_product(
    name: str,
    exchange: AuctionExchange,
    post_auction_price: float,
    fee_per_unit: float,
    my_label: str,
):
    result = exchange.clear()

    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"{'='*60}")
    print(f"  Clearing price : {result.clearing_price}")
    print(f"  Total volume   : {result.total_volume:,}")
    print()

    print("  Bid fills:")
    for f in result.bid_fills:
        tag = " <-- YOU" if f.label == my_label else ""
        print(f"    {f.label:>20s}  |  requested {f.volume_requested:>7,}  |  filled {f.volume_filled:>7,}{tag}")

    print()
    print("  Ask fills:")
    for f in result.ask_fills:
        print(f"    {f.label:>20s}  |  requested {f.volume_requested:>7,}  |  filled {f.volume_filled:>7,}")

    my_fill = next((f for f in result.bid_fills if f.label == my_label), None)
    if my_fill is None or my_fill.volume_filled == 0:
        print(f"\n  You got NO fill.")
        return 0.0

    qty = my_fill.volume_filled
    cost = qty * result.clearing_price
    revenue = qty * post_auction_price
    fees = qty * fee_per_unit
    profit = revenue - cost - fees

    print(f"\n  Your fill       : {qty:,} units @ clearing price {result.clearing_price}")
    print(f"  Cost            : {cost:,.2f}")
    print(f"  Revenue (sell)  : {qty:,} x {post_auction_price} = {revenue:,.2f}")
    print(f"  Fees            : {qty:,} x {fee_per_unit} = {fees:,.2f}")
    print(f"  NET PROFIT      : {profit:,.2f} XIRECs")

    return profit


# ---------------------------------------------------------------------------
# Order books from the images + recommended user orders
# ---------------------------------------------------------------------------

def run_flax():
    ex = AuctionExchange()

    # Existing bids (added first = higher time priority)
    ex.add_bid(30, 30_000, "bid@30")
    ex.add_bid(29, 5_000, "bid@29")
    ex.add_bid(28, 12_000, "bid@28")
    ex.add_bid(27, 28_000, "bid@27")

    # Existing asks
    ex.add_ask(28, 40_000, "ask@28")
    ex.add_ask(31, 20_000, "ask@31")
    ex.add_ask(32, 20_000, "ask@32")
    ex.add_ask(33, 30_000, "ask@33")

    # MY ORDER — last to submit
    ex.add_bid(30, 9_999, "ME")

    return analyze_product(
        name="Dryland Flax",
        exchange=ex,
        post_auction_price=30,
        fee_per_unit=0,
        my_label="ME",
    )


def run_mushroom():
    ex = AuctionExchange()

    # Existing bids
    ex.add_bid(20, 43_000, "bid@20")
    ex.add_bid(19, 17_000, "bid@19")
    ex.add_bid(18, 6_000, "bid@18")
    ex.add_bid(17, 5_000, "bid@17")
    ex.add_bid(16, 10_000, "bid@16")
    ex.add_bid(15, 5_000, "bid@15")
    ex.add_bid(14, 10_000, "bid@14")
    ex.add_bid(13, 7_000, "bid@13")

    # Existing asks
    ex.add_ask(12, 20_000, "ask@12")
    ex.add_ask(13, 25_000, "ask@13")
    ex.add_ask(14, 35_000, "ask@14")
    ex.add_ask(15, 6_000, "ask@15")
    ex.add_ask(16, 5_000, "ask@16")
    ex.add_ask(17, 0, "ask@17")
    ex.add_ask(18, 10_000, "ask@18")
    ex.add_ask(19, 12_000, "ask@19")

    # MY ORDER — last to submit
    ex.add_bid(18, 35_000, "ME")

    return analyze_product(
        name="Ember Mushroom",
        exchange=ex,
        post_auction_price=20,
        fee_per_unit=0.10,
        my_label="ME",
    )


if __name__ == "__main__":
    p1 = run_flax()
    p2 = run_mushroom()

    print(f"\n{'='*60}")
    print(f"  COMBINED PROFIT: {p1 + p2:,.2f} XIRECs")
    print(f"{'='*60}")
