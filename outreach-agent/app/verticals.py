from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class VerticalConfig:
    key: str
    label: str
    search_terms: tuple[str, ...]
    opportunity_signals: tuple[str, ...]
    category: str
    offer: str
    sender_brand: str = ""
    demo_url: str = ""
    cta: str = "Offer a short, low-pressure discussion."
    messaging_rules: tuple[str, ...] = ("Use public business evidence only.", "Avoid pressure and unsupported claims.")


VERTICALS = {
    "dental": VerticalConfig(
        "dental", "Стоматологические клиники", ("стоматология", "стоматологическая клиника"),
        ("online_booking", "ai_assistant", "crm", "online_payment", "patient_communication"),
        "dental", "Современный сайт для клиники, ИИ-ассистент для вопросов пациентов и помощи с записью, онлайн-запись и онлайн-оплата; при необходимости — автоматизация процессов и интеграции.", "DENTARA",
        "https://yernaribadulla.github.io/Dentist_rus_commercial/",
        "Предложить коротко обсудить возможный следующий шаг.",
        ("Использовать только публичные сведения о клинике.", "Не давать медицинских или пациентских утверждений.", "Не утверждать отсутствие функции по NOT_DETECTED; предлагать её как возможное дополнение только при релевантных evidence."),
    ),
    "detailing": VerticalConfig(
        "detailing", "Detailing centers", ("детейлинг", "автодетейлинг"),
        ("service_catalog", "booking", "whatsapp", "before_after", "crm", "follow_up", "online_payment"),
        "detailing", "Digital customer experience for detailing centers",
        cta="Offer a brief conversation about the customer journey.",
        messaging_rules=("Use public business evidence only.", "Do not claim service or customer problems without evidence."),
    ),
}


def get_vertical(name: str) -> VerticalConfig:
    try:
        return VERTICALS[name.lower()]
    except KeyError as exc:
        raise ValueError(f"Unknown vertical: {name}. Available: {', '.join(sorted(VERTICALS))}") from exc


def build_search_query(vertical: str | VerticalConfig, city: str) -> str:
    config = get_vertical(vertical) if isinstance(vertical, str) else vertical
    return " ".join((*config.search_terms, city)).strip()


def sender_config(vertical: str | VerticalConfig, env: dict[str, Any] | None = None) -> dict[str, str]:
    config = get_vertical(vertical) if isinstance(vertical, str) else vertical
    values = env or {}
    prefix = config.key.upper()
    return {
        "category": config.category,
        "sender_name": str(values.get(f"{prefix}_SENDER_NAME", values.get("SENDER_NAME", "Ернар Ибадулла" if config.key == "dental" else "Sales team"))),
        "sender_brand": str(values.get(f"{prefix}_SENDER_BRAND", values.get("SENDER_BRAND", config.sender_brand))),
        "sender_contact": str(values.get(f"{prefix}_SENDER_CONTACT", values.get("SENDER_CONTACT", ""))),
        "demo_url": str(values.get(f"{prefix}_DEMO_URL", values.get("DEMO_URL", config.demo_url))),
        "offer": config.offer,
        "cta": config.cta,
        "messaging_rules": "\n".join(config.messaging_rules),
        "language": str(values.get(f"{prefix}_LANGUAGE", values.get("LANGUAGE", "ru"))),
    }
