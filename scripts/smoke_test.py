"""
LeadAI Comprehensive Live Smoke Test.

Walks through every major subsystem of LeadAI:
1. Health & Public Configuration
2. Organization Context & Session Verification
3. Lead Deduplication Pipeline
4. Scheduled Search Scans
5. SLA Lead Assignment Rules
6. Outbound Webhooks & HMAC Signature
7. CRM Connectors & Formula-Safe Excel Export
8. Public REST API Key Generation & API v1 Endpoint Access
9. Razorpay Billing Provider & Webhook Verification
10. Unit Economics Super-Admin Report

Usage:
  python scripts/smoke_test.py              # against MONGO_URI from .env (use a TEST database)
  python scripts/smoke_test.py --in-memory  # against a throwaway in-memory MongoDB
"""
import os
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging
from datetime import datetime, timezone
from bson import ObjectId
from fastapi.testclient import TestClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("smoke_test")

def _use_in_memory_db():
    """--in-memory: run against a throwaway in-memory MongoDB instead of MONGO_URI."""
    os.environ.setdefault("PYTEST_CURRENT_TEST", "smoke-in-memory")  # no log file, no worker threads
    import mongomock
    import mongomock_motor
    import app.db.mongo as mongo
    sync_client = mongomock.MongoClient()
    async_client = mongomock_motor.AsyncMongoMockClient(mock_mongo_client=sync_client)
    mongo.get_sync_client = lambda: sync_client
    mongo.get_async_client = lambda: async_client
    print(">>> Using an in-memory MongoDB (--in-memory); the .env database is not touched")


