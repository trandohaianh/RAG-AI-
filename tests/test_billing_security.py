"""Signed webhooks must preserve account boundaries and current entitlement."""
import hashlib
import hmac
import json
import time
from types import SimpleNamespace

import pytest
import stripe
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import billing.router as billing
from auth.models import User
from chat import models as chat_models  # noqa: F401
from database import Base, get_db


@pytest.fixture
def billing_client(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        db.add_all([
            User(id=701, email="payer@example.com", hashed_password="unused",
                 stripe_customer_id="cus_payer", subscription_plan="free"),
            User(id=702, email="other@example.com", hashed_password="unused",
                 stripe_customer_id="cus_other", subscription_plan="free"),
        ])
        db.commit()
    state = {"status": "active", "price": "price_pro", "failure": False, "calls": []}

    def subscriptions_list(**params):
        state["calls"].append(params)
        if state["failure"]:
            raise RuntimeError("provider unavailable")
        matches = state["status"] == params["status"] and state["price"] == params["price"]
        return {"data": [{
            "customer": "cus_payer", "status": state["status"],
            "items": {"data": [{"price": {"id": state["price"]}}]},
        }] if matches else []}

    sdk = SimpleNamespace(Webhook=stripe.Webhook, Subscription=SimpleNamespace(list=subscriptions_list))
    monkeypatch.setattr(billing, "_stripe", lambda: sdk)
    monkeypatch.setattr(billing, "STRIPE_WEBHOOK_SECRET", "whsec_test_only")
    monkeypatch.setattr(billing, "STRIPE_PRICE_PRO", "price_pro")
    app = FastAPI()
    app.include_router(billing.router)

    def override_db():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        yield client, sessions, state
    engine.dispose()


def post_event(client, event_type="customer.subscription.updated", customer="cus_payer",
               status="active", signature=True):
    payload = json.dumps({
        "id": "evt_test", "object": "event", "type": event_type,
        "data": {"object": {
            "id": "sub_test", "customer": customer, "status": status,
            "metadata": {"user_id": "702"},
        }},
    }, separators=(",", ":"))
    now = str(int(time.time()))
    digest = hmac.new(b"whsec_test_only", f"{now}.{payload}".encode(), hashlib.sha256).hexdigest()
    headers = {"Content-Type": "application/json"}
    if signature:
        headers["stripe-signature"] = f"t={now},v1={digest}"
    return client.post("/billing/webhook", content=payload, headers=headers)


def plan(sessions, user_id=701):
    with sessions() as db:
        return db.get(User, user_id).subscription_plan


def test_signature_required_and_sdk_event_is_supported(billing_client, monkeypatch):
    client, sessions, state = billing_client
    assert post_event(client, signature=False).status_code == 400
    assert not state["calls"]
    monkeypatch.setattr(billing, "STRIPE_WEBHOOK_SECRET", "")
    assert post_event(client).status_code == 503
    assert plan(sessions) == "free"


def test_signed_event_uses_current_customer_not_metadata_or_old_status(billing_client):
    client, sessions, state = billing_client
    # Stale canceled event must not revoke an active current subscription.
    assert post_event(client, "customer.subscription.deleted", status="canceled").status_code == 200
    assert plan(sessions) == "pro"
    assert plan(sessions, 702) == "free"
    # Replaying an old active event must not restore a canceled subscription.
    state["status"] = "canceled"
    assert post_event(client).status_code == 200
    assert plan(sessions) == "free"
    assert post_event(client).status_code == 200
    assert plan(sessions) == "free"


@pytest.mark.parametrize("status,price", [
    ("incomplete", "price_pro"), ("past_due", "price_pro"), ("unpaid", "price_pro"),
    ("paused", "price_pro"), ("active", "price_other"),
])
def test_unentitled_subscription_never_grants_pro(billing_client, status, price):
    client, sessions, state = billing_client
    state.update(status=status, price=price)
    assert post_event(client).status_code == 200
    assert plan(sessions) == "free"


def test_provider_failure_and_unknown_customer_do_not_change_plan(billing_client):
    client, sessions, state = billing_client
    state["failure"] = True
    assert post_event(client).status_code == 503
    assert plan(sessions) == "free"
    assert post_event(client, customer="cus_unknown").status_code == 200
    assert plan(sessions, 702) == "free"


def test_deleted_account_cannot_be_reactivated(billing_client):
    from datetime import datetime, timezone
    client, sessions, state = billing_client
    with sessions() as db:
        db.get(User, 701).deleted_at = datetime.now(timezone.utc)
        db.commit()
    assert post_event(client).status_code == 200
    assert not state["calls"]
    assert plan(sessions) == "free"


def test_current_trial_with_matching_price_grants_pro(billing_client):
    client, sessions, state = billing_client
    state["status"] = "trialing"
    assert post_event(client).status_code == 200
    assert plan(sessions) == "pro"


def test_tampered_signature_is_rejected_without_provider_lookup(billing_client):
    client, sessions, state = billing_client
    payload = json.dumps({"object": "event", "type": "customer.subscription.updated",
                          "data": {"object": {"customer": "cus_payer"}}})
    response = client.post("/billing/webhook", content=payload, headers={
        "Content-Type": "application/json", "stripe-signature": f"t={int(time.time())},v1=wrong",
    })
    assert response.status_code == 400
    assert not state["calls"]
    assert plan(sessions) == "free"


def test_checkout_is_disabled_without_working_webhook(billing_client, monkeypatch):
    from auth.schemas import CurrentUser
    client, sessions, state = billing_client
    client.app.dependency_overrides[billing.get_current_user] = lambda: CurrentUser(
        id=701, email="payer@example.com", role="user",
    )
    monkeypatch.setattr(billing, "STRIPE_WEBHOOK_SECRET", "")
    assert client.post("/billing/checkout").status_code == 503
    assert not state["calls"]


def test_concurrent_newer_webhook_wins_over_delayed_lookup(billing_client, monkeypatch, tmp_path):
    import asyncio
    import threading
    from concurrent.futures import ThreadPoolExecutor

    client, _, _ = billing_client
    engine = create_engine(
        f"sqlite:///{tmp_path / 'billing-race.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        db.add(User(id=701, email="payer@example.com", hashed_password="unused",
                    stripe_customer_id="cus_payer", subscription_plan="free"))
        db.commit()
    first_started = threading.Event()
    release_first = threading.Event()
    counter_lock = threading.Lock()
    calls = 0

    def lookup(_stripe, _customer):
        nonlocal calls
        with counter_lock:
            calls += 1
            number = calls
        if number == 1:
            first_started.set()
            assert release_first.wait(10)
            return "pro"
        return "free"

    monkeypatch.setattr(billing, "_current_customer_plan", lookup)
    payload = json.dumps({
        "object": "event", "id": "evt_race", "type": "customer.subscription.updated",
        "data": {"object": {"customer": "cus_payer"}},
    }).encode()
    now = str(int(time.time()))
    digest = hmac.new(b"whsec_test_only", now.encode() + b"." + payload, hashlib.sha256).hexdigest()

    async def body():
        return payload

    def invoke():
        request = SimpleNamespace(body=body, headers={"stripe-signature": f"t={now},v1={digest}"})
        with sessions() as db:
            return asyncio.run(billing.stripe_webhook(request, db))

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(invoke)
            assert first_started.wait(10)
            try:
                assert pool.submit(invoke).result(timeout=10) == {"received": True}
            finally:
                release_first.set()
            assert first.result(timeout=10) == {"received": True}
        assert plan(sessions) == "free"
        with sessions() as db:
            assert db.get(User, 701).billing_sync_version == 2
    finally:
        release_first.set()
        engine.dispose()


def test_account_disabled_during_lookup_cannot_receive_pro(billing_client, monkeypatch):
    from datetime import datetime, timezone

    client, sessions, state = billing_client

    def lookup(_stripe, _customer):
        with sessions() as db:
            db.get(User, 701).deleted_at = datetime.now(timezone.utc)
            db.commit()
        return "pro"

    monkeypatch.setattr(billing, "_current_customer_plan", lookup)
    assert post_event(client).status_code == 200
    assert plan(sessions) == "free"
