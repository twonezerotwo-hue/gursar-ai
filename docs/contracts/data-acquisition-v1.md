# Data Acquisition V1 Contract

## Güvenlik

- Lisans doğrulanmadan download/training yoktur.
- Remote keşif açıkça etkinleştirilir; domain ve bütçe sınırları zorunludur.
- ZIP absolute path, `..`, executable, unsupported tür ve bomb limitleri reddedilir.
- Karantina production dataset değildir.
- Duplicate archive/file SHA-256 ile yeniden alınmaz.
- Destructive cleanup otomatik yapılmaz.
- Public veri estimator için verified final quantity değildir.

## Sürümler

- Acquisition: `AUTONOMOUS_ACQUISITION_V1`
- License: `LICENSE_GATE_V1`
- Importer: `EXTERNAL_ANNOTATION_IMPORT_V1`
- Mapping: `GURSAR_ARCH_ONTOLOGY_V1`
- Normalization: `NORMALIZED_COORDINATES_V1`
- Synthetic: `GURSAR_SYNTHETIC_ARCH_V1`

## API

- `POST /api/v1/training/acquisition/discover`
- `GET /api/v1/training/sources/discovered`
- `GET /api/v1/training/sources/{id}`
- `POST /api/v1/training/sources/{id}/license-review|approve|reject|download|ingest`
- `POST /api/v1/training/sources/{id}/class-mappings`
- `GET /api/v1/training/vision/readiness`
- `GET /api/v1/training/acquisition/status`
- `POST /api/v1/training/synthetic/projects/generate`

Dataset split mevcut Vision Dataset Factory tarafından proje seviyesinde yapılır. Training-ready event modeli otomatik ACTIVE yapmaz.

## Full bootstrap

`POST /api/v1/training/acquisition/bootstrap`

```json
{
  "mode": "FULL_BOOTSTRAP",
  "use_remote_providers": false
}
```

İstek hızlı biçimde `QUEUED` job döndürür. Aşamalar: `DISCOVERY`, `LICENSE_CHECK`,
`SOURCE_RANKING`, `DOWNLOAD`, `IMPORT`, `IFC_HARVEST`, `SYNTHETIC_GENERATION`,
`QA`, `READINESS`, `DATASET_BUILD`, `COMPLETE`.

`GET /api/v1/training/acquisition/status`, yalnız veritabanındaki gerçek sayaçlardan
özet ve sınıf bazlı readiness sonucu üretir.
