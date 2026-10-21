"""Faz 1 — sağlık istatistikleri (alan: saglik): talep (spec 4.4 A) ve başvuru oranı v (spec 4.4 G) girdileri.

Kaynaklar (ayrıntı ve kaynak kimlikleri data/sources/saglik.yaml):
  - SBSGM 'Sağlık İstatistikleri Yıllığı Haber Bülteni' 2025 (Sayı 14), 2024 (13), 2023 (12): Türkiye ve İBBS-1
  - 'Sağlık İstatistikleri Yıllığı' 2024 ve 2023 (tam metin): il tabloları (Ankara) ve Türkiye toplamları
  - SBSGM duyuru/liste sayfaları (yayım tarihi kanıtı), DergiPark makaleleri (hedef doluluk), Crossref künyesi
  - Hedef doluluk denetimi: Bagust vd. 1999 tam metni (PMC28163), SB Stratejik Plan 2024–2028, SB 2026 Performans
    Programı, KHGM GÖREN gösterge kılavuzu 2026 (check_occupancy_targets; CSV'ye değer yazmaz)
  - 2025 nüfus paydası: TÜİK ADNKS 2025 bülten tablosu, sayfa 't3' (nufus alanının arşivi; 10_tuik_population.py
    indirir). Karar haberi (aynı arşiv) yalnız çapraz kontrol.
  - Çapraz kontrol (ikincil, atıf kaynağı değil): Zenodo 'Türkiye Sağlık İstatistikleri Arşivi' v1.1.0 (Kaya 2026)

Adımlar
  1. fetch   : belgeleri data/raw/saglik/ (Zenodo: data/raw/saglik/zenodo/) altına indirir; dosya ve manifest kaydı
               varsa atlar, dosya var ama kayıt yoksa yalnız manifest kaydı yazar (çevrimdışı yeniden üretilebilir)
  2. extract : PDF tablo satırlarını pdfplumber ile ayrıştırır; her değer beklenen değerle karşılaştırılır (assert)
  3. hesap   : türetilmiş değerler ayrı satırlarda, formülüyle yazılır (location = 'hesap: …')
  4. kontrol : bülten nüfusu ↔ TÜİK 2025; TÜİK ↔ Karar haberi; Zenodo derlemesi ↔ yıllık il satırları

Çıktı: data/reference/saglik_istatistikleri.csv (indicator, year, geography, value, unit, source_id, location)

Çalıştırma (repo kökünden):  .venv/bin/python pipeline/17_health_stats.py [--offline]
  --offline : hiçbir şey indirme; eksik ham dosya varsa hata ver (Zenodo eksikse yalnız uyarı).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path

import pdfplumber
import xlrd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline import common  # noqa: E402

RAWS = common.RAW / "saglik"
ZENODO_DIR = RAWS / "zenodo"
OUT_CSV = common.REFERENCE / "saglik_istatistikleri.csv"
# nufus alanının ham dosyaları (10_tuik_population.py indirir)
TUIK_T3_XLS = common.RAW / "nufus" / "tuik" / "bt_adnks2025_il_cinsiyet_nufus_yogunluk.xls"
KARAR_HTML = common.RAW / "nufus" / "tuik" / "haber_karar_2027851.html"

FIELDS = ["indicator", "year", "geography", "value", "unit", "source_id", "location"]

# ---------------------------------------------------------------- 1. indirme listesi
SBSGM = "https://sbsgm.saglik.gov.tr/"
DOSYA = "https://dosyamerkez.saglik.gov.tr/Eklenti/"
DOCS = [  # (dosya adı, URL)
    # SBSGM duyuru sayfaları (yayım tarihi ve PDF bağlantısı kanıtı)
    ("sbsgm_TR-121059_bulten2025_duyuru.html",
     SBSGM + "TR-121059/saglik-istatistikleri-yilligi-2025-haber-bulteni-yayimlanmistir.html"),
    ("sbsgm_TR-121058_bulten2025.html", SBSGM + "TR-121058/saglik-istatistikleri-yilligi-2025-haber-bulteni.html"),
    ("sbsgm_TR-111039_bulten2024_duyuru.html",
     SBSGM + "TR-111039/saglik-istatistikleri-yilligi-2024-haber-bulteni-yayimlanmistir.html"),
    ("sbsgm_TR-105979_bulten2023.html", SBSGM + "TR-105979/saglik-istatistikleri-yilligi-2023-haber-bulteni.html"),
    ("sbsgm_TR-93566_yilliklar_listesi.html", SBSGM + "TR-93566/saglik-istatistikleri-yilligi.html"),
    ("sbsgm_TR-93450_bultenler_listesi.html", SBSGM + "TR-93450/saglik-istatistikleri-yilligi-haber-bultenleri.html"),
    ("sbsgm_TR-114954_yillik2024.html", SBSGM + "TR-114954/saglik-istatistikleri-yilligi-2024.html"),
    ("sbsgm_TR-107087_yillik2023.html", SBSGM + "TR-107087/saglik-istatistikleri-yilligi-2023.html"),
    # Haber bültenleri (Türkçe)
    ("haber-bulteni-2025-tr.pdf", DOSYA + "56342/0/haber-bulteni-2025-trpdf.pdf"),
    ("haber-bulteni-2024-tr.pdf", DOSYA + "51942/0/haber-bulteni-2024-trpdf.pdf"),
    ("haber-bulteni-2023-tr.pdf", DOSYA + "49429/0/haber-bulteni-2023-30092024pdf.pdf"),
    # Tam yıllıklar (il tabloları)
    ("siy2024tr.pdf", DOSYA + "52859/0/siy2024tr31122025pdf.pdf"),
    ("siy2023tr.pdf", DOSYA + "50500/0/siy202307032025pdf.pdf"),
    # Hedef doluluk için akademik kaynaklar (DergiPark)
    ("dergipark_795199_aydemir2019_usaysad.pdf", "https://dergipark.org.tr/tr/download/article-file/795199"),
    ("dergipark_747322_cirakli2019_skad.pdf", "https://dergipark.org.tr/tr/download/article-file/747322"),
    # Bagust vd. 1999: künye (Crossref) ve tam metin (BMJ makalesinin PMC kopyası, PMC28163; bmj.com vekilde engelli)
    ("crossref_bagust1999.json", "https://api.crossref.org/works/10.1136/bmj.319.7203.155"),
    # PMC OAI-PMH 'pmc_fm' kaydı: özet + 'Key messages' (makale PMC açık erişim alt kümesinde değil, gövde OAI'de yok)
    ("pmc_oai_fm_PMC28163_bagust1999.xml",
     "https://pmc.ncbi.nlm.nih.gov/api/oai/v1/mh/?verb=GetRecord&identifier=oai:pubmedcentral.nih.gov:28163"
     "&metadataPrefix=pmc_fm"),
    # Sağlık Bakanlığı hedef belgeleri: resmî bir yatak doluluk hedefi/göstergesi var mı? (check_occupancy_targets)
    # Stratejik Plan sayfası sgb.saglik.gov.tr/TR-102640 ve Performans Programı sayfası TR-59135 bu PDF'lere yönleniyor.
    ("sb_stratejik_plan_2024_2028.pdf", DOSYA + "47452/0/saglik-bakanligi-stratejik-plan-2024-2028pdf.pdf"),
    ("sb_performans_programi_2026.pdf", DOSYA + "53814/0/2026-performans-programipdf.pdf"),
    # KHGM Kurum Hedef (GÖREN) gösterge kılavuzu 2026 (sayfa: khgm.saglik.gov.tr/TR-119310)
    ("khgm_goren_kilavuz_2026.pdf", DOSYA + "54107/0/gorenkilavuzguncel-2026pdf.pdf"),
]
# Not: Karar haberi (2025 nüfusu) artık burada indirilmiyor; nufus alanı arşivliyor (tuik_haber_karar_adnks2025).

ZENODO_API = "https://zenodo.org/api/records/23325100"
ZENODO_RECORD = "zenodo_23325100_record.json"   # kayıt üst verisi (dosya md5'leri)
ZENODO_FILES = ("il_verileri_yilliklardan.csv", "gostergeler.csv", "kaynaklar.csv")


def _manifest_has(dest: Path, url: str) -> bool:
    man = dest.parent / "_manifest.jsonl"
    if not (dest.exists() and man.exists()):
        return False
    rel = str(dest.relative_to(common.ROOT))
    for line in man.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("url") == url and rec.get("file") == rel:
            return True
    return False


def ensure(url: str, dest: Path, *, offline: bool, timeout: int = 300) -> None:
    """Dosya + manifest kaydı varsa atlar; dosya varsa yalnız manifest kaydı yazar; yoksa indirir."""
    if _manifest_has(dest, url):
        return
    existed = dest.exists()
    if not existed and offline:
        raise FileNotFoundError(f"çevrimdışı mod: ham dosya yok: {dest.relative_to(common.ROOT)}")
    rec = common.download(url, dest, timeout=timeout)  # dosya varsa indirmez
    print(("  manifest kaydı: " if existed else "  indirildi: ") + f"{rec['file']} ({rec['bytes']:,} B)")


def _md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


PMC_PAGE = ("pmc_PMC28163_bagust1999.html", "https://pmc.ncbi.nlm.nih.gov/articles/PMC28163/")
PMC_PAGE_UA = "curl/8.5.0"
PMC_CHALLENGE = "Checking your browser"


def _drop_manifest_record(dest: Path) -> None:
    man = dest.parent / "_manifest.jsonl"
    rel = str(dest.relative_to(common.ROOT))
    keep = [ln for ln in man.read_text(encoding="utf-8").splitlines()
            if ln.strip() and json.loads(ln).get("file") != rel]
    man.write_text("".join(ln + "\n" for ln in keep), encoding="utf-8")


def ensure_pmc_page(offline: bool) -> None:
    """Bagust vd. PMC makale sayfası (gövde metni; isteğe bağlı kanıt, özet ve 'Key messages' OAI kaydında).

    PMC, tarayıcı UA'sı taşıyan betik isteklerine reCAPTCHA ara sayfası ('Checking your browser') döndürüyor. Bu
    yüzden yalnız bu indirmede common.USER_AGENT geçici olarak curl UA'sına çevrilir (common.py değişmez). Ara sayfa
    gelirse dosya ve manifest kaydı silinir; gövde alıntıları o zaman denetlenmez.
    """
    name, url = PMC_PAGE
    dest = RAWS / name
    if _manifest_has(dest, url) or offline:
        return
    old = common.USER_AGENT
    common.USER_AGENT = PMC_PAGE_UA
    try:
        rec = common.download(url, dest, timeout=120)
    except OSError as e:
        print(f"  uyarı: PMC sayfası alınamadı ({type(e).__name__}); gövde alıntıları denetlenmeyecek")
        return
    finally:
        common.USER_AGENT = old
    if PMC_CHALLENGE in dest.read_text(encoding="utf-8", errors="replace"):
        dest.unlink()
        _drop_manifest_record(dest)
        print("  uyarı: PMC ara sayfa (reCAPTCHA) döndürdü; dosya silindi, gövde alıntıları denetlenmeyecek")
    else:
        print(f"  indirildi: {rec['file']} ({rec['bytes']:,} B)")


def fetch(offline: bool) -> None:
    for name, url in DOCS:
        ensure(url, RAWS / name, offline=offline)
    ensure_pmc_page(offline)
    # Zenodo (yalnız çapraz kontrol): eksikse uyarı, hata değil
    try:
        ensure(ZENODO_API, ZENODO_DIR / ZENODO_RECORD, offline=offline)
        meta = json.loads((ZENODO_DIR / ZENODO_RECORD).read_text(encoding="utf-8"))
        md5 = {f["key"]: f["checksum"].removeprefix("md5:") for f in meta["files"]}
        for name in ZENODO_FILES:
            dest = ZENODO_DIR / name
            ensure(f"{ZENODO_API}/files/{name}/content", dest, offline=offline)
            assert _md5(dest) == md5[name], f"Zenodo md5 kayıtla uyuşmuyor: {name}"
    except (OSError, ValueError) as e:
        print(f"  uyarı: Zenodo dosyaları alınamadı, çapraz kontrol atlanacak ({e})")


# ---------------------------------------------------------------- 2. PDF ayrıştırma
B25, B24, B23 = "haber-bulteni-2025-tr.pdf", "haber-bulteni-2024-tr.pdf", "haber-bulteni-2023-tr.pdf"
Y24, Y23 = "siy2024tr.pdf", "siy2023tr.pdf"
SRC = {B25: "saglik_sbsgm_bulten2025", B24: "saglik_sbsgm_bulten2024", B23: "saglik_sbsgm_bulten2023",
       Y24: "saglik_sbsgm_siy2024", Y23: "saglik_sbsgm_siy2023"}
# basılı sayfa = PDF sayfası − kayma (yıllıklarda); bültenlerde 'Sayfa n' = PDF sayfası − 1
PRINT_OFFSET = {Y24: 29, Y23: 27, B25: 1, B24: 1, B23: 1}
DOC_LABEL = {B25: "SİY 2025 Haber Bülteni", B24: "SİY 2024 Haber Bülteni", B23: "SİY 2023 Haber Bülteni",
             Y24: "Sağlık İstatistikleri Yıllığı 2024", Y23: "Sağlık İstatistikleri Yıllığı 2023"}
POP_SRC = "tuik_bt_adnks2025_yogunluk"     # resmî 2025 nüfus paydası
POP_XCHECK = "tuik_haber_karar_adnks2025"  # aynı değeri aktaran haber (çapraz kontrol)

NUM = re.compile(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+,\d+|\d+")
_pages: dict = {}


def page_text(doc: str, page: int) -> str:
    key = (doc, page)
    if key not in _pages:
        with pdfplumber.open(RAWS / doc) as pdf:
            _pages[key] = pdf.pages[page - 1].extract_text() or ""
    return _pages[key]


def tr_num(tok: str) -> float:
    """'1.138.060.185' -> 1138060185 ; '31,5' -> 31.5"""
    return float(tok.replace(".", "").replace(",", "."))


def row_nums(doc: str, page: int, label: str, after: str | None = None) -> list[float]:
    """Sayfada 'after' satırından sonra 'label' ile başlayan ilk satırdaki sayıları döndürür."""
    lines = page_text(doc, page).split("\n")
    start = 0
    if after:
        start = next((i for i, l in enumerate(lines) if after in l), None)
        if start is None:
            raise LookupError(f"{doc} s.{page}: '{after}' bulunamadı")
    pat = re.compile(r"(?:^|\s)" + re.escape(label) + r"\s+(\d[\d.,\s]*)$")
    for l in lines[start:]:
        m = pat.search(l.strip())
        if m:
            return [tr_num(t) for t in NUM.findall(m.group(1))]
    raise LookupError(f"{doc} s.{page}: '{label}' bulunamadı (after={after})")


def next_line_nums(doc: str, page: int, anchor: str) -> list[float]:
    """Etiketi iki satıra bölünmüş tablo satırları için: 'anchor' satırından sonraki satırın sayıları."""
    lines = page_text(doc, page).split("\n")
    i = next(i for i, l in enumerate(lines) if anchor in l)
    return [tr_num(t) for t in NUM.findall(lines[i + 1])]


def loc(doc: str, page: int, table: str) -> str:
    table = table.rstrip(".")
    printed = page - PRINT_OFFSET[doc]
    pp = f"bülten 'Sayfa {printed}'" if doc.startswith("haber") else f"basılı s. {printed}"
    return f"{DOC_LABEL[doc]}, {table} (PDF s. {page}; {pp})"


class Table:
    """Çıktı satırları; kaynaklı değerler isteğe bağlı beklenen değerle karşılaştırılır."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def add(self, indicator, year, geo, value, unit, doc, page, table, expect=None):
        if expect is not None:
            assert abs(value - expect) < 1e-9, f"{indicator} {year} {geo}: {value} != {expect}"
        return self.add_row(indicator, year, geo, value, unit, SRC[doc], loc(doc, page, table))

    def add_calc(self, indicator, year, geo, value, unit, source_ids, formula):
        return self.add_row(indicator, year, geo, value, unit, ";".join(source_ids), "hesap: " + formula)

    def add_row(self, indicator, year, geo, value, unit, source_id, location):
        self.rows.append(dict(indicator=indicator, year=year, geography=geo, value=value, unit=unit,
                              source_id=source_id, location=location))
        return value

    def val(self, ind, yr, geo):
        return next(r["value"] for r in self.rows
                    if r["indicator"] == ind and r["year"] == yr and r["geography"] == geo)


