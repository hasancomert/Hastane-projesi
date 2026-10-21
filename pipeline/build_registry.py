"""Faz 1 — kaynak ve parametre kayıtlarını birleştirir, doğrular; parameters.json ve SOURCES.md üretir.

Girdiler
  data/sources/README.md              şema: izinli durumlar/türler, zorunlu alanlar, kanonik anahtarlar
  data/sources/*.yaml                 kaynak parçaları (alan başına)
  data/reference/params/*.yaml        parametre parçaları (alan başına)
  data/raw/**/_manifest.jsonl         indirme kayıtları (url, tarih, sha256) — git dışı, varsa denetlenir

Çıktılar
  data/reference/parameters.json      anahtar -> {label, value, unit, status, source_ids, derivation, rationale,
                                      range, alternatives, notes, group, domain, sources}; backend/frontend bunu okur
  data/SOURCES.md                     Türkçe kaynak listesi, parametre tablosu, varsayımlar, Bölüm 3 karşılaştırması

Hata varsa çıktı yazılmaz ve çıkış kodu 1 olur. Uyarılar çıktıyı engellemez.
Çalıştırma (repo kökünden):  .venv/bin/python pipeline/build_registry.py [--no-sha]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline import common  # noqa: E402

README = common.SOURCES_DIR / "README.md"
OUT_JSON = common.REFERENCE / "parameters.json"
OUT_MD = common.DATA / "SOURCES.md"
TUIK_CSV = common.REFERENCE / "tuik_adnks_ankara.csv"

# Alan dosyası -> (Türkçe başlık, kaynak kimliği öneki). Sıra SOURCES.md'deki sıradır.
DOMAINS: dict[str, tuple[str, str]] = {
    "nufus": ("Nüfus — TÜİK ADNKS ve il projeksiyonu", "tuik_"),
    "sinirlar": ("İdari sınırlar", "sinirlar_"),
    "nufus_izgara": ("Nüfus ızgaraları (GHS-POP, WorldPop, Kontur)", "nufus_izgara_"),
    "arazi_osm": ("Arazi örtüsü, OSM yol ağı ve dolambaç katsayısı", "arazi_osm_"),
    "saglik": ("Sağlık istatistikleri (talep, Hill-Burton, hekime müracaat)", "saglik_"),
    "maliyet": ("İnşaat birim maliyetleri, hastane boyutu, arsa alanı", "maliyet_"),
    "yatirim": ("2026 Kamu Yatırım Programı ve sabit maliyet regresyonu", "yatirim_"),
    "erisim": ("Erişim maliyeti, arsa fiyatı, işletme gideri, ızgara/çözücü", "erisim_"),
    "alan_literatur": ("Ankara yüzölçümü kaynakları ve literatür", "alan_literatur_"),
}

STATUS_TR = {"kaynakli": "kaynaklı", "hesap": "hesap", "varsayim": "varsayım", "model_secimi": "model seçimi"}
KIND_TR = {"resmi": "resmî", "akademik": "akademik", "veri": "veri", "haber": "haber",
           "ikincil": "ikincil", "hesap": "hesap"}

SOURCE_REQUIRED = ("id", "title", "publisher", "url", "accessed", "kind")
PARAM_REQUIRED = ("key", "label", "value", "unit", "status")

# Entegrasyon adımında parçalarda yapılan elle düzeltmeler (değer değişikliği YOK). SOURCES.md'ye yazılır.
INTEGRATION_CHANGES: list[str] = [
    "data/reference/params/talep.yaml — `demand.hb.admissions_per_1000` ile `demand.beds_per_1000` alternatiflerindeki "
    "(3,1458 ve 2,5841) `source_ids` listelerine resmî TÜİK kaynağı `tuik_bt_adnks2025_yogunluk` eklendi "
    "(Türkiye nüfusu 31.12.2025 = 86.092.168, sayfa 't3', satır 5, sütun B; dosyadan okunarak doğrulandı). "
    "Haber kaynağı `saglik_karar_adnks2025` çapraz kontrol olarak korundu. Değer değişmedi.",
]

# ---------------------------------------------------------------- Bölüm 3 karşılaştırması (alan raporlarından)
# durum: teyit | çelişki | bulunamadı (+ kısa niteleme). source_ids doğrulanır.
SPEC_SECTION3: list[dict] = [
    dict(konu="Ankara nüfusu 31.12.2025 (TÜİK ADNKS)", spec="5.910.320 [1], [2]", bulunan="5.910.320",
         durum="teyit",
         yer="TÜİK istatistiksel tablo sayfa '2305' satır 70; bülten tablosu 't3' satır 11; bülten metni; "
             "haber [1] ve [3] aynı",
         source_ids=["tuik_st_il_ilce_2025", "tuik_bt_adnks2025_yogunluk", "tuik_adnks2025_bulten",
                     "tuik_haber_yeniankara_adnks2025", "tuik_haber_karar_adnks2025"]),
    dict(konu="İlçe nüfusları 2025 (25 ilçe)", spec="ATO 'Rakamlarla Ankara' Tablo 5–6 [2]",
         bulunan="25 ilçe doğrudan TÜİK'ten; toplam tam 5.910.320",
         durum="teyit (TÜİK); [2] erişilemedi",
         yer="TÜİK tablo sayfa '2305' satır 71–95; atonet.org.tr bağlantıyı sıfırlıyor, www.atonet.org.tr vekil 403",
         source_ids=["tuik_st_il_ilce_2025", "tuik_df_adnks_t22", "tuik_ato_rakamlarla_ankara_2026"]),
    dict(konu="Çankaya / Keçiören 2025", spec="952.198 / 931.722 [3]", bulunan="952.198 / 931.722",
         durum="teyit", yer="TÜİK tablo satır 77 / 87; bülten metni; Karar [3]",
         source_ids=["tuik_st_il_ilce_2025", "tuik_adnks2025_bulten", "tuik_haber_karar_adnks2025"]),
    dict(konu="Yüzölçümü — göller hariç", spec="24.521 km² [4]", bulunan="24.521 km² (birebir alıntı)",
         durum="teyit", yer="[4] PDF s. 2 (aynı metin s. 32); TÜİK 2023 yoğunluk tablosunun örtük alanı da 24.521,00",
         source_ids=["alan_literatur_csb_ref4_altindag", "tuik_bt_adnks2023_yogunluk"]),
    dict(konu="Yüzölçümü", spec="25.706 km² [4]", bulunan="25.706 km² (birebir alıntı)", durum="teyit",
         yer="[4] PDF s. 2; göl dahil/hariç yazmıyor, kaynak göstermiyor",
         source_ids=["alan_literatur_csb_ref4_altindag"]),
    dict(konu="Yüzölçümü", spec="25.632 km² [5]", bulunan="25.632 km² (birebir alıntı)", durum="teyit",
         yer="[5] PDF s. 3; HGM tablosunun iki ikincil kopyası ve Valilik ÇDR 2018 Çizelge E.85 toplamı da 25.632; "
             "COD-AB özniteliği 25.631,76",
         source_ids=["alan_literatur_csb_ref5_beypazari", "alan_literatur_hgm_ikincil_snrylmz",
                     "alan_literatur_valilik_cdr_2018", "sinirlar_hdx_codab_tur"]),
    dict(konu="Yüzölçümü", spec="25.978 km² [6]", bulunan="25.978 km² (birebir alıntı)", durum="teyit",
         yer="[6] PDF s. 3; metin 'Ankara şehrinin' diyor (il değil), kaynak göstermiyor",
         source_ids=["alan_literatur_csb_ref6_cankaya"]),
    dict(konu="Yüzölçümü — ek değerler (Bölüm 3'te yok)", spec="—",
         bulunan="26.897 (Polatlı raporu 2025); 24.842,63 (TÜİK 2024–2025 yoğunluk esası, göller hariç, hesap); "
                 "CORINE 25.476,25 / 25.654,75; poligon (EPSG:6933) geoBoundaries 25.647,90, Kontur 25.568,17, "
                 "COD-AB 25.631,76",
         durum="ek bulgu", yer="alan karşılaştırma tablosuna (Faz 2 kartı) eklenecek",
         source_ids=["alan_literatur_csb_polatli_2025", "tuik_bt_adnks2025_yogunluk", "alan_literatur_valilik_cdr_2018",
                     "sinirlar_secim_hesap"]),
    dict(konu="10.000 kişiye hastane yatağı, Türkiye 2023", spec="31,2 → b ≈ 3,12 [7]", bulunan="31,2",
         durum="teyit", yer="SİY 2023 Tablo 7.19 (PDF s. 182). Varsayılan öğrenci kararıyla 2025 = 31,5 → 3,15",
         source_ids=["saglik_sbsgm_siy2023", "saglik_sbsgm_bulten2025"]),
    dict(konu="Türkiye 2024 yatak ve hastane", spec="268.359 yatak; 1.562 hastane (941 / 69 / 552) [8], [9]",
         bulunan="268.359; 1.562 (941 / 69 / 552)", durum="teyit",
         yer="SİY 2024 Haber Bülteni Tablo 6 (PDF s. 4) ve Tablo 7 (PDF s. 5); SİY 2024 Tablo 7.20 (PDF s. 192)",
         source_ids=["saglik_sbsgm_bulten2024", "saglik_sbsgm_siy2024"]),
    dict(konu="Hekime müracaat 2023",
         spec="kişi başı 11,4; toplam 973,52 M; SB hastaneleri 424,55 M, özel 67,44 M, üniversite 45,53 M; "
              "aile hekimliği 416,81 M [14]",
         bulunan="11,4; 973.519.087; 424.550.715; 67.440.929; 45.534.172; 416.806.846", durum="teyit",
         yer="SİY 2023 Haber Bülteni Tablo 14 (PDF s. 8). Not: SİY 2024 Tablo 8.1 toplamı 973.518.842'ye revize ediyor "
             "(−245)",
         source_ids=["saglik_sbsgm_bulten2023", "saglik_sbsgm_siy2024"]),
    dict(konu="Hekime müracaat 2024", spec="kişi başı 12,2; %43,3 birinci, %56,7 ikinci+üçüncü basamak [9]",
         bulunan="12,2; %43,3 / %56,7", durum="teyit",
         yer="SİY 2024 Haber Bülteni Tablo 16 ve metin (PDF s. 9)", source_ids=["saglik_sbsgm_bulten2024"]),
    dict(konu="Kişi başı yıllık hastane başvurusu (hesap)", spec="12,2 × 0,567 ≈ 6,9", bulunan="6,92 (hesap)",
         durum="teyit", yer="Varsayılan öğrenci kararıyla 2025: 13,2 × 0,587 = 7,75",
         source_ids=["saglik_sbsgm_bulten2024", "saglik_sbsgm_bulten2025"]),
    dict(konu="2026 yapı yaklaşık birim maliyetleri",
         spec="RG 03.02.2026/33157; IV-C 40.500, V-B 43.850, V-C 48.750, V-D 53.500, V-A 42.350 TL/m²; KDV hariç; "
              "%15 genel gider + %10 kâr dahil; arsa, çevre düzenlemesi, altyapı hariç [10]",
         bulunan="aynı değerler ve aynı koşullar", durum="teyit",
         yer="Resmî Gazete aslı, Tebliğ md. 1 ve md. 2(4); alomaliye [10] çapraz kontrol",
         source_ids=["maliyet_rg_2026_birim_maliyet_teblig", "maliyet_alomaliye_2026_teblig"]),
    dict(konu="Yatak başına alan (genel hastane)",
         spec="2010 Kılavuzu: '1200 yataklı hastaneler için' asgari 130 m²/yatak [11]",
         bulunan="130 m² teyit; ancak kaynakta '1200 yataklı' niteliği yok ve 130 resmî olarak ARSA (plan alanı) standardı",
         durum="çelişki (nitelik)",
         yer="Balcalı makalesi s. 211: 'İhtisas hastaneleri … yatak sayısı × 130 m²'; '1200' Balcalı'nın kendi yatak "
             "sayısı. Mekânsal Planlar Yapım Yönetmeliği EK-2: 130 m²/yatak sağlık tesisi ALANI. 2026 KYP yeni SB "
             "genel hastaneleri medyan 250 m²/yatak. Kılavuzun kendisi indirilemedi (vekil 403). 130 varsayılan korundu",
         source_ids=["maliyet_balcali_2019", "maliyet_ehta_2021", "maliyet_mpyy_ek2", "maliyet_hesap_kyp_alan",
                     "maliyet_sb_asgari_tasarim_2010"]),
    dict(konu="Balcalı Hastanesi alan/yatak", spec="≈159 m²/yatak [11]", bulunan="158,9 (190.651 ÷ 1.200)",
         durum="teyit", yer="Balcalı makalesi", source_ids=["maliyet_balcali_2019"]),
    dict(konu="Şehir hastaneleri: Elazığ / Eskişehir / Kayseri",
         spec="1.038 yatak; 1.081 yatak, 333.303 m² (≈308); 1.607 yatak [12]",
         bulunan="1.038; 1.081 ve 333.303 m² (308,3); 1.607", durum="teyit",
         yer="Memurlar.net 2018 (Elazığ'ın 355.752 m²'si kapalı alan mı arsa mı belirsiz)",
         source_ids=["maliyet_memurlar_2018"]),
    dict(konu="Bilkent yatak sayısı", spec="3.704 [12]; 3.810 [13]; 4.050 [16]",
         bulunan="3.704 / 3.711 / 3.810 / 4.050", durum="teyit (kaynaklar arası çelişki)",
         yer="Memurlar 2018 ve Kalkınma 2017: 3.704; SB 2020 bütçe sunumu (Savaş 2020): 3.711; Medikal Akademi tablo "
             "toplamı 3.810 (aynı sayfa metni 3.704 diyor); TBMM 2025: 4.050 'fiilî ve resmî'",
         source_ids=["maliyet_memurlar_2018", "maliyet_kalkinma_ankara_2017", "maliyet_savas_2020",
                     "maliyet_medikalakademi_bilkent", "maliyet_tbmm_2025_ankara"]),
    dict(konu="Bilkent kapalı alan", spec="1.312.358 m², 3.810 yatak (≈344) [13]", bulunan="1.312.358 m² → 344,5",
         durum="teyit", yer="Medikal Akademi; 2017 planı 1.200.000 m² ÷ 3.704 = 324,0",
         source_ids=["maliyet_medikalakademi_bilkent", "maliyet_kalkinma_ankara_2017"]),
    dict(konu="Etlik yatak sayısı", spec="planlanan 3.566 [17]; 4.050 [16]", bulunan="3.566 / 3.577 / 4.050",
         durum="teyit (kaynaklar arası çelişki)",
         yer="Kalkınma 2017: 3.566; SB 2020 bütçe sunumu: 3.577; TBMM 2025: 4.050",
         source_ids=["maliyet_kalkinma_ankara_2017", "maliyet_savas_2020", "maliyet_tbmm_2025_ankara"]),
    dict(konu="Küçük/orta boyut örnekleri", spec="Bahçe 50 [18]; Dikili 75 [19]; Çayırova 200 [20]",
         bulunan="50; 75; 200 (Çayırova KYP'de 45.655 m² → 228,3 m²/yatak)", durum="teyit",
         yer="SYGM 2021 duyuruları; CNN Türk 10.03.2026; 2026 KYP proje 2017I00-145177",
         source_ids=["maliyet_sygm_bahce_2021", "maliyet_cnnturk_dikili_2026", "maliyet_sygm_cayirova_2021",
                     "yatirim_kyp2026"]),
    dict(konu="Büyük genel hastane örnekleri", spec="Ankara EAH fiilî 616; Etlik Bölge Hastanesi 718 [17]",
         bulunan="616; 718", durum="teyit", yer="Kalkınma Kütüphanesi 2017 raporu",
         source_ids=["maliyet_kalkinma_ankara_2017"]),
    dict(konu="Nüfus ızgarası GHS-POP R2023A",
         spec="100 m ve 1 km Mollweide; 1975–2030 beşer yıl; 2025 ve 2030 projeksiyon; CC BY 4.0 [21]–[23]",
         bulunan="aynı", durum="teyit",
         yer="JRC katalog sayfası; GHSL Data Package 2023 PDF s. 50–53; copyright.txt. Katalog atıf yılını artık 2026 "
             "gösteriyor ve ek DOI 10.2905/JRC.CXKEDRR veriyor (2023 atfı da listede). Türkiye girdisi TÜİK ADNKS 2010",
         source_ids=["nufus_izgara_jrc_katalog_ghs_pop", "nufus_izgara_ghsl_data_package_2023",
                     "nufus_izgara_ghs_copyright", "nufus_izgara_ghs_input_metadata"]),
    dict(konu="Nüfus ızgarası Kontur", spec="400 m H3 altıgenleri; GHSL, Facebook, Microsoft Buildings, Copernicus, "
                                            "OSM füzyonu [24], [25]",
         bulunan="H3 çözünürlük 8 (ortalama 0,737 km², Ankara kutusunda 0,812 km²); füzyon tanımı HDX'te teyit",
         durum="çelişki (çözünürlük)",
         yer="'400m' HDX başlığı; hücreler gerçekte H3 r8. Hangi GHSL dönemine dayandığı belgelenmemiş (kontur.io 403)",
         source_ids=["nufus_izgara_kontur_population_tr_20231101", "nufus_izgara_hdx_kontur_population_meta"]),
    dict(konu="Hill-Burton girdileri", spec="Henüz alınmadı; 2024 Haber Bülteni'nden alınacak [26]",
         bulunan="2025: doluluk %54,5, ortalama kalış 4,0 gün, devir hızı 50,3 → yatış 158,23/1000 (hesap); "
                 "2024: %59,8, 4,2 gün, 13.881.750 yatan (162,05/1000)",
         durum="bulundu (Bölüm 3'te değer yoktu)",
         yer="SİY 2025 Haber Bülteni Tablo 16 (PDF s. 10); SİY 2024 Tablo 8.26 (PDF s. 236). 2025 tam yıllığı henüz "
             "yayımlanmadı; 2025'teki düşüş yıllıkla yeniden kontrol edilmeli",
         source_ids=["saglik_sbsgm_bulten2025", "saglik_sbsgm_bulten2024", "saglik_sbsgm_siy2024"]),
]

# Şartname Bölüm 4.3/4.4'teki varsayılanlar -> kayıttaki varsayılan (otomatik karşılaştırılır)
SPEC_DEFAULTS: list[tuple[str, object, str, str]] = [
    ("demand.beds_per_1000", 3.12, "3,12 (2023)", "öğrenci kararı: 2025 verisi (31,5 / 10)"),
    ("access.hospital_visits_per_capita", 6.9, "≈6,9 (2024)", "öğrenci kararı: 2025 verisi (13,2 × 0,587)"),
    ("access.circuity", 1.3, "1,3 (kaynaksız)", "Ankara OSM yol ağında ölçüldü (5–40 km, nüfus ağırlıklı)"),
    ("cost.m2_per_bed.general", 130, "130", "2026 KYP yeni SB genel hastaneleri medyan 250 m²/yatak; bkz. Bölüm 3"),
    ("cost.m2_per_bed.city", 310, "≈310", "32 hastanelik KÖİ programı toplam/toplam; Eskişehir 308,3 alternatif"),
    ("size.general_min_beds", 50, "50", ""),
    ("size.general_max_beds", 999, "999", ""),
    ("size.city_min_beds", 1000, "1.000", ""),
    ("size.city_max_beds", 4000, "4.000", "Bilkent, Etlik, Sancaktepe 4.050 (alternatif)"),
    ("size.class_threshold_1", 200, "200", ""),
    ("size.class_threshold_2", 400, "400", ""),
    ("access.round_trip_factor", 2, "2", ""),
    ("access.horizon_years", 25, "25", "KÖİ sözleşmeleri 25 yıl teyit edildi"),
    ("access.alpha", 1, "1", ""),
    ("access.max_distance_km", 30, "30 (varsayım)", "resmî erişim standardı bulunamadı"),
    ("land.include", False, "hayır (kamu arazisi; varsayım)", ""),
    ("opex.include", False, "hayır", ""),
    ("population.province_2025", 5910320, "5.910.320", ""),
    ("population.province_2030", None, "GHS-POP 2030 dağılımı + varsayımsal büyüme",
     "TÜİK resmî il projeksiyonu bulundu; il toplamı için büyüme varsayımı gerekmiyor (dağılım GHS-POP 2030)"),
]


# ---------------------------------------------------------------- yardımcılar
@dataclass
class Issue:
    level: str   # HATA | UYARI
    where: str
    msg: str

    def __str__(self) -> str:
        return f"[{self.level}] {self.where}: {self.msg}"


@dataclass
class Schema:
    statuses: list[str]
    kinds: list[str]
    required_by_status: dict[str, set[str]]   # alan adı -> hangi durumlarda zorunlu
    source_fields: list[str]
    param_fields: list[str]
    canonical: dict[str, str]                 # anahtar -> grup (README sırası)


@dataclass
class Registry:
    schema: Schema
    sources: dict[str, dict] = field(default_factory=dict)      # id -> kayıt (+ _domain)
    params: dict[str, dict] = field(default_factory=dict)       # key -> kayıt (+ _domain)
    issues: list[Issue] = field(default_factory=list)
    missing_canonical: list[str] = field(default_factory=list)
    unknown_keys: list[str] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "HATA"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "UYARI"]


def _yaml_blocks(text: str) -> list[str]:
    return re.findall(r"```yaml\n(.*?)```", text, flags=re.S)


def parse_readme(path: Path = README) -> Schema:
    """README.md'den şemayı okur (durumlar, türler, zorunluluklar, kanonik anahtarlar)."""
    text = path.read_text(encoding="utf-8")
    blocks = _yaml_blocks(text)
    if len(blocks) < 2:
        raise ValueError("README.md: iki yaml şema bloğu bekleniyordu")
    src_block, par_block = blocks[0], blocks[1]

    def opts(pattern: str) -> list[str]:
        m = re.search(pattern, text)
        if not m:
            raise ValueError(f"README.md: '{pattern}' bulunamadı")
        return [s.strip() for s in m.group(1).split("|") if s.strip()]

    kinds = opts(r"kind:\s*\w+\s*#\s*([^\n]+)")
    statuses = opts(r"status:\s*\w+\s*#\s*([^\n]+)")

    def fields(block: str) -> list[str]:
        out = []
        for line in block.splitlines():
            m = re.match(r"^\s*-?\s*([a-z_0-9]+):", line)
            if m and m.group(1) not in out:
                out.append(m.group(1))
        return out

    required: dict[str, set[str]] = {}
    for line in par_block.splitlines():
        m = re.match(r"^\s*-?\s*([a-z_]+):.*#(.*için zorunlu.*)$", line)
        if m:
            required[m.group(1)] = set(re.findall(r"'(\w+)'", m.group(2)))

    canonical: dict[str, str] = {}
    sec = text.split("## 3.", 1)[1] if "## 3." in text else ""
    for line in sec.splitlines():
        if not line.startswith("|") or "---" in line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2 or cells[0] == "Grup":
            continue
        for k in re.findall(r"`([^`]+)`", cells[1]):
            canonical[k] = cells[0]
    if not canonical:
        raise ValueError("README.md: kanonik anahtar tablosu okunamadı")
    return Schema(statuses=statuses, kinds=kinds, required_by_status=required,
                  source_fields=fields(src_block), param_fields=fields(par_block), canonical=canonical)


