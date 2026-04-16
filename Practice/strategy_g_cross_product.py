from datamodel import Order
import json
import math


class Trader:
    POSITION_LIMIT = 80

    EMERALD_ANCHOR = 10000
    EMERALD_POS_PENALTY = 0.8
    EMERALD_CROSS_WEIGHT = 0.3
    EMERALD_MIN_EDGE = 0.8
    EMERALD_EDGE_FRAC = 0.2
    EMERALD_MM_SIZE = 8

    TOMATO_EWMA_ALPHA = 0.35
    TOMATO_POS_PENALTY = 0.4
    TOMATO_CROSS_WEIGHT = 0.6
    TOMATO_MIN_EDGE = 1.2
    TOMATO_EDGE_FRAC = 1 / 2.8
    TOMATO_MM_SIZE = 8

    SPREAD_EWMA_ALPHA = 0.1
    STRESS_THRESHOLD = 0.5
    CALM_THRESHOLD = -0.3

    TAKE_QTY_CAP = 10

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
        emerald_avg_spread = trader_data.get("emerald_avg_spread", None)
        tomato_avg_spread = trader_data.get("tomato_avg_spread", None)

        # ── Phase 1: gather signals for all products ──

        emerald_mid = None
        emerald_spread = None
        emerald_imb = 0.0
        emerald_microprice = None
        emerald_bid_vol = 0.0
        emerald_ask_vol = 0.0

        tomato_mid = None
        tomato_spread = None
        tomato_imb = 0.0
        tomato_microprice = None
        tomato_bid_vol = 0.0
        tomato_ask_vol = 0.0

        if "EMERALDS" in state.order_depths:
            depth = state.order_depths["EMERALDS"]
            if depth.buy_orders and depth.sell_orders:
                best_bid = max(depth.buy_orders.keys())
                best_ask = min(depth.sell_orders.keys())
                emerald_mid = (best_bid + best_ask) / 2
                emerald_spread = best_ask - best_bid

                emerald_bid_vol = sum(abs(v) for v in depth.buy_orders.values())
                emerald_ask_vol = sum(abs(v) for v in depth.sell_orders.values())
                emerald_imb = (emerald_bid_vol - emerald_ask_vol) / (
                    emerald_bid_vol + emerald_ask_vol + 1e-9
                )
                emerald_microprice = (
                    best_bid * emerald_ask_vol + best_ask * emerald_bid_vol
                ) / (emerald_bid_vol + emerald_ask_vol + 1e-9)

        if "TOMATOES" in state.order_depths:
            depth = state.order_depths["TOMATOES"]
            if depth.buy_orders and depth.sell_orders:
                best_bid = max(depth.buy_orders.keys())
                best_ask = min(depth.sell_orders.keys())
                tomato_mid = (best_bid + best_ask) / 2
                tomato_spread = best_ask - best_bid

                tomato_bid_vol = sum(abs(v) for v in depth.buy_orders.values())
                tomato_ask_vol = sum(abs(v) for v in depth.sell_orders.values())
                tomato_imb = (tomato_bid_vol - tomato_ask_vol) / (
                    tomato_bid_vol + tomato_ask_vol + 1e-9
                )
                tomato_microprice = (
                    best_bid * tomato_ask_vol + best_ask * tomato_bid_vol
                ) / (tomato_bid_vol + tomato_ask_vol + 1e-9)

        # Update spread EWMAs
        if emerald_spread is not None:
            if emerald_avg_spread is None:
                emerald_avg_spread = emerald_spread
            else:
                emerald_avg_spread += self.SPREAD_EWMA_ALPHA * (
                    emerald_spread - emerald_avg_spread
                )

        if tomato_spread is not None:
            if tomato_avg_spread is None:
                tomato_avg_spread = tomato_spread
            else:
                tomato_avg_spread += self.SPREAD_EWMA_ALPHA * (
                    tomato_spread - tomato_avg_spread
                )

        # Cross-product signals
        cross_signal = 0.5 * emerald_imb + 0.5 * tomato_imb

        emerald_spread_z = 0.0
        if emerald_avg_spread is not None and emerald_avg_spread > 0 and emerald_spread is not None:
            emerald_spread_z = (emerald_spread - emerald_avg_spread) / emerald_avg_spread

        # Stress / calm regime sizing multiplier for TOMATOES
        if emerald_spread_z > self.STRESS_THRESHOLD:
            tomato_size_mult = 0.5
        elif emerald_spread_z < self.CALM_THRESHOLD:
            tomato_size_mult = 1.3
        else:
            tomato_size_mult = 1.0

        # ── Phase 2: place orders for each product using cross-product info ──

        # --- EMERALDS ---
        if "EMERALDS" in state.order_depths and emerald_mid is not None:
            depth = state.order_depths["EMERALDS"]
            position = state.position.get("EMERALDS", 0)
            orders = []

            fair = self.EMERALD_ANCHOR + self.EMERALD_CROSS_WEIGHT * cross_signal
            adjusted_fair = fair - self.EMERALD_POS_PENALTY * position

            best_bid = max(depth.buy_orders.keys())
            best_ask = min(depth.sell_orders.keys())
            spread = best_ask - best_bid

            threshold = max(self.EMERALD_MIN_EDGE, spread * self.EMERALD_EDGE_FRAC)
            buy_capacity = self.POSITION_LIMIT - position
            sell_capacity = self.POSITION_LIMIT + position

            if best_ask < adjusted_fair - threshold and buy_capacity > 0:
                ask_vol = abs(depth.sell_orders[best_ask])
                qty = min(ask_vol, buy_capacity, self.TAKE_QTY_CAP)
                if qty > 0:
                    orders.append(Order("EMERALDS", best_ask, qty))

            if best_bid > adjusted_fair + threshold and sell_capacity > 0:
                bid_vol = abs(depth.buy_orders[best_bid])
                qty = min(bid_vol, sell_capacity, self.TAKE_QTY_CAP)
                if qty > 0:
                    orders.append(Order("EMERALDS", best_bid, -qty))

            bid_price = min(best_bid + 1, math.floor(adjusted_fair))
            ask_price = max(best_ask - 1, math.ceil(adjusted_fair))

            if bid_price < ask_price:
                remaining_buy = self.POSITION_LIMIT - (
                    position + sum(o.quantity for o in orders if o.quantity > 0)
                )
                remaining_sell = self.POSITION_LIMIT + (
                    position + sum(o.quantity for o in orders if o.quantity < 0)
                )

                buy_size = min(self.EMERALD_MM_SIZE, remaining_buy)
                sell_size = min(self.EMERALD_MM_SIZE, remaining_sell)

                if buy_size > 0:
                    orders.append(Order("EMERALDS", bid_price, buy_size))
                if sell_size > 0:
                    orders.append(Order("EMERALDS", ask_price, -sell_size))

            print(
                f"[G] EMERALDS t={state.timestamp} fair={adjusted_fair:.1f} "
                f"cross={cross_signal:.2f} e_spread_z={emerald_spread_z:.2f} pos={position}"
            )
            result["EMERALDS"] = orders

        # --- TOMATOES ---
        if "TOMATOES" in state.order_depths and tomato_microprice is not None:
            depth = state.order_depths["TOMATOES"]
            position = state.position.get("TOMATOES", 0)
            orders = []

            if tomato_ewma is None:
                tomato_ewma = tomato_microprice
            else:
                tomato_ewma += self.TOMATO_EWMA_ALPHA * (tomato_microprice - tomato_ewma)

            fair = tomato_ewma + self.TOMATO_CROSS_WEIGHT * cross_signal
            adjusted_fair = fair - self.TOMATO_POS_PENALTY * position

            best_bid = max(depth.buy_orders.keys())
            best_ask = min(depth.sell_orders.keys())
            spread = best_ask - best_bid

            threshold = max(self.TOMATO_MIN_EDGE, spread * self.TOMATO_EDGE_FRAC)
            buy_capacity = self.POSITION_LIMIT - position
            sell_capacity = self.POSITION_LIMIT + position

            take_cap = max(1, int(self.TAKE_QTY_CAP * tomato_size_mult))

            if best_ask < adjusted_fair - threshold and buy_capacity > 0:
                ask_vol = abs(depth.sell_orders[best_ask])
                qty = min(ask_vol, buy_capacity, take_cap)
                if qty > 0:
                    orders.append(Order("TOMATOES", best_ask, qty))

            if best_bid > adjusted_fair + threshold and sell_capacity > 0:
                bid_vol = abs(depth.buy_orders[best_bid])
                qty = min(bid_vol, sell_capacity, take_cap)
                if qty > 0:
                    orders.append(Order("TOMATOES", best_bid, -qty))

            bid_price = min(best_bid + 1, math.floor(adjusted_fair))
            ask_price = max(best_ask - 1, math.ceil(adjusted_fair))

            if bid_price < ask_price:
                remaining_buy = self.POSITION_LIMIT - (
                    position + sum(o.quantity for o in orders if o.quantity > 0)
                )
                remaining_sell = self.POSITION_LIMIT + (
                    position + sum(o.quantity for o in orders if o.quantity < 0)
                )

                mm_size = max(1, int(self.TOMATO_MM_SIZE * tomato_size_mult))
                buy_size = min(mm_size, remaining_buy)
                sell_size = min(mm_size, remaining_sell)

                if buy_size > 0:
                    orders.append(Order("TOMATOES", bid_price, buy_size))
                if sell_size > 0:
                    orders.append(Order("TOMATOES", ask_price, -sell_size))

            print(
                f"[G] TOMATOES t={state.timestamp} fair={adjusted_fair:.1f} "
                f"cross={cross_signal:.2f} e_spread_z={emerald_spread_z:.2f} pos={position}"
            )
            result["TOMATOES"] = orders

        # ── Persist state ──
        trader_data["tomato_ewma"] = tomato_ewma
        trader_data["emerald_avg_spread"] = emerald_avg_spread
        trader_data["tomato_avg_spread"] = tomato_avg_spread
        return result, conversions, json.dumps(trader_data)
