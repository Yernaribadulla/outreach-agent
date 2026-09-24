import tempfile
import json
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
from app.discovery.gemini_import import import_gemini_file


class LeadEngineTests(unittest.TestCase):
    def test_vertical_configuration_is_not_dental_hardcoded(self):
        self.assertEqual(get_vertical("dental").key, "dental")
        self.assertIn("before_after", get_vertical("detailing").opportunity_signals)

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

    def test_website_audit_unknown_and_deterministic_statuses(self):
        self.assertEqual(audit_website(None)["website_status"], "NO_WEBSITE_FOUND")
        html = '<html><head><title>Clinic</title><meta name="viewport" content="width=device-width"></head><body><a href="/booking">Запись</a></body></html>'
        with patch("app.website_audit._fetch", return_value=(html, "https://clinic.test/", 200, .04)):
            result = audit_website("https://clinic.test")
        self.assertEqual(result["website_status"], "WEBSITE_OK")
        self.assertTrue(result["signals"]["booking"])
        self.assertTrue(all(item["status"] == "CONFIRMED" for item in result["evidence"]))

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
