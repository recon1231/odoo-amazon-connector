# -*- coding: utf-8 -*-
import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .sp_api import SPAPIClient, SPAPIError

_logger = logging.getLogger(__name__)


class AmazonAccount(models.Model):
    _name = "chi.amazon.account"
    _description = "Amazon Seller Account"
    _inherit = ["mail.thread"]
    _order = "sequence, name"

    name = fields.Char(required=True, tracking=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    color = fields.Integer()
    company_id = fields.Many2one("res.company", required=True, default=lambda s: s.env.company)
    currency_id = fields.Many2one(related="company_id.currency_id")

    # --- connection -------------------------------------------------------
    region = fields.Selection([("na", "North America"), ("eu", "Europe / Middle East / India"),
                               ("fe", "Far East")], required=True, default="na", tracking=True)
    environment = fields.Selection([("production", "Production"), ("sandbox", "Sandbox")],
                                   required=True, default="production", tracking=True)
    seller_id = fields.Char("Seller ID (Merchant Token)", tracking=True)
    lwa_client_id = fields.Char("LWA Client ID", groups="chi_amazon_connector.group_amazon_manager")
    lwa_client_secret = fields.Char("LWA Client Secret", groups="chi_amazon_connector.group_amazon_manager")
    refresh_token = fields.Char(groups="chi_amazon_connector.group_amazon_manager")
    access_token = fields.Char(groups="base.group_system", copy=False)
    token_expiry = fields.Datetime(groups="base.group_system", copy=False)
    state = fields.Selection([("draft", "Not connected"), ("connected", "Connected"), ("error", "Error")],
                             default="draft", required=True, tracking=True, copy=False)
    last_error = fields.Text(copy=False)
    marketplace_ids = fields.Many2many("chi.amazon.marketplace", string="Marketplaces",
                                       domain="[('region', '=', region)]")
    default_marketplace_id = fields.Many2one("chi.amazon.marketplace", compute="_compute_default_marketplace",
                                             store=True)

    # --- sales ------------------------------------------------------------
    customer_id = fields.Many2one("res.partner", string="Amazon Customer",
                                  help="Partner used on every Amazon sale order (buyer PII is restricted by Amazon).")
    team_id = fields.Many2one("crm.team", string="Sales Team")
    warehouse_id = fields.Many2one("stock.warehouse", string="FBM Warehouse",
                                   help="Ships merchant-fulfilled orders and is the stock source for quantity push.")
    fba_warehouse_id = fields.Many2one("stock.warehouse", string="FBA Warehouse",
                                       help="Virtual warehouse that represents stock held by Amazon.")
    fiscal_position_id = fields.Many2one("account.fiscal.position")
    shipping_product_id = fields.Many2one("product.product", domain="[('type', '=', 'service')]")
    auto_create_order = fields.Boolean("Create sale orders", default=True)
    auto_confirm_order = fields.Boolean("Confirm sale orders", default=True)
    auto_validate_fba = fields.Boolean("Validate FBA deliveries", default=True,
                                       help="FBA orders are already shipped by Amazon: validate the delivery automatically.")
    auto_invoice = fields.Boolean("Create invoices", default=True)
    auto_post_invoice = fields.Boolean("Post invoices", default=False)
    orders_synced_until = fields.Datetime(copy=False)
    order_history_days = fields.Integer(default=30, help="How far back the first order import goes.")

    # --- stock ------------------------------------------------------------
    stock_push = fields.Boolean("Push FBM stock to Amazon", default=False, tracking=True)
    stock_buffer = fields.Integer(default=0, help="Units kept back from Amazon to avoid overselling.")

    # --- accounting -------------------------------------------------------
    settlement_journal_id = fields.Many2one("account.journal", domain="[('type', 'in', ('bank', 'general'))]")
    account_clearing_id = fields.Many2one("account.account", string="Amazon Receivable / Clearing",
                                          help="Sales, tax and shipping collected by Amazon.")
    account_transfer_id = fields.Many2one("account.account", string="Payout in Transit",
                                          help="Receives the net deposit until the bank statement is reconciled.")
    account_referral_id = fields.Many2one("account.account", string="Referral Fees")
    account_fba_id = fields.Many2one("account.account", string="FBA Fulfilment Fees")
    account_storage_id = fields.Many2one("account.account", string="Storage Fees")
    account_ads_id = fields.Many2one("account.account", string="Advertising")
    account_promotion_id = fields.Many2one("account.account", string="Promotions")
    account_refund_id = fields.Many2one("account.account", string="Refunds")
    account_reimbursement_id = fields.Many2one("account.account", string="Reimbursements")
    account_other_id = fields.Many2one("account.account", string="Other Fees")
    auto_settlement_entry = fields.Boolean("Create settlement entries", default=True)
    auto_post_settlement = fields.Boolean("Post settlement entries", default=False)

    # --- stats --------------------------------------------------------------
    order_count = fields.Integer(compute="_compute_counts")
    listing_count = fields.Integer(compute="_compute_counts")
    unmapped_count = fields.Integer(compute="_compute_counts")
    error_order_count = fields.Integer(compute="_compute_counts")
    last_sync = fields.Datetime(compute="_compute_counts")

    @api.depends("marketplace_ids")
    def _compute_default_marketplace(self):
        for rec in self:
            rec.default_marketplace_id = rec.marketplace_ids[:1]

    def _compute_counts(self):
        Order = self.env["chi.amazon.order"]
        Listing = self.env["chi.amazon.listing"]
        Log = self.env["chi.amazon.sync.log"]
        for rec in self:
            rec.order_count = Order.search_count([("account_id", "=", rec.id)])
            rec.error_order_count = Order.search_count([("account_id", "=", rec.id), ("state", "=", "error")])
            rec.listing_count = Listing.search_count([("account_id", "=", rec.id)])
            rec.unmapped_count = Listing.search_count([("account_id", "=", rec.id), ("product_id", "=", False)])
            last = Log.search([("account_id", "=", rec.id), ("state", "=", "done")], limit=1, order="date_end desc")
            rec.last_sync = last.date_end

    @api.onchange("region")
    def _onchange_region(self):
        self.marketplace_ids = self.marketplace_ids.filtered(lambda m: m.region == self.region)

    # ------------------------------------------------------------------ API
    def _get_client(self):
        self.ensure_one()
        sudo = self.sudo()

        def _store(token, expiry):
            # separate cursor-free write: keep the token even if the sync fails later
            sudo.write({"access_token": token, "token_expiry": expiry})

        try:
            return SPAPIClient(sudo.lwa_client_id, sudo.lwa_client_secret, sudo.refresh_token,
                               region=self.region, environment=self.environment,
                               access_token=sudo.access_token, token_expiry=sudo.token_expiry,
                               on_token=_store)
        except SPAPIError as e:
            raise UserError(str(e)) from e

    def action_test_connection(self):
        self.ensure_one()
        try:
            participations = self._get_client().get_marketplace_participations()
        except SPAPIError as e:
            self.write({"state": "error", "last_error": str(e)})
            raise UserError(_("Amazon refused the connection:\n%s", e)) from e
        codes = [p.get("marketplace", {}).get("id") for p in participations if p.get("participation", {}).get("isParticipating")]
        found = self.env["chi.amazon.marketplace"].search([("code", "in", codes)])
        vals = {"state": "connected", "last_error": False}
        if found and not self.marketplace_ids:
            vals["marketplace_ids"] = [(6, 0, found.ids)]
        self.write(vals)
        return {
            "type": "ir.actions.client", "tag": "display_notification",
            "params": {"type": "success", "title": _("Connected to Amazon"),
                       "message": _("%s marketplace(s) available.", len(found)), "sticky": False},
        }

    def _run(self, operation, method, *args):
        """Run one sync step inside a log record; never let one account break the others."""
        self.ensure_one()
        log = self.env["chi.amazon.sync.log"].create({"account_id": self.id, "operation": operation})
        try:
            count = method(*args) or 0
            log.write({"state": "done", "record_count": count, "date_end": fields.Datetime.now()})
            if self.state != "connected":
                self.state = "connected"
        except (SPAPIError, UserError) as e:
            _logger.warning("Amazon %s failed for %s: %s", operation, self.name, e)
            log.write({"state": "error", "message": str(e), "date_end": fields.Datetime.now()})
            self.write({"state": "error", "last_error": str(e)})
        return log

    # --- orders -------------------------------------------------------------
    def _sync_orders(self):
        self.ensure_one()
        if not self.marketplace_ids:
            raise UserError(_("Select at least one marketplace on %s.", self.name))
        since = self.orders_synced_until or (fields.Datetime.now() - timedelta(days=self.order_history_days))
        since -= timedelta(minutes=10)  # overlap: Amazon updates are eventually consistent
        started = fields.Datetime.now()
        client = self._get_client()
        Order = self.env["chi.amazon.order"]
        count = 0
        for data in client.iter_orders(self.marketplace_ids.mapped("code"), since):
            items = client.get_order_items(data["AmazonOrderId"])
            Order._import_from_api(self, data, items)
            count += 1
        self.orders_synced_until = started
        Order.search([("account_id", "=", self.id), ("state", "=", "new")])._process()
        return count

    def action_sync_orders(self):
        for rec in self:
            rec._run("orders", rec._sync_orders)
        return self._notify(_("Order sync finished"))

    # --- listings -----------------------------------------------------------
    def _sync_listings(self):
        self.ensure_one()
        if not self.seller_id:
            raise UserError(_("Set the Seller ID on %s first.", self.name))
        client = self._get_client()
        count = 0
        for marketplace in self.marketplace_ids:
            for item in client.iter_listings(self.seller_id, marketplace.code):
                self.env["chi.amazon.listing"]._import_from_api(self, marketplace, item)
                count += 1
        return count

    def action_sync_listings(self):
        for rec in self:
            rec._run("listings", rec._sync_listings)
        return self._notify(_("Listing sync finished"))

    def _push_stock(self):
        self.ensure_one()
        listings = self.env["chi.amazon.listing"].search([
            ("account_id", "=", self.id), ("fulfillment", "=", "fbm"), ("product_id", "!=", False),
            ("status", "=", "active"), ("push_enabled", "=", True)])
        return listings._push_quantity()

    def action_push_stock(self):
        for rec in self:
            rec._run("stock", rec._push_stock)
        return self._notify(_("Stock pushed to Amazon"))

    # --- FBA ----------------------------------------------------------------
    def _sync_fba(self):
        self.ensure_one()
        client = self._get_client()
        count = 0
        for marketplace in self.marketplace_ids:
            for summary in client.iter_fba_inventory(marketplace.code):
                self.env["chi.amazon.fba.inventory"]._import_from_api(self, marketplace, summary)
                count += 1
        return count

    def action_sync_fba(self):
        for rec in self:
            rec._run("fba", rec._sync_fba)
        return self._notify(_("FBA inventory updated"))

    # --- settlements ----------------------------------------------------------
    def _sync_settlements(self):
        self.ensure_one()
        client = self._get_client()
        Settlement = self.env["chi.amazon.settlement"]
        last = Settlement.search([("account_id", "=", self.id)], order="start_date desc", limit=1)
        since = (last.start_date or fields.Datetime.now() - timedelta(days=90)) - timedelta(days=1)
        count = 0
        for group in client.iter_financial_event_groups(since):
            if group.get("ProcessingStatus") != "Closed":
                continue
            if Settlement.search_count([("account_id", "=", self.id), ("name", "=", group["FinancialEventGroupId"])]):
                continue
            events = client.get_financial_events(group["FinancialEventGroupId"])
            Settlement._import_from_api(self, group, events)
            count += 1
        return count

    def action_sync_settlements(self):
        for rec in self:
            rec._run("settlements", rec._sync_settlements)
        return self._notify(_("Settlements imported"))

    def action_sync_all(self):
        for rec in self:
            rec._run("orders", rec._sync_orders)
            rec._run("listings", rec._sync_listings)
            rec._run("fba", rec._sync_fba)
            rec._run("settlements", rec._sync_settlements)
            if rec.stock_push:
                rec._run("stock", rec._push_stock)
        return self._notify(_("Amazon is up to date"))

    def _notify(self, title):
        errors = self.filtered(lambda a: a.state == "error")
        return {
            "type": "ir.actions.client", "tag": "display_notification",
            "params": {
                "type": "danger" if errors else "success",
                "title": title,
                "message": errors and _("Check the sync log of: %s", ", ".join(errors.mapped("name"))) or _("Done."),
                "next": {"type": "ir.actions.act_window_close"} if errors else False,
            },
        }

    # --- crons ----------------------------------------------------------------
    @api.model
    def _cron_sync_orders(self):
        for acc in self.search([("state", "!=", "draft")]):
            acc._run("orders", acc._sync_orders)
            if acc.stock_push:
                acc._run("stock", acc._push_stock)
            self.env.cr.commit()  # keep finished accounts even if a later one crashes

    @api.model
    def _cron_sync_daily(self):
        for acc in self.search([("state", "!=", "draft")]):
            acc._run("listings", acc._sync_listings)
            acc._run("fba", acc._sync_fba)
            acc._run("settlements", acc._sync_settlements)
            self.env.cr.commit()
        # 30-day sales are time based: refresh them once a day
        self.env["chi.amazon.listing"].search([])._compute_sales()

    # --- smart buttons ----------------------------------------------------------
    def _open(self, model, name, domain=None, view_mode="list,form"):
        return {"type": "ir.actions.act_window", "res_model": model, "name": name,
                "view_mode": view_mode, "domain": [("account_id", "in", self.ids)] + (domain or []),
                "context": {"default_account_id": self[:1].id}}

    def action_open_orders(self):
        return self._open("chi.amazon.order", _("Amazon Orders"))

    def action_open_listings(self):
        return self._open("chi.amazon.listing", _("Listings"), view_mode="kanban,list,form")

    def action_open_unmapped(self):
        return self._open("chi.amazon.listing", _("Unmapped listings"), [("product_id", "=", False)])

    def action_open_logs(self):
        return self._open("chi.amazon.sync.log", _("Sync log"), view_mode="list")
