# Real Drawing Acquisition V3

Bu faz gerçek PDF, DXF ve SVG çizimleri `DocumentVersion` ve `ExternalProjectPackage` üzerinden alır. Her kaynak için beyan edilen lisans, upstream lisans ve etkin lisans zinciri doğrulanır. Upstream kaynağı bilinmeyen veya ticari eğitime izin vermeyen veri indirilse dahi training dataset'e alınmaz.

`SemanticSvgImporter` grup transformlarını miras alır; `path`, `polygon`, `polyline`, `rect` ve `line` geometrilerini normalize eder. Semantik adaylar `DETERMINISTIC_VECTOR_CANDIDATE / REVIEW_REQUIRED` olarak oluşturulur. Otomatik aday hiçbir zaman gerçek çizim Silver veya Gold değildir.

Kalite skoru; lisans açıklığı, vektör/ölçek/annotation varlığı, proje bütünlüğü, revision ve IFC eşleşmesi faktörlerinden oluşur. Orijinal dosya değişmez; her türetilmiş kayıt kaynak ve sürüm izini taşır.
