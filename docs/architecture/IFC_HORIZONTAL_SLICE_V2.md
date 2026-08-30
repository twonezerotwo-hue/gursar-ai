# IFC Horizontal Slice V2

`IFC_HORIZONTAL_SLICE_V2`, kat kotuna konfigüre edilen kesit yüksekliğini ekleyerek IfcOpenShell üçgen mesh'lerini yatay düzlemle keser. Kapalı section halkaları `EXACT_SECTION`; kesit üretilemezse projeksiyonun convex hull çıktısı `CONVEX_HULL_FALLBACK` olarak etiketlenir. Başarısız geometri miktar üretmez.

Geometri cache anahtarı document SHA-256, IFC GlobalId, storey, geometri ayar sürümü ve kesit yüksekliğidir. Annotation lineage; DocumentVersion, GlobalId, storey, slice height, extraction mode, renderer version ve source geometry hash içerir.

IfcSpace `ROOM` olur; geometri yoksa oda uydurulmaz. `IfcRelVoidsElement` ve `IfcRelFillsElement` ilişkileri door/window → opening → host wall zincirini korur. V1 kayıtları audit için kalır; V2 ayrı renderer version olarak yazılır.