def _is_date(v) -> bool:
    return isinstance(v, dt.date)


def _published_ok(v) -> bool:
    """Yayım tarihi: tam tarih, yıl (int) ya da 'YYYY' / 'YYYY-AA' metni."""
    if v is None or _is_date(v):
        return True
    if isinstance(v, int):
        return 1800 <= v <= 2100
    return isinstance(v, str) and re.fullmatch(r"\d{4}(-\d{2})?", v) is not None


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def load_manifests(raw: Path = common.RAW) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    if not raw.exists():
        return out
    for m in sorted(raw.rglob("_manifest.jsonl")):
        for line in m.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                rec = json.loads(line)
                out.setdefault(rec["file"], []).append(rec)
    return out


# ---------------------------------------------------------------- doğrulama
def validate(verify_sha: bool = True, root: Path = common.ROOT) -> Registry:
    """Tüm parçaları yükler ve README şemasına göre doğrular."""
    schema = parse_readme(root / "data" / "sources" / "README.md")
    reg = Registry(schema=schema)
    add = reg.issues.append
    manifests = load_manifests(root / "data" / "raw")
    raw_present = (root / "data" / "raw").exists()

    # --- kaynaklar
    for path in sorted((root / "data" / "sources").glob("*.yaml")):
        domain = path.stem
        try:
            items = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        except yaml.YAMLError as e:
            add(Issue("HATA", path.name, f"YAML okunamadı: {e}"))
            continue
        if not isinstance(items, list):
            add(Issue("HATA", path.name, "dosya bir liste olmalı"))
            continue
        prefix = DOMAINS.get(domain, (domain, domain + "_"))[1]
        if domain not in DOMAINS:
            add(Issue("UYARI", path.name, "tanınmayan alan dosyası (DOMAINS listesine eklenmeli)"))
        for s in items:
            sid = s.get("id") if isinstance(s, dict) else None
            where = f"{path.name}:{sid}"
            if not sid:
                add(Issue("HATA", path.name, f"id'siz kaynak kaydı: {str(s)[:80]}"))
                continue
            for f in SOURCE_REQUIRED:
                if s.get(f) in (None, ""):
                    add(Issue("HATA", where, f"zorunlu alan eksik: {f}"))
            if sid in reg.sources:
                add(Issue("HATA", where, f"yinelenen kaynak id (ilk: {reg.sources[sid]['_domain']})"))
                continue
            if not re.fullmatch(r"[a-z0-9_]+", sid):
                add(Issue("HATA", where, "id snake_case olmalı"))
            if not sid.startswith(prefix):
                add(Issue("UYARI", where, f"id alan önekiyle başlamıyor ({prefix})"))
            if s.get("kind") not in schema.kinds:
                add(Issue("HATA", where, f"geçersiz kind: {s.get('kind')!r}"))
            if s.get("accessed") is not None and not _is_date(s["accessed"]):
                add(Issue("HATA", where, f"accessed YYYY-AA-GG olmalı: {s['accessed']!r}"))
            if not _published_ok(s.get("published")):
                add(Issue("UYARI", where, f"published biçimi beklenmedik: {s.get('published')!r}"))
            uf = s.get("used_for")
            if not isinstance(uf, list):
                add(Issue("HATA", where, "used_for liste olmalı"))
            elif not uf:
                add(Issue("UYARI", where, "used_for boş (kaynaktan değer alınmadı)"))
            extra = set(s) - set(schema.source_fields)
            if extra:
                add(Issue("UYARI", where, f"şemada olmayan alan(lar): {sorted(extra)}"))
            url = str(s.get("url", ""))
            if not url.startswith(("http://", "https://")) and not (root / url).exists():
                add(Issue("UYARI", where, f"url ne web adresi ne de repodaki bir yol: {url}"))
            _check_archive(s, where, root, manifests, raw_present, verify_sha, add)
            reg.sources[sid] = {**s, "_domain": domain}

    # --- parametreler
    for path in sorted((root / "data" / "reference" / "params").glob("*.yaml")):
        domain = path.stem
        try:
            items = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        except yaml.YAMLError as e:
            add(Issue("HATA", path.name, f"YAML okunamadı: {e}"))
            continue
        if not isinstance(items, list):
            add(Issue("HATA", path.name, "dosya bir liste olmalı"))
            continue
        for p in items:
            key = p.get("key") if isinstance(p, dict) else None
            where = f"params/{path.name}:{key}"
            if not key:
                add(Issue("HATA", path.name, f"key'siz parametre: {str(p)[:80]}"))
                continue
            if key in reg.params:
                add(Issue("HATA", where, f"yinelenen anahtar (ilk: {reg.params[key]['_domain']})"))
                continue
            for f in PARAM_REQUIRED:
                if f not in p or p[f] is None or p[f] == "":
                    add(Issue("HATA", where, f"zorunlu alan eksik: {f}"))
            st = p.get("status")
            if st not in schema.statuses:
                add(Issue("HATA", where, f"geçersiz status: {st!r}"))
            for f, sts in schema.required_by_status.items():
                if st in sts and not p.get(f):
                    add(Issue("HATA", where, f"'{st}' için zorunlu alan eksik: {f}"))
            ids = p.get("source_ids") or []
            if not isinstance(ids, list):
                add(Issue("HATA", where, "source_ids liste olmalı"))
                ids = []
            extra = set(p) - set(schema.param_fields)
            if extra:
                add(Issue("UYARI", where, f"şemada olmayan alan(lar): {sorted(extra)}"))
            if key not in schema.canonical:
                add(Issue("UYARI", where, "kanonik listede olmayan anahtar"))
                reg.unknown_keys.append(key)
            _check_range(p, where, add)
            for j, a in enumerate(p.get("alternatives") or []):
                aw = f"{where} alternatif {j + 1}"
                if not isinstance(a, dict) or "label" not in a or "value" not in a:
                    add(Issue("HATA", aw, "alternatif {label, value} içermeli"))
                    continue
                if a.get("status") is not None and a["status"] not in schema.statuses:
                    add(Issue("HATA", aw, f"geçersiz status: {a['status']!r}"))
                if a.get("status") in ("kaynakli", "hesap") and not a.get("source_ids"):
                    add(Issue("UYARI", aw, f"'{a['status']}' alternatifinde source_ids yok"))
            reg.params[key] = {**p, "_domain": domain}

    # --- kaynak kimliklerinin varlığı (parametreler, alternatifler, Bölüm 3 tablosu)
    for key, p in reg.params.items():
        where = f"params/{p['_domain']}.yaml:{key}"
        for sid in p.get("source_ids") or []:
            if sid not in reg.sources:
                add(Issue("HATA", where, f"bilinmeyen source_id: {sid}"))
        for a in p.get("alternatives") or []:
            for sid in (a.get("source_ids") or []) if isinstance(a, dict) else []:
                if sid not in reg.sources:
                    add(Issue("HATA", where, f"alternatifte bilinmeyen source_id: {sid}"))
    for row in SPEC_SECTION3:
        for sid in row["source_ids"]:
            if sid not in reg.sources:
                add(Issue("HATA", "build_registry.SPEC_SECTION3", f"bilinmeyen source_id: {sid}"))

    reg.missing_canonical = [k for k in schema.canonical if k not in reg.params]
    for k in reg.missing_canonical:
        add(Issue("UYARI", "kanonik anahtarlar", f"eksik anahtar: {k}"))

    _consistency(reg, root)
    _duplicate_urls(reg)
    return reg


