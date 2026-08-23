# GÜRSAR AI Teknik Analiz ve Test Raporu

**Tarih:** 23 Ağustos 2026
**İncelenen commit:** `0a2b876` (`main`)
**Genel karar:** Proje yerel demo/MVP olarak çalışıyor; mevcut haliyle gerçek müşteri veya üretim kullanımı için hazır değil.

## 1. Yönetici özeti

Temel teklif akışı sağlıklı: uygulama açılıyor, SQLite şeması kuruluyor, QR ve doğrudan görüşme akışları çalışıyor, fiyatlar `Decimal` ile deterministik hesaplanıyor ve mevcut 10 otomatik test geçiyor. Kod organizasyonu ve modüler monolit sınırları da başlangıç seviyesi için iyi durumda.

Üretime çıkışı engelleyen en önemli sorun QR görüşme izolasyonudur. Aynı QR kodunu kullanan tüm ziyaretçiler, QR kaynağına bağlı tek “son görüşmeye” yönlendiriliyor. İkinci ziyaretçi birinci ziyaretçinin mesajlarını ve tekliflerini görebiliyor. Buna ek olarak müşteri görüşme uç noktalarında sahiplik/yetkilendirme bulunmuyor, varsayılan yönetici anahtarı güvenli biçimde reddedilmiyor ve fiyatın para birimi/KDV gösterimi bazı Türkçe arayüzlerde sabit değerlerle yazılıyor.

## 2. Uygulanan testler

| Kontrol | Sonuç |
| --- | --- |
| `pytest -vv` | 10/10 geçti |
| Ruff | Hata yok |
| Pyright | 0 hata, 0 uyarı |
| Python `compileall` | Başarılı |
| `pip check` | Bozuk bağımlılık yok |
| Dal kapsamlı coverage | Toplam %93 |
| Sağlık uç noktası | `200`, `status=ok` |
| QR müşteri sayfası | `200` |
| Gerçek tarayıcı teklif akışı | Başarılı; 120 m² × 2.950 TL = 354.000 TL |
| Tarayıcı konsolu | Hata/uyarı yok |
| 390×844 mobil kırılım | Yatay taşma yok; teklif özeti görünmüyor |

Coverage içindeki başlıca boşluklar:

- OpenAI sağlayıcısı: %48
- Fiyat yönetimi API yönlendiricisi: %69
- Teklif sunum katmanı: %72
- Ana uygulama/teslimat katmanı: %81

Mevcut testler temel iş senaryolarını iyi kapsıyor ancak güvenlik, ziyaretçi izolasyonu, hatalı girişler, OpenAI, PostgreSQL, eşzamanlılık ve tarayıcı uçtan uca akışlarını kapsamıyor.

## 3. Doğrulanmış bulgular

### P0 — QR görüşmeleri ziyaretçiler arasında paylaşılıyor

`QRConversationService`, ziyaretçiyi veya cihazı ayırmadan `qr_source_id` için en son görüşmeyi bulup sürdürüyor. İki ayrı QR başlatma isteğinde aynı görüşme kimliği döndü ve ikinci istekte ilk ziyaretçinin `100 m2 kalıp` mesajı görüldü.

**Etkisi:** Müşteri mesajları, konumları, metrajları ve ticari teklifleri başka müşterilere açılabilir. Bu doğrudan veri gizliliği ihlalidir.

**Kaynak:** `backend/app/modules/qr/application/service.py`, `backend/app/modules/conversation/infrastructure/repository.py`

**Gerekli çözüm:**

- QR kodu yalnızca kaynak/kampanya atfı sağlamalı; her yeni ziyaretçi için ayrı görüşme oluşturulmalı.
- Sürdürme gerekiyorsa güvenli, rastgele bir oturum belirteci imzalı `HttpOnly`, `Secure`, `SameSite` cookie ile ziyaretçiye bağlanmalı.
- QR kaynağı tek başına görüşme sahipliği anahtarı olmamalı.
- Bu davranış için “iki farklı istemci birbirinin görüşmesini göremez” entegrasyon testi eklenmeli.

### P1 — Müşteri görüşmelerinde sahiplik kontrolü yok

Görüşme kimliğini bilen biri görüşmeyi okuyabilir, teklif geçmişini çekebilir ve yeni mesaj ekleyebilir. UUID tahmin edilmesi zor olsa da URL, log, ekran görüntüsü veya yönlendiren bilgi üzerinden sızabilir; UUID bir yetkilendirme mekanizması değildir.

