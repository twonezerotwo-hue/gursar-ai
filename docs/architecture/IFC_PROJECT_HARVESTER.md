# IFC Project Harvester

Harvester yeni bir IFC parser yazmaz. Onaylı harici IFC, güvenli `DocumentVersion` sonrasında mevcut `ProjectGraphService.analyze_ifc` akışına gönderilir. Building, level, element, Qto, geometry ve evidence aynı ortak domain modellerinde tutulur.

Özet; kat ve eleman dağılımı, Qto coverage, geometry coverage ve GlobalId uniqueness içerir. Aynı document version tekrar işlendiğinde duplicate oluşmaz. IFC parser regression corpus rolü ayrı tutulabilir. Estimator calibration varsayılanı `false` değeridir.

IFC ile 2D çizim eşleşmesi için `CrossModalProjectLink` yalnız ilişki ve alignment durumu saklar; otomatik ground truth kabul etmez.

V2 `Ifc2DGroundTruthRenderer`, kat bazında deterministik plan türevi ve annotation üretir. Her annotation IFC GlobalId, storey, source DocumentVersion, projection parameters ve renderer version ile bağlıdır. Bu veri `BIM_DERIVED_SILVER` katmanındadır; insan çizimi veya GÜRSAR GOLD sayılmaz. Aynı document/version yeniden işlendiğinde render, annotation ve lineage kayıtları çoğalmaz.
