# Oskilo 4.0 — Elle test listesi

Otomatik testler (GitHub Actions, Windows + macOS + Linux) kodun her yerini çalıştırıyor; ama gerçek
kart, gerçek kablo ve gerçek bilgisayar ancak elle denenebilir. Sürümü **Latest** yapmadan önce bu
listeyi bir kez gezmek yeterli. Her madde 1–5 dakika.

## 1. Kurulum (Windows)
- [ ] `Oskilo_Setup.exe` indir → çalıştır. Mavi pencere çıkarsa **Ek bilgi → Yine de çalıştır**. Yönetici izni **sorulmamalı**.
- [ ] Kurulum Türkçe; "Masaüstü kısayolu" kutusu boş geliyor. Bitince Oskilo açılıyor.
- [ ] Başlat menüsünde **Oskilo** var. **Ayarlar → Uygulamalar**'da "Oskilo 4.0.0" görünüyor.
- [ ] Eski zip sürümünün ayarları (kayıt klasörü, tema, birimler) yeni kurulumda da duruyor.
- [ ] Oskilo açıkken kurulumu tekrar çalıştır: "uygulamayı kapat" önerisi çıkıyor, sorunsuz bitiyor.

## 2. Kart ile kayıt
- [ ] USB (STM32): bağlan, 10 dk kayıt, kopar. Kayıp **0**, dosya Belgeler\Oskilo'da.
- [ ] Ağ kartı (W5500 / ESP32): port listesinde **🌐 kart adı** görünüyor, tek tıkla bağlanıyor.
- [ ] Kayıt sırasında USB kablosunu çek → "yeniden bağlanıyor" → tak → kayıt devam ediyor, veri kaybolmadı.
- [ ] Kayıt sırasında kartı **reset**'le → sıra hatası 1 kez sayılıyor, uygulama donmuyor, sahte "25 bin kayıp" yazmıyor.
- [ ] 921600 baud, 1 kHz, 4–8 kanal: 15 dk boyunca kayıp 0, grafik akıcı.

## 3. Uzun test (bir gece)
- [ ] 1 kHz kayıt, gece boyunca açık kalsın. Sabah: Görev Yöneticisi'nde Oskilo'nun belleği akşamkinden çok farklı değil (±100 MB).
- [ ] Grafik her ~17 dakikada (1 milyon satır) sıfırlanmıyor; pencere kesintisiz akıyor.
- [ ] Otomatik kayıt dosyası güncel; Excel seçiliyse bilgisayar kasmıyor.

## 4. Zayıf bilgisayar / küçük ekran
- [ ] 1366×768 ekran, Windows ölçeği **%125** ve **%150**: pencere ekrana sığıyor, alt durum çubuğu görünüyor, grafik araçları simgeye iniyor (üzerine gelince adı çıkıyor).
- [ ] **🎨 → Hafif mod** açılıyor; grafik saniyede bir güncelleniyor, kayıt devam ediyor. Kapatınca normale dönüyor.
- [ ] Filtre (alçak geçiren 5 Hz, medyan 5) açıkken 8 kanal 1 kHz'de pencere donmuyor.

## 5. Dosyalar
- [ ] CSV'yi **Türkçe Excel**'de aç → "Farklı kaydet → CSV (noktalı virgülle ayrılmış)" → Oskilo'da **Dosya aç**: değerler doğru (2,5 → 2.5), kanallar aynı.
- [ ] 1 milyon satırlık bir dosyayı aç: pencere donmuyor, durum çubuğunda "Dosya açılıyor…" görünüyor.
- [ ] Kayıt dosyası Excel'de açıkken otomatik kayıt: hata bandı çıkıyor, Excel kapanınca yeni adla kaydediliyor.
- [ ] Türkçe karakterli / boşluklu klasöre kayıt (ör. `Belgeler\Ölçüm Sonuçları`).

## 6. Güncelleme
- [ ] 3.3.0 (zip) → **? → Güncellemeleri denetle** → 4.0.0'a güncelleniyor, ayarlar korunuyor.
- [ ] Kurulum programıyla kurulu sürümde de aynı şey çalışıyor; güncellemeden sonra **Uygulamalar** listesinde sürüm numarası yeni sürümü gösteriyor.

## 7. Kaldırma
- [ ] **Uygulamalar → Oskilo → Kaldır**: "ayarlarınız da silinsin mi?" sorusu çıkıyor. **Hayır** → yeniden kurunca ayarlar duruyor. **Evet** → ilk açılış gibi geliyor.
- [ ] Her iki durumda da Belgeler\Oskilo'daki kayıtlar silinmedi.

## 8. macOS (varsa)
- [ ] DMG → Applications → ilk açılışta **Yine de Aç**. DEMO bağlan, 1 dk kayıt, CSV aç.

---
Bir madde tutmazsa: **? → Hata bildir / öneri** ya da GitHub'da "Issues". Hata penceresi çıktıysa
`%LOCALAPPDATA%\Oskilo\oskilo_hata.txt` dosyasını ekleyin.