def _check_archive(s, where, root, manifests, raw_present, verify_sha, add) -> None:
    arch = s.get("archived")
    if not arch:
        if s.get("kind") != "hesap":
            add(Issue("UYARI", where, "yerel arşiv yok (archived boş)"))
        return
    p = root / arch
    if not p.exists():
        # data/raw git dışıdır; başka makinede bulunmaması hata değildir
        lvl = "HATA" if (raw_present and str(arch).startswith("data/raw")) else "UYARI"
        if not str(arch).startswith("data/raw"):
            lvl = "HATA"
        add(Issue(lvl, where, f"arşiv yolu bulunamadı: {arch}"))
        return
    if p.is_dir():
        return
    if s.get("sha256") and verify_sha:
        if common.sha256(p) != s["sha256"]:
            add(Issue("HATA", where, f"sha256 arşivle uyuşmuyor: {arch}"))
    if s.get("kind") != "hesap" and str(arch).startswith("data/raw"):
        recs = manifests.get(str(arch))
        if recs is None:
            add(Issue("UYARI", where, f"arşiv _manifest.jsonl'de kayıtlı değil: {arch}"))
        else:
            if s.get("sha256") and s["sha256"] not in {r["sha256"] for r in recs}:
                add(Issue("HATA", where, "sha256 manifest kaydıyla uyuşmuyor"))
            if _is_date(s.get("accessed")) and s["accessed"].isoformat() not in {r["accessed"] for r in recs}:
                add(Issue("UYARI", where, "accessed manifestteki indirme tarihleriyle uyuşmuyor"))


