# -*- coding: utf-8 -*-
from odoo import fields, models


class SaleOrder(models.Model):
    _inherit = "sale.order"

    amazon_order_id = fields.Many2one("chi.amazon.order", string="Amazon order", readonly=True, copy=False,
                                      index="btree_not_null")

    def action_open_amazon_order(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": "chi.amazon.order",
                "res_id": self.amazon_order_id.id, "view_mode": "form"}
