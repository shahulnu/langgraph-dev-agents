"""Generate the synthetic incidents: logs, metrics and notes for each, from a fixed seed.

  uv run python scripts/make_incidents.py

The v1.4.2 deploy lands at 14:10 UTC. Logs switch from plain text to JSON at the deploy, because one
of the deployed commits changed the log format (a red herring). Each incident's evidence is written
to data/incident/incidents/<id>.json."""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "incident" / "incidents"
START = datetime(2026, 11, 12, 13, 30, tzinfo=timezone.utc)
DEPLOY = datetime(2026, 11, 12, 14, 10, tzinfo=timezone.utc)
END = datetime(2026, 11, 12, 15, 0, tzinfo=timezone.utc)


def iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


class Logs:
    def __init__(self):
        self.lines: list[dict] = []

    def add(self, t: datetime, level: str, logger: str, message: str):
        self.lines.append({"t": t, "level": level, "logger": logger, "message": message})

    def finish(self) -> list[dict]:
        out = []
        for i, l in enumerate(sorted(self.lines, key=lambda x: x["t"]), 1):
            fmt = "json" if l["t"] >= DEPLOY else "text"
            out.append({"id": f"L{i:04d}", "ts": iso(l["t"]), "level": l["level"], "logger": l["logger"], "message": l["message"], "format": fmt})
        return out


def minutes():
    t = START
    while t < END:
        yield t
        t += timedelta(minutes=1)


def series(fn) -> list[dict]:
    return [{"ts": iso(t), "value": round(fn(t), 3)} for t in minutes()]


def base_orders(logs: Logs, rnd: random.Random, every: int = 3, coupon_bug: bool = False, gift_bug: bool = False):
    n = 0
    for t in minutes():
        if (t.minute % every) != 0:
            continue
        n += 1
        oid = f"O-{58000 + n}"
        gift = n % 7 == 0
        after = t >= DEPLOY
        if gift and gift_bug and after:
            logs.add(t + timedelta(seconds=12), "ERROR", "checkout.api",
                     f"POST /checkout 500 order={oid} KeyError: 'shipping_address' "
                     f"(File \"checkout/orders.py\", line 18, in build_gift_order)")
            continue
        coupons, subtotal = [], rnd.choice([40.0, 60.0, 80.0, 100.0, 120.0])
        if n % 4 == 0:
            coupons = [{"kind": "percent", "value": 10}]
        total = subtotal
        if coupons:
            total = subtotal * (0.8 if (coupon_bug and after) else 0.9)
        c = ",".join(f"{x['kind']}:{x['value']}" for x in coupons) or "none"
        logs.add(t + timedelta(seconds=10), "INFO", "checkout.orders", f"order placed order={oid} gift={str(gift).lower()} subtotal={subtotal:.2f} coupons={c} total={total:.2f}")


def background(logs: Logs, rnd: random.Random, timeouts_from: datetime | None = None):
    for t in minutes():
        if t.minute % 10 == 0:
            logs.add(t + timedelta(seconds=2), "INFO", "checkout.health", "health check ok latency_ms=" + str(rnd.randint(18, 40)))
        # PayFast has an occasional timeout on a normal day.
        if t.minute in (37, 58) and t.hour == 13 or (t.hour == 14 and t.minute == 41):
            logs.add(t + timedelta(seconds=30), "WARN", "checkout.payments", "PayFast charge timeout after 8s; retry 1 succeeded")
        if timeouts_from and t >= timeouts_from:
            for _ in range(rnd.randint(2, 5) if t < timeouts_from + timedelta(minutes=70) else 0):
                logs.add(t + timedelta(seconds=rnd.randint(0, 59)), "WARN", "checkout.payments", "PayFast charge timeout after 8s; retry exhausted, order left unpaid")
    logs.add(DEPLOY, "INFO", "deploy", "deployed checkout v1.4.2 (a41c9e2, 7d03b5f, c92e1aa)")


