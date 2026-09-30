from __future__ import annotations
import json, shutil, subprocess, time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

class LMStudioError(RuntimeError): pass

CONNECT_TIMEOUT_SECONDS = 10
READ_TIMEOUT_SECONDS = 180

class LMStudioClient:
    def __init__(self, base_url: str, model: str): self.base_url, self.model = base_url.rstrip("/"), model

    def _request(self, body: dict) -> dict:
        encoded = json.dumps(body, ensure_ascii=False)
        curl = shutil.which("curl.exe") or shutil.which("curl")
        if curl:
            try: result = subprocess.run([curl, "-sS", "--connect-timeout", str(CONNECT_TIMEOUT_SECONDS), "--max-time", str(READ_TIMEOUT_SECONDS), "-H", "Content-Type: application/json", "-X", "POST", self.base_url + "/chat/completions", "--data-binary", encoded], capture_output=True, text=True, encoding="utf-8", check=False)
            except subprocess.TimeoutExpired as exc: raise LMStudioError(f"read timeout: curl exceeded {READ_TIMEOUT_SECONDS} seconds") from exc
            if result.returncode: raise LMStudioError(f"connection/read error: {result.stderr.strip()[:240]}")
            try: return json.loads(result.stdout)
            except json.JSONDecodeError as exc: raise LMStudioError("invalid JSON: malformed transport response") from exc
        request = Request(self.base_url + "/chat/completions", data=encoded.encode("utf-8"), headers={"Content-Type": "application/json", "Connection": "close"}, method="POST")
        try:
            with urlopen(request, timeout=READ_TIMEOUT_SECONDS) as response: return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc: raise LMStudioError(f"HTTP error {exc.code}: {exc.read().decode(errors='replace')[:240]}") from exc
        except URLError as exc: raise LMStudioError(f"connection timeout/error: {exc.reason}") from exc
        except TimeoutError as exc: raise LMStudioError(f"read timeout: LM Studio exceeded {READ_TIMEOUT_SECONDS} seconds") from exc
        except json.JSONDecodeError as exc: raise LMStudioError("invalid JSON: malformed response") from exc

    def chat_json(self, payload: dict) -> dict:
        started = time.perf_counter()
        prompt = "Только JSON, без markdown и пояснений. Не выдумывай факты; используй только evidence. Верни компактный объект строго с ключами clinic_summary, observations, potential_opportunities, personalization_points, recommended_angle, confidence. Массивы максимум по 2 коротких пункта; неизвестное обозначай uncertain. Значения на русском."
        body = {"model": self.model, "temperature": 0.0, "max_tokens": 500, "messages": [{"role": "user", "content": prompt + "\nДанные:\n" + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))}]}
        try:
            data = self._request(body)
            try: content = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc: raise LMStudioError("missing content: choices[0].message.content") from exc
            if isinstance(content, list): content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
            content = str(content).replace("```json", "").replace("```", "").strip(); start, end = content.find("{"), content.rfind("}")
            if start < 0 or end < start: raise LMStudioError(f"model error: no JSON object in content; raw={content[:320]!r}")
            try: result = json.loads(content[start:end + 1])
            except json.JSONDecodeError as exc: raise LMStudioError("invalid JSON: malformed model response") from exc
            if not isinstance(result, dict): raise LMStudioError("invalid JSON: expected an object")
            print(f"[LM] clinic={payload.get('clinic', {}).get('name', 'unknown')} model={self.model} latency={time.perf_counter()-started:.2f}s status=success", flush=True)
            return result
        except Exception as exc:
            print(f"[LM] clinic={payload.get('clinic', {}).get('name', 'unknown')} model={self.model} latency={time.perf_counter()-started:.2f}s status=fail error={exc}", flush=True); raise

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
            if isinstance(content, list):
                content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
            content = str(content).replace("```json", "").replace("```", "").strip()
            start, end = content.find("{"), content.rfind("}")
            if start < 0 or end < start:
                raise LMStudioError("draft response did not contain JSON")
            try:
                result = json.loads(content[start:end + 1])
            except json.JSONDecodeError as exc:
                raise LMStudioError("draft response contained malformed JSON") from exc
            required = {"subject", "body", "rationale", "source_observations", "confidence"}
            if not isinstance(result, dict) or set(result) != required:
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
            print(f"[LM] stage=draft company={(payload.get('clinic') or {}).get('name', 'unknown')} model={self.model} latency={time.perf_counter()-started:.2f}s status=fail error={exc}", flush=True)
            raise

    def chat_opportunity(self, payload: dict) -> dict:
        prompt = ("Только JSON. Ты анализируешь B2B lead по переданным evidence. Никогда не выдумывай факты, владельцев, технологии или отсутствие функций. "
                  "Разделяй CONFIRMED, INFERRED, UNKNOWN и NOT_DETECTED. В evidence_ids используй только точные evidence_id из входных данных; указывай только evidence, которое напрямую поддерживает claim. "
                  "Различай chat_widget, обычный chatbot и ai_assistant: наличие чата не доказывает использование AI; если виджет есть, а AI не подтверждён — ai_assistant должен быть UNKNOWN с объяснением. "
                  "Не считай HTTP-доступность подтверждением booking, CRM или AI. Верни ключи: company_summary, digital_state, priority, why_this_lead, recommended_angle, sales_brief. "
                  "digital_state должен содержать website,mobile,online_booking,crm,chat_widget,ai_assistant,online_payment,automation; каждый объект: status, reason, evidence_ids, confidence. priority: score и website_opportunity,booking_opportunity,crm_opportunity,ai_opportunity,automation_opportunity. ")
        body = {"model": self.model, "temperature": 0.0, "max_tokens": 900, "messages": [{"role": "user", "content": prompt + "\nEVIDENCE:\n" + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))}]}
        print(f"[LM] stage=opportunity company={payload.get('company', {}).get('name', 'unknown')} model={self.model} payload_chars={len(json.dumps(payload, ensure_ascii=False))} timeout={READ_TIMEOUT_SECONDS}s evidence_count={len(payload.get('evidence', []))}", flush=True)
        data = self._request(body)
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        if isinstance(content, list): content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        content = str(content).replace("```json", "").replace("```", "").strip(); start, end = content.find("{"), content.rfind("}")
        if start < 0 or end < start: raise LMStudioError("opportunity response did not contain JSON")
        try: result = json.loads(content[start:end + 1])
        except json.JSONDecodeError as exc: raise LMStudioError("opportunity response contained malformed JSON") from exc
        if not isinstance(result, dict): raise LMStudioError("opportunity response must be a JSON object")
        print(f"[LM] stage=opportunity company={payload.get('company', {}).get('name', 'unknown')} status=response_received output_chars={len(content)}", flush=True)
        return result

    def health(self) -> dict:
        request = Request(self.base_url + "/models")
        with urlopen(request, timeout=5) as response: return json.loads(response.read().decode("utf-8"))
