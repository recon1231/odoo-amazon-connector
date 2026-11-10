# -*- coding: utf-8 -*-
"""Realistic demo data (auto-parts seller on Amazon.ca / Amazon.com).

Only loaded when the database has demo data enabled.
"""
import base64
import random
from datetime import timedelta

from odoo import api, fields, models

CATALOG = [
    # sku, title, cost, price, kind
    ("NI-ROG-14-HL-LED-RH", "LED Headlight for Nissan Rogue 2014-2016, Passenger Side", 118, 414, "headlight"),
    ("NI-ROG-14-HL-LED-LH", "LED Headlight for Nissan Rogue 2014-2016, Driver Side", 118, 414, "headlight"),
    ("NI-KIC-25-HL-RH", "LED Headlight for Nissan Kicks 2025-2026, Passenger Side", 142, 550, "headlight"),
    ("NI-ROG-21-HL-BLK-LH", "Black Bezel LED Headlight for Nissan Rogue 2021-2023, Driver", 160, 501, "headlight"),
    ("NI-ROG-24-GRL", "Front Grille for Nissan Rogue 2024-2025, No Camera", 52, 245, "grille"),
    ("MA-MAZ3-19-HL-RH", "Halogen Headlight for Mazda 3 2019-2026, RH Non-Adaptive", 96, 450, "headlight"),
    ("NI-KIC-21-GRL", "Main Grille for Nissan Kicks 2021-2023", 65, 291, "grille"),
    ("NI-QAS-20-HL-RH", "Halogen Headlight for Nissan Qashqai 2020-2022, Passenger", 111, 420, "headlight"),
    ("TO-COR-23-TL-LH", "LED Tail Light for Toyota Corolla 2023-2025, Driver Side", 74, 289, "taillight"),
    ("KI-FOR-22-FL-RH", "LED Fog Light for Kia Forte 2022-2024, RH", 28, 119, "fog"),
    ("HY-ELA-21-MIR-LH", "Power Side Mirror for Hyundai Elantra 2021-2023, Heated, Driver", 58, 219, "mirror"),
    ("HO-CIV-22-BMP-F", "Front Bumper Cover for Honda Civic 2022-2024, Primed", 89, 329, "bumper"),
    ("TO-RAV-19-GRL", "Upper Grille for Toyota RAV4 2019-2021, Chrome", 61, 0, "grille"),  # no price -> incomplete
    ("FO-ESC-17-HL-LH", "Headlight Assembly for Ford Escape 2017-2019, Driver", 0, 309, "headlight"),  # no cost
]
COLORS = {"headlight": ("#2563eb", "#60a5fa"), "grille": ("#334155", "#94a3b8"), "taillight": ("#dc2626", "#f87171"),
          "fog": ("#f59e0b", "#fde68a"), "mirror": ("#0891b2", "#67e8f9"), "bumper": ("#7c3aed", "#c4b5fd")}
SHAPES = {
    "headlight": '<path d="M18 52c0-14 12-24 30-24h22c8 0 12 6 12 14v8c0 8-4 14-12 14H40c-14 0-22-4-22-12z" fill="url(#g)"/>'
                 '<circle cx="62" cy="46" r="9" fill="#fff" opacity=".85"/><circle cx="40" cy="48" r="6" fill="#fff" opacity=".6"/>',
    "grille": '<rect x="14" y="30" width="72" height="40" rx="10" fill="url(#g)"/>'
              + "".join('<rect x="20" y="%d" width="60" height="3" rx="1.5" fill="#fff" opacity=".5"/>' % y for y in range(36, 66, 6)),
    "taillight": '<path d="M24 30h40c10 0 14 8 14 18v6c0 10-6 16-16 16H30c-6 0-10-4-10-10V36c0-4 2-6 4-6z" fill="url(#g)"/>'
                 '<rect x="30" y="40" width="38" height="5" rx="2.5" fill="#fff" opacity=".7"/>',
    "fog": '<circle cx="50" cy="50" r="24" fill="url(#g)"/><circle cx="50" cy="50" r="12" fill="#fff" opacity=".75"/>',
    "mirror": '<path d="M22 36c0-6 6-10 14-10h30c8 0 12 6 12 14v14c0 10-8 16-18 16H36c-8 0-14-6-14-14z" fill="url(#g)"/>'
              '<path d="M30 40h40v14H30z" fill="#fff" opacity=".35"/>',
    "bumper": '<path d="M12 44c0-6 6-10 14-10h48c8 0 14 4 14 10v12c0 6-6 10-14 10H26c-8 0-14-4-14-10z" fill="url(#g)"/>'
              '<rect x="34" y="50" width="32" height="6" rx="3" fill="#0f172a" opacity=".35"/>',
}


