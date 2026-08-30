# Vision Intelligence V1 Contract

## Sürümler

- Analysis: `VISION_INTELLIGENCE_V1`
- Fusion: `VECTOR_VISION_FUSION_V1`
- Dataset generation: `VISION_DATASET_FACTORY_V1`
- Golden set: `v1`

## Güvenlik sözleşmesi

- Prediction truth değildir ve `REVIEW_REQUIRED` başlar.
- Vision confidence mühendislik doğruluğu değildir.
- Raw vector geometry, engineering quantity değildir.
- Vision final metraj veya fiyat oluşturamaz.
- Structured verified geometry vision'dan önceliklidir.
- Correction append-only revision üretir.
- Eğitim hakkı, anonimleştirme ve doğrulama yoksa dataset üyeliği yoktur.
- Dataset split proje seviyesindedir ve leakage validation zorunludur.
- Golden benchmark eğitim dataset'ine katılmaz.
- Başarılı benchmark modeli otomatik ACTIVE yapmaz; yalnız APPROVED olabilir.

## Kullanıcı etiketi

Doğrulanmamış overlay metni: `AI önerisi · İnceleme gerekli`. Kaynak anlaşmasında `VECTOR + VISION` badge kullanılabilir.

## Pilot değerlendirme hedefleri

Hedefler ürün garantisi değildir: sheet macro-F1 0.95, title block mAP50 0.95, sheet number/scale exact match 0.98, door-window-column count error 0.03, vector measurement error 0.02 ve kalibre raster measurement error 0.05.
