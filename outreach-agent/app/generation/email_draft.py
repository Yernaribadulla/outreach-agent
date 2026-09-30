from __future__ import annotations

from html import escape
import re
from ..analysis.lm_studio import LMStudioClient

_DENTAL_OFFER = {
    "website": "современный сайт для клиники",
    "ai_assistant": "ИИ-ассистент для ответов на вопросы пациентов",
    "online_booking": "онлайн-запись и бронирование",
    "online_payment": "онлайн-оплата",
    "automation": "автоматизация процессов и интеграции",
}
_FEATURE_PATTERNS = {
    "website": re.compile(r"\bсайт\w*|\bwebsite\b", re.I),
    "ai_assistant": re.compile(r"ии[- ]?ассистент\w*|ai assistant|чат.?бот\w*|помощник\w*", re.I),
    "online_booking": re.compile(r"запис\w*|бронир\w*|\bbooking\b", re.I),
    "online_payment": re.compile(r"оплат\w*|\bpayment\b", re.I),
    "automation": re.compile(r"автоматизац\w*|интеграц\w*|\bautomation\b", re.I),
    "crm": re.compile(r"\bcrm\b", re.I),
}
_FEATURE_REMOVE_PATTERNS = {
    "website": re.compile(r"\b(?:современ\w*\s+)?сайт\w*|\bwebsite\b", re.I),
    "ai_assistant": re.compile(r"ии[- ]?ассистент\w*|ai assistant|чат.?бот\w*|помощник\w*", re.I),
    "online_booking": re.compile(r"(?:онлайн[- ]?)?(?:запис\w*|бронир\w*)|\bbooking\b", re.I),
    "online_payment": re.compile(r"(?:онлайн[- ]?)?оплат\w*|\bpayment\b", re.I),
    "automation": re.compile(r"автоматизац\w*|интеграц\w*|\bautomation\b", re.I),
    "crm": re.compile(r"\bcrm\b", re.I),
}
_PRIMARY_OFFER_LINES = {
    "website": "Могу помочь с разработкой или развитием сайта клиники.",
    "ai_assistant": "Могу предложить ИИ-ассистента для ответов на вопросы пациентов.",
    "online_booking": "Можно обсудить, как встроить онлайн-запись в цифровой путь пациентов.",
    "online_payment": "При необходимости можно обсудить онлайн-оплату.",
    "automation": "При необходимости можно обсудить автоматизацию процессов и интеграции.",
}
_UNSUPPORTED_COPY = (
    (re.compile(r"у вас нет|отсутству\w*|не обнаружен\w*|не предусмотрен\w*|не используете|нет онлайн[- ]?(?:запис|оплат)", re.I), "asserts a feature is absent"),
    (re.compile(r"гарантир\w*|гарантированно|увелич\w*.{0,35}(?:выручк|пациент|запис)|(?:рост|роста).{0,35}(?:выручк|пациент|запис)", re.I), "promises an unsupported outcome"),
    (re.compile(r"без сложн\w*.{0,24}(?:изменен|настроек|работ)|без необходимости.{0,20}измен", re.I), "asserts unsupported implementation effort"),
    (re.compile(r"отличн\w* старт|прекрасн\w* клиник|замечательн\w* клиник|впечатл\w* ваш|порадовал\w* ваш", re.I), "contains an unsupported compliment"),
    (re.compile(r"мы уже (?:работали|сотрудничали)|наш клиент|как мы помогли вашей клинике|ваш партнёр|ваш партнер", re.I), "claims a prior relationship"),
)


