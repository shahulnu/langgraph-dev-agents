"""Builds a cart from the checkout form."""


def make_cart(items: list[dict], shipping_address: dict, gift: bool = False, gift_message: str = "") -> dict:
    return {
        "items": items,
        "shipping_address": shipping_address,
        "gift": gift,
        "gift_message": gift_message,
    }
