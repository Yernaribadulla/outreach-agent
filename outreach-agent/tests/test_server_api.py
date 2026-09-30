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

    def approved_draft(self, name="Send fixture", email="clinic@example.test"):
        clinic = server.DB.add_clinic({"name": name, "city": "Test", "category": "dental", "profile": {"vertical": "dental"}})
        contact = server.DB.add_contact(clinic, email, "https://example.test/contact")
        draft = server.DB.add_draft(clinic, contact, {"subject": f"For {name}", "body": "Local test", "rationale": "Fixture", "source_observations": ["Fixture evidence"], "confidence": 1.0})
        server.DB.update_draft(draft, status="APPROVED")
        server.DB.update_clinic_status(clinic, "READY_TO_SEND")
        return clinic, contact, draft

    def wait_for_job(self, route, job_id, terminal, timeout=4):
        result = None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            _, payload = self.request("GET", f"{route}/{job_id}")
            result = payload["job"]
            if result["status"] in terminal: return result
            time.sleep(.025)
        self.fail(f"Job did not reach {terminal}; last state: {result}")

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

    def test_health_vertical_dashboard_and_batch_contracts(self):
        with patch("app.server.lm_client") as client_factory:
            client_factory.return_value.health.return_value = {"data": [{"id": "qwen/qwen3-vl-8b"}]}
            health_status, health = self.request("GET", "/api/health")
        self.assertEqual(health_status, 200)
        self.assertTrue(health["lm_studio"])
        self.assertTrue(health["capabilities"]["batch_analysis"])
        self.assertTrue(health["capabilities"]["send_batch"])

        vertical_status, verticals = self.request("GET", "/api/verticals")
        self.assertEqual(vertical_status, 200)
        vertical_map = {item["key"]: item["label"] for item in verticals["items"]}
        self.assertEqual(vertical_map["dental"], "Dental")
        self.assertEqual(vertical_map["detailing"], "Detailing")

        for path in ("/api/dashboard", "/api/batches/latest", "/api/send-batches/latest", "/api/discovery/latest"):
            with self.subTest(path=path):
                status, _ = self.request("GET", path)
                self.assertEqual(status, 200)
        status, eligibility = self.request("GET", "/api/batches/eligible?vertical=detailing")
        self.assertEqual(status, 200)
        self.assertEqual(eligibility["vertical"], "detailing")

    def test_empty_vertical_configuration_is_explicit_and_ui_disables_discovery(self):
        with patch.dict(server.VERTICALS, {}, clear=True):
            status, payload = self.request("GET", "/api/verticals")
            self.assertEqual(status, 200)
            self.assertEqual(payload["items"], [])
            status, result = self.request("POST", "/api/discovery/start", {"vertical": "dental", "city": "Astana", "target_count": 10})
            self.assertEqual(status, 400)
            self.assertIn("Проверьте", result["error"])
        ui = (server.UI.parent / "review.js").read_text(encoding="utf-8")
        self.assertIn("No verticals configured", ui)
        self.assertIn("${hasVerticals ? '' : 'disabled'}", ui)

    def test_discovery_start_passes_vertical_city_and_integer_limit_to_worker(self):
        captured = []
        finished = threading.Event()

        def capture_worker(run_id, vertical, city, limit):
            captured.append((run_id, vertical, city, limit))
            finished.set()

        with patch("app.server._discovery_worker", side_effect=capture_worker):
            status, response = self.request("POST", "/api/discovery/start", {"vertical": "detailing", "city": "Astana", "target_count": 10})
            self.assertEqual(status, 202)
            self.assertTrue(finished.wait(1))
        self.assertEqual(captured[0][1:], ("detailing", "Astana", 10))
        run_status, run = self.request("GET", f"/api/discovery/{response['run_id']}")
        self.assertEqual(run_status, 200)
        self.assertEqual((run["vertical"], run["city"], run["target_count"]), ("detailing", "Astana", 10))

    def test_discovery_start_defaults_to_configured_dental_vertical(self):
        captured = []
        finished = threading.Event()

        def capture_worker(run_id, vertical, city, limit):
            captured.append((vertical, city, limit))
            finished.set()

        with patch("app.server._discovery_worker", side_effect=capture_worker):
            status, _ = self.request("POST", "/api/discovery/start", {"city": "Astana", "target_count": 10})
            self.assertEqual(status, 202)
            self.assertTrue(finished.wait(1))
        self.assertEqual(captured, [("dental", "Astana", 10)])

    def test_discovery_limit_rejects_nonpositive_or_fractional_values(self):
        for limit in (0, -1, 1.5, True):
            with self.subTest(limit=limit):
                status, result = self.request("POST", "/api/discovery/start", {"vertical": "dental", "city": "Astana", "target_count": limit})
                self.assertEqual(status, 400)
                self.assertIn("Проверьте", result["error"])

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
        self.assertIn("явное подтверждение", result["error"])
        self.assertEqual(server.DB.list_send_logs(), [])

    def test_database_startup_preserves_sent_status_and_timestamp(self):
        clinic, contact, draft = self.approved_draft("Startup SENT fixture", "startup@example.test")
        server.DB.update_draft(draft, status="SENT")
        historic_timestamp = "2024-01-02T03:04:05+00:00"
        server.DB.conn.execute("UPDATE drafts SET updated_at=? WHERE id=?", (historic_timestamp, draft)); server.DB.conn.commit()
        server.DB.log_send(draft, "startup@example.test", "historical", "SENT")
        path = server.DB.path
        server.DB.close(); server.DB = server.Database(path)
        saved = server.DB.get_draft(draft)
        self.assertEqual(saved["status"], "SENT")
        self.assertEqual(saved["updated_at"], historic_timestamp)

    def test_batch_analysis_persists_draft_and_continues_after_one_failure(self):
        evidence = {"evidence_id": "ev-batch-booking", "status": "CONFIRMED", "fact": "Online booking action link is present", "snippet": "Book an appointment", "source": "https://batch.example/booking", "company_name": "Batch success fixture", "confidence": "HIGH", "observed_at": "2026-09-30T10:00:00+00:00"}
        profile = {"vertical": "dental", "contacts": [{"email": "desk@batch.example", "source": "https://batch.example/contact", "source_url": "https://batch.example/contact"}], "evidence": [evidence], "website_audit": {"website_status": "WEBSITE_OK", "signals": {"booking": True}, "evidence": [evidence]}}
        success_id = server.DB.add_clinic({"name": "Batch success fixture", "city": "Test", "category": "dental", "website": "https://batch.example", "profile": profile})
        server.DB.add_contact(success_id, "desk@batch.example", "https://batch.example/contact")
        failure_id = server.DB.add_clinic({"name": "Batch failure fixture", "city": "Test", "category": "dental", "profile": {"vertical": "dental"}})
        analysis = {"company_summary": "Verified booking flow", "confidence": 0.91, "digital_state": {"online_booking": {"status": "CONFIRMED", "reason": "A booking action is present.", "evidence_ids": ["ev-batch-booking"], "confidence": "HIGH"}}, "priority": {"score": 75}, "why_this_lead": ["A public booking action was verified."], "recommended_angle": "Discuss the online booking journey.", "sales_brief": "A verified booking action is present."}
        fake_client = SimpleNamespace(model="qwen/qwen3-vl-8b", chat_opportunity=lambda payload: analysis)
        draft = {"subject": "Booking journey", "body": "Evidence-bound note. https://yernaribadulla.github.io/Dentist_rus_commercial/", "rationale": "Based on a verified booking action.", "source_observations": ["ignored model observation"], "confidence": 0.91}

        def analyze(clinic_id, **kwargs):
            if clinic_id == failure_id: raise RuntimeError("fixture error; continue")
            return original_analyze(clinic_id, **kwargs)

        original_analyze = server._analyze_clinic
        with patch("app.server.lm_client", return_value=fake_client), patch("app.server.generate_draft", return_value=draft), patch("app.server._analyze_clinic", side_effect=analyze):
            status, started = self.request("POST", "/api/batches/start", {"vertical": "dental", "requested_count": 2})
            self.assertEqual(status, 202)
            final = self.wait_for_job("/api/batches", started["batch_id"], {"COMPLETED"})

        self.assertEqual(final["requested_count"], 2)
        self.assertEqual(final["queued_count"], 2)
        self.assertEqual(final["processed_count"], 2)
        self.assertEqual(final["failed"], 1)
        self.assertEqual(final["analyzed_count"], 1)
        self.assertEqual(final["qualified_count"], 1)
        self.assertEqual(final["letters_generated"], 1)
        _, queue = self.request("GET", "/api/queue")
        saved = next(item for item in queue["items"] if item["clinic_id"] == success_id)
        self.assertEqual(saved["workflow_status"], "LETTER_DONE")
        self.assertEqual(saved["analysis"]["recommended_angle"], analysis["recommended_angle"])
        self.assertEqual(saved["subject"], draft["subject"])
        self.assertTrue(saved["html_body"])
        self.assertTrue(saved["rationale"])
        self.assertTrue(saved["evidence"])
        self.assertEqual(next(item for item in final["items"] if item["clinic_id"] == failure_id)["status"], "FAILED")

        db_path = server.DB.path
        server.DB.close(); server.DB = server.Database(db_path)
        persisted = server.DB.get_batch_job(started["batch_id"])
        self.assertEqual(persisted["status"], "COMPLETED")
        self.assertEqual(persisted["letters_generated"], 1)
        self.assertIsNotNone(server.DB.active_draft(success_id))

    def test_batch_resume_retries_only_pending_items(self):
        clinics = [server.DB.add_clinic({"name": f"Resume fixture {i}", "city": "Test", "category": "dental", "profile": {"vertical": "dental"}}) for i in range(2)]
        batch_id = "batch-resume-fixture"
        server.DB.create_batch_job(batch_id, "dental", 2, [{"id": value, "name": f"Resume fixture {i}"} for i, value in enumerate(clinics)])
        server.DB.update_batch_item(batch_id, clinics[0], status="COMPLETED", stage="COMPLETED", finished_at="2026-09-30T10:00:00+00:00")
        server.DB.conn.execute("UPDATE batch_jobs SET status='INTERRUPTED' WHERE batch_id=?", (batch_id,)); server.DB.conn.commit()
        processed = []

        def cached_analyze(clinic_id, **kwargs):
            processed.append(clinic_id)
            return 200, {"qualification": {"status": "NEEDS_REVIEW"}, "draft": None}

        with patch("app.server._analyze_clinic", side_effect=cached_analyze):
            status, result = self.request("POST", f"/api/batches/{batch_id}/resume", {})
            self.assertEqual(status, 202)
            final = self.wait_for_job("/api/batches", batch_id, {"COMPLETED"})
        self.assertEqual(result["status"], "RUNNING")
        self.assertEqual(processed, [clinics[1]])
        self.assertEqual(final["processed_count"], 2)

    def test_repeated_analysis_is_explicit_and_history_is_append_only(self):
        repeat_profile = {"vertical": "dental", "qualification": {"status": "NEEDS_REVIEW", "reasons": ["fixture"]}, "website_audit": {"website_status": "WEBSITE_OK", "signals": {}}}
        clinic = server.DB.add_clinic({"name": "Repeat fixture", "city": "Test", "category": "dental", "website": "https://repeat.example", "profile": repeat_profile})
        old = {"confidence": 0.2, "digital_state": {}, "recommended_angle": "old"}
        server.DB.add_analysis(clinic, "qwen/qwen3-vl-8b", {}, old)
        server.DB.update_clinic_status(clinic, "NEEDS_REVIEW", repeat_profile)
        fresh = {"confidence": 0.2, "digital_state": {}, "recommended_angle": "new"}
        fake_client = SimpleNamespace(model="qwen/qwen3-vl-8b", chat_opportunity=lambda payload: fresh)
        with patch("app.server.lm_client", return_value=fake_client):
            status, cached = self.request("POST", f"/api/clinics/{clinic}/analyze", {"repeat": False})
            self.assertEqual(status, 200)
            self.assertEqual(cached["analysis"]["recommended_angle"], "old")
            self.assertEqual(server.DB.conn.execute("SELECT COUNT(*) FROM analyses WHERE clinic_id=?", (clinic,)).fetchone()[0], 1)
            status, repeated = self.request("POST", f"/api/clinics/{clinic}/analyze", {"repeat": True})
        self.assertEqual(status, 200)
        self.assertEqual(repeated["analysis"]["recommended_angle"], "new")
        self.assertEqual(server.DB.conn.execute("SELECT COUNT(*) FROM analyses WHERE clinic_id=?", (clinic,)).fetchone()[0], 2)

    def test_send_preview_requires_confirmation_and_simulated_batch_never_uses_smtp(self):
        self.approved_draft("Explicit sim fixture", "sim@example.test")
        with patch.object(server.SMTPProvider, "send") as smtp_send:
            status, preview = self.request("POST", "/api/send-batches/preview", {"vertical": "dental", "count": 10, "mode": "SIMULATED_SEND", "min_delay_seconds": 0, "max_delay_seconds": 0})
            self.assertEqual(status, 200)
            self.assertEqual(preview["ready_count"], 1)
            self.assertEqual(preview["planned_count"], 1)
            self.assertEqual(server.DB.list_send_logs(), [])
            status, started = self.request("POST", f"/api/send-batches/{preview['send_id']}/confirm", {})
            self.assertEqual(status, 202)
            final = self.wait_for_job("/api/send-batches", started["send_id"], {"COMPLETED"})
            smtp_send.assert_not_called()
        self.assertEqual(final["simulated_count"], 1)
        self.assertEqual(final["sent_count"], 0)
        self.assertEqual(server.DB.list_send_logs()[0]["status"], "SIMULATED_SENT")
        self.assertEqual(server.DB.get_draft(final["items"][0]["draft_id"])["status"], "SIMULATED_SENT")

    def test_send_ready_filter_excludes_suppressed_and_nonpublic_contacts(self):
        good = self.approved_draft("Ready fixture", "ready@example.test")
        suppressed = self.approved_draft("Suppressed fixture", "stop@example.test")
        private = self.approved_draft("Private fixture", "private@example.test")
        server.DB.suppress("stop@example.test", "fixture")
        server.DB.conn.execute("UPDATE contacts SET is_public=0 WHERE id=?", (private[1],)); server.DB.conn.commit()
        ready = server.DB.ready_to_send("dental")
        self.assertEqual([item["id"] for item in ready], [good[2]])

    def test_send_preview_obeys_user_count_batch_cap_and_daily_limit(self):
        for index in range(3): self.approved_draft(f"Send cap {index}", f"cap{index}@example.test")
        with patch.dict(os.environ, {"MAX_SENDS_PER_BATCH": "1", "SMTP_DAILY_LIMIT": "2"}, clear=False):
            status, preview = self.request("POST", "/api/send-batches/preview", {"vertical": "dental", "count": 10, "mode": "SIMULATED_SEND", "min_delay_seconds": 0, "max_delay_seconds": 0})
        self.assertEqual(status, 200)
        self.assertEqual(preview["requested_count"], 10)
        self.assertEqual(preview["max_per_batch"], 1)
        self.assertEqual(preview["daily_remaining"], 2)
        self.assertEqual(preview["planned_count"], 1)

    def test_send_delay_is_randomized_within_selected_range(self):
        self.approved_draft("Delay first", "delay1@example.test")
        self.approved_draft("Delay second", "delay2@example.test")
        items = server.DB.ready_to_send()
        server.DB.create_send_job("send-delay-fixture", "dental", "SIMULATED_SEND", 2, 10, 20, 50, 50, items)
        server.DB.confirm_send_job("send-delay-fixture")
        with patch("app.server.random.randint", return_value=17) as randomized, patch("app.server.time.sleep") as sleep:
            server._send_worker("send-delay-fixture")
        randomized.assert_called_once_with(10, 20)
        sleep.assert_called_once_with(17)
        self.assertEqual(server.DB.get_send_job("send-delay-fixture")["status"], "COMPLETED")

    def test_provider_rejection_stops_batch_and_rate_limit_sets_cooldown(self):
        self.approved_draft("Provider reject first", "reject1@example.test")
        self.approved_draft("Provider reject second", "reject2@example.test")
        items = server.DB.ready_to_send()
        server.DB.create_send_job("send-reject-fixture", "dental", "REAL_SMTP", 2, 30, 30, 50, 50, items)
        server.DB.confirm_send_job("send-reject-fixture")
        env = {"SMTP_ENABLED": "true", "SMTP_HOST": "smtp.example.test", "SMTP_USERNAME": "u", "SMTP_PASSWORD": "p", "SMTP_FROM": "sender@example.test"}
        rejection = __import__("smtplib").SMTPResponseException(421, b"rate limit exceeded")
        with patch("app.server.load_env", return_value=env), patch.object(server.SMTPProvider, "check_available"), patch.object(server.SMTPProvider, "send", side_effect=rejection):
            server._send_worker("send-reject-fixture")
        job = server.DB.get_send_job("send-reject-fixture")
        self.assertEqual(job["status"], "STOPPED_PROVIDER_LIMIT")
        self.assertEqual(job["failed_count"], 1)
        self.assertEqual(job["items"][0]["status"], "FAILED")
        self.assertEqual(job["items"][1]["status"], "PENDING")
        self.assertIsNotNone(server.DB.provider_cooldown())

    def test_real_batch_preview_is_refused_when_smtp_disabled(self):
        self.approved_draft("Disabled SMTP fixture", "disabled@example.test")
        with patch("app.server.load_env", return_value={"SMTP_ENABLED": "false"}):
            status, result = self.request("POST", "/api/send-batches/preview", {"vertical": "dental", "count": 1, "mode": "REAL_SMTP", "min_delay_seconds": 45, "max_delay_seconds": 120})
        self.assertEqual(status, 403)
        self.assertIn("SMTP отключён", result["error"])
        self.assertEqual(server.DB.conn.execute("SELECT COUNT(*) FROM send_logs").fetchone()[0], 0)

    def test_send_lock_blocks_second_batch_and_real_endpoint_is_always_blocked(self):
        clinic, contact, draft = self.approved_draft("Send lock fixture", "lock@example.test")
        server.DB.create_send_job("send-lock-fixture", "dental", "SIMULATED_SEND", 1, 0, 0, 50, 50, [dict(server.DB.ready_to_send()[0])])
        server.DB.confirm_send_job("send-lock-fixture")
        status, result = self.request("POST", "/api/send-batches/preview", {"vertical": "dental", "count": 1, "mode": "SIMULATED_SEND", "min_delay_seconds": 0, "max_delay_seconds": 0})
        self.assertEqual(status, 409)
        self.assertEqual(result["code"], "SEND_ALREADY_RUNNING")
        status, result = self.request("POST", "/api/send", {"id": draft, "simulate": False})
        self.assertEqual(status, 403)
        self.assertEqual(server.DB.list_send_logs(), [])

    def test_restart_marks_current_send_uncertain_and_resume_sends_only_pending(self):
        first = self.approved_draft("Interrupted current", "current@example.test")
        second = self.approved_draft("Interrupted pending", "pending@example.test")
        items = [next(row for row in server.DB.ready_to_send() if row["id"] == first[2]), next(row for row in server.DB.ready_to_send() if row["id"] == second[2])]
        server.DB.create_send_job("send-resume-fixture", "dental", "SIMULATED_SEND", 2, 0, 0, 50, 50, items)
        server.DB.confirm_send_job("send-resume-fixture")
        reserved, reason = server.DB.reserve_send_item("send-resume-fixture", first[2])
        self.assertIsNone(reason); self.assertIsNotNone(reserved)
        db_path = server.DB.path
        server.DB.close(); server.DB = server.Database(db_path); server.DB.recover_interrupted_jobs()
        interrupted = server.DB.get_send_job("send-resume-fixture")
        self.assertEqual(interrupted["status"], "SEND_INTERRUPTED")
        self.assertEqual(interrupted["items"][0]["status"], "UNCERTAIN")
        self.assertEqual(interrupted["items"][1]["status"], "PENDING")
        self.assertEqual(server.DB.get_draft(first[2])["status"], "SEND_INTERRUPTED")
        self.assertEqual(server.DB.get_draft(second[2])["status"], "APPROVED")
        status, result = self.request("POST", "/api/send-batches/send-resume-fixture/resume", {})
        self.assertEqual(status, 202)
        final = self.wait_for_job("/api/send-batches", "send-resume-fixture", {"COMPLETED"})
        self.assertEqual(final["simulated_count"], 1)
        self.assertEqual(final["items"][0]["status"], "UNCERTAIN")
        self.assertEqual(final["items"][1]["status"], "SIMULATED_SENT")

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