def _svg(kind):
    a, b = COLORS[kind]
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><defs><linearGradient id="g" x1="0" y1="0" '
           'x2="1" y2="1"><stop offset="0" stop-color="%s"/><stop offset="1" stop-color="%s"/></linearGradient>'
           '</defs><rect width="100" height="100" rx="18" fill="#f1f5f9"/>%s</svg>') % (a, b, SHAPES[kind])
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


class AmazonDemo(models.AbstractModel):
    _name = "chi.amazon.demo"
    _description = "Amazon Seller Hub demo data"

    @api.model
    def _create_demo_data(self):
        rnd = random.Random(42)
        env = self.env
        company = env.company
        now = fields.Datetime.now()
        mk_ca = env.ref("chi_amazon_connector.marketplace_ca")
        mk_us = env.ref("chi_amazon_connector.marketplace_us")
        env.ref("base.CAD").active = True

        def account(code, name, atype, reconcile=False):
            acc = env["account.account"].search([("code", "=", code), ("company_ids", "in", company.id)], limit=1)
            return acc or env["account.account"].create({
                "code": code, "name": name, "account_type": atype, "reconcile": reconcile, "company_ids": [(6, 0, company.ids)]})

        journal = env["account.journal"].search([("type", "=", "general"), ("company_id", "=", company.id)], limit=1)
        customer = env["res.partner"].create({"name": "Amazon Customer", "is_company": True,
                                              "comment": "Generic partner for Amazon marketplace orders"})
        warehouse = env["stock.warehouse"].search([("company_id", "=", company.id)], limit=1)
        fba_wh = env["stock.warehouse"].create({"name": "Amazon FBA", "code": "FBA", "company_id": company.id})

        acc = env["chi.amazon.account"].create({
            "name": "Auto Parts — Amazon.ca",
            "region": "na", "environment": "production", "seller_id": "A1DEMO7SELLER",
            "lwa_client_id": "amzn1.application-oa2-client.demo", "lwa_client_secret": "demo",
            "refresh_token": "Atzr|demo",
            "state": "connected",
            "marketplace_ids": [(6, 0, [mk_ca.id, mk_us.id])],
            "customer_id": customer.id, "warehouse_id": warehouse.id, "fba_warehouse_id": fba_wh.id,
            "auto_create_order": True, "auto_confirm_order": True, "auto_validate_fba": False,
            "auto_invoice": False, "stock_push": True, "stock_buffer": 1,
            "settlement_journal_id": journal.id,
            "account_clearing_id": account("AMZ100", "Amazon Receivable (Clearing)", "asset_current", True).id,
            "account_transfer_id": account("AMZ101", "Amazon Payout in Transit", "asset_current", True).id,
            "account_referral_id": account("AMZ600", "Amazon Referral Fees", "expense").id,
            "account_fba_id": account("AMZ601", "Amazon FBA Fees", "expense").id,
            "account_storage_id": account("AMZ602", "Amazon Storage Fees", "expense").id,
            "account_ads_id": account("AMZ603", "Amazon Advertising", "expense").id,
            "account_promotion_id": account("AMZ604", "Amazon Promotions", "expense").id,
            "account_refund_id": account("AMZ605", "Amazon Refunds", "income").id,
            "account_reimbursement_id": account("AMZ606", "Amazon Reimbursements", "income_other").id,
            "account_other_id": account("AMZ609", "Amazon Other Fees", "expense").id,
            "orders_synced_until": now,
        })
        acc_us = env["chi.amazon.account"].create({
            "name": "Auto Parts — Amazon.com (US)", "region": "na", "seller_id": "A1DEMO7SELLER",
            "state": "draft", "marketplace_ids": [(6, 0, [mk_us.id])], "customer_id": customer.id,
        })

        # ---- products & listings ---------------------------------------------
        listings = env["chi.amazon.listing"]
        for i, (sku, title, cost, price, kind) in enumerate(CATALOG):
            product = env["product.product"].create({
                "name": title, "default_code": sku, "type": "consu", "is_storable": True,
                "standard_price": cost, "list_price": price or 299,
            })
            fba = i % 3 == 0
            qty = rnd.randint(0, 14)
            if qty and warehouse:
                env["stock.quant"]._update_available_quantity(product, warehouse.lot_stock_id, qty)
            listing_qty = qty - 1 if i % 5 else qty + 3  # every 5th listing over-lists -> oversell
            listings |= env["chi.amazon.listing"].create({
                "account_id": acc.id, "marketplace_id": mk_ca.id, "seller_sku": sku, "title": title,
                "asin": "B0%08d" % (31415926 + i * 7919), "product_type": "AUTO_PART",
                "product_id": product.id if i != 11 else False,  # one unmapped SKU
                "fulfillment": "fba" if fba else "fbm",
                "status": "incomplete" if not price else "active",
                "price": price, "amazon_qty": max(listing_qty, 0), "image_url": _svg(kind),
            })
        listings |= env["chi.amazon.listing"].create({
            "account_id": acc.id, "marketplace_id": mk_ca.id, "seller_sku": "GEN-HL-UNIV-7IN",
            "title": "7 inch Round LED Headlight, Universal", "asin": "B0DEMO7777", "fulfillment": "fbm",
            "price": 89, "amazon_qty": 6, "image_url": _svg("fog"),
        })
        sellable = listings.filtered(lambda l: l.price)

        # ---- orders (90 days) ----------------------------------------------------
        weights = [9, 7, 6, 5, 5, 4, 4, 4, 3, 3, 2, 2, 1, 1, 2][: len(sellable)]
        Order = env["chi.amazon.order"]
        orders = Order
        for n in range(150):
            age = rnd.random() ** 1.15 * 90
            purchase = now - timedelta(days=age, minutes=rnd.randint(0, 600))
            listing = rnd.choices(sellable, weights=weights[: len(sellable)])[0]
            qty = 1 if rnd.random() < .88 else 2
            subtotal = listing.price * qty
            fba = listing.fulfillment == "fba"
            status = "shipped"
            if age < 1.2:
                status = rnd.choice(["pending", "unshipped", "shipped"])
            elif rnd.random() < .04:
                status = "canceled"
            mk = mk_ca if rnd.random() < .85 else mk_us
            orders |= Order.create({
                "name": "70%d-%07d-%07d" % (rnd.randint(1, 2), rnd.randint(1, 9999999), rnd.randint(1, 9999999)),
                "account_id": acc.id, "marketplace_id": mk.id,
                "currency_id": mk.currency_id.id,
                "purchase_date": purchase, "last_update": purchase + timedelta(hours=3),
                "amazon_status": status, "fulfillment": "fba" if fba else "fbm",
                "sales_channel": "Amazon.ca" if mk == mk_ca else "Amazon.com",
                "is_prime": fba and rnd.random() < .7, "buyer_name": "Amazon Customer",
                "ship_city": rnd.choice(["Toronto", "Mississauga", "Ottawa", "Calgary", "Vancouver", "Montreal", "Hamilton"]),
                "ship_state": rnd.choice(["ON", "ON", "ON", "AB", "BC", "QC"]),
                "ship_country_id": env.ref("base.ca").id,
                "line_ids": [(0, 0, {
                    "order_item_id": str(rnd.randint(10 ** 13, 10 ** 14)), "seller_sku": listing.seller_sku,
                    "asin": listing.asin, "title": listing.title, "listing_id": listing.id,
                    "product_id": listing.product_id.id, "quantity": qty, "subtotal": subtotal,
                    "tax": round(subtotal * .13, 2), "shipping": 0.0 if fba else 14.99,
                })],
                "amount_fees": -round(subtotal * .15 + (qty * 6.4 if fba else 0), 2) if status == "shipped" and age > 7 else 0.0,
            })
        recent = orders.filtered(lambda o: o.purchase_date >= now - timedelta(days=12))
        recent._process()
        (orders - recent).filtered(lambda o: o.amazon_status != "canceled").write({"state": "done"})
        orders.filtered(lambda o: o.amazon_status == "canceled").write({"state": "cancel"})

        # ---- FBA inventory ---------------------------------------------------------
        for l in listings.filtered(lambda x: x.fulfillment == "fba"):
            env["chi.amazon.fba.inventory"].create({
                "account_id": acc.id, "marketplace_id": mk_ca.id, "listing_id": l.id, "product_id": l.product_id.id,
                "seller_sku": l.seller_sku, "asin": l.asin, "fnsku": "X00" + l.asin[-7:], "title": l.title,
                "fulfillable": rnd.randint(2, 40), "inbound_shipped": rnd.choice([0, 0, 6, 12]),
                "reserved": rnd.randint(0, 3), "unfulfillable": rnd.choice([0, 0, 1, 2]), "last_sync": now,
            })

        # ---- returns -----------------------------------------------------------------
        shipped = orders.filtered(lambda o: o.amazon_status == "shipped")
        reasons = ["not_compatible", "defective", "damaged", "no_longer_needed", "not_as_described"]
        states = ["new", "new", "to_receive", "received", "claim", "closed", "closed", "closed"]
        for o in rnd.sample(list(shipped), min(14, len(shipped))):
            line = o.line_ids[:1]
            st = rnd.choice(states)
            env["chi.amazon.return"].create({
                "account_id": acc.id, "order_id": o.id, "amazon_order_ref": o.name,
                "return_date": (o.purchase_date + timedelta(days=rnd.randint(4, 20))).date(),
                "fulfillment": o.fulfillment, "seller_sku": line.seller_sku, "listing_id": line.listing_id.id,
                "product_id": line.product_id.id, "quantity": 1, "reason": rnd.choice(reasons),
                "disposition": "unknown" if st in ("new", "to_receive") else rnd.choice(["sellable", "damaged", "customer_damaged"]),
                "refund_amount": line.subtotal / (line.quantity or 1),
                "reimbursed_amount": line.subtotal * .5 if st == "closed" and rnd.random() < .4 else 0.0,
                "state": st,
            })

        # ---- settlements (every 14 days) -----------------------------------------------
        Settlement = env["chi.amazon.settlement"]
        end = now - timedelta(days=2)
        for k in range(6):
            s_end, s_start = end - timedelta(days=14 * k), end - timedelta(days=14 * (k + 1))
            period = shipped.filtered(lambda o: s_start <= o.purchase_date < s_end and o.marketplace_id == mk_ca)
            if not period:
                continue
            lines = []
            for o in period:
                fba = o.fulfillment == "fba"
                lines += [
                    {"posted_date": o.purchase_date, "transaction_type": "Shipment", "order_ref": o.name,
                     "sku": o.line_ids[:1].seller_sku, "description": "Principal", "category": "principal",
                     "amount": o.amount_untaxed},
                    {"posted_date": o.purchase_date, "transaction_type": "Shipment", "order_ref": o.name,
                     "sku": o.line_ids[:1].seller_sku, "description": "Tax", "category": "tax", "amount": o.amount_tax},
                    {"posted_date": o.purchase_date, "transaction_type": "Shipment", "order_ref": o.name,
                     "sku": o.line_ids[:1].seller_sku, "description": "Commission", "category": "referral",
                     "amount": -round(o.amount_untaxed * .15, 2)},
                ]
                if o.amount_shipping:
                    lines.append({"posted_date": o.purchase_date, "transaction_type": "Shipment", "order_ref": o.name,
                                  "description": "ShippingCharge", "category": "shipping", "amount": o.amount_shipping})
                if fba:
                    lines.append({"posted_date": o.purchase_date, "transaction_type": "Shipment", "order_ref": o.name,
                                  "description": "FBAPerUnitFulfillmentFee", "category": "fba",
                                  "amount": -round(6.4 * o.units, 2)})
            lines += [
                {"posted_date": s_end, "transaction_type": "ServiceFee", "description": "FBA storage fee",
                 "category": "storage", "amount": -round(rnd.uniform(12, 45), 2)},
                {"posted_date": s_end, "transaction_type": "ProductAdsPayment", "description": "Sponsored Products",
                 "category": "advertising", "amount": -round(rnd.uniform(60, 180), 2)},
            ]
            total = round(sum(l["amount"] for l in lines), 2)
            st = Settlement.create({
                "name": "2%010d" % rnd.randint(1, 9999999999), "account_id": acc.id, "currency_id": mk_ca.currency_id.id,
                "start_date": s_start, "end_date": s_end, "deposit_date": s_end + timedelta(days=1),
                "deposit_amount": total, "line_ids": [(0, 0, l) for l in lines],
            })
            if k >= 2:
                st.action_post()
            elif k == 1:
                st.action_create_entry()

        # ---- sync log ------------------------------------------------------------------
        Log = env["chi.amazon.sync.log"]
        for minutes, op, state, count, msg in [
            (6, "orders", "done", 4, ""), (21, "stock", "done", 3, ""), (36, "orders", "done", 2, ""),
            (180, "listings", "done", len(listings), ""), (182, "fba", "done", 5, ""),
            (185, "settlements", "error", 0, "SP-API GET /finances/v0/financialEventGroups failed (429): QuotaExceeded"),
        ]:
            start = now - timedelta(minutes=minutes)
            Log.create({"account_id": acc.id, "operation": op, "state": state, "record_count": count,
                        "message": msg, "date_start": start, "date_end": start + timedelta(seconds=rnd.randint(2, 40))})
        listings._compute_sales()
        return acc | acc_us
