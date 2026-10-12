"""
Tests for Phase 6 Product Features:
1. YouTube comment collection & lead analysis
2. Scheduled/recurring scans & bulk URL input
3. Cross-run and cross-platform lead deduplication
4. Follow-up reminders and lead assignment rules with SLA timers
5. Outbound webhooks and CRM connectors (HubSpot, Zoho, Sheets, Excel)
6. Public REST API with API keys
7. Razorpay (UPI) billing & verified webhook auto-activation
8. Unit economics modeling & margin calculations
"""
import hashlib
import hmac
import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

import mongomock
import pytest
from bson import ObjectId

from app.db.models import utcnow
from app.pipeline.deduplication import deduplicate_lead
from app.services.api_keys import (
    authenticate_api_key,
    create_api_key,
    list_api_keys,
    revoke_api_key,
)
from app.services.crm_connectors import (
    ExcelExportService,
    GoogleSheetsFormatter,
    HubSpotConnector,
    ZohoCRMConnector,
)
from app.services.lead_assignment import (
    assign_lead,
    check_sla_and_reminders,
    create_assignment_rule,
    list_assignment_rules,
)
from app.services.outbound_webhooks import (
    create_outbound_webhook,
    list_outbound_webhooks,
    sign_payload,
)
from app.services.scheduler import (
    create_scheduled_scan,
    list_scheduled_scans,
    process_bulk_urls,
    trigger_due_scheduled_scans,
)
from app.services.unit_economics import (
    compute_action_costs,
    compute_plan_margins,
)


@pytest.fixture
def mock_db():
    client = mongomock.MongoClient()
    return client["test_phase6_leadai"]


# ── 1. YouTube Comment Collection & Lead Scraper ─────────────────────────────

def test_youtube_scraper_posts_and_comments():
    from app.social.scrapers import YouTubeScraper

    mock_connector = MagicMock()
    scraper = YouTubeScraper(connector=mock_connector)

    # 1. Channel video scrape
    mock_connector.scrape_actor.return_value = [
        {"url": "https://www.youtube.com/watch?v=vid1", "title": "Top Tech Review"}
    ]
    posts = scraper.fetch_posts("https://www.youtube.com/@techchannel", max_posts=10)
    assert len(posts) == 1
    assert posts[0]["title"] == "Top Tech Review"

    # 2. Comments scrape
    mock_connector.scrape_actor.return_value = [
        {"author": "Jane Doe", "text": "I need pricing for 10 licenses. Call me 555-123-4567"}
    ]
    comments = scraper.fetch_comments("https://www.youtube.com/watch?v=vid1", max_comments=20)
    assert len(comments) == 1
    assert comments[0]["author"] == "Jane Doe"


# ── 2. Scheduled Scans & Bulk URL Search ────────────────────────────────────

def test_scheduled_scans_lifecycle(mock_db):
    with patch("app.queue.service.get_sync_db", return_value=mock_db):
        # Create scan
        scan = create_scheduled_scan(
            mock_db,
            organization_id="org_alpha",
            user_id="user_1",
            url="https://www.facebook.com/acmebrand",
            frequency="daily",
            only_new_posts=True,
            max_posts=20,
        )
        assert scan["frequency"] == "daily"
        assert scan["platform"] == "facebook"
        assert scan["status"] == "active"
        assert scan["next_run_at"] is not None

        # List
        scans = list_scheduled_scans(mock_db, "org_alpha")
        assert len(scans) == 1

        # Trigger due scan
        future_time = utcnow() + timedelta(days=2)
        triggered = trigger_due_scheduled_scans(mock_db, now=future_time)
        assert len(triggered) == 1
        assert triggered[0].startswith("URL_REC_")


def test_bulk_url_search(mock_db):
    with patch("app.queue.service.get_sync_db", return_value=mock_db):
        urls = [
            "https://www.facebook.com/firstbrand",
            "https://www.facebook.com/firstbrand",  # Duplicate
            "https://www.instagram.com/secondbrand",
            "https://invalid-non-social.com/bad",
        ]

        result = process_bulk_urls(
            mock_db,
            urls=urls,
            organization_id="org_alpha",
            max_posts=10,
        )
        assert result["total_submitted"] == 4
        assert result["total_queued"] == 2  # 2 valid deduplicated
        assert result["total_invalid"] == 1


# ── 3. Lead Deduplication ───────────────────────────────────────────────────

