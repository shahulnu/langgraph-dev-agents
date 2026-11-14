"""Turns a cart into an order."""

from checkout.pricing import compute_total


def build_order(order_id: str, cart: dict, coupons: list[dict]) -> dict:
    return {
        "id": order_id,
        "items": cart["items"],
        "ship_to": cart["shipping"],
        "total": compute_total(cart["items"], coupons),
    }


def build_gift_order(order_id: str, cart: dict, coupons: list[dict]) -> dict:
    order = build_order(order_id, cart, coupons)
    order["gift_message"] = cart["gift_message"]
    order["ship_to"] = cart["shipping_address"]
    order["hide_prices"] = True
    return order


def place_order(order_id: str, cart: dict, coupons: list[dict]) -> dict:
    if cart["gift"]:
        return build_gift_order(order_id, cart, coupons)
    return build_order(order_id, cart, coupons)