def _check_range(p, where, add) -> None:
    rng = p.get("range")
    if rng is None:
        return
    if not (isinstance(rng, list) and len(rng) == 2 and all(_num(x) for x in rng) and rng[0] <= rng[1]):
        add(Issue("HATA", where, f"range [alt, üst] sayı çifti olmalı: {rng!r}"))
        return
    v = p.get("value")
    if _num(v) and not (rng[0] <= v <= rng[1]):
        add(Issue("UYARI", where, f"değer {v} aralığın dışında {rng}"))


def _rel(a: float, b: float) -> float:
    return abs(a - b) / max(abs(b), 1e-12)


def _consistency(reg: Registry, root: Path) -> None:
    """Alanlar arası tutarlılık (yalnız uyarı)."""
    P = {k: p.get("value") for k, p in reg.params.items()}
    checks = [
        ("access.cost_tl_per_km = fuel_price × l_per_100km / 100",
         ("access.cost_tl_per_km", "access.fuel_price_tl_per_l", "access.fuel_l_per_100km"),
         lambda: (P["access.cost_tl_per_km"], P["access.fuel_price_tl_per_l"] * P["access.fuel_l_per_100km"] / 100)),
        ("access.hospital_visits_per_capita = visits_total × share",
         ("access.hospital_visits_per_capita", "access.visits_per_capita_total", "access.share_hospital_visits"),
         lambda: (P["access.hospital_visits_per_capita"],
                  P["access.visits_per_capita_total"] * P["access.share_hospital_visits"])),
        ("land.min_parcel_m2 = land.m2_per_bed × size.general_min_beds",
         ("land.min_parcel_m2", "land.m2_per_bed", "size.general_min_beds"),
         lambda: (P["land.min_parcel_m2"], P["land.m2_per_bed"] * P["size.general_min_beds"])),
        ("size.general_max_beds = size.city_min_beds − 1",
         ("size.general_max_beds", "size.city_min_beds"),
         lambda: (P["size.general_max_beds"], P["size.city_min_beds"] - 1)),
    ]
    for name, keys, fn in checks:
        if all(k in P and _num(P[k]) for k in keys):
            a, b = fn()
            if _rel(a, b) > 0.005:
                reg.issues.append(Issue("UYARI", "tutarlılık", f"{name}: {a} ≠ {b:.4g}"))
    for lo, hi in (("size.general_min_beds", "size.general_max_beds"), ("size.city_min_beds", "size.city_max_beds"),
                   ("size.class_threshold_1", "size.class_threshold_2")):
        if lo in P and hi in P and _num(P[lo]) and _num(P[hi]) and P[lo] >= P[hi]:
            reg.issues.append(Issue("UYARI", "tutarlılık", f"{lo} ({P[lo]}) ≥ {hi} ({P[hi]})"))
    # İl nüfusu = TÜİK ilçe toplamı
    csv = root / "data" / "reference" / "tuik_adnks_ankara.csv"
    if csv.exists() and _num(P.get("population.province_2025")):
        import csv as _csv
        with open(csv, encoding="utf-8", newline="") as f:
            total = sum(int(r["nufus_2025"]) for r in _csv.DictReader(f))
        if total != P["population.province_2025"]:
            reg.issues.append(Issue("HATA", "tutarlılık",
                                    f"TÜİK ilçe toplamı {total} ≠ population.province_2025"))