def test_lead_deduplication(mock_db):
    org_id = "org_dedup_test"

    lead_1 = {
        "author_name": "Alice Smith",
        "phone": "+1 (555) 999-1111",
        "email": "alice@company.com",
        "lead_score": 70,
        "platform": "facebook",
        "intent": "purchase_inquiry",
    }

    # Ingest lead 1 (run 1)
    lead_id_1, is_dup_1, count_1 = deduplicate_lead(mock_db, lead_1, org_id, run_id="run_001")
    assert is_dup_1 is False
    assert count_1 == 1

    # Ingest same person from run 2 with slightly different format and higher score
    lead_2 = {
        "author_name": "Alice Smith",
        "phone": "555-999-1111",
        "email": "alice@company.com",
        "lead_score": 85,
        "platform": "instagram",
        "intent": "purchase_inquiry",
    }
    lead_id_2, is_dup_2, count_2 = deduplicate_lead(mock_db, lead_2, org_id, run_id="run_002")
    assert is_dup_2 is True
    assert count_2 == 2
    assert lead_id_2 == lead_id_1

    # Check consolidated database record
    doc = mock_db.ai_comments.find_one({"_id": ObjectId(lead_id_1)})
    assert doc["lead_score"] == 85  # Updated to higher score
    assert "run_001" in doc["source_runs"]
    assert "run_002" in doc["source_runs"]


# ── 4. Lead Assignment Rules & SLA Timers ───────────────────────────────────

def test_lead_assignment_rules_and_sla(mock_db):
    org_id = "org_assign_test"

    # Create Round-Robin rule for Hot Leads (4-hour SLA)
    rule = create_assignment_rule(
        mock_db,
        organization_id=org_id,
        name="Hot Leads Fast SLA",
        rule_type="round_robin",
        assignees=["sales_rep_1", "sales_rep_2"],
        criteria={"priority": "hot"},
        sla_hours=4,
    )
    assert rule["sla_hours"] == 4

    rules = list_assignment_rules(mock_db, org_id)
    assert len(rules) == 1

    # Insert mock hot lead
    lead_doc = {
        "_id": ObjectId(),
        "organization_id": org_id,
        "priority": "hot",
        "status": "new",
    }
    mock_db.ai_comments.insert_one(lead_doc)

    # Assign lead 1 -> rep 1
    meta1 = assign_lead(mock_db, str(lead_doc["_id"]), lead_doc, org_id)
    assert meta1["assigned_user_id"] == "sales_rep_1"
    assert meta1["sla_status"] == "on_track"

    # Check SLA breach detection
    now = utcnow()
    past_due = now + timedelta(hours=5)  # past 4h SLA
    sweep = check_sla_and_reminders(mock_db, now=past_due)
    assert sweep["sla_breaches_flagged"] == 1

    updated_lead = mock_db.ai_comments.find_one({"_id": lead_doc["_id"]})
    assert updated_lead["sla_status"] == "breached"


# ── 5. Outbound Webhooks & CRM Exporters ────────────────────────────────────

def test_outbound_webhooks_and_signing(mock_db):
    org_id = "org_whk_test"
    whk = create_outbound_webhook(
        mock_db,
        organization_id=org_id,
        target_url="https://crm.example.com/webhook",
        secret="super_secret_key_123",
        events=["lead.created"],
    )
    assert whk["url"] == "https://crm.example.com/webhook"

    listing = list_outbound_webhooks(mock_db, org_id)
    assert len(listing) == 1
    assert "super_secret_key_123" not in listing[0]["masked_secret"]

    # Signature verification
    payload = b'{"event":"lead.created","id":"123"}'
    sig = sign_payload(payload, "super_secret_key_123")
    assert sig.startswith("sha256=")

    # Verify signature calculation
    expected_hex = hmac.new(b"super_secret_key_123", payload, hashlib.sha256).hexdigest()
    assert sig == f"sha256={expected_hex}"


def test_crm_connectors_and_excel_export():
    sample_lead = {
        "id": "lead_123",
        "author_name": "Bob Vance",
        "phone": "+1 555-0199",
        "email": "bob@vancerefrigeration.com",
        "platform": "facebook",
        "intent": "purchase_inquiry",
        "priority": "hot",
        "lead_score": 90,
        "comment_text": "=CMD('calc')",  # Formula injection attempt
    }

    # HubSpot
    hs = HubSpotConnector().format_lead(sample_lead)
    assert hs["properties"]["firstname"] == "Bob"
    assert hs["properties"]["lastname"] == "Vance"
    assert hs["properties"]["email"] == "bob@vancerefrigeration.com"

    # Zoho
    zoho = ZohoCRMConnector().format_lead(sample_lead)
    assert zoho["First_Name"] == "Bob"
    assert zoho["Rating"] == "Hot"

    # Sheets format with formula sanitization
    rows = GoogleSheetsFormatter.format_rows([sample_lead])
    assert len(rows) == 2
    # Verify formula is escaped with single quote
    assert rows[1][8].startswith("'=")

    # Excel XML export
    xml_bytes = ExcelExportService.generate_spreadsheet_xml([sample_lead])
    assert b"Workbook" in xml_bytes
    assert b"Bob Vance" in xml_bytes