SECT = [("moh", "Sağlık Bakanlığı"), ("university", "Üniversite"), ("private", "Özel")]
BA = "Batı Anadolu (İBBS-1 TR5)"
USAGE_UNITS = {"visits_per_capita_total": "müracaat / kişi", "dental_visits_per_capita": "müracaat / kişi",
               "bed_occupancy_pct": "%", "alos_days": "gün", "bed_turnover_rate": "hasta / yatak / yıl",
               "bed_turnover_interval_days": "gün"}


def sector_table(t: Table, doc, page, after, year, ind, unit, expect_tr, expect_ba):
    """İBBS-1 × sektör tablosu: Türkiye ve Batı Anadolu satırları (SB, Üni, Özel, Toplam)."""
    for geo, label, exp in [("Türkiye", "Türkiye", expect_tr), (BA, "Batı Anadolu", expect_ba)]:
        v = row_nums(doc, page, label, after)
        assert v == exp, (doc, page, label, v, exp)
        t.add(ind, year, geo, v[3], unit, doc, page, after.rstrip("."))
        if geo == "Türkiye":
            for (code, _), x in zip(SECT, v[:3]):
                t.add(f"{ind}_{code}", year, geo, x, unit, doc, page, after.rstrip("."))


def bulletin_visits(t: Table, doc, page, table, year, exp):
    """Kurum türlerine göre hekime müracaat tablosu (Türkiye) + basamak payları metni."""
    v = row_nums(doc, page, "Birinci Basamak Toplamı", table)
    t.add("visits_primary", year, "Türkiye", v[0], "müracaat", doc, page, table, exp["prim"][0])
    t.add("visits_per_capita_primary", year, "Türkiye", v[1], "müracaat / kişi", doc, page, table, exp["prim"][1])
    v = row_nums(doc, page, "Özel Tıp Merkezleri", table)
    t.add("visits_private_medical_centres", year, "Türkiye", v[0], "müracaat", doc, page, table, exp["otm"])
    v = row_nums(doc, page, "Hastaneler", table)
    t.add("visits_hospitals", year, "Türkiye", v[0], "müracaat", doc, page, table, exp["hosp"][0])
    t.add("visits_per_capita_hospitals", year, "Türkiye", v[1], "müracaat / kişi", doc, page, table, exp["hosp"][1])
    for code, label in SECT:
        v = row_nums(doc, page, label, table)
        t.add(f"visits_hospitals_{code}", year, "Türkiye", v[0], "müracaat", doc, page, table, exp[code])
    v = row_nums(doc, page, "İkinci ve Üçüncü Basamak Toplamı", table)
    t.add("visits_secondary_tertiary", year, "Türkiye", v[0], "müracaat", doc, page, table, exp["sec"][0])
    t.add("visits_per_capita_secondary_tertiary", year, "Türkiye", v[1], "müracaat / kişi", doc, page, table,
          exp["sec"][1])
    v = row_nums(doc, page, "Genel Toplam", table)
    t.add("visits_total", year, "Türkiye", v[0], "müracaat", doc, page, table, exp["tot"][0])
    t.add("visits_per_capita_total", year, "Türkiye", v[1], "müracaat / kişi", doc, page, table, exp["tot"][1])
    txt = page_text(doc, page).replace("\n", " ")
    m = re.search(r"%(\d+,\d)’ü birinci basamak.*?%(\d+,\d)’si\s*ikinci ve üçüncü", txt)
    text_loc = "metin (Sağlık Hizmetlerinin Kullanımı)"
    t.add("share_primary_pct", year, "Türkiye", tr_num(m.group(1)), "%", doc, page, text_loc, exp["share"][0])
    t.add("share_secondary_tertiary_pct", year, "Türkiye", tr_num(m.group(2)), "%", doc, page, text_loc,
          exp["share"][1])


