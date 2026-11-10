# -*- coding: utf-8 -*-
{
    "name": "Amazon Seller Hub — SP-API Connector",
    "version": "18.0.1.0.0",
    "category": "Sales/Sales",
    "summary": "Connect Amazon Seller Central (SP-API) to Odoo: orders, listings, "
               "stock sync, FBA inventory, settlements to accounting, returns, "
               "and a live profit dashboard.",
    "description": """
Amazon Seller Hub
=================
Run your whole Amazon business from Odoo:

* Multi-account, multi-marketplace (NA / EU / FE, incl. Egypt, Saudi Arabia, UAE)
* Orders (FBA + FBM) -> Sale Orders -> Invoices, fully automatic
* Listings mapped to Odoo products, with stock health and quantity push
* FBA inventory snapshot and difference vs. Odoo
* Settlements (financial event groups) -> balanced journal entries
* Customer returns tracking and claim follow-up
* Live dashboard: sales, fees, margin, best sellers, problems to fix
""",
    "author": "CH Hamza International",
    "website": "https://www.ch-international.com",
    "support": "ahmed.naser@ch-international.com",
    "license": "OPL-1",
    "price": 249.0,
    "currency": "USD",
    "depends": ["sale_management", "stock", "account"],
    "external_dependencies": {"python": ["requests"]},
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/marketplace_data.xml",
        "data/ir_sequence_data.xml",
        "data/ir_cron_data.xml",
        "views/dashboard_action.xml",
        "views/amazon_account_views.xml",
        "views/amazon_marketplace_views.xml",
        "views/amazon_listing_views.xml",
        "views/amazon_order_views.xml",
        "views/amazon_fba_inventory_views.xml",
        "views/amazon_settlement_views.xml",
        "views/amazon_return_views.xml",
        "views/amazon_sync_log_views.xml",
        "views/product_views.xml",
        "views/sale_order_views.xml",
        "wizard/amazon_sync_wizard_views.xml",
        "views/menus.xml",
    ],
    "demo": [
        "data/demo_data.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "chi_amazon_connector/static/src/scss/amazon_hub.scss",
            "chi_amazon_connector/static/src/dashboard/*.js",
            "chi_amazon_connector/static/src/dashboard/*.xml",
            "chi_amazon_connector/static/src/dashboard/*.scss",
        ],
    },
    "images": [
        "static/description/banner.gif",
        "static/description/screenshot_dash.jpg",
        "static/description/screenshot_dash_bottom.jpg",
        "static/description/screenshot_listings.jpg",
        "static/description/screenshot_settlement.jpg",
    ],
    "application": True,
    "installable": True,
}
