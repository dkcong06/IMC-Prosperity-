from datamodel import Order
import json
import math


class Trader:
    POSITION_LIMIT = 80

    # EMERALDS: pegged at 10,000 with certainty.
    # Key insight: do NOT apply position penalty to fair value.
    # For a pegged product, fair is always 10,000 regardless of inventory.
    # Manage risk through SIZE SKEWING only, keeping quotes centered on true fair.
    EMERALD_FAIR = 10000
    EMERALD_TAKE_EDGE = 5

    # TOMATOES: floating price, uncertain fair — use EWMA + standard penalties.
    TOMATO_EWMA_ALPHA = 0.38
    TOMATO_POS_PENALTY = 0.5
    TOMATO_IMB_TILT = 1.5
    TOMATO_FADE_COEF = 0.35
    TOMATO_TAKE_MIN_EDGE = 1.2
    TOMATO_TAKE_EDGE_FRAC = 1 / 3.0

    def run(self, state):
        result = {}
        conversions = 0

        td = {}
        if state.traderData:
            try:
                td = json.loads(state.traderData)
            except Exception:
                td = {}

        tomato_ewma = td.get("tomato_ewma")

        for product, depth in state.order_depths.items():
            orders = []
            pos = state.position.get(product, 0)

            if not depth.buy_orders or not depth.sell_orders:
                result[product] = orders
                continue

            best_bid = max(depth.buy_orders)
            best_ask = min(depth.sell_orders)
            spread = best_ask - best_bid
            bid_vol = depth.buy_orders[best_bid]
            ask_vol = -depth.sell_orders[best_ask]

            buy_cap = self.POSITION_LIMIT - pos
            sell_cap = self.POSITION_LIMIT + pos

            if product == "EMERALDS":
                orders, pos = self._trade_emeralds(
                    depth, pos, best_bid, best_ask, spread, buy_cap, sell_cap,
                    state.timestamp,
                )

            elif product == "TOMATOES":
                orders, pos, tomato_ewma = self._trade_tomatoes(
                    depth, pos, best_bid, best_ask, spread,
                    bid_vol, ask_vol, buy_cap, sell_cap,
                    tomato_ewma, state.timestamp,
                )

            result[product] = orders

        td["tomato_ewma"] = tomato_ewma
        return result, conversions, json.dumps(td)

    def _trade_emeralds(self, depth, pos, best_bid, best_ask, spread, buy_cap, sell_cap, timestamp):
        orders = []
        fair = self.EMERALD_FAIR

        # Aggressive take: sweep all levels with clear edge from the peg.
        # With typical spread of 16, this only fires when spread narrows significantly.
        for price in sorted(depth.sell_orders):
            if price > fair - self.EMERALD_TAKE_EDGE or buy_cap <= 0:
                break
            vol = abs(depth.sell_orders[price])
            qty = min(vol, buy_cap)
            if qty > 0:
                orders.append(Order("EMERALDS", price, qty))
                buy_cap -= qty
                pos += qty

        for price in sorted(depth.buy_orders, reverse=True):
            if price < fair + self.EMERALD_TAKE_EDGE or sell_cap <= 0:
                break
            vol = depth.buy_orders[price]
            qty = min(vol, sell_cap)
            if qty > 0:
                orders.append(Order("EMERALDS", price, -qty))
                sell_cap -= qty
                pos -= qty

        # Passive: penny-jump but never cross fair.
        # Quotes stay centered on 10,000 at ALL position levels — only sizes change.
        bid_price = min(best_bid + 1, fair - 1)
        ask_price = max(best_ask - 1, fair + 1)

        bid_size, ask_size = self._emerald_sizes(pos, buy_cap, sell_cap)

        if bid_price < ask_price:
            if bid_size > 0:
                orders.append(Order("EMERALDS", bid_price, bid_size))
            if ask_size > 0:
                orders.append(Order("EMERALDS", ask_price, -ask_size))

        print(
            f"[K] EMERALDS t={timestamp} pos={pos} "
            f"bid={bid_price}x{bid_size} ask={ask_price}x{ask_size} spread={spread}"
        )
        return orders, pos

    def _trade_tomatoes(self, depth, pos, best_bid, best_ask, spread,
                        bid_vol, ask_vol, buy_cap, sell_cap, tomato_ewma, timestamp):
        orders = []

        micro = (best_ask * bid_vol + best_bid * ask_vol) / (bid_vol + ask_vol + 1e-9)
        if tomato_ewma is None:
            tomato_ewma = micro
        tomato_ewma += self.TOMATO_EWMA_ALPHA * (micro - tomato_ewma)
        fair = tomato_ewma

        imb = (bid_vol - ask_vol) / (bid_vol + ask_vol + 1e-9)
        adjusted_fair = fair + self.TOMATO_IMB_TILT * imb
        adjusted_fair -= self.TOMATO_POS_PENALTY * pos

        momentum = micro - fair
        fade_buy = max(0.0, momentum) * self.TOMATO_FADE_COEF
        fade_sell = max(0.0, -momentum) * self.TOMATO_FADE_COEF

        threshold = max(self.TOMATO_TAKE_MIN_EDGE, spread * self.TOMATO_TAKE_EDGE_FRAC)

        if best_ask < adjusted_fair - threshold - fade_buy:
            qty = min(ask_vol, buy_cap, 8)
            if abs(pos) > 20:
                qty = min(qty, 5)
            if qty > 0:
                orders.append(Order("TOMATOES", best_ask, qty))
                buy_cap -= qty
                pos += qty

        if best_bid > adjusted_fair + threshold + fade_sell:
            qty = min(bid_vol, sell_cap, 8)
            if abs(pos) > 20:
                qty = min(qty, 5)
            if qty > 0:
                orders.append(Order("TOMATOES", best_bid, -qty))
                sell_cap -= qty
                pos -= qty

        bid_price = min(best_bid + 1, math.floor(adjusted_fair))
        ask_price = max(best_ask - 1, math.ceil(adjusted_fair))

        bid_size, ask_size = self._tomato_sizes(pos, buy_cap, sell_cap)

        if bid_price < ask_price:
            if bid_size > 0:
                orders.append(Order("TOMATOES", bid_price, bid_size))
            if ask_size > 0:
                orders.append(Order("TOMATOES", ask_price, -ask_size))

        print(
            f"[K] TOMATOES t={timestamp} fair={adjusted_fair:.1f} "
            f"pos={pos} imb={imb:.2f} mom={momentum:.1f} spread={spread}"
        )
        return orders, pos, tomato_ewma

    @staticmethod
    def _emerald_sizes(pos, buy_cap, sell_cap):
        """Size skewing for EMERALDS. No fair-value penalty — inventory managed here.

        Since EMERALDS is pegged, large positions are safe (mark-to-market is
        always ~10,000). We use bigger base sizes than TOMATOES and skew
        gradually toward the reducing side as inventory grows.
        """
        abs_pos = abs(pos)

        if abs_pos <= 15:
            add_size = 20
            red_size = 20
        elif abs_pos <= 30:
            add_size = 10
            red_size = 25
        elif abs_pos <= 50:
            add_size = 3
            red_size = 30
        else:
            add_size = 0
            red_size = 35

        if pos > 0:
            return min(add_size, buy_cap), min(red_size, sell_cap)
        elif pos < 0:
            return min(red_size, buy_cap), min(add_size, sell_cap)
        return min(add_size, buy_cap), min(add_size, sell_cap)

    @staticmethod
    def _tomato_sizes(pos, buy_cap, sell_cap):
        """Conservative sizing for TOMATOES with graduated position skew."""
        abs_pos = abs(pos)

        if abs_pos <= 10:
            add_size = 8
            red_size = 8
        elif abs_pos <= 20:
            add_size = 5
            red_size = 11
        elif abs_pos <= 35:
            add_size = 2
            red_size = 14
        else:
            add_size = 0
            red_size = 16

        if pos > 0:
            return min(add_size, buy_cap), min(red_size, sell_cap)
        elif pos < 0:
            return min(red_size, buy_cap), min(add_size, sell_cap)
        return min(add_size, buy_cap), min(add_size, sell_cap)
