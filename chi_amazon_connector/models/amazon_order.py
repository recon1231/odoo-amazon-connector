# -*- coding: utf-8 -*-
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_round

_logger = logging.getLogger(__name__)

AMAZON_STATUS = [
    ("pending", "Pending"),
    ("unshipped", "Unshipped"),
    ("partially_shipped", "Partially shipped"),
    ("shipped", "Shipped"),
    ("canceled", "Canceled"),
    ("unfulfillable", "Unfulfillable"),
    ("invoice_unconfirmed", "Invoice unconfirmed"),
    ("pending_availability", "Pending availability"),
]
STATUS_MAP = {
    "Pending": "pending", "Unshipped": "unshipped", "PartiallyShipped": "partially_shipped",
    "Shipped": "shipped", "Canceled": "canceled", "Unfulfillable": "unfulfillable",
    "InvoiceUnconfirmed": "invoice_unconfirmed", "PendingAvailability": "pending_availability",
}


def _money(node):
    return float((node or {}).get("Amount") or 0.0)


class AmazonOrder(models.Model):
    _name = "chi.amazon.order"
    _description = "Amazon Order"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "purchase_date desc, id desc"

    name = fields.Char("Amazon Order", required=True, index=True, copy=False)
    account_id = fields.Many2one("chi.amazon.account", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="account_id.company_id", store=True)
    marketplace_id = fields.Many2one("chi.amazon.marketplace")
    currency_id = fields.Many2one("res.currency", required=True,
                                  default=lambda s: s.env.company.currency_id)
    purchase_date = fields.Datetime(index=True)
    last_update = fields.Datetime()
    latest_ship_date = fields.Datetime()
    amazon_status = fields.Selection(AMAZON_STATUS, default="pending", tracking=True)
    fulfillment = fields.Selection([("fbm", "FBM"), ("fba", "FBA")], default="fbm", required=True)
    sales_channel = fields.Char()
    is_prime = fields.Boolean("Prime")
    is_business = fields.Boolean("Business order")
    buyer_name = fields.Char()
    ship_city = fields.Char()
    ship_state = fields.Char()
    ship_country_id = fields.Many2one("res.country")

    line_ids = fields.One2many("chi.amazon.order.line", "order_id", copy=True)
    amount_untaxed = fields.Monetary(compute="_compute_amounts", store=True)
    amount_tax = fields.Monetary(compute="_compute_amounts", store=True)
    amount_shipping = fields.Monetary(compute="_compute_amounts", store=True)
    amount_total = fields.Monetary(compute="_compute_amounts", store=True)
    amount_cost = fields.Monetary("Product cost", compute="_compute_amounts", store=True)
    amount_fees = fields.Monetary("Amazon fees", help="Filled from settlements once Amazon pays out.")
    margin = fields.Monetary(compute="_compute_margin", store=True)
    margin_pct = fields.Float("Margin %", compute="_compute_margin", store=True, aggregator="avg")
    units = fields.Integer(compute="_compute_amounts", store=True)

    state = fields.Selection([
        ("new", "To process"), ("done", "In Odoo"), ("error", "Needs attention"), ("cancel", "Canceled"),
    ], default="new", required=True, tracking=True, index=True)
    error_message = fields.Text()
    sale_order_id = fields.Many2one("sale.order", copy=False, readonly=True)
    invoice_status = fields.Selection(related="sale_order_id.invoice_status")
    invoice_count = fields.Integer(compute="_compute_invoice_count")
    return_ids = fields.One2many("chi.amazon.return", "order_id")

    _name_unique = models.Constraint("unique(account_id, name)", "This Amazon order was already imported.")

    @api.depends("line_ids.subtotal", "line_ids.tax", "line_ids.shipping", "line_ids.cost", "line_ids.quantity")
    def _compute_amounts(self):
        for rec in self:
            lines = rec.line_ids
            rec.amount_untaxed = sum(lines.mapped("subtotal"))
            rec.amount_tax = sum(lines.mapped("tax"))
            rec.amount_shipping = sum(lines.mapped("shipping"))
            rec.amount_total = rec.amount_untaxed + rec.amount_tax + rec.amount_shipping
            rec.amount_cost = sum(lines.mapped("cost"))
            rec.units = int(sum(lines.mapped("quantity")))

    @api.depends("amount_untaxed", "amount_shipping", "amount_cost", "amount_fees")
    def _compute_margin(self):
        for rec in self:
            revenue = rec.amount_untaxed + rec.amount_shipping
            rec.margin = revenue - rec.amount_cost - abs(rec.amount_fees)
            rec.margin_pct = revenue and rec.margin / revenue * 100 or 0.0

    def _compute_invoice_count(self):
        for rec in self:
            rec.invoice_count = len(rec.sale_order_id.invoice_ids)

    # ------------------------------------------------------------ import
    @api.model
    def _import_from_api(self, account, data, items):
        marketplace = self.env["chi.amazon.marketplace"].search([("code", "=", data.get("MarketplaceId"))], limit=1)
        total = data.get("OrderTotal") or {}
        currency = self.env["res.currency"].with_context(active_test=False).search(
            [("name", "=", total.get("CurrencyCode") or marketplace.currency_id.name)], limit=1)
        address = data.get("ShippingAddress") or {}
        country = address.get("CountryCode") and self.env["res.country"].search(
            [("code", "=", address["CountryCode"])], limit=1)
        vals = {
            "account_id": account.id,
            "marketplace_id": marketplace.id,
            "currency_id": (currency or marketplace.currency_id or account.currency_id).id,
            "purchase_date": self._parse_dt(data.get("PurchaseDate")),
            "last_update": self._parse_dt(data.get("LastUpdateDate")),
            "latest_ship_date": self._parse_dt(data.get("LatestShipDate")),
            "amazon_status": STATUS_MAP.get(data.get("OrderStatus"), "pending"),
            "fulfillment": "fba" if data.get("FulfillmentChannel") == "AFN" else "fbm",
            "sales_channel": data.get("SalesChannel"),
            "is_prime": bool(data.get("IsPrime")),
            "is_business": bool(data.get("IsBusinessOrder")),
            "buyer_name": (data.get("BuyerInfo") or {}).get("BuyerName") or _("Amazon Customer"),
            "ship_city": address.get("City"),
            "ship_state": address.get("StateOrRegion"),
            "ship_country_id": country.id if country else False,
        }
        order = self.search([("account_id", "=", account.id), ("name", "=", data["AmazonOrderId"])], limit=1)
        lines = [(5, 0, 0)] + [(0, 0, self.env["chi.amazon.order.line"]._vals_from_api(account, marketplace, i))
                               for i in items]
        if order:
            if order.state in ("new", "error"):
                vals["line_ids"] = lines
            order.write(vals)
            order._sync_status_to_odoo()
        else:
            order = self.create(dict(vals, name=data["AmazonOrderId"], line_ids=lines))
        return order

    @staticmethod
    def _parse_dt(value):
        if not value:
            return False
        return fields.Datetime.to_datetime(value.replace("T", " ").replace("Z", "")[:19])

    def _sync_status_to_odoo(self):
        for rec in self:
            if rec.amazon_status == "canceled" and rec.sale_order_id and rec.sale_order_id.state != "cancel":
                if not rec.sale_order_id.invoice_ids.filtered(lambda m: m.state == "posted"):
                    rec.sale_order_id._action_cancel()
                    rec.state = "cancel"
                else:
                    rec.message_post(body=_("Amazon canceled this order but it is already invoiced: "
                                            "issue a credit note."))
            elif rec.amazon_status == "canceled" and not rec.sale_order_id:
                rec.state = "cancel"

    # ------------------------------------------------------------ process
    def _process(self):
        """Turn imported Amazon orders into confirmed sale orders, deliveries and invoices."""
        for rec in self:
            if rec.amazon_status == "canceled":
                rec.state = "cancel"
                continue
            if rec.amazon_status == "pending" or not rec.account_id.auto_create_order:
                continue  # Amazon has not charged the buyer yet
            try:
                with self.env.cr.savepoint():
                    rec._create_sale_order()
                    rec.write({"state": "done", "error_message": False})
            except UserError as e:
                rec.write({"state": "error", "error_message": str(e)})
            except Exception as e:  # noqa: BLE001 - one bad order must not stop the batch
                _logger.exception("Amazon order %s failed", rec.name)
                rec.write({"state": "error", "error_message": str(e)})

    def _check_ready(self):
        self.ensure_one()
        missing = self.line_ids.filtered(lambda l: not l.product_id)
        if missing:
            raise UserError(_("No Odoo product for SKU(s): %s. Map the listing, then retry.",
                              ", ".join(missing.mapped("seller_sku"))))
        if not self.account_id.customer_id:
            raise UserError(_("Set the Amazon customer on account %s.", self.account_id.name))

    def _prepare_sale_order(self):
        account = self.account_id
        warehouse = (account.fba_warehouse_id if self.fulfillment == "fba" else account.warehouse_id) \
            or account.warehouse_id
        lines = []
        for line in self.line_ids:
            lines.append((0, 0, {
                "product_id": line.product_id.id,
                "name": line.title or line.product_id.display_name,
                "product_uom_qty": line.quantity,
                "price_unit": line.quantity and float_round(line.subtotal / line.quantity, 4) or 0.0,
                "tax_ids": [(6, 0, line.product_id.taxes_id.filtered(lambda t: t.company_id == account.company_id).ids)]
                if not account.fiscal_position_id else False,
            }))
        if self.amount_shipping and account.shipping_product_id:
            lines.append((0, 0, {
                "product_id": account.shipping_product_id.id,
                "name": _("Amazon shipping"),
                "product_uom_qty": 1,
                "price_unit": self.amount_shipping,
            }))
        vals = {
            "partner_id": account.customer_id.id,
            "client_order_ref": self.name,
            "origin": _("Amazon %s", self.marketplace_id.name or ""),
            "company_id": account.company_id.id,
            "date_order": self.purchase_date or fields.Datetime.now(),
            "team_id": account.team_id.id,
            "fiscal_position_id": account.fiscal_position_id.id,
            "amazon_order_id": self.id,
            "order_line": lines,
        }
        if warehouse:
            vals["warehouse_id"] = warehouse.id
        pricelist = self.env["product.pricelist"].search(
            [("currency_id", "=", self.currency_id.id), ("company_id", "in", (False, account.company_id.id))], limit=1)
        if pricelist:
            vals["pricelist_id"] = pricelist.id
        return vals

    def _create_sale_order(self):
        self.ensure_one()
        if self.sale_order_id:
            return self.sale_order_id
        self._check_ready()
        account = self.account_id
        so = self.env["sale.order"].with_company(account.company_id).create(self._prepare_sale_order())
        # keep the Amazon price, never let a pricelist recompute it
        self.sale_order_id = so
        if account.auto_confirm_order:
            so.action_confirm()
            if self.fulfillment == "fba" and account.auto_validate_fba:
                for picking in so.picking_ids.filtered(lambda p: p.state not in ("done", "cancel")):
                    for move in picking.move_ids:
                        move.quantity = move.product_uom_qty
                        move.picked = True
                    picking.with_context(cancel_backorder=True, skip_sms=True)._action_done()
            if account.auto_invoice and so.invoice_status == "to invoice":
                invoices = so._create_invoices()
                if account.auto_post_invoice:
                    invoices.action_post()
        return so

    # ------------------------------------------------------------ actions
    def action_process(self):
        self._process()
        errors = self.filtered(lambda o: o.state == "error")
        if len(self) == 1 and errors:
            raise UserError(errors.error_message)
        return True

    def action_reset(self):
        self.filtered(lambda o: o.state == "error").write({"state": "new", "error_message": False})

    def action_open_sale_order(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": "sale.order", "res_id": self.sale_order_id.id,
                "view_mode": "form"}

    def action_open_invoices(self):
        self.ensure_one()
        return self.sale_order_id.action_view_invoice()

    def action_open_amazon(self):
        self.ensure_one()
        domain = {"na": "sellercentral.amazon.com", "eu": "sellercentral-europe.amazon.com",
                  "fe": "sellercentral.amazon.co.jp"}[self.account_id.region]
        return {"type": "ir.actions.act_url", "target": "new",
                "url": "https://%s/orders-v3/order/%s" % (domain, self.name)}


