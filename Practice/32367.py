from datamodel import Order
import json
import math


class Trader:
    POSITION_LIMIT = {
        "EMERALDS": 80,
        "TOMATOES": 80,
    }
    # 来自历史盘口统计：bid 侧量相对更重时，下一跳 mid 更易上行（见 strategy_research.py）
    # From historical book stats: when bid-side size is heavier, the next mid tick is more likely to move up (see strategy_research.py).
    IMB_TILT_EMERALD = 1.05
    IMB_TILT_TOMATO = 2.0
    # 番茄 fair：EWMA 比「最后 30 个 micro 的均值」反应更快，减少 systematic 滞后
    # Tomato fair: EWMA reacts faster than the mean of the last 30 micro prices, reducing systematic lag.
    TOMATO_EWMA_ALPHA = 0.38
    # micro 相对上一期 fair 的意外抬高 → 提高买入门槛（数据里有短周期反转）
    # Micro unexpectedly above prior fair → raise buy hurdle (short-horizon mean reversion in the data).
    TOMATO_FADE_COEF = 0.48
    EMERALD_FADE_COEF = 0.32
    # 吃单最小优势：随 spread 放大，避免在噪声边沿零碎成交
    # Minimum edge to hit: scales with spread to avoid tiny fills at noisy edges.
    EMERALD_MIN_EDGE_ABS = 0.85
    EMERALD_MIN_EDGE_FRAC = 0.22

    def run(self, state):
        result = {}
        conversions = 0

        # ---------- Load traderData ----------
        trader_data_dict = {}
        if state.traderData:
            try:
                trader_data_dict = json.loads(state.traderData)
            except Exception:
                trader_data_dict = {}

        tomato_ewma_state = trader_data_dict.get("tomato_ewma")

        # ---------- Helpers ----------
        def get_best_bid_ask(order_depth):
            best_bid = max(order_depth.buy_orders.keys()) if order_depth.buy_orders else None
            best_ask = min(order_depth.sell_orders.keys()) if order_depth.sell_orders else None
            return best_bid, best_ask

        def buy_capacity(product, position):
            return max(0, self.POSITION_LIMIT[product] - position)

        def sell_capacity(product, position):
            return max(0, self.POSITION_LIMIT[product] + position)

        def microprice_from_book(bid_p, ask_p, bid_v, ask_v):
            if bid_v + ask_v > 0:
                return (ask_p * bid_v + bid_p * ask_v) / (bid_v + ask_v)
            return (bid_p + ask_p) / 2

        for product, order_depth in state.order_depths.items():
            orders = []
            position = state.position.get(product, 0)
            best_bid, best_ask = get_best_bid_ask(order_depth)

            if best_bid is None or best_ask is None:
                result[product] = orders
                continue

            # =========================================================
            # EMERALDS
            # =========================================================
            if product == "EMERALDS":
                fair = 10000
                adjusted_fair = fair - 0.8 * position

                bid_volume_at_best = order_depth.buy_orders[best_bid]
                ask_volume_at_best = -order_depth.sell_orders[best_ask]
                spread = best_ask - best_bid
                micro_e = microprice_from_book(
                    best_bid, best_ask, bid_volume_at_best, ask_volume_at_best
                )
                imb = (bid_volume_at_best - ask_volume_at_best) / (
                    bid_volume_at_best + ask_volume_at_best + 1e-9
                )
                adjusted_fair = adjusted_fair + self.IMB_TILT_EMERALD * imb

                min_edge = max(
                    self.EMERALD_MIN_EDGE_ABS, spread * self.EMERALD_MIN_EDGE_FRAC
                )
                momentum = micro_e - fair
                fade_buy = max(0.0, momentum) * self.EMERALD_FADE_COEF
                fade_sell = max(0.0, -momentum) * self.EMERALD_FADE_COEF

                buy_hurdle = adjusted_fair - min_edge - fade_buy
                sell_hurdle = adjusted_fair + min_edge + fade_sell

                # ---- aggressive take ----
                if best_ask < buy_hurdle:
                    edge = adjusted_fair - best_ask
                    tier = 1.5 + 0.25 * spread
                    if edge > min_edge + tier:
                        qty = min(ask_volume_at_best, buy_capacity(product, position), 12)
                    else:
                        qty = min(ask_volume_at_best, buy_capacity(product, position), 8)
                    if position > 18:
                        qty = min(qty, 6)

                    if qty > 0:
                        orders.append(Order(product, best_ask, qty))
                        position += qty

                if best_bid > sell_hurdle:
                    edge = best_bid - adjusted_fair
                    tier = 1.5 + 0.25 * spread
                    if edge > min_edge + tier:
                        qty = min(bid_volume_at_best, sell_capacity(product, position), 12)
                    else:
                        qty = min(bid_volume_at_best, sell_capacity(product, position), 8)
                    if position < -18:
                        qty = min(qty, 6)

                    if qty > 0:
                        orders.append(Order(product, best_bid, -qty))
                        position -= qty

                # ---- passive market making ----
                remaining_buy = buy_capacity(product, position)
                remaining_sell = sell_capacity(product, position)

                bid_quote = min(best_bid + 1, math.floor(adjusted_fair))
                ask_quote = max(best_ask - 1, math.ceil(adjusted_fair))

                if bid_quote < ask_quote:
                    bid_size = min(remaining_buy, 8)
                    ask_size = min(remaining_sell, 8)

                    if position > 12:
                        bid_size = min(bid_size, 2)
                        ask_size = min(remaining_sell, 10)
                    elif position > 6:
                        bid_size = min(bid_size, 4)
                    elif position < -12:
                        ask_size = min(ask_size, 2)
                        bid_size = min(remaining_buy, 10)
                    elif position < -6:
                        ask_size = min(ask_size, 4)

                    if bid_size > 0:
                        orders.append(Order(product, bid_quote, bid_size))
                    if ask_size > 0:
                        orders.append(Order(product, ask_quote, -ask_size))

            # =========================================================
            # TOMATOES
            # =========================================================
            elif product == "TOMATOES":
                bid_vol = order_depth.buy_orders[best_bid]
                ask_vol = -order_depth.sell_orders[best_ask]
                spread = best_ask - best_bid
                microprice = microprice_from_book(best_bid, best_ask, bid_vol, ask_vol)

                prev_ewma = tomato_ewma_state
                if prev_ewma is None:
                    hist = trader_data_dict.get("tomato_history", [])
                    if hist and len(hist) >= 3:
                        prev_ewma = sum(hist) / len(hist)
                    else:
                        prev_ewma = microprice
                momentum = microprice - prev_ewma
                tomato_ewma_state = (
                    self.TOMATO_EWMA_ALPHA * microprice
                    + (1.0 - self.TOMATO_EWMA_ALPHA) * prev_ewma
                )
                fair = tomato_ewma_state

                adjusted_fair = fair - 0.4 * position
                imb = (bid_vol - ask_vol) / (bid_vol + ask_vol + 1e-9)
                adjusted_fair = adjusted_fair + self.IMB_TILT_TOMATO * imb

                threshold = max(1.35, spread / 2.75)
                fade_buy = max(0.0, momentum) * self.TOMATO_FADE_COEF
                fade_sell = max(0.0, -momentum) * self.TOMATO_FADE_COEF
                buy_threshold = threshold + fade_buy
                sell_threshold = threshold + fade_sell

                # ---- aggressive take ----
                if best_ask < adjusted_fair - buy_threshold:
                    if best_ask < adjusted_fair - 2 * buy_threshold:
                        qty = min(ask_vol, buy_capacity(product, position), 10)
                    else:
                        qty = min(ask_vol, buy_capacity(product, position), 6)
                    if position > 18:
                        qty = min(qty, 5)

                    if qty > 0:
                        orders.append(Order(product, best_ask, qty))
                        position += qty

                if best_bid > adjusted_fair + sell_threshold:
                    if best_bid > adjusted_fair + 2 * sell_threshold:
                        qty = min(bid_vol, sell_capacity(product, position), 10)
                    else:
                        qty = min(bid_vol, sell_capacity(product, position), 6)
                    if position < -18:
                        qty = min(qty, 5)

                    if qty > 0:
                        orders.append(Order(product, best_bid, -qty))
                        position -= qty

                # ---- passive quoting around fair ----
                remaining_buy = buy_capacity(product, position)
                remaining_sell = sell_capacity(product, position)

                passive_bid = min(best_bid + 1, math.floor(adjusted_fair - 0.5))
                passive_ask = max(best_ask - 1, math.ceil(adjusted_fair + 0.5))

                if passive_bid < passive_ask:
                    bid_size = min(remaining_buy, 5)
                    ask_size = min(remaining_sell, 5)

                    if position > 12:
                        bid_size = min(bid_size, 1)
                        ask_size = min(remaining_sell, 7)
                    elif position > 6:
                        bid_size = min(bid_size, 3)
                    elif position < -12:
                        ask_size = min(ask_size, 1)
                        bid_size = min(remaining_buy, 7)
                    elif position < -6:
                        ask_size = min(ask_size, 3)

                    if bid_size > 0:
                        orders.append(Order(product, passive_bid, bid_size))
                    if ask_size > 0:
                        orders.append(Order(product, passive_ask, -ask_size))

            result[product] = orders

        # ---------- Save traderData ----------
        trader_data_dict["tomato_ewma"] = tomato_ewma_state
        if "tomato_history" in trader_data_dict:
            del trader_data_dict["tomato_history"]
        trader_data = json.dumps(trader_data_dict)

        return result, conversions, trader_data