def _select_dental_offer(analysis: dict) -> tuple[str, list[str]]:
    angle = str(analysis.get("recommended_angle") or "").lower()
    angle_aliases = (
        ("automation", ("automation", "автоматизац", "интеграц")),
        ("online_payment", ("online payment", "payment", "оплат")),
        ("online_booking", ("online booking", "booking", "бронир", "запис")),
        ("ai_assistant", ("ai opportunity", "ai assistant", "ии-ассист", "чат-бот", "помощник")),
        ("website", ("website", "site opportunity", "сайт", "веб-сайт")),
    )
    primary = next((key for key, aliases in angle_aliases if any(alias in angle for alias in aliases)), None)
    if primary is None:
        priorities = analysis.get("priority") or {}
        priority_aliases = (
            ("automation", "automation_opportunity"),
            ("online_booking", "booking_opportunity"),
            ("ai_assistant", "ai_opportunity"),
            ("website", "website_opportunity"),
        )
        primary = next((key for key, flag in priority_aliases if priorities.get(flag) is True), None)
    if primary is None:
        states = analysis.get("digital_state") or {}
        primary = "ai_assistant" if (states.get("website") or {}).get("status") == "CONFIRMED" else "website"
    add_ons = {
        "website": ["ai_assistant", "online_booking"],
        "ai_assistant": ["online_booking", "automation"],
        "online_booking": ["ai_assistant", "online_payment"],
        "online_payment": ["online_booking", "ai_assistant"],
        "automation": ["ai_assistant", "online_booking"],
    }
    states = analysis.get("digital_state") or {}
    candidates = list(add_ons[primary]) + [key for key in _DENTAL_OFFER if key != primary]
    selected = []
    for key in candidates:
        claim = states.get(key) or {}
        if key != primary and claim.get("status") == "CONFIRMED":
            continue
        if key not in selected:
            selected.append(key)
        if len(selected) == 2:
            break
    return primary, selected


def _validate_generated_copy(body: str, selected_elements: set[str], demo_url: str) -> None:
    copy_for_checks = body.replace(demo_url, "") if demo_url else body
    copy_for_checks = re.sub(r"пример сайта\s*:?", "", copy_for_checks, flags=re.I)
    for pattern, reason in _UNSUPPORTED_COPY:
        if pattern.search(copy_for_checks):
            raise ValueError(f"Draft rejected by evidence guard: {reason}")
    unselected = [name for name, pattern in _FEATURE_PATTERNS.items() if pattern.search(copy_for_checks) and name not in selected_elements]
    if unselected:
        raise ValueError(f"Draft rejected by offer guard: unselected elements {', '.join(unselected)}")


def _sanitize_generated_copy(body: str, selected_elements: set[str]) -> str:
    """Remove obvious model-added fluff and offer items outside the selected angle."""
    cleaned_paragraphs = []
    for paragraph in body.replace("\r\n", "\n").split("\n"):
        cleaned_sentences = []
        for sentence in re.split(r"(?<=[.!?])\s+", paragraph):
            sentence = re.sub(
                r"\s*[—–-]\s*(?:это\s*)?(?:отличн\w*|прекрасн\w*|замечательн\w*)[^.!?]*[.!?]?",
                "",
                sentence,
                flags=re.I,
            )
            if re.search(r"без сложн\w*.{0,24}(?:изменен|настроек|работ)|без необходимости.{0,20}измен", sentence, re.I):
                continue
            sentence = re.sub(
                r"(?:как\s+)?сделать\s+[^,:;.!?]{0,80}(?:еще\s+)?более эффективн\w*",
                "варианты цифрового взаимодействия с пациентами",
                sentence,
                flags=re.I,
            )
            removed_offer = False
            for name, pattern in _FEATURE_REMOVE_PATTERNS.items():
                if name in selected_elements:
                    continue
                sentence, count = re.subn(
                    r"(?:(?:,\s*)|(?:\s+(?:и|а также|или)\s+))?" + pattern.pattern,
                    "",
                    sentence,
                    flags=re.I,
                )
                removed_offer = removed_offer or bool(count)
            sentence = re.sub(r"\s+(?:и|а также|или)\s*(?=[,.;!?])", "", sentence, flags=re.I)
            sentence = re.sub(r"\s+([,.;!?])", r"\1", sentence)
            sentence = re.sub(r"[,;:]\s*([.!?])", r"\1", sentence)
            sentence = re.sub(r"\s{2,}", " ", sentence).strip()
            sentence = re.sub(r"(?<=[а-яё])\s+(?=(?:мы|я|если|посмотрите|могу|можно)\b)", ". ", sentence, flags=re.I)
            if not sentence:
                continue
            if removed_offer and not any(_FEATURE_PATTERNS[name].search(sentence) for name in selected_elements):
                continue
            cleaned_sentences.append(sentence)
        cleaned_paragraphs.append(" ".join(cleaned_sentences).strip())
    return "\n".join(cleaned_paragraphs).strip()