def _duplicate_urls(reg: Registry) -> None:
    seen: dict[str, str] = {}
    for sid, s in reg.sources.items():
        u = str(s.get("url"))
        if u.startswith("http") and u in seen and reg.sources[seen[u]]["_domain"] != s["_domain"]:
            reg.issues.append(Issue("UYARI", "yinelenen URL",
                                    f"{sid} ve {seen[u]} aynı belgeyi gösteriyor (alanlar arası; birleştirilebilir)"))
        seen.setdefault(u, sid)


# ---------------------------------------------------------------- çıktı: parameters.json
def _jsonable(v):
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    return v


def build_parameters(reg: Registry) -> dict:
    order = list(reg.schema.canonical) + [k for k in reg.params if k not in reg.schema.canonical]
    out: dict[str, dict] = {}
    for key in order:
        p = reg.params.get(key)
        if p is None:
            continue
        ids = list(p.get("source_ids") or [])
        out[key] = _jsonable({
            "label": p.get("label"),
            "value": p.get("value"),
            "unit": p.get("unit"),
            "status": p.get("status"),
            "source_ids": ids,
            "derivation": p.get("derivation"),
            "rationale": p.get("rationale"),
            "range": p.get("range"),
            "alternatives": p.get("alternatives") or [],
            "notes": p.get("notes"),
            "group": reg.schema.canonical.get(key),
            "domain": p["_domain"],
            "sources": [{k: reg.sources[i].get(k) for k in ("id", "title", "publisher", "url", "accessed", "kind")}
                        for i in ids if i in reg.sources],
        })
    return out