def bulletin_usage(t: Table, doc, page, table, year, cols, exp_tr, exp_ba):
    """İBBS-1'e göre kullanım göstergeleri (kişi başı müracaat, doluluk, kalış, devir hızı, devir aralığı)."""
    for geo, label, exp in [("Türkiye", "Türkiye", exp_tr), (BA, "Batı Anadolu", exp_ba)]:
        v = row_nums(doc, page, label, table)
        assert v == exp, (doc, page, label, v, exp)
        for c, x in zip(cols, v):
            if geo == "Türkiye" and c == "visits_per_capita_total":
                continue  # Türkiye kişi başı müracaat zaten kurum türü tablosundan yazıldı
            t.add(c, year, geo, x, USAGE_UNITS[c], doc, page, table)


def extract_bulletins(t: Table, pop25_tr: int) -> None:
    # ---- 2025 (Haber Bülteni 2025)
    y = 2025
    sector_table(t, B25, 4, "Tablo 6.", y, "hospitals", "hastane", [943, 70, 540, 1553], [73, 13, 52, 138])
    sector_table(t, B25, 5, "Tablo 7.", y, "hospital_beds", "yatak", [172079, 45740, 53010, 270829],
                 [20700, 7740, 4719, 33159])
    v = row_nums(B25, 5, "Nitelikli Yatak Sayısı", "Tablo 8.")
    t.add("qualified_beds", y, "Türkiye", v[3], "yatak", B25, 5, "Tablo 8", 184965)
    v = row_nums(B25, 6, "Toplam", "Tablo 9.")
    t.add("icu_beds", y, "Türkiye", v[3], "yatak", B25, 6, "Tablo 9", 48360)
    v = next_line_nums(B25, 7, "10.000 Kişiye Düşen Hastane")
    t.add("beds_per_10000", y, "Türkiye", v[4], "yatak / 10.000 kişi", B25, 7, "Tablo 11", 31.5)
    bulletin_visits(t, B25, 9, "Tablo 15.", y, dict(
        prim=[470356652, 5.5], otm=11536197, hosp=[656167336, 7.6], moh=537905374, university=52631121,
        private=65630841, sec=[667703533, 7.8], tot=[1138060185, 13.2], share=[41.3, 58.7]))
    bulletin_usage(t, B25, 10, "Tablo 16.", y,
                   ["visits_per_capita_total", "bed_occupancy_pct", "alos_days", "bed_turnover_rate",
                    "bed_turnover_interval_days"],
                   [13.2, 54.5, 4.0, 50.3, 3.3], [14.0, 59.5, 4.3, 50.8, 2.9])
    # Tablo 11 (PDF s. 7): hastanelerde hekime müracaat (sektör toplamı) Tablo 15 ile aynı olmalı (yalnız kontrol)
    v = next_line_nums(B25, 7, "Hastanelerde Hekime Müracaat")
    assert v[4] == t.val("visits_hospitals", y, "Türkiye") == 656167336, v
    # Bülten nüfusu: Tablo 14 'acil yardım istasyonu başına nüfus' × istasyon sayısı ile tutarlılık (yalnız kontrol)
    st_pop = row_nums(B25, 9, "Türkiye", "Tablo 14.")[0]
    n_station = row_nums(B25, 8, "Acil Yardım İstasyonu", "Tablo 12.")[0]
    assert (st_pop - 0.5) * n_station <= pop25_tr <= (st_pop + 0.5) * n_station

    # ---- 2024 (Haber Bülteni 2024)
    y = 2024
    sector_table(t, B24, 4, "Tablo 6.", y, "hospitals", "hastane", [941, 69, 552, 1562], [75, 13, 51, 139])
    sector_table(t, B24, 5, "Tablo 7.", y, "hospital_beds", "yatak", [169834, 44131, 54394, 268359],
                 [22303, 7578, 4570, 34451])
    v = row_nums(B24, 5, "Nitelikli Yatak Sayısı", "Tablo 8.")
    t.add("qualified_beds", y, "Türkiye", v[3], "yatak", B24, 5, "Tablo 8", 183033)
    v = row_nums(B24, 6, "Toplam", "Tablo 9.")
    t.add("icu_beds", y, "Türkiye", v[3], "yatak", B24, 6, "Tablo 9", 49010)
    v = next_line_nums(B24, 7, "10.000 Kişiye Düşen Hastane")
    t.add("beds_per_10000", y, "Türkiye", v[4], "yatak / 10.000 kişi", B24, 7, "Tablo 12", 31.3)
    bulletin_visits(t, B24, 9, "Tablo 16.", y, dict(
        prim=[453941227, 5.3], otm=13320281, hosp=[580616393, 6.8], moh=466094368, university=48283052,
        private=66238973, sec=[593936674, 6.9], tot=[1047877901, 12.2], share=[43.3, 56.7]))
    bulletin_usage(t, B24, 10, "Tablo 17.", y,
                   ["visits_per_capita_total", "dental_visits_per_capita", "bed_occupancy_pct", "alos_days",
                    "bed_turnover_rate", "bed_turnover_interval_days"],
                   [12.2, 0.73, 59.8, 4.2, 51.7, 2.8], [12.9, 0.81, 60.4, 4.9, 45.0, 3.2])

    # ---- 2023 (Haber Bülteni 2023)
    y = 2023
    sector_table(t, B23, 4, "Tablo 5.", y, "hospitals", "hastane", [933, 68, 565, 1566], [75, 13, 51, 139])
    sector_table(t, B23, 4, "Tablo 6.", y, "hospital_beds", "yatak", [167649, 43878, 55067, 266594],
                 [22347, 7593, 4521, 34461])
    v = row_nums(B23, 5, "Nitelikli Yatak Sayısı", "Tablo 7.")
    t.add("qualified_beds", y, "Türkiye", v[3], "yatak", B23, 5, "Tablo 7", 179597)
    v = row_nums(B23, 5, "Toplam", "Tablo 8.")
    t.add("icu_beds", y, "Türkiye", v[3], "yatak", B23, 5, "Tablo 8", 48966)
    bulletin_visits(t, B23, 8, "Tablo 14.", y, dict(
        prim=[421121236, 4.9], otm=14872035, hosp=[537525816, 6.3], moh=424550715, university=45534172,
        private=67440929, sec=[552397851, 6.5], tot=[973519087, 11.4], share=[43.3, 56.7]))
    v = row_nums(B23, 8, "Aile Hekimliği", "Tablo 14.")
    t.add("visits_family_medicine", y, "Türkiye", v[0], "müracaat", B23, 8, "Tablo 14", 416806846)
    bulletin_usage(t, B23, 8, "Tablo 15.", y,
                   ["visits_per_capita_total", "dental_visits_per_capita", "bed_occupancy_pct", "alos_days",
                    "bed_turnover_rate", "bed_turnover_interval_days"],
                   [11.4, 0.68, 58.2, 4.2, 50.2, 3.0], [12.3, 0.76, 58.4, 5.0, 42.6, 3.6])