# ── 6. Public REST API & API Keys ───────────────────────────────────────────

def test_api_key_management_and_auth(mock_db):
    org_id = "org_api_test"
    raw_key, doc = create_api_key(
        mock_db,
        organization_id=org_id,
        name="Production Ingestion Key",
        scopes=["leads:read", "leads:write"],
    )

    assert raw_key.startswith("lai_live_")
    assert doc["name"] == "Production Ingestion Key"

    # List keys does not expose hash
    keys = list_api_keys(mock_db, org_id)
    assert len(keys) == 1
    assert "key_hash" not in keys[0]

    # Authenticate valid key and scope
    auth_data = authenticate_api_key(mock_db, raw_key, required_scope="leads:read")
    assert auth_data["organization_id"] == org_id

    # Missing required scope raises 403
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        authenticate_api_key(mock_db, raw_key, required_scope="admin:superuser")
    assert exc.value.status_code == 403

    # Revoke key
    revoked = revoke_api_key(mock_db, doc["key_id"], org_id)
    assert revoked is True

    # Revoked key authentication raises 401
    with pytest.raises(HTTPException) as exc_revoked:
        authenticate_api_key(mock_db, raw_key)
    assert exc_revoked.value.status_code == 401


# ── 7. Razorpay Billing & Verified Webhooks ─────────────────────────────────

@pytest.mark.asyncio
async def test_razorpay_billing_provider(monkeypatch):
    import httpx

    from app.billing import provider as prov
    from app.db.mongo import get_sync_db

    orders = []

    def razorpay_api(request):  # the Razorpay Orders API, mocked
        orders.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "order_Phase6", "amount": orders[-1]["amount"],
                                         "currency": orders[-1]["currency"], "status": "created"})
    monkeypatch.setattr(prov, "_HTTP_TRANSPORT", httpx.MockTransport(razorpay_api))

    provider = prov.RazorpayBillingProvider(
        key_id="rzp_test_key123",
        key_secret="rzp_secret_abc",
        webhook_secret="rzp_webhook_secret_xyz",
    )

    db = get_sync_db()
    sub_id = ObjectId()
    db.subscriptions.insert_one({"_id": sub_id, "status": "pending_payment"})

    checkout = await provider.create_checkout_session(
        subscription={"_id": sub_id},
        plan={"name": "Starter", "price_cents": 249900, "currency": "INR"},
        customer_email="buyer@test.com",
        success_url="/success",
        cancel_url="/cancel",
    )
    assert checkout["provider"] == "razorpay"
    assert checkout["currency"] == "INR"
    assert checkout["amount"] == 249900
    # a real order was created and is the checkout reference
    assert orders[0]["amount"] == 249900 and orders[0]["notes"]["subscription_id"] == str(sub_id)
    assert checkout["order_id"] == "order_Phase6"
    assert db.subscriptions.find_one({"_id": sub_id})["checkout_session_id"] == "order_Phase6"

    # Webhook payload verification
    event_payload = {
        "event": "payment.captured",
        "event_id": "evt_rzp_999",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_12345",
                    "order_id": checkout["order_id"],
                    "amount": 249900,
                    "currency": "INR",
                    "email": "buyer@test.com",
                    "notes": {"subscription_id": str(sub_id)},
                }
            }
        }
    }
    body_bytes = json.dumps(event_payload).encode("utf-8")
    expected_sig = hmac.new(b"rzp_webhook_secret_xyz", body_bytes, hashlib.sha256).hexdigest()

    verified = provider.verify_webhook(body_bytes, {"x-razorpay-signature": expected_sig})
    assert verified["type"] == "checkout.session.completed"
    assert verified["data"]["object"]["payment_status"] == "paid"
    assert verified["data"]["object"]["order_id"] == "order_Phase6"
    assert verified["data"]["object"]["payment_intent"] == "pay_12345"


# ── 8. Unit Economics & Margins ─────────────────────────────────────────────

def test_unit_economics_computation(mock_db):
    costs = compute_action_costs()
    assert costs["apify_cost_per_search_run_usd"] > 0
    assert costs["gemini_cost_per_1000_comments_usd"] > 0
    assert costs["blended_cost_per_search_run_usd"] < 0.05  # Highly efficient (< 5 cents per search)

    margins = compute_plan_margins()
    plan_margins = margins["plan_margins"]
    assert plan_margins["starter"]["max_margin_percent"] > 80.0
    assert plan_margins["pro"]["max_margin_percent"] > 80.0
    assert plan_margins["business"]["max_margin_percent"] > 80.0
