from __future__ import annotations

from decimal import Decimal

from app.contracts.v1.quick_quote import QuickQuoteV1

UNIT_LABELS = {
    "tr": {"m2": "m²", "m3": "m³", "m": "m", "adet": "adet"},
    "en": {"m2": "m²", "m3": "m³", "m": "m", "adet": "pcs"},
}


def format_number(value: Decimal, locale: str, *, money: bool = False) -> str:
    decimals = 2 if money and value != value.to_integral() else 0
    rendered = f"{value:,.{decimals}f}"
    if locale == "tr":
        rendered = rendered.replace(",", "_").replace(".", ",").replace("_", ".")
    return rendered


def unit_label(unit: str, locale: str) -> str:
    return UNIT_LABELS.get(locale, UNIT_LABELS["en"]).get(unit, unit)


def render_quote_response(quotes: list[QuickQuoteV1], locale: str) -> str:
    if not quotes:
        if locale == "en":
            return "Which construction work would you like us to price?"
        return "Hangi yapım işini fiyatlandırmamızı istersiniz?"

    if len(quotes) == 1:
        return _render_single(quotes[0], locale)

    sections: list[str] = []
    for index, quote in enumerate(quotes, start=1):
        title = f"**{index}. {quote.location_label} · {quote.service_label}**"
        sections.append(f"{title}\n{_render_price_line(quote, locale)}")
    return "\n\n".join(sections)


def _render_single(quote: QuickQuoteV1, locale: str) -> str:
    if quote.status == "SERVICE_CLARIFICATION_REQUIRED":
        if quote.clarification_question:
            return quote.clarification_question
        if locale == "en":
            return "Which construction work would you like us to price?"
        return "Hangi yapım işini fiyatlandırmamızı istersiniz?"
    if quote.status == "PRICE_NOT_CONFIGURED":
        if locale == "en":
            return "I need to confirm the current price for this work. I have saved your scope."
        metraj = " Metrajınızı kaydettim." if quote.quantity is not None else ""
        return f"Bu iş için güncel fiyatı netleştirip teklif kapsamına eklemem gerekiyor.{metraj}"

    if quote.quantity is not None and quote.total_price is not None:
        return f"**{_render_price_line(quote, locale)}**"

    if locale == "en":
        return (
            f"Our current turnkey unit price for {quote.service_label} is:\n"
            f"**{format_number(quote.unit_price or Decimal(0), locale, money=True)} "
            f"{quote.currency}/{unit_label(quote.unit, locale)} + VAT.**\n\n"
            f"{quote.scope_summary}\n\nWhat is the quantity?"
        )

    return (
        f"{quote.service_label} anahtar teslim güncel fiyatımız:\n"
        f"**{format_number(quote.unit_price or Decimal(0), locale, money=True)} "
        f"TL/{unit_label(quote.unit, locale)} + KDV.**\n\n"
        f"{quote.scope_summary}\n\nKaç {unit_label(quote.unit, locale)} iş var?"
    )


def _render_price_line(quote: QuickQuoteV1, locale: str) -> str:
    if quote.status == "SERVICE_CLARIFICATION_REQUIRED":
        return "Service needs clarification." if locale == "en" else "İş türünü netleştirelim."
    if quote.status == "PRICE_NOT_CONFIGURED":
        return (
            "Current price requires confirmation."
            if locale == "en"
            else "Güncel fiyat teklif kapsamında netleştirilecek."
        )
    if quote.quantity is not None and quote.total_price is not None:
        tax = " + VAT" if locale == "en" else " + KDV"
        currency = quote.currency if locale == "en" else "TL"
        return (
            f"{format_number(quote.quantity, locale)} {unit_label(quote.unit, locale)} × "
            f"{format_number(quote.unit_price or Decimal(0), locale, money=True)} "
            f"{currency}/{unit_label(quote.unit, locale)} = "
            f"{format_number(quote.total_price, locale, money=True)} {currency}{tax}"
        )
    return (
        f"{format_number(quote.unit_price or Decimal(0), locale, money=True)} "
        f"{quote.currency}/{unit_label(quote.unit, locale)}"
    )
