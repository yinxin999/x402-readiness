"""Tests for the readiness grader.

The fixtures here are trimmed from real endpoints I audited, so a regression in
the scoring rules shows up as a failing test rather than as a wrong verdict for
a real seller.
"""

import base64
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from x402_readiness.core import decode_payment_required, grade  # noqa: E402

BASE_USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
PAY_TO = "0x66c5c45d3bC09F2244C02E1c3e66F49116eFb633"


def v2_accept(network="eip155:8453", asset=BASE_USDC, amount="5000", payto=PAY_TO):
    return {"scheme": "exact", "network": network, "amount": amount,
            "asset": asset, "payTo": payto, "maxTimeoutSeconds": 120,
            "extra": {"name": "USD Coin", "version": "2"}}


def probe_with_header(accepts, extra_headers=None, status=402):
    doc = {"x402Version": 2,
           "resource": {"url": "https://example.invalid/paid", "description": "d", "mimeType": "application/json"},
           "accepts": accepts}
    headers = {"payment-required": base64.b64encode(json.dumps(doc).encode()).decode(),
               "access-control-expose-headers": "payment-required, payment-response",
               "access-control-allow-origin": "*"}
    headers.update(extra_headers or {})
    return {"ok": True, "status": status, "headers": {k.lower(): v for k, v in headers.items()}, "body": "", "error": None}


class TestDecode(unittest.TestCase):
    def test_prefers_v2_header_over_body(self):
        p = probe_with_header([v2_accept()])
        p["body"] = json.dumps({"x402Version": 1, "accepts": [{"maxAmountRequired": "1"}]})
        info = decode_payment_required(p)
        self.assertIn("v2", info["source"])
        self.assertEqual(info["accepts"][0]["amount"], "5000")

    def test_falls_back_to_v1_body(self):
        p = {"ok": True, "status": 402, "headers": {},
             "body": json.dumps({"x402Version": 1, "accepts": [{"maxAmountRequired": "2000"}]})}
        info = decode_payment_required(p)
        self.assertIn("v1", info["source"])


class TestGrade(unittest.TestCase):
    def test_clean_endpoint_is_ready(self):
        g = grade(probe_with_header([v2_accept()]))
        self.assertEqual(g["score"], 100, g["findings"])
        self.assertEqual(g["verdict"], "ready")

    def test_regression_usdc_address_must_be_recognised(self):
        """A wrong constant used to flag real USDC as an unknown asset."""
        g = grade(probe_with_header([v2_accept()]))
        self.assertNotIn("unknown_asset", [f["code"] for f in g["findings"]])

    def test_regression_duplicate_accepts_not_double_charged(self):
        """The same defect on 5 accepts[] entries must cost the same as on 1."""
        bad = v2_accept(asset="0xdeadbeef")
        one = grade(probe_with_header([bad]))
        five = grade(probe_with_header([bad] * 5))
        self.assertEqual(one["score"], five["score"], (one["score"], five["score"]))

    def test_polygon_usdc_is_known(self):
        g = grade(probe_with_header([v2_accept(network="eip155:137",
                                              asset="0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359")]))
        self.assertNotIn("unknown_asset", [f["code"] for f in g["findings"]])

    def test_non_402_scores_low(self):
        p = probe_with_header([], status=200)
        p["headers"] = {}
        g = grade(p)
        self.assertLessEqual(g["score"], 40)
        self.assertIn("not_402", [f["code"] for f in g["findings"]])

    def test_missing_expose_header_flagged(self):
        p = probe_with_header([v2_accept()], {"access-control-expose-headers": ""})
        g = grade(p)
        self.assertIn("not_exposed", [f["code"] for f in g["findings"]])

    def test_bad_payto_flagged(self):
        g = grade(probe_with_header([v2_accept(payto="not-an-address")]))
        self.assertIn("bad_payto", [f["code"] for f in g["findings"]])

    def test_legacy_v1_network_alias_flagged(self):
        g = grade(probe_with_header([v2_accept(network="base")]))
        self.assertIn("legacy_network", [f["code"] for f in g["findings"]])

    def test_unreachable(self):
        g = grade({"ok": False, "status": None, "headers": {}, "body": "", "error": "TimeoutError"})
        self.assertEqual(g["score"], 0)
        self.assertEqual(g["verdict"], "unreachable")

    def test_fix_order_is_worst_first(self):
        p = probe_with_header([v2_accept(payto="bad")], {"access-control-expose-headers": ""})
        g = grade(p)
        self.assertIn("payTo", g["fix_order"][0])

    def test_v1_only_body_costs_some_points(self):
        p = {"ok": True, "status": 402,
             "headers": {"access-control-expose-headers": "payment-required",
                         "access-control-allow-origin": "*"},
             "body": json.dumps({"x402Version": 1, "accepts": [
                 {"scheme": "exact", "network": "eip155:8453", "maxAmountRequired": "5000",
                  "asset": BASE_USDC, "payTo": PAY_TO}]})}
        g = grade(p)
        self.assertIn("v1_only", [f["code"] for f in g["findings"]])
        self.assertEqual(g["verdict"], "workable")


if __name__ == "__main__":
    unittest.main(verbosity=2)
