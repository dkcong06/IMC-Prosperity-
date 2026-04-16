from datamodel import Order
import json
import math


class Trader:
    POSITION_LIMIT = {"EMERALDS": 80, "TOMATOES": 80}
    FLOW_WINDOW = 10
    TOMATO_EWMA_ALPHA = 0.35

    def run(self, state):
        result = {}
        conversions = 0

        td = {}
        if state.traderData:
            try:
                td = json.loads(state.traderData)
            except Exception:
                td = {}

        emerald_flows = td.get("emerald_flows", [])
        tomato_flows = td.get("tomato_flows", [])
        tomato_ewma = td.get("tomato_ewma", None)

        def best_bid_ask(order_depth):
            bid = max(order_depth.buy_orders) if order_depth.buy_orders else None
            ask = min(order_depth.sell_orders) if order_depth.sell_orders else None
            return bid, ask

        def buy_cap(product, pos):
            return max(0, self.POSITION_LIMIT[product] - pos)

        def sell_cap(product, pos):
            return max(0, self.POSITION_LIMIT[product] + pos)

        def compute_net_flow(product, mid):
            trades = state.market_trades.get(product, [])
            buy_flow, sell_flow = 0, 0
            for t in trades:
                if t.price >= mid:
                    buy_flow += t.quantity
                else:
                    sell_flow += t.quantity
            return buy_flow - sell_flow

        def update_flow_list(flow_list, net_flow):
            flow_list.append(net_flow)
            if len(flow_list) > self.FLOW_WINDOW:
                flow_list[:] = flow_list[-self.FLOW_WINDOW:]
            return sum(flow_list) / len(flow_list)

        for product, order_depth in state.order_depths.items():
            orders = []
            position = state.position.get(product, 0)
            bb, ba = best_bid_ask(order_depth)

            if bb is None or ba is None:
                result[product] = orders
                continue

            mid = (bb + ba) / 2
            spread = ba - bb
            bid_vol = order_depth.buy_orders[bb]
            ask_vol = -order_depth.sell_orders[ba]

            net_flow = compute_net_flow(product, mid)

            if product == "EMERALDS":
                flow_signal = update_flow_list(emerald_flows, net_flow)

                fair = 10000 + 0.5 * flow_signal
                adjusted_fair = fair - 0.7 * position

                threshold = max(0.9, spread * 0.22)

                # Aggressive take
                if ba < adjusted_fair - threshold:
                    flow_confirms = flow_signal > 0
                    max_qty = 12 if flow_confirms else 8
                    if abs(position) > 20:
                        max_qty = 5
                    qty = min(ask_vol, buy_cap(product, position), max_qty)
                    if qty > 0:
                        orders.append(Order(product, ba, qty))
                        position += qty

                if bb > adjusted_fair + threshold:
                    flow_confirms = flow_signal < 0
                    max_qty = 12 if flow_confirms else 8
                    if abs(position) > 20:
                        max_qty = 5
                    qty = min(bid_vol, sell_cap(product, position), max_qty)
                    if qty > 0:
                        orders.append(Order(product, bb, -qty))
                        position -= qty

                # Passive market making
                bid_quote = min(bb + 1, math.floor(adjusted_fair))
                ask_quote = max(ba - 1, math.ceil(adjusted_fair))

                if bid_quote < ask_quote:
                    bid_size = 6
                    ask_size = 6
                    if abs(position) > 15:
                        if position > 0:
                            bid_size = 2
                            ask_size = 8
                        else:
                            ask_size = 2
                            bid_size = 8

                    bid_size = min(bid_size, buy_cap(product, position))
                    ask_size = min(ask_size, sell_cap(product, position))

                    if bid_size > 0:
                        orders.append(Order(product, bid_quote, bid_size))
                    if ask_size > 0:
                        orders.append(Order(product, ask_quote, -ask_size))

                print(
                    f"[B] {product} t={state.timestamp} fair={adjusted_fair:.1f} "
                    f"flow={flow_signal:.1f} net={net_flow} pos={position}"
                )

            elif product == "TOMATOES":
                flow_signal = update_flow_list(tomato_flows, net_flow)

                # BBO microprice → EWMA
                microprice = (ba * bid_vol + bb * ask_vol) / (bid_vol + ask_vol + 1e-9)
                if tomato_ewma is None:
                    tomato_ewma = microprice
                tomato_ewma = (
                    self.TOMATO_EWMA_ALPHA * microprice
                    + (1.0 - self.TOMATO_EWMA_ALPHA) * tomato_ewma
                )

                fair = tomato_ewma + 0.8 * flow_signal
                adjusted_fair = fair - 0.4 * position

                threshold = max(1.3, spread / 2.75)

                # Aggressive take
                if ba < adjusted_fair - threshold:
                    flow_confirms = flow_signal > 0
                    max_qty = 12 if flow_confirms else 8
                    if abs(position) > 20:
                        max_qty = 5
                    qty = min(ask_vol, buy_cap(product, position), max_qty)
                    if qty > 0:
                        orders.append(Order(product, ba, qty))
                        position += qty

                if bb > adjusted_fair + threshold:
                    flow_confirms = flow_signal < 0
                    max_qty = 12 if flow_confirms else 8
                    if abs(position) > 20:
                        max_qty = 5
                    qty = min(bid_vol, sell_cap(product, position), max_qty)
                    if qty > 0:
                        orders.append(Order(product, bb, -qty))
                        position -= qty

                # Passive market making
                bid_quote = min(bb + 1, math.floor(adjusted_fair))
                ask_quote = max(ba - 1, math.ceil(adjusted_fair))

                if bid_quote < ask_quote:
                    bid_size = 6
                    ask_size = 6
                    if abs(position) > 15:
                        if position > 0:
                            bid_size = 2
                            ask_size = 8
                        else:
                            ask_size = 2
                            bid_size = 8

                    bid_size = min(bid_size, buy_cap(product, position))
                    ask_size = min(ask_size, sell_cap(product, position))

                    if bid_size > 0:
                        orders.append(Order(product, bid_quote, bid_size))
                    if ask_size > 0:
                        orders.append(Order(product, ask_quote, -ask_size))

                print(
                    f"[B] {product} t={state.timestamp} fair={adjusted_fair:.1f} "
                    f"flow={flow_signal:.1f} net={net_flow} pos={position}"
                )

            result[product] = orders

        td["emerald_flows"] = emerald_flows
        td["tomato_flows"] = tomato_flows
        td["tomato_ewma"] = tomato_ewma
        trader_data = json.dumps(td)

        return result, conversions, trader_data
