# -*- coding: utf-8 -*-
from odoo import fields, models


class AmazonSyncWizard(models.TransientModel):
    _name = "chi.amazon.sync.wizard"
    _description = "Sync Amazon now"

    account_ids = fields.Many2many("chi.amazon.account", string="Accounts",
                                   default=lambda s: s.env["chi.amazon.account"].search([("state", "!=", "draft")]))
    sync_orders = fields.Boolean("Orders", default=True)
    sync_listings = fields.Boolean("Listings & prices", default=True)
    sync_fba = fields.Boolean("FBA inventory", default=True)
    sync_settlements = fields.Boolean("Settlements", default=True)
    push_stock = fields.Boolean("Push FBM stock", default=False)
    orders_from = fields.Datetime("Re-import orders updated after",
                                  help="Leave empty to continue from the last sync.")

    def action_run(self):
        for acc in self.account_ids:
            if self.orders_from:
                acc.orders_synced_until = self.orders_from
            if self.sync_orders:
                acc._run("orders", acc._sync_orders)
            if self.sync_listings:
                acc._run("listings", acc._sync_listings)
            if self.sync_fba:
                acc._run("fba", acc._sync_fba)
            if self.sync_settlements:
                acc._run("settlements", acc._sync_settlements)
            if self.push_stock:
                acc._run("stock", acc._push_stock)
        return self.account_ids._notify("Amazon sync finished")