YEARBOOKS = [
    (Y24, 2024, (45, 46), "Tablo 7.20", (191, 192), "Tablo 8.26", (233, 234, 235, 236), dict(
        pop={"Ankara": 5864049, "Türkiye": 85664944},
        beds={"Ankara": [85, 24909, 42.5, 15422, 4137, 74.2, 7.1],
              "Türkiye": [1562, 268359, 31.3, 183033, 49010, 83.4, 5.7]},
        visits={"Ankara": [28920274, 47073855, 13.0], "Türkiye": [453941227, 593936674, 12.2]},
        inpat={"Ankara": [1063851, 5409136, 575665, 59.5, 5.1, 42.7, 3.5],
               "Türkiye": [13881750, 58593340, 6269324, 59.8, 4.2, 51.7, 2.8]})),
    (Y23, 2023, (41, 42), "Tablo 7.19", (181, 182), "Tablo 8.25", (223, 224, 225, 226), dict(
        pop={"Ankara": 5803482, "Türkiye": 85372377},
        beds={"Ankara": [85, 25020, 43.1, 15922, 4206, 76.5, 7.2],
              "Türkiye": [1566, 266594, 31.2, 179597, 48966, 82.5, 5.7]},
        visits={"Ankara": [27961355, 44014412, 12.4], "Türkiye": [421121236, 552397851, 11.4]},
        inpat={"Ankara": [998148, 5170606, 554455, 56.6, 5.2, 39.9, 4.0],
               "Türkiye": [13391434, 56594852, 5992338, 58.2, 4.2, 50.2, 3.0]})),
]
BEDS_COLS = [("hospitals", "hastane"), ("hospital_beds", "yatak"), ("beds_per_10000", "yatak / 10.000 kişi"),
             ("qualified_beds", "yatak"), ("icu_beds", "yatak"),
             ("qualified_bed_share_pct", "% (yoğun bakım hariç yataklar içinde)"),
             ("icu_beds_per_10000", "yatak / 10.000 kişi")]