# ---------------------------------------------------------------- çıktı: SOURCES.md
def fmt_value(v) -> str:
    """Değeri Türkçe sayı biçimiyle yazar (ondalık basamak kaynaktaki gibi korunur)."""
    if isinstance(v, bool):
        return "evet" if v else "hayır"
    if isinstance(v, int):
        return common.fmt_tr(v, 0)
    if isinstance(v, float):
        if not math.isfinite(v):
            return str(v)
        exp = Decimal(repr(v)).as_tuple().exponent
        return common.fmt_tr(v, max(0, -int(exp)))
    if v is None:
        return "—"
    if isinstance(v, list):
        return " – ".join(fmt_value(x) for x in v)
    return str(v)


def md(s) -> str:
    if s is None:
        return ""
    return re.sub(r"\s*\n\s*", " ", str(s)).replace("|", "\\|").strip()


def md_url(u: str) -> str:
    u = str(u)
    if not u.startswith("http"):
        return f"`{u}`"
    m = re.match(r"https?://([^/]+)(/.*)?", u)
    host, path = (m.group(1), m.group(2) or "") if m else (u, "")
    text = host + (path if len(host + path) <= 60 else path[:max(0, 57 - len(host))] + "…")
    return f"[{md(text)}](<{u}>)"


def _short(s: str, n: int = 300) -> str:
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + " …"


def _unit(p: dict) -> str:
    return "" if isinstance(p.get("value"), bool) else md(p.get("unit"))


def _ids(ids) -> str:
    return ", ".join(f"`{i}`" for i in ids) if ids else ""


def _usage(reg: Registry) -> dict[str, list[str]]:
    """Kaynak id -> onu kullanan parametreler (Markdown parçası olarak)."""
    use: dict[str, list[str]] = {}
    for key, p in reg.params.items():
        for sid in p.get("source_ids") or []:
            use.setdefault(sid, []).append(f"`{key}`")
        for a in p.get("alternatives") or []:
            for sid in a.get("source_ids") or []:
                lst = use.setdefault(sid, [])
                if f"`{key}`" not in lst and f"`{key}` (alternatif)" not in lst:
                    lst.append(f"`{key}` (alternatif)")
    return use


