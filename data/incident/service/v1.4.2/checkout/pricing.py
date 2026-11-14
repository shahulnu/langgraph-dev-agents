"""Order totals and coupons."""


def compute_total(items: list[dict], coupons: list[dict]) -> float:
    """Subtotal minus every coupon. A percent coupon takes value% off the subtotal;
    a fixed coupon takes value dollars off. Coupons can now be stacked."""
    subtotal = sum(i["price"] * i["qty"] for i in items)
    total = subtotal
    for c in coupons:
        if c["kind"] == "percent":
            total -= subtotal * c["value"] / 100
        elif c["kind"] == "fixed":
            total -= c["value"]
    # Percent coupons apply to the subtotal, not to an already-discounted total.
    percent = sum(c["value"] for c in coupons if c["kind"] == "percent")
    total -= subtotal * percent / 100
    return round(max(total, 0), 2)
