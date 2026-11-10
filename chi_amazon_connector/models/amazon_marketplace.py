# -*- coding: utf-8 -*-
from odoo import fields, models


class AmazonMarketplace(models.Model):
    _name = "chi.amazon.marketplace"
    _description = "Amazon Marketplace"
    _order = "region, sequence, name"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    code = fields.Char("Marketplace ID", required=True, help="Amazon marketplace identifier, e.g. A2EUQ1WTGCTBG2")
    domain = fields.Char(help="Storefront domain, e.g. amazon.ca")
    region = fields.Selection([("na", "North America"), ("eu", "Europe / Middle East / India"),
                               ("fe", "Far East")], required=True)
    country_id = fields.Many2one("res.country")
    currency_id = fields.Many2one("res.currency")
    active = fields.Boolean(default=True)

    _code_unique = models.Constraint("unique(code)", "This marketplace already exists.")

    def product_url(self, asin):
        self.ensure_one()
        return "https://www.%s/dp/%s" % (self.domain or "amazon.com", asin)
