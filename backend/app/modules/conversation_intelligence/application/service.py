from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from app.modules.conversation_intelligence.domain.models import (
    ConstructionIntent,
    ProjectContext,
    RoutedTurn,
)
from app.modules.customer_understanding.infrastructure.location import fold_text

AREA_PATTERN = re.compile(r"(?<!\d)(\d[\d.,]*)\s*(?:m2|m²|metrekare)", re.IGNORECASE)
FLOOR_PATTERN = re.compile(r"(?<!\d)(\d{1,3})\s*(?:kat|katli|katlı)\b", re.IGNORECASE)
CREW_PATTERN = re.compile(r"(?<!\d)(\d{1,4})\s*(?:kisi|kişi|personel|isci|işçi)\b", re.IGNORECASE)


class StructuredIntentRouter:
    """Construction-first deterministic router; LLM is not needed for obvious tool intents."""

    def route(self, text: str, context: ProjectContext) -> RoutedTurn:
        folded = fold_text(text)
        updates = self._extract_context(text, folded, context)
        intent, confidence = self._intent(folded)
        if intent in {ConstructionIntent.SITE_SCHEDULE, ConstructionIntent.CREW_PLANNING}:
            updates["goal"] = "site_schedule"
        assumptions: list[str] = []
        if updates.get("project_type") == "RESIDENTIAL" and "konut" not in folded:
            assumptions.append("Yapı türü aksi belirtilene kadar klasik konut kabul edildi.")
        return RoutedTurn(intent, confidence, updates, tuple(assumptions))

    @staticmethod
    def _intent(folded: str) -> tuple[ConstructionIntent, float]:
        priority: tuple[tuple[ConstructionIntent, tuple[str, ...]], ...] = (
            (ConstructionIntent.CONTACT, ("telefon", "whatsapp", "yetkili numara", "iletisim")),
            (
                ConstructionIntent.SITE_SCHEDULE,
                (
                    "is program",
                    "iş program",
                    "takvim",
                    "kac gun",
                    "kaç gün",
                    "ne kadar surer",
                    "ne kadar sürer",
                ),
            ),
            (
                ConstructionIntent.CREW_PLANNING,
                (
                    "kac kisi",
                    "kaç kişi",
                    "ekip plan",
                    "personeli",
                    "personel plan",
                    "isci say",
                    "işçi say",
                ),
            ),
            (ConstructionIntent.IFC, ("ifc",)),
            (ConstructionIntent.DXF, ("dxf", "dwg")),
            (ConstructionIntent.PDF, ("pdf", "pafta")),
            (ConstructionIntent.DOCUMENT_ANALYSIS, ("dosya analiz", "proje oku", "cizim oku")),
            (
                ConstructionIntent.REGULATION,
                ("yonetmelik", "mevzuat", "kanun", "sartname", "standart", "tbdy", "ts500"),
            ),
            (
                ConstructionIntent.SAFETY,
                ("is guven", "iş güven", "isg", "risk deger", "risk değer"),
            ),
            (ConstructionIntent.QUALITY, ("kalite", "kusur", "ncr", "kontrol plan")),
            (
                ConstructionIntent.CONTRACT,
                ("sozlesme", "sözleşme", "hak edis", "hakediş", "claim", "fidic"),
            ),
            (ConstructionIntent.QUOTE, ("teklif", "birim fiyat", "fiyati", "fiyatı")),
            (ConstructionIntent.COST, ("maliyet", "kaca yap", "kaça yap", "butce", "bütçe")),
            (ConstructionIntent.QUANTITY, ("metraj", "miktar", "kac m2", "kaç m2")),
            (
                ConstructionIntent.STRUCTURAL,
                ("statik", "betonarme", "kolon", "kiris", "kiriş", "donati", "donatı"),
            ),
            (
                ConstructionIntent.MEP,
                ("mekanik", "elektrik", "tesisat", "hvac", "yangin tesis", "yangın tesis"),
            ),
            (
                ConstructionIntent.ARCHITECTURE,
                ("mimari", "ruhsat", "mahal", "kat plani", "kat planı"),
            ),
            (ConstructionIntent.RENOVATION, ("tadilat", "renovasyon", "yenileme")),
            (
                ConstructionIntent.NEW_BUILD,
                ("yapacag", "yapacağ", "yeni bina", "yeni insaat", "yeni inşaat"),
            ),
            (
                ConstructionIntent.SITE_MANAGEMENT,
                ("santiye", "şantiye", "ilerleme", "gunluk rapor", "günlük rapor"),
            ),
        )
        for intent, markers in priority:
            if any(marker in folded for marker in markers):
                return intent, 0.98
        if any(marker in folded for marker in ("insaat", "inşaat", "yapi", "yapı")):
            return ConstructionIntent.GENERAL_CONSTRUCTION, 0.80
        return ConstructionIntent.UNKNOWN, 0.40

    @staticmethod
    def _extract_context(text: str, folded: str, context: ProjectContext) -> dict[str, Any]:
        updates: dict[str, Any] = {}
        area_match = AREA_PATTERN.search(text)
        floor_match = FLOOR_PATTERN.search(text)
        if floor_match:
            updates["floor_count"] = int(floor_match.group(1))
        if area_match:
            area = StructuredIntentRouter._decimal(area_match.group(1))
            if "brut" in folded or "brüt" in folded or "toplam alan" in folded:
                updates["gross_area_m2"] = area
            else:
                updates["footprint_m2"] = area
        footprint = updates.get("footprint_m2", context.footprint_m2)
        floors = updates.get("floor_count", context.floor_count)
        if (
            footprint
            and floors
            and not updates.get("gross_area_m2")
            and context.gross_area_m2 is None
        ):
            updates["gross_area_m2"] = footprint * Decimal(floors)
            updates.setdefault("facts", {})["gross_area_source"] = "DERIVED_FOOTPRINT_X_FLOORS"
        residential_marker = any(marker in folded for marker in ("konut", "apartman", "rezidans"))
        if residential_marker or (context.project_type is None and (area_match or floor_match)):
            updates["project_type"] = "RESIDENTIAL"
        if any(marker in folded for marker in ("betonarme", "beton arme")):
            updates["structural_system"] = "REINFORCED_CONCRETE"
        if any(
            marker in folded for marker in ("temel tamamlandi", "temel tamamlandı", "temel bitti")
        ):
            updates["stage"] = "FOUNDATION_COMPLETED"
        elif "kaba insaat bitti" in folded or "kaba inşaat bitti" in folded:
            updates["stage"] = "ROUGH_CONSTRUCTION_COMPLETED"
        elif "temel" in folded and context.stage is None:
            updates["stage"] = "FOUNDATION"
        if "istanbul" in folded:
            updates["location"] = "İstanbul"
        crew_match = CREW_PATTERN.search(text)
        if crew_match:
            updates["available_crew"] = int(crew_match.group(1))
        if any(marker in folded for marker in ("personeli siz", "ekibi siz", "personel sizin")):
            updates.setdefault("facts", {})["crew_planning_delegated"] = True
        return updates

    @staticmethod
    def _decimal(raw: str) -> Decimal:
        normalized = raw.replace(" ", "")
        if "." in normalized and len(normalized.rsplit(".", 1)[1]) == 3:
            normalized = normalized.replace(".", "")
        elif "," in normalized and len(normalized.rsplit(",", 1)[1]) == 3:
            normalized = normalized.replace(",", "")
        else:
            normalized = normalized.replace(",", ".")
        return Decimal(normalized)
