from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .context import build_llm_context


class LMStudioError(RuntimeError):
    pass


class StructuredOutputUnsupported(LMStudioError):
    """LM Studio or the loaded model rejected the JSON-schema response format."""


CONNECT_TIMEOUT_SECONDS = 10
READ_TIMEOUT_SECONDS = 110
OPPORTUNITY_MAX_TOKENS = 1400
OPPORTUNITY_MAX_ATTEMPTS = 2


OPPORTUNITY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "company_summary": {"type": "string"},
        "priority": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "score": {"type": "integer", "minimum": 0, "maximum": 100},
                "website_opportunity": {"type": "boolean"},
                "booking_opportunity": {"type": "boolean"},
                "crm_opportunity": {"type": "boolean"},
                "ai_opportunity": {"type": "boolean"},
                "automation_opportunity": {"type": "boolean"},
            },
            "required": [
                "score", "website_opportunity", "booking_opportunity", "crm_opportunity",
                "ai_opportunity", "automation_opportunity",
            ],
        },
        "why_this_lead": {"type": "array", "items": {"type": "string"}},
        "recommended_angle": {"type": "string"},
        "sales_brief": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "opportunities": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "signal": {"type": "string", "enum": ["website", "booking", "crm", "ai", "automation", "other"]},
                    "rationale": {"type": "string"},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["signal", "rationale", "evidence_ids"],
            },
        },
    },
    "required": [
        "company_summary", "priority", "why_this_lead", "recommended_angle", "sales_brief",
        "confidence", "opportunities",
    ],
}


def _api_error_message(data: object) -> str:
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            return " ".join(str(error.get(key) or "") for key in ("type", "code", "message")).strip()
        if error:
            return str(error)
    return ""


def _structured_output_rejected(status: int, message: str) -> bool:
    lowered = message.casefold()
    structured_terms = ("response_format", "json_schema", "structured output", "grammar", "schema")
    return status in {400, 404, 422} and any(term in lowered for term in structured_terms)


def _clean_fences(content: str) -> str:
    lines = content.strip().splitlines()
    if lines and re.fullmatch(r"```\s*(?:json)?\s*", lines[0].strip(), re.I):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _balanced_json_objects(content: str) -> list[str]:
    """Find complete top-level JSON object candidates without interpreting their contents."""
    objects: list[str] = []
    start = None
    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(content):
        if start is None:
            if char == "{":
                start, depth, in_string, escaped = index, 1, False, False
            continue
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                objects.append(content[start:index + 1])
                start = None
    return objects


def _parse_json_object(content: object) -> dict:
    if isinstance(content, list):
        content = "".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
    if not isinstance(content, str):
        raise ValueError("response content is not text")
    cleaned = _clean_fences(content)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        candidates = _balanced_json_objects(cleaned)
        if len(candidates) != 1:
            if not candidates:
                raise ValueError("no complete JSON object")
            raise ValueError("multiple JSON objects")
        try:
            value = json.loads(candidates[0])
        except json.JSONDecodeError as exc:
            raise ValueError(f"malformed JSON at line {exc.lineno}, column {exc.colno}") from exc
    if not isinstance(value, dict):
        raise ValueError("top-level JSON value must be an object")
    return value


