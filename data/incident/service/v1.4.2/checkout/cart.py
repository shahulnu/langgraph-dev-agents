"""Builds a cart from the checkout form."""


def make_cart(items: list[dict], shipping: dict, gift: bool = False, gift_message: str = "") -> dict:
    return {
        "items": items,
        "shipping": shipping,
        "gift": gift,
        "gift_message": gift_message,
    }