def _add_dental_intro(body: str, sender_name: str) -> str:
    text = body.strip()
    text = re.sub(r"^(?:Здравствуйте|Добрый день)[!,.]?\s*", "", text, flags=re.I)
    if re.match(r"^\s*(?:меня зовут|я представляю|я разрабатываю)\b", text, re.I):
        text = re.sub(r"^\s*.*?[.!?]\s*", "", text, count=1, flags=re.S)
    text = text.strip()
    intro = f"Здравствуйте!\n\nМеня зовут {sender_name}, я разрабатываю цифровые решения для стоматологических клиник."
    return f"{intro}\n\n{text}".rstrip()


def _add_primary_offer(body: str, primary: str, sender_name: str) -> str:
    intro = f"Здравствуйте!\n\nМеня зовут {sender_name}, я разрабатываю цифровые решения для стоматологических клиник."
    if not body.startswith(intro):
        body = _add_dental_intro(body, sender_name)
    return f"{intro}\n\n{_PRIMARY_OFFER_LINES[primary]}\n\n{body[len(intro):].lstrip()}"


def _canonical_dental_close(body: str, demo_url: str) -> str:
    text = body.strip()
    if demo_url and demo_url in text:
        text = text.split(demo_url, 1)[0]
    text = re.sub(r"(?:посмотрите|смотрите|пример|демо)(?:\s+сайт|\s+прототип)?\s*[:—-]?\s*$", "", text, flags=re.I).rstrip()
    close = []
    if demo_url:
        close.append(f"Пример сайта: {demo_url}")
    close.append("Если интересно, могу показать, как это можно адаптировать именно под вашу клинику.")
    return f"{text}\n\n" + "\n\n".join(close) if text else "\n\n".join(close)