def _validate_opportunity_schema(result: dict) -> None:
    required = set(OPPORTUNITY_SCHEMA["required"])
    missing = sorted(required - set(result))
    extra = sorted(set(result) - required)
    if missing:
        raise ValueError("missing required fields: " + ", ".join(missing))
    if extra:
        raise ValueError("unexpected fields: " + ", ".join(extra))
    for field in ("company_summary", "recommended_angle", "sales_brief"):
        if not isinstance(result[field], str):
            raise ValueError(f"{field} must be a string")
    if not isinstance(result["why_this_lead"], list) or not all(isinstance(item, str) for item in result["why_this_lead"]):
        raise ValueError("why_this_lead must be an array of strings")
    confidence = result["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise ValueError("confidence must be a number from 0 to 1")
    priority = result["priority"]
    priority_fields = {
        "score", "website_opportunity", "booking_opportunity", "crm_opportunity",
        "ai_opportunity", "automation_opportunity",
    }
    if not isinstance(priority, dict) or set(priority) != priority_fields:
        raise ValueError("priority must contain score and all five opportunity flags")
    score = priority["score"]
    if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 100:
        raise ValueError("priority.score must be an integer from 0 to 100")
    if any(not isinstance(priority[key], bool) for key in priority_fields - {"score"}):
        raise ValueError("priority opportunity flags must be booleans")
    opportunities = result["opportunities"]
    if not isinstance(opportunities, list):
        raise ValueError("opportunities must be an array")
    allowed_signals = {"website", "booking", "crm", "ai", "automation", "other"}
    for opportunity in opportunities:
        if not isinstance(opportunity, dict) or set(opportunity) != {"signal", "rationale", "evidence_ids"}:
            raise ValueError("each opportunity must contain signal, rationale, and evidence_ids")
        if opportunity["signal"] not in allowed_signals or not isinstance(opportunity["rationale"], str):
            raise ValueError("opportunity signal or rationale is invalid")
        references = opportunity["evidence_ids"]
        if not isinstance(references, list) or not all(isinstance(item, str) for item in references):
            raise ValueError("opportunity evidence_ids must be an array of strings")
        if not references or len(set(references)) != len(references):
            raise ValueError("each opportunity needs unique supporting evidence IDs")


def _safe_preview(value: object, limit: int = 240) -> str:
    preview = str(value or "")[:limit]
    preview = re.sub(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", "[email]", preview)
    preview = re.sub(r"(?<!\w)(?:\+?\d[\d\s().-]{7,}\d)(?!\w)", "[phone]", preview)
    return preview.replace("\r", " ").replace("\n", " ")


def _deterministic_digital_state(context: dict) -> dict:
    """Copy deterministic audit outcomes into the app contract; the model does not classify them."""
    summary = context.get("website_summary") or {}
    evidence = context.get("key_evidence") or []
    mapping = {
        "website": "website", "mobile": "mobile", "online_booking": "online_booking",
        "crm": "crm", "chat_widget": "chat_widget", "ai_assistant": "ai_assistant",
        "online_payment": "online_payment", "automation": "automation",
    }
    categories = {
        "website": {"website"}, "mobile": {"mobile"}, "online_booking": {"online_booking", "booking"},
        "crm": {"crm"}, "chat_widget": {"chat_widget"}, "ai_assistant": {"ai_assistant"},
        "online_payment": {"online_payment"}, "automation": {"automation"},
    }
    state = {}
    for key, signal_key in mapping.items():
        raw = summary.get("status", "UNKNOWN") if key == "website" else summary.get(signal_key, "UNKNOWN")
        if key == "website":
            status = "CONFIRMED" if raw == "WEBSITE_OK" else "NOT_DETECTED" if raw == "NO_WEBSITE_FOUND" else "UNKNOWN"
        else:
            status = str(raw or "UNKNOWN").upper()
            if status not in {"CONFIRMED", "NOT_DETECTED", "INFERRED", "UNKNOWN"}:
                status = "UNKNOWN"
        refs = [
            str(item.get("evidence_id")) for item in evidence
            if status == "CONFIRMED" and item.get("status") == "CONFIRMED"
            and item.get("category") in categories[key] and item.get("evidence_id")
        ]
        if status == "CONFIRMED" and not refs:
            status = "UNKNOWN"
        reason = {
            "CONFIRMED": "Confirmed by the deterministic website audit and its source evidence.",
            "NOT_DETECTED": "The deterministic page scan did not detect this signal; absence is not proven.",
            "UNKNOWN": "The available deterministic audit did not establish this signal.",
            "INFERRED": "The audit provides indirect evidence only; manual review is required.",
        }[status]
        state[key] = {"status": status, "reason": reason, "evidence_ids": refs[:2], "confidence": "HIGH" if refs else "LOW"}
    return state


class LMStudioClient:
    def __init__(self, base_url: str, model: str):
        self.base_url, self.model = base_url.rstrip("/"), model

    def _request(self, body: dict) -> dict:
        encoded = json.dumps(body, ensure_ascii=False)
        endpoint = self.base_url + "/chat/completions"
        curl = shutil.which("curl.exe") or shutil.which("curl")
        if curl:
            try:
                result = subprocess.run(
                    [curl, "-sS", "--connect-timeout", str(CONNECT_TIMEOUT_SECONDS), "--max-time", str(READ_TIMEOUT_SECONDS),
                     "-H", "Content-Type: application/json", "-X", "POST", endpoint, "--data-binary", encoded,
                     "-w", "\n%{http_code}"],
                    capture_output=True, text=True, encoding="utf-8", check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise LMStudioError(f"LM Studio request timed out after {READ_TIMEOUT_SECONDS} seconds") from exc
            if result.returncode:
                raise LMStudioError("LM Studio connection/read error")
            raw_body, _, raw_status = result.stdout.rpartition("\n")
            try:
                status = int(raw_status.strip())
                data = json.loads(raw_body)
            except (ValueError, json.JSONDecodeError) as exc:
                raise LMStudioError("LM Studio returned an invalid transport response") from exc
            self._raise_api_error(data, status)
            return data

        request = Request(endpoint, data=encoded.encode("utf-8"), headers={"Content-Type": "application/json", "Connection": "close"}, method="POST")
        try:
            with urlopen(request, timeout=READ_TIMEOUT_SECONDS) as response:
                data = json.loads(response.read().decode("utf-8"))
                self._raise_api_error(data, response.status)
                return data
        except HTTPError as exc:
            body_text = exc.read(1000).decode("utf-8", errors="replace")
            try:
                error_data = json.loads(body_text)
            except json.JSONDecodeError:
                error_data = {"error": body_text}
            self._raise_api_error(error_data, exc.code)
        except URLError as exc:
            if isinstance(exc.reason, (TimeoutError,)):
                raise LMStudioError(f"LM Studio request timed out after {READ_TIMEOUT_SECONDS} seconds") from exc
            raise LMStudioError("LM Studio connection error") from exc
        except TimeoutError as exc:
            raise LMStudioError(f"LM Studio request timed out after {READ_TIMEOUT_SECONDS} seconds") from exc
        except json.JSONDecodeError as exc:
            raise LMStudioError("LM Studio returned invalid transport JSON") from exc
        raise LMStudioError("LM Studio request failed")

    @staticmethod
    def _raise_api_error(data: object, status: int) -> None:
        message = _api_error_message(data)
        if not message and status < 400:
            return
        if _structured_output_rejected(status, message):
            raise StructuredOutputUnsupported("LM Studio rejected response_format=json_schema")
        raise LMStudioError(f"LM Studio API request failed (HTTP {status})")

    def chat_json(self, payload: dict) -> dict:
        started = time.perf_counter()
        prompt = "Только JSON, без markdown и пояснений. Не выдумывай факты; используй только evidence. Верни компактный объект строго с ключами clinic_summary, observations, potential_opportunities, personalization_points, recommended_angle, confidence. Массивы максимум по 2 коротких пункта; неизвестное обозначай uncertain. Значения на русском."
        body = {"model": self.model, "temperature": 0.0, "max_tokens": 500, "messages": [{"role": "user", "content": prompt + "\nДанные:\n" + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))}]}
        try:
            data = self._request(body)
            try: content = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc: raise LMStudioError("missing content: choices[0].message.content") from exc
            result = _parse_json_object(content)
            print(f"[LM] clinic={payload.get('clinic', {}).get('name', 'unknown')} model={self.model} latency={time.perf_counter()-started:.2f}s status=success", flush=True)
            return result
        except Exception as exc:
            print(f"[LM] clinic={payload.get('clinic', {}).get('name', 'unknown')} model={self.model} latency={time.perf_counter()-started:.2f}s status=fail error={type(exc).__name__}", flush=True)
            raise

    def chat_draft(self, payload: dict) -> dict:
        """Generate an email draft using a draft-specific JSON contract."""
        started = time.perf_counter()
        prompt = (
            "Ты пишешь письмо потенциальному B2B-клиенту. Только JSON, без markdown и пояснений. "
            "Не выдумывай факты, используй только переданные evidence и соблюдай все rules из данных. "
            "Верни объект строго с ключами subject, body, rationale, source_observations, confidence; subject/body/rationale — строки, "
            "source_observations — массив строк, confidence — число от 0 до 1."
        )
        body = {"model": self.model, "temperature": 0.0, "max_tokens": 800, "messages": [{"role": "user", "content": prompt + "\nДанные:\n" + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))}]}
        try:
            data = self._request(body)
            try:
                content = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                raise LMStudioError("missing draft content: choices[0].message.content") from exc
            result = _parse_json_object(content)
            required = {"subject", "body", "rationale", "source_observations", "confidence"}
            if set(result) != required:
                raise LMStudioError("draft response is missing required fields")
            if not all(isinstance(result[key], str) for key in ("subject", "body", "rationale")):
                raise LMStudioError("draft subject, body, and rationale must be strings")
            if not isinstance(result["source_observations"], list) or not all(isinstance(item, str) for item in result["source_observations"]):
                raise LMStudioError("draft source_observations must be an array of strings")
            if isinstance(result["confidence"], bool) or not isinstance(result["confidence"], (int, float)) or not 0 <= result["confidence"] <= 1:
                raise LMStudioError("draft confidence must be a number from 0 to 1")
            print(f"[LM] stage=draft company={(payload.get('clinic') or {}).get('name', 'unknown')} model={self.model} latency={time.perf_counter()-started:.2f}s status=success", flush=True)
            return result
        except Exception as exc:
            print(f"[LM] stage=draft company={(payload.get('clinic') or {}).get('name', 'unknown')} model={self.model} latency={time.perf_counter()-started:.2f}s status=fail error={type(exc).__name__}", flush=True)
            raise

    def _opportunity_request(self, context: dict, *, strict_retry: bool, structured: bool) -> dict:
        system_prompt = (
            "Ты делаешь семантический B2B opportunity analysis по переданному compact research object. "
            "Верни только один JSON object, без Markdown, fences, пояснений до или после. Не добавляй и не удаляй ключи. "
            "Контракт: company_summary:string; priority:{score:integer 0..100, website_opportunity:boolean, booking_opportunity:boolean, crm_opportunity:boolean, ai_opportunity:boolean, automation_opportunity:boolean}; "
            "why_this_lead:string[]; recommended_angle:string; sales_brief:string; confidence:number 0..1; "
            "opportunities:{signal:website|booking|crm|ai|automation|other, rationale:string, evidence_ids:string[]}[]. "
            "Все фактические выводы должны быть основаны на переданных evidence; не выдумывай функции, владельцев, технологии или sales context. "
            "Не пересчитывай deterministic data из Python: HTTP status, website availability, mobile, наличие email/phone/URL, booking, WhatsApp. "
            "priority.score — целое от 0 до 100; confidence — число от 0 до 1 и отражает уверенность в семантических выводах. "
            "recommended_angle и why_this_lead должны быть короткими и конкретными. opportunities — только возможные направления, подкреплённые evidence; "
            "evidence_ids должны буквально совпадать с ID из key_evidence. Если evidence для opportunity нет — не добавляй её. "
            "Неизвестное не выдавай за подтверждённый факт. Пиши по-русски."
        )
        if strict_retry:
            system_prompt = (
                "Return ONLY valid JSON matching the schema. Do not include any other text. "
                "Use only the supplied evidence; do not invent facts. Every opportunity must cite exact evidence_ids from the input."
            )
        body = {
            "model": self.model,
            "temperature": 0.0,
            "max_tokens": OPPORTUNITY_MAX_TOKENS,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "COMPACT RESEARCH OBJECT:\n" + json.dumps(context, ensure_ascii=False, separators=(",", ":"))},
            ],
        }
        if structured:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "opportunity_analysis", "strict": True, "schema": OPPORTUNITY_SCHEMA},
            }
        return self._request(body)

    def chat_opportunity(self, payload: dict) -> dict:
        started = time.perf_counter()
        context = build_llm_context(payload)
        serialized_context = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
        estimated_tokens = (len(serialized_context) + 3) // 4
        evidence_ids = {str(item.get("evidence_id")) for item in context.get("key_evidence", []) if item.get("evidence_id")}
        print(
            f"[LM] stage=opportunity model={self.model} context_chars={len(serialized_context)} "
            f"estimated_tokens={estimated_tokens} timeout={READ_TIMEOUT_SECONDS}s "
            f"evidence_count={len(context.get('key_evidence', []))} max_tokens={OPPORTUNITY_MAX_TOKENS}",
            flush=True,
        )
        structured = True
        try:
            try:
                data = self._opportunity_request(context, strict_retry=False, structured=True)
            except StructuredOutputUnsupported:
                structured = False
                print(f"[LM] stage=opportunity model={self.model} structured_output=unsupported using=strict_prompt", flush=True)
                data = self._opportunity_request(context, strict_retry=False, structured=False)

            output_content = ""
            last_parser_error = ""
            result = None
            for attempt in range(OPPORTUNITY_MAX_ATTEMPTS):
                try:
                    output_content = self._content(data)
                    result = _parse_json_object(output_content)
                    _validate_opportunity_schema(result)
                    unknown_refs = [
                        reference
                        for item in result["opportunities"]
                        for reference in item["evidence_ids"]
                        if reference not in evidence_ids
                    ]
                    if unknown_refs:
                        raise ValueError("opportunity references evidence not present in supplied context")
                    break
                except (LMStudioError, ValueError, KeyError, IndexError, TypeError) as exc:
                    last_parser_error = str(exc)[:160]
                    elapsed = time.perf_counter() - started
                    print(
                        f"[LM] stage=opportunity model={self.model} elapsed={elapsed:.2f}s "
                        f"response_length={len(output_content)} parser_error={last_parser_error!r} "
                        f"preview={_safe_preview(output_content)!r}",
                        flush=True,
                    )
                    if attempt + 1 >= OPPORTUNITY_MAX_ATTEMPTS:
                        raise LMStudioError(f"opportunity response invalid after {OPPORTUNITY_MAX_ATTEMPTS} attempts: {last_parser_error}") from exc
                    data = self._opportunity_request(context, strict_retry=True, structured=structured)

            assert result is not None
            result["digital_state"] = _deterministic_digital_state(context)
            print(
                f"[LM] stage=opportunity model={self.model} structured_output={'json_schema' if structured else 'unsupported'} "
                f"status=response_received output_chars={len(output_content)} elapsed={time.perf_counter()-started:.2f}s",
                flush=True,
            )
            return result
        except Exception as exc:
            print(
                f"[LM] stage=opportunity model={self.model} status=fail elapsed={time.perf_counter()-started:.2f}s "
                f"error={type(exc).__name__}: {str(exc)[:180]}",
                flush=True,
            )
            raise

    @staticmethod
    def _content(data: dict) -> str:
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LMStudioError("missing content: choices[0].message.content") from exc
        if isinstance(content, list):
            return "".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
        if not isinstance(content, str):
            raise LMStudioError("response content is not text")
        return content

    def health(self) -> dict:
        request = Request(self.base_url + "/models")
        with urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))