def inc_500s(rnd):
    logs = Logs()
    background(logs, rnd)
    base_orders(logs, rnd, every=1, gift_bug=True, coupon_bug=True)
    return {
        "id": "INC-201",
        "title": "Checkout 500 errors after the afternoon deploy",
        "teaser": "Errors jump right after the deploy; one code path missed a rename",
        "opened": "2026-11-12T14:24:00Z",
        "description": "Alert: checkout error rate above 5% for 10 minutes. Some customers can't place orders. The v1.4.2 deploy went out at 14:10 UTC.",
        "logs": logs.finish(),
        "metrics": {
            "error_rate_pct": series(lambda t: (6.1 if t >= DEPLOY else 0.2) + rnd.uniform(-0.3, 0.3)),
            "orders_per_min": series(lambda t: (19.0 if t >= DEPLOY else 20.0) + rnd.uniform(-1.5, 1.5)),
            "avg_order_value_usd": series(lambda t: 74.0 + rnd.uniform(-2, 2)),
            "payment_timeouts_per_min": series(lambda t: rnd.choice([0, 0, 0, 1])),
        },
        "notes": [
            {"id": "N1", "source": "support", "ts": "2026-11-12T14:21:00Z", "text": "Two customers report 'something went wrong' when sending an order as a gift."},
        ],
    }


def inc_totals(rnd):
    logs = Logs()
    background(logs, rnd)
    base_orders(logs, rnd, every=1, coupon_bug=True)
    return {
        "id": "INC-202",
        "title": "Average order value dropped",
        "teaser": "No errors at all, but customers are paying less than they should",
        "opened": "2026-11-12T15:05:00Z",
        "description": "Finance dashboard: average order value down about 10% since early afternoon. No error alerts fired. The v1.4.2 deploy went out at 14:10 UTC.",
        "logs": logs.finish(),
        "metrics": {
            "error_rate_pct": series(lambda t: 0.2 + rnd.uniform(-0.1, 0.1)),
            "orders_per_min": series(lambda t: 20.0 + rnd.uniform(-1.5, 1.5)),
            "avg_order_value_usd": series(lambda t: (71.5 if t >= DEPLOY else 74.0) + rnd.uniform(-1, 1)),
            "coupon_redemptions_per_min": series(lambda t: 5.0 + rnd.uniform(-1, 1)),
        },
        "notes": [
            {"id": "N1", "source": "support", "ts": "2026-11-12T14:37:00Z", "text": "Customer says their 10% welcome coupon took 20% off. Happy customer, but flagging it."},
            {"id": "N2", "source": "support", "ts": "2026-11-12T14:52:00Z", "text": "Another order shows double the coupon discount on the receipt."},
        ],
    }


def inc_timeouts(rnd):
    logs = Logs()
    onset = datetime(2026, 11, 12, 13, 52, tzinfo=timezone.utc)
    background(logs, rnd, timeouts_from=onset)
    base_orders(logs, rnd, every=2, coupon_bug=True)
    return {
        "id": "INC-203",
        "title": "Payment timeouts spiking",
        "teaser": "Looks like the deploy, but the timeouts started before it",
        "opened": "2026-11-12T14:30:00Z",
        "description": "Alert: payment timeouts above threshold; unpaid orders piling up. The v1.4.2 deploy went out at 14:10 UTC and the log format changed at the same time.",
        "logs": logs.finish(),
        "metrics": {
            "error_rate_pct": series(lambda t: (2.8 if t >= onset else 0.2) + rnd.uniform(-0.3, 0.3)),
            "orders_per_min": series(lambda t: 20.0 + rnd.uniform(-1.5, 1.5)),
            "avg_order_value_usd": series(lambda t: 74.0 + rnd.uniform(-2, 2)),
            "payment_timeouts_per_min": series(lambda t: (3.5 if t >= onset else 0.1) + rnd.uniform(-0.5, 0.5)),
        },
        "notes": [
            {"id": "N1", "source": "vendor status page", "ts": "2026-11-12T14:02:00Z", "text": "PayFast: degraded performance in us-east. Elevated API latency and timeouts since 13:50 UTC. Investigating."},
            {"id": "N2", "source": "on-call chat", "ts": "2026-11-12T14:26:00Z", "text": "Logs look different since the deploy (JSON now). Did the deploy break payments?"},
        ],
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for fn, seed in ((inc_500s, 201), (inc_totals, 202), (inc_timeouts, 203)):
        inc = fn(random.Random(seed))
        (OUT / f"{inc['id']}.json").write_text(json.dumps(inc, indent=1) + "\n", encoding="utf-8")
        print(f"Wrote {inc['id']}: {len(inc['logs'])} log lines, {len(inc['metrics'])} metrics, {len(inc['notes'])} notes")


if __name__ == "__main__":
    main()
