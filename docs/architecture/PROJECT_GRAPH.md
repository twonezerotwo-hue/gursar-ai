# Project Graph V1

GÜRSAR AI proje zekâsının ortak veri omurgası `project_graph` modülüdür. IFC, ileride DXF,
vektör PDF, raster/vision ve manuel ölçüm aynı `BuildingElement` ve kanıtlı `Measurement`
modeline normalize edilir.

## Miktar önceliği

1. `VERIFIED_PROJECT_MEASUREMENT`
2. `PROJECT_GEOMETRY`
3. `IFC_QUANTITY`
4. `CALCULATED_GEOMETRY`
5. `ESTIMATION_ENGINE`

LLM miktar veya geometri hesabı yapmaz. Geometri backend tarafından yeniden hesaplanır.
`TakeoffItem.measurement_ids` boş bırakılamaz; servis katmanı her ölçümle birlikte tekil
`MeasurementEvidence` oluşturur.

## IFC akışı

1. Dosya imzası kontrol edilir, SHA-256 alınır ve proje belge sürümü oluşturulur.
2. IfcOpenShell bina, kat, eleman, property set ve `Qto_*` quantity setlerini okur.
3. IFC quantity bulunmayan elemanlarda geometri motoru denenir.
4. Aynı nicelik hem Qto hem geometriden geldiyse yüzde 5 üstü fark `QuantityConflict` olur.
5. Eleman, ölçüm, kanıt ve toplulaştırılmış takeoff satırları tek transaction içinde yazılır.
6. Aynı belge sürümünün tekrar analizi yeni satır üretmez.

## Manuel ölçüm

`ViewRegion` sayfa içindeki bağımsız ölçek alanıdır. `ScaleCalibration` gerçek uzunluk / piksel
uzunluğu oranını tutar. LENGTH, AREA ve VOLUME için doğrulanabilir ölçek zorunludur; COUNT
ölçeksiz çalışabilir. İstemcinin gönderdiği hazır quantity kabul edilmez.

## V1 sınırı

IFC geometri oluşturma, temsil içermeyen veya IfcOpenShell tarafından triangüle edilemeyen
elemanlarda atlanır. DXF semantik eşleme, vektör PDF title-block/sheet classification ve
görsel annotation arayüzü sonraki fazdadır.
