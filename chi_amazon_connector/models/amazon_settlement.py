# -*- coding: utf-8 -*-
from collections import defaultdict

from odoo import _, api, fields, models
from odoo.exceptions import UserError

CATEGORIES = [
    ("principal", "Sales"),
    ("tax", "Tax collected"),
    ("shipping", "Shipping"),
    ("promotion", "Promotions"),
    ("referral", "Referral fees"),
    ("fba", "FBA fees"),
    ("storage", "Storage fees"),
    ("advertising", "Advertising"),
    ("refund", "Refunds"),
    ("reimbursement", "Reimbursements"),
    ("other", "Other"),
]
# Category -> account field on chi.amazon.account
ACCOUNT_FIELD = {
    "principal": "account_clearing_id",
    "tax": "account_clearing_id",
    "shipping": "account_clearing_id",
    "promotion": "account_promotion_id",
    "referral": "account_referral_id",
    "fba": "account_fba_id",
    "storage": "account_storage_id",
    "advertising": "account_ads_id",
    "refund": "account_refund_id",
    "reimbursement": "account_reimbursement_id",
    "other": "account_other_id",
}


def classify(description, event_type=""):
    d = (description or "").lower()
    e = (event_type or "").lower()
    if "refund" in e:
        return "refund"
    if "ads" in e or "advert" in d or "productads" in e:
        return "advertising"
    if "reimburse" in d or "reversal_reimbursement" in d or "warehouse_lost" in d or "warehouse_damage" in d:
        return "reimbursement"
    if "storage" in d:
        return "storage"
    if "fba" in d or "fulfil" in d or "pick" in d:
        return "fba"
    if "commission" in d or "referral" in d or "closing" in d or "variableclosing" in d:
        return "referral"
    if "promotion" in d or "promo" in d or "discount" in d:
        return "promotion"
    if "tax" in d or "gst" in d or "hst" in d or "pst" in d or "vat" in d:
        return "tax"
    if "shipping" in d or "giftwrap" in d:
        return "shipping"
    if d in ("principal", "itemprice", "product"):
        return "principal"
    return "other"


