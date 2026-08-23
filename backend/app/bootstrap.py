from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import Base
from app.core.persistence import new_id
from app.infrastructure import model_registry as _model_registry  # noqa: F401
from app.modules.pricing.domain.models import NewPriceVersion
from app.modules.pricing.infrastructure.repository import PriceBookRepository
from app.modules.qr.infrastructure.repository import QRSourceRepository
from app.modules.service_catalog.domain.models import ServiceDefinition
from app.modules.service_catalog.infrastructure.repository import ServiceCatalogRepository
from app.modules.work_package.domain.models import (
    PricingRole,
    SupplyBy,
    WorkPackage,
    WorkPackageItem,
)
from app.modules.work_package.infrastructure.repository import WorkPackageRepository

SERVICES = (
    ("formwork.standard.turnkey", "Standart Kalıp", "Standard Formwork"),
    ("rough_construction.turnkey", "Kaba İnşaat", "Rough Construction"),
    ("facade.composite.turnkey", "Kompozit Cephe", "Composite Facade"),
    ("facade.insulation_paint.turnkey", "Mantolama ve Cephe Boyası", "Insulation and Paint"),
    ("architecture.design", "Mimari Tasarım", "Architectural Design"),
    ("construction.general", "Genel İnşaat", "General Construction"),
    ("site_management.standard", "Şantiye Yönetimi", "Site Management"),
    ("site_management.licensed", "Yetkili Şantiye Yönetimi", "Licensed Site Management"),
    ("contracting.general", "Müteahhitlik", "Contracting"),
    ("subcontracting.general", "Taşeronluk", "Subcontracting"),
    ("consultancy.technical", "Teknik Danışmanlık", "Technical Consultancy"),
)


def initialize_database(engine: Engine, factory: sessionmaker[Session]) -> None:
    Base.metadata.create_all(engine)
    with factory.begin() as session:
        seed_reference_data(session)


def seed_reference_data(session: Session) -> None:
    catalog = ServiceCatalogRepository(session)
    for code, label_tr, label_en in SERVICES:
        catalog.add_if_missing(
            ServiceDefinition(
                code=code,
                labels={"tr": label_tr, "en": label_en, "de": label_en, "nl": label_en},
                version=1,
                is_active=True,
            )
        )

    packages = WorkPackageRepository(session)
    for package in _initial_work_packages():
        packages.add_if_missing(package)

    prices = PriceBookRepository(session)
    _seed_price(
        prices,
        service_code="formwork.standard.turnkey",
        location_scope="TR/ISTANBUL",
        price=Decimal("1350"),
        unit="m2",
        notes="V1 internal price-book seed from approved formwork scenario.",
    )
    _seed_price(
        prices,
        service_code="facade.composite.turnkey",
        location_scope="TR/ISTANBUL/EYUPSULTAN/GOKTURK",
        price=Decimal("2950"),
        unit="m2",
        notes="V1 internal demonstration price for the configured Göktürk package.",
    )
    _seed_price(
        prices,
        service_code="facade.insulation_paint.turnkey",
        location_scope="TR/ISTANBUL/AVCILAR",
        price=Decimal("1850"),
        unit="m2",
        notes="V1 internal demonstration price for the configured Avcılar package.",
    )

    QRSourceRepository(session).add_if_missing(
        code="CARD001",
        source_type="BUSINESS_CARD",
        display_name="GÜRSAR Kartvizit",
        metadata={"campaign": "business-card-v1"},
    )


def _seed_price(
    repository: PriceBookRepository,
    *,
    service_code: str,
    location_scope: str,
    price: Decimal,
    unit: str,
    notes: str,
) -> None:
    if repository.list_history(
        service_code=service_code,
        location_scope=location_scope,
    ):
        return
    repository.add_version(
        NewPriceVersion(
            service_code=service_code,
            location_scope=location_scope,
            price=price,
            unit=unit,
            currency="TRY",
            vat_policy="EXCLUDED",
            valid_from=datetime(2026, 1, 1, tzinfo=UTC),
            valid_to=None,
            is_active=True,
            created_by="SYSTEM_SEED",
            notes=notes,
        )
    )


