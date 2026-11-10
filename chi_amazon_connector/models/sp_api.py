# -*- coding: utf-8 -*-
"""Thin, dependency-free client for the Amazon Selling Partner API.

SP-API no longer requires AWS SigV4 signing (Oct 2023): a Login-with-Amazon
access token in the ``x-amz-access-token`` header is enough. The client
handles token refresh, throttling (HTTP 429) with exponential back-off,
pagination and the report document download/decompression flow.
"""
import gzip
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests

_logger = logging.getLogger(__name__)

LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"

ENDPOINTS = {
    ("na", "production"): "https://sellingpartnerapi-na.amazon.com",
    ("eu", "production"): "https://sellingpartnerapi-eu.amazon.com",
    ("fe", "production"): "https://sellingpartnerapi-fe.amazon.com",
    ("na", "sandbox"): "https://sandbox.sellingpartnerapi-na.amazon.com",
    ("eu", "sandbox"): "https://sandbox.sellingpartnerapi-eu.amazon.com",
    ("fe", "sandbox"): "https://sandbox.sellingpartnerapi-fe.amazon.com",
}

USER_AGENT = "CHi-Amazon-Seller-Hub/19.0 (Language=Python; Platform=Odoo)"


class SPAPIError(Exception):
    def __init__(self, message, status=None, payload=None):
        super().__init__(message)
        self.status = status
        self.payload = payload


