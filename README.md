# x402-readiness

Audit whether an x402 endpoint can actually be reached **and paid**.

Most x402 tooling tells you whether a URL responds. This tells you whether a
*buyer client* can complete a payment against it — which is a different question,
and the one that costs sellers money.

```console
$ x402-readiness https://your-api.example/paid/endpoint
READY       100/100  HTTP 402   https://your-api.example/paid/endpoint
             nothing to fix; buyers can read the payment requirement.

$ x402-readiness https://your-api.example/legacy-route
IMPAIRED     55/100  HTTP 402   https://your-api.example/legacy-route
   1. accepts[0].payTo is not a valid address: '0xabc'
   2. Only the v1 JSON body is emitted; v2 clients lose resource metadata.
   3. PAYMENT-REQUIRED is not listed in Access-Control-Expose-Headers.
```

Exit code is `0` when every endpoint scores ≥ 90, otherwise `1`, so it drops
into CI unchanged:

```yaml
- run: pip install git+https://github.com/yinxin999/x402-readiness
- run: x402-readiness ${{ env.PAID_ENDPOINT }} --fail-under 90
```

No dependencies. Python 3.9+. One file of logic, standard library only, so you
can vendor it if your build policy requires it.

## Why this exists

Across the self-registered x402 listings indexed by 402index, the dominant
failure mode is not "the service is down". It is a seller who believes they are
live while no buyer can pay them:

- **v2 clients read the `PAYMENT-REQUIRED` response header first.** A seller that
  only emits the v1 JSON body still half-works, but loses `resource` metadata
  (service name, tags, icon) that buyers and directories use for discovery.
- **`PAYMENT-REQUIRED` must be listed in `Access-Control-Expose-Headers`.** Without
  it, a browser-based agent can see the 402 status but cannot read the payment
  requirement at all — the payment is impossible, not merely awkward.
- **v1 and v2 disagree on field names.** v1 uses `maxAmountRequired` and
  `network: "base"`; v2 uses `amount` and `network: "eip155:8453"`. Emitting only
  one shape silently breaks the other generation of clients.
- **A `payTo` that is not an address, or an `asset` that is not USDC**, passes a
  naive "does it return 402" check while being unpayable.

## Signal → score

Start at 100 and subtract for each defect, weighted by how likely it is to break
a real payment:

| Finding | Impact |
| --- | --- |
| Endpoint does not return 402 | −60 |
| No payment requirements in header or body | −40 |
| `payTo` is not a valid EVM/Solana address | −20 |
| `PAYMENT-REQUIRED` header is not base64 JSON | −20 |
| Only the v1 body, no v2 header | −10 |
| Header present but not in `Access-Control-Expose-Headers` | −10 |
| No `Access-Control-Allow-Origin` | −10 |
| Unrecognised asset | −10 |
| No `amount` / `maxAmountRequired` | −10 |
| Legacy v1 network alias (`base` instead of `eip155:8453`) | −5 |

Each distinct defect is charged once, even when it appears on several
`accepts[]` entries — offering USDC on five chains should not look five times
worse than offering it on one.

| Verdict | Score |
| --- | --- |
| `ready` | ≥ 90 |
| `workable` | 70–89 |
| `impaired` | 40–69 |
| `broken` | < 40 |
| `unreachable` | no response |

## Library use

```python
from x402_readiness.core import audit

r = audit("https://your-api.example/paid/endpoint")
if r["verdict"] != "ready":
    for fix in r["fix_order"]:
        print("fix:", fix)
```

`probe()` and `grade()` are separate if you want to feed in a response you
captured yourself (from a test fixture, a proxy log, or a recorded call):

```python
from x402_readiness.core import probe, grade
g = grade(probe(url))          # probe() never raises on HTTP errors
```

## Scope and honesty about it

- It does not settle or verify a payment. It inspects the *requirement* a seller
  publishes. Signing and settlement are a separate problem.
- The USDC contract list covers Base, Arbitrum, Polygon, Ethereum, Optimism and
  Solana. An asset outside that list is flagged, not rejected — you may be on a
  testnet on purpose.
- Probes are made with a single `GET` (or `--method`). It does not follow
  redirects into a payment flow, by design: it reports the first hop a buyer
  sees.

## Development

```console
$ python -m unittest discover -s tests -v
```

The tests pin the two scoring bugs I shipped and then found: real USDC being
flagged as an unknown asset because of a mistyped constant, and a rank-order
sign error that put the worst defect last.

## License

MIT