VISIT_COLS = [("visits_primary", "müracaat"), ("visits_secondary_tertiary", "müracaat"),
              ("visits_per_capita_total", "müracaat / kişi")]
INPAT_COLS = [("inpatients", "hasta (taburcu + ölen)"), ("inpatient_days", "gün"), ("operations", "ameliyat"),
              ("bed_occupancy_pct", "%"), ("alos_days", "gün"), ("bed_turnover_rate", "hasta / yatak / yıl"),
              ("bed_turnover_interval_days", "gün")]


def extract_yearbooks(t: Table) -> None:
    """Yıllıklar: il tabloları (Ankara, Türkiye) ve yıllara göre Türkiye tabloları."""
    for doc, yr, pop_pages, t_beds, beds_pages, t_use, use_pages, exp in YEARBOOKS:
        for gi, geo in enumerate(["Ankara", "Türkiye"]):
            v = row_nums(doc, pop_pages[gi], geo, "Tablo 1.3")
            t.add("population", yr, geo, v[0], "kişi", doc, pop_pages[gi], "Tablo 1.3", exp["pop"][geo])
            v = row_nums(doc, beds_pages[gi], geo, t_beds)[:7]
            assert v == exp["beds"][geo], (doc, geo, v)
            for (name, unit), x in zip(BEDS_COLS, v):
                t.add(name, yr, geo, x, unit, doc, beds_pages[gi], t_beds)
            v = row_nums(doc, use_pages[gi], geo, t_use)[:3]
            assert v == exp["visits"][geo], (doc, geo, v)
            for (name, unit), x in zip(VISIT_COLS, v):
                t.add(name, yr, geo, x, unit, doc, use_pages[gi], t_use)
            v = row_nums(doc, use_pages[2 + gi], geo, t_use)[:7]
            assert v == exp["inpat"][geo], (doc, geo, v)
            for (name, unit), x in zip(INPAT_COLS, v):
                t.add(name, yr, geo, x, unit, doc, use_pages[2 + gi], t_use)

    # Yıllık 2024: yıllara göre tablolar (2023 revize değerleri dahil)
    v = row_nums(Y24, 195, "Genel Toplam", "Tablo 8.1.")
    t.add("visits_total", 2023, "Türkiye (SİY 2024 revize)", v[4], "müracaat", Y24, 195, "Tablo 8.1", 973518842)
    v = row_nums(Y24, 205, "Toplam", "Tablo 8.3.")
    t.add("inpatients", 2024, "Türkiye (sektör toplamı)", v[5], "hasta (taburcu + ölen)", Y24, 205, "Tablo 8.3",
          13881750)
    for code, label in SECT:
        v = row_nums(Y24, 205, label, "Tablo 8.3.")
        t.add(f"inpatients_{code}", 2024, "Türkiye", v[5], "hasta (taburcu + ölen)", Y24, 205, "Tablo 8.3")
    v = row_nums(Y24, 208, "Toplam", "Tablo 8.10.")
    t.add("inpatient_days", 2024, "Türkiye (sektör toplamı)", v[5], "gün", Y24, 208, "Tablo 8.10", 58593340)