def derived_checks(reg: Registry) -> list[tuple[str, str, str]]:
    """Varsayılanlardan türetilen kontrol değerleri (hesap)."""
    P = {k: p.get("value") for k, p in reg.params.items()}
    out = []
    try:
        pop = P["population.province_2025"]
        b = P["demand.beds_per_1000"]
        out.append(("Ankara yatak talebi — oran yöntemi", f"{fmt_value(pop)} × {fmt_value(b)} / 1000",
                    common.fmt_tr(pop * b / 1000, 0) + " yatak"))
        adm, alos, occ = (P["demand.hb.admissions_per_1000"], P["demand.hb.alos_days"],
                          P["demand.hb.target_occupancy"])
        hb = pop * adm / 1000 * alos / (365 * occ)
        out.append(("Ankara yatak talebi — Hill-Burton", f"{fmt_value(pop)} × {fmt_value(adm)}/1000 × "
                    f"{fmt_value(alos)} / (365 × {fmt_value(occ)})", common.fmt_tr(hb, 0) + " yatak"))
        cur = P["demand.hb.current_occupancy"]
        out.append(("Hill-Burton, mevcut doluluk ile (kontrol)", f"aynı, doluluk {fmt_value(cur)}",
                    common.fmt_tr(pop * adm / 1000 * alos / (365 * cur), 0) + " yatak"))
        T, r = P["access.horizon_years"], P["access.discount_rate"]
        pv = (1 - (1 + r) ** -T) / r if r > 0 else T
        out.append(("Bugünkü değer çarpanı PV(T, r)", f"(1 − (1 + {fmt_value(r)})^−{fmt_value(T)}) / {fmt_value(r)}",
                    common.fmt_tr(pv, 3)))
        v, rt, c = P["access.hospital_visits_per_capita"], P["access.round_trip_factor"], P["access.cost_tl_per_km"]
        vot, spd = P["access.value_of_time_tl_per_h"], P["access.speed_kmh"]
        lam = pv * v * rt * (c + vot / spd)
        out.append(("λ (erişim maliyeti katsayısı)", f"PV × {fmt_value(v)} × {fmt_value(rt)} × ({fmt_value(c)} + "
                    f"{fmt_value(vot)} / {fmt_value(spd)})", common.fmt_tr(lam, 0) + " TL / (kişi·km)"))
    except (KeyError, TypeError, ZeroDivisionError):
        pass
    return out


