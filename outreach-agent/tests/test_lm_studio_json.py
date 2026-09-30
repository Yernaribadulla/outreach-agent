import io
import json
import unittest
from unittest.mock import patch

from app.analysis import lm_studio
from app.analysis.lm_studio import LMStudioClient, LMStudioError, StructuredOutputUnsupported


def valid_opportunity(**overrides):
    result = {
        "company_summary": "Синтетическая клиника с публичной страницей.",
        "priority": {
            "score": 42,
            "website_opportunity": False,
            "booking_opportunity": False,
            "crm_opportunity": False,
            "ai_opportunity": False,
            "automation_opportunity": False,
        },
        "why_this_lead": ["Есть публичный источник для проверки."],
        "recommended_angle": "Сначала уточнить текущий путь записи.",
        "sales_brief": "Недостаточно данных для сильного предложения.",
        "confidence": 0.72,
        "opportunities": [],
    }
    result.update(overrides)
    return result


def response(content):
    return {"choices": [{"message": {"content": content}}]}


class LMStudioJSONContractTests(unittest.TestCase):
    def setUp(self):
        self.client = LMStudioClient("http://127.0.0.1:1234/v1", "qwen/qwen3-vl-8b")
        self.payload = {"company": {"name": "Synthetic clinic", "city": "Astana", "website": "https://clinic.example"}, "evidence": []}

    def test_parser_accepts_valid_json_fences_and_short_prefix(self):
        serialized = json.dumps(valid_opportunity(), ensure_ascii=False)
        for content in (serialized, "```json\n" + serialized + "\n```", "Короткая пометка.\n" + serialized + "\nГотово."):
            parsed = lm_studio._parse_json_object(content)
            lm_studio._validate_opportunity_schema(parsed)
            self.assertEqual(parsed["priority"]["score"], 42)

    def test_parser_rejects_malformed_or_multiple_objects(self):
        for content in ("ответ без JSON", '{"company_summary": broken}', '{"a":1}{"b":2}'):
            with self.subTest(content=content), self.assertRaises(ValueError):
                lm_studio._parse_json_object(content)

    def test_schema_rejects_missing_confidence_invalid_confidence_and_priority(self):
        cases = [
            ({key: value for key, value in valid_opportunity().items() if key != "confidence"}, "confidence"),
            (valid_opportunity(confidence="high"), "confidence"),
            (valid_opportunity(priority={**valid_opportunity()["priority"], "score": True}), "priority.score"),
            (valid_opportunity(priority={**valid_opportunity()["priority"], "score": 101}), "priority.score"),
            ({key: value for key, value in valid_opportunity().items() if key != "sales_brief"}, "sales_brief"),
        ]
        for candidate, expected in cases:
            with self.subTest(expected=expected), self.assertRaisesRegex(ValueError, expected):
                lm_studio._validate_opportunity_schema(candidate)

    def test_malformed_first_response_gets_one_strict_retry(self):
        calls = []
        contents = ["{not valid}", json.dumps(valid_opportunity(), ensure_ascii=False)]

        def fake_request(body):
            calls.append(body)
            return response(contents.pop(0))

        self.client._request = fake_request
        result = self.client.chat_opportunity(self.payload)
        self.assertEqual(result["confidence"], 0.72)
        self.assertEqual(len(calls), 2)
        self.assertIn("Return ONLY valid JSON matching the schema. Do not include any other text.", calls[1]["messages"][0]["content"])
        self.assertLess(len(calls[1]["messages"][0]["content"]), len(calls[0]["messages"][0]["content"]))
        self.assertEqual(calls[1]["response_format"]["type"], "json_schema")

    def test_second_malformed_response_fails_after_exactly_one_retry_with_safe_diagnostics(self):
        calls = []
        output = io.StringIO()

        def fake_request(body):
            calls.append(body)
            return response('{"email":"clinic@example.test", "phone":"+1 555 123 4567", bad}')

        self.client._request = fake_request
        with patch("sys.stdout", output), self.assertRaisesRegex(LMStudioError, "after 2 attempts"):
            self.client.chat_opportunity(self.payload)
        self.assertEqual(len(calls), 2)
        diagnostic = output.getvalue()
        self.assertIn("response_length=", diagnostic)
        self.assertIn("parser_error=", diagnostic)
        self.assertIn("preview=", diagnostic)
        self.assertNotIn("clinic@example.test", diagnostic)
        self.assertNotIn("+1 555 123 4567", diagnostic)

    def test_missing_required_field_and_invalid_enum_are_retried_then_rejected(self):
        missing = valid_opportunity()
        missing.pop("recommended_angle")
        invalid = valid_opportunity(opportunities=[{"signal": "made_up", "rationale": "x", "evidence_ids": []}])
        for first in (missing, invalid):
            calls = []

            def fake_request(body):
                calls.append(body)
                return response(json.dumps(first, ensure_ascii=False))

            self.client._request = fake_request
            with self.subTest(first=first), self.assertRaises(LMStudioError):
                self.client.chat_opportunity(self.payload)
            self.assertEqual(len(calls), 2)

    def test_invalid_evidence_reference_is_rejected_even_if_json_is_valid(self):
        candidate = valid_opportunity(opportunities=[{"signal": "booking", "rationale": "Book now", "evidence_ids": ["ev-not-sent"]}])
        calls = []

        def fake_request(body):
            calls.append(body)
            return response(json.dumps(candidate))

        self.client._request = fake_request
        with self.assertRaisesRegex(LMStudioError, "invalid after 2 attempts"):
            self.client.chat_opportunity(self.payload)
        self.assertEqual(len(calls), 2)

    def test_structured_output_rejection_uses_strict_prompt_without_claiming_support(self):
        calls = []

        def fake_request(body):
            calls.append(body)
            if len(calls) == 1:
                raise StructuredOutputUnsupported("unsupported")
            return response(json.dumps(valid_opportunity(), ensure_ascii=False))

        self.client._request = fake_request
        output = io.StringIO()
        with patch("sys.stdout", output):
            result = self.client.chat_opportunity(self.payload)
        self.assertEqual(result["priority"]["score"], 42)
        self.assertIn("structured_output=unsupported", output.getvalue())
        self.assertNotIn("response_format", calls[1])
        self.assertIn("confidence:number 0..1", calls[1]["messages"][0]["content"])

    def test_schema_sent_to_lm_studio_and_request_parameters_are_bounded(self):
        captured = {}

        def fake_request(body):
            captured.update(body)
            return response(json.dumps(valid_opportunity(), ensure_ascii=False))

        self.client._request = fake_request
        self.client.chat_opportunity(self.payload)
        self.assertEqual(captured["model"], "qwen/qwen3-vl-8b")
        self.assertEqual(captured["temperature"], 0.0)
        self.assertLessEqual(captured["max_tokens"], 1400)
        self.assertEqual(captured["response_format"]["type"], "json_schema")
        self.assertEqual(lm_studio.READ_TIMEOUT_SECONDS, 110)

    def test_deterministic_website_signals_are_python_owned_not_model_schema_fields(self):
        candidate = valid_opportunity()
        evidence = [
            {"evidence_id": "ev-site", "type": "website", "status": "CONFIRMED", "fact": "Website responds", "source": "https://clinic.example/", "company_name": "Synthetic clinic"},
            {"evidence_id": "ev-booking", "type": "booking", "status": "CONFIRMED", "fact": "Online booking action detected", "snippet": "Book now", "source": "https://clinic.example/booking", "company_name": "Synthetic clinic"},
        ]
        payload = {
            **self.payload,
            "website_audit": {"website_status": "WEBSITE_OK", "signals": {"booking": True, "mobile_friendly": False}},
            "evidence": evidence,
        }
        self.client._request = lambda body: response(json.dumps(candidate, ensure_ascii=False))
        result = self.client.chat_opportunity(payload)
        self.assertNotIn("digital_state", lm_studio.OPPORTUNITY_SCHEMA["properties"])
        self.assertEqual(result["digital_state"]["website"]["status"], "CONFIRMED")
        self.assertEqual(result["digital_state"]["online_booking"]["status"], "CONFIRMED")
        self.assertEqual(result["digital_state"]["mobile"]["status"], "NOT_DETECTED")


if __name__ == "__main__":
    unittest.main()
