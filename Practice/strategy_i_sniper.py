from datamodel import Order
import json
import math


class Trader:
    POSITION_LIMIT = 80
    TOMATO_EWMA_ALPHA = 0.15
    ROLLING_WINDOW = 30
    ENTRY_Z = 1.5
    EXIT_Z = 0.3
    MAX_ENTRY_SIZE = 30

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
        emerald_mids = td.get("emerald_mids", [])
        tomato_mids = td.get("tomato_mids", [])

        for product in ["EMERALDS", "TOMATOES"]:
            if product not in state.order_depths:
                continue

            orders = []
            depth = state.order_depths[product]
            pos = state.position.get(product, 0)

            buy_orders = depth.buy_orders
            sell_orders = depth.sell_orders

            if not buy_orders or not sell_orders:
                result[product] = orders
                continue

            best_bid = max(buy_orders)
            best_ask = min(sell_orders)
            mid = (best_bid + best_ask) / 2.0

            bid_vol = abs(buy_orders[best_bid])
            ask_vol = abs(sell_orders[best_ask])

            if product == "EMERALDS":
                fair = 10000.0
                emerald_mids.append(mid)
                if len(emerald_mids) > self.ROLLING_WINDOW:
                    emerald_mids = emerald_mids[-self.ROLLING_WINDOW:]
                mids = emerald_mids
                signal_price = mid
            else:
                micro = (best_ask * bid_vol + best_bid * ask_vol) / (bid_vol + ask_vol + 1e-9)
                if tomato_ewma is None:
                    tomato_ewma = micro
                tomato_ewma += self.TOMATO_EWMA_ALPHA * (micro - tomato_ewma)
                fair = tomato_ewma
                tomato_mids.append(micro)
                if len(tomato_mids) > self.ROLLING_WINDOW:
                    tomato_mids = tomato_mids[-self.ROLLING_WINDOW:]
                mids = tomato_mids
                signal_price = micro

            rolling_std = self._std(mids)
            z_score = (signal_price - fair) / (rolling_std + 1e-9)

            buy_cap = self.POSITION_LIMIT - pos
            sell_cap = self.POSITION_LIMIT + pos
            action = "HOLD"

            # --- Exit: unwind when price reverts toward fair ---
            if pos > 0 and z_score > -self.EXIT_Z:
                action = "EXIT"
                remaining = pos
                for price in sorted(buy_orders, reverse=True):
                    if remaining <= 0:
                        break
                    vol = abs(buy_orders[price])
                    qty = min(vol, remaining, sell_cap)
                    if qty > 0:
                        orders.append(Order(product, price, -qty))
                        remaining -= qty
                        sell_cap -= qty

            elif pos < 0 and z_score < self.EXIT_Z:
                action = "EXIT"
                remaining = abs(pos)
                for price in sorted(sell_orders):
                    if remaining <= 0:
                        break
                    vol = abs(sell_orders[price])
                    qty = min(vol, remaining, buy_cap)
                    if qty > 0:
                        orders.append(Order(product, price, qty))
                        remaining -= qty
                        buy_cap -= qty

            # --- Entry: aggressive sweep on large dislocation ---
            elif z_score < -self.ENTRY_Z:
                action = "BUY"
                budget = min(buy_cap, self.MAX_ENTRY_SIZE)
                for price in sorted(sell_orders):
                    if budget <= 0 or price > fair:
                        break
                    vol = abs(sell_orders[price])
                    qty = min(vol, budget)
                    if qty > 0:
                        orders.append(Order(product, price, qty))
                        budget -= qty

            elif z_score > self.ENTRY_Z:
                action = "SELL"
                budget = min(sell_cap, self.MAX_ENTRY_SIZE)
                for price in sorted(buy_orders, reverse=True):
                    if budget <= 0 or price < fair:
                        break
                    vol = abs(buy_orders[price])
                    qty = min(vol, budget)
                    if qty > 0:
                        orders.append(Order(product, price, -qty))
                        budget -= qty

            print(
                f"[I] {product} t={state.timestamp} fair={fair:.1f} mid={mid:.1f} "
                f"z={z_score:.2f} pos={pos} action={action}"
            )
            result[product] = orders

        td["tomato_ewma"] = tomato_ewma
        td["emerald_mids"] = emerald_mids
        td["tomato_mids"] = tomato_mids
        return result, conversions, json.dumps(td)

    @staticmethod
    def _std(values):
        n = len(values)
        if n < 2:
            return 0.0
        mean = sum(values) / n
        variance = sum((v - mean) ** 2 for v in values) / n
        return math.sqrt(variance)