def build_markdown(reg: Registry) -> str:
    S, Pm = reg.sources, reg.params
    use = _usage(reg)
    sec3_use: dict[str, list[str]] = {}
    for row in SPEC_SECTION3:
        for sid in row["source_ids"]:
            sec3_use.setdefault(sid, []).append(row["konu"])
    last = max((s["accessed"] for s in S.values() if _is_date(s.get("accessed"))), default=None)
    n_status = {st: sum(1 for p in Pm.values() if p.get("status") == st) for st in reg.schema.statuses}
    n_kind = {k: sum(1 for s in S.values() if s.get("kind") == k) for k in reg.schema.kinds}

    L: list[str] = []
    w = L.append
    w("# Kaynaklar ve parametreler")
    w("")
    w("> Bu dosya `pipeline/build_registry.py` tarafından `data/sources/*.yaml` ve `data/reference/params/*.yaml` "
      "parçalarından üretilir — **elle düzenlemeyin**; parçaları düzenleyip betiği yeniden çalıştırın. "
      "Makinece okunan sürüm: `data/reference/parameters.json`.")
    w("")
    w(f"- Kaynak sayısı: **{len(S)}** ({', '.join(f'{KIND_TR.get(k, k)} {n}' for k, n in n_kind.items() if n)})")
    w(f"- Parametre sayısı: **{len(Pm)}** / kanonik {len(reg.schema.canonical)} "
      f"({', '.join(f'{STATUS_TR.get(k, k)} {n}' for k, n in n_status.items() if n)})")
    w(f"- Son erişim tarihi: {last.isoformat() if last else '—'}")
    w(f"- Eksik kanonik anahtar: {_ids(reg.missing_canonical) or 'yok'}; kanonik olmayan anahtar: "
      f"{_ids(reg.unknown_keys) or 'yok'}")
    w(f"- Doğrulama: {len(reg.errors)} hata, {len(reg.warnings)} uyarı (ayrıntı en sonda)")
    w("- Durumlar: **kaynaklı** = değer kaynakta yazıyor · **hesap** = kaynaklı girdilerden formülle · "
      "**varsayım** = kaynağı yok, gerekçeli · **model seçimi** = modelleme tercihi, gerekçeli")
    w("- Ham belgeler `data/raw/<alan>/` altında (git dışı); her indirmenin url/tarih/sha256 kaydı aynı klasördeki "
      "`_manifest.jsonl` dosyasında.")
    w("")
    w("## İçindekiler")
    w("")
    w("1. [Kaynaklar (alan bazında)](#1-kaynaklar-alan-bazında)")
    w("2. [Parametreler](#2-parametreler)")
    w("3. [Varsayımlar](#3-varsayımlar)")
    w("4. [Bölüm 3 ile karşılaştırma](#4-bölüm-3-ile-karşılaştırma)")
    w("5. [Doğrulama raporu](#5-doğrulama-raporu)")
    w("")

    # 1. Kaynaklar
    w("## 1. Kaynaklar (alan bazında)")
    w("")
    domains = list(DOMAINS) + sorted({s["_domain"] for s in S.values()} - set(DOMAINS))
    for i, d in enumerate(domains, 1):
        items = [s for s in S.values() if s["_domain"] == d]
        if not items:
            continue
        w(f"### 1.{i} {DOMAINS.get(d, (d,))[0]}")
        w("")
        w(f"Dosya: `data/sources/{d}.yaml` · {len(items)} kaynak")
        w("")
        w("| Başlık | Yayımcı | URL | Erişim tarihi | Kullanıldığı değerler |")
        w("|---|---|---|---|---|")
        for s in items:
            title = f"**{md(s.get('title'))}**<br>`{s['id']}` · {KIND_TR.get(s.get('kind'), s.get('kind'))}"
            if s.get("published"):
                title += f" · yayım {md(_jsonable(s['published']))}"
            if not s.get("archived"):
                title += " · *yerel arşiv yok*"
            parts = [f"• {md(u)}" for u in (s.get("used_for") or [])] or ["— (değer alınmadı)"]
            if s["id"] in use:
                parts.append("Parametre: " + ", ".join(use[s["id"]]))
            if s["id"] in sec3_use:
                parts.append("Bölüm 3: " + "; ".join(md(x) for x in sec3_use[s["id"]]))
            acc = _jsonable(s.get("accessed")) or "—"
            w(f"| {title} | {md(s.get('publisher'))} | {md_url(s.get('url', ''))} | {acc} | {'<br>'.join(parts)} |")
        w("")

    # 2. Parametreler
    w("## 2. Parametreler")
    w("")
    w("Değerler Türkçe sayı biçimindedir (`1.234.567,8`); oranlar 0–1 kesridir. Alternatifler, aralıklar ve uzun "
      "notlar `data/reference/parameters.json` dosyasındadır.")
    w("")
    groups: dict[str, list[str]] = {}
    for k in reg.schema.canonical:
        groups.setdefault(reg.schema.canonical[k], []).append(k)
    if reg.unknown_keys:
        groups["Kanonik olmayan"] = reg.unknown_keys
    for g, keys in groups.items():
        w(f"### {g}")
        w("")
        w("| Anahtar | Değer | Birim | Durum | Kaynak / gerekçe |")
        w("|---|---|---|---|---|")
        for k in keys:
            p = Pm.get(k)
            if p is None:
                w(f"| `{k}` | **EKSİK** | | | parametre parçalarında yok |")
                continue
            st = p.get("status")
            why = []
            if st == "hesap" and p.get("derivation"):
                why.append(f"Hesap: {md(p['derivation'])}")
            if st in ("varsayim", "model_secimi"):
                why.append("Gerekçe: bkz. [Varsayımlar](#3-varsayımlar)")
            if p.get("source_ids"):
                why.append(("Kaynak: " if st in ("kaynakli", "hesap") else "Dayanak: ") + _ids(p["source_ids"]))
            rng = p.get("range")
            if rng:
                why.append(f"Aralık: {fmt_value(rng[0])} – {fmt_value(rng[1])}")
            n_alt = len(p.get("alternatives") or [])
            if n_alt:
                why.append(f"{n_alt} alternatif")
            label = f"`{k}`<br>{md(p.get('label'))}"
            stxt = STATUS_TR.get(st, st)
            stxt = f"**{stxt}**" if st == "varsayim" else stxt
            w(f"| {label} | {fmt_value(p.get('value'))} | {md(p.get('unit'))} | {stxt} | {'<br>'.join(why)} |")
        w("")
    dc = derived_checks(reg)
    if dc:
        w("### Varsayılanlarla türetilen kontrol değerleri (hesap)")
        w("")
        w("| Büyüklük | Formül | Sonuç |")
        w("|---|---|---|")
        for a, b, c in dc:
            w(f"| {md(a)} | {md(b)} | {md(c)} |")
        w("")

    # 3. Varsayımlar
    w("## 3. Varsayımlar")
    w("")
    w("Kaynağı olmayan (**varsayım**) ya da modelleme tercihi olan (**model seçimi**) her parametre; arayüzde "
      "\"varsayım\" etiketiyle ve bu gerekçeyle gösterilir.")
    w("")
    for st in ("varsayim", "model_secimi"):
        keys = [k for k in Pm if Pm[k].get("status") == st]
        if not keys:
            continue
        w(f"### {STATUS_TR[st].capitalize()} ({len(keys)})")
        w("")
        for k in keys:
            p = Pm[k]
            w(f"- **`{k}` = {fmt_value(p.get('value'))}** {_unit(p)} — {md(p.get('label'))}  ")
            w(f"  {md(p.get('rationale'))}")
            if p.get("source_ids"):
                w(f"  Dayanak belgeler: {_ids(p['source_ids'])}")
        w("")
    alt_v = [(k, a) for k, p in Pm.items() for a in (p.get("alternatives") or []) if a.get("status") == "varsayim"]
    if alt_v:
        w("### Alternatiflerde varsayım olarak etiketli değerler")
        w("")
        w("| Anahtar | Alternatif | Değer |")
        w("|---|---|---|")
        for k, a in alt_v:
            w(f"| `{k}` | {md(a.get('label'))} | {fmt_value(a.get('value'))} |")
        w("")

    # 4. Bölüm 3
    w("## 4. Bölüm 3 ile karşılaştırma")
    w("")
    w("Şartname Bölüm 3 'Referans değerler' tablosu, alan raporları ve arşivlenen belgelerle karşılaştırıldı. "
      "Çelişkilerde şartname değeri değiştirilmedi; alternatif olarak tutuldu.")
    w("")
    w("| Konu | Şartname değeri | Bulunan değer | Durum | Yer / açıklama | Kaynak |")
    w("|---|---|---|---|---|---|")
    for r in SPEC_SECTION3:
        durum = f"**{r['durum']}**" if r["durum"].startswith(("çelişki", "bulunamadı")) else r["durum"]
        w(f"| {md(r['konu'])} | {md(r['spec'])} | {md(r['bulunan'])} | {durum} | {md(r['yer'])} | "
          f"{_ids(r['source_ids'])} |")
    w("")
    w("### Şartname Bölüm 4 varsayılanları ile kayıt varsayılanları")
    w("")
    w("| Anahtar | Şartname | Kayıt | Durum | Not |")
    w("|---|---|---|---|---|")
    for key, spec_v, spec_txt, extra in SPEC_DEFAULTS:
        p = Pm.get(key)
        if p is None:
            w(f"| `{key}` | {md(spec_txt)} | **EKSİK** | — | |")
            continue
        v = p.get("value")
        same = spec_v is not None and (v == spec_v or (_num(v) and _num(spec_v) and _rel(v, spec_v) < 1e-9))
        durum = "aynı" if same else "**farklı**"
        note = STATUS_TR.get(p.get("status"), p.get("status"))
        alt_spec = any(_num(a.get("value")) and _num(spec_v) and _rel(a["value"], spec_v) < 0.01
                       for a in (p.get("alternatives") or []))
        if not same and alt_spec:
            note += "; şartname değeri alternatif olarak duruyor"
        if extra:
            note += f"; {extra}"
        w(f"| `{key}` | {md(spec_txt)} | {fmt_value(v)} {_unit(p)} | {durum} | {md(note)} |")
    w("")

    # 5. Doğrulama
    w("## 5. Doğrulama raporu")
    w("")
    w("### Entegrasyon düzeltmeleri (parçalarda elle yapılan, değer değiştirmeyen)")
    w("")
    for c in INTEGRATION_CHANGES or ["Yok."]:
        w(f"- {c}")
    w("")
    w("### Yerel arşivi olmayan kaynaklar")
    w("")
    no_arch = [s for s in S.values() if not s.get("archived")]
    for s in no_arch:
        w(f"- `{s['id']}` ({KIND_TR.get(s.get('kind'))}) — {md(s.get('title'))}: {_short(md(s.get('notes')))}")
    if not no_arch:
        w("- Yok.")
    w("")
    w(f"### Hatalar ({len(reg.errors)}) ve uyarılar ({len(reg.warnings)})")
    w("")
    for i in reg.issues or []:
        w(f"- {md(str(i))}")
    if not reg.issues:
        w("- Yok.")
    w("")
    return "\n".join(L)


# ---------------------------------------------------------------- ana akış
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-sha", action="store_true", help="arşiv sha256 doğrulamasını atla (hızlı)")
    args = ap.parse_args(argv)

    reg = validate(verify_sha=not args.no_sha)
    for i in reg.issues:
        print(i)
    print(f"Kaynak: {len(reg.sources)} · parametre: {len(reg.params)} / kanonik {len(reg.schema.canonical)} · "
          f"eksik kanonik: {reg.missing_canonical or 'yok'} · hata: {len(reg.errors)} · uyarı: {len(reg.warnings)}")
    if reg.errors:
        print("HATA var: çıktılar yazılmadı.", file=sys.stderr)
        return 1
    params = build_parameters(reg)
    OUT_JSON.write_text(json.dumps(params, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    OUT_MD.write_text(build_markdown(reg), encoding="utf-8")
    print(f"Yazıldı: {OUT_JSON.relative_to(common.ROOT)} ({len(params)} anahtar), {OUT_MD.relative_to(common.ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