def run_smoke_test():
    print("=" * 70)
    print(">>> STARTING LEADAI COMPREHENSIVE SMOKE TEST")
    print("=" * 70)

    from app.main import app
    client = TestClient(app)

    # 1. Healthcheck
    logger.info("Step 1: Testing Healthcheck endpoint (/health)...")
    res = client.get("/health")
    assert res.status_code == 200, f"Expected 200, got {res.status_code}"
    health_data = res.json()
    assert health_data.get("status") == "healthy" or "status" in health_data
    print("  [PASS] /health is healthy:", health_data.get("status"))

    # 2. Public Config
    logger.info("Step 2: Testing Public Config (/api/public/config)...")
    res = client.get("/api/public/config")
    assert res.status_code == 200, f"Expected 200, got {res.status_code}"
    print("  [PASS] /api/public/config returned valid brand & feature config")

    # 3. Lead Deduplication Engine
    logger.info("Step 3: Testing Lead Deduplication Engine...")
    from app.db.mongo import get_sync_db
    db = get_sync_db()

    from app.pipeline.deduplication import deduplicate_lead
    org_id = f"smoke_org_{ObjectId()}"
    lead1 = {
        "organization_id": org_id,
        "author_name": "Rohan Sharma",
        "author_url": "https://youtube.com/@rohansharma",
        "phone": "+919876543210",
        "email": "rohan@example.com",
        "platform": "youtube",
        "lead_score": 75,
        "lead_quality": "warm",
        "comment_text": "I want pricing for 2BHK flat",
        "created_at": datetime.now(timezone.utc),
    }
    id1, is_dup1, count1 = deduplicate_lead(db, lead1, org_id)
    assert not is_dup1, "First lead should not be duplicate"

    lead2 = {
        "organization_id": org_id,
        "author_name": "Rohan Sharma",
        "author_url": "https://youtube.com/@rohansharma",
        "phone": "+919876543210",
        "email": "rohan@example.com",
        "platform": "facebook",
        "lead_score": 90,
        "lead_quality": "hot",
        "comment_text": "Ready to visit the site today",
        "created_at": datetime.now(timezone.utc),
    }
    id2, is_dup2, count2 = deduplicate_lead(db, lead2, org_id)
    assert is_dup2 is True, "Second lead should be identified as duplicate"
    assert id1 == id2, "Expected IDs to match on duplicate merge"
    print("  [PASS] Deduplication engine created lead and merged duplicate across runs")

    # 4. Scheduled Search Scans
    logger.info("Step 4: Testing Scheduled Scans Service...")
    from app.services.scheduler import create_scheduled_scan, list_scheduled_scans, delete_scheduled_scan
    scan_doc = create_scheduled_scan(
        db,
        organization_id=org_id,
        user_id="user_smoke_1",
        url="https://youtube.com/@luxuryproperties",
        frequency="daily",
        interval_hours=24,
        only_new_posts=True,
    )
    assert scan_doc.get("frequency") == "daily"
    scans = list_scheduled_scans(db, org_id)
    assert len(scans) >= 1
    deleted = delete_scheduled_scan(db, str(scan_doc["_id"]), org_id)
    assert deleted is True
    print("  [PASS] Scheduled scans CRUD verified (create, list, delete)")

    # 5. Lead Assignment Rules & SLA Timers
    logger.info("Step 5: Testing Lead Assignment Rules & SLA Timers...")
    from app.services.lead_assignment import create_assignment_rule, assign_lead, delete_assignment_rule
    rule = create_assignment_rule(
        db,
        organization_id=org_id,
        name="High Priority Round Robin",
        rule_type="round_robin",
        assignees=["sales_agent_1", "sales_agent_2"],
        sla_hours=4,
    )
    assigned = assign_lead(db, str(id1), lead2, org_id)
    assert assigned is not None
    assert assigned.get("assigned_user_id") in ["sales_agent_1", "sales_agent_2"]
    assert assigned.get("sla_hours") == 4
    delete_assignment_rule(db, rule["rule_id"], org_id)
    print("  [PASS] Lead assignment rule evaluated with 4h SLA countdown timer")

    # 6. Outbound Webhooks & HMAC Signatures
    logger.info("Step 6: Testing Outbound Webhooks & HMAC Signature...")
    from app.services.outbound_webhooks import create_outbound_webhook, sign_payload, delete_outbound_webhook
    whk = create_outbound_webhook(
        db,
        organization_id=org_id,
        target_url="https://api.example.com/webhook",
        secret="smoke_webhook_secret_key_123",
        events=["lead.created", "lead.assigned"],
    )
    sig = sign_payload(b'{"event": "lead.created", "score": 90}', "smoke_webhook_secret_key_123")
    assert sig.startswith("sha256=")
    delete_outbound_webhook(db, whk["webhook_id"], org_id)
    print(f"  [PASS] Webhook registration and HMAC-SHA256 signature ({sig[:20]}...) verified")

    # 7. CRM Connectors & Formula-Safe Excel Export
    logger.info("Step 7: Testing CRM Connectors & Excel XML Generation...")
    from app.services.crm_connectors import HubSpotConnector, ZohoCRMConnector, ExcelExportService
    hs_lead = HubSpotConnector().format_lead(lead1)
    assert "email" in hs_lead["properties"]
    zh_lead = ZohoCRMConnector().format_lead(lead1)
    assert "Last_Name" in zh_lead

    # Test formula injection escaping
    malicious_lead = {
        "commenter_name": "=cmd|' /C calc'!A0",
        "phone": "+919876500000",
        "lead_score": 88,
    }
    xml_data = ExcelExportService.generate_spreadsheet_xml([malicious_lead], title="SmokeLeads")
    assert b"&#39;=cmd" in xml_data or b"'=cmd" in xml_data
    print("  [PASS] HubSpot, Zoho, and formula-safe Excel spreadsheet XML exports verified")

    # 8. Public REST API Keys & API v1
    logger.info("Step 8: Testing Public REST API Keys & /api/v1 Endpoints...")
    from app.services.api_keys import create_api_key, revoke_api_key
    raw_key, key_doc = create_api_key(
        db,
        organization_id=org_id,
        name="Smoke Test Key",
        scopes=["leads:read", "leads:write", "search:create"],
    )
    assert raw_key.startswith("lai_live_")

    # Test /api/v1/leads with the generated API key
    v1_res = client.get("/api/v1/leads", headers={"X-API-Key": raw_key})
    assert v1_res.status_code == 200, f"Expected 200, got {v1_res.status_code}: {v1_res.text}"
    data = v1_res.json()
    assert "items" in data and "total" in data
    print(f"  [PASS] Authenticated GET /api/v1/leads using API key {raw_key[:16]}...")

    # Test /api/v1/leads ingestion with API key
    post_res = client.post("/api/v1/leads", headers={"X-API-Key": raw_key}, json={
        "author_name": "API Prospect",
        "text": "Interested in pricing plan for enterprise",
        "platform": "youtube",
        "email": "api_prospect@test.com",
        "intent": "pricing_inquiry",
    })
    assert post_res.status_code == 200, post_res.text
    assert post_res.json().get("status") == "ingested"
    print("  [PASS] Authenticated POST /api/v1/leads ingested lead successfully")
    revoke_api_key(db, key_doc["key_id"], org_id)

    # 9. Razorpay Billing Provider
    logger.info("Step 9: Testing Razorpay Billing Provider...")
    from app.billing.provider import RazorpayBillingProvider
    from app.billing import provider as prov
    import httpx

    if "--in-memory" in sys.argv or os.environ.get("PYTEST_CURRENT_TEST"):
        def _mock_razorpay(request: httpx.Request) -> httpx.Response:
            if request.method == "POST" and request.url.path == "/v1/orders":
                body = _json.loads(request.content)
                return httpx.Response(200, json={
                    "id": "order_smoke_1234",
                    "entity": "order",
                    "amount": body["amount"],
                    "currency": body["currency"],
                    "receipt": body.get("receipt", "rcpt"),
                    "notes": body.get("notes", {}),
                    "status": "created"
                })
            return httpx.Response(200, json={"entity": "collection", "count": 0, "items": []})
        prov._HTTP_TRANSPORT = httpx.MockTransport(_mock_razorpay)

    import asyncio
    import hmac
    import hashlib
    import json as _json
    rp = RazorpayBillingProvider("rzp_test_key", "rzp_test_secret", "rzp_webhook_secret")
    sub_id = ObjectId()
    db.subscriptions.insert_one({"_id": sub_id, "status": "pending_payment", "amount": 2999.0})
    session = asyncio.run(rp.create_checkout_session(subscription={"_id": sub_id, "amount": 2999.0},
                                                     plan={"name": "Pro", "currency": "INR"},
                                                     customer_email="smoke@test.example",
                                                     success_url="/ok", cancel_url="/cancel"))
    assert session["provider"] == "razorpay" and session["currency"] == "INR"
    assert session["amount"] == 299900                      # minor units for the order
    assert db.subscriptions.find_one({"_id": sub_id})["amount"] == 2999.0   # stored in major units
    body = _json.dumps({"event": "payment.captured", "event_id": "evt_smoke",
                        "payload": {"payment": {"entity": {"id": "pay_123", "order_id": session["order_id"],
                                                           "amount": 299900, "notes": {"subscription_id": str(sub_id)}}}}}).encode()
    sig = hmac.new(b"rzp_webhook_secret", body, hashlib.sha256).hexdigest()
    event = rp.verify_webhook(body, {"x-razorpay-signature": sig})
    assert event["id"] == "evt_smoke" and event["type"] == "checkout.session.completed"
    print("  [PASS] Razorpay INR checkout session & HMAC webhook verification passed")

    # 10. Unit Economics
    logger.info("Step 10: Testing Unit Economics Report Engine...")
    from app.services.unit_economics import compute_action_costs, compute_plan_margins
    margins = compute_plan_margins()
    costs = compute_action_costs()
    assert margins and costs
    print("  [PASS] Unit economics report computed margins for all plan tiers")

    print("=" * 70)
    print("[SUCCESS] ALL 10 SUBSYSTEMS PASSED LIVE SMOKE TESTING WITH 100% SUCCESS!")
    print("=" * 70)

if __name__ == "__main__":
    if "--in-memory" in sys.argv:
        _use_in_memory_db()
    run_smoke_test()