class AmazonSettlement(models.Model):
    _name = "chi.amazon.settlement"
    _description = "Amazon Settlement"
    _inherit = ["mail.thread"]
    _order = "start_date desc"

    name = fields.Char("Settlement ID", required=True, index=True, copy=False)
    account_id = fields.Many2one("chi.amazon.account", required=True, ondelete="cascade")
    company_id = fields.Many2one(related="account_id.company_id", store=True)
    currency_id = fields.Many2one("res.currency", required=True)
    start_date = fields.Datetime()
    end_date = fields.Datetime()
    deposit_date = fields.Datetime()
    deposit_amount = fields.Monetary("Payout", help="Net amount Amazon transferred to the bank.")
    line_ids = fields.One2many("chi.amazon.settlement.line", "settlement_id")
    line_count = fields.Integer(compute="_compute_line_count")
    total_sales = fields.Monetary(compute="_compute_totals", store=True)
    total_fees = fields.Monetary(compute="_compute_totals", store=True)
    total_refunds = fields.Monetary(compute="_compute_totals", store=True)
    total_other = fields.Monetary(compute="_compute_totals", store=True)
    total_lines = fields.Monetary(compute="_compute_totals", store=True)
    difference = fields.Monetary(compute="_compute_totals", store=True,
                                 help="Payout minus the sum of the lines; must be zero to post.")
    move_id = fields.Many2one("account.move", readonly=True, copy=False)
    state = fields.Selection([("draft", "Imported"), ("entry", "Entry created"), ("posted", "Posted")],
                             default="draft", tracking=True)

    _sql_constraints = [("name_unique", "unique(account_id, name)", "Settlement already imported.")]

    @api.depends("line_ids.amount", "line_ids.category", "deposit_amount")
    def _compute_totals(self):
        fee_cats = {"referral", "fba", "storage", "advertising", "other", "promotion"}
        for rec in self:
            lines = rec.line_ids
            rec.total_sales = sum(lines.filtered(lambda l: l.category in ("principal", "tax", "shipping")).mapped("amount"))
            rec.total_fees = sum(lines.filtered(lambda l: l.category in fee_cats).mapped("amount"))
            rec.total_refunds = sum(lines.filtered(lambda l: l.category == "refund").mapped("amount"))
            rec.total_other = sum(lines.filtered(lambda l: l.category == "reimbursement").mapped("amount"))
            rec.total_lines = sum(lines.mapped("amount"))
            rec.difference = rec.deposit_amount - rec.total_lines

    def _compute_line_count(self):
        for rec in self:
            rec.line_count = len(rec.line_ids)

    # ------------------------------------------------------------ import
    @api.model
    def _import_from_api(self, account, group, events):
        total = group.get("OriginalTotal") or group.get("ConvertedTotal") or {}
        currency = self.env["res.currency"].with_context(active_test=False).search(
            [("name", "=", total.get("CurrencyCode") or account.currency_id.name)], limit=1)
        lines = []
        for event_type, entries in events.items():
            for event in entries:
                lines += self._lines_from_event(event_type, event)
        Order = self.env["chi.amazon.order"]
        settlement = self.create({
            "name": group["FinancialEventGroupId"],
            "account_id": account.id,
            "currency_id": (currency or account.currency_id).id,
            "start_date": Order._parse_dt(group.get("FinancialEventGroupStart")),
            "end_date": Order._parse_dt(group.get("FinancialEventGroupEnd")),
            "deposit_date": Order._parse_dt(group.get("FundTransferDate")),
            "deposit_amount": float(total.get("CurrencyAmount") or 0.0),
            "line_ids": [(0, 0, l) for l in lines],
        })
        settlement._apply_fees_to_orders()
        if account.auto_settlement_entry and not settlement.difference:
            settlement.action_create_entry()
            if account.auto_post_settlement:
                settlement.action_post()
        return settlement

    @api.model
    def _lines_from_event(self, event_type, event):
        Order = self.env["chi.amazon.order"]
        posted = Order._parse_dt(event.get("PostedDate"))
        order_ref = event.get("AmazonOrderId")
        out = []

        def add(desc, amount_node, sku=None, kind=None):
            amount = float((amount_node or {}).get("CurrencyAmount") or 0.0)
            if amount:
                out.append({"posted_date": posted, "transaction_type": event_type.replace("EventList", ""),
                            "order_ref": order_ref, "sku": sku, "description": desc,
                            "amount": amount, "category": kind or classify(desc, event_type)})

        items = event.get("ShipmentItemList") or event.get("ShipmentItemAdjustmentList") or []
        for item in items:
            sku = item.get("SellerSKU")
            for c in item.get("ItemChargeList") or item.get("ItemChargeAdjustmentList") or []:
                add(c.get("ChargeType"), c.get("ChargeAmount"), sku)
            for f in item.get("ItemFeeList") or item.get("ItemFeeAdjustmentList") or []:
                add(f.get("FeeType"), f.get("FeeAmount"), sku)
            for p in item.get("PromotionList") or item.get("PromotionAdjustmentList") or []:
                add("Promotion " + (p.get("PromotionType") or ""), p.get("PromotionAmount"), sku, "promotion")
            for t in item.get("ItemTaxWithheldList") or []:
                for c in t.get("TaxesWithheld") or []:
                    add("Tax withheld " + (c.get("ChargeType") or ""), c.get("ChargeAmount"), sku, "tax")
        for f in event.get("FeeList") or []:
            add(f.get("FeeType"), f.get("FeeAmount"))
        if "ProductAds" in event_type:
            add("Advertising " + (event.get("invoiceId") or ""), event.get("transactionValue"), kind="advertising")
        if "Adjustment" in event_type and event.get("AdjustmentAmount"):
            add(event.get("AdjustmentType") or "Adjustment", event.get("AdjustmentAmount"))
        return out

    def _apply_fees_to_orders(self):
        fee_cats = ("referral", "fba", "promotion")
        for rec in self:
            fees = defaultdict(float)
            for line in rec.line_ids.filtered(lambda l: l.order_ref and l.category in fee_cats):
                fees[line.order_ref] += line.amount
            if not fees:
                continue
            for order in self.env["chi.amazon.order"].search([("account_id", "=", rec.account_id.id),
                                                               ("name", "in", list(fees))]):
                order.amount_fees = fees[order.name]

    # ------------------------------------------------------------ accounting
    def _check_accounts(self):
        acc = self.account_id
        if not acc.settlement_journal_id or not acc.account_transfer_id:
            raise UserError(_("Set the settlement journal and the 'Payout in Transit' account on %s.", acc.name))
        missing = {cat for cat in set(self.line_ids.mapped("category")) if not acc[ACCOUNT_FIELD[cat]]}
        if missing:
            labels = dict(CATEGORIES)
            raise UserError(_("Map an account for: %s (Amazon account > Accounting tab).",
                              ", ".join(labels[m] for m in missing)))

    def _prepare_move(self):
        self.ensure_one()
        acc = self.account_id
        grouped = defaultdict(float)
        for line in self.line_ids:
            grouped[line.category] += line.amount
        labels = dict(CATEGORIES)
        company_currency = acc.company_id.currency_id
        date = (self.deposit_date or self.end_date or fields.Datetime.now()).date()

        def line_vals(account, amount, name):
            amount_cc = self.currency_id._convert(amount, company_currency, acc.company_id, date)
            vals = {"account_id": account.id, "name": name,
                    "debit": amount_cc > 0 and amount_cc or 0.0,
                    "credit": amount_cc < 0 and -amount_cc or 0.0}
            if self.currency_id != company_currency:
                vals.update(currency_id=self.currency_id.id, amount_currency=amount)
            return vals

        move_lines = []
        for cat, amount in grouped.items():
            if self.currency_id.is_zero(amount):
                continue
            # money Amazon owes us (positive) is a credit on the income/clearing side, fees are debits
            move_lines.append((0, 0, line_vals(acc[ACCOUNT_FIELD[cat]], -amount, _("Amazon %s", labels[cat]))))
        move_lines.append((0, 0, line_vals(acc.account_transfer_id, self.deposit_amount, _("Amazon payout %s", self.name))))
        # absorb rounding from currency conversion on the payout line
        debit = sum(l[2]["debit"] for l in move_lines)
        credit = sum(l[2]["credit"] for l in move_lines)
        diff = company_currency.round(debit - credit)
        if diff:
            last = move_lines[-1][2]
            if last["debit"]:
                last["debit"] -= diff
            else:
                last["credit"] += diff
        return {
            "move_type": "entry",
            "journal_id": acc.settlement_journal_id.id,
            "date": date,
            "ref": _("Amazon settlement %s", self.name),
            "company_id": acc.company_id.id,
            "line_ids": move_lines,
        }

    def action_create_entry(self):
        for rec in self:
            if rec.move_id:
                continue
            if not rec.currency_id.is_zero(rec.difference):
                raise UserError(_("Settlement %(s)s does not balance: payout differs from lines by %(d)s.",
                                  s=rec.name, d=rec.difference))
            rec._check_accounts()
            rec.move_id = self.env["account.move"].create(rec._prepare_move())
            rec.state = "entry"

    def action_post(self):
        for rec in self:
            if not rec.move_id:
                rec.action_create_entry()
            if rec.move_id.state == "draft":
                rec.move_id.action_post()
            rec.state = "posted"

    def action_open_move(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": "account.move", "res_id": self.move_id.id,
                "view_mode": "form"}


class AmazonSettlementLine(models.Model):
    _name = "chi.amazon.settlement.line"
    _description = "Amazon Settlement Line"
    _order = "posted_date, id"

    settlement_id = fields.Many2one("chi.amazon.settlement", required=True, ondelete="cascade", index=True)
    currency_id = fields.Many2one(related="settlement_id.currency_id")
    posted_date = fields.Datetime()
    transaction_type = fields.Char()
    order_ref = fields.Char("Amazon order", index=True)
    sku = fields.Char("SKU")
    description = fields.Char()
    category = fields.Selection(CATEGORIES, required=True, default="other")
    amount = fields.Monetary()
