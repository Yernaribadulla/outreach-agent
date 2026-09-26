from __future__ import annotations

import json
from ..analysis.lm_studio import LMStudioClient


def generate_draft(client: LMStudioClient, clinic: dict, analysis: dict, config: dict) -> dict:
    prompt = {"task": "Сгенерируй короткое профессиональное B2B-письмо на русском языке объёмом примерно 100–180 слов", "clinic": clinic, "analysis": analysis, "sender": config, "rules": ["Используй только наблюдения из входных данных.", "Не называй клинику устаревшей без доказательств.", "Обязательно включи в body точную demo-ссылку https://yernaribadulla.github.io/Dentist_rus_commercial/.", "Не добавляй никаких других URL или email, кроме подтверждённых входными данными.", "Добавь один мягкий CTA.", "Ответ обязан быть JSON ровно с ключами subject, body, rationale, source_observations, confidence.", "subject — строка темы письма; body — полный текст письма; не возвращай clinic_summary, observations или digital_state вместо письма."]}
    result = client.chat_json(prompt)
    result.setdefault("source_observations", analysis.get("observations", [])); result.setdefault("confidence", analysis.get("confidence", 0))
    return result