class AmazonOrderLine(models.Model):
    _name = "chi.amazon.order.line"
    _description = "Amazon Order Line"

    order_id = fields.Many2one("chi.amazon.order", required=True, ondelete="cascade", index=True)
    currency_id = fields.Many2one(related="order_id.currency_id")
    order_item_id = fields.Char()
    seller_sku = fields.Char("SKU")
    asin = fields.Char("ASIN")
    title = fields.Char()
    listing_id = fields.Many2one("chi.amazon.listing", index=True)
    product_id = fields.Many2one("product.product")
    quantity = fields.Float(default=1.0)
    subtotal = fields.Monetary("Item price", help="Item price excluding tax (all units).")
    tax = fields.Monetary()
    shipping = fields.Monetary()
    promotion = fields.Monetary()
    cost = fields.Monetary(compute="_compute_cost", store=True)

    @api.depends("product_id", "quantity")
    def _compute_cost(self):
        for line in self:
            line.cost = line.product_id.standard_price * line.quantity

    @api.model
    def _vals_from_api(self, account, marketplace, item):
        listing = self.env["chi.amazon.listing"].search([
            ("account_id", "=", account.id), ("seller_sku", "=", item.get("SellerSKU")),
            ("marketplace_id", "=", marketplace.id)], limit=1)
        product = listing.product_id
        if not product and item.get("SellerSKU"):
            product = self.env["product.product"].search([("default_code", "=", item["SellerSKU"])], limit=1)
        qty = float(item.get("QuantityOrdered") or 0)
        promo = _money(item.get("PromotionDiscount")) + _money(item.get("ShippingDiscount"))
        return {
            "order_item_id": item.get("OrderItemId"),
            "seller_sku": item.get("SellerSKU"),
            "asin": item.get("ASIN"),
            "title": item.get("Title"),
            "listing_id": listing.id,
            "product_id": product.id,
            "quantity": qty,
            "subtotal": _money(item.get("ItemPrice")) - _money(item.get("PromotionDiscount")),
            "tax": _money(item.get("ItemTax")),
            "shipping": _money(item.get("ShippingPrice")) - _money(item.get("ShippingDiscount")),
            "promotion": promo,
        }
