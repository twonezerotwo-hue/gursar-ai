# Vision Intelligence V1

GÜRSAR AI vision katmanı yalnız semantik aday üretir. Bir model tahmini doğrulanmış eleman, mühendislik metrajı veya fiyat kalemi değildir.

İşleme sırası sayfa türüne göre belirlenir: VECTOR sayfalarda vektör motoru, RASTER sayfalarda yerel vision sağlayıcısı, HYBRID sayfalarda ise vektör öncelikli füzyon çalışır. Native koordinatlı metin varsa OCR tekrarlanmaz. Sonuçlar normalize 0–1 koordinat, model sürümü, ham güven ve kaynak bilgisiyle `REVIEW_REQUIRED` olarak saklanır.

Doğrulama akışı `ACCEPT / CORRECT / REJECT` kullanır. Düzeltme eski tahmini değiştirmez; yeni revision ve audit olayı oluşturur. Vision sonucu hiçbir zaman doğrudan `TakeoffItem` üretmez. Ölçü yalnız doğrulanmış geometri, doğrulanmış ölçek ve backend ölçüm motorundan gelir.

Varsayılan sağlayıcı ücretsiz ve yerel `HeuristicVisionProvider`'dır. Yerel veya uzak model yalnız config ile etkinleştirilir. Model dosyaları repository içine gömülmez; GPU bulunmadığında güvenli hata/fallback uygulanır.