# ---------------------------------------------------------------- 2025 nüfus paydası (TÜİK)
# Bülten tablosu sayfa 't3': (coğrafya, A sütunu etiketi, Excel satırı); sütun B = toplam nüfus
T3_ROWS = [("Türkiye", "Toplam-Total", 5), ("Ankara", "Ankara", 11)]


def tuik_population_2025() -> dict[str, int]:
    """TÜİK ADNKS 2025 bülten tablosu 't3'ten Türkiye ve Ankara nüfusu (31.12.2025)."""
    if not TUIK_T3_XLS.exists():
        raise FileNotFoundError(f"{TUIK_T3_XLS.relative_to(common.ROOT)} yok; önce pipeline/10_tuik_population.py")
    sh = xlrd.open_workbook(TUIK_T3_XLS).sheet_by_name("t3")
    out = {}
    for geo, label, row in T3_ROWS:
        assert str(sh.cell_value(row - 1, 0)).strip() == label, (row, sh.cell_value(row - 1, 0))
        out[geo] = int(sh.cell_value(row - 1, 1))
    assert out == {"Türkiye": 86_092_168, "Ankara": common.TUIK_PROVINCE_POP_2025}, out
    return out


def crosscheck_karar() -> None:
    """Karar haberi (09.02.2026) aynı değerleri aktarıyor mu? (yalnız kontrol)"""
    if not KARAR_HTML.exists():
        print("  uyarı: Karar haberi arşivi yok; çapraz kontrol atlandı")
        return
    txt = KARAR_HTML.read_text(encoding="utf-8", errors="replace")
    assert "86 milyon 92 bin 168" in txt and "5 milyon 910 bin 320" in txt, "Karar haberi TÜİK ile uyuşmuyor"
    print("  Karar haberi: Türkiye 86.092.168 ve Ankara 5.910.320 TÜİK tablosuyla aynı")


def add_population_2025(t: Table, pop: dict[str, int]) -> None:
    for geo, _, row in T3_ROWS:
        t.add_row("population", 2025, geo, pop[geo], "kişi", f"{POP_SRC};{POP_XCHECK}",
                  f"TÜİK ADNKS 2025 bülten tablosu, sayfa 't3', satır {row}, sütun B (31.12.2025); "
                  f"çapraz kontrol: Karar haberi 09.02.2026")


