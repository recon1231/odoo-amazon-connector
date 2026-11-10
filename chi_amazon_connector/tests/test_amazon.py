# -*- coding: utf-8 -*-
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

from ..models.amazon_settlement import classify
from ..models.sp_api import SPAPIClient

ORDER = {
    "AmazonOrderId": "702-0000001-0000001", "MarketplaceId": "A2EUQ1WTGCTBG2", "OrderStatus": "Shipped",
    "FulfillmentChannel": "AFN", "PurchaseDate": "2026-11-01T10:00:00Z", "LastUpdateDate": "2026-11-01T12:00:00Z",
    "OrderTotal": {"CurrencyCode": "CAD", "Amount": "113.00"}, "IsPrime": True,
    "ShippingAddress": {"City": "Toronto", "StateOrRegion": "ON", "CountryCode": "CA"},
}
ITEMS = [{"OrderItemId": "1", "SellerSKU": "TEST-SKU", "ASIN": "B0TEST", "Title": "Test part",
          "QuantityOrdered": 1, "ItemPrice": {"Amount": "100.00"}, "ItemTax": {"Amount": "13.00"}}]
EVENTS = {"ShipmentEventList": [{
    "AmazonOrderId": "702-0000001-0000001", "PostedDate": "2026-11-02T00:00:00Z",
    "ShipmentItemList": [{
        "SellerSKU": "TEST-SKU",
        "ItemChargeList": [{"ChargeType": "Principal", "ChargeAmount": {"CurrencyAmount": 100.0}},
                           {"ChargeType": "Tax", "ChargeAmount": {"CurrencyAmount": 13.0}}],
        "ItemFeeList": [{"FeeType": "Commission", "FeeAmount": {"CurrencyAmount": -15.0}},
                        {"FeeType": "FBAPerUnitFulfillmentFee", "FeeAmount": {"CurrencyAmount": -6.4}}],
    }],
}]}
GROUP = {"FinancialEventGroupId": "G-1", "ProcessingStatus": "Closed",
         "FinancialEventGroupStart": "2026-11-01T00:00:00Z", "FinancialEventGroupEnd": "2026-11-14T00:00:00Z",
         "FundTransferDate": "2026-11-15T00:00:00Z",
         "OriginalTotal": {"CurrencyCode": "CAD", "CurrencyAmount": 91.6}}


@tagged("post_install", "-at_install")
class TestAmazonHub(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        company = env.company
        cad = env.ref("base.CAD")
        cad.active = True
        cls.product = env["product.product"].create({"name": "Test part", "default_code": "TEST-SKU",
                                                     "type": "consu", "standard_price": 40, "list_price": 100})

        def acc(code, t):
            return env["account.account"].create({"code": code, "name": code, "account_type": t,
                                                  "reconcile": t == "asset_current", "company_ids": [(6, 0, company.ids)]})
        cls.account = env["chi.amazon.account"].create({
            "name": "Test", "seller_id": "S1", "lwa_client_id": "id", "lwa_client_secret": "s", "refresh_token": "r",
            "state": "connected",
            "marketplace_ids": [(6, 0, env.ref("chi_amazon_connector.marketplace_ca").ids)],
            "customer_id": env["res.partner"].create({"name": "Amazon Customer"}).id,
            "auto_validate_fba": False, "auto_invoice": False,
            "settlement_journal_id": env["account.journal"].search([("type", "=", "general"), ("company_id", "=", company.id)], limit=1).id,
            "account_clearing_id": acc("TAMZ1", "asset_current").id,
            "account_transfer_id": acc("TAMZ2", "asset_current").id,
            "account_referral_id": acc("TAMZ3", "expense").id,
            "account_fba_id": acc("TAMZ4", "expense").id,
        })

    def test_classify(self):
        self.assertEqual(classify("Commission"), "referral")
        self.assertEqual(classify("FBAPerUnitFulfillmentFee"), "fba")
        self.assertEqual(classify("Principal"), "principal")
        self.assertEqual(classify("FBA storage fee"), "storage")
        self.assertEqual(classify("anything", "RefundEventList"), "refund")

    def test_order_import_creates_sale_order(self):
        Order = self.env["chi.amazon.order"]
        order = Order._import_from_api(self.account, ORDER, ITEMS)
        self.assertEqual(order.fulfillment, "fba")
        self.assertEqual(order.amount_total, 113.0)
        self.assertEqual(order.line_ids.product_id, self.product)
        order._process()
        self.assertEqual(order.state, "done", order.error_message)
        so = order.sale_order_id
        self.assertEqual(so.state, "sale")
        self.assertEqual(so.client_order_ref, ORDER["AmazonOrderId"])
        self.assertAlmostEqual(so.order_line.price_unit, 100.0)
        # re-import is idempotent
        again = Order._import_from_api(self.account, ORDER, ITEMS)
        self.assertEqual(again, order)

    def test_unmapped_sku_goes_to_attention(self):
        items = [dict(ITEMS[0], SellerSKU="UNKNOWN")]
        order = self.env["chi.amazon.order"]._import_from_api(self.account, dict(ORDER, AmazonOrderId="X-2"), items)
        order._process()
        self.assertEqual(order.state, "error")
        self.assertIn("UNKNOWN", order.error_message)

    def test_settlement_entry_is_balanced(self):
        self.env["chi.amazon.order"]._import_from_api(self.account, ORDER, ITEMS)
        st = self.env["chi.amazon.settlement"]._import_from_api(self.account, GROUP, EVENTS)
        self.assertAlmostEqual(st.total_lines, 91.6)
        self.assertFalse(st.difference)
        self.assertTrue(st.move_id, "entry created automatically when balanced")
        self.assertEqual(sum(st.move_id.line_ids.mapped("debit")), sum(st.move_id.line_ids.mapped("credit")))
        transfer = st.move_id.line_ids.filtered(lambda l: l.account_id == self.account.account_transfer_id)
        self.assertAlmostEqual(transfer.amount_currency, 91.6)
        order = self.env["chi.amazon.order"].search([("name", "=", ORDER["AmazonOrderId"])])
        self.assertAlmostEqual(order.amount_fees, -21.4)

    def test_sync_orders_uses_api(self):
        with patch.object(SPAPIClient, "iter_orders", return_value=iter([ORDER])), \
             patch.object(SPAPIClient, "get_order_items", return_value=ITEMS):
            self.account.action_sync_orders()
        log = self.env["chi.amazon.sync.log"].search([("account_id", "=", self.account.id)], limit=1)
        self.assertEqual(log.state, "done", log.message)
        self.assertEqual(log.record_count, 1)

    def test_dashboard_data(self):
        self.env["chi.amazon.order"]._import_from_api(self.account, ORDER, ITEMS)
        data = self.env["chi.amazon.dashboard"].get_data(account_id=self.account.id, days=90)
        self.assertEqual({k["key"] for k in data["kpis"]}, {"sales", "orders", "aov", "fees", "margin", "refunds"})
        self.assertEqual(len(data["series"]["labels"]), 90)
