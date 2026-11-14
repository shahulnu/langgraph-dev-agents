"""Charges an order through the PayFast gateway."""

GATEWAY_TIMEOUT_SECONDS = 8


class GatewayTimeout(Exception):
    pass


def charge(order: dict, gateway) -> dict:
    """Charge the order total. The gateway client raises GatewayTimeout after the timeout."""
    response = gateway.charge(amount=order["total"], currency="USD", timeout=GATEWAY_TIMEOUT_SECONDS)
    return {"order_id": order["id"], "status": response["status"], "charge_id": response["id"]}
