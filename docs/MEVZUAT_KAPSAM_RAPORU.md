# Türkiye İnşaat Mevzuatı Kapsam Raporu

Snapshot tarihi: 23 Ağustos 2026

## Sonuç

Yerel bilgi tabanında 69 etkin resmî belge ve 2.554 aranabilir metin parçası vardır. Belgeler
kaynak adresi, yetkili kurum, alınma zamanı ve SHA-256 içerik özetiyle sürümlenir. Aynı içerik
yeniden alınırsa çoğaltılmaz; değişen içerik yeni sürüm olarak yazılır.

Mevcut dağılım:

| Tür | Belge sayısı |
| --- | ---: |
| Bağlı resmî mevzuat / kılavuz | 38 |
| Teknik şartname | 23 |
| Kanun | 3 |
| Yönetmelik | 2 |
| Mevzuat kataloğu | 2 |
| Teknik şartname kataloğu | 1 |

Öne çıkan içerik; 4708 sayılı Yapı Denetimi Hakkında Kanun, 6331 sayılı İş Sağlığı ve
Güvenliği Kanunu, 3194 sayılı İmar Kanunu'nun resmî aynada yayımlanan ilgili hükümleri, Planlı
Alanlar İmar Yönetmeliği, Türkiye Bina Deprem Yönetmeliği 2018, güncel yangın güvenliği kılavuzu,
Yüksek Fen Kurulu İnşaat ve Makina Tesisatı Genel Teknik Şartnameleri ile yapı malzemeleri ve
laboratuvar mevzuatıdır.

## Resmî kaynak politikası

- İçe aktarma yalnızca HTTPS kullanan `*.gov.tr` adreslerini kabul eder.
- Kataloglardan keşfedilen PDF ve Resmî Gazete HTML belgeleri aynı güvenlik kontrolünden geçer.
- Sohbet yanıtı, kullandığı pasajları `K1`–`K3` olarak işaretler ve resmî kaynak adreslerini ekler.
- Kaynakta yeterli bilgi yoksa model kesin hüküm veya madde uydurmamakla yükümlüdür.
- Yanıtlar genel bilgilendirmedir; belediye, ruhsat makamı, yapı denetim kuruluşu veya hukukçu
  kararının yerine geçmez.

## Bilinen eksikler

“Türkiye'deki bütün inşaat mevzuatı” sabit ve tek bir belge kümesi değildir. Merkezî mevzuat,
Resmî Gazete değişiklikleri, belediyelerin yerel plan notları, ihale/proje özel şartnameleri ve
lisanslı standartlar sürekli değişir. Bu nedenle bu snapshot mutlak tamlık iddiası taşımaz.

- On Bakanlık belgesi/genelgesi yalnızca taranmış görüntüdür; metin katmanı olmadığı için OCR
  kuyruğuna alınmaları gerekir.
- Bir çalıştay dosyası 40 MB güvenlik sınırını aştığı için otomatik yüklemenin dışında bırakılmıştır.
- Cumhurbaşkanlığı Mevzuat Bilgi Sistemi bu kurulum sırasında doğrudan PDF isteklerine zaman aşımı
  verdi. 3194 için resmî kurum aynasındaki ilgili hükümler kullanıldı; tam ve güncel birleştirilmiş
  metin erişilebilir olduğunda bununla değiştirilmelidir.
- TSE standartlarının tam metinleri çoğunlukla lisanslı/telif korumalıdır. Standart numarası ve
  resmî atıfları indekslenebilir; tam metin ancak kullanıcının uygun lisansı ve belgeyi sağlamasıyla
  yüklenmelidir.
- İl/ilçe belediyesi plan notları ve proje özel teknik şartnameleri ulusal korpusun parçası değildir;
  proje veya konum bazında ayrıca eklenmelidir.

## Güncelleme

```powershell
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe backend\scripts\ingest_legal_corpus.py --missing-only
```

Üretimde bu komut periyodik çalıştırılmalı, başarısız belge listesi izlenmeli ve içerik değişikliği
görülen belgeler için idari inceleme kaydı tutulmalıdır.
