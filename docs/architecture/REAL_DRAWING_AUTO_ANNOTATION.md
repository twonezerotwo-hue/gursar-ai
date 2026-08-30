# Real Drawing Auto-Annotation

Cross-modal projection sınıfları WALL, ROOM, DOOR, WINDOW, COLUMN ve STAIR ile sınırlıdır. İlk kaynak tipi `CROSS_MODAL_AUTO_ANNOTATION`, durumu `REVIEW_REQUIRED` ve training izni kapalıdır.

Varsayılan `AUTO_SILVER_ENABLED=false` değeridir. Otomatik promotion ancak açık politika, verified alignment, yüksek vector agreement, geçerli geometri ve uygun haklar birlikte sağlanırsa mümkündür. Normal akışta mühendis ACCEPT/CORRECT/REJECT verir. ACCEPT edilen cross-modal kayıt `GURSAR_EXPERT_VERIFIED` yani Gold olur; correction yeni immutable revision üretir.

Lineage alignment, IFC annotation, GlobalId, vector agreement, clipping ve projection matrisi taşır. Böylece her etiket kaynak pafta ve IFC elemanına geri izlenebilir.
