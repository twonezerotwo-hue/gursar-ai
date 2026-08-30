from __future__ import annotations

import re
import unicodedata
from decimal import Decimal

from app.modules.estimation.domain.models import (
    BuildingInput,
    BuildingUse,
    EstimationResult,
    FoundationType,
    SeismicDemand,
    SoilCondition,
    StructuralSystem,
)

AREA_PATTERN = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:m2|metrekare)(?:\s+oturum)?")
FLOOR_PATTERN = re.compile(r"(\d+)\s*kat")
BASEMENT_PATTERN = re.compile(r"(\d+)\s*(?:bodrum|bodrum kat)")
HEIGHT_PATTERN = re.compile(r"(\d+(?:[.,]\d+)?)\s*m(?:etre)?\s*kat yuksekligi")
ESTIMATION_INTENT_MARKERS = (
    "metraj",
    "kabataslak hesapla",
    "kabaca hesapla",
    "yaklasik hesapla",
    "miktarlarini hesapla",
)
COMMERCIAL_MARKERS = ("iscilik", "birim fiyat", "fiyati", "tl/m2", "tl m2")


def _fold(value: str) -> str:
    value = value.casefold().translate(
        str.maketrans({"ı": "i", "ş": "s", "ğ": "g", "ü": "u", "ö": "o", "ç": "c"})
    )
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(character)
    )


def parse_building_estimation_request(text: str) -> BuildingInput | None:
    folded = _fold(text)
    if any(marker in folded for marker in COMMERCIAL_MARKERS):
        return None
    area_match = AREA_PATTERN.search(folded)
    floor_match = FLOOR_PATTERN.search(folded)
    if area_match is None or floor_match is None:
        return None

    footprint = Decimal(area_match.group(1).replace(",", "."))
    floors = int(floor_match.group(1))
    basement_match = BASEMENT_PATTERN.search(folded)
    height_match = HEIGHT_PATTERN.search(folded)
    building_use = BuildingUse.RESIDENTIAL
    for marker, candidate in (
        ("hastane", BuildingUse.HOSPITAL),
        ("otel", BuildingUse.HOTEL),
        ("ofis", BuildingUse.OFFICE),
        ("avm", BuildingUse.MALL),
        ("otopark", BuildingUse.PARKING),
        ("yurt", BuildingUse.DORMITORY),
        ("sanayi", BuildingUse.INDUSTRIAL),
        ("karma", BuildingUse.MIXED_USE),
        ("konut", BuildingUse.RESIDENTIAL),
    ):
        if marker in folded:
            building_use = candidate
            break
    structural_system = StructuralSystem.FRAME_PLUS_SHEAR_WALL
    for marker, candidate in (
        ("mantar doseme", StructuralSystem.FLAT_SLAB),
        ("asmolen", StructuralSystem.RIBBED_SLAB),
        ("nervurlu", StructuralSystem.RIBBED_SLAB),
        ("kaset doseme", StructuralSystem.WAFFLE),
        ("prekast", StructuralSystem.PRECAST),
        ("perde agirlikli", StructuralSystem.SHEAR_WALL_DOMINANT),
    ):
        if marker in folded:
            structural_system = candidate
            break
    foundation_type = FoundationType.UNKNOWN
    for marker, candidate in (
        ("kazikli radye", FoundationType.PILED_RAFT),
        ("radye", FoundationType.RAFT),
        ("münferit", FoundationType.ISOLATED),
        ("munferit", FoundationType.ISOLATED),
        ("surekli temel", FoundationType.STRIP),
        ("kazik temel", FoundationType.PILE),
    ):
        if marker in folded:
            foundation_type = candidate
            break
    return BuildingInput(
        footprint_area_m2=footprint,
        above_ground_floors=floors,
        basement_floors=int(basement_match.group(1)) if basement_match else 0,
        building_use=building_use,
        floor_height_m=(Decimal(height_match.group(1).replace(",", ".")) if height_match else None),
        structural_system=structural_system,
        foundation_type=foundation_type,
        soil_condition=SoilCondition.UNKNOWN,
        seismic_demand=SeismicDemand.UNKNOWN,
    )


