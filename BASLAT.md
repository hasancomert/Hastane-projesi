# Hızlı başlangıç (Windows dizüstü)

1. **İndir:** GitHub'da `ccr-11fe526f-w5m6uk` dalını ZIP olarak indirin
   (https://github.com/hasancomert/Hastane-projesi/archive/refs/heads/ccr-11fe526f-w5m6uk.zip) ve bir klasöre çıkarın
   (ör. `C:\Hastane`). Daha yeni bir sürümü aynı klasörün üzerine çıkarırsanız kurulum (`.venv`) korunur.
2. **Çalıştır:** klasördeki **`start.bat`** dosyasına çift tıklayın.
   - İlk seferde sanal ortam kurulur ve paketler indirilir (1–3 dakika, **internet gerekir**). Sonraki açılışlar birkaç saniyedir.
   - Tarayıcı kendiliğinden `http://127.0.0.1:8000` adresini açar. Açılmazsa adresi elle yazın.
   - Windows "Bilinmeyen yayımcı / Bilgisayarınız korundu" uyarısı verirse: *Ek bilgi → Yine de çalıştır*.
   - Sunucuyu kapatmak için siyah pencereyi kapatın.
3. **Yedek (paket/internet yoksa):** **`sunum_yedek.bat`** — hiçbir paket gerektirmez; sayfayı `http://127.0.0.1:8001`
   adresinde açar ve önceden çözülmüş 3 km varsayılan çözümü "çevrimdışı mod" ile gösterir (canlı çözüm kapalı).

Gereksinim: 64-bit Python 3.12–3.14 (python.org; kurulumda "Add python.exe to PATH" işaretli).
Sunum akışı için: `docs/SUNUM_REHBERI.md`.
