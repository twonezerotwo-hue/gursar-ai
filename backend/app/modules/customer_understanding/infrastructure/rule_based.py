from __future__ import annotations

import re
from decimal import Decimal
from difflib import SequenceMatcher

from app.contracts.v1.customer_understanding import (
    FACADE_SERVICE_CONSTRAINT,
    CustomerUnderstandingContextV1,
    CustomerUnderstandingResultV1,
    EvidenceV1,
    JobUnderstandingV1,
    LocationV1,
    QuantityV1,
    SourceType,
)
from app.modules.customer_understanding.infrastructure.location import (
    LOCATION_ALIASES,
    fold_text,
)

SERVICE_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "formwork.labor_material",
        (
            "kalip malzemesi dahil",
            "malzeme dahil kalip",
            "malzemeli kalip",
            "kalip isciligi malzeme dahil",
        ),
    ),
    (
        "reinforcement.labor",
        ("demir isciligi", "donati isciligi", "demirci isciligi", "demir", "donati"),
    ),
    ("masonry.labor", ("duvar isciligi", "duvar orme isciligi", "duvar orme", "duvar")),
    ("formwork.labor", ("kalip isciligi", "kalip", "kalib", "formwork")),
    ("facade.insulation_paint.turnkey", ("mantolama", "isi yalitim", "yalitim boya")),
    ("facade.composite.turnkey", ("kompozit", "composite facade")),
    ("rough_construction.turnkey", ("kaba insaat", "rough construction")),
    ("architecture.design", ("mimari", "tasarim", "architecture")),
    ("site_management.licensed", ("santiye sefi", "licensed site management")),
    ("site_management.standard", ("santiye yonet", "site management")),
    ("subcontracting.general", ("taseron", "subcontract")),
    ("contracting.general", ("muteahhit", "contracting")),
    ("consultancy.technical", ("danisman", "consultancy")),
    ("construction.general", ("insaat", "construction")),
)

FUZZY_SERVICE_TOKENS = {
    "demir": "reinforcement.labor",
    "donati": "reinforcement.labor",
    "kalip": "formwork.labor",
    "duvar": "masonry.labor",
}

FACADE_AMBIGUITY_PATTERNS = (
    "dis cephe",
    "cephe isi",
    "cephe uygulamasi",
)

EXCLUSION_PATTERNS = {
    "masonry": ("duvar haric", "duvar olmayacak", "duvari cikart"),
    "access.scaffolding": ("iskeleyi ben", "iskele haric", "iskele benden"),
}

ADDITION_PATTERNS = {
    "facade.demolition": ("eski cepheyi sok", "cephe sokumu", "sokum de"),
}

QUANTITY_PATTERN = re.compile(
    r"(?<![\w])(?P<number>\d+(?:[.,]\d+)?)\s*(?P<multiplier>bin|k)?"
    r"\s*(?P<unit>m2|m²|metrekare|metre\s*kare|metre)?\b",
    re.IGNORECASE,
)


