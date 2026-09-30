from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from app.analysis.lm_studio import LMStudioClient, LMStudioError
from app.generation.email_draft import generate_draft, prepare_draft_formats
from app.verticals import sender_config


DEMO_URL = "https://yernaribadulla.github.io/Dentist_rus_commercial/"


class DraftClient:
    def __init__(self, body: str):
        self.body = body
        self.payload = None

    def chat_draft(self, payload: dict) -> dict:
        self.payload = payload
        return {
            "subject": "Цифровой путь для пациентов",
            "body": self.body,
            "rationale": "Angle выбран из переданного lead brief и цифрового профиля.",
            "source_observations": ["На сайте есть раздел услуг."],
            "confidence": 0.8,
        }


class EmailDraftTests(unittest.TestCase):
    def setUp(self):
        self.clinic = {
            "name": "Стоматология Пример",
            "category": "dental",
            "evidence": [{"evidence_id": "ev-site", "status": "CONFIRMED", "fact": "На сайте опубликован раздел услуг.", "source": "https://clinic.example/services"}],
        }
        self.analysis = {
            "sales_brief": "Официальный сайт содержит информацию об услугах.",
            "recommended_angle": "Website opportunity",
            "opportunities": ["Показать цифровой путь пациента"],
            "observations": ["На сайте опубликован раздел услуг."],
            "digital_state": {
                "website": {"status": "CONFIRMED", "evidence_ids": ["ev-site"]},
                "ai_assistant": {"status": "NOT_DETECTED", "reason": "Не обнаружено в проверенных страницах.", "evidence_ids": []},
                "online_booking": {"status": "NOT_DETECTED", "reason": "Не обнаружено в проверенных страницах.", "evidence_ids": []},
                "online_payment": {"status": "UNKNOWN", "reason": "Проверка не дала результата.", "evidence_ids": []},
                "automation": {"status": "UNKNOWN", "reason": "Нет данных.", "evidence_ids": []},
            },
        }

    def make_draft(self, client: DraftClient) -> dict:
        return generate_draft(client, self.clinic, self.analysis, sender_config("dental", {}))

    def test_personalized_draft_uses_supported_offer_and_not_detected_is_not_absence(self):
        body = (
            "Здравствуйте!\n\nМеня зовут Ернар Ибадулла, я разрабатываю цифровые решения для стоматологических клиник. "
            "Можно обсудить современный сайт с ИИ-ассистентом, который отвечает на вопросы пациентов и помогает с онлайн-записью. "
            "Пример сайта: " + DEMO_URL + "\n\nЕсли интересно, покажу, как это можно адаптировать для вашей клиники."
        )
        client = DraftClient(body)
        draft = self.make_draft(client)

        self.assertIsNotNone(client.payload)
        self.assertIn("сайт", draft["body"].lower())
        self.assertIn("ии-ассистент", draft["body"].lower())
        self.assertIn("онлайн-запись", draft["body"].lower())
        self.assertTrue(draft["body"].startswith("Здравствуйте!\n\nМеня зовут Ернар Ибадулла, я разрабатываю цифровые решения для стоматологических клиник."))
        self.assertIn("Могу помочь с разработкой или развитием сайта клиники.", draft["body"])
        self.assertIn(DEMO_URL, draft["body"])
        self.assertEqual(draft["body"].count("Если интересно, могу показать, как это можно адаптировать именно под вашу клинику."), 1)
        self.assertNotIn("online_payment", client.payload["sender"]["offer"])
        rules = "\n".join(client.payload["rules"])
        self.assertIn("NOT_DETECTED", rules)
        self.assertIn("не более двух", rules)
        self.assertIn("гарантий роста", rules)
        self.assertIn("NOT_DETECTED", json.dumps(client.payload["analysis"], ensure_ascii=False))
        self.assertEqual(draft["source_observations"], ["На сайте опубликован раздел услуг."])
        for unsupported in ("у вас нет онлайн-записи", "клиника устарела", "гарантируем рост выручки", "ваша CRM"):
            self.assertNotIn(unsupported, draft["body"].lower())

    def test_online_payment_can_be_personalized_as_a_separate_angle(self):
        self.analysis["recommended_angle"] = "Optional online payment"
        client = DraftClient(
            "Здравствуйте! Можно обсудить ИИ-ассистента для ответов пациентам, онлайн-запись и оплату онлайн. "
            + DEMO_URL
        )
        draft = self.make_draft(client)
        self.assertIn("оплату онлайн", draft["body"].lower())
        self.assertIn("ии-ассистента", draft["body"].lower())
        self.assertIn("онлайн-запись", draft["body"].lower())
        self.assertIn(DEMO_URL, draft["body"])
        self.assertIn("Optional online payment", client.payload["analysis"]["recommended_angle"])

    def test_generated_copy_is_trimmed_to_selected_elements_and_unsupported_absence_is_rejected(self):
        draft = self.make_draft(DraftClient(
            "У вас на сайте уже есть список услуг — это отличный старт! Можно обсудить сайт, ИИ-ассистента, онлайн-запись и онлайн-оплату. "
            "Все это можно реализовать без сложных изменений и сделать цифровой путь более эффективным."
        ))
        self.assertNotIn("отличный старт", draft["body"].lower())
        self.assertNotIn("онлайн-оплату", draft["body"].lower())
        self.assertNotIn("без сложных изменений", draft["body"].lower())
        self.assertNotIn("более эффективным", draft["body"].lower())
        self.assertIn("онлайн-запись", draft["body"].lower())
        punctuation_client = DraftClient("На сайте опубликован список услуг Мы можем обсудить цифровой путь пациента.")
        punctuation_draft = self.make_draft(punctuation_client)
        self.assertIn("услуг. Мы можем", punctuation_draft["body"])
        with self.assertRaisesRegex(ValueError, "asserts a feature is absent"):
            self.make_draft(DraftClient("У вас нет онлайн-записи, но можно обсудить сайт и ИИ-ассистента."))

    def test_plain_and_html_are_rendered_from_one_canonical_body(self):
        draft = prepare_draft_formats({"body": "Первый абзац\n<script>alert(1)</script>", "plain_text_body": "different", "html_body": "<script>bad()</script>"})
        self.assertEqual(draft["plain_text_body"], draft["body"])
        self.assertIn("&lt;script&gt;", draft["html_body"])
        self.assertNotIn("<script>", draft["html_body"])
        self.assertNotIn("different", draft["html_body"])

    def test_lm_draft_contract_parses_draft_fields_and_rejects_analysis_shaped_json(self):
        client = LMStudioClient("http://127.0.0.1:1234/v1", "qwen/qwen3-vl-8b")
        draft_response = {"subject": "Тема", "body": "Текст", "rationale": "Основание", "source_observations": ["Факт"], "confidence": 0.7}
        with patch.object(client, "_request", return_value={"choices": [{"message": {"content": json.dumps(draft_response, ensure_ascii=False)}}]}) as request:
            self.assertEqual(client.chat_draft({"clinic": {"name": "Test"}}), draft_response)
            self.assertIn("subject, body, rationale", request.call_args.args[0]["messages"][0]["content"])
        with patch.object(client, "_request", return_value={"choices": [{"message": {"content": '{"recommended_angle":"analysis only"}'}}]}):
            with self.assertRaisesRegex(LMStudioError, "missing required fields"):
                client.chat_draft({"clinic": {"name": "Test"}})


if __name__ == "__main__":
    unittest.main()
