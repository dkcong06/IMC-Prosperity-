from datamodel import Order
import json
import math


class Trader:
    POSITION_LIMIT = 80

    EMERALD_ANCHOR = 10000
    EMERALD_ANCHOR_WEIGHT = 0.7
    EMERALD_IMB_TILT = 1.0
    EMERALD_POS_PENALTY = 0.8
    EMERALD_MIN_EDGE = 0.8
    EMERALD_EDGE_FRAC = 0.2

    TOMATO_EWMA_ALPHA = 0.35
    TOMATO_IMB_TILT = 1.8
    TOMATO_POS_PENALTY = 0.4
    TOMATO_MIN_EDGE = 1.3
    TOMATO_EDGE_FRAC = 1 / 2.8

    MM_SIZE = 6
    MM_SIZE_REDUCED = 2
    MM_SIZE_BOOSTED = 8
    MM_POS_THRESHOLD = 15

    TAKE_QTY_CAP = 10
    TAKE_QTY_CAP_HEAVY = 5
    TAKE_POS_THRESHOLD = 20

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

            deep_book_fair, total_bid_vol, total_ask_vol = self._deep_book_microprice(
                buy_orders, sell_orders
            )

            if product == "EMERALDS":
                fair = (
                    self.EMERALD_ANCHOR_WEIGHT * self.EMERALD_ANCHOR
                    + (1 - self.EMERALD_ANCHOR_WEIGHT) * deep_book_fair
                )
            else:
                if tomato_ewma is None:
                    tomato_ewma = deep_book_fair
                else:
                    tomato_ewma += self.TOMATO_EWMA_ALPHA * (deep_book_fair - tomato_ewma)
                fair = tomato_ewma

            imb = (total_bid_vol - total_ask_vol) / (
                total_bid_vol + total_ask_vol + 1e-9
            )

            if product == "EMERALDS":
                adjusted_fair = fair + self.EMERALD_IMB_TILT * imb
                adjusted_fair -= self.EMERALD_POS_PENALTY * position
            else:
                adjusted_fair = fair + self.TOMATO_IMB_TILT * imb
                adjusted_fair -= self.TOMATO_POS_PENALTY * position

            best_bid = max(buy_orders.keys())
            best_ask = min(sell_orders.keys())
            spread = best_ask - best_bid

            if product == "EMERALDS":
                threshold = max(self.EMERALD_MIN_EDGE, spread * self.EMERALD_EDGE_FRAC)
            else:
                threshold = max(self.TOMATO_MIN_EDGE, spread * self.TOMATO_EDGE_FRAC)

            buy_capacity = self.POSITION_LIMIT - position
            sell_capacity = self.POSITION_LIMIT + position

            qty_cap = self.TAKE_QTY_CAP
            if abs(position) > self.TAKE_POS_THRESHOLD:
                qty_cap = self.TAKE_QTY_CAP_HEAVY

            # --- Aggressive take ---
            if best_ask < adjusted_fair - threshold and buy_capacity > 0:
                ask_vol = abs(sell_orders[best_ask])
                qty = min(ask_vol, buy_capacity, qty_cap)
                if qty > 0:
                    orders.append(Order(product, best_ask, qty))

            if best_bid > adjusted_fair + threshold and sell_capacity > 0:
                bid_vol = abs(buy_orders[best_bid])
                qty = min(bid_vol, sell_capacity, qty_cap)
                if qty > 0:
                    orders.append(Order(product, best_bid, -qty))

            # --- Passive market making ---
            bid_price = min(best_bid + 1, math.floor(adjusted_fair))
            ask_price = max(best_ask - 1, math.ceil(adjusted_fair))

            if bid_price < ask_price:
                remaining_buy = self.POSITION_LIMIT - (
                    position + sum(o.quantity for o in orders if o.quantity > 0)
                )
                remaining_sell = self.POSITION_LIMIT + (
                    position + sum(o.quantity for o in orders if o.quantity < 0)
                )

                if abs(position) > self.MM_POS_THRESHOLD:
                    buy_size = self.MM_SIZE_BOOSTED if position < 0 else self.MM_SIZE_REDUCED
                    sell_size = self.MM_SIZE_BOOSTED if position > 0 else self.MM_SIZE_REDUCED
                else:
                    buy_size = self.MM_SIZE
                    sell_size = self.MM_SIZE

                buy_size = min(buy_size, remaining_buy)
                sell_size = min(sell_size, remaining_sell)

                if buy_size > 0:
                    orders.append(Order(product, bid_price, buy_size))
                if sell_size > 0:
                    orders.append(Order(product, ask_price, -sell_size))

            print(
                f"[A] {product} t={state.timestamp} fair={adjusted_fair:.1f} "
                f"deep={deep_book_fair:.1f} imb={imb:.3f} pos={position} spread={spread}"
            )

            result[product] = orders

        trader_data["tomato_ewma"] = tomato_ewma
        return result, conversions, json.dumps(trader_data)

    @staticmethod
    def _deep_book_microprice(buy_orders, sell_orders):
        bid_vwap = 0.0
        total_bid_vol = 0.0
        for price, vol in buy_orders.items():
            v = abs(vol)
            bid_vwap += price * v
            total_bid_vol += v
        bid_vwap /= total_bid_vol if total_bid_vol else 1.0

        ask_vwap = 0.0
        total_ask_vol = 0.0
        for price, vol in sell_orders.items():
            v = abs(vol)
            ask_vwap += price * v
            total_ask_vol += v
        ask_vwap /= total_ask_vol if total_ask_vol else 1.0

        fair = (bid_vwap * total_ask_vol + ask_vwap * total_bid_vol) / (
            total_bid_vol + total_ask_vol + 1e-9
        )
        return fair, total_bid_vol, total_ask_vol
