import unittest
from unittest.mock import patch

from app.autonomous import validate_analysis
from app.website_audit import audit_website


class WebsiteAuditFalsePositiveTests(unittest.TestCase):
    def audit_html(self, html):
        with patch("app.website_audit._fetch", return_value=(html, "https://berlin.example/", 200, 0.1)):
            return audit_website("https://berlin.example/")

    def test_negated_booking_whatsapp_word_crm_article_and_payment_article_are_not_confirmed(self):
        cases = [
            ('<p>Online booking is not available.</p>', "booking"),
            ('<p>WhatsApp us</p>', "whatsapp"),
            ('<p>CRM systems are important for clinics.</p>', "crm"),
            ('<article>Online payment options are discussed in this information article.</article>', "online_payment"),
        ]
        for html, signal in cases:
            with self.subTest(signal=signal):
                audit = self.audit_html(f"<html><head><title>Clinic</title><meta name='description' content='Clinic'></head><body>{html}</body></html>")
                self.assertFalse(audit["signals"][signal])
                evidence = next(item for item in audit["evidence"] if item["fact"].startswith(f"{signal} not detected"))
                self.assertEqual(evidence["status"], "NOT_DETECTED")

    def test_chat_widget_does_not_claim_ai_assistant(self):
        audit = self.audit_html("<html><head><title>Berlin Clinic</title><script src='https://cdn.jivo.ru/widget.js'></script></head><body><p>Contact us online</p></body></html>")
        self.assertTrue(audit["signals"]["chat_widget"])
        self.assertFalse(audit["signals"]["ai_assistant"])
        analysis = {"digital_state": {
            "chat_widget": {"status": "CONFIRMED", "reason": "Chat marker detected", "evidence_ids": [], "confidence": "HIGH"},
            "ai_assistant": {"status": "NOT_DETECTED", "reason": "No AI marker", "evidence_ids": [], "confidence": "LOW"},
        }}
        result = validate_analysis({"company": {"name": "Berlin Clinic", "website": "https://berlin.example/"}, "website_audit": audit, "evidence": audit["evidence"]}, analysis)
        self.assertEqual(result["digital_state"]["chat_widget"]["status"], "CONFIRMED")
        self.assertEqual(result["digital_state"]["ai_assistant"]["status"], "UNKNOWN")
        self.assertIn("does not show whether it is AI-powered", result["digital_state"]["ai_assistant"]["reason"])
        self.assertTrue(result["digital_state"]["ai_assistant"]["evidence_ids"])


if __name__ == "__main__":
    unittest.main()