# ---------------------------------------------------------------- 3. türetilmiş değerler (hesap)
def add_derived(t: Table, pop25_tr: int) -> None:
    b25 = SRC[B25]
    pop_ids = [b25, POP_SRC, POP_XCHECK]
    beds25, turn25, occ25 = (t.val("hospital_beds", 2025, "Türkiye"), t.val("bed_turnover_rate", 2025, "Türkiye"),
                             t.val("bed_occupancy_pct", 2025, "Türkiye"))
    inp25 = t.add_calc("inpatients_est", 2025, "Türkiye", round(turn25 * beds25), "hasta (taburcu + ölen)", [b25],
                       f"yatak devir hızı {turn25} (Tablo 16) × hastane yatağı {beds25:.0f} (Tablo 7); "
                       "bülten yatan hasta sayısını vermiyor")
    t.add_calc("inpatient_days_est", 2025, "Türkiye", round(occ25 / 100 * beds25 * 365), "gün", [b25],
               f"doluluk %{occ25} × yatak {beds25:.0f} × 365 (SİY tanımı: doluluk = yatılan gün×100/(yatak×365))")
    t.add_calc("alos_days_implied", 2025, "Türkiye", round(occ25 / 100 * 365 / turn25, 3), "gün", [b25],
               f"doluluk {occ25/100} × 365 / devir hızı {turn25} (yuvarlama kontrolü; yayımlanan 4,0)")
    t.add_calc("admissions_per_1000", 2025, "Türkiye", round(inp25 / pop25_tr * 1000, 2), "yatan hasta / 1.000 kişi",
               pop_ids, f"{inp25} / {pop25_tr} × 1000")
    t.add_calc("beds_per_1000_exact", 2025, "Türkiye", round(beds25 / pop25_tr * 1000, 4), "yatak / 1.000 kişi",
               pop_ids, f"{beds25:.0f} / {pop25_tr} × 1000 (yayımlanan 31,5/10.000)")
    icu25 = t.val("icu_beds", 2025, "Türkiye")
    t.add_calc("beds_per_1000_excl_icu", 2025, "Türkiye", round((beds25 - icu25) / pop25_tr * 1000, 4),
               "yatak / 1.000 kişi", pop_ids, f"({beds25:.0f} − yoğun bakım {icu25:.0f}) / {pop25_tr} × 1000")
    t.add_calc("share_hospitals_only_pct", 2025, "Türkiye",
               round(t.val("visits_hospitals", 2025, "Türkiye") / t.val("visits_total", 2025, "Türkiye") * 100, 2),
               "%", [b25], "Hastaneler / Genel Toplam (Tablo 15; özel tıp merkezleri hariç)")
    for yr, srcs in [(2024, [SRC[Y24]]), (2023, [SRC[Y23]])]:
        for geo in ["Türkiye", "Ankara"]:
            inp, pop, days = t.val("inpatients", yr, geo), t.val("population", yr, geo), t.val("inpatient_days", yr, geo)
            t.add_calc("admissions_per_1000", yr, geo, round(inp / pop * 1000, 2), "yatan hasta / 1.000 kişi", srcs,
                       f"yatan hasta {inp:.0f} / nüfus {pop:.0f} × 1000")
            t.add_calc("alos_days_exact", yr, geo, round(days / inp, 3), "gün", srcs,
                       f"yatılan gün {days:.0f} / yatan hasta {inp:.0f}")
            sec, prim = t.val("visits_secondary_tertiary", yr, geo), t.val("visits_primary", yr, geo)
            suffix = " (il tablosu)" if geo == "Türkiye" else ""
            t.add_calc("share_secondary_tertiary_pct", yr, geo + suffix, round(sec / (sec + prim) * 100, 2), "%",
                       srcs, f"{sec:.0f} / ({sec:.0f} + {prim:.0f})")
            t.add_calc("visits_per_capita_secondary_tertiary", yr, geo + suffix, round(sec / pop, 3),
                       "müracaat / kişi", srcs, f"{sec:.0f} / nüfus {pop:.0f}")
    for yr, b in [(2024, SRC[B24]), (2023, SRC[B23])]:
        t.add_calc("share_hospitals_only_pct", yr, "Türkiye",
                   round(t.val("visits_hospitals", yr, "Türkiye") / t.val("visits_total", yr, "Türkiye") * 100, 2),
                   "%", [b], "Hastaneler / Genel Toplam (özel tıp merkezleri hariç)")


# ---------------------------------------------------------------- 4. Zenodo çapraz kontrolü (ikincil)
# bizim gösterge adı -> Zenodo 'gosterge_kodu'
ZENODO_CODES = {
    "population": "nufus", "hospitals": "hastane_sayisi", "hospital_beds": "yatak_sayisi",
    "beds_per_10000": "yatak_10bin", "qualified_beds": "nitelikli_yatak", "icu_beds": "yogun_bakim_yatak",
    "qualified_bed_share_pct": "nitelikli_yatak_orani", "icu_beds_per_10000": "yb_yatak_10bin",
    "visits_primary": "birinci_basamak_muracaat", "visits_secondary_tertiary": "ikinci_ucuncu_basamak_muracaat",
    "visits_per_capita_total": "kisi_basi_hekim_muracaat", "inpatients": "yatan_hasta",
    "inpatient_days": "yatilan_gun", "operations": "ameliyat", "bed_occupancy_pct": "yatak_doluluk",
    "alos_days": "ortalama_kalis", "bed_turnover_rate": "yatak_devir_hizi",
    "bed_turnover_interval_days": "yatak_devir_araligi",
}


def crosscheck_zenodo(t: Table) -> None:
    """Yıllık il satırları (2023–2024; Ankara, Türkiye) Zenodo derlemesiyle birebir aynı mı? (atıf değil)"""
    path = ZENODO_DIR / "il_verileri_yilliklardan.csv"
    if not path.exists():
        print("  uyarı: Zenodo derlemesi yok; çapraz kontrol atlandı")
        return
    years = {str(yr) for _, yr, *_ in YEARBOOKS}
    z = {}
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if r["yil"] in years and r["il"] in ("Ankara", "Türkiye"):
                z[(int(r["yil"]), r["il"], r["gosterge_kodu"])] = float(r["deger"])
    n, missing, bad = 0, [], []
    for r in t.rows:
        if (r["source_id"] not in (SRC[Y24], SRC[Y23]) or r["geography"] not in ("Ankara", "Türkiye")
                or r["indicator"] not in ZENODO_CODES or r["location"].startswith("hesap")):
            continue
        key = (r["year"], r["geography"], ZENODO_CODES[r["indicator"]])
        if key not in z:
            missing.append(key)
            continue
        n += 1
        if abs(z[key] - r["value"]) > 1e-9:
            bad.append((key, r["value"], z[key]))
    assert not bad, f"Zenodo ile uyuşmayan değerler: {bad}"
    print(f"  Zenodo (Kaya 2026): {n} yıllık il tablosu değeri birebir aynı; derlemede olmayan: {missing or 'yok'}")


