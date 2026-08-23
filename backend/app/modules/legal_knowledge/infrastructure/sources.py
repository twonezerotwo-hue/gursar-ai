from __future__ import annotations

from datetime import date

from app.modules.legal_knowledge.domain.models import LegalSourceDefinition

OFFICIAL_SOURCES: tuple[LegalSourceDefinition, ...] = (
    LegalSourceDefinition(
        key="yfk-technical-specifications-catalog",
        title="Yüksek Fen Kurulu Teknik Şartnameler Kataloğu",
        category="technical_specification_catalog",
        authority="T.C. Çevre, Şehircilik ve İklim Değişikliği Bakanlığı - Yüksek Fen Kurulu",
        source_url="https://yfk.csb.gov.tr/sartnameler-330",
        discover_official_links=True,
    ),
    LegalSourceDefinition(
        key="law-3194-zoning",
        title="3194 Sayılı İmar Kanunu - ilgili hükümler",
        category="law",
        authority="Basın İlan Kurumu (resmî mevzuat aynası)",
        source_url="https://bik.gov.tr/storage/2021/08/3194-Sayili-Imar-Kanunu-1.pdf",
        metadata={
            "law_number": "3194",
            "canonical_system": "mevzuat.gov.tr",
            "coverage": "selected_provisions",
        },
    ),
    LegalSourceDefinition(
        key="law-4708-building-inspection",
        title="4708 Sayılı Yapı Denetimi Hakkında Kanun",
        category="law",
        authority="T.C. Çevre, Şehircilik ve İklim Değişikliği Bakanlığı",
        source_url=(
            "https://webdosya.csb.gov.tr/db/sivas/menu/"
            "yapi-denetimi-hakkinda-kanun-4708_20250116090943.pdf"
        ),
        metadata={"law_number": "4708"},
    ),
    LegalSourceDefinition(
        key="law-6331-occupational-health-and-safety",
        title="6331 Sayılı İş Sağlığı ve Güvenliği Kanunu",
        category="law",
        authority="T.C. Çalışma ve Sosyal Güvenlik Bakanlığı",
        source_url="https://www.csgb.gov.tr/Media/fldk2co0/6331_isgkanunu_tr.pdf",
        official_gazette_number="28339",
        published_on=date(2012, 6, 30),
        metadata={"law_number": "6331", "canonical_system": "mevzuat.gov.tr"},
    ),
    LegalSourceDefinition(
        key="regulation-planned-areas-zoning",
        title="Planlı Alanlar İmar Yönetmeliği",
        category="regulation",
        authority="T.C. Çevre, Şehircilik ve İklim Değişikliği Bakanlığı",
        source_url=("https://webdosya.csb.gov.tr/db/tabiat/icerikler/planli-20210811131259.pdf"),
        official_gazette_number="30113",
        published_on=date(2017, 7, 3),
    ),
    LegalSourceDefinition(
        key="regulation-turkey-building-earthquake-2018",
        title="Türkiye Bina Deprem Yönetmeliği 2018",
        category="regulation",
        authority="T.C. İçişleri Bakanlığı - AFAD",
        source_url="https://www.afad.gov.tr/kurumlar/afad.gov.tr/2309/files/TBDY_2018.pdf",
        official_gazette_number="30364 Mükerrer",
        published_on=date(2018, 3, 18),
    ),
    LegalSourceDefinition(
        key="specification-reinforced-concrete-works",
        title="İnşaat Genel Teknik Şartnamesi - Betonarme İşleri",
        category="technical_specification",
        authority="T.C. Çevre, Şehircilik ve İklim Değişikliği Bakanlığı - Yüksek Fen Kurulu",
        source_url=(
            "https://webdosya.csb.gov.tr/db/yfk/icerikler/c18---betonarme-isler--20190412161656.pdf"
        ),
    ),
    LegalSourceDefinition(
        key="specification-mechanical-installation-general",
        title="Makina Tesisatı Genel Teknik Şartnamesi - Genel Esaslar",
        category="technical_specification",
        authority="T.C. Çevre, Şehircilik ve İklim Değişikliği Bakanlığı - Yüksek Fen Kurulu",
        source_url=(
            "https://webdosya.csb.gov.tr/db/yfk/icerikler/m1---genel-esaslar-20190412134859.pdf"
        ),
    ),
    LegalSourceDefinition(
        key="construction-materials-legislation-catalog",
        title="Yapı Malzemeleri Mevzuat ve Tebliğler Kataloğu",
        category="legislation_catalog",
        authority="T.C. Çevre, Şehircilik ve İklim Değişikliği Bakanlığı",
        source_url="https://meslekihizmetler.csb.gov.tr/mevzuat-ve-tebligler-i-112709",
        discover_official_links=True,
    ),
    LegalSourceDefinition(
        key="building-technologies-legislation-catalog",
        title="Yapım Teknolojileri ve Laboratuvar Mevzuatı Kataloğu",
        category="legislation_catalog",
        authority="T.C. Çevre, Şehircilik ve İklim Değişikliği Bakanlığı",
        source_url=(
            "https://yapiisleri.csb.gov.tr/"
            "yapim-teknolojileri-ve-laboratuvar-daire-baskanligi-mevzuati-102049"
        ),
        discover_official_links=True,
    ),
)
