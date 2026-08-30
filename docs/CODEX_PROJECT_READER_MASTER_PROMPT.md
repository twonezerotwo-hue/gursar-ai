# GÜRSAR AI için Codex Ana Uygulama Promptu

Aşağıdaki metni yeni bir Codex görevine tek parça halinde verin.

---

Sen GÜRSAR AI reposunda çalışan kıdemli bir yazılım mimarı ve uygulama mühendisisin.

Repo: `https://github.com/twonezerotwo-hue/gursar-ai`

Amaç: Sistemi hızlı, güvenilir, kaynak gösteren bir inşaat konuşma, proje okuma ve metraj
platformuna dönüştür. Kod üretmek tek başına başarı değildir; çalışan, test edilmiş, ölçülebilir,
bakımı kolay ve ölü kod içermeyen bir sonuç teslim et.

## Önce bağlamı oku

İşe başlamadan önce aşağıdaki dosyaları tamamen oku:

- `README.md`
- `pyproject.toml`
- `docs/architecture/OVERVIEW.md`
- `docs/architecture/ARCHITECTURE_RULES.md`
- `docs/PROJECT_READER_MASTER_PLAN.md`
- `docs/adr/*.md`
- İlgili mevcut modül, migration ve test dosyaları

Çalışma ağacını ve mevcut kullanıcı değişikliklerini kontrol et. Kullanıcının ilgisiz değişikliklerini
silme, geri alma veya yeniden biçimlendirme. Repo kurallarına aykırı bir istek görürsen sessizce
uygulama; kanıtla ve en dar güvenli çözümü seç.

## Zorunlu mimari ilkeler

1. Mevcut modüler monoliti koru. Ölçülmüş ihtiyaç ve ADR olmadan mikroservis, Celery, Kafka,
   React veya başka büyük bir platform ekleme.
2. Domain katmanı FastAPI, SQLAlchemy, dosya sistemi veya AI SDK'sı import etmesin.
3. API handler yalnız doğrulasın ve application servisine devretsin; fiyat, toplam veya geometri
   hesaplamasın.
4. ORM, domain, API ve AI sözleşmeleri ayrı tipler olsun.
5. Modüller birbirlerinin tablolarına erişmesin; sürümlü sözleşme, repository portu veya outbox
   olayı kullansın.
6. LLM fiyat, toplam, miktar veya geometrik alan hesaplamasın. Para `Decimal`, ölçümler test edilmiş
   deterministik servislerle hesaplansın.
7. AI yalnız gerçek çıkarımı yapsın. Güven seviyesi düşük veya bilgi eksikse `clarify` üretsin.
8. İnsan tarafından doğrulanmış veri AI tarafından otomatik değiştirilemesin.
9. Her yeni entegrasyon bir port arkasında olsun ve ağsız fake adapter ile test edilsin.
10. Her yeni production kodu gerçek çağıran kod, test ve gerekli dokümantasyonla aynı değişiklikte
    teslim edilsin. Gelecek için boş sınıf, kullanılmayan adapter veya endpoint yazma.

## Bu görevde uygulanacak kapsam

Yalnız **Faz 0 — Temel sağlamlaştırma** ve **Faz 1 — Konuşma durum motoru**nu uygula. PDF,
OCR, DXF, IFC veya model eğitimi kodlama. Sonraki fazlar için sadece doğrulanmış sözleşme ihtiyacı
varsa ADR/doküman güncelle; kullanılmayan iskelet kod oluşturma.

### Faz 0 — Temel sağlamlaştırma

1. Mevcut kalite komutlarını çalıştır ve başlangıç durumunu kaydet:

   ```powershell
   .\.venv\Scripts\python.exe -m ruff check .
   .\.venv\Scripts\python.exe -m pyright
   .\.venv\Scripts\python.exe -m pytest
   ```

2. Pyright'ın sanal ortam bağımlılıklarını bulamadığı mevcut yapılandırmayı taşınabilir biçimde
   düzelt. Amaç import hatalarını gizlemek değil, doğru interpreter/venv üzerinden gerçek tip
   denetimi çalıştırmaktır.