# ---------------------------------------------------------------- 5. hedef doluluk kanıtları (yalnız denetim)
# saglik.yaml (saglik_bagust1999, saglik_sb_*, saglik_khgm_goren_kilavuz_2026) ve talep.yaml'daki alıntılar burada
# arşivden yeniden aranır; CSV'ye değer yazılmaz.
BAGUST_OAI = "pmc_oai_fm_PMC28163_bagust1999.xml"
BAGUST_FM_QUOTES = {  # OAI 'pmc_fm' kaydındaki bölüm -> birebir cümle
    "Abstract, Results": "Risks are discernible when average bed occupancy rates exceed about 85%, and an acute "
                         "hospital can expect regular bed shortages and periodic bed crises if average bed occupancy "
                         "rises to 90% or more.",
    "Key messages, 1. madde": "Acute hospitals which operate at bed occupancy levels of 90% or more face regular bed "
                              "crises, with the associated risks to patients",
}
BAGUST_BODY_QUOTES = {  # PMC makale sayfasındaki gövde metni (PMC_PAGE arşivi varsa denetlenir)
    "Results, 1. paragraf": "Risks are minimal so long as the mean bed occupancy remains below about 85%. Above this "
                            "level the risks become substantial (at 85%, a hospital can expect to be short of beds for "
                            "admissions on four days in a year), and above a mean bed occupancy of 90% the system is "
                            "regularly subject to bed crises.",
    "Discussion, 2. paragraf": "At rates above 85% risks become discernible, and above 90% the hospital system is "
                               "subject to regular bed crises.",
}
SP_DOC, PP_DOC, GOREN_DOC = ("sb_stratejik_plan_2024_2028.pdf", "sb_performans_programi_2026.pdf",
                             "khgm_goren_kilavuz_2026.pdf")
SP_QUOTES = {  # PDF sayfası -> birebir metin (satır sonları boşluğa çevrilmiş)
    19: "10.000 Kişiye Düşen Hastane Yatağı Sayısı 30,7 38 (Tüm Sektörler) 11",
    156: "PG4.2.1 10.000 Kişiye Düşen 30,7 Hastane Yatağı 30 32,5 34,5 36 37 38 (2022) Sayısı (Tüm Sektörler)136",
}
PP_QUOTE = (28, "711.5. Fiziki altyapının dengeli dağılımı sağlanacak, mevcut hastane yataklarının kullanım "
                "etkinliği yükseltilecektir.")
GOREN_INDICATORS = ("SYPG-BH-16 Servis Yatak Devir Hızı", "SYPG-BH-17 Yoğun Bakım Yatak Devir Hızı")


def _flat(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def check_occupancy_targets() -> None:
    """Bagust vd. alıntıları tam metinde mi; Bakanlık belgelerinde yatak doluluk hedefi/göstergesi var mı?"""
    import html as html_mod

    def text_of(name: str) -> str:
        return _flat(html_mod.unescape(re.sub(r"<[^>]+>", " ", (RAWS / name).read_text(encoding="utf-8"))))

    fm = text_of(BAGUST_OAI)
    assert "PMC28163" in fm and "10.1136/bmj.319.7203.155" in fm
    for where, q in BAGUST_FM_QUOTES.items():
        assert q in fm, f"Bagust vd. alıntısı OAI kaydında yok ({where})"
    n, body = len(BAGUST_FM_QUOTES), ""
    if (RAWS / PMC_PAGE[0]).exists():
        body = text_of(PMC_PAGE[0])
        for where, q in BAGUST_BODY_QUOTES.items():
            assert q in body, f"Bagust vd. alıntısı PMC sayfasında yok ({where})"
        n += len(BAGUST_BODY_QUOTES)
    # makale %85'i 'standart' ya da 'hedef' diye adlandırmıyor ve mortaliteden söz etmiyor (Aydemir 2019'un aktarımı)
    absent = [w for w in ("mortality", "target occupancy", "standard of 85") if w in (fm + body).lower()]
    print(f"  Bagust vd. 1999 (PMC28163): {n} alıntı birebir bulundu (gövde sayfası "
          f"{'var' if body else 'YOK — yalnız özet'}); 'mortality'/'standard' kontrolü: {'temiz' if not absent else absent}")
    for doc in (SP_DOC, PP_DOC):
        with pdfplumber.open(RAWS / doc) as pdf:
            n_occ = sum(len(re.findall(r"(?i)doluluk", p.extract_text() or "")) for p in pdf.pages)
        assert n_occ == 0, f"{doc}: 'doluluk' geçiyor ({n_occ}); kaydı güncelleyin"
    for pg, q in SP_QUOTES.items():
        assert q in _flat(page_text(SP_DOC, pg)), (SP_DOC, pg)
    assert PP_QUOTE[1] in _flat(page_text(PP_DOC, PP_QUOTE[0])), PP_DOC
    with pdfplumber.open(RAWS / GOREN_DOC) as pdf:  # tablo dikey yazılı sütunlar içeriyor: yalnız dik karakterler
        gt = " ".join(p.filter(lambda o: o.get("object_type") != "char" or o.get("upright", True)).extract_text() or ""
                      for p in pdf.pages)
    assert all(i in gt for i in GOREN_INDICATORS) and not re.search(r"(?i)doluluk", gt), GOREN_DOC
    print("  Stratejik Plan 2024–2028, Performans Programı 2026, GÖREN kılavuzu 2026: yatak doluluk hedefi/göstergesi "
          "YOK; PG4.2.1 (10.000 kişiye yatak 30,7 → 2028: 38) ve GÖREN SYPG-BH-16/17 (yatak devir hızı) var")


# ---------------------------------------------------------------- yazım ve ana akış
def write_csv(t: Table) -> None:
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in t.rows:
            v = r["value"]
            w.writerow(dict(r, value=(int(v) if float(v).is_integer() else v)))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--offline", action="store_true", help="indirme yapma; yalnız arşivlenmiş ham dosyaları kullan")
    args = ap.parse_args()

    print("1) Ham belgeler (data/raw/saglik/)")
    fetch(args.offline)

    print("2) Ayrıştırma ve kontroller")
    pop25 = tuik_population_2025()
    crosscheck_karar()
    t = Table()
    extract_bulletins(t, pop25["Türkiye"])
    extract_yearbooks(t)
    add_population_2025(t, pop25)
    add_derived(t, pop25["Türkiye"])
    crosscheck_zenodo(t)
    check_occupancy_targets()

    write_csv(t)
    print(f"3) {len(t.rows)} satır -> {OUT_CSV.relative_to(common.ROOT)}")
    for r in t.rows:
        if r["location"].startswith("hesap"):
            print(f"  {r['indicator']:38s} {r['year']} {r['geography']:28s} {r['value']}")


if __name__ == "__main__":
    main()
