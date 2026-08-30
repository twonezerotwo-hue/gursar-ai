# Vision Dataset Factory V1

Dataset Factory yalnız doğrulanmış, eğitim hakkı bulunan ve anonimleştirilmiş türevleri kabul eder. `REFERENCE_ONLY` ve `UNKNOWN` hak durumları engellenir. Otomatik vektör/model etiketi uzman doğrulaması olmadan ground truth olamaz.

Kaynak PDF değişmez. Sayfa veya ViewRegion, yapılandırılabilir DPI ile deterministik olarak render edilir; kaynak belge hash'i, sayfa, bbox, render sürümü, SHA-256 ve perceptual hash saklanır. Aynı girdi aynı türevi üretir.

Dataset bölünmesi pafta değil proje seviyesindedir. `DatasetLeakageValidator` aynı proje, belge hash'i, revision family veya aynı crop hash'inin split'ler arasında bulunmasını engeller. Dondurulan dataset üyeliği ve manifest checksum'u değişmez.

Desteklenen görevler: sheet classification, title block, view region, room, wall, door, window, column ve stair detection. V1 sınıf kümesi küçük tutulmuştur. Export biçimleri COCO ve GÜRSAR native JSON'dur; büyük export işlemi background job olarak çalışır.

Kalite katmanları GOLD (uzman doğrulamalı), SILVER (doğrulanmış deterministik kaynak) ve WEAK (inceleme bekleyen aday) mantığına hazırlanmıştır. Yalnız uygun kalite eşiği ve hak koşulları dataset'e alınır.

Real Data Expansion V2 raporlaması SILVER veriyi `REAL_DRAWING_SILVER`, `BIM_DERIVED_SILVER` ve `EXTERNAL_OBJECT_SILVER` olarak ayırır. Candidate builder sentetik örnekleri silmeden dataset üyeliğinde sınırlar; candidate sentetik oranı yüzde 40'ı geçmez. BIM-derived geometri gerçek insan çizimi kotasını karşılamaz ve source-family diversity ayrı gate olarak uygulanır.
