# Cross-Modal Alignment

Aynı proje paketindeki IFC ile PDF/DXF/SVG dokümanları `CrossModalProjectLink` altında eşleşir. Seviye eşlemesi kat adı, pafta başlığı, package yapısı ve gerektiğinde manuel anchor noktalarıyla yapılır.

2B transform OpenCV partial-affine RANSAC ile translation, rotation, uniform scale ve olası reflection üretir. Inlier sayısı/oranı, ortalama-medyan reprojection error, ölçek ve rotation kaydedilir. Eşikler config'dedir. Otomatik sonuç `REVIEW_REQUIRED`; yalnız uzman kabulü `VERIFIED` yapar.

Verified transform IFC V2 geometrisini çizim koordinatına taşır. Sayfa sınırı, minimum alan ve polygon geçerliliği kontrol edilir. Vektör adayla düşük uyum `DetectionConflict` üretir. Aynı IFC annotation/alignment çifti ikinci kez candidate üretmez.
