# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .sp_api import SPAPIError


class AmazonListing(models.Model):
    _name = "chi.amazon.listing"
    _description = "Amazon Listing"
    _inherit = ["mail.thread"]
    _order = "units_30d desc, seller_sku"
    _rec_name = "seller_sku"

    account_id = fields.Many2one("chi.amazon.account", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="account_id.company_id", store=True)
    marketplace_id = fields.Many2one("chi.amazon.marketplace", required=True, ondelete="restrict")
    currency_id = fields.Many2one(related="marketplace_id.currency_id")
    title = fields.Char()
    seller_sku = fields.Char("SKU", required=True, index=True, tracking=True)
    asin = fields.Char("ASIN", index=True)
    fnsku = fields.Char("FNSKU")
    product_type = fields.Char(help="Amazon product type, required to patch the listing.")
    product_id = fields.Many2one("product.product", tracking=True, index=True,
                                 domain="[('type', '=', 'consu')]")
    image_url = fields.Char()
    amazon_url = fields.Char(compute="_compute_amazon_url")
    fulfillment = fields.Selection([("fbm", "FBM — Merchant"), ("fba", "FBA — Amazon")],
                                   default="fbm", required=True)
    status = fields.Selection([("active", "Active"), ("inactive", "Inactive"),
                               ("incomplete", "Incomplete"), ("deleted", "Deleted")], default="active")
    condition = fields.Selection([("new", "New"), ("used", "Used"), ("refurbished", "Refurbished"),
                                  ("collectible", "Collectible")], default="new")
    price = fields.Monetary(tracking=True)
    amazon_qty = fields.Integer("Qty on Amazon")
    push_enabled = fields.Boolean("Sync stock", default=True)
    odoo_qty = fields.Integer("Free qty in Odoo", compute="_compute_stock")
    qty_to_push = fields.Integer(compute="_compute_stock")
    stock_health = fields.Selection([
        ("ok", "In sync"), ("oversell", "Oversell risk"), ("under", "Under-listed"),
        ("unmapped", "No product"), ("fba", "Amazon stock"), ("off", "Not synced"),
    ], compute="_compute_stock", search="_search_stock_health")
    last_push = fields.Datetime(readonly=True)
    push_error = fields.Text(readonly=True)

    cost = fields.Float(related="product_id.standard_price", string="Unit cost")
    margin_pct = fields.Float("Margin %", compute="_compute_margin")
    units_30d = fields.Integer("Units (30d)", compute="_compute_sales", store=True)
    revenue_30d = fields.Monetary("Sales (30d)", compute="_compute_sales", store=True)
    order_line_ids = fields.One2many("chi.amazon.order.line", "listing_id")

    _sql_constraints = [("sku_unique", "unique(account_id, marketplace_id, seller_sku)", "This SKU already exists for this marketplace.")]

    @api.depends("asin", "marketplace_id.domain")
    def _compute_amazon_url(self):
        for rec in self:
            rec.amazon_url = rec.asin and rec.marketplace_id and rec.marketplace_id.product_url(rec.asin)

    @api.depends("product_id", "amazon_qty", "fulfillment", "push_enabled", "account_id.stock_buffer",
                 "account_id.warehouse_id")
    def _compute_stock(self):
        for rec in self:
            if not rec.product_id:
                rec.odoo_qty = rec.qty_to_push = 0
                rec.stock_health = "unmapped"
                continue
            wh = rec.account_id.warehouse_id
            product = rec.product_id.with_context(warehouse_id=wh.id) if wh else rec.product_id
            rec.odoo_qty = int(product.free_qty)
            rec.qty_to_push = max(rec.odoo_qty - rec.account_id.stock_buffer, 0)
            if rec.fulfillment == "fba":
                rec.stock_health = "fba"
            elif not rec.push_enabled:
                rec.stock_health = "off"
            elif rec.amazon_qty > rec.qty_to_push:
                rec.stock_health = "oversell"
            elif rec.amazon_qty < rec.qty_to_push:
                rec.stock_health = "under"
            else:
                rec.stock_health = "ok"

    def _search_stock_health(self, operator, value):
        if operator not in ("=", "in"):
            raise UserError(_("Unsupported search on stock health."))
        values = [value] if operator == "=" else value
        candidates = self.search([])
        return [("id", "in", candidates.filtered(lambda r: r.stock_health in values).ids)]

    @api.depends("price", "cost")
    def _compute_margin(self):
        for rec in self:
            rec.margin_pct = rec.price and (rec.price - rec.cost) / rec.price * 100 or 0.0

    @api.depends("order_line_ids.quantity", "order_line_ids.subtotal", "order_line_ids.order_id.purchase_date")
    def _compute_sales(self):
        limit = fields.Datetime.now() - timedelta(days=30)
        for rec in self:
            lines = rec.order_line_ids.filtered(
                lambda l: l.order_id.purchase_date and l.order_id.purchase_date >= limit
                and l.order_id.amazon_status != "canceled")
            rec.units_30d = int(sum(lines.mapped("quantity")))
            rec.revenue_30d = sum(lines.mapped("subtotal"))

    # ------------------------------------------------------------- mapping
    def _find_product(self):
        self.ensure_one()
        Product = self.env["product.product"]
        for domain in ([("default_code", "=", self.seller_sku)], [("barcode", "=", self.seller_sku)],
                       [("amazon_sku_alias", "ilike", self.seller_sku)]):
            product = Product.search(domain, limit=2)
            if len(product) == 1:
                return product
        return Product

    def action_auto_map(self):
        mapped = 0
        for rec in self.filtered(lambda r: not r.product_id):
            product = rec._find_product()
            if product:
                rec.product_id = product
                mapped += 1
        return {
            "type": "ir.actions.client", "tag": "display_notification",
            "params": {"type": "success" if mapped else "warning", "title": _("Auto-mapping"),
                       "message": _("%(m)s of %(t)s listings matched by SKU / barcode.", m=mapped, t=len(self))},
        }

    @api.model
    def _import_from_api(self, account, marketplace, item):
        summary = next((s for s in item.get("summaries", []) if s.get("marketplaceId") == marketplace.code), {})
        offer = next((o for o in item.get("offers", []) if o.get("marketplaceId") == marketplace.code), {})
        avail = item.get("fulfillmentAvailability") or []
        fba = any(a.get("fulfillmentChannelCode", "DEFAULT") != "DEFAULT" for a in avail)
        qty = sum(a.get("quantity") or 0 for a in avail if a.get("fulfillmentChannelCode") == "DEFAULT")
        statuses = summary.get("status") or []
        status = "active" if "BUYABLE" in statuses else ("incomplete" if not statuses else "inactive")
        vals = {
            "title": summary.get("itemName"),
            "asin": summary.get("asin"),
            "fnsku": summary.get("fnSku"),
            "product_type": summary.get("productType"),
            "image_url": (summary.get("mainImage") or {}).get("link"),
            "condition": (summary.get("conditionType") or "new_new").split("_")[0] if summary.get("conditionType") else "new",
            "status": status,
            "fulfillment": "fba" if fba else "fbm",
            "price": float(((offer.get("price") or {}).get("amount")) or 0.0),
            "amazon_qty": qty,
        }
        if vals["condition"] not in ("new", "used", "refurbished", "collectible"):
            vals["condition"] = "new"
        listing = self.search([("account_id", "=", account.id), ("marketplace_id", "=", marketplace.id),
                               ("seller_sku", "=", item["sku"])], limit=1)
        if listing:
            listing.write(vals)
        else:
            listing = self.create(dict(vals, account_id=account.id, marketplace_id=marketplace.id,
                                       seller_sku=item["sku"]))
        if not listing.product_id:
            listing.product_id = listing._find_product()
        return listing

    # ------------------------------------------------------------- push
    def _push_quantity(self):
        count = 0
        by_account = {}
        for rec in self:
            by_account.setdefault(rec.account_id, self.browse())
            by_account[rec.account_id] |= rec
        for account, listings in by_account.items():
            client = account._get_client()
            for rec in listings:
                qty = rec.qty_to_push
                if qty == rec.amazon_qty:
                    continue
                try:
                    client.patch_listing_quantity(account.seller_id, rec.seller_sku, rec.marketplace_id.code,
                                                  rec.product_type, qty)
                    rec.write({"amazon_qty": qty, "last_push": fields.Datetime.now(), "push_error": False})
                    count += 1
                except SPAPIError as e:
                    rec.push_error = str(e)
        return count

    def action_push_quantity(self):
        listings = self.filtered(lambda l: l.fulfillment == "fbm" and l.product_id)
        if not listings:
            raise UserError(_("Only merchant-fulfilled listings linked to a product can be pushed."))
        count = listings._push_quantity()
        return {"type": "ir.actions.client", "tag": "display_notification",
                "params": {"type": "success", "title": _("Stock pushed"),
                           "message": _("%s listing(s) updated on Amazon.", count)}}

    def action_open_amazon(self):
        self.ensure_one()
        if not self.amazon_url:
            raise UserError(_("This listing has no ASIN yet."))
        return {"type": "ir.actions.act_url", "url": self.amazon_url, "target": "new"}