def is_building_estimation_intent(text: str) -> bool:
    folded = _fold(text)
    return not any(marker in folded for marker in COMMERCIAL_MARKERS) and any(
        marker in folded for marker in ESTIMATION_INTENT_MARKERS
    )


def render_estimation_intake(text: str) -> str:
    folded = _fold(text)
    has_area = AREA_PATTERN.search(folded) is not None
    has_floors = FLOOR_PATTERN.search(folded) is not None
    if has_area and not has_floors:
        missing = "kat sayısını"
    elif has_floors and not has_area:
        missing = "oturum alanını m² olarak"
    else:
        missing = "oturum alanını (m²) ve kat sayısını"
    return (
        f"Metrajı hemen yaklaşık olarak hesaplayabilirim. Lütfen {missing} yazın. "
        "Aksi belirtilmedikçe klasik betonarme konut, 3,00 m kat yüksekliği, "
        "normal açıklık ve bodrumsuz yapı kabul edeceğim."
    )


def render_estimation_response(result: EstimationResult) -> str:
    selected = {item.code: item for item in result.items}
    use_labels = {
        "residential": "Klasik konut",
        "office": "Ofis",
        "hotel": "Otel",
        "hospital": "Hastane",
        "mall": "Ticari yapı/AVM",
        "parking": "Otopark",
    }
    system_labels = {
        "frame_plus_shear_wall": "betonarme çerçeve + perde",
        "moment_frame": "betonarme çerçeve",
        "shear_wall_dominant": "perde ağırlıklı betonarme",
        "flat_slab": "mantar döşeme",
        "ribbed_slab": "asmolen/nervürlü döşeme",
        "waffle": "kaset döşeme",
        "precast": "prekast",
    }
    lines = [
        f"{_number(result.above_ground_gross_area_m2)} m² brüt alan için erken aşama tahmini:",
        (
            "Yapı kabulü: "
            f"{use_labels.get(result.building_use.value, result.building_use.value)}, "
            f"{system_labels.get(result.structural_system.value, result.structural_system.value)}"
        ),
        "",
    ]
    for code, label in (
        ("concrete", "Beton"),
        ("reinforcement", "Demir"),
        ("formwork", "Kalıp"),
        ("masonry", "Duvar"),
        ("gross_facade", "Brüt cephe"),
        ("opaque_facade", "Net opak cephe"),
    ):
        item = selected.get(code)
        if item is None:
            continue
        band = item.quantity
        if band.low == band.high:
            lines.append(f"{label}: ≈ {_number(band.base)} {band.unit}")
        else:
            lines.append(
                f"{label}: ≈ {_number(band.low)}–{_number(band.high)} {band.unit} "
                f"(merkez: {_number(band.base)})"
            )
    lines.extend(
        (
            "",
            f"Tahmin güveni: {result.confidence.label.value} "
            f"(ESTIMATION_CONFIDENCE {result.confidence.estimation_confidence})",
            "Varsayımlar: " + ", ".join(_assumption_label(item) for item in result.assumptions),
            "Bu sonuç yaklaşık mühendislik tahminidir; kesin proje metrajı değildir.",
        )
    )
    if "raft_thickness=missing_foundation_quantity_not_added" in result.assumptions:
        lines.insert(
            -1,
            "Radye kalınlığı verilmediği için temel betonu ayrı geometrik kaleme eklenmedi.",
        )
    return "\n".join(lines)


def _number(value: Decimal) -> str:
    rounded = value.quantize(Decimal("0.1"))
    formatted = f"{rounded:,.1f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return formatted.removesuffix(",0")


def _assumption_label(value: str) -> str:
    labels = {
        "building_use=residential": "klasik konut",
        "floor_height=3.00m": "kat yüksekliği 3,00 m",
        "structural_system=frame_plus_shear_wall": "betonarme çerçeve + perde",
        "average_span=4.50m": "normal açıklık 4,50 m",
        "soil_condition=normal": "normal zemin",
        "seismic_demand=medium_assumed": "deprem talebi orta kabul edildi",
        "no_basement": "bodrum yok",
        "footprint_geometry=square": "oturum kareye yakın",
        "facade_opening_ratio=0.25": "cephe açıklık oranı %25",
        "raft_thickness=missing_foundation_quantity_not_added": "radye kalınlığı bilinmiyor",
    }
    return labels.get(value, value)