class RuleBasedCustomerUnderstandingProvider:
    @property
    def provider_name(self) -> str:
        return "rule_based.v1"

    def understand(
        self,
        message: str,
        context: CustomerUnderstandingContextV1,
    ) -> CustomerUnderstandingResultV1:
        segments = [part.strip() for part in re.split(r"[,;\n]+", message) if part.strip()]
        jobs = [
            self._understand_segment(segment, index, context)
            for index, segment in enumerate(segments or [message])
        ]
        return CustomerUnderstandingResultV1(
            detected_language=self._detect_language(message),
            jobs=jobs,
        )

    def _understand_segment(
        self,
        segment: str,
        index: int,
        context: CustomerUnderstandingContextV1,
    ) -> JobUnderstandingV1:
        folded = fold_text(segment)
        service_code = self._detect_service(folded)
        if (
            service_code is None
            and any(
                pattern in folded
                for pattern in ("malzeme dahil", "malzemeyi de ekle", "malzeme de dahil")
            )
            and len(context.jobs) == 1
            and context.jobs[0].service_code == "formwork.labor"
        ):
            service_code = "formwork.labor_material"
        quantity = self._detect_quantity(segment)
        location = self._detect_location(folded)
        constraints = []
        if service_code is None and any(
            pattern in folded for pattern in FACADE_AMBIGUITY_PATTERNS
        ):
            constraints.append(FACADE_SERVICE_CONSTRAINT)
        evidence: list[EvidenceV1] = []

        if service_code:
            evidence.append(
                EvidenceV1(
                    field_path=f"jobs[{index}].service_candidate",
                    value=service_code,
                    source_message_id=context.message_id,
                    source_type=SourceType.USER_EXPLICIT,
                    confidence=0.98,
                )
            )
        if quantity:
            evidence.append(
                EvidenceV1(
                    field_path=f"jobs[{index}].quantity",
                    value=quantity.model_dump(mode="json"),
                    source_message_id=context.message_id,
                    source_type=SourceType.USER_EXPLICIT,
                    confidence=1,
                )
            )
        if location:
            evidence.append(
                EvidenceV1(
                    field_path=f"jobs[{index}].location",
                    value=location.model_dump(mode="json"),
                    source_message_id=context.message_id,
                    source_type=SourceType.USER_EXPLICIT,
                    confidence=1,
                )
            )

        job_reference = None
        if (
            context.jobs
            and not service_code
            and quantity is not None
            and self._looks_like_quantity_only(folded)
        ):
            job_reference = context.jobs[-1].job_id

        return JobUnderstandingV1(
            job_reference=job_reference,
            service_candidate=service_code,
            service_confidence=0.98 if service_code else 0,
            quantity=quantity,
            location=location,
            customer_additions=self._matching_scope_items(folded, ADDITION_PATTERNS),
            customer_exclusions=self._matching_scope_items(folded, EXCLUSION_PATTERNS),
            constraints=constraints,
            evidence=evidence,
        )

    @staticmethod
    def _detect_service(folded: str) -> str | None:
        if "kaba insaat" in folded:
            return "rough_construction.turnkey"
        for service_code, patterns in SERVICE_PATTERNS:
            if any(pattern in folded for pattern in patterns):
                return service_code
        for token in re.findall(r"[a-z0-9]+", folded):
            if len(token) < 4:
                continue
            for expected, service_code in FUZZY_SERVICE_TOKENS.items():
                if SequenceMatcher(None, token, expected).ratio() >= 0.78:
                    return service_code
        return None

    @staticmethod
    def _looks_like_quantity_only(folded: str) -> bool:
        remainder = QUANTITY_PATTERN.sub("", folded)
        remainder = re.sub(r"\b(olsun|hesapla|hesaplayalim|yap|metraj)\b", "", remainder)
        return not remainder.strip(" .,;:-_+")

    @staticmethod
    def _detect_quantity(segment: str) -> QuantityV1 | None:
        match = QUANTITY_PATTERN.search(fold_text(segment))
        if not match:
            return None
        raw = match.group("number")
        multiplier = match.group("multiplier")
        value = RuleBasedCustomerUnderstandingProvider._parse_number(raw)
        if multiplier in {"bin", "k"}:
            value *= Decimal("1000")
        return QuantityV1(value=value, unit="m2")

    @staticmethod
    def _parse_number(raw: str) -> Decimal:
        if "." in raw and len(raw.rsplit(".", 1)[1]) == 3:
            raw = raw.replace(".", "")
        elif "," in raw and len(raw.rsplit(",", 1)[1]) == 3:
            raw = raw.replace(",", "")
        else:
            raw = raw.replace(",", ".")
        return Decimal(raw)

    @staticmethod
    def _detect_location(folded: str) -> LocationV1 | None:
        for alias, normalized in LOCATION_ALIASES.items():
            if alias in folded:
                parts = normalized.code.split("/")
                return LocationV1(
                    country=parts[0],
                    city=parts[1].title() if len(parts) > 1 else None,
                    district=parts[2].title() if len(parts) > 2 else None,
                    locality=parts[3].title() if len(parts) > 3 else None,
                    code=normalized.code,
                    display_name=normalized.display_name,
                )
        return None

    @staticmethod
    def _matching_scope_items(
        folded: str,
        patterns_by_code: dict[str, tuple[str, ...]],
    ) -> list[str]:
        return [
            code
            for code, patterns in patterns_by_code.items()
            if any(pattern in folded for pattern in patterns)
        ]

    @staticmethod
    def _detect_language(message: str) -> str:
        folded = fold_text(message)
        turkish_markers = ("kac", "kalip", "insaat", "cephe", "metre", "hariç", "haric")
        english_markers = ("formwork", "construction", "facade", "price", "square")
        has_turkish = any(marker in folded for marker in turkish_markers)
        has_english = any(marker in folded for marker in english_markers)
        return "en" if has_english and not has_turkish else "tr"