def _item(
    code: str,
    label_tr: str,
    order: int,
    *,
    supply_by: SupplyBy = SupplyBy.COMPANY,
    default_included: bool = True,
    pricing_role: PricingRole = PricingRole.BASE_PACKAGE,
) -> WorkPackageItem:
    return WorkPackageItem(
        item_code=code,
        labels={"tr": label_tr, "en": code.replace(".", " ").title()},
        default_included=default_included,
        supply_by=supply_by,
        pricing_role=pricing_role,
        display_order=order,
    )


def _initial_work_packages() -> list[WorkPackage]:
    return [
        WorkPackage(
            id=new_id(),
            service_code="formwork.standard.turnkey",
            version=1,
            customer_scope_summaries={
                "tr": (
                    "Standart kapsama kalıp sistemi, dikme/iskele, işçilik, "
                    "kurulum-söküm ve standart sarf dahildir."
                ),
                "en": (
                    "Standard scope includes formwork, shoring, labor, installation, "
                    "removal, and consumables."
                ),
            },
            items=[
                _item("formwork.system", "Kalıp sistemi", 1),
                _item("access.shoring", "Dikme ve iskele", 2),
                _item("labor.standard", "Standart işçilik", 3),
                _item("installation.removal", "Kurulum ve söküm", 4),
                _item("consumables.standard", "Standart sarf", 5),
            ],
        ),
        WorkPackage(
            id=new_id(),
            service_code="rough_construction.turnkey",
            version=1,
            customer_scope_summaries={
                "tr": (
                    "Standart kapsama kalıp, donatı, beton uygulama işçiliği, "
                    "duvar işleri, ekipman ve sarf dahildir."
                ),
                "en": (
                    "Standard scope includes formwork, reinforcement, concrete labor, "
                    "masonry, equipment, and consumables."
                ),
            },
            items=[
                _item("formwork", "Kalıp işleri", 1),
                _item("reinforcement", "Donatı işleri", 2),
                _item("concrete.application", "Beton uygulama işçiliği", 3),
                _item("masonry", "Duvar işleri", 4),
                _item("labor.standard", "Standart işçilik", 5),
                _item("equipment.standard", "Standart ekipman", 6),
                _item("consumables.standard", "Standart sarf", 7),
            ],
        ),
        WorkPackage(
            id=new_id(),
            service_code="facade.composite.turnkey",
            version=1,
            customer_scope_summaries={
                "tr": (
                    "Standart kapsama karkas, yalıtım, bağlantılar, montaj, işçilik "
                    "ve tamamlayıcı uygulamalar dahildir."
                ),
                "en": (
                    "Standard scope includes substructure, insulation, fixings, "
                    "installation, labor, and finishing works."
                ),
            },
            items=[
                _item("facade.substructure", "Cephe karkası", 1),
                _item("facade.insulation", "Isı yalıtımı", 2),
                _item("facade.anchors", "Ankraj ve bağlantılar", 3),
                _item(
                    "facade.finish_material",
                    "Ana cephe kaplama malzemesi",
                    4,
                    supply_by=SupplyBy.CUSTOMER,
                ),
                _item("facade.installation", "Montaj ve işçilik", 5),
                _item(
                    "facade.demolition",
                    "Mevcut cephe sökümü",
                    6,
                    default_included=False,
                    supply_by=SupplyBy.NOT_APPLICABLE,
                    pricing_role=PricingRole.ADD_ON,
                ),
            ],
        ),
        WorkPackage(
            id=new_id(),
            service_code="facade.insulation_paint.turnkey",
            version=1,
            customer_scope_summaries={
                "tr": (
                    "Standart kapsama ısı yalıtımı, dübel, sıva filesi, kaplama, "
                    "boya ve işçilik dahildir."
                ),
                "en": (
                    "Standard scope includes insulation, anchors, mesh, render, "
                    "paint, and labor."
                ),
            },
            items=[
                _item("facade.insulation", "Isı yalıtımı", 1),
                _item("facade.fixings", "Dübel ve bağlantılar", 2),
                _item("facade.mesh_render", "Fileli sıva", 3),
                _item("facade.paint", "Cephe boyası", 4),
                _item("labor.standard", "Standart işçilik", 5),
            ],
        ),
    ]
