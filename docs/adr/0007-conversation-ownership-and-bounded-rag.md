# ADR 0007: Görüşme sahipliği ve sınırlı RAG aday seçimi

## Karar

Her doğrudan veya QR başlangıcı yeni bir `VisitorSession` üretir. Bearer token HMAC-SHA256 ile
imzalanır; veritabanında yalnız SHA-256 özeti ve son kullanma zamanı saklanır. Conversation ID
tek başına yetki değildir. Token, `X-Conversation-Owner-Token` başlığı veya görüşmeye özel
HttpOnly/SameSite=Strict cookie ile doğrulanır; başarısızlık 403 döner.

QR kaynak kodu kampanya atfı olarak kalır fakat artık görüşme yeniden kullanım anahtarı değildir.

RAG ilk sorguda embedding JSON seçmez. PostgreSQL `to_tsvector`/GIN, migrasyonlu SQLite FTS5,
migrasyonsuz test ortamında ise sınırlı LIKE aday seçimi kullanılır. Yalnız en fazla 500 adayın
embedding'i ikinci sorguda yüklenir ve Python tarafında yeniden sıralanır.

## Sonuçlar

- Ziyaretçiler arası görüşme sızıntısı engellenir.
- 17 bin+ chunk sorgusu tüm vektörleri RAM'e almaz.
- Production ortamı varsayılan token secret ile başlamaz.
- pgvector benzerliği sonraki PostgreSQL optimizasyon fazına bırakılmıştır.
