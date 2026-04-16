from datamodel import Order
import json
import math


def rolling_volatility(mids):
    """Standard deviation of consecutive mid-price differences."""
    if len(mids) < 3:
        return 0.0
    returns = [mids[i] - mids[i - 1] for i in range(1, len(mids))]
    n = len(returns)
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / n
    return math.sqrt(var)


class Trader:
    POSITION_LIMIT = {
        "EMERALDS": 80,
        "TOMATOES": 80,
    }

    MID_HISTORY_LEN = 20
    EWMA_SPREAD_ALPHA = 0.2

    REGIME_PARAMS = {
        "L": {"edge_mult": 0.7, "size_mult": 1.4, "ewma_alpha": 0.25},
        "M": {"edge_mult": 1.0, "size_mult": 1.0, "ewma_alpha": 0.35},
        "H": {"edge_mult": 1.5, "size_mult": 0.6, "ewma_alpha": 0.50},
    }

    IMB_TILT = {"EMERALDS": 1.0, "TOMATOES": 1.8}
    POS_PENALTY = {"EMERALDS": 0.8, "TOMATOES": 0.4}

    def run(self, state):
        result = {}
        conversions = 0

        td = {}
        if state.traderData:
            try:
                td = json.loads(state.traderData)
            except Exception:
                td = {}

        emerald_mids = td.get("emerald_mids", [])
        tomato_mids = td.get("tomato_mids", [])
        emerald_avg_spread = td.get("emerald_avg_spread", 0.0)
        tomato_avg_spread = td.get("tomato_avg_spread", 0.0)
        tomato_ewma = td.get("tomato_ewma", None)

        # ----- helpers -----
        def best_bid_ask(od):
            bb = max(od.buy_orders) if od.buy_orders else None
            ba = min(od.sell_orders) if od.sell_orders else None
            return bb, ba

        def buy_cap(product, pos):
            return max(0, self.POSITION_LIMIT[product] - pos)

        def sell_cap(product, pos):
            return max(0, self.POSITION_LIMIT[product] + pos)

        def microprice(bp, ap, bv, av):
            total = bv + av
            if total > 0:
                return (ap * bv + bp * av) / total
            return (bp + ap) / 2.0

        def classify(vol, avg_sp):
            ratio = vol / avg_sp if avg_sp > 0 else 1.0
            if ratio < 0.3:
                return "L"
            if ratio > 0.7:
                return "H"
            return "M"

        # ----- per-product logic -----
        for product, od in state.order_depths.items():
            orders = []
            pos = state.position.get(product, 0)
            bb, ba = best_bid_ask(od)

            if bb is None or ba is None:
                result[product] = orders
                continue

            spread = ba - bb
            bv = od.buy_orders[bb]
            av = -od.sell_orders[ba]
            mid = (bb + ba) / 2.0

            # --- update rolling history & spread EWMA ---
            if product == "EMERALDS":
                emerald_mids.append(mid)
                if len(emerald_mids) > self.MID_HISTORY_LEN:
                    emerald_mids = emerald_mids[-self.MID_HISTORY_LEN:]
                emerald_avg_spread = (
                    self.EWMA_SPREAD_ALPHA * spread
                    + (1.0 - self.EWMA_SPREAD_ALPHA) * emerald_avg_spread
                )
                mids_list = emerald_mids
                avg_sp = emerald_avg_spread
            else:
                tomato_mids.append(mid)
                if len(tomato_mids) > self.MID_HISTORY_LEN:
                    tomato_mids = tomato_mids[-self.MID_HISTORY_LEN:]
                tomato_avg_spread = (
                    self.EWMA_SPREAD_ALPHA * spread
                    + (1.0 - self.EWMA_SPREAD_ALPHA) * tomato_avg_spread
                )
                mids_list = tomato_mids
                avg_sp = tomato_avg_spread

            # --- regime ---
            vol = rolling_volatility(mids_list)
            has_history = len(mids_list) >= 5
            regime = classify(vol, avg_sp) if has_history else "M"
            params = self.REGIME_PARAMS[regime]
            edge_mult = params["edge_mult"]
            size_mult = params["size_mult"]
            ewma_alpha = params["ewma_alpha"]

            # --- fair value ---
            imb = (bv - av) / (bv + av + 1e-9)

            if product == "EMERALDS":
                fair = 10000.0
                adjusted_fair = fair - self.POS_PENALTY[product] * pos
                adjusted_fair += self.IMB_TILT[product] * imb

                base_edge = max(0.85, spread * 0.22) * edge_mult

            else:  # TOMATOES
                micro = microprice(bb, ba, bv, av)
                if tomato_ewma is None:
                    tomato_ewma = micro
                tomato_ewma = ewma_alpha * micro + (1.0 - ewma_alpha) * tomato_ewma
                fair = tomato_ewma

                adjusted_fair = fair - self.POS_PENALTY[product] * pos
                adjusted_fair += self.IMB_TILT[product] * imb

                base_edge = max(1.35, spread / 2.75) * edge_mult

            # --- aggressive take ---
            take_size = min(8, round(8 * size_mult))

            if ba < adjusted_fair - base_edge:
                qty = min(av, buy_cap(product, pos), take_size)
                if abs(pos) > 20:
                    qty = min(qty, 5)
                if qty > 0:
                    orders.append(Order(product, ba, qty))
                    pos += qty

            if bb > adjusted_fair + base_edge:
                qty = min(bv, sell_cap(product, pos), take_size)
                if abs(pos) > 20:
                    qty = min(qty, 5)
                if qty > 0:
                    orders.append(Order(product, bb, -qty))
                    pos -= qty

            # --- passive market making ---
            bid_quote = min(bb + 1, math.floor(adjusted_fair))
            ask_quote = max(ba - 1, math.ceil(adjusted_fair))

            passive_size = min(6, round(6 * size_mult))
            bid_size = min(buy_cap(product, pos), passive_size)
            ask_size = min(sell_cap(product, pos), passive_size)

            # position skew: reduce the side that grows |pos|, boost the other
            if pos > 12:
                bid_size = min(bid_size, max(1, passive_size // 3))
                ask_size = min(sell_cap(product, pos), passive_size + 2)
            elif pos > 6:
                bid_size = min(bid_size, passive_size // 2)
            elif pos < -12:
                ask_size = min(ask_size, max(1, passive_size // 3))
                bid_size = min(buy_cap(product, pos), passive_size + 2)
            elif pos < -6:
                ask_size = min(ask_size, passive_size // 2)

            if bid_quote < ask_quote:
                if bid_size > 0:
                    orders.append(Order(product, bid_quote, bid_size))
                if ask_size > 0:
                    orders.append(Order(product, ask_quote, -ask_size))

            result[product] = orders

            print(
                f"[C] {product} t={state.timestamp} "
                f"fair={adjusted_fair:.1f} vol={vol:.2f} regime={regime} "
                f"edge_m={edge_mult:.1f} pos={pos}"
            )

        # ----- persist state -----
        td["emerald_mids"] = emerald_mids
        td["tomato_mids"] = tomato_mids
        td["emerald_avg_spread"] = emerald_avg_spread
        td["tomato_avg_spread"] = tomato_avg_spread
        td["tomato_ewma"] = tomato_ewma

        return result, conversions, json.dumps(td)
