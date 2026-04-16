from datamodel import Order
import json
import math


class Trader:
    POSITION_LIMIT = 80
    EMERALD_FAIR = 10000
    TOMATO_EWMA_ALPHA = 0.35
    POS_PENALTY = 1.0
    TAKE_EDGE = 0.5

    PASSIVE_LEVELS = [
        (0, 0.5),   # tight: 50% of remaining capacity
        (1, 0.3),   # mid:   30%
        (2, 0.2),   # wide:  20%
    ]
    SKEW_POS_THRESHOLD = 30

    def run(self, state):
        result = {}
        conversions = 0

        trader_data = {}
        if state.traderData:
            try:
                trader_data = json.loads(state.traderData)
            except (json.JSONDecodeError, TypeError):
                pass

        tomato_ewma = trader_data.get("tomato_ewma", None)

        for product in ["EMERALDS", "TOMATOES"]:
            if product not in state.order_depths:
                continue

            orders = []
            depth = state.order_depths[product]
            position = state.position.get(product, 0)
            buy_orders = depth.buy_orders
            sell_orders = depth.sell_orders

            if not buy_orders or not sell_orders:
                result[product] = orders
                continue

            best_bid = max(buy_orders.keys())
            best_ask = min(sell_orders.keys())
            microprice = (
                best_bid * abs(sell_orders[best_ask])
                + best_ask * abs(buy_orders[best_bid])
            ) / (abs(buy_orders[best_bid]) + abs(sell_orders[best_ask]) + 1e-9)

            if product == "EMERALDS":
                fair = self.EMERALD_FAIR
            else:
                if tomato_ewma is None:
                    tomato_ewma = microprice
                else:
                    tomato_ewma += self.TOMATO_EWMA_ALPHA * (microprice - tomato_ewma)
                fair = tomato_ewma

            adjusted_fair = fair - self.POS_PENALTY * position

            buy_cap = self.POSITION_LIMIT - position
            sell_cap = self.POSITION_LIMIT + position

            # --- Aggressive take: sweep all levels ---
            for ask_price in sorted(sell_orders.keys()):
                if ask_price >= adjusted_fair - self.TAKE_EDGE:
                    break
                if buy_cap <= 0:
                    break
                vol = abs(sell_orders[ask_price])
                qty = min(vol, buy_cap)
                orders.append(Order(product, ask_price, qty))
                buy_cap -= qty

            for bid_price in sorted(buy_orders.keys(), reverse=True):
                if bid_price <= adjusted_fair + self.TAKE_EDGE:
                    break
                if sell_cap <= 0:
                    break
                vol = abs(buy_orders[bid_price])
                qty = min(vol, sell_cap)
                orders.append(Order(product, bid_price, -qty))
                sell_cap -= qty

            # --- Multi-level passive quoting ---
            pending_buy = sum(o.quantity for o in orders if o.quantity > 0)
            pending_sell = sum(-o.quantity for o in orders if o.quantity < 0)
            remaining_buy = self.POSITION_LIMIT - position - pending_buy
            remaining_sell = self.POSITION_LIMIT + position - pending_sell

            if abs(position) > self.SKEW_POS_THRESHOLD:
                # Skewed: only quote the side that flattens, at 2 levels
                if position > 0:
                    self._add_skew_quotes(
                        orders, product, adjusted_fair, remaining_sell, side="sell"
                    )
                else:
                    self._add_skew_quotes(
                        orders, product, adjusted_fair, remaining_buy, side="buy"
                    )
            else:
                buy_placed = 0
                sell_placed = 0
                for offset, frac in self.PASSIVE_LEVELS:
                    bid_price = math.floor(adjusted_fair) - offset
                    ask_price = math.ceil(adjusted_fair) + offset
                    bid_size = int(remaining_buy * frac)
                    ask_size = int(remaining_sell * frac)

                    bid_size = min(bid_size, remaining_buy - buy_placed)
                    ask_size = min(ask_size, remaining_sell - sell_placed)

                    if bid_size > 0:
                        orders.append(Order(product, bid_price, bid_size))
                        buy_placed += bid_size
                    if ask_size > 0:
                        orders.append(Order(product, ask_price, -ask_size))
                        sell_placed += ask_size

            capacity_used = abs(position) + max(
                sum(o.quantity for o in orders if o.quantity > 0),
                sum(-o.quantity for o in orders if o.quantity < 0),
            )
            print(
                f"[F] {product} t={state.timestamp} fair={adjusted_fair:.1f} "
                f"pos={position} cap_used={capacity_used}/{self.POSITION_LIMIT}"
            )

            result[product] = orders

        trader_data["tomato_ewma"] = tomato_ewma
        return result, conversions, json.dumps(trader_data)

    def _add_skew_quotes(self, orders, product, adjusted_fair, remaining, side):
        """When heavily skewed, quote only the flattening side at 2 levels."""
        if remaining <= 0:
            return
        size_tight = int(remaining * 0.6)
        size_wide = remaining - size_tight

        if side == "sell":
            p1 = math.ceil(adjusted_fair)
            p2 = math.ceil(adjusted_fair) + 1
            if size_tight > 0:
                orders.append(Order(product, p1, -size_tight))
            if size_wide > 0:
                orders.append(Order(product, p2, -size_wide))
        else:
            p1 = math.floor(adjusted_fair)
            p2 = math.floor(adjusted_fair) - 1
            if size_tight > 0:
                orders.append(Order(product, p1, size_tight))
            if size_wide > 0:
                orders.append(Order(product, p2, size_wide))
