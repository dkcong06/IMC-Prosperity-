from datamodel import Order
import json
import math


class Trader:
    POSITION_LIMIT = {"EMERALDS": 80, "TOMATOES": 80}
    TOMATO_EWMA_ALPHA = 0.30
    HARD_CUTOFF = 50
    FLATTEN_THRESHOLD = 30

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
        fill_count = td.get("fill_count", 0)

        def best_bid_ask(depth):
            bid = max(depth.buy_orders) if depth.buy_orders else None
            ask = min(depth.sell_orders) if depth.sell_orders else None
            return bid, ask

        def buy_cap(product, pos):
            return max(0, self.POSITION_LIMIT[product] - pos)

        def sell_cap(product, pos):
            return max(0, self.POSITION_LIMIT[product] + pos)

        def exp_penalty(pos):
            return 0.3 * math.copysign(abs(pos) ** 1.3, pos)

        def microprice(bp, ap, bv, av):
            total = bv + av
            if total > 0:
                return (ap * bv + bp * av) / total
            return (bp + ap) / 2.0

        for product, depth in state.order_depths.items():
            orders = []
            pos = state.position.get(product, 0)
            bb, ba = best_bid_ask(depth)

            if bb is None or ba is None:
                result[product] = orders
                continue

            bid_vol = depth.buy_orders[bb]
            ask_vol = -depth.sell_orders[ba]
            spread = ba - bb

            # ---------- Fair value ----------
            if product == "EMERALDS":
                fair = 10000.0
            else:
                micro = microprice(bb, ba, bid_vol, ask_vol)
                if tomato_ewma is None:
                    tomato_ewma = micro
                tomato_ewma = self.TOMATO_EWMA_ALPHA * micro + (1.0 - self.TOMATO_EWMA_ALPHA) * tomato_ewma
                fair = tomato_ewma

            adjusted_fair = fair - exp_penalty(pos)

            # ---------- Aggressive take ----------
            if product == "EMERALDS":
                thresh = max(0.5, spread * 0.15)
            else:
                thresh = max(0.8, spread / 3.5)

            # Buy: lift asks below fair
            if ba < adjusted_fair - thresh:
                if abs(pos) > self.HARD_CUTOFF and pos > 0:
                    pass  # no new long exposure
                else:
                    qty = min(ask_vol, buy_cap(product, pos), 15)
                    if qty > 0:
                        orders.append(Order(product, ba, qty))
                        pos += qty
                        fill_count += 1

            # Sell: hit bids above fair
            if bb > adjusted_fair + thresh:
                if abs(pos) > self.HARD_CUTOFF and pos < 0:
                    pass  # no new short exposure
                else:
                    qty = min(bid_vol, sell_cap(product, pos), 15)
                    if qty > 0:
                        orders.append(Order(product, bb, -qty))
                        pos -= qty
                        fill_count += 1

            # ---------- Passive penny-jumping ----------
            bid_quote = min(bb + 1, math.floor(adjusted_fair - 0.5))
            ask_quote = max(ba - 1, math.ceil(adjusted_fair + 0.5))

            if bid_quote < ask_quote:
                bid_size = 10
                ask_size = 10

                if pos > 0:
                    ask_size = min(sell_cap(product, pos), 15)
                    bid_size = max(1, 10 - pos // 4)
                elif pos < 0:
                    bid_size = min(buy_cap(product, pos), 15)
                    ask_size = max(1, 10 - abs(pos) // 4)

                # Hard cutoff: block side that adds exposure
                if pos > self.HARD_CUTOFF:
                    bid_size = 0
                if pos < -self.HARD_CUTOFF:
                    ask_size = 0

                bid_size = min(bid_size, buy_cap(product, pos))
                ask_size = min(ask_size, sell_cap(product, pos))

                if bid_size > 0:
                    orders.append(Order(product, bid_quote, bid_size))
                    fill_count += 1
                if ask_size > 0:
                    orders.append(Order(product, ask_quote, -ask_size))
                    fill_count += 1

            # ---------- Aggressive flattening ----------
            if pos > self.FLATTEN_THRESHOLD:
                flat_qty = min(pos - 20, bid_vol, 10)
                flat_qty = min(flat_qty, sell_cap(product, pos))
                if flat_qty > 0:
                    orders.append(Order(product, bb, -flat_qty))
                    pos -= flat_qty
                    fill_count += 1
            elif pos < -self.FLATTEN_THRESHOLD:
                flat_qty = min(abs(pos) - 20, ask_vol, 10)
                flat_qty = min(flat_qty, buy_cap(product, pos))
                if flat_qty > 0:
                    orders.append(Order(product, ba, flat_qty))
                    pos += flat_qty
                    fill_count += 1

            print(
                f"[D] {product} t={state.timestamp} fair={adjusted_fair:.1f} "
                f"pos={pos} fills={fill_count} spread={spread}"
            )
            result[product] = orders

        td["tomato_ewma"] = tomato_ewma
        td["fill_count"] = fill_count
        return result, conversions, json.dumps(td)
