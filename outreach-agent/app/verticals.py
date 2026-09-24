from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VerticalConfig:
    key: str
    label: str
    search_terms: tuple[str, ...]
    opportunity_signals: tuple[str, ...]


VERTICALS = {
    "dental": VerticalConfig("dental", "Стоматологические клиники", ("стоматология", "стоматологическая клиника"), ("online_booking", "ai_assistant", "crm", "online_payment", "patient_communication")),
    "detailing": VerticalConfig("detailing", "Detailing centers", ("детейлинг", "автодетейлинг"), ("service_catalog", "booking", "whatsapp", "before_after", "crm", "follow_up", "online_payment")),
}


def get_vertical(name: str) -> VerticalConfig:
    try:
        return VERTICALS[name.lower()]
    except KeyError as exc:
        raise ValueError(f"Unknown vertical: {name}. Available: {', '.join(sorted(VERTICALS))}") from exc
