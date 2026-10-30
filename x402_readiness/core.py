"""
Probe and grade an x402 endpoint for buyer-side payment readiness.

The grading rules here are the ones I derived by hand-auditing listings on
402index; each rule maps to a concrete, observed payment failure.
"""

from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_TIMEOUT = 15
USER_AGENT = "x402-readiness/0.1 (+https://github.com/yinxin999/x402-readiness)"

# Canonical mainnet assets. Anything else is flagged, not rejected: the seller
# may be on a testnet deliberately.
KNOWN_ASSETS = {
    "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": ("USDC", "base"),
    "0xaf88d065e77c8cc2239327c5edb3a432268e5831": ("USDC", "arbitrum"),
    "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359": ("USDC", "polygon"),
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": ("USDC", "ethereum"),
    "0x0b2c639c533813f4aa9d7837caf62653d097ff85": ("USDC", "optimism"),
    "epjfwdd5aufqssqem2qn1xzybapc8g4weggkzwytdt1v": ("USDC", "solana"),
}

EVM_ADDR = re.compile(r"^0x[0-9a-fA-F]{40}$")
BASE58 = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

# CAIP-2 network ids and the legacy v1 aliases they replace.
NETWORK_ALIASES = {
    "base": "eip155:8453",
    "base-sepolia": "eip155:84532",
    "ethereum": "eip155:1",
    "arbitrum": "eip155:42161",
    "polygon": "eip155:137",
}


def _quote_url(url: str) -> str:
    """Percent-encode unsafe characters while preserving URL structure."""
    return urllib.parse.quote(url, safe=":/?#[]@!$&'()*+,;=%~")


