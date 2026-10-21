# Kaynak ve parametre kayıtları — şema

Projedeki **her sayı** ya bir kaynağa ya da açık bir **varsayım** gerekçesine bağlanır.
Bu klasör ile `data/reference/params/` makinece okunur; `data/SOURCES.md` ve arayüzdeki
tooltip'ler bunlardan üretilir (`pipeline/build_registry.py`). Elle düzenlenen tek "doğru kaynak" bu YAML dosyalarıdır.

## 1. Kaynak parçaları: `data/sources/<alan>.yaml`

Her alan (ör. `nufus`, `saglik`, `maliyet`) kendi dosyasını yazar. Dosya bir liste içerir:

```yaml
- id: tuik_adnks_2025_bulten          # benzersiz, snake_case, alan önekli olması önerilir
  title: "Adrese Dayalı Nüfus Kayıt Sistemi Sonuçları, 2025"   # belgenin özgün başlığı
  publisher: "TÜİK"
  url: "https://data.tuik.gov.tr/..."  # erişilen tam adres
  accessed: 2026-10-21                 # erişim tarihi (YYYY-AA-GG)
  published: 2026-02-09                # biliniyorsa yayım tarihi
  kind: resmi                          # resmi | akademik | veri | haber | ikincil | hesap
  archived: data/raw/nufus/ADNKS_2025.pdf   # yerel kopya (git dışı), varsa
  sha256: "…"                          # yerel kopyanın özeti, varsa
  used_for:                            # bu kaynaktan alınan değerler (kısa, Türkçe)
    - "Ankara il nüfusu 31.12.2025: 5.910.320"
  notes: "Tablo 3, s. 4"               # sayfa/tablo, uyarılar
```

`kind: haber` kaynaklar yalnız resmi kaynak bulunamadığında ya da çapraz kontrol için kullanılır.

## 2. Parametre parçaları: `data/reference/params/<alan>.yaml`

```yaml
- key: demand.beds_per_1000            # modelin kullandığı anahtar (aşağıdaki listeden)
  label: "1000 kişiye düşen hastane yatağı"   # arayüzde görünen Türkçe ad
  value: 3.12                          # varsayılan değer (nokta ondalık, SI/TL birimleri)
  unit: "yatak / 1000 kişi"
  status: kaynakli                     # kaynakli | hesap | varsayim | model_secimi
  source_ids: [saglik_siy2025_bulten]  # 'kaynakli' ve 'hesap' için zorunlu
  derivation: "31,2 yatak / 10.000 kişi ÷ 10"   # 'hesap' için zorunlu (formül + girdiler)
  rationale: "…"                       # 'varsayim' ve 'model_secimi' için zorunlu: neden bu değer
  range: [2.0, 5.0]                    # arayüz kaydırıcısı için makul aralık (gerekçeli)
  alternatives:                        # varsa başka yıl/kaynak değerleri (karşılaştırma için)
    - {label: "2024", value: 3.13, source_ids: [saglik_siy2024_bulten]}
  notes: "…"
```

- `kaynakli`: değer doğrudan bir kaynakta yazıyor.
- `hesap`: değer kaynaklı girdilerden bizim hesabımızla türetildi (formül `derivation` alanında).
- `varsayim`: kaynağı yok; gerekçe `rationale` alanında, arayüzde "varsayım" etiketiyle gösterilir.
- `model_secimi`: modelleme tercihi (ör. α = 1, gidiş-dönüş çarpanı 2); gerekçesi yazılır.

## 3. Parametre anahtarları (model bu adları kullanır)

| Grup | Anahtarlar |
|---|---|
| Nüfus | `population.province_2025`, `population.province_2030`, `population.grid_source` |
| A. Talep | `demand.beds_per_1000`, `demand.safety_margin`, `demand.hb.admissions_per_1000`, `demand.hb.alos_days`, `demand.hb.target_occupancy`, `demand.hb.current_occupancy` |
| B. Boyut | `size.general_min_beds`, `size.general_max_beds`, `size.city_min_beds`, `size.city_max_beds`, `size.class_threshold_1` (200), `size.class_threshold_2` (400) |
| C. İnşaat | `cost.unit.IV-C`, `cost.unit.V-A`, `cost.unit.V-B`, `cost.unit.V-C`, `cost.unit.V-D` (TL/m²), `cost.m2_per_bed.general`, `cost.m2_per_bed.city`, `cost.core_area_m2`, `cost.infra_ratio`, `cost.equipment_ratio`, `cost.vat_rate`, `cost.include_vat` |
| D. Arsa | `land.include`, `land.m2_per_bed`, `land.min_parcel_m2`, `land.price_base_tl_m2`, `land.price_beta` |
| E. Sabit | `cost.fixed_per_hospital` |
| F. İşletme | `opex.include`, `opex.fixed_per_hospital_year`, `opex.per_bed_year` |
| G. Erişim | `access.visits_per_capita_total`, `access.share_hospital_visits`, `access.hospital_visits_per_capita`, `access.round_trip_factor`, `access.fuel_price_tl_per_l`, `access.fuel_l_per_100km`, `access.cost_tl_per_km`, `access.value_of_time_tl_per_h`, `access.speed_kmh`, `access.horizon_years`, `access.discount_rate`, `access.circuity`, `access.circuity_mode`, `access.circuity_profile`, `access.circuity_scale`, `access.alpha`, `access.max_distance_km` |
| H. Izgara/çözücü | `grid.cell_km`, `grid.candidate_min_pop`, `grid.k_nearest`, `solver.time_limit_s`, `solver.mip_gap`, `grid.candidate_min_inside`, `grid.candidate_max_water`, `grid.candidate_near_density` |

Bir anahtar için kaynak bulunamazsa yine yazılır: `status: varsayim` + `rationale`.