**Kaynak:** `backend/app/modules/conversation/api/router.py`

**Gerekli çözüm:** Görüşmeyi kullanıcı/oturum belirtecine bağlayıp tüm okuma ve yazma uç noktalarında sahiplik doğrulaması yapmak.

### P1 — Yönetici API’si güvenli biçimde “fail closed” çalışmıyor

`GURSAR_ADMIN_API_KEY` tanımlanmadığında uygulama `local-development-key` değerini kullanıyor. Yanlış yapılandırılmış bir üretim ortamı bilinen anahtarla açılabilir. Tek statik anahtar; kullanıcı kimliği, rol, anahtar rotasyonu ve ayrıntılı aktör kaydı sağlamıyor.

**Kaynak:** `backend/app/core/config.py`, `backend/app/core/security.py`

**Gerekli çözüm:**

- `environment != development/test` iken eksik, varsayılan veya zayıf anahtarla uygulama başlatılmamalı.
- Tercihen yönetici kimliği + rol tabanlı erişim kullanılmalı.
- Anahtar rotasyonu, başarısız giriş logları ve hız sınırlaması eklenmeli.

### P1 — Para birimi ve vergi metni veriden bağımsız gösterilebiliyor

Backend fiyat kaydı `currency` ve `vat_policy` taşıyor; fakat Türkçe sunum bazı yerlerde daima `TL` ve `+ KDV`, frontend ise daima `TRY` ve `KDV hariç` gösteriyor. Yönetici API’si farklı para birimi/vergi politikası kabul ettiği için müşteriye finansal olarak yanlış teklif gösterilebilir.

**Kaynak:** `backend/app/modules/quick_quote/application/presentation.py`, `frontend/app.js`

**Gerekli çözüm:** Para birimi ve KDV metnini yalnızca teklif alanlarından üretmek; desteklenmeyen değerleri yönetici API’sinde reddetmek; TRY/EUR ve KDV dahil/hariç senaryolarını test etmek.

### P1 — Fiyat defteri doğrulamaları yetersiz

Doğrulama testinde `valid_to < valid_from` olan kayıt API tarafından `201 Created` ile kabul edildi. Ayrıca servis kodunun katalogda bulunması, konum kodu biçimi, birim, para birimi ve KDV politikası kontrollü listelere karşı doğrulanmıyor. `price=0` da kabul ediliyor.

**Kaynak:** `backend/app/modules/pricing/api/schemas.py`, `backend/app/modules/pricing/api/router.py`

**Gerekli çözüm:** Çapraz alan tarih doğrulaması, katalog referansı, enum/izinli değer doğrulaması, normalizasyon ve iş kuralına uygun pozitif fiyat kontrolü eklemek.

### P2 — Boş mesaj ve geçersiz locale kabul ediliyor

Tek boşluk içeren mesaj şema kontrolünden geçiyor, handler içinde boş metne çevriliyor ve `200` dönüyor. 100 karakterlik geçersiz locale değeri de SQLite üzerinde `201` ile kabul edildi.

**Kaynak:** `backend/app/modules/conversation/api/schemas.py`, `backend/app/modules/conversation/api/router.py`

**Gerekli çözüm:** `strip_whitespace`, boşluk sonrası minimum uzunluk, locale enum/desen ve en fazla 12 karakter doğrulaması.

### P2 — Mobil görünüm teklif özetini tamamen gizliyor

390×844 gerçek tarayıcı testinde yatay taşma yok ve mesaj akışı çalışıyor; ancak `.quote-pane` yalnızca 920 px ve üzerinde gösteriliyor. Mobil kullanıcının “İşler ve fiyatlar” kartlarına erişebileceği alternatif bir bölüm veya açılır panel yok.

**Kaynak:** `frontend/styles.css`, `frontend/index.html`

**Gerekli çözüm:** Mobilde konuşmanın altında teklif kartları, sekme veya açılır alt panel göstermek; 360/390/768/1024 px uçtan uca görsel testler eklemek.

### P2 — OpenAI üretim yolu hazır ve testli değil

Varsayılan kurulum `openai` ekstra paketini kurmuyor; sağlayıcı seçildiğinde paket eksikliği ilk mesaj sırasında ortaya çıkıyor. Gerçek sağlayıcı için sözleşme testi, timeout, retry, hata dönüşümü, devre kesici/fallback, maliyet ve kişisel veri politikası yok. Sağlayıcı coverage değeri %48.

