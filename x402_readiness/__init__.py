"""
x402-readiness: audit whether an x402 endpoint can actually be reached and paid.

Why this exists
---------------
Across ~13k self-registered x402 listings on 402index, the common failure is not
"the service is down". It is "the seller thinks they are live, but no buyer can
complete a payment": missing PAYMENT-REQUIRED header, a v1 body that lacks the
fields a v2 client reads, no CORS headers so browser agents are blocked, a payTo
address that is not an address at all, or a payment asset that is not USDC.

This tool probes an endpoint the way a buyer client would, and reports the
specific reasons a payment would fail. It is deliberately dependency-free: only
the Python standard library, so it can be vendored into CI.

Only standard library. No network calls beyond the target URL and public RPC.
"""

__version__ = "0.1.0"
