"""Stripe subscription — tuần 7–8."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from auth.models import User
from auth.schemas import CurrentUser
from config import (
    API_PUBLIC_URL,
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


@router.get("/status", response_model=BillingStatusOut)
def billing_status(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from saas.limits import daily_query_count, plan_daily_limit

    return BillingStatusOut(
        plan=user.subscription_plan,
        stripe_configured=bool(STRIPE_SECRET_KEY and STRIPE_PRICE_PRO),
        daily_limit=plan_daily_limit(user.subscription_plan),
        queries_today=daily_query_count(db, user.id),
    )


@router.post("/checkout", response_model=CheckoutOut)
def create_checkout(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stripe = _stripe()
    if not stripe or not STRIPE_PRICE_PRO:
        raise HTTPException(
            status_code=503,
            detail="Stripe chưa cấu hình (STRIPE_SECRET_KEY, STRIPE_PRICE_PRO)",
        )

    row = db.query(User).filter(User.id == user.id).first()
    if not row:
        raise HTTPException(status_code=404, detail="User không tồn tại")

    customer_id = row.stripe_customer_id
    if not customer_id:
        customer = stripe.Customer.create(email=row.email, metadata={"user_id": str(row.id)})
        customer_id = customer.id
        row.stripe_customer_id = customer_id
        db.commit()

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


@router.post("/webhook")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    stripe = _stripe()
    if not stripe:
        raise HTTPException(status_code=503, detail="Stripe chưa cấu hình")

    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        if STRIPE_WEBHOOK_SECRET:
            event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
        else:
            import json

            event = stripe.Event.construct_from(json.loads(payload), stripe.api_key)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    etype = event["type"]
    data = event.get("data", {}).get("object", {})

    if etype in ("checkout.session.completed", "customer.subscription.created", "customer.subscription.updated"):
        user_id = data.get("metadata", {}).get("user_id")
        customer_id = data.get("customer")
        if not user_id and customer_id:
            row = db.query(User).filter(User.stripe_customer_id == customer_id).first()
        else:
            row = db.query(User).filter(User.id == int(user_id)).first() if user_id else None
        if row:
            row.subscription_plan = "pro"
            if customer_id and not row.stripe_customer_id:
                row.stripe_customer_id = customer_id
            db.commit()

    if etype in ("customer.subscription.deleted",):
        customer_id = data.get("customer")
        row = db.query(User).filter(User.stripe_customer_id == customer_id).first()
        if row:
            row.subscription_plan = "free"
            db.commit()

    return {"received": True}
