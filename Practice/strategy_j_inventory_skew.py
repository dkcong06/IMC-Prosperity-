from datamodel import Order
import json
import math


class Trader:
    POSITION_LIMIT = 80
    MAX_MIDS_HISTORY = 20

    EMERALD_ANCHOR = 10000
    TOMATO_EWMA_ALPHA = 0.35

    GAMMA = 0.05
    MIN_SPREAD = 1.0
    MAX_SPREAD = 5.0

    BASE_SIZE = 15
    SKEW_SIZE_BOOST = 25
    SKEW_SIZE_REDUCE = 5
    SKEW_THRESHOLD_MILD = 40
    SKEW_THRESHOLD_HARD = 60

    TAKE_QTY_CAP = 20

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
        emerald_mids = trader_data.get("emerald_mids", [])
        tomato_mids = trader_data.get("tomato_mids", [])

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
            spread = best_ask - best_bid
            mid = (best_bid + best_ask) / 2.0

            microprice = self._microprice(buy_orders, sell_orders)

            if product == "EMERALDS":
                fair = self.EMERALD_ANCHOR
                emerald_mids.append(mid)
                if len(emerald_mids) > self.MAX_MIDS_HISTORY:
                    emerald_mids = emerald_mids[-self.MAX_MIDS_HISTORY:]
                mids_history = emerald_mids
            else:
                if tomato_ewma is None:
                    tomato_ewma = microprice
                else:
                    tomato_ewma += self.TOMATO_EWMA_ALPHA * (microprice - tomato_ewma)
                fair = tomato_ewma
                tomato_mids.append(mid)
                if len(tomato_mids) > self.MAX_MIDS_HISTORY:
                    tomato_mids = tomato_mids[-self.MAX_MIDS_HISTORY:]
                mids_history = tomato_mids

            sigma = self._estimate_sigma(mids_history, spread)

            reservation_price = fair - self.GAMMA * position * sigma * sigma

            optimal_spread = sigma * math.sqrt(2.0 / self.GAMMA)
            optimal_spread = max(optimal_spread, self.MIN_SPREAD)
            optimal_spread = min(optimal_spread, self.MAX_SPREAD)

            bid_price = math.floor(reservation_price - optimal_spread / 2.0)
            ask_price = math.ceil(reservation_price + optimal_spread / 2.0)

            buy_capacity = self.POSITION_LIMIT - position
            sell_capacity = self.POSITION_LIMIT + position

            # --- Aggressive take when edge exceeds optimal spread ---
            if best_ask < reservation_price - optimal_spread and buy_capacity > 0:
                ask_vol = abs(sell_orders[best_ask])
                qty = min(ask_vol, buy_capacity, self.TAKE_QTY_CAP)
                if qty > 0:
                    orders.append(Order(product, best_ask, qty))

            if best_bid > reservation_price + optimal_spread and sell_capacity > 0:
                bid_vol = abs(buy_orders[best_bid])
                qty = min(bid_vol, sell_capacity, self.TAKE_QTY_CAP)
                if qty > 0:
                    orders.append(Order(product, best_bid, -qty))

            # --- Passive quoting with inventory skew ---
            remaining_buy = self.POSITION_LIMIT - (
                position + sum(o.quantity for o in orders if o.quantity > 0)
            )
            remaining_sell = self.POSITION_LIMIT + (
                position + sum(o.quantity for o in orders if o.quantity < 0)
            )

            abs_pos = abs(position)
            if abs_pos > self.SKEW_THRESHOLD_HARD:
                buy_size = 0 if position > 0 else self.SKEW_SIZE_BOOST
                sell_size = 0 if position < 0 else self.SKEW_SIZE_BOOST
            elif abs_pos > self.SKEW_THRESHOLD_MILD:
                buy_size = self.SKEW_SIZE_REDUCE if position > 0 else self.SKEW_SIZE_BOOST
                sell_size = self.SKEW_SIZE_REDUCE if position < 0 else self.SKEW_SIZE_BOOST
            else:
                buy_size = self.BASE_SIZE
                sell_size = self.BASE_SIZE

            buy_size = min(buy_size, remaining_buy)
            sell_size = min(sell_size, remaining_sell)

            if buy_size > 0:
                orders.append(Order(product, bid_price, buy_size))
            if sell_size > 0:
                orders.append(Order(product, ask_price, -sell_size))

            print(
                f"[J] {product} t={state.timestamp} "
                f"res_price={reservation_price:.1f} opt_spread={optimal_spread:.2f} "
                f"sigma={sigma:.3f} pos={position}"
            )

            result[product] = orders

        trader_data["tomato_ewma"] = tomato_ewma
        trader_data["emerald_mids"] = emerald_mids
        trader_data["tomato_mids"] = tomato_mids
        return result, conversions, json.dumps(trader_data)

    @staticmethod
    def _microprice(buy_orders, sell_orders):
        best_bid = max(buy_orders.keys())
        best_ask = min(sell_orders.keys())
        bid_vol = abs(buy_orders[best_bid])
        ask_vol = abs(sell_orders[best_ask])
        return (best_bid * ask_vol + best_ask * bid_vol) / (bid_vol + ask_vol + 1e-9)

    @staticmethod
    def _estimate_sigma(mids, spread):
        if len(mids) < 5:
            return spread / 2.0

        returns = [mids[i] - mids[i - 1] for i in range(1, len(mids))]
        n = len(returns)
        mean_ret = sum(returns) / n
        variance = sum((r - mean_ret) ** 2 for r in returns) / (n - 1)
        return math.sqrt(variance) if variance > 0 else spread / 2.0
