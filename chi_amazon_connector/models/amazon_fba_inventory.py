# -*- coding: utf-8 -*-
from odoo import api, fields, models


class AmazonFbaInventory(models.Model):
    _name = "chi.amazon.fba.inventory"
    _description = "Amazon FBA Inventory"
    _order = "fulfillable desc, seller_sku"
    _rec_name = "seller_sku"

    account_id = fields.Many2one("chi.amazon.account", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="account_id.company_id", store=True)
    marketplace_id = fields.Many2one("chi.amazon.marketplace", required=True)
    listing_id = fields.Many2one("chi.amazon.listing")
    product_id = fields.Many2one("product.product")
    seller_sku = fields.Char("SKU", required=True, index=True)
    asin = fields.Char("ASIN")
    fnsku = fields.Char("FNSKU")
    title = fields.Char()
    fulfillable = fields.Integer()
    inbound_working = fields.Integer("Inbound — working")
    inbound_shipped = fields.Integer("Inbound — shipped")
    inbound_receiving = fields.Integer("Inbound — receiving")
    reserved = fields.Integer()
    unfulfillable = fields.Integer()
    researching = fields.Integer()
    total = fields.Integer(compute="_compute_totals", store=True)
    inbound_total = fields.Integer("Inbound", compute="_compute_totals", store=True)
    odoo_qty = fields.Integer("In Odoo FBA warehouse", compute="_compute_odoo_qty")
    difference = fields.Integer(compute="_compute_odoo_qty")
    days_of_cover = fields.Float(compute="_compute_odoo_qty", help="Fulfillable units / average daily sales (30d).")
    last_sync = fields.Datetime()

    _sql_constraints = [("sku_unique", "unique(account_id, marketplace_id, seller_sku)", "One FBA line per SKU and marketplace.")]

    @api.depends("fulfillable", "inbound_working", "inbound_shipped", "inbound_receiving",
                 "reserved", "unfulfillable", "researching")
    def _compute_totals(self):
        for rec in self:
            rec.inbound_total = rec.inbound_working + rec.inbound_shipped + rec.inbound_receiving
            rec.total = rec.fulfillable + rec.reserved + rec.unfulfillable + rec.researching

    def _compute_odoo_qty(self):
        for rec in self:
            wh = rec.account_id.fba_warehouse_id
            if rec.product_id and wh:
                rec.odoo_qty = int(rec.product_id.with_context(warehouse_id=wh.id).qty_available)
            else:
                rec.odoo_qty = 0
            rec.difference = rec.total - rec.odoo_qty
            daily = (rec.listing_id.units_30d or 0) / 30.0
            rec.days_of_cover = daily and rec.fulfillable / daily or 0.0

    @api.model
    def _import_from_api(self, account, marketplace, summary):
        details = summary.get("inventoryDetails") or {}
        reserved = (details.get("reservedQuantity") or {}).get("totalReservedQuantity") or 0
        unful = (details.get("unfulfillableQuantity") or {}).get("totalUnfulfillableQuantity") or 0
        research = (details.get("researchingQuantity") or {}).get("totalResearchingQuantity") or 0
        sku = summary["sellerSku"]
        listing = self.env["chi.amazon.listing"].search([
            ("account_id", "=", account.id), ("marketplace_id", "=", marketplace.id), ("seller_sku", "=", sku)], limit=1)
        vals = {
            "asin": summary.get("asin"),
            "fnsku": summary.get("fnSku"),
            "title": summary.get("productName"),
            "fulfillable": details.get("fulfillableQuantity") or 0,
            "inbound_working": details.get("inboundWorkingQuantity") or 0,
            "inbound_shipped": details.get("inboundShippedQuantity") or 0,
            "inbound_receiving": details.get("inboundReceivingQuantity") or 0,
            "reserved": reserved,
            "unfulfillable": unful,
            "researching": research,
            "listing_id": listing.id,
            "product_id": listing.product_id.id,
            "last_sync": fields.Datetime.now(),
        }
        rec = self.search([("account_id", "=", account.id), ("marketplace_id", "=", marketplace.id),
                           ("seller_sku", "=", sku)], limit=1)
        if rec:
            rec.write(vals)
        else:
            rec = self.create(dict(vals, account_id=account.id, marketplace_id=marketplace.id, seller_sku=sku))
        return rec
