# Synthetic Project Factory V1

`GURSAR_SYNTHETIC_ARCH_V1` seed ve spec'ten deterministik dikdörtgen konut planı, PNG türevi ve native ground-truth JSON üretir. WALL, DOOR, WINDOW, COLUMN, ROOM ve STAIR bbox'ları üretim sırasında bilindiği için `SYNTHETIC_GROUND_TRUTH / SILVER` kaynağıdır.

Doğrulama; 0-1 sınırları, sıfır alan, oda çakışması ve boşta kapı/pencereyi kontrol eder. Aynı generator version + seed + spec aynı checksum'u verir ve yeniden üretilmez. Orijinal kullanıcı projesi değiştirilmez.

Sentetik veri gerçek verinin yerine geçmez. Readiness ve dataset kalite raporu synthetic oranını ayrı gösterir; varsayılan yüzde 40 üst sınır aşılırsa dataset freeze engellenir.

Bootstrap V1, `0..1999` seed aralığını 50 projelik kalıcı partiler halinde üretir.
Stüdyo, 1+1, 2+1, 3+1, ofis, koridorlu, kompakt ve büyük konut profilleri dönüşümlü
kullanılır. Ayak izi, oda sayısı, koridor genişliği, duvar kalınlığı, kolon ızgarası,
merdiven varlığı, kâğıt, ölçek ve çizim stili kontrollü biçimde çeşitlenir.

Her proje spec, dönüşüm metadata'sı, kaynak checksum'ları ve doğrulama sonucunu taşır.
Generator version + spec hash idempotency anahtarıdır.
