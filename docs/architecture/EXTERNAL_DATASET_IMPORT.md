# External Dataset Import

İndirilen içerik önce `quarantine/` altında saklanır. SHA-256, dosya sayısı, açılmış boyut, uzantı, traversal ve executable kontrolleri geçmeden paketlenmez. Otomatik silme yapılmaz.

Project Package Builder arşiv yollarından bağımsız projeleri ayırır ve completeness hesaplar. Harici annotation sınıfları verified `ExternalClassMapping` olmadan GÜRSAR sınıfına dönüşmez. Orijinal sınıf, importer, mapping ve normalization sürümü lineage kaydında kalır.

COCO bbox'ları 0-1 koordinata çevrilir; bozuk bbox, bilinmeyen sınıf ve yüzde 30 üzeri bozuk örnek oranı dataset'i engeller. Harici güvenilir ground truth `SILVER`, GÜRSAR uzman doğrulaması `GOLD`, otomatik/model etiketleri `WEAK` olarak ayrılır.

