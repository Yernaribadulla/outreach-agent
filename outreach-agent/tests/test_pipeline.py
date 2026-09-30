import tempfile
import unittest
from pathlib import Path

from app.storage.db import Database
from app.email.sender import SMTPProvider, SendError, SimulatedProvider


class OutreachSafetyTests(unittest.TestCase):
    def test_sending_requires_approval_at_storage_boundary(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Database(Path(folder) / "test.db")
            clinic = db.add_clinic({"name": "Demo", "website": "https://example.invalid"})
            contact = db.add_contact(clinic, "info@example.invalid", "mock://contact")
            draft = db.add_draft(clinic, contact, {"subject": "Test", "body": "Test", "rationale": "Mock", "source_observations": [], "confidence": .9})
            row = db.get_draft(draft)
            self.assertEqual(row["status"], "DRAFTED")
            self.assertRaises(SendError, SMTPProvider({"SMTP_ENABLED": "false"}).send, row["email"], row["subject"], row["body"])
            db.close()

    def test_suppression_is_checked(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Database(Path(folder) / "test.db")
            db.suppress("info@example.invalid", "opt-out")
            self.assertTrue(db.is_suppressed("INFO@EXAMPLE.INVALID"))
            db.close()

    def test_simulated_provider_does_not_send_network_requests(self):
        provider = SimulatedProvider(); provider.send("info@example.invalid", "Subject", "Body")
        self.assertEqual(provider.name, "simulated")
        self.assertEqual(provider.sent[0]["recipient"], "info@example.invalid")

    def test_contact_provenance_and_draft_persistence_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Database(Path(folder) / "test.db")
            clinic = db.add_clinic({"name": "Detail Co", "website": "https://detail.example", "category": "detailing"})
            contact = db.add_contact(clinic, "hello@detail.example", "https://detail.example/contact")
            row = db.list_contacts()[0]
            self.assertEqual(row["source_url"], "https://detail.example/contact")
            self.assertTrue(row["is_public"])
            draft_data = {"subject": "A review draft", "body": "Plain text", "plain_text_body": "Plain text", "html_body": "<p>Plain text</p>", "rationale": "Evidence-based", "source_observations": ["ev-001"], "confidence": .8}
            draft_id = db.add_draft(clinic, contact, draft_data)
            second_id = db.add_draft(clinic, contact, {**draft_data, "subject": "Should not duplicate"})
            self.assertEqual(draft_id, second_id)
            persisted = db.get_draft(draft_id)
            self.assertEqual(persisted["subject"], "A review draft")
            self.assertEqual(persisted["plain_text_body"], "Plain text")
            self.assertEqual(persisted["html_body"], "<p>Plain text</p>")
            self.assertIn("ev-001", persisted["source_observations"])
            db.close()

    def test_rediscovery_preserves_existing_profile_and_contacts(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Database(Path(folder) / "test.db")
            profile = {"qualification": {"status": "QUALIFIED"}, "evidence": [{"evidence_id": "ev-old", "source": "https://detail.example/"}], "contacts": [{"email": "hello@detail.example", "source": "https://detail.example/contact"}]}
            clinic = db.add_clinic({"name": "Detail Co", "city": "Astana", "website": "https://detail.example/", "category": "detailing", "profile": profile})
            same = db.add_clinic({"name": "Detail Co", "city": "Astana", "website": None, "category": "detailing", "profile": {"status": "DISCOVERED", "evidence": [], "contacts": []}})
            detail = db.clinic_detail(same)
            self.assertEqual(clinic, same)
            self.assertEqual(detail["clinic"]["website"], "https://detail.example/")
            self.assertEqual(detail["clinic"]["profile"]["qualification"]["status"], "QUALIFIED")
            self.assertEqual(detail["evidence"][0]["evidence_id"], "ev-old")
            self.assertEqual(detail["clinic"]["profile"]["contacts"][0]["email"], "hello@detail.example")
            db.close()

    def test_startup_preserves_existing_sent_timestamp(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "startup.db"
            db = Database(path)
            clinic = db.add_clinic({"name": "Historical Sent Fixture", "city": "Test"})
            contact = db.add_contact(clinic, "sent@example.invalid", "https://example.invalid/contact")
            draft = db.add_draft(clinic, contact, {"subject": "Sent", "body": "Sent", "rationale": "Fixture", "source_observations": ["local"], "confidence": 1})
            sent_at = "2025-01-02T03:04:05+00:00"
            db.conn.execute("UPDATE drafts SET status='SENT', updated_at=? WHERE id=?", (sent_at, draft))
            db.conn.commit()
            db.log_send(draft, "sent@example.invalid", "simulated", "SENT")
            db.close()

            reopened = Database(path)
            persisted = reopened.get_draft(draft)
            self.assertEqual(persisted["status"], "SENT")
            self.assertEqual(persisted["updated_at"], sent_at)
            reopened.close()


if __name__ == "__main__": unittest.main()