class SPAPIClient:
    max_retries = 5
    timeout = 60

    def __init__(self, client_id, client_secret, refresh_token, region="na",
                 environment="production", access_token=None, token_expiry=None,
                 on_token=None, session=None):
        if not (client_id and client_secret and refresh_token):
            raise SPAPIError("Missing LWA credentials (client id, secret or refresh token).")
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.base_url = ENDPOINTS[(region, environment)]
        self.access_token = access_token
        self.token_expiry = token_expiry
        self.on_token = on_token  # callback(token, expiry) to persist in DB
        self.session = session or requests.Session()

    # ------------------------------------------------------------------ auth
    def _token_valid(self):
        if not self.access_token or not self.token_expiry:
            return False
        expiry = self.token_expiry
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        return expiry - timedelta(seconds=120) > datetime.now(timezone.utc)

    def get_access_token(self, force=False):
        if not force and self._token_valid():
            return self.access_token
        resp = self.session.post(LWA_TOKEN_URL, data={
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }, timeout=self.timeout)
        if resp.status_code != 200:
            raise SPAPIError("Login with Amazon refused the credentials: %s" % resp.text[:300],
                             status=resp.status_code)
        data = resp.json()
        self.access_token = data["access_token"]
        self.token_expiry = datetime.now(timezone.utc) + timedelta(seconds=int(data.get("expires_in", 3600)))
        if self.on_token:
            self.on_token(self.access_token, self.token_expiry.replace(tzinfo=None))
        return self.access_token

    # ------------------------------------------------------------- transport
    def request(self, method, path, params=None, body=None):
        url = self.base_url + path
        attempt = 0
        while True:
            headers = {
                "x-amz-access-token": self.get_access_token(),
                "user-agent": USER_AGENT,
                "content-type": "application/json",
                "accept": "application/json",
            }
            resp = self.session.request(method, url, params=params,
                                        data=json.dumps(body) if body is not None else None,
                                        headers=headers, timeout=self.timeout)
            if resp.status_code == 403 and attempt == 0 and "Unauthorized" in resp.text:
                self.get_access_token(force=True)
                attempt += 1
                continue
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                rate = resp.headers.get("x-amzn-RateLimit-Limit")
                wait = (1.0 / float(rate)) if rate else 0
                wait = max(wait, 2 ** attempt)
                _logger.info("SP-API %s %s throttled (%s), retry in %.1fs", method, path, resp.status_code, wait)
                time.sleep(min(wait, 60))
                attempt += 1
                continue
            if resp.status_code >= 400:
                try:
                    payload = resp.json()
                    errors = payload.get("errors") or []
                    msg = "; ".join("%s: %s" % (e.get("code"), e.get("message")) for e in errors) or resp.text
                except ValueError:
                    payload, msg = None, resp.text
                raise SPAPIError("SP-API %s %s failed (%s): %s" % (method, path, resp.status_code, msg[:500]),
                                 status=resp.status_code, payload=payload)
            if not resp.content:
                return {}
            return resp.json()

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def iso(dt):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # ----------------------------------------------------------------- orders
    def iter_orders(self, marketplace_ids, last_updated_after, max_pages=50):
        params = {
            "MarketplaceIds": ",".join(marketplace_ids),
            "LastUpdatedAfter": self.iso(last_updated_after),
            "MaxResultsPerPage": 100,
        }
        for _page in range(max_pages):
            payload = self.request("GET", "/orders/v0/orders", params=params).get("payload", {})
            yield from payload.get("Orders", [])
            token = payload.get("NextToken")
            if not token:
                return
            params = {"MarketplaceIds": ",".join(marketplace_ids), "NextToken": token}

    def get_order_items(self, amazon_order_id):
        items, token = [], None
        while True:
            params = {"NextToken": token} if token else None
            payload = self.request("GET", "/orders/v0/orders/%s/orderItems" % quote(amazon_order_id),
                                   params=params).get("payload", {})
            items += payload.get("OrderItems", [])
            token = payload.get("NextToken")
            if not token:
                return items

    # --------------------------------------------------------------- listings
    def iter_listings(self, seller_id, marketplace_id, page_size=20, max_pages=500):
        params = {
            "marketplaceIds": marketplace_id,
            "includedData": "summaries,offers,fulfillmentAvailability",
            "pageSize": page_size,
        }
        for _page in range(max_pages):
            data = self.request("GET", "/listings/2021-08-01/items/%s" % quote(seller_id), params=params)
            yield from data.get("items", [])
            token = (data.get("pagination") or {}).get("nextToken")
            if not token:
                return
            params = dict(params, pageToken=token)

    def patch_listing_quantity(self, seller_id, sku, marketplace_id, product_type, quantity):
        body = {
            "productType": product_type or "PRODUCT",
            "patches": [{
                "op": "replace",
                "path": "/attributes/fulfillment_availability",
                "value": [{"fulfillment_channel_code": "DEFAULT", "quantity": int(max(quantity, 0))}],
            }],
        }
        return self.request("PATCH", "/listings/2021-08-01/items/%s/%s" % (quote(seller_id), quote(sku, safe="")),
                            params={"marketplaceIds": marketplace_id}, body=body)

    def patch_listing_price(self, seller_id, sku, marketplace_id, product_type, price, currency):
        body = {
            "productType": product_type or "PRODUCT",
            "patches": [{
                "op": "replace",
                "path": "/attributes/purchasable_offer",
                "value": [{
                    "marketplace_id": marketplace_id,
                    "currency": currency,
                    "our_price": [{"schedule": [{"value_with_tax": round(price, 2)}]}],
                }],
            }],
        }
        return self.request("PATCH", "/listings/2021-08-01/items/%s/%s" % (quote(seller_id), quote(sku, safe="")),
                            params={"marketplaceIds": marketplace_id}, body=body)

    # ------------------------------------------------------------------- FBA
    def iter_fba_inventory(self, marketplace_id, max_pages=200):
        params = {
            "details": "true",
            "granularityType": "Marketplace",
            "granularityId": marketplace_id,
            "marketplaceIds": marketplace_id,
        }
        for _page in range(max_pages):
            data = self.request("GET", "/fba/inventory/v1/summaries", params=params)
            yield from (data.get("payload") or {}).get("inventorySummaries", [])
            token = (data.get("pagination") or {}).get("nextToken")
            if not token:
                return
            params = dict(params, nextToken=token)

    # -------------------------------------------------------------- finances
    def iter_financial_event_groups(self, started_after, max_pages=50):
        params = {"FinancialEventGroupStartedAfter": self.iso(started_after), "MaxResultsPerPage": 100}
        for _page in range(max_pages):
            payload = self.request("GET", "/finances/v0/financialEventGroups", params=params).get("payload", {})
            yield from payload.get("FinancialEventGroupList", [])
            token = payload.get("NextToken")
            if not token:
                return
            params = {"NextToken": token}

    def get_financial_events(self, group_id, max_pages=100):
        merged = {}
        params = {"MaxResultsPerPage": 100}
        for _page in range(max_pages):
            payload = self.request("GET", "/finances/v0/financialEventGroups/%s/financialEvents" % quote(group_id),
                                   params=params).get("payload", {})
            for key, value in (payload.get("FinancialEvents") or {}).items():
                merged.setdefault(key, []).extend(value or [])
            token = payload.get("NextToken")
            if not token:
                return merged
            params = {"NextToken": token}
        return merged

    # --------------------------------------------------------------- reports
    def create_report(self, report_type, marketplace_ids, start=None, end=None):
        body = {"reportType": report_type, "marketplaceIds": marketplace_ids}
        if start:
            body["dataStartTime"] = self.iso(start)
        if end:
            body["dataEndTime"] = self.iso(end)
        return self.request("POST", "/reports/2021-06-30/reports", body=body)["reportId"]

    def get_report(self, report_id):
        return self.request("GET", "/reports/2021-06-30/reports/%s" % quote(report_id))

    def download_report_document(self, document_id):
        doc = self.request("GET", "/reports/2021-06-30/documents/%s" % quote(document_id))
        raw = self.session.get(doc["url"], timeout=self.timeout).content
        if doc.get("compressionAlgorithm") == "GZIP":
            raw = gzip.decompress(raw)
        return raw.decode("utf-8", errors="replace")

    # ------------------------------------------------------------------ misc
    def get_marketplace_participations(self):
        return self.request("GET", "/sellers/v1/marketplaceParticipations").get("payload", [])
