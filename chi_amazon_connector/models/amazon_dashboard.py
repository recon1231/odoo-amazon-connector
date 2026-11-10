# -*- coding: utf-8 -*-
from collections import defaultdict
from datetime import timedelta

from odoo import api, fields, models


class AmazonDashboard(models.AbstractModel):
    _name = "chi.amazon.dashboard"
    _description = "Amazon Seller Hub Dashboard"

    @api.model
    def get_data(self, account_id=False, days=30, fulfillment="all"):
        days = int(days or 30)
        now = fields.Datetime.now()
        start = now - timedelta(days=days)
        prev_start = start - timedelta(days=days)
        accounts = self.env["chi.amazon.account"].search([])
        base = [("account_id", "=", account_id)] if account_id else [("account_id", "in", accounts.ids)]
        if fulfillment in ("fba", "fbm"):
            order_base = base + [("fulfillment", "=", fulfillment)]
        else:
            order_base = list(base)
        valid = [("amazon_status", "!=", "canceled")]
        Order = self.env["chi.amazon.order"]
        orders = Order.search(order_base + valid + [("purchase_date", ">=", start)])
        prev = Order.search(order_base + valid + [("purchase_date", ">=", prev_start), ("purchase_date", "<", start)])
        currency = (accounts.filtered(lambda a: a.id == account_id) or accounts[:1] or self.env.company).currency_id

        def totals(recs):
            sales = sum(recs.mapped("amount_total"))
            revenue = sum(recs.mapped("amount_untaxed")) + sum(recs.mapped("amount_shipping"))
            fees = abs(sum(recs.mapped("amount_fees")))
            margin = sum(recs.mapped("margin"))
            return {
                "sales": sales, "orders": len(recs), "units": sum(recs.mapped("units")),
                "aov": recs and sales / len(recs) or 0.0, "fees": fees, "margin": margin,
                "margin_pct": revenue and margin / revenue * 100 or 0.0,
            }

        cur, old = totals(orders), totals(prev)

        # ---- daily series ------------------------------------------------
        labels, by_day = [], defaultdict(lambda: {"fba": 0.0, "fbm": 0.0, "orders": 0, "margin": 0.0})
        for i in range(days):
            day = (start + timedelta(days=i + 1)).date()
            labels.append(fields.Date.to_string(day))
        for o in orders:
            key = fields.Date.to_string(fields.Datetime.context_timestamp(self, o.purchase_date).date())
            by_day[key][o.fulfillment] += o.amount_total
            by_day[key]["orders"] += 1
            by_day[key]["margin"] += o.margin
        series = {
            "labels": labels,
            "fba": [round(by_day[d]["fba"], 2) for d in labels],
            "fbm": [round(by_day[d]["fbm"], 2) for d in labels],
            "orders": [by_day[d]["orders"] for d in labels],
            "margin": [round(by_day[d]["margin"], 2) for d in labels],
        }
        best_day = max(labels, key=lambda d: by_day[d]["fba"] + by_day[d]["fbm"]) if orders else False

        # ---- refunds -------------------------------------------------------
        Return = self.env["chi.amazon.return"]
        returns = Return.search(base + [("return_date", ">=", start.date())])
        refunds = sum(returns.mapped("refund_amount"))

        # ---- marketplaces ----------------------------------------------------
        mk = defaultdict(float)
        for o in orders:
            mk[o.marketplace_id.name or "—"] += o.amount_total
        marketplaces = sorted(({"name": k, "value": round(v, 2)} for k, v in mk.items()),
                              key=lambda x: -x["value"])

        # ---- products ----------------------------------------------------------
        lines = self.env["chi.amazon.order.line"].search([("order_id", "in", orders.ids)])
        prod = defaultdict(lambda: {"units": 0, "sales": 0.0, "cost": 0.0, "listing": False, "sku": "", "title": ""})
        for line in lines:
            key = line.listing_id.id or line.seller_sku
            p = prod[key]
            p["units"] += line.quantity
            p["sales"] += line.subtotal
            p["cost"] += line.cost
            p["listing"] = line.listing_id.id
            p["sku"] = line.seller_sku
            p["title"] = line.listing_id.title or line.title
            p["image"] = line.listing_id.image_url or False
        returned = defaultdict(int)
        for r in returns:
            returned[r.listing_id.id or r.seller_sku] += r.quantity
        products = []
        for key, p in prod.items():
            products.append({
                "id": p["listing"], "sku": p["sku"], "title": p["title"], "image": p.get("image"),
                "units": int(p["units"]), "sales": round(p["sales"], 2),
                "margin_pct": p["sales"] and round((p["sales"] - p["cost"]) / p["sales"] * 100, 1) or 0,
                "returned": returned.get(key, 0),
                "no_cost": not p["cost"],
            })
        top = sorted(products, key=lambda x: -x["sales"])[:8]
        problems = sorted(
            [p for p in products if p["returned"] or p["no_cost"] or p["margin_pct"] < 15],
            key=lambda x: (-(x["returned"] / (x["units"] or 1)), x["margin_pct"]))[:6]
        for p in problems:
            rate = p["units"] and p["returned"] / p["units"] * 100 or 0
            p["why"] = (rate and "%d%% returned" % rate) or (p["no_cost"] and "No cost in Odoo") or "Low margin"
            p["why_level"] = "danger" if rate >= 30 or p["no_cost"] else "warning"

        # ---- stock health ----------------------------------------------------
        Listing = self.env["chi.amazon.listing"]
        listings = Listing.search(base)
        health = defaultdict(int)
        for l in listings:
            health[l.stock_health] += 1

        # ---- attention chips ---------------------------------------------------
        Settlement = self.env["chi.amazon.settlement"]
        attention = [
            {"key": "orders_error", "label": "Orders need attention", "level": "danger",
             "count": Order.search_count(base + [("state", "=", "error")])},
            {"key": "orders_new", "label": "Orders to process", "level": "info",
             "count": Order.search_count(base + [("state", "=", "new"), ("amazon_status", "!=", "pending")])},
            {"key": "unmapped", "label": "SKUs not mapped", "level": "warning", "count": health["unmapped"]},
            {"key": "oversell", "label": "Oversell risk", "level": "danger", "count": health["oversell"]},
            {"key": "returns", "label": "Returns to handle", "level": "warning",
             "count": Return.search_count(base + [("state", "in", ("new", "to_receive", "claim"))])},
            {"key": "settlements", "label": "Payouts to book", "level": "info",
             "count": Settlement.search_count(base + [("state", "!=", "posted")])},
            {"key": "no_cost", "label": "Products with no cost", "level": "warning",
             "count": len({l.product_id.id for l in listings if l.product_id and not l.product_id.standard_price})},
        ]

        payouts = [{
            "id": s.id, "name": s.name, "date": s.deposit_date and fields.Date.to_string(s.deposit_date.date()),
            "amount": s.deposit_amount, "fees": s.total_fees, "state": s.state,
        } for s in Settlement.search(base, limit=5, order="deposit_date desc")]

        logs = [{
            "id": l.id, "operation": dict(l._fields["operation"].selection).get(l.operation),
            "state": l.state, "records": l.record_count, "account": l.account_id.name,
            "date": fields.Datetime.to_string(l.date_start), "message": (l.message or "")[:140],
        } for l in self.env["chi.amazon.sync.log"].search(base, limit=6)]

        def delta(a, b):
            return b and round((a - b) / abs(b) * 100, 1) or (100.0 if a else 0.0)

        kpis = [
            {"key": "sales", "label": "Sales", "value": cur["sales"], "money": True,
             "delta": delta(cur["sales"], old["sales"]), "sub": "%s orders" % cur["orders"],
             "spark": [a + b for a, b in zip(series["fba"], series["fbm"])]},
            {"key": "orders", "label": "Orders", "value": cur["orders"], "delta": delta(cur["orders"], old["orders"]),
             "sub": "%s units" % cur["units"], "spark": series["orders"]},
            {"key": "aov", "label": "Avg. order", "value": cur["aov"], "money": True,
             "delta": delta(cur["aov"], old["aov"]), "sub": "per order"},
            {"key": "fees", "label": "Amazon fees", "value": cur["fees"], "money": True, "inverse": True,
             "delta": delta(cur["fees"], old["fees"]),
             "sub": "%.1f%% of sales" % (cur["sales"] and cur["fees"] / cur["sales"] * 100 or 0)},
            {"key": "margin", "label": "Net margin", "value": cur["margin"], "money": True,
             "delta": delta(cur["margin"], old["margin"]), "sub": "%.0f%% after cost & fees" % cur["margin_pct"],
             "spark": series["margin"]},
            {"key": "refunds", "label": "Refunds", "value": refunds, "money": True, "inverse": True,
             "delta": 0, "sub": "%s returns" % len(returns)},
        ]

        return {
            "currency": {"symbol": currency.symbol, "position": currency.position, "name": currency.name},
            "accounts": [{"id": a.id, "name": a.name, "state": a.state} for a in accounts],
            "account_id": account_id, "days": days, "fulfillment": fulfillment,
            "kpis": kpis, "series": series, "best_day": best_day,
            "marketplaces": marketplaces, "top": top, "problems": problems,
            "health": dict(health), "listing_total": len(listings),
            "attention": [a for a in attention if a["count"]],
            "payouts": payouts, "logs": logs,
            "last_sync": max((a.last_sync for a in accounts if a.last_sync), default=False)
            and fields.Datetime.to_string(max(a.last_sync for a in accounts if a.last_sync)),
        }

    @api.model
    def get_attention_action(self, key, account_id=False):
        base = [("account_id", "=", account_id)] if account_id else []
        mapping = {
            "orders_error": ("chi.amazon.order", "Orders that need attention", base + [("state", "=", "error")]),
            "orders_new": ("chi.amazon.order", "Orders to process", base + [("state", "=", "new")]),
            "unmapped": ("chi.amazon.listing", "Unmapped SKUs", base + [("product_id", "=", False)]),
            "oversell": ("chi.amazon.listing", "Oversell risk", base + [("stock_health", "=", "oversell")]),
            "returns": ("chi.amazon.return", "Returns to handle", base + [("state", "in", ("new", "to_receive", "claim"))]),
            "settlements": ("chi.amazon.settlement", "Payouts to book", base + [("state", "!=", "posted")]),
            "no_cost": ("chi.amazon.listing", "Products with no cost",
                        base + [("product_id", "!=", False), ("product_id.standard_price", "=", 0)]),
        }
        model, name, domain = mapping[key]
        return {"type": "ir.actions.act_window", "res_model": model, "name": name, "domain": domain,
                "views": [[False, "list"], [False, "form"]], "target": "current"}
