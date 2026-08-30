# Real Data Expansion V2

Bu faz, eğitim verisinin kaynağını açık biçimde ayırır ve sentetik verinin gerçek veri gibi görünmesini engeller.

## Kaynak katmanları

- `GURSAR_GOLD`: Gürsar uzmanı tarafından doğrulanmış gerçek proje verisi.
- `REAL_DRAWING_SILVER`: Lisansı ve üst kaynak zinciri doğrulanmış gerçek 2B çizim.
- `BIM_DERIVED_SILVER`: Lisanslı IFC modelinden deterministik olarak türetilen 2B etiket.
- `EXTERNAL_OBJECT_SILVER`: Harici nesne/kırpım ground-truth verisi.
- `SYNTHETIC`: Sentetik proje fabrikası çıktısı.
- `WEAK`: Uzman doğrulaması olmayan aday veya belirsiz kaynak.

Legacy kaynak adları raporlama sırasında bu katmanlara normalize edilir; özgün `source_type` ve lineage kayıtları değişmez.

## Üst kaynak lisans zinciri

Bir türevin kendi lisansı tek başına eğitim izni vermez. Sistem declared license, parent/root source, upstream license, effective license, ticari kullanım, eğitim, yeniden dağıtım, doğrulama ve çatışma kararını sürümlü olarak saklar.

Şu durumlar indirme ve eğitim öncesinde engellenir:

- üst kaynak lisansı bilinmiyorsa;
- non-commercial, research-only veya academic-only kısıtı varsa;
- türev lisansı üst kaynaktan daha izin verici görünüyorsa;
- lineage doğrulanmamışsa.

Bu durumda `DERIVED_DATASET_LICENSE_CONFLICT` veya ilgili kısıt kodu üretilir. Manuel inceleme olmadan veri dataset'e alınmaz. Download manifest, o anda geçerli lineage kimliğini ve effective license kararını snapshot olarak taşır.

## ResPlan

Adapter; vector wall, room, door ve window geometrilerini 0–1 koordinata çevirir, polygon doğrulaması yapar, oda tipini korur ve her etiketi kaynak proje ile bağlar. Ancak resmi paketin üst kaynakları emlak ilanlarından türetilmiş ve kimlik/lisans ayrıntıları yayımlanmamışsa gerçek indirme `REVIEW_REQUIRED` kalır. Adapter test fixture'larıyla doğrulanabilir; bu, gerçek verinin eğitim izni aldığı anlamına gelmez.

## GNI BIM

GNI paketleri güvenli DocumentVersion akışından ve mevcut IFC Intelligence servisinden geçer. IFC GlobalId, storey ve source document ilişkisi korunur. Büyük resmi ZIP dosyasındaki pilot IFC dosyaları HTTP Range ile seçilerek alınabilir; bütün arşivi belleğe veya diske indirme zorunluluğu yoktur.

## IFC → 2D ground truth

Renderer, her `IfcBuildingStorey` için plan görünüşü üretir. Desteklenen başlangıç sınıfları WALL, DOOR, WINDOW, COLUMN, STAIR ve ROOM'dur. Ürün geometrisi dünya koordinatına taşınır, footprint çıkarılır, ortak normalleştirilmiş koordinat modeline çevrilir ve deterministik PNG/JSON türevleri yazılır.

Her annotation şu lineage bilgisini taşır:

- source DocumentVersion;
- IFC GlobalId ve IFC class;
- storey kimliği;
- projection parameters;
- renderer version;
- geometri hash'i;
- `BIM_DERIVED_SILVER` kalite katmanı.

Renderer çıktısı insan çizimi değildir. Ayrı source family olarak raporlanır ve gerçek çizim hedefini tek başına karşılamaz.

## Dataset adayı ve hazırlık

Hazırlık V2 sınıf bazında GOLD, real drawing, BIM-derived, external-object, synthetic ve weak adetlerini ayrı raporlar. WALL/ROOM/DOOR/WINDOW için gerçek çizim eşiği; COLUMN/STAIR için BIM veya gerçek veri eşiği aranır. Kaynak aile çeşitliliği ve tek kaynak yoğunluğu ayrıca kontrol edilir.

Dataset candidate oluşturulurken gerçek/governed örnekler korunur. Sentetik veri sınıf dengeli round-robin seçimle azaltılır ve aday içindeki oranı yüzde 40'ı aşmaz. Veritabanındaki sentetik kayıtlar silinmez; yalnız dataset üyeliği sınırlandırılır. Eğitim eşiği karşılanmıyorsa model eğitimi başlatılmaz.

## Güvenlik ve idempotency

- Bilinmeyen upstream lisans sessizce kabul edilmez.
- Aynı proje veya IFC tekrar işlendiğinde duplicate annotation/render oluşmaz.
- Orijinal dosyalar değiştirilmez.
- Parser veya renderer hatası ilgili model düzeyinde kaydedilebilir; çalışan batch'in daha önce tamamlanan kayıtları korunur.
- Sentetik üretim varsayılan bootstrap davranışı değildir ve açıkça etkinleştirilmelidir.
