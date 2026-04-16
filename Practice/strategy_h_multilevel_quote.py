from datamodel import Order
import json
import math


class Trader:
    POSITION_LIMIT = 80

    EMERALD_FAIR = 10000
    TOMATO_EWMA_ALPHA = 0.30
    POS_PENALTY = 1.2

    GRID_LEVELS = 5
    # Pyramid: more size at tight levels, less at wide
    LEVEL_WEIGHTS = [0.35, 0.25, 0.20, 0.12, 0.08]

    CANCEL_THRESHOLD = 50

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

            if product == "EMERALDS":
                fair = float(self.EMERALD_FAIR)
            else:
                microprice = self._microprice(buy_orders, sell_orders)
                if tomato_ewma is None:
                    tomato_ewma = microprice
                else:
                    tomato_ewma += self.TOMATO_EWMA_ALPHA * (microprice - tomato_ewma)
                fair = tomato_ewma

            adjusted_fair = fair - self.POS_PENALTY * position

            buy_capacity = self.POSITION_LIMIT - position
            sell_capacity = self.POSITION_LIMIT + position

            # Inventory-driven skew (tick shifts)
            if position > 0:
                bid_shift = -(position // 10)
                ask_shift = -(position // 20)
            elif position < 0:
                bid_shift = -(position // 20)   # position is negative, so this shifts up
                ask_shift = -(position // 10)
            else:
                bid_shift = 0
                ask_shift = 0

            # Hard cutoff: cancel the side that would grow |position| past threshold
            allow_bids = not (position > self.CANCEL_THRESHOLD)
            allow_asks = not (position < -self.CANCEL_THRESHOLD)

            n_bid_levels = 0
            n_ask_levels = 0

            if allow_bids and buy_capacity > 0:
                n_bid_levels = self._place_grid(
                    orders, product, adjusted_fair, buy_capacity,
                    side="bid", shift=bid_shift,
                )

            if allow_asks and sell_capacity > 0:
                n_ask_levels = self._place_grid(
                    orders, product, adjusted_fair, sell_capacity,
                    side="ask", shift=ask_shift,
                )

            print(
                f"[H] {product} t={state.timestamp} fair={adjusted_fair:.1f} "
                f"pos={position} bid_levels={n_bid_levels} ask_levels={n_ask_levels}"
            )

            result[product] = orders

        trader_data["tomato_ewma"] = tomato_ewma
        return result, conversions, json.dumps(trader_data)

    def _place_grid(self, orders, product, adjusted_fair, capacity, *, side, shift):
        """Place orders at multiple levels around adjusted_fair. Returns level count."""
        levels_placed = 0
        remaining = capacity

        for i in range(self.GRID_LEVELS):
            if remaining <= 0:
                break

            offset = i + 1
            if side == "bid":
                price = math.floor(adjusted_fair - offset) + shift
            else:
                price = math.ceil(adjusted_fair + offset) + shift

            raw_size = capacity * self.LEVEL_WEIGHTS[i]
            size = max(1, round(raw_size))
            size = min(size, remaining)

            if size <= 0:
                continue

            if side == "bid":
                orders.append(Order(product, price, size))
            else:
                orders.append(Order(product, price, -size))

            remaining -= size
            levels_placed += 1

        return levels_placed

    @staticmethod
    def _microprice(buy_orders, sell_orders):
        best_bid = max(buy_orders.keys())
        best_ask = min(sell_orders.keys())
        bid_vol = abs(buy_orders[best_bid])
        ask_vol = abs(sell_orders[best_ask])
        return (best_bid * ask_vol + best_ask * bid_vol) / (bid_vol + ask_vol + 1e-9)
