# Background Analysis Jobs

PDF, DXF, IFC, raster vision, hybrid analiz, dataset export ve benchmark işleri DB-backed kuyruk üzerinden çalışır. HTTP isteği yalnız işi oluşturur ve `QUEUED` döner.

Durumlar `QUEUED, RUNNING, COMPLETED, FAILED, CANCELLED`; aşamalar `VALIDATING, RENDERING, VECTOR_EXTRACTION, OCR, VISION_INFERENCE, FUSION, PROJECT_GRAPH_WRITE, ACTIVE_LEARNING, COMPLETED` şeklindedir. Aynı document version, job type ve analiz/model sürümü idempotency anahtarıdır.

Claim koşullu veritabanı güncellemesiyle yapılır; iki worker aynı işi alamaz. Retry limiti config'tedir. Kalp atışı eskimiş RUNNING işler yeniden kuyruğa alınabilir, limit dolmuşsa kalıcı FAILED olur. İptal sayfa sınırında kontrol edilir.

İşler süre, CPU, sayfa/nesne sayısı ve mümkünse bellek metriği taşır. İşleyici sayfaları tek tek kalıcılaştırır; tüm belgeyi aynı anda RAM/GPU belleğine yüklemez. Arayüz status endpoint'i üzerinden ilerlemeyi izler.

