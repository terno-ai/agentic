"""Customer support agent example.

Demonstrates:
- Custom system prompt
- Domain-specific tools (no file/bash access)
- Persistent sessions per customer
- Escalation workflow

Run:
    python examples/sdk_customer_support.py
"""

from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta

from agentic import Agent, tool

# ---------------------------------------------------------------------------
# Simulated database / services
# ---------------------------------------------------------------------------

_ORDERS = {
    "ORD-1001": {"status": "delivered", "item": "Wireless Headphones", "date": "2026-05-10"},
    "ORD-1002": {"status": "in_transit", "item": "Laptop Stand", "eta": "2026-05-23"},
    "ORD-1003": {"status": "processing", "item": "USB-C Hub", "eta": "2026-05-25"},
    "ORD-1004": {"status": "cancelled", "item": "Phone Case", "refund": "pending"},
}

_REFUNDS = {}


# ---------------------------------------------------------------------------
# Tools — all domain-specific, no file/bash access
# ---------------------------------------------------------------------------

@tool
async def get_order_status(order_id: str) -> str:
    """Look up the current status and details of an order."""
    order = _ORDERS.get(order_id.upper())
    if not order:
        return f"Order {order_id} not found. Please check the order ID."
    lines = [f"Order {order_id.upper()} — {order['item']}"]
    lines.append(f"Status: {order['status'].replace('_', ' ').title()}")
    if "date" in order:
        lines.append(f"Delivered: {order['date']}")
    if "eta" in order:
        lines.append(f"Estimated delivery: {order['eta']}")
    if "refund" in order:
        lines.append(f"Refund: {order['refund']}")
    return "\n".join(lines)


@tool
async def submit_refund_request(order_id: str, reason: str) -> str:
    """Submit a refund request for an order."""
    order = _ORDERS.get(order_id.upper())
    if not order:
        return f"Cannot submit refund: order {order_id} not found."
    if order["status"] not in ("delivered", "cancelled"):
        return f"Cannot refund order in '{order['status']}' status. Only delivered or cancelled orders are eligible."
    refund_id = f"REF-{random.randint(10000, 99999)}"
    _REFUNDS[refund_id] = {"order_id": order_id, "reason": reason, "status": "submitted"}
    return (
        f"Refund submitted successfully.\n"
        f"Refund ID: {refund_id}\n"
        f"Processing time: 3–5 business days.\n"
        f"You will receive an email confirmation shortly."
    )


@tool
async def check_refund_status(refund_id: str) -> str:
    """Check the status of a previously submitted refund."""
    refund = _REFUNDS.get(refund_id.upper())
    if not refund:
        return f"Refund {refund_id} not found."
    return f"Refund {refund_id}: {refund['status']} (for order {refund['order_id']})"


@tool
async def escalate_to_human(customer_message: str, urgency: str) -> str:
    """Escalate the issue to a human support agent.

    Args:
        customer_message: Summary of the customer's issue.
        urgency: low, medium, or high.
    """
    ticket_id = f"TKT-{random.randint(100000, 999999)}"
    print(f"\n[ESCALATION] Ticket {ticket_id} created — urgency: {urgency}")
    print(f"[ESCALATION] Summary: {customer_message}\n")
    return (
        f"I've escalated your case to our support team.\n"
        f"Ticket ID: {ticket_id}\n"
        f"Urgency: {urgency}\n"
        f"A human agent will contact you within "
        f"{'1 hour' if urgency == 'high' else '4 hours' if urgency == 'medium' else '24 hours'}."
    )


# ---------------------------------------------------------------------------
# Build the agent
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are Aria, a friendly and efficient customer support agent for ShopFast.

## Your responsibilities
- Help customers track their orders and understand delivery status
- Process refund requests for eligible orders
- Answer questions about ShopFast policies
- Escalate complex or high-priority issues to human agents

## Refund policy
- Items can be refunded within 30 days of delivery
- Cancelled orders are automatically eligible for a full refund
- In-transit or processing orders cannot be refunded until delivered

## Escalation triggers
Escalate immediately (urgency=high) if the customer:
- Reports a missing or stolen package after marked delivered
- Has been waiting more than 14 days for a refund
- Is expressing significant frustration or distress
- Has a dispute over a charge above $500

## Tone guidelines
- Be warm and empathetic
- Be concise — don't over-explain
- Always confirm the action you've taken before ending a message
- Never promise timelines you can't guarantee
"""


def build_agent() -> Agent:
    agent = Agent(
        model="claude-sonnet-4-6",
        system_prompt=SYSTEM_PROMPT,
        tools=[],  # No built-in tools — only domain tools below
        memory=True,
        user_id="support-demo",
    )
    agent.add_tool(get_order_status)
    agent.add_tool(submit_refund_request)
    agent.add_tool(check_refund_status)
    agent.add_tool(escalate_to_human)
    return agent


# ---------------------------------------------------------------------------
# Demo conversation
# ---------------------------------------------------------------------------

async def demo_conversation() -> None:
    agent = build_agent()
    session = agent.session()

    conversations = [
        "Hi! I placed an order a few days ago and I'm wondering where it is. My order number is ORD-1002.",
        "Great, thanks! Also, I have another order ORD-1001 that was delivered but the headphones are broken. Can I get a refund?",
        "Yes please, the right earbud stopped working after one day.",
        "What's my refund ID again? I forgot to write it down.",
    ]

    print("=" * 60)
    print("ShopFast Customer Support Demo")
    print("=" * 60)

    for user_msg in conversations:
        print(f"\nCustomer: {user_msg}")
        print("Aria: ", end="", flush=True)

        from agentic import TextEvent, ToolStartEvent
        async for event in session.stream(user_msg):
            if isinstance(event, TextEvent):
                print(event.text, end="", flush=True)
            elif isinstance(event, ToolStartEvent):
                print(f"\n  [→ {event.tool_name}({list(event.tool_input.values())})]", end="", flush=True)
                print("\nAria: ", end="", flush=True)
        print()

    print("\n" + "=" * 60)
    print("Session complete. The agent remembered all prior context.")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(demo_conversation())
