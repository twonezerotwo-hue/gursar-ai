# Dataset Readiness V3

Her sınıf ayrı olarak Gold, gerçek çizim Silver, cross-modal reviewed, BIM-derived Silver, external object Silver, synthetic ve weak dağılımıyla raporlanır. Foundational sınıflar WALL, ROOM, DOOR ve WINDOW'dur; varsayılan aday politikası hepsinin READY olmasını ister.

Başlangıç hedefleri: WALL 5.000 gerçek çizim, ROOM 3.000 gerçek çizim, DOOR/WINDOW 2.000 gerçek veya object, COLUMN 1.000 ve STAIR 300 gerçek veya BIM. Minimum üç bağımsız source family korunur; BIM gerçek çizim ailesi sayılmaz.

Candidate seçimi gerçek/Gold/object örneklerini önceler. Synthetic oranı en fazla %40, BIM-derived oranı varsayılan en fazla %50'dir. Oranlar config üzerinden yönetilir. Salt BIM veya synthetic veri foundational readiness oluşturmaz. Hazır olduğunda isim `gursar_arch_vision_v0.1_candidate` olur; bu faz model eğitmez.
