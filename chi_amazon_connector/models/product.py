# -*- coding: utf-8 -*-
from odoo import _, fields, models


class ProductProduct(models.Model):
    _inherit = "product.product"

    amazon_listing_ids = fields.One2many("chi.amazon.listing", "product_id", string="Amazon listings")
    amazon_listing_count = fields.Integer(compute="_compute_amazon_listing_count")
    amazon_sku_alias = fields.Char("Amazon SKU aliases",
                                   help="Comma separated SKUs used on Amazon for this product (auto-mapping).")

    def _compute_amazon_listing_count(self):
        for rec in self:
            rec.amazon_listing_count = len(rec.amazon_listing_ids)


class ProductTemplate(models.Model):
    _inherit = "product.template"

    amazon_listing_count = fields.Integer(compute="_compute_amazon_listing_count")

    def _compute_amazon_listing_count(self):
        for rec in self:
            rec.amazon_listing_count = sum(rec.product_variant_ids.mapped("amazon_listing_count"))

    def action_open_amazon_listings(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": "chi.amazon.listing", "name": _("Amazon listings"),
                "view_mode": "list,form", "domain": [("product_id", "in", self.product_variant_ids.ids)]}