**Kaynak:** `pyproject.toml`, `backend/app/modules/customer_understanding/infrastructure/openai.py`

**Gerekli çözüm:** `.[openai]` kurulumu, başlangıçta bağımlılık/yapılandırma kontrolü, sahte istemciyle contract testleri, açık timeout/retry/fallback politikası ve veri saklama değerlendirmesi.

### P2 — Outbox yalnızca yazılıyor, yayımlanmıyor

Outbox kayıtları oluşturuluyor fakat `published_at` alanını güncelleyen worker/dispatcher yok. Mimari belgelerde gelecekteki modüllerin olaylara abone olacağı belirtilse de mevcut uygulamada olay teslimi gerçekleşmiyor.

**Kaynak:** `backend/app/core/persistence.py`

**Gerekli çözüm:** Kilitleme, yeniden deneme, idempotency, hata kuyruğu ve metrikleri olan outbox publisher eklemek; gerek yoksa V1 dokümanında bunun henüz uygulanmadığını açıkça belirtmek.

### P2 — Şema yönetimi uygulama başlangıcında migration sistemini atlıyor

Uygulama her açılışta `Base.metadata.create_all()` çalıştırıyor. Bu yeni tablo oluşturabilir fakat mevcut tabloları migration gibi güvenli biçimde dönüştürmez. İlk Alembic migration’ı da açık DDL yerine `create_all/drop_all` kullanıyor.

**Kaynak:** `backend/app/bootstrap.py`, `backend/alembic/versions/0001_initial.py`

**Gerekli çözüm:** Üretimde yalnızca `alembic upgrade head` ile şema yönetmek; başlangıçta revision uyumluluğunu kontrol etmek; migration dosyalarında açık ve gözden geçirilebilir işlemler kullanmak.

### P2 — Eşzamanlı yazma yarışları için koruma yok

Mesaj sırası `max(ordinal)+1`, fiyat sürümü `max(version)+1` ile hesaplanıyor. Aynı anda gelen iki istek aynı değeri seçip benzersiz kısıt hatası ve `500` üretebilir. QR başlatma da aynı anda birden fazla görüşme yaratabilir.

**Kaynak:** `backend/app/modules/conversation/infrastructure/repository.py`, `backend/app/modules/pricing/infrastructure/repository.py`

**Gerekli çözüm:** PostgreSQL üzerinde gerçek eşzamanlılık testleri; uygun kilit, atomik sayaç/sequence veya çakışma yeniden denemesi.

### P2 — Sayfa yenilemede tamamlanmamış teklif durumu kayboluyor

Teklif geçmişi yalnızca kaydedilmiş `quick_quotes` satırlarını döndürüyor. Hizmet açıklaması bekleyen veya fiyatı yapılandırılmamış işler kart olarak geri yüklenmiyor; mobil/masaüstü sayfa yenilemesinde özet kaybolabiliyor.

**Kaynak:** `backend/app/modules/quick_quote/application/history.py`

**Gerekli çözüm:** Güncel iş projeksiyonlarını ve son durumlarını döndüren bir endpoint veya kalıcı teklif-durum kaydı eklemek.

## 4. Operasyon ve teslimat eksikleri

- CI iş akışı yok; test, lint, type check ve migration kontrolü her push/PR’da otomatik çalışmıyor.
- Kilit dosyası yok; geniş sürüm aralıkları nedeniyle kurulumlar zaman içinde farklı bağımlılıklar çözebilir.
- PostgreSQL sürücüsü opsiyonel fakat PostgreSQL entegrasyon testi yok.
- Docker/üretim çalıştırma tanımı, reverse proxy/TLS yönergesi ve release süreci yok.
- Sağlık endpoint’i yalnızca sabit JSON döndürüyor; veritabanı hazır olma kontrolü yok.
- Yapılandırılmış request/error logları, correlation ID, metrik, tracing ve alarm yok.
- Outbox derinliği, eksik fiyat, AI hata oranı ve teklif üretim süresi için operasyonel metrik yok.
- Rate limiting, istek gövdesi sınırı ve kötüye kullanım koruması yok.
- Veri saklama/silme, KVKK, yedekleme ve geri yükleme prosedürü tanımlı değil.
- Frontend otomatik testi, erişilebilirlik taraması ve görsel regresyon testi yok.
- Bağımlılık CVE taraması ve otomatik güncelleme süreci yok.
- README ağırlıklı olarak Bash komutları içeriyor; Windows/PowerShell ve `.env` yükleme davranışı açıklanmalı.
- Testlerde Starlette `TestClient`/`httpx` geçişiyle ilgili bir deprecation uyarısı var; bağımlılık uyumluluğu güncellenmeli.

## 5. Güçlü yönler

- Domain, API ve ORM tiplerinin ayrılması genel olarak korunmuş.
- Fiyat hesabı AI sağlayıcısından ayrılmış ve `Decimal` ile deterministik.
- Fiyat sürümü teklif üzerinde saklanıyor; geçmiş teklif yeniden üretilebilir.
- Eksik fiyat durumunda sayı uydurulmuyor, audit ve outbox olayı oluşturuluyor.
- Mesaj ve analiz snapshot yaklaşımı izlenebilirlik için doğru temel sağlıyor.
- Mevcut ana iş senaryoları hızlı ve ağsız test ediliyor.
- Frontend müşteri metnini `textContent`/text node ile işlediğinden incelenen akışta doğrudan mesaj kaynaklı DOM XSS görülmedi.

## 6. Önerilen uygulama sırası

### Aşama 0 — Üretimi engelleyenler

1. QR görüşmelerini ziyaretçi bazında izole et.
2. Görüşme sahiplik belirteci ve tüm müşteri endpoint’lerinde yetki kontrolü ekle.
3. Üretimde varsayılan yönetici anahtarını kesin olarak reddet.
4. Para birimi/KDV sunumunu veriye bağla.

### Aşama 1 — Veri doğruluğu ve güvenilirlik

1. Fiyat, tarih, servis, konum, birim, para birimi, KDV ve locale doğrulamalarını ekle.
2. Boş mesajı `422` ile reddet.
3. Tamamlanmamış iş durumlarını sayfa yenilemesinde geri yükle.
4. Güvenlik ve negatif senaryo testlerini ekle.
5. Mobil teklif özetini görünür ve kullanılabilir yap.

### Aşama 2 — Üretim altyapısı

1. PostgreSQL entegrasyon ve eşzamanlılık testlerini kur.
2. Alembic migration disiplinini zorunlu yap.
3. CI, bağımlılık kilidi, CVE taraması ve dağıtım tanımını ekle.
4. Readiness, log, metrik, alarm, yedekleme ve geri yükleme mekanizmalarını kur.
5. Outbox publisher’ı uygula veya kapsam dışı olarak açıkça işaretle.

### Aşama 3 — OpenAI sağlayıcısı

1. Ek bağımlılığı ve başlangıç doğrulamasını ekle.
2. Mock/contract, timeout, hata ve fallback testlerini yaz.
3. KVKK, veri minimizasyonu, saklama ve maliyet limitlerini netleştir.

## 7. Üretime geçiş kabul kriterleri

- Farklı iki tarayıcı/cihaz aynı QR koduyla birbirinin görüşmesini göremiyor.
- Sahiplik belirteci olmayan veya yanlış olan görüşme istekleri reddediliyor.
- Üretim ortamı varsayılan/eksik yönetici anahtarıyla başlamıyor.
- Desteklenen tüm currency/VAT kombinasyonları backend ve frontend’de tutarlı gösteriliyor.
- Hatalı fiyat tarihleri, kodlar, birimler, locale ve boş mesajlar `4xx` ile reddediliyor.
- PostgreSQL üzerinde migration, rollback planı ve eşzamanlı istek testleri geçiyor.
- Mobilde teklif özeti erişilebilir; temel akışlar 360–1024 px aralığında E2E testli.
- OpenAI hatası müşteri verisini bozup kontrolsüz `500` üretmiyor; fallback/hata mesajı tanımlı.
- CI; test, lint, type check, migration kontrolü, güvenlik taraması ve frontend E2E adımlarını zorunlu çalıştırıyor.
- Yedekleme/geri yükleme denemesi ve KVKK veri yaşam döngüsü dokümante edilmiş.

## 8. Test kapsamı dışında kalanlar

Bu incelemede gerçek bir OpenAI isteği gönderilmedi, PostgreSQL sunucusu kurulmadı, yük/performans testi ve bağımlılık CVE taraması yapılmadı. Bu alanlarda “sorun yok” sonucu çıkarılmamalıdır; üretim öncesi ayrı doğrulama gerekir.
