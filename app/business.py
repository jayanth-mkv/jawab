"""The demo business: a neighbourhood bakery in Indore, and the facts its owner starts with."""

from __future__ import annotations

from app.store import new_id, now, store

PROFILE = {
    "name": "Sweet Crumbs Bakery",
    "owner": "Ritu Sharma",
    "city": "Indore",
    "area": "Vijay Nagar",
    "address": "Shop 14, Scheme 54, Vijay Nagar, Indore, Madhya Pradesh 452010",
    "phone": "+91 731 400 1234",
    "hours": "Monday to Saturday 9am to 9pm, Sunday 10am to 6pm",
    "about": "Family-run bakery known for fresh cakes, eggless options and custom birthday cakes.",
}

SEED_FACTS = [
    ("What are your opening hours?", "Monday to Saturday 9am to 9pm, and Sunday 10am to 6pm."),
    ("Do you make eggless cakes?", "Yes. Every cake on our menu is available eggless at no extra cost."),
    ("How much does a cake cost?", "Half-kilo cakes start at 450 rupees and one-kilo cakes start at 650 rupees. Designer cakes are priced on request."),
    ("Do you make custom birthday cakes?", "Yes. Custom and photo cakes need at least 24 hours' notice."),
    ("Where is the shop?", "Shop 14, Scheme 54, Vijay Nagar, Indore."),
    ("Do you deliver?", "Yes, we deliver within Vijay Nagar. The delivery fee is 50 rupees."),
    ("How can I pay?", "We accept UPI, cards and cash."),
]


async def seed() -> None:
    """Reset the demo to the owner's starting facts and no gaps."""
    await store.clear()
    for q, a in SEED_FACTS:
        await store.put_fact({
            "id": new_id(), "question": q, "answer": a, "answer_hi": "",
            "source": "owner (setup)", "status": "confirmed", "published": True,
            "gap_id": None, "created_at": now(),
        })