def probe(url: str, method: str = "GET", timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Fetch the endpoint the way a paying client would, without paying.

    Returns a dict with status, headers, body snippet and the raw
    PAYMENT-REQUIRED header if present. Never raises on HTTP errors: a 402 is
    the expected, healthy outcome and is reported as such.
    """
    req = urllib.request.Request(
        _quote_url(url),
        method=method.upper(),
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Origin": "https://example.invalid",
        },
    )
    out = {"url": url, "method": method.upper(), "ok": False, "error": None}
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(65536)
            out.update(
                status=resp.status,
                headers={k.lower(): v for k, v in resp.headers.items()},
                body=body.decode("utf-8", "replace"),
                ok=True,
            )
    except urllib.error.HTTPError as e:
        body = e.read(65536) if hasattr(e, "read") else b""
        out.update(
            status=e.code,
            headers={k.lower(): v for k, v in (e.headers or {}).items()},
            body=body.decode("utf-8", "replace"),
            ok=True,
        )
    except Exception as e:  # DNS, TLS, timeout: the endpoint is unreachable
        out.update(status=None, headers={}, body="", error="%s: %s" % (type(e).__name__, e))
    return out


def decode_payment_required(probe_result: dict) -> dict:
    """Extract the payment requirements, preferring the v2 PAYMENT-REQUIRED header.

    A v2-capable buyer reads the header first and falls back to the JSON body.
    Sellers that only emit the v1 body still work with v2 clients, but lose the
    richer resource metadata, so we record which one was found.
    """
    info = {"source": None, "raw_v2": None, "raw_v1": None, "accepts": [], "resource": None}
    header = probe_result.get("headers", {}).get("payment-required")
    if header:
        try:
            decoded = json.loads(base64.b64decode(header + "==").decode("utf-8"))
            info["source"] = "PAYMENT-REQUIRED header (v2)"
            info["raw_v2"] = decoded
            info["accepts"] = decoded.get("accepts") or []
            info["resource"] = decoded.get("resource")
            return info
        except Exception:
            info["v2_decode_error"] = True

    try:
        body = json.loads(probe_result.get("body") or "{}")
    except Exception:
        return info
    if isinstance(body, dict) and body.get("accepts"):
        info["source"] = "JSON body (v1)"
        info["raw_v1"] = body
        info["accepts"] = body.get("accepts") or []
    return info


def _amount(accept: dict) -> int | None:
    """v2 uses `amount`; v1 uses `maxAmountRequired`. Accept either."""
    for key in ("amount", "maxAmountRequired"):
        if accept.get(key) is not None:
            try:
                return int(accept[key])
            except (TypeError, ValueError):
                return None
    return None


def _valid_payto(addr: str) -> bool:
    if not isinstance(addr, str) or not addr:
        return False
    return bool(EVM_ADDR.match(addr) or BASE58.match(addr))


def grade(probe_result: dict) -> dict:
    """Turn a probe result into a 0-100 readiness score plus actionable findings.

    Start at 100 and subtract for each defect. The weights reflect how likely
    each defect is to break a real payment, not how easy it is to fix.
    """
    findings = []
    score = 100

    def add(delta: int, code: str, detail: str):
        nonlocal score
        score -= delta
        findings.append({"code": code, "impact": -delta, "detail": detail})

    seen_codes: set[str] = set()

    def add_once(delta: int, code: str, detail: str):
        """Charge a defect once per audit, not once per accepts[] entry."""
        nonlocal score
        if code in seen_codes:
            return
        seen_codes.add(code)
        score -= delta
        findings.append({"code": code, "impact": -delta, "detail": detail})

    if not probe_result.get("ok"):
        return {
            "score": 0,
            "verdict": "unreachable",
            "findings": [{"code": "unreachable", "impact": -100,
                          "detail": probe_result.get("error") or "no response"}],
            "payment": None,
            "fix_order": ["Make the endpoint return HTTP 200 or 402 before anything else."],
        }

    status = probe_result.get("status")
    if status != 402:
        add(60, "not_402", "Endpoint returned HTTP %s, not 402. Buyers never see a payment requirement." % status)

    pay = decode_payment_required(probe_result)
    headers = probe_result.get("headers", {})

    if not pay["accepts"]:
        add(40, "no_accepts", "No payment requirements found in PAYMENT-REQUIRED header or JSON body.")
    if pay.get("v2_decode_error"):
        add(20, "bad_v2_header", "PAYMENT-REQUIRED header is present but is not base64-encoded JSON.")
    if pay["source"] and pay["source"].startswith("JSON body"):
        add(10, "v1_only", "Only the v1 JSON body is emitted; v2 clients lose resource metadata. Add the PAYMENT-REQUIRED header.")
    if pay["source"] and "payment-required" not in headers:
        add(10, "header_missing", "No PAYMENT-REQUIRED response header.")

    exposed = headers.get("access-control-expose-headers", "").lower()
    if "payment-required" not in exposed:
        add(10, "not_exposed", "PAYMENT-REQUIRED is not listed in Access-Control-Expose-Headers, so browser-based agents cannot read it.")
    if not headers.get("access-control-allow-origin"):
        add(10, "no_cors", "No Access-Control-Allow-Origin header; browser agents are blocked outright.")

    # Validate every accepted scheme. A defect present on only one option still
    # matters (a buyer may pick that option), but the same defect reported by
    # several accepts[] entries must not be charged several times, otherwise a
    # seller offering USDC+USDC+USDC+SOL looks five times worse than a seller
    # offering the same thing once.
    for i, accept in enumerate(pay["accepts"]):
        if not isinstance(accept, dict):
            add_once(15, "bad_accept", "accepts[%d] is not an object." % i)
            continue
        payto = accept.get("payTo")
        if not _valid_payto(payto):
            add_once(20, "bad_payto", "accepts[%d].payTo is not a valid address: %r" % (i, payto))
        asset = accept.get("asset")
        if isinstance(asset, str) and asset.lower() in KNOWN_ASSETS:
            pass
        elif asset:
            add_once(10, "unknown_asset",
                     "accepts[%d].asset %r is not a recognised mainnet USDC contract." % (i, asset))
        net = accept.get("network")
        if net in NETWORK_ALIASES:
            add_once(5, "legacy_network",
                     "accepts[%d].network %r is the v1 alias; v2 expects %r." % (i, net, NETWORK_ALIASES[net]))
        if _amount(accept) is None:
            add_once(10, "no_amount", "accepts[%d] has neither `amount` nor `maxAmountRequired`." % i)

    score = max(0, min(100, score))
    if score >= 90:
        verdict = "ready"
    elif score >= 70:
        verdict = "workable"
    elif score >= 40:
        verdict = "impaired"
    else:
        verdict = "broken"

    # impact is already negative, so ascending order puts the worst defect first.
    fix_order = [f["detail"] for f in sorted(findings, key=lambda x: x["impact"])]
    return {"score": score, "verdict": verdict, "findings": findings,
            "payment": pay, "fix_order": fix_order}


def audit(url: str, method: str = "GET") -> dict:
    """Probe and grade in one call."""
    p = probe(url, method=method)
    g = grade(p)
    return {"url": url, "method": method.upper(), "status": p.get("status"), "score": g["score"],
            "verdict": g["verdict"], "findings": g["findings"], "fix_order": g["fix_order"],
            "payment_source": (g["payment"] or {}).get("source"),
            "accepts": (g["payment"] or {}).get("accepts") or []}
