"""Describe our shop's ACP checkout interface."""

from starlette.responses import JSONResponse


async def checkout_card(request):
    base_url = str(request.base_url).rstrip("/")

    tools = [
        {
            "name": "create_checkout_session",
            "method": "POST",
            "path": "/checkout_sessions",
            "input": {
                "line_items": "[{id: item, quantity: 1}]",
                "receipt": "The receipt signed by our seller",
            },
        },
        {
            "name": "get_checkout_session",
            "method": "GET",
            "path": "/checkout_sessions/{id}",
            "input": {},
        },
        {
            "name": "complete_checkout_session",
            "method": "POST",
            "path": "/checkout_sessions/{id}/complete",
            "input": {
                "payment_data": (
                    "{order, checkout_mandate, payment_mandate}"
                ),
            },
        },
        {
            "name": "cancel_checkout_session",
            "method": "POST",
            "path": "/checkout_sessions/{id}/cancel",
            "input": {},
        },
    ]

    return JSONResponse({
        "protocol": "acp",
        "version": "2026-04-17",
        "endpoint": f"{base_url}/acp",
        "capabilities": {
            "services": ["checkout"],
            "payment_methods": ["ap2-mandate"],
        },
        "tools": tools,
    })