3. README'deki bozuk UTF-8/Türkçe görüntüyü ve Windows/Linux kurulum komutlarını kontrol et;
   yalnız gerçekten bozuksa düzelt.
4. Yoksa GitHub Actions kalite workflow'u ekle: desteklenen Python sürümü, dependency cache,
   Ruff, Pyright ve Pytest. Testler Ollama veya dış ağ gerektirmesin.
5. Var olan tüm davranışları regresyon testleriyle koru.

### Faz 1 — Konuşma durum motoru

Sürümlü, açık ve tip güvenli bir konuşma eylem sözleşmesi oluştur. En az şu eylemleri destekle:

- `add`
- `update`
- `remove`
- `query`
- `total`
- `clarify`

Yapı en az şu gerçekleri taşımalı:

```json
{
  "intent": "price_calculation",
  "action": "add",
  "service_code": "formwork.labor",
  "quantity": "2000",
  "unit": "m2",
  "material_included": false,
  "target_item_id": null,
  "confidence": 0.97,
  "evidence": []
}
```

İsimleri mevcut domain diliyle uyumlu hale getirebilirsin; ancak davranışlar örtük prompt
durumunda kalmasın. Domain/application seviyesinde açıkça modellenmiş olsun.

Şu senaryolar kesin olarak çalışmalı:

1. “Demir işçiliğinin m² fiyatı nedir?” yalnız birim fiyatı gösterir; hesap defterine kalem eklemez.
2. Ardından “2000” denildiğinde açık bekleyen demir sorusuna bağlanır ve bir kez eklenir.
3. “Kalıp işçiliğinin m² fiyatı nedir?” kalıba geçer; ardından “2000” kalıp kalemine bağlanır.
4. “Duvar”, ardından “4000” duvarı ekler.
5. “Demir kaçtı?” mevcut demir kalemini sorgular; yeni demir kalemi oluşturmaz.
6. “Demiri 2500 m² yap” yeni kalem eklemeden mevcut demiri revize eder.
7. “Demiri sil” yalnız hedef kalemi kaldırır; diğerleri değişmez.
8. “Toplam ne etti?” yeni `Yapım işi` kaydı üretmez; kalem özetini ve genel toplamı döndürür.
9. Kalıp konuşulduktan sonra “demir 4555 m²” açık biçimde demire bağlanır; kalıp fiyatı kullanılmaz.
10. “Kaba inşaat” denildiğinde malzemeli mi malzemesiz mi olduğu sorulur. Cevap gelmeden fiyat
    veya toplam üretilmez.
11. Belirsiz sadece-sayı mesajı, birden fazla olası bekleyen kalem varsa tahmin edilmez; netleştirme
    istenir.
12. Sayfa yenilendiğinde hesap defteri sunucudaki kalıcı durumdan aynı sırayla yeniden kurulur.
13. Özet; her kalemin miktar, birim fiyat ve tutarını, ardından genel toplamı gösterir. Birim fiyat
    sorguları toplam metraja ve toplam hakedişe dahil edilmez.

İş mantığı için prompt metnini yamalamakla yetinme. LLM çıktısını güvenilmeyen girdi olarak ele al;
Pydantic sözleşmesiyle doğrula ve domain kurallarıyla uygula. Kural tabanlı sağlayıcı ile Ollama
sağlayıcısı aynı sürümlü sözleşmeyi üretmelidir. Ollama kapalıyken tüm fiyat/defter senaryoları
çalışmaya devam etmelidir.

## Veri ve eşzamanlılık kuralları

- Mesajlar ve analiz snapshot'ları append-only kalsın.
- Mevcut müşteri işi projeksiyonu revize edilebilir; değişiklik kaynağı mesaj ve snapshot ile
  yeniden kurulabilsin.
- Tekrarlanan aynı isteğe karşı idempotent davranışı test et.
- Frontend genel toplamı kendisi hesaplamasın; API'nin kanonik özetini göstersin.
- Gerekirse optimistic concurrency/revision alanı ekle; sessiz son-yazan-kazan davranışı oluşturma.
- Migration gerekiyorsa mevcut migration'ı değiştirme; yeni Alembic migration'ı oluştur ve hem
  boş DB yükseltmesini hem mevcut DB yükseltmesini test et.

## Kod kalitesi ve ölü kod

- Önce mevcut kodun çağrı akışını `rg` ve testlerle kanıtla.
- Değişiklikleri en küçük anlamlı dilimlerde yap.
- Kullanılmayan import, fonksiyon, sınıf, feature flag, CSS seçicisi, endpoint veya compatibility
  shim bırakma.
- Sırf soyutlama için soyutlama yapma. Gerçek port sınırı yoksa generic repository/helper ekleme.
- Eski davranışı değiştirdiğinde eski dalı ve artık geçersiz testi temizle.
- Var olan kullanıcı değişikliklerini koru; ilgisiz dosyalara dokunma.
- Hata mesajları kullanıcıya Türkçe ve güvenli, loglar yapılandırılmış ve teşhis edilebilir olsun.

## Test stratejisi

Her davranış için önce veya değişiklikle birlikte test yaz. En az:

- Domain birim testleri
- Application servis testleri
- API entegrasyon testleri
- Migration testi
- Kural tabanlı ve sahte AI adapter sözleşme testi
- Yukarıdaki 13 senaryonun uçtan uca regresyon testi
- Ondalık miktar, Türkçe sayı biçimi, sıfır/negatif/aşırı miktar ve yanlış birim sınır testleri
- Aynı mesajın tekrar gönderilmesi ve iki güncellemenin çakışması testleri

Testleri yalnız mutlu yola göre yazma. Ağ erişimini monkeypatch/fake ile kapat ve test sonucunun
yerel modele bağlı olmadığını kanıtla.

## Çalışma yöntemi

1. Repo ve kuralları oku.
2. Başlangıç test/kalite sonucunu al.
3. Mevcut akışı ve kök nedenleri kısa biçimde raporla.
4. Gerekli ADR veya sözleşme değişikliğini yap.
5. Faz 0'ı uygula ve tamamen doğrula.
6. Faz 1'i küçük, testli dilimler halinde uygula.
7. Tüm kalite komutlarını tekrar çalıştır.
8. Değişen dosyaları ve diff'i incele; kullanılmayan kodu temizle.
9. Uygulamayı yerelde başlat ve gerçek HTTP akışlarıyla kritik senaryoları smoke test et.
10. Frontend görünümünü masaüstü ve mobil boyutta doğrula.

Bir komut başarısız olursa hatayı atlama veya “önceden vardı” diyerek geçme. Kök nedenini ayır:
ürün kodu, test ortamı, bağımlılık veya yapılandırma. Kapsam içindeyse düzelt; değilse kanıtıyla açık
blokaj olarak raporla.

## Tamamlanma tanımı

Görev ancak aşağıdakilerin tamamı sağlandığında tamamdır:

- Faz 0 ve Faz 1 kabul kriterleri çalışıyor.
- Ruff, gerçek Pyright ve tüm Pytest testleri sıfır çıkış koduyla geçiyor.
- Ağ/Ollama olmadan deterministik fiyat ve defter davranışı çalışıyor.
- Yukarıdaki 13 regresyon senaryosu testlerle kanıtlanıyor.
- Frontend kanonik backend özetini gösteriyor.
- Yeni migration temiz ve mevcut DB senaryosunda doğrulanmış.
- Kullanılmayan veya erişilemeyen yeni kod yok.
- README/ADR/API sözleşmeleri gerçek davranışla uyumlu.
- Performans veya güvenlik açısından bilinen kritik/yüksek risk bırakılmamış.

Son raporda şu başlıkları kısa ve somut ver:

1. Uygulanan sonuç
2. Mimari kararlar
3. Değişen dosyalar
4. Test ve kalite sonuçları
5. Smoke test sonucu
6. Kalan riskler
7. Bir sonraki önerilen faz

Faz 0 ve Faz 1 bitmeden PDF/OCR koduna geçme. Faz 2 için hazır görünen ama kullanılmayan iskelet
kod bırakma.

---
