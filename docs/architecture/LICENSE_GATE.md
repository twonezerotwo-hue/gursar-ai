# License Gate V1

Her kaynak indirmeden önce lisans değerlendirmesine girer. `OPEN_LICENSED`, `LICENSED_BY_GURSAR` ve `INTERNAL_PROJECT` training için uygundur. `RESEARCH_ONLY`, `REFERENCE_ONLY`, `LICENSE_UNKNOWN` ve `REJECTED` engellenir.

Karar lisans adı, URL ve varsa lisans metni hash'ine dayanır. Kaynak itibarı tek başına izin değildir. Bilinmeyen lisans manuel inceleme gerektirir. Manuel karar reviewer ve audit kaydı taşır. Daha önce izinli lisans değişirse kaynak `SUSPENDED` olur; eski dataset sürümleri silinmez.

Attribution gerektiren lisans bilgisi download ve dataset manifestinde korunur. Harici IFC verisi estimator kalibrasyonuna varsayılan olarak girmez.

V2'de türev veri için `UpstreamLicenseLineage` zorunludur. Dataset'in kendi MIT/CC-BY beyanı üst kaynağın kısıtını geçersiz kılamaz. Üst lisans bilinmiyor, non-commercial/research-only veya türev daha izin verici ise `DERIVED_DATASET_LICENSE_CONFLICT` üretilir; indirme ve training engellenir. Download manifest effective license ve lineage kararının snapshot'ını taşır.
