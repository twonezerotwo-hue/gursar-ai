# Detection Fusion V1

Füzyon önceliği:

`VERIFIED_MANUAL > IFC > DXF > VECTOR_PDF > VISION > HEURISTIC`

Eşleştirme IoU, merkez mesafesi, sınıf ve geometri benzerliği üzerinden config eşikleriyle yapılır. Aynı sınıf ve örtüşen geometri kaynak anlaşmasıdır; güveni kontrollü artırabilir ve tek aday üretir. Sınıf çatışması `DetectionConflict` ve Active Learning kaydı oluşturur.

Structured ve doğrulanmış kaynak vision tarafından değiştirilmez. Çatışmalı aday `REVIEW_REQUIRED` kalır. Füzyon çıktısı semantik `BuildingElement` adayıdır; kendi başına ölçüm veya hakediş değildir.

