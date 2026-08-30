# Autonomous Data Acquisition V1

Akış `DISCOVERY -> LICENSE_CHECK -> QUALITY_PREFILTER -> DOWNLOAD -> QUARANTINE_VALIDATION -> EXTRACT -> ANNOTATION_IMPORT -> PROJECT_NORMALIZATION -> DATASET_CANDIDATE_CREATION -> QA` şeklindedir. Ağır işler mevcut DB-backed `AnalysisJob` kuyruğunda çalışır ve aynı kaynak/sürüm isteği idempotenttir.

Keşif provider arayüzü statik seed, GitHub, Hugging Face ve resmî kaynak provider'larını destekler. Varsayılan test modu yalnız sınırlı seed listesini işler; remote provider açıkça istenmeden internet taraması yapmaz. Domain, kaynak ve indirme bütçeleri config'tedir. Login/paywall/erişim kontrolü aşılmaz.

Deterministik durum özeti yalnız DB sayaçlarından üretilir. Keşfedilen, lisans bekleyen, indirilen, paketlenen, IFC ve annotation sayıları LLM tarafından tahmin edilmez.

## Bootstrap V1

`AutonomousAcquisitionOrchestrator`, `DISCOVERY_ONLY`, `SAFE_ACQUIRE` ve
`FULL_BOOTSTRAP` modlarını destekler. Tam çalışma `AUTONOMOUS_DATA_ACQUISITION`
arka plan işi olarak kuyruğa alınır; aşama ve ilerleme kontrol noktaları kalıcıdır.
Kaynak hataları savepoint ile birbirinden ayrılır.

İlk kontrollü çalışma buildingSMART Certification datasets (CC BY 4.0) ve PERDAW
(MIT) kaynaklarını kullanır. CubiCasa5K, CC BY-NC lisansı nedeniyle ticari eğitimden
çıkarılır. MLStructFP veri indirmesi form onayı gerektirdiğinden erişim kısıtlıdır.
Belirsiz depo lisansları `LICENSE_UNKNOWN` kalır.

Readiness V2; sınıf hedefi, proje ve kaynak ailesi sayısı, gerçek GOLD/SILVER oranı,
sentetik oranı, kalite, duplicate oranı ve kaynak yoğunluğunu birlikte değerlendirir.
Sentetik hacim tek başına READY sonucu üretemez. Aday dataset yalnız READY sınıfları
alır; WEAK ve GOLDEN benchmark kayıtlarını dışarıda tutar.
