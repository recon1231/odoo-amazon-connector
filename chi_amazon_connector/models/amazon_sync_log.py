# -*- coding: utf-8 -*-
from odoo import api, fields, models


class AmazonSyncLog(models.Model):
    _name = "chi.amazon.sync.log"
    _description = "Amazon Sync Log"
    _order = "date_start desc, id desc"

    account_id = fields.Many2one("chi.amazon.account", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="account_id.company_id", store=True)
    operation = fields.Selection([
        ("orders", "Orders"), ("listings", "Listings"), ("stock", "Stock push"),
        ("fba", "FBA inventory"), ("settlements", "Settlements"), ("returns", "Returns"),
    ], required=True)
    date_start = fields.Datetime(default=fields.Datetime.now, required=True)
    date_end = fields.Datetime()
    duration = fields.Float("Seconds", compute="_compute_duration")
    state = fields.Selection([("running", "Running"), ("done", "Done"), ("error", "Failed")],
                             default="running", required=True)
    record_count = fields.Integer("Records")
    message = fields.Text()

    @api.depends("date_start", "date_end")
    def _compute_duration(self):
        for rec in self:
            rec.duration = rec.date_end and (rec.date_end - rec.date_start).total_seconds() or 0.0

    @api.autovacuum
    def _gc_old_logs(self):
        limit = fields.Datetime.subtract(fields.Datetime.now(), days=60)
        self.search([("date_start", "<", limit)]).unlink()
