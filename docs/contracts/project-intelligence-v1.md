# Project Intelligence API V1

- `POST /api/v1/projects/{project_id}/documents` — imza doğrulamalı PDF/IFC yükleme
- `POST /api/v1/projects/{project_id}/analysis` — belge sürümünden IFC ProjectGraph üretme
- `GET /api/v1/projects/{project_id}/graph` — bina/kat/eleman özetleri
- `GET /api/v1/projects/{project_id}/elements` — kaynak kimlikli normalize elemanlar
- `GET /api/v1/projects/{project_id}/takeoff` — kanıt bağlantılı metraj kalemleri
- `GET /api/v1/takeoff/{item_id}/evidence` — kaynak koordinatı/ölçek/algoritma kanıtı
- `POST /api/v1/projects/{project_id}/view-regions` — sayfa içi bağımsız görünüş alanı
- `POST /api/v1/projects/{project_id}/scale-calibrations` — iki nokta veya native ölçek
- `POST /api/v1/projects/{project_id}/measurements` — backend hesaplı manuel ölçüm
- `POST /api/v1/admin/backups?dry_run=true|false` — yönetici kontrollü yedek doğrulama/oluşturma

IFC analizinin aynı `document_version_id` ile tekrarı idempotenttir. Analiz sonucu `reused=true`
döner ve eleman/metraj çoğaltmaz.
