# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AmazonReturn(models.Model):
    _name = "chi.amazon.return"
    _description = "Amazon Customer Return"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "return_date desc, id desc"

    name = fields.Char(required=True, default=lambda s: _("New"), copy=False)
    account_id = fields.Many2one("chi.amazon.account", required=True, ondelete="cascade")
    company_id = fields.Many2one(related="account_id.company_id", store=True)
    currency_id = fields.Many2one(related="account_id.currency_id")
    order_id = fields.Many2one("chi.amazon.order", index=True)
    amazon_order_ref = fields.Char("Amazon order", required=True)
    return_date = fields.Date(default=fields.Date.context_today)
    fulfillment = fields.Selection([("fbm", "FBM"), ("fba", "FBA")], default="fba")
    seller_sku = fields.Char("SKU")
    listing_id = fields.Many2one("chi.amazon.listing")
    product_id = fields.Many2one("product.product")
    quantity = fields.Integer(default=1)
    reason = fields.Selection([
        ("not_as_described", "Not as described"), ("defective", "Defective"), ("damaged", "Damaged in transit"),
        ("wrong_item", "Wrong item"), ("no_longer_needed", "No longer needed"), ("not_compatible", "Not compatible"),
        ("other", "Other"),
    ], default="other", tracking=True)
    customer_comment = fields.Text()
    disposition = fields.Selection([
        ("sellable", "Sellable"), ("damaged", "Damaged"), ("customer_damaged", "Customer damaged"),
        ("defective", "Defective"), ("carrier_damaged", "Carrier damaged"), ("unknown", "Unknown"),
    ], default="unknown", tracking=True)
    refund_amount = fields.Monetary()
    reimbursed_amount = fields.Monetary(tracking=True)
    state = fields.Selection([
        ("new", "New"), ("to_receive", "Waiting item"), ("received", "Received"),
        ("claim", "Claim open"), ("closed", "Closed"),
    ], default="new", tracking=True, required=True, group_expand=True)
    loss = fields.Monetary(compute="_compute_loss", store=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("chi.amazon.return") or _("New")
            if vals.get("amazon_order_ref") and not vals.get("order_id"):
                order = self.env["chi.amazon.order"].search([("name", "=", vals["amazon_order_ref"])], limit=1)
                vals["order_id"] = order.id
        return super().create(vals_list)

    @api.depends("refund_amount", "reimbursed_amount", "disposition")
    def _compute_loss(self):
        for rec in self:
            rec.loss = 0.0 if rec.disposition == "sellable" else max(rec.refund_amount - rec.reimbursed_amount, 0.0)

    def action_wait(self):
        self.write({"state": "to_receive"})

    def action_receive(self):
        for rec in self:
            if rec.disposition == "unknown":
                raise UserError(_("Set the condition (disposition) of %s before receiving it.", rec.name))
        self.write({"state": "received"})

    def action_open_claim(self):
        self.write({"state": "claim"})
        for rec in self:
            rec.activity_schedule("mail.mail_activity_data_todo", summary=_("File SAFE-T / reimbursement claim"),
                                  note=_("Open a claim in Seller Central for %s.", rec.amazon_order_ref))

    def action_close(self):
        self.write({"state": "closed"})