def generate_draft(client: LMStudioClient, clinic: dict, analysis: dict, config: dict) -> dict:
    is_dental = config.get("category") == "dental" or clinic.get("category") == "dental"
    selected_elements: set[str] = set()
    sender = dict(config)
    rules = [
        "Пиши короткое индивидуальное письмо, используя LEAD BRIEF, analysis, digital_state, recommended_angle, opportunities и подтверждённые evidence.",
        "Выбери один главный angle и не более двух дополнительных элементов предложения. Не перечисляй весь каталог услуг механически.",
        "CONFIRMED используй только в пределах supporting evidence. NOT_DETECTED и UNKNOWN не означают, что функции точно нет; не пиши об отсутствии функции.",
        "Если opportunity основана на NOT_DETECTED, говори только о возможном дополнении существующего процесса и только если это уместно по evidence; иначе выбери другой angle.",
        "Не выдумывай факты о клинике, проблемах, технологиях, контактах, CRM, отношениях или результатах; никаких гарантий роста выручки или числа пациентов и ложных комплиментов.",
        "Не называй сайт устаревшим и не критикуй его без прямого подтверждённого evidence. Если цифровое присутствие выглядит хорошим, выбери аккуратный дополнительный angle.",
        "Используй только подтверждённые URL/email из входных данных и demo URL из sender config. Добавь один простой CTA.",
        "Верни JSON-объект ровно с ключами subject, body, rationale, source_observations, confidence. body — полный текст письма; source_observations — только опоры из входных данных.",
    ]
    if is_dental:
        primary, add_ons = _select_dental_offer(analysis)
        selected_elements = {primary, *add_ons}
        sender["offer"] = [f"{key}: {_DENTAL_OFFER[key]}" for key in (primary, *add_ons)]
        sender["primary_offer_element"] = primary
        sender["additional_offer_elements"] = add_ons
        rules.extend([
            "Для этого письма доступны только primary_offer_element и additional_offer_elements из sender.offer. Не упоминай иные услуги/функции.",
            "Назови ровно один основной элемент и максимум два дополнительных из заданного списка. Не добавляй новые элементы даже в заключении.",
            "Основной элемент будет добавлен генератором отдельно; в body не повторяй его, а сосредоточься на evidence и дополнительных элементах из списка.",
            "Объясняй ИИ-ассистента простыми словами. Представляй перечисленные возможности как предложение автора, а не как уже внедрённые функции клиники.",
            "Не добавляй приветствие или самопрезентацию: генератор добавит проверенное представление автора с точным именем из sender config.",
            "Не добавляй demo URL и CTA: генератор добавит точную ссылку и стандартный мягкий CTA после текста.",
            "Не добавляй похвалу клинике и не утверждай, что внедрение будет простым или не потребует сложных изменений.",
        ])
        prompt_analysis = dict(analysis)
        prompt_analysis["digital_state"] = {
            key: value for key, value in (analysis.get("digital_state") or {}).items()
            if key in selected_elements
        }
        prompt_analysis["opportunities"] = [
            item for item in (analysis.get("opportunities") or [])
            if not isinstance(item, str) or not any(
                pattern.search(item) for key, pattern in _FEATURE_PATTERNS.items() if key in selected_elements
            )
        ]
        if not prompt_analysis["opportunities"]:
            prompt_analysis.pop("opportunities", None)
    else:
        prompt_analysis = analysis
    if config.get("demo_url"):
        rules.append(f"Включи в письмо точную demo-ссылку из sender config: {config['demo_url']}")
    payload = {
        "task": f"Сгенерируй короткое человеческое B2B-письмо на языке {config.get('language', 'ru')}.",
        "LEAD BRIEF": prompt_analysis.get("sales_brief") or prompt_analysis.get("lead_brief") or prompt_analysis.get("clinic_summary") or "",
        "clinic": clinic,
        "analysis": prompt_analysis,
        "sender": sender,
        "rules": rules,
    }
    # chat_json is the legacy opportunity-analysis contract; drafts have a separate
    # JSON contract so an analysis object can never be mistaken for an email.
    result = client.chat_draft(payload)
    if not isinstance(result, dict):
        raise ValueError("Draft response must be a JSON object")
    observations = list(dict.fromkeys(
        str(item["fact"]).strip()
        for item in clinic.get("evidence", [])
        if isinstance(item, dict) and item.get("status") == "CONFIRMED" and item.get("fact")
    ))
    # Keep provenance metadata source-bound; don't persist model-invented observations.
    result["source_observations"] = observations
    result.setdefault("rationale", analysis.get("recommended_angle", "Draft generated from the saved analysis and source observations."))
    result.setdefault("confidence", analysis.get("confidence", 0))
    if config.get("demo_url") and not is_dental:
        body = str(result.get("body", ""))
        if config["demo_url"] not in body:
            result["body"] = f"{body.rstrip()}\n\nПример сайта: {config['demo_url']}".strip()
    if is_dental:
        result["body"] = _sanitize_generated_copy(str(result.get("body", "")), selected_elements)
        result["body"] = _add_primary_offer(str(result.get("body", "")), primary, str(sender.get("sender_name") or "Ернар Ибадулла"))
        result["body"] = _canonical_dental_close(str(result.get("body", "")), str(config.get("demo_url") or ""))
        _validate_generated_copy(str(result.get("body", "")), selected_elements, str(config.get("demo_url") or ""))
    return result


def prepare_draft_formats(draft: dict) -> dict:
    # One canonical body guarantees plain and HTML previews carry the same content.
    draft["plain_text_body"] = str(draft.get("body", ""))
    draft["html_body"] = f'<div style="font:15px Arial,sans-serif;line-height:1.6;color:#26342f">{escape(draft["plain_text_body"]).replace(chr(10), "<br>")}</div>'
    return draft
