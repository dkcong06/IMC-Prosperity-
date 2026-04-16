from datamodel import Order
import json
import math


class Trader:
    POSITION_LIMIT = 80

    EMERALD_FAIR = 10000
    EMERALD_POS_PENALTY = 0.8
    EMERALD_MIN_EDGE = 0.7

    TOMATO_EWMA_ALPHA = 0.35
    TOMATO_POS_PENALTY = 0.4
    TOMATO_MIN_EDGE = 1.0

    PASSIVE_SIZE = 5
    PASSIVE_SPREAD = 1
    PASSIVE_POS_CUTOFF = 40

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
                + best_ask * buy_orders[best_bid]
            ) / (buy_orders[best_bid] + abs(sell_orders[best_ask]) + 1e-9)

            if product == "EMERALDS":
                fair = self.EMERALD_FAIR
                adjusted_fair = fair - self.EMERALD_POS_PENALTY * position
                min_edge = self.EMERALD_MIN_EDGE
            else:
                if tomato_ewma is None:
                    tomato_ewma = microprice
                else:
                    tomato_ewma += self.TOMATO_EWMA_ALPHA * (microprice - tomato_ewma)
                fair = tomato_ewma
                adjusted_fair = fair - self.TOMATO_POS_PENALTY * position
                min_edge = self.TOMATO_MIN_EDGE

            remaining_buy = self.POSITION_LIMIT - position
            remaining_sell = self.POSITION_LIMIT + position
            total_bought = 0
            total_sold = 0
            levels_hit = 0

            # --- Multi-level aggressive sweep: asks (buy side) ---
            for ask_price in sorted(sell_orders.keys()):
                if ask_price >= adjusted_fair - min_edge:
                    break
                if remaining_buy <= 0:
                    break
                vol_at_level = abs(sell_orders[ask_price])
                qty = min(vol_at_level, remaining_buy)
                if qty > 0:
                    orders.append(Order(product, ask_price, qty))
                    remaining_buy -= qty
                    total_bought += qty
                    levels_hit += 1

            # --- Multi-level aggressive sweep: bids (sell side) ---
            for bid_price in sorted(buy_orders.keys(), reverse=True):
                if bid_price <= adjusted_fair + min_edge:
                    break
                if remaining_sell <= 0:
                    break
                vol_at_level = buy_orders[bid_price]
                qty = min(vol_at_level, remaining_sell)
                if qty > 0:
                    orders.append(Order(product, bid_price, -qty))
                    remaining_sell -= qty
                    total_sold += qty
                    levels_hit += 1

            # --- Passive market making with remaining capacity ---
            effective_position = position + total_bought - total_sold
            if abs(effective_position) <= self.PASSIVE_POS_CUTOFF:
                bid_price = math.floor(adjusted_fair) - self.PASSIVE_SPREAD
                ask_price = math.ceil(adjusted_fair) + self.PASSIVE_SPREAD

                buy_size = min(remaining_buy, self.PASSIVE_SIZE)
                sell_size = min(remaining_sell, self.PASSIVE_SIZE)

                if buy_size > 0:
                    orders.append(Order(product, bid_price, buy_size))
                if sell_size > 0:
                    orders.append(Order(product, ask_price, -sell_size))

            print(
                f"[E] {product} t={state.timestamp} fair={adjusted_fair:.1f} "
                f"pos={position} swept_buy={total_bought} swept_sell={total_sold} "
                f"levels_hit={levels_hit}"
            )

            result[product] = orders

        trader_data["tomato_ewma"] = tomato_ewma
        return result, conversions, json.dumps(trader_data)
