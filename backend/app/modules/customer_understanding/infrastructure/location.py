from __future__ import annotations

import unicodedata

from app.modules.pricing.domain.location import NormalizedLocation

LOCATION_ALIASES = {
    "gokturk": NormalizedLocation(
        code="TR/ISTANBUL/EYUPSULTAN/GOKTURK",
        display_name="İstanbul / Eyüpsultan / Göktürk",
    ),
    "avcilar": NormalizedLocation(
        code="TR/ISTANBUL/AVCILAR",
        display_name="İstanbul / Avcılar",
    ),
    "istanbul": NormalizedLocation(code="TR/ISTANBUL", display_name="İstanbul"),
    "turkiye": NormalizedLocation(code="TR", display_name="Türkiye"),
    "turkey": NormalizedLocation(code="TR", display_name="Türkiye"),
}

LOCATION_DISPLAY_NAMES = {
    "TR/ISTANBUL/EYUPSULTAN/GOKTURK": "İstanbul / Eyüpsultan / Göktürk",
    "TR/ISTANBUL/AVCILAR": "İstanbul / Avcılar",
    "TR/ISTANBUL": "İstanbul",
    "TR": "Türkiye",
    "GLOBAL": "Genel",
}


def fold_text(value: str) -> str:
    translated = value.casefold().translate(
        str.maketrans({"ı": "i", "ş": "s", "ğ": "g", "ü": "u", "ö": "o", "ç": "c"})
    )
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", translated)
        if not unicodedata.combining(character)
    )


class TurkishLocationNormalizer:
    def normalize(self, raw_value: str | None, default_code: str) -> NormalizedLocation:
        if raw_value:
            folded = fold_text(raw_value)
            for alias, location in LOCATION_ALIASES.items():
                if alias in folded:
                    return location
            if "/" in raw_value:
                code = raw_value.strip("/").upper()
                return NormalizedLocation(
                    code=code,
                    display_name=LOCATION_DISPLAY_NAMES.get(code, raw_value),
                )

        code = default_code.strip("/").upper()
        return NormalizedLocation(
            code=code,
            display_name=LOCATION_DISPLAY_NAMES.get(code, code),
        )
