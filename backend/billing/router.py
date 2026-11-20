"""Stripe subscription — tuần 7–8."""

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from auth.models import User
from auth.schemas import CurrentUser
from config import (
    APP_PUBLIC_URL,
    STRIPE_PRICE_PRO,
    STRIPE_SECRET_KEY,
    STRIPE_WEBHOOK_SECRET,
)
from database import get_db
from dependencies import get_current_user

router = APIRouter(prefix="/billing", tags=["billing"])


class BillingStatusOut(BaseModel):
    plan: str
    stripe_configured: bool
    daily_limit: int
    queries_today: int


class CheckoutOut(BaseModel):
    url: str


def _stripe():
    if not STRIPE_SECRET_KEY:
        return None
    import stripe

    stripe.api_key = STRIPE_SECRET_KEY
    return stripe


def cancel_customer_subscriptions(customer_id: str | None) -> int:
    """Huỷ ngay mọi subscription còn hiệu lực của một customer (khi tài khoản bị vô hiệu hoá).

    Trả về số subscription đã huỷ. Không cấu hình Stripe hoặc không có customer thì bỏ qua.
    """
    stripe = _stripe()
    if not stripe or not customer_id:
        return 0
    cancelled = 0
    for status in ("active", "trialing", "past_due"):
        listing = stripe.Subscription.list(customer=customer_id, status=status, limit=100)
        rows = listing.get("data", []) if isinstance(listing, dict) else getattr(listing, "data", [])
        for sub in rows:
            sub_id = sub.get("id") if isinstance(sub, dict) else getattr(sub, "id", None)
            if sub_id:
                stripe.Subscription.cancel(sub_id)
                cancelled += 1
    return cancelled


@router.get("/status", response_model=BillingStatusOut)
def billing_status(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from saas.limits import daily_query_count, plan_daily_limit

    return BillingStatusOut(
        plan=user.subscription_plan,
        stripe_configured=bool(STRIPE_SECRET_KEY and STRIPE_PRICE_PRO and STRIPE_WEBHOOK_SECRET),
        daily_limit=plan_daily_limit(user.subscription_plan),
        queries_today=daily_query_count(db, user.id),
    )


@router.post("/checkout", response_model=CheckoutOut)
def create_checkout(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stripe = _stripe()
    if not stripe or not STRIPE_PRICE_PRO or not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(
            status_code=503,
            detail="Stripe chưa cấu hình (STRIPE_SECRET_KEY, STRIPE_PRICE_PRO, STRIPE_WEBHOOK_SECRET)",
        )

    row = db.query(User).filter(User.id == user.id).first()
    if not row:
        raise HTTPException(status_code=404, detail="User không tồn tại")

    customer_id = row.stripe_customer_id
    if not customer_id:
        # Hai lần bấm đồng thời không được tạo hai customer: webhook chỉ nhận diện tài khoản
        # theo stripe_customer_id, nên thanh toán trên customer bị ghi đè sẽ không bao giờ nâng gói.
        customer = stripe.Customer.create(
            email=row.email,
            metadata={"user_id": str(row.id)},
            idempotency_key=f"fieldops-customer-{row.id}",
        )
        db.execute(
            update(User)
            .where(User.id == row.id, User.stripe_customer_id.is_(None))
            .values(stripe_customer_id=customer.id)
            .execution_options(synchronize_session=False)
        )
        db.commit()
        # Nếu request khác đã ghi trước, dùng customer đã lưu (người thắng), không phải bản vừa tạo.
        customer_id = db.scalar(select(User.stripe_customer_id).where(User.id == row.id))

    session = stripe.checkout.Session.create(
        customer=customer_id,
        mode="subscription",
        line_items=[{"price": STRIPE_PRICE_PRO, "quantity": 1}],
        success_url=f"{APP_PUBLIC_URL}/settings?billing=success",
        cancel_url=f"{APP_PUBLIC_URL}/settings?billing=cancel",
        metadata={"user_id": str(row.id)},
    )
    return CheckoutOut(url=session.url)


@router.post("/portal", response_model=CheckoutOut)
def customer_portal(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stripe = _stripe()
    if not stripe:
        raise HTTPException(status_code=503, detail="Stripe chưa cấu hình")

    row = db.query(User).filter(User.id == user.id).first()
    if not row or not row.stripe_customer_id:
        raise HTTPException(status_code=400, detail="Chưa có subscription Stripe")

    session = stripe.billing_portal.Session.create(
        customer=row.stripe_customer_id,
        return_url=f"{APP_PUBLIC_URL}/settings",
    )
    return CheckoutOut(url=session.url)


def _current_customer_plan(stripe, customer_id: str) -> str:
    """Resolve entitlement from current subscriptions instead of an event snapshot."""
    for status in ("active", "trialing"):
        page = stripe.Subscription.list(
            customer=customer_id,
            status=status,
            price=STRIPE_PRICE_PRO,
            limit=1,
        )
        for subscription in page["data"]:
            if (
                subscription["customer"] == customer_id
                and subscription["status"] == status
                and any(item["price"]["id"] == STRIPE_PRICE_PRO for item in subscription["items"]["data"])
            ):
                return "pro"
    return "free"


@router.post("/webhook")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    stripe = _stripe()
    if not stripe or not STRIPE_WEBHOOK_SECRET or not STRIPE_PRICE_PRO:
        raise HTTPException(status_code=503, detail="Stripe webhook chưa cấu hình đầy đủ")

    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        # Verify the original bytes before decoding. Use native dicts after verification:
        # Stripe Event objects do not expose dict.get() in all supported SDK versions.
        stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
        event = json.loads(payload)
        etype = event["type"]
        data = event["data"]["object"]
        if not isinstance(data, dict):
            raise ValueError("Invalid event object")
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Stripe webhook không hợp lệ") from exc

    if etype not in (
        "checkout.session.completed",
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    ):
        return {"received": True}

    customer_id = data.get("customer")
    if not isinstance(customer_id, str) or not customer_id:
        raise HTTPException(status_code=400, detail="Stripe webhook thiếu customer")

    # The customer is bound during authenticated checkout; never bind accounts using
    # user_id from an incoming event's metadata.
    row = (
        db.query(User)
        .filter(
            User.stripe_customer_id == customer_id,
            User.deleted_at.is_(None),
        )
        .first()
    )
    if row is None:
        return {"received": True}
    # Reserve a monotonic revision before the provider call. An older in-flight
    # lookup cannot overwrite a newer webhook, even across API workers.
    user_id = row.id
    revision = db.execute(
        update(User)
        .where(
            User.id == user_id,
            User.deleted_at.is_(None),
            User.stripe_customer_id == customer_id,
        )
        .values(billing_sync_version=User.billing_sync_version + 1)
        .returning(User.billing_sync_version)
        .execution_options(synchronize_session=False)
    ).scalar_one_or_none()
    db.commit()
    if revision is None:
        return {"received": True}
    try:
        plan = await asyncio.to_thread(_current_customer_plan, stripe, customer_id)
    except Exception as exc:
        # A non-2xx response asks Stripe to retry; retain the previous entitlement.
        raise HTTPException(status_code=503, detail="Chưa xác minh được subscription Stripe") from exc
    db.execute(
        update(User)
        .where(
            User.id == user_id,
            User.deleted_at.is_(None),
            User.stripe_customer_id == customer_id,
            User.billing_sync_version == revision,
        )
        .values(subscription_plan=plan)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return {"received": True}
