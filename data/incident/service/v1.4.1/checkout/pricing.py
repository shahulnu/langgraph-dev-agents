"""Order totals and coupons."""


def compute_total(items: list[dict], coupons: list[dict]) -> float:
    """Subtotal minus at most one coupon. A percent coupon takes value% off the subtotal;
    a fixed coupon takes value dollars off."""
    subtotal = sum(i["price"] * i["qty"] for i in items)
    total = subtotal
    if coupons:
        c = coupons[0]
        if c["kind"] == "percent":
            total -= subtotal * c["value"] / 100
        elif c["kind"] == "fixed":
            total -= c["value"]
    return round(max(total, 0), 2)
