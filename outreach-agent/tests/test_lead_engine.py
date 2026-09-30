import tempfile
import json
import io
from pathlib import Path
import unittest
from unittest.mock import patch

from app.discovery.engine import discover
from app.discovery.providers import PublicSourceProvider, _usable_result
from app.discovery.resolution import contact_conflicts, match_confidence, resolve_entities
from app.website_audit import audit_website
from app.verticals import get_vertical
from app.discovery.providers import SourceUnavailable, _get
from app.analysis.lm_studio import LMStudioClient, LMStudioError
from app.analysis.context import LLM_CONTEXT_MAX_CHARS, LLM_CONTEXT_MAX_EVIDENCE, build_llm_context, deduplicate_evidence
from app.discovery.gemini_import import import_gemini_file
from app.autonomous import validate_analysis, EvidenceValidationError
from app.qualification import qualify_lead
from app.verticals import build_search_query


class LeadEngineTests(unittest.TestCase):
    def test_evidence_dedup_uses_claim_url_and_normalized_text_without_losing_other_sources(self):
        evidence = [
            {"evidence_id": "old-1", "type": "booking", "fact": "Online booking detected", "snippet": "Book now", "source": "https://clinic.example/booking#top", "status": "CONFIRMED", "observed_at": "yesterday"},
            {"evidence_id": "new-1", "type": "booking", "fact": "Online booking detected", "snippet": "  Book   now ", "source": "https://clinic.example/booking/", "status": "CONFIRMED", "observed_at": "today"},
            {"evidence_id": "dir-1", "type": "booking", "fact": "Online booking detected", "snippet": "Book now", "source": "https://directory.example/clinic", "status": "CONFIRMED"},
        ]
        result = deduplicate_evidence(evidence)
        self.assertEqual(len(result), 2)
        self.assertEqual({item["source"] for item in result}, {"https://clinic.example/booking#top", "https://directory.example/clinic"})

    def test_llm_context_is_bounded_deduplicated_and_excludes_raw_audit(self):
        repeated = {
            "evidence_id": "ev-repeat", "type": "booking", "fact": "Booking action detected", "snippet": "Book now",
            "source": "https://clinic.example/booking", "status": "CONFIRMED", "confidence": "HIGH",
        }
        payload = {
            "company": {"name": "Synthetic clinic", "city": "Antalya", "website": "https://clinic.example"},
            "website_audit": {"website_status": "WEBSITE_OK", "http_status": 200, "signals": {"booking": True}, "evidence": [repeated], "raw_html": "OVERSIZED_RAW_HTML_MARKER " + "x" * 20000},
            "evidence": [repeated, {**repeated, "evidence_id": "ev-copy", "observed_at": "later"}, *[
                {"evidence_id": f"ev-{i}", "type": "signal", "fact": f"Signal {i}", "snippet": f"signal {i} " + "details " * 80, "source": f"https://clinic.example/{i}", "status": "CONFIRMED"}
                for i in range(10)
            ]],
            "contacts": [{"email": f"desk{i}@clinic.example", "source_url": f"https://clinic.example/contact/{i}"} for i in range(8)],
        }
        context = build_llm_context(payload, ("online_booking",))
        serialized = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
        self.assertLessEqual(len(serialized), LLM_CONTEXT_MAX_CHARS)
        self.assertLessEqual(len(context["key_evidence"]), LLM_CONTEXT_MAX_EVIDENCE)
        self.assertLessEqual(len(context["contacts"]), 3)
        self.assertNotIn("OVERSIZED_RAW_HTML_MARKER", serialized)
        self.assertNotIn("website_audit", serialized)
        self.assertEqual(sum(item["claim"] == "booking" for item in context["key_evidence"]), 1)
        self.assertEqual(build_llm_context(context)["key_evidence"], context["key_evidence"])

    def test_opportunity_request_uses_compact_context_and_logs_input_diagnostics(self):
        client = LMStudioClient("http://127.0.0.1:1234/v1", "qwen/qwen3-vl-8b")
        captured = {}
        opportunity = {
            "company_summary": "ok", "priority": {"score": 1, "website_opportunity": False, "booking_opportunity": False, "crm_opportunity": False, "ai_opportunity": False, "automation_opportunity": False},
            "why_this_lead": ["Review public evidence."], "recommended_angle": "review", "sales_brief": "review",
            "confidence": 0.72, "opportunities": [],
        }
        response = {"choices": [{"message": {"content": json.dumps(opportunity, ensure_ascii=False)}}]}
        def fake_request(body):
            captured.update(body)
            return response
        client._request = fake_request
        output = io.StringIO()
        payload = {"company": {"name": "Fixture", "website": "https://fixture.example"}, "website_audit": {"raw_html": "RAW_AUDIT_MARKER" + "x" * 10000, "signals": {}}, "evidence": [{"evidence_id": "ev-1", "type": "booking", "fact": "Booking action", "snippet": "Book now", "source": "https://fixture.example/booking", "status": "CONFIRMED"}]}
        with patch("sys.stdout", output):
            result = client.chat_opportunity(payload)
        request_text = "\n".join(message["content"] for message in captured["messages"])
        self.assertIn("confidence", result)
        self.assertIn("confidence — число от 0 до 1", request_text)
        self.assertEqual(captured["response_format"]["type"], "json_schema")
        self.assertNotIn("RAW_AUDIT_MARKER", request_text)
        self.assertLessEqual(len(request_text.split("COMPACT RESEARCH OBJECT:\n", 1)[-1]), LLM_CONTEXT_MAX_CHARS)
        sent_context = json.loads(request_text.split("COMPACT RESEARCH OBJECT:\n", 1)[-1])
        self.assertEqual(sent_context["key_evidence"][0]["text"], "Book now")
        self.assertIn("estimated_tokens=", output.getvalue())
        self.assertIn("elapsed=", output.getvalue())
        self.assertIn("qwen/qwen3-vl-8b", output.getvalue())

    def test_vertical_configuration_is_not_dental_hardcoded(self):
        self.assertEqual(get_vertical("dental").key, "dental")
        self.assertIn("before_after", get_vertical("detailing").opportunity_signals)
        query = build_search_query("detailing", "Екатеринбург")
        self.assertIn("детейлинг", query)
        self.assertNotIn("стомат", query.lower())
        self.assertNotIn("dental", query.lower())

    def test_autonomous_query_uses_selected_vertical(self):
        from app import autonomous
        from app.discovery.engine import DiscoveryRun
        captured = {}
        def fake_discover(providers, vertical, query, target):
            captured["query"] = query
            return DiscoveryRun(vertical, query)
        with tempfile.TemporaryDirectory() as folder, patch("app.autonomous.discover", side_effect=fake_discover), patch("app.autonomous.LMStudioClient.health", side_effect=RuntimeError("offline")):
            autonomous.run_autonomous("detailing", "Алматы", 3, str(Path(folder) / "test.db"))
        self.assertIn("детейлинг", captured["query"])
        self.assertNotIn("стомат", captured["query"].lower())

    def test_ai_success_does_not_qualify_without_requirements(self):
        result = qualify_lead({"category": "dental", "contacts": [], "evidence": []}, {"confidence": 0.99, "digital_state": {}}, "dental")
        self.assertEqual(result["status"], "NEEDS_REVIEW")
        self.assertFalse(result["factors"]["evidence_quality"])
        self.assertFalse(result["factors"]["contactability"])
        self.assertFalse(result["factors"]["opportunity_fit"])
        self.assertTrue(any("evidence" in reason.lower() for reason in result["reasons"]))
        self.assertTrue(any("контакт" in reason.lower() for reason in result["reasons"]))

    def test_successful_analysis_missing_confidence_is_needs_review_not_ai_error(self):
        result = qualify_lead({"category": "dental", "contacts": [], "evidence": []}, {"digital_state": {}}, "dental")
        self.assertEqual(result["status"], "NEEDS_REVIEW")
        self.assertFalse(result["factors"]["confidence_available"])
        self.assertTrue(any("confidence" in reason.lower() for reason in result["reasons"]))

    def test_resolution_requires_objective_signal(self):
        a = {"name": "ABC Dental", "city": "Astana", "phone": "+7 700 000"}
        b = {"name": "ABC Dental", "city": "Astana", "phone": "+7 700 000"}
        c = {"name": "ABC Dental", "city": "Almaty", "phone": "+7 701 111"}
        self.assertGreaterEqual(match_confidence(a, b), .7)
        self.assertEqual(match_confidence(a, c), 0.0)
        self.assertEqual(len(resolve_entities([a, b, c])), 2)

    def test_provenance_and_conflict_are_preserved(self):
        records = [{"name": "A", "phone": "+7 1", "source_url": "2gis"}, {"name": "A", "phone": "+7 2", "source_url": "yandex"}]
        conflict = contact_conflicts(records)
        self.assertEqual(conflict[0]["type"], "CONTACT_CONFLICT")
        self.assertEqual(set(conflict[0]["values"]), {"+7 1", "+7 2"})
        same_company = [{"name": "A", "city": "Astana", "address": "A 1", "website": "https://a.example", "phone": "+7 1", "source_url": "https://2gis.example/a"}, {"name": "A", "city": "Astana", "address": "A 1", "website": "https://a.example", "phone": "+7 2", "source_url": "https://yandex.example/a"}]
        entity = resolve_entities(same_company)
        self.assertEqual(len(entity), 1)
        self.assertEqual(set(contact_conflicts(entity[0]["records"])[0]["values"]), {"+7 1", "+7 2"})

    def test_resolution_matches_same_business_and_keeps_distinct_locations_apart(self):
        same_business = [
            {"name": "Dental ABC", "city": "Almaty", "phone": "+7 777 123", "website": "https://abc.kz"},
            {"name": "ABC Dental", "city": "Almaty", "phone": "+7 777 123", "website": "https://abc.kz"},
        ]
        self.assertEqual(len(resolve_entities(same_business)), 1)
        distinct_locations = [
            {"name": "Dental ABC", "city": "Almaty", "address": "Address A", "phone": "+7 111"},
            {"name": "Dental ABC", "city": "Almaty", "address": "Address B", "phone": "+7 222"},
        ]
        self.assertEqual(len(resolve_entities(distinct_locations)), 2)

    def test_website_audit_unknown_and_deterministic_statuses(self):
        self.assertEqual(audit_website(None)["website_status"], "NO_WEBSITE_FOUND")
        html = '<html><head><title>Clinic</title><meta name="viewport" content="width=device-width"></head><body><a href="/booking">Запись</a></body></html>'
        with patch("app.website_audit._fetch", return_value=(html, "https://clinic.test/", 200, .04)):
            result = audit_website("https://clinic.test")
        self.assertEqual(result["website_status"], "WEBSITE_OK")
        self.assertTrue(result["signals"]["booking"])
        self.assertTrue(any(item["status"] == "CONFIRMED" for item in result["evidence"]))
        self.assertTrue(any(item["status"] == "NOT_DETECTED" for item in result["evidence"]))

    def test_website_audit_does_not_turn_informational_mentions_into_features(self):
        html = """<html><head><title>Example clinic</title><meta name="description" content="About us"></head><body>
          <p>Online booking is not available.</p><p>WhatsApp us</p>
          <p>CRM systems are important for clinics.</p><article>Our article discusses payment options.</article>
        </body></html>"""
        with patch("app.website_audit._fetch", return_value=(html, "https://clinic.test/", 200, .04)):
            result = audit_website("https://clinic.test")
        self.assertFalse(result["signals"]["booking"])
        self.assertFalse(result["signals"]["whatsapp"])
        self.assertFalse(result["signals"]["crm"])
        self.assertFalse(result["signals"]["online_payment"])
        for key in ("booking", "whatsapp", "crm", "online_payment"):
            item = next(item for item in result["evidence"] if item["fact"].startswith(f"{key} "))
            self.assertEqual(item["status"], "NOT_DETECTED")
            self.assertTrue(item["source"])
            self.assertTrue(item["observed_at"])

    def test_website_audit_requires_real_action_links_for_booking_whatsapp_and_payment(self):
        html = """<html><head><title>Example</title><meta name="description" content="Example"></head><body>
          <a href="/booking">Запись</a><a href="https://wa.me/77771234567">WhatsApp</a>
          <a href="https://paybox.money/merchant/123">Оплатить</a>
        </body></html>"""
        with patch("app.website_audit._fetch", return_value=(html, "https://clinic.test/", 200, .04)):
            result = audit_website("https://clinic.test")
        self.assertTrue(result["signals"]["booking"])
        self.assertTrue(result["signals"]["whatsapp"])
        self.assertTrue(result["signals"]["online_payment"])

    def test_unavailable_website_is_unknown_not_confirmed_failure_evidence(self):
        with patch("app.website_audit._fetch", side_effect=TimeoutError("offline")):
            result = audit_website("https://clinic.test")
        self.assertEqual(result["website_status"], "WEBSITE_UNAVAILABLE")
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["evidence"][0]["status"], "UNKNOWN")
        self.assertTrue(result["evidence"][0]["observed_at"])

    def test_invalid_enum_and_missing_fields_never_create_confirmed_claim(self):
        payload = {"company": {"name": "Detail Co", "website": "https://detail.example"}, "evidence": []}
        result = validate_analysis(payload, {"digital_state": {"crm": {"status": "PRESENT", "evidence_ids": []}, "online_booking": "malformed"}})
        self.assertEqual(result["digital_state"]["crm"]["status"], "UNKNOWN")
        self.assertEqual(result["digital_state"]["online_booking"]["status"], "UNKNOWN")
        missing = validate_analysis({"company": {"name": "Detail Co"}, "evidence": []}, {})
        self.assertFalse(any(claim.get("status") == "CONFIRMED" for claim in missing.get("digital_state", {}).values()))

    def test_missing_source_is_explicit(self):
        class Broken:
            name = "Yandex"
            def search(self, query, target_count=10): raise RuntimeError("blocked")
        run = discover([Broken()], "dental", "стоматология", 10)
        self.assertEqual(run.source_status["Yandex"], "SOURCE_UNAVAILABLE")
        self.assertEqual(run.candidates, [])

    def test_provider_timeout_is_bounded_and_structured(self):
        with patch("app.discovery.providers.urlopen", side_effect=TimeoutError("timed out")):
            with self.assertRaises(SourceUnavailable) as ctx: _get("https://timeout.test", timeout=1)
        self.assertIn("timed out", str(ctx.exception))

    def test_search_result_filter_rejects_interstitials(self):
        self.assertFalse(_usable_result("Why might this happen?", "https://support.google.com/websearch"))
        self.assertFalse(_usable_result("WhatsApp", "https://link.2gis.ru/redirect"))
        self.assertFalse(_usable_result("��������", "https://example.test/clinic"))

    def test_one_provider_failure_does_not_stop_another(self):
        class Broken:
            name = "2gis"
            def search(self, query, target_count=10): raise SourceUnavailable("blocked")
        good = PublicSourceProvider("yandex", [{"name": "A", "city": "Astana", "source_url": "yandex://a"}])
        run = discover([Broken(), good], "dental", "стоматология", 10)
        self.assertEqual(run.source_status["2gis"], "SOURCE_UNAVAILABLE")
        self.assertEqual(run.source_status["yandex"], "SUCCESS")
        self.assertEqual(len(run.candidates), 1)

    def test_lm_malformed_json_is_rejected(self):
        client = LMStudioClient("http://127.0.0.1:1234/v1", "test")
        with patch.object(client, "_request", return_value={"choices": [{"message": {"content": "not json"}}]}):
            with self.assertRaises(LMStudioError): client.chat_opportunity({"company": {}})
        with patch.object(client, "_request", return_value={"choices": [{"message": {"content": "{broken}"}}]}):
            with self.assertRaises(LMStudioError): client.chat_opportunity({"company": {}})

    def test_dashboard_lm_contract_never_falls_back_to_another_model(self):
        from app import server
        with patch("app.server.load_env", return_value={"LM_STUDIO_MODEL": "qwen/qwen2.5-coder-14b"}):
            with self.assertRaisesRegex(RuntimeError, "Требуется только модель"):
                server.lm_client({"LM_STUDIO_MODEL": "qwen/qwen2.5-coder-14b"})
        with patch("app.server.LMStudioClient") as client_type:
            client_type.return_value.health.return_value = {"data": [{"id": "qwen/qwen2.5-coder-14b"}]}
            with self.assertRaisesRegex(RuntimeError, "автоматическая замена запрещена"):
                server.lm_client({})
            self.assertEqual(client_type.call_args.args[1], "qwen/qwen3-vl-8b")

    def test_invalid_evidence_id_is_rejected(self):
        payload = {"company": {"name": "Detail Co", "website": "https://detail.example"}, "evidence": [{"evidence_id": "ev-001", "status": "CONFIRMED", "fact": "booking detected", "source": "https://detail.example", "company_name": "Detail Co"}]}
        analysis = {"digital_state": {"online_booking": {"status": "CONFIRMED", "evidence_ids": ["ev-missing"]}}}
        with self.assertRaises(EvidenceValidationError): validate_analysis(payload, analysis)

    def test_analysis_cannot_reference_evidence_omitted_from_llm_context(self):
        evidence = [
            {"evidence_id": "ev-sent", "status": "CONFIRMED", "fact": "Booking action detected", "source": "https://detail.example/booking", "company_name": "Detail Co"},
            {"evidence_id": "ev-not-sent", "status": "CONFIRMED", "fact": "CRM detected", "source": "https://detail.example/crm", "company_name": "Detail Co"},
        ]
        payload = {"company": {"name": "Detail Co", "website": "https://detail.example"}, "evidence": evidence}
        analysis = {"digital_state": {"crm": {"status": "CONFIRMED", "reason": "CRM found.", "evidence_ids": ["ev-not-sent"]}}}
        with self.assertRaises(EvidenceValidationError):
            validate_analysis(payload, analysis, {"ev-sent"})

    def test_confirmed_claim_without_evidence_is_downgraded(self):
        payload = {"company": {"name": "Detail Co", "website": "https://detail.example"}, "evidence": []}
        analysis = {"digital_state": {"crm": {"status": "CONFIRMED", "reason": "CRM", "evidence_ids": []}}}
        result = validate_analysis(payload, analysis)
        self.assertEqual(result["digital_state"]["crm"]["status"], "UNKNOWN")

    def test_evidence_owned_by_another_company_is_rejected(self):
        payload = {"company": {"name": "Detail Co", "website": "https://detail.example"}, "evidence": [{"evidence_id": "ev-001", "status": "CONFIRMED", "fact": "CRM detected", "source": "https://detail.example", "company_name": "Other Co"}]}
        analysis = {"digital_state": {"crm": {"status": "CONFIRMED", "evidence_ids": ["ev-001"]}}}
        with self.assertRaises(EvidenceValidationError): validate_analysis(payload, analysis)

    def test_unknown_evidence_source_cannot_confirm_claim(self):
        payload = {"company": {"name": "Detail Co", "website": "https://detail.example"}, "evidence": [{"evidence_id": "ev-001", "status": "CONFIRMED", "fact": "CRM detected", "source": "file://private", "company_name": "Detail Co"}]}
        analysis = {"digital_state": {"crm": {"status": "CONFIRMED", "evidence_ids": ["ev-001"]}}}
        result = validate_analysis(payload, analysis)
        self.assertEqual(result["digital_state"]["crm"]["status"], "UNKNOWN")
        self.assertEqual(result["digital_state"]["crm"]["evidence_ids"], [])

    def test_gemini_import_new_duplicate_null_and_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "batch.json"; db_path = Path(tmp) / "leads.db"
            record = {"name": "Clinic A", "address": "Astana, A 1", "phone": "+7 700 1", "website": None, "source_url": "https://gemini.example/source", "source_type": "web_research", "summary": "Observed clinic listing", "evidence": ["listing"], "confidence": 0.81}
            path.write_text(json.dumps([record], ensure_ascii=False), encoding="utf-8")
            first = import_gemini_file(path, "dental", db_path)
            second = import_gemini_file(path, "dental", db_path)
            self.assertEqual((first["new"], first["duplicates"]), (1, 0))
            self.assertEqual((second["new"], second["duplicates"]), (0, 1))
            from app.storage.db import Database
            db = Database(db_path); stored = db.list_clinics()[0]; db.close()
            profile = json.loads(stored["profile_json"])
            self.assertIsNone(stored["website"])
            self.assertEqual(profile["sources"][0]["source"], "gemini_discovery")
            self.assertEqual(profile["claim_status"], "UNVERIFIED")
            self.assertEqual(profile["duplicate_discoveries"], 1)


if __name__ == "__main__": unittest.main()
