# Vector PDF Intelligence

## İşleme sırası

PDF güvenlik kontrolü ve SHA-256 `DocumentVersion` kaydından sonra sayfalar tek tek işlenir. Öncelik native vektör yapı ve koordinatlı PDF metnidir; OCR yalnız native metin yetersizse çalışır. `VECTOR_PDF_INTELLIGENCE_V1`, aynı belge sürümü için idempotency anahtarıdır.

## Sayfa ve koordinatlar

Her `Sheet`, `VECTOR`, `RASTER`, `HYBRID` veya `UNKNOWN` olarak sınıflandırılır. Sayfa boyutu PDF point cinsinden korunur; metin, bölge ve geometri ayrıca 0–1 koordinatlarına normalize edilir. Evidence her iki gösterimi de saklar.

## Antet ve pafta metadatası

Alt-sağ metin yoğunluğu, antet anahtar kelimeleri, pafta numarası, revizyon ve ölçek kalıpları deterministik antet adayını üretir. Pafta numarası, ad, revizyon, disiplin ve çizim türünün güvenleri ayrı tutulur. Uzman düzeltmesi `SheetMetadataRevision` ile append-only kaydedilir ve audit olayı üretir.

## ViewRegion ve ölçek

Plan, kesit, görünüş, detay ve antet aynı paftada ayrı `ViewRegion` kayıtlarıdır. Ölçek önceliği manuel doğrulama, bölgesel ölçek metni ve pafta fallback sırasındadır. `UNKNOWN` ölçek fiziksel metrajı engeller; inferred ölçekten çıkan ölçüm `REVIEW_REQUIRED` kalır. İki noktalı manuel kalibrasyon backend tarafından hesaplanır ve bölgeyi `VERIFIED` yapar.

## Vektör geometri ve ölçüm

pdfplumber çizgi, dikdörtgen ve curve/path verisini ortak ProjectGraph geometrisine dönüştürür. Uzunluk ve kapalı dikdörtgen alanı backend tarafından ölçeklenir. Her `Measurement`, belge sürümü, pafta, sayfa, ViewRegion, ham/normalize koordinat, `ScaleCalibration`, algoritma sürümü ve tekrar render edilebilir crop descriptor içeren `MeasurementEvidence` taşır.

Kapalı alan içindeki güvenilir mahal metni bir `ROOM` BuildingElement adayı oluşturabilir. Otomatik adaylar doğrulanmış sayılmaz; uzman incelemesi ve active-learning kuyruğuna gider. Genel vektör şekilleri kapı, pencere veya kolon diye zorla sınıflandırılmaz.

## Review, takeoff ve training

Measurement ve BuildingElement için mevcut `ACCEPT`, `CORRECT`, `REJECT` akışı kullanılır. Correction overwrite etmez; yeni revision üretir. Doğrulanan Vector PDF measurement ancak review sonrasında `VERIFIED_VECTOR_PDF` takeoff kaynağı olabilir. Pafta metadata correction kayıtları training-rights ve anonimleştirme kontrollerinden sonra `SHEET_CLASSIFICATION`, `TITLE_BLOCK` veya `SCALE` sample’ına dönüştürülebilir. Project-level dataset split korunur.

## Görüntüleyici

`/static/pdf-viewer.html?project=...&version=...&pages=...` sayfa gezintisi, zoom, pan ve overlay sunar. Uzunluk, alan, adet ve iki-nokta ölçek araçları yalnız geometri gönderir; gerçek miktarı backend yeniden hesaplar.

## Sınırlar

Bu sürüm vision segmentation yapmaz. Duvar çift-çizgi heuristiği ve genel sembol sınıflandırması precision kanıtı olmadan etkinleştirilmemiştir. OCR sayfa/region fallback’tir; native vektör PDF’de çalıştırılmaz.
