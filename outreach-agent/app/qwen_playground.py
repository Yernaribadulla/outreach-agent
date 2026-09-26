"""Small manual playground for raw LM Studio/Qwen experiments.

This module is intentionally independent from the autonomous pipeline: it does
not touch SQLite, discovery, qualification, drafts, or sending.
"""

from __future__ import annotations

import argparse
import json
import time
from urllib.request import Request, urlopen


MODEL = "qwen/qwen3-vl-8b"
DEFAULT_ENDPOINT = "http://127.0.0.1:1234/v1"

DOCTOR_DENT_PROMPT = """Ты анализируешь B2B lead только по переданным evidence.
Не выдумывай факты, владельцев, технологии или отсутствие функций.
Разделяй CONFIRMED, INFERRED, UNKNOWN и NOT_DETECTED.
Сохраняй evidence_ids только из входных evidence.
Верни JSON с ключами company_summary, digital_state, priority,
why_this_lead, recommended_angle и sales_brief.

EVIDENCE:
{
  "company": {
    "name": "Doctor Dent",
    "city": "Астана",
    "website": "https://doctor-dent.kz/"
  },
  "contacts": [
    {"email": "dd@doctordent.kz", "source_url": "https://doctor-dent.kz/", "kind": "public_business"},
    {"email": "info@doctor-dent.kz", "source_url": "https://doctor-dent.kz/", "kind": "generic_business"}
  ],
  "website_audit": {
    "website_status": "WEBSITE_OK",
    "http_status": 200,
    "https": true,
    "signals": {
      "mobile_friendly": true,
      "booking": true,
      "whatsapp": true,
      "online_payment": true,
      "forms": false,
      "chat": false
    },
    "evidence": [
      {"status": "CONFIRMED", "fact": "Website responds", "source": "https://doctor-dent.kz/", "confidence": "HIGH"},
      {"status": "CONFIRMED", "fact": "HTTP 200", "source": "https://doctor-dent.kz/", "confidence": "HIGH"}
    ]
  },
  "evidence": [
    {"status": "CONFIRMED", "fact": "Website responds", "source": "https://doctor-dent.kz/", "confidence": "HIGH"},
    {"status": "CONFIRMED", "fact": "HTTP 200", "source": "https://doctor-dent.kz/", "confidence": "HIGH"}
  ]
}
"""


def request_raw(endpoint: str, prompt: str, temperature: float, max_tokens: int) -> tuple[int, str, float]:
    body = {
        "model": MODEL,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    request = Request(
        endpoint.rstrip("/") + "/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    with urlopen(request, timeout=180) as response:
        status = getattr(response, "status", 200)
        raw = response.read().decode("utf-8", errors="replace")
    elapsed = time.perf_counter() - started
    try:
        parsed = json.loads(raw)
        content = parsed["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    except (ValueError, KeyError, IndexError, TypeError):
        content = raw
    return status, str(content), elapsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Raw Qwen playground for LM Studio")
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--max-tokens", type=int, default=900)
    parser.add_argument("--prompt")
    parser.add_argument("--preset", choices=("doctor-dent",))
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    args = parser.parse_args(argv)
    if args.prompt and args.preset:
        parser.error("use either --prompt or --preset")
    prompt = args.prompt
    if args.preset == "doctor-dent":
        prompt = DOCTOR_DENT_PROMPT
    if prompt is None:
        print("Enter prompt; finish with Ctrl+Z then Enter on Windows (Ctrl+D on Unix):")
        prompt = __import__("sys").stdin.read()
    if not prompt.strip():
        parser.error("prompt is empty")
    print("REQUEST")
    print(json.dumps({"endpoint": args.endpoint, "model": MODEL, "temperature": args.temperature, "max_tokens": args.max_tokens}, ensure_ascii=False, indent=2))
    print("PROMPT")
    print(prompt)
    try:
        status, content, elapsed = request_raw(args.endpoint, prompt, args.temperature, args.max_tokens)
    except Exception as exc:
        print(json.dumps({"http_status": None, "elapsed_seconds": None, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False, indent=2))
        return 1
    print("RESPONSE")
    print(json.dumps({"http_status": status, "elapsed_seconds": round(elapsed, 3)}, ensure_ascii=False, indent=2))
    print(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
