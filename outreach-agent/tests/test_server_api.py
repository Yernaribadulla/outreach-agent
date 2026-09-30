import json
import atexit
import os
import tempfile
import threading
import time
import unittest
from http.client import HTTPConnection
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from http.server import ThreadingHTTPServer

# Import the local server with a throwaway SQLite path so the test cannot touch
# the user's application database, even during module initialization.
_server_db = tempfile.TemporaryDirectory(prefix="outreach-server-import-")
_previous_db = os.environ.get("OUTREACH_DB_PATH")
os.environ["OUTREACH_DB_PATH"] = str(Path(_server_db.name) / "server.db")
from app import server
if _previous_db is None:
    os.environ.pop("OUTREACH_DB_PATH", None)
else:
    os.environ["OUTREACH_DB_PATH"] = _previous_db

def _cleanup_server_test_db():
    try:
        server.DB.close()
    except Exception:
        pass
    _server_db.cleanup()

atexit.register(_cleanup_server_test_db)


class DashboardApiTests(unittest.TestCase):
    def setUp(self):
        self.original_db = server.DB
        self.temp = tempfile.TemporaryDirectory(prefix="outreach-server-test-")
        server.DB = server.Database(Path(self.temp.name) / "test.db")
        server.DISCOVERY_RUNS.clear()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        server.DB.close()
        server.DB = self.original_db
        self.temp.cleanup()

    def request(self, method, path, body=None):
        connection = HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=3)
        raw = json.dumps(body).encode() if body is not None else None
        connection.request(method, path, raw, {"Content-Type": "application/json"} if raw else {})
        response = connection.getresponse()
        result = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, result

    def test_dashboard_counts_needs_review_qualification(self):
        server.DB.add_clinic({
            "name": "Dashboard Qualification Fixture",
            "city": "Test",
            "category": "dental",
            "profile": {"qualification": {"status": "NEEDS_REVIEW"}},
        })
        status, dashboard = self.request("GET", "/api/dashboard")
        self.assertEqual(status, 200)
        self.assertEqual(dashboard["needs_review"], 1)

    def test_discovery_api_reports_all_providers_unavailable_as_failure(self):
        from app.discovery.engine import DiscoveryRun
        unavailable = DiscoveryRun("dental", "стоматология Астана", source_status={"duckduckgo": "SOURCE_UNAVAILABLE", "web": "SOURCE_UNAVAILABLE"})
        with patch("app.server.discover", return_value=unavailable):
            status, started = self.request("POST", "/api/discovery/start", {"vertical": "dental", "city": "Астана", "target_count": 1})
            self.assertEqual(status, 202)
            final = None
            for _ in range(30):
                time.sleep(.03)
                _, final = self.request("GET", f"/api/discovery/{started['run_id']}")
                if final["status"] in {"FAILED", "COMPLETE"}: break
        self.assertEqual(final["status"], "FAILED")
        self.assertIn("недоступны", final["error"])
        self.assertEqual(final["source_status"]["duckduckgo"], "SOURCE_UNAVAILABLE")

    def test_invalid_discovery_vertical_is_a_client_error(self):
        status, result = self.request("POST", "/api/discovery/start", {"vertical": "unknown", "city": "Астана"})
        self.assertEqual(status, 400)
        self.assertIn("Проверьте", result["error"])

    def test_analysis_api_rejects_parallel_request_for_same_lead(self):
        clinic = server.DB.add_clinic({"name": "Analysis Lock Fixture", "city": "Test", "category": "detailers"})
        server.ANALYZING_LEADS.add(clinic)
        try:
            status, result = self.request("POST", f"/api/clinics/{clinic}/analyze", {})
        finally:
            server.ANALYZING_LEADS.discard(clinic)
        self.assertEqual(status, 409)
        self.assertIn("уже выполняется", result["error"])

    def test_review_queue_and_dashboard_exclude_historical_and_nonreviewable_drafts(self):
        draft_ids = {}
        for status in ("DRAFTED", "APPROVED", "SENT", "DO_NOT_CONTACT"):
            clinic = server.DB.add_clinic({"name": f"Queue {status} Fixture", "city": "Test", "category": "dental"})
            contact = server.DB.add_contact(clinic, f"{status.lower()}@example.invalid", "https://example.invalid/contact")
            draft = server.DB.add_draft(clinic, contact, {"subject": status, "body": status, "rationale": "Fixture", "source_observations": ["local"], "confidence": 1})
            if status != "DRAFTED": server.DB.update_draft(draft, status=status)
            draft_ids[status] = draft
            if status == "SENT": server.DB.log_send(draft, f"{status.lower()}@example.invalid", "historical", "SENT")
        sent_before = dict(server.DB.get_draft(draft_ids["SENT"]))
        status, queue = self.request("GET", "/api/queue")
        dashboard_status, dashboard = self.request("GET", "/api/dashboard")
        self.assertEqual((status, dashboard_status), (200, 200))
        self.assertEqual({item["id"] for item in queue["items"]}, {draft_ids["DRAFTED"], draft_ids["APPROVED"]})
        self.assertEqual((dashboard["drafted"], dashboard["approved"], dashboard["ready_to_review"], dashboard["sent"]), (2, 1, 1, 1))
        sent_after = dict(server.DB.get_draft(draft_ids["SENT"]))
        self.assertEqual(sent_after["status"], sent_before["status"])
        self.assertEqual(sent_after["updated_at"], sent_before["updated_at"])

    def test_legacy_discovery_contract_keeps_candidates_response(self):
        status, result = self.request("POST", "/api/discover", {"mock": True, "target_count": 1})
        self.assertEqual(status, 200)
        self.assertIn("candidates", result)
        self.assertIn("count", result)
        self.assertIn("sources", result)

    def test_nonmock_legacy_discover_persists_candidates_and_run(self):
        from app.discovery.engine import DiscoveryRun
        candidate = {"name": "Legacy API Persistence Fixture", "city": "Астана", "website": "https://legacy.example", "source_url": "https://source.example/legacy", "contacts": [], "evidence": []}
        discovery = DiscoveryRun("dental", "стоматология Астана", source_status={"fixture": "SUCCESS"}, candidates=[candidate])
        with patch("app.server.discover", return_value=discovery):
            status, result = self.request("POST", "/api/discover", {"vertical": "dental", "city": "Астана", "target_count": 1})
        self.assertEqual(status, 200)
        self.assertEqual(result["saved"], 1)
        self.assertEqual(result["status"], "COMPLETE")
        self.assertIn("run_id", result)
        db_path = server.DB.path
        server.DB.close()
        server.DB = server.Database(db_path)
        server.DISCOVERY_RUNS.clear()
        _, restored = self.request("GET", f"/api/discovery/{result['run_id']}")
        _, clinics = self.request("GET", "/api/clinics")
        self.assertEqual(restored["candidates"][0]["name"], candidate["name"])
        self.assertTrue(any(item["name"] == candidate["name"] for item in clinics["items"]))

    def test_real_send_api_is_blocked_even_for_approved_draft(self):
        clinic = server.DB.add_clinic({"name": "No Send Fixture", "city": "Test", "category": "dental"})
        contact = server.DB.add_contact(clinic, "hello@example.invalid", "https://example.invalid/contact")
        draft = server.DB.add_draft(clinic, contact, {"subject": "Test", "body": "No send", "rationale": "Test", "source_observations": ["local fixture"], "confidence": 1.0})
        server.DB.update_draft(draft, status="APPROVED")
        status, result = self.request("POST", "/api/send", {"id": draft, "simulate": False})
        self.assertEqual(status, 403)
        self.assertIn("SMTP отключён", result["error"])
        self.assertEqual(server.DB.list_send_logs(), [])

    def test_analysis_to_review_queue_and_approval_is_persisted_without_send(self):
        evidence = {
            "evidence_id": "ev-fixture-booking", "status": "CONFIRMED",
            "fact": "Online booking appointment link detected", "snippet": "Book an appointment",
            "source": "https://fixture.example/booking", "company_name": "Local E2E Fixture",
            "confidence": "HIGH", "observed_at": "2026-09-30T10:00:00+00:00",
        }
        profile = {
            "vertical": "dental",
            "contacts": [{"email": "desk@fixture.example", "source": "https://fixture.example/contact", "source_url": "https://fixture.example/contact"}],
            "evidence": [evidence],
            "website_audit": {"website_status": "WEBSITE_OK", "signals": {"booking": True}, "evidence": [evidence]},
        }
        clinic = server.DB.add_clinic({"name": "Local E2E Fixture", "city": "Test", "category": "dental", "website": "https://fixture.example", "profile": profile})
        server.DB.add_contact(clinic, "desk@fixture.example", "https://fixture.example/contact")
        analysis = {
            "company_summary": "Fixture company", "confidence": 0.91,
            "digital_state": {"online_booking": {"status": "CONFIRMED", "reason": "A booking link is present.", "evidence_ids": ["ev-fixture-booking"], "confidence": "HIGH"}},
            "priority": {"score": 75}, "why_this_lead": ["Public booking link verified."],
            "recommended_angle": "Discuss the booking journey.", "sales_brief": "A verified public booking link is available.",
        }
        fake_client = SimpleNamespace(model="qwen/qwen3-vl-8b", chat_opportunity=lambda payload: analysis)
        draft = {
            "subject": "A local review draft", "body": "Evidence-based note. https://yernaribadulla.github.io/Dentist_rus_commercial/",
            "rationale": "Based on the verified booking link.", "source_observations": ["ev-fixture-booking"], "confidence": 0.91,
        }
        with patch("app.server.lm_client", return_value=fake_client), patch("app.server.generate_draft", return_value=draft):
            status, outcome = server._analyze_clinic(clinic)
        self.assertEqual(status, 200)
        self.assertEqual(outcome["qualification"]["status"], "QUALIFIED")
        self.assertEqual(outcome["draft"]["status"], "DRAFTED")

        queue_status, queue = self.request("GET", "/api/queue")
        dashboard_status, dashboard = self.request("GET", "/api/dashboard")
        detail_status, detail = self.request("GET", f"/api/clinics/{clinic}")
        self.assertEqual((queue_status, dashboard_status, detail_status), (200, 200, 200))
        saved = next(item for item in queue["items"] if item["clinic_id"] == clinic)
        self.assertEqual(saved["status"], "DRAFTED")
        self.assertEqual(saved["subject"], draft["subject"])
        self.assertEqual(saved["plain_text_body"], draft["body"])
        self.assertIn("<div", saved["html_body"])
        self.assertEqual(saved["rationale"], draft["rationale"])
        self.assertEqual(saved["source_observations"], ["ev-fixture-booking"])
        self.assertEqual(detail["clinic"]["profile"]["qualification"]["status"], "QUALIFIED")
        self.assertEqual(detail["analysis"]["digital_state"]["online_booking"]["evidence_ids"], ["ev-fixture-booking"])
        self.assertEqual(dashboard["ready_to_review"], 1)

        with patch.object(server.SMTPProvider, "send") as smtp_send:
            approve_status, approved = self.request("POST", "/api/drafts/status", {"id": saved["id"], "status": "APPROVED"})
            self.assertEqual(approve_status, 200)
            self.assertTrue(approved["ok"])
            with patch.object(server.DB, "update_draft", wraps=server.DB.update_draft) as update_draft:
                approve_status, approved = self.request("POST", "/api/drafts/status", {"id": saved["id"], "status": "APPROVED"})
                self.assertEqual(approve_status, 200)
                self.assertTrue(approved["ok"])
                update_draft.assert_not_called()
            persisted = server.DB.get_draft(saved["id"])
            self.assertEqual(persisted["status"], "APPROVED")
            self.assertEqual(server.DB.list_send_logs(), [])
            smtp_send.assert_not_called()

    def test_discovery_candidates_survive_database_reopen_and_api_refresh(self):
        from app.discovery.engine import DiscoveryRun
        candidate = {
            "name": "Persistent Discovery Fixture", "city": "Астана", "website": "https://persistent.example",
            "phone": "+7 700 000", "source_url": "https://source.example/business", "sources": ["local-fixture"],
            "contacts": [{"email": "hello@persistent.example", "source_url": "https://persistent.example/contact"}],
            "evidence": [], "website_audit": {"website_status": "UNKNOWN", "evidence": []},
        }
        run = DiscoveryRun("dental", "стоматология Астана", source_status={"fixture": "SUCCESS"}, candidates=[candidate])
        db_path = server.DB.path
        with patch("app.server.discover", return_value=run):
            status, started = self.request("POST", "/api/discovery/start", {"vertical": "dental", "city": "Астана", "target_count": 1})
            self.assertEqual(status, 202)
            final = None
            for _ in range(60):
                time.sleep(.02)
                _, final = self.request("GET", f"/api/discovery/{started['run_id']}")
                if final["status"] in {"COMPLETE", "FAILED"}:
                    break
        self.assertEqual(final["status"], "COMPLETE")
        self.assertEqual(final["count"], 1)
        server.DB.close()
        server.DB = server.Database(db_path)
        server.DISCOVERY_RUNS.clear()
        _, clinics = self.request("GET", "/api/clinics")
        _, dashboard = self.request("GET", "/api/dashboard")
        run_status, restored_run = self.request("GET", f"/api/discovery/{started['run_id']}")
        _, latest = self.request("GET", "/api/discovery/latest")
        persisted = next(item for item in clinics["items"] if item["name"] == candidate["name"])
        self.assertEqual(persisted["category"], "dental")
        self.assertEqual(persisted["emails"], "hello@persistent.example")
        self.assertEqual(dashboard["clinics"], 1)
        self.assertEqual(run_status, 200)
        self.assertEqual(restored_run["status"], "COMPLETE")
        self.assertEqual(restored_run["source_status"], {"fixture": "SUCCESS"})
        self.assertEqual(restored_run["candidates"][0]["name"], candidate["name"])
        self.assertEqual(latest["run"]["run_id"], started["run_id"])
        saved_run = next(item for item in server.DB.list_discovery_runs() if item["run_id"] == started["run_id"])
        self.assertEqual(saved_run["status"], "COMPLETE")
        self.assertEqual(saved_run["discovered"], 1)

    def test_existing_read_api_contracts_remain_available(self):
        class HealthClient:
            base_url = "http://127.0.0.1:1234/v1"
            model = "qwen/qwen3-vl-8b"
            def health(self): return {"data": [{"id": self.model}]}
        with patch("app.server.lm_client", return_value=HealthClient()):
            for path in ("/api/health", "/api/dashboard", "/api/clinics", "/api/queue", "/api/activity", "/api/settings"):
                status, payload = self.request("GET", path)
                self.assertEqual(status, 200, path)
                self.assertIsInstance(payload, dict)

    def test_latest_dashboard_discovery_ignores_autonomous_history(self):
        server.DB.create_autonomous_run("auto-history-fixture", "dental", 1, discovered=1)
        status, result = self.request("GET", "/api/discovery/latest")
        self.assertEqual(status, 200)
        self.assertIsNone(result["run"])


if __name__ == "__main__":
    unittest.main()
