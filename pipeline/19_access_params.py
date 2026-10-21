"""Erişim alanı (erisim): erişim maliyeti (G), arsa fiyatı (D) ve işletme gideri (F) türetmeleri.

Faz 1'de oturum çalışma alanında kalan fetch.py (arşivleme) ve derive.py (hesap) betiklerinin yerini alır.

Adımlar
1. Arşiv: DOWNLOADS'taki belgeler data/raw/erisim/ altında yoksa indirilir; dosya ve manifest kaydı varsa
   atlanır (manifest: common.download). --offline ile hiç indirme denenmez, eksik belge hata verir.
   Not: TÜİK 'downloads?p=…' bağlantıları oturuma bağlı belirteç içerir, yeniden indirmede geçersiz olabilir.
2. Girdiler arşivden okunur: TÜİK tabloları (xls/csv) doğrudan; haber/PDF metnindeki sayılar arşiv belgesinin
   metninden düzenli ifadeyle çekilir. Metin katmanı olmayan belge (RG asgari ücret kararı) görsel okumadır ve
   sabit olarak yazılır. Yatak sayıları saglik alanının data/reference/saglik_istatistikleri.csv tablosundan okunur.
3. Türetmeler yazdırılır (ilk bölüm eski derive.py çıktısıyla aynı biçimde) ve
   data/reference/params/{erisim,arsa,isletme}.yaml değerleriyle karşılaştırılır.

Çıktı: dosya yazmaz (parametre YAML'ları elle düzenlenen tek doğru kaynaktır); uyuşmazlıkta çıkış kodu 1.
Çalıştırma (repo kökünden):  .venv/bin/python pipeline/19_access_params.py [--offline]
Yeni belge arşivlemek:       .venv/bin/python pipeline/19_access_params.py --fetch URL DOSYA_ADI [--xhr] [--cacert YOL] [--post JSON]
Ayrıntılı not: data/reference/erisim_turetim.md
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import re
import sys
from functools import lru_cache
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline import common  # noqa: E402

RAW_DIR = common.RAW / "erisim"
PARAM_DOMAINS = ("erisim", "arsa", "isletme")
HEALTH_CSV = common.REFERENCE / "saglik_istatistikleri.csv"

# Arşiv belgeleri: dosya adı -> URL (data/raw/erisim/_manifest.jsonl ile aynı)
DOWNLOADS: dict[str, str] = {
    "tuik_press_58046_mkt_eylul2026.json":
        "https://veriportali.tuik.gov.tr/api/tr/press/58046",
    "tuik_mkt_eylul2026_iller.xls":
        "https://veriportali.tuik.gov.tr/api/tr/data/downloads?t=t&pid=58046&p=GgZLaFTAcNZyXV6IndZr7s9zUTyWyamP%2FIEPx%2FXUlS8vgS6KHwitZuwpfywkhVPpgbFKrclYOqZMMi6ZkFfCr5%2BwYIvqpbpyyUECt%2FTObbuC%2BARxhOeLZ7MduTlFQ3ymAL1dQQjF%2FWghS85HQkXKLg%3D%3D",
    "tuik_mkt_eylul2026_otomobil_yakit.xls":
        "https://veriportali.tuik.gov.tr/api/tr/data/downloads?t=t&pid=58046&p=QbnDV82P2BgapDdu3Zb99ku6MrEvCDVPzp%2BJPcaDcXOD691b5hAWgxEXLpgM9ZaVsiFA%2FbBcmWdKTb4ykXpJubV5qal3eNAk1GhC95WxvYqQWpofXCe4tg67Dx%2FkBAmsrl6bAb1rYNPUcoNi1ViFVqoeHETvjjgMbwz1gPF9nCQ%3D",
    "epdk_3-0-158_akaryakit_fiyatlari.html":
        "https://www.epdk.gov.tr/Detay/Icerik/3-0-158/akaryak%C4%B1tfiyat",
    "epdk_3-0-143_fiyatlandirma_raporu_liste.html":
        "https://www.epdk.gov.tr/Detay/Icerik/3-0-143/fiyatlandirma-raporu",
    "epdk_fiyatlandirma_raporu_2026_09.pdf":
        "https://www.epdk.gov.tr/Detay/DownloadDocument?id=93FETDA2vvw=",
    "haber_birgun_741622_akaryakit_21ekim2026.html":
        "https://www.birgun.net/haber/21-ekim-2026-benzin-ve-motorin-fiyatlarinda-indirim-zam-var-mi-guncel-akaryakit-fiyatlari-741622",
    "haber_birgun_741363_akaryakit_20ekim2026.html":
        "https://www.birgun.net/haber/20-ekim-2026-benzin-ve-motorin-fiyatlarinda-indirim-zam-var-mi-guncel-akaryakit-fiyatlari-741363",
    "haber_yeniankara_187070_akaryakit_21ekim2026.html":
        "https://www.yeniankara.com.tr/ekonomi/21-ekim-2026-akaryakit-fiyatlari-benzine-indirim-var-mi-ankara-istanbul-izmir-guncel-liste-187070",
    "sbb_oik2023_kent_ici_ulasim.pdf":
        "https://www.sbb.gov.tr/wp-content/uploads/2025/08/Kent-Ici-Ulasim-OIK-Raporu_01082025.pdf",
    "sbb_oik2023_karayolu_ulastirmasi.pdf":
        "https://www.sbb.gov.tr/wp-content/uploads/2025/08/Karayolu-Ulastirmasi-Ozel-Ihtisas-Komisyonu-Raporu_01082025.pdf",
    "haber_yeniankara_185439_tomtom_trafik.html":
        "https://www.yeniankara.com.tr/ankara/ankara-trafik-suresi-suruculer-yilda-kac-saat-trafikte-kaliyor-185439",
    "dergipark_193241_ulastirma_iskonto_orani.pdf":
        "https://dergipark.org.tr/en/download/article-file/193241",
    "dergipark_pausbed_1115230.html":
        "https://dergipark.org.tr/tr/pub/pausbed/article/1115230",
    "dergipark_2421920_kalayci_oflaz_2022_sosyal_iskonto.pdf":
        "https://dergipark.org.tr/tr/download/article-file/2421920",
    "sbb_uzunkaya_2012_ekonomik_indirgeme_orani.pdf":
        "https://www.sbb.gov.tr/wp-content/uploads/2018/11/Turkiye_Icin_Ekonomik_Indirgeme_Orani_Tahmini.pdf",
    "haber_diken_536163_bilkent_koi.html":
        "https://www.diken.com.tr/?p=536163",
    "mevzuat_1.5.6428_koi_saglik_kanunu.pdf":
        "https://www.mevzuat.gov.tr/MevzuatMetin/1.5.6428.pdf",
    "sygm_TR-32393_koi_daire_baskanligi.html":
        "https://sygm.saglik.gov.tr/TR,32393/kamu-ozel-isbirligi-daire-baskanligi.html",
    "tbmm_haber_saglik_butce_2024_pbk.html":
        "https://www.tbmm.gov.tr/Haber/Detay?Id=0092bb1b-0174-46b9-8cf3-018bb76df0af",
    "RG_20251226_fihrist.htm":
        "https://www.resmigazete.gov.tr/eskiler/2025/12/20251226.htm",
    "RG_20251226-6_asgari_ucret_karari_2026.pdf":
        "https://www.resmigazete.gov.tr/eskiler/2025/12/20251226-6.pdf",
    "dergipark_gazimmfd_416487.html":
        "https://dergipark.org.tr/tr/pub/gazimmfd/article/416487",
    "dergipark_679156_tuydes_dalkic_2019_yht_zaman_degeri.pdf":
        "https://dergipark.org.tr/tr/download/article-file/679156",
    "dergipark_5040620_sakaltutan_tunel.pdf":
        "https://dergipark.org.tr/tr/download/article-file/5040620",
    "tuik_press_53700_kazanc_yapisi_2023.json":
        "https://veriportali.tuik.gov.tr/api/tr/press/53700",
    "tuik_press_54188_saglik_harcamalari_2024.json":
        "https://veriportali.tuik.gov.tr/api/tr/press/54188",
    "tuik_press_58298_tufe_eylul2026.json":
        "https://veriportali.tuik.gov.tr/api/tr/press/58298",
    "tuik_saglik_harcamalari_2024_sunuculara_gore.xls":
        "https://veriportali.tuik.gov.tr/api/tr/data/downloads?t=t&pid=54188&p=3cHvVwntSQbVw%2BWpt0AAOK64WYQU%2BIi%2FQFaoiCE%2F4JBwCLBGORIs0AqgCvsXXmWjA8FInMk%2Bpc%2B6C8dxb1dfGhsMlwwdq4%2Fkaa%2B%2BNbJl8wuZWrPaf5fTIVesp%2FhMjSSG8NMIuU5rAP1Hd6scvRWe4rFDzMGWJP43qLSK8%2FAhQCo%3D",
    "tuik_df_tufe_sdmx_tt10.csv":
        "https://veriportali.tuik.gov.tr/api/tr/dataflows/DF_TUFE_SDMX_TT10+V1.0/file/csv",
    "dergipark_1472050_karayolu_karbon_ayak_izi.pdf":
        "https://dergipark.org.tr/tr/download/article-file/1472050",
    "mevzuat_33805_kamu_saglik_tesisleri_ruhsatlandirma_yonetmeligi.pdf":
        "https://www.mevzuat.gov.tr/File/GeneratePdf?mevzuatNo=33805&mevzuatTur=KurumVeKurulusYonetmeligi&mevzuatTertip=5",
    "dergipark_guiibd_1515345.html":
        "https://dergipark.org.tr/en/pub/guiibd/article/1515345",
    "dergipark_4066881_saglik_tesisi_erisilebilirlik_mevzuat.pdf":
        "https://dergipark.org.tr/en/download/article-file/4066881",
    "RG_20251231M5-27_emlak_vergisi_teblig.pdf":
        "https://www.resmigazete.gov.tr/eskiler/2025/12/20251231M5-27.pdf",
    "haber_yeniankara_184430_toki_arsa.html":
        "https://www.yeniankara.com.tr/ankara/tokinin-arsa-acik-artirmasinda-ankarada-nereler-satista-184430",
    "haber_cumhuriyet_2495348_arsa_fiyatlari.html":
        "https://www.cumhuriyet.com.tr/ekonomi/arsa-fiyatlari-en-cok-artan-bolgeler-belli-oldu-zirvedeki-il-sasirtti-2495348",
    "mevzuat_1.5.4857_is_kanunu.pdf":
        "https://www.mevzuat.gov.tr/MevzuatMetin/1.5.4857.pdf",
    "haber_yeniankara_167274_evren_arsa_2026.html":
        "https://www.yeniankara.com.tr/evren/ankaranin-sakli-bahcesi-degerleniyor-2026-yili-evren-arsa-fiyatlari-167274",
}

# Kısa adlar (türetmede okunan belgeler)
F_FLEET = "tuik_mkt_eylul2026_otomobil_yakit.xls"
F_BIRGUN_21 = "haber_birgun_741622_akaryakit_21ekim2026.html"
F_BIRGUN_20 = "haber_birgun_741363_akaryakit_20ekim2026.html"
F_YENIANKARA_21 = "haber_yeniankara_187070_akaryakit_21ekim2026.html"
F_EPDK = "epdk_fiyatlandirma_raporu_2026_09.pdf"
F_YALILI = "dergipark_1472050_karayolu_karbon_ayak_izi.pdf"
F_CPI = "tuik_df_tufe_sdmx_tt10.csv"
F_BAGDATLI = "dergipark_193241_ulastirma_iskonto_orani.pdf"
F_LABOUR_LAW = "mevzuat_1.5.4857_is_kanunu.pdf"
F_KYA = "tuik_press_53700_kazanc_yapisi_2023.json"
F_TOMTOM = "haber_yeniankara_185439_tomtom_trafik.html"
F_KALAYCI = "dergipark_2421920_kalayci_oflaz_2022_sosyal_iskonto.pdf"
F_HEALTH_EXP = "tuik_saglik_harcamalari_2024_sunuculara_gore.xls"
F_TOKI = "haber_yeniankara_184430_toki_arsa.html"
F_EVREN = "haber_yeniankara_167274_evren_arsa_2026.html"

# Metin katmanı olmayan belge: RG 26.12.2025/33119, Karar 2025/1 md. 2 (görsel okuma)
MIN_WAGE_DAY_TL = 1101.0
WORK_DAYS_PER_WEEK = 6          # 45 sa/hafta 6 güne eşit bölünür (4857 md. 63) -> 7,5 sa/gün
VOT_FRACTION = 0.5              # varsayım (bkz. access.value_of_time_tl_per_h)
MONTH_HOURS = 225               # 30 gün × 7,5 sa (= 45 sa × 5 hafta); aylık 33.030 / 225 = 146,80
LEGACY_VISITS = (7.75, 6.92)    # derive.py'deki v örnekleri (önceki varsayılan; şartname ≈6,9)
PV_CASES = ((25, 0.05), (25, 0.03), (25, 0.098), (30, 0.05), (20, 0.05), (15, 0.05))
FUELS = ("benzin", "motorin", "lpg")


# ---------------------------------------------------------------- arşiv (eski fetch.py)

def _manifest_records(dest: Path) -> list[dict]:
    man = dest.parent / "_manifest.jsonl"
    if not man.exists():
        return []
    out = []
    for line in man.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _manifest_has(dest: Path, url: str) -> bool:
    rel = str(dest.relative_to(common.ROOT))
    return any(r.get("url") == url and r.get("file") == rel for r in _manifest_records(dest))


def ensure(name: str, url: str | None = None, *, offline: bool = False, xhr: bool = False,
           cacert: str | None = None, post: str | None = None, timeout: int = 120) -> Path:
    """Belgeyi data/raw/erisim/ altına arşivler; dosya + manifest kaydı varsa hiçbir şey yapmaz."""
    url = url or DOWNLOADS[name]
    dest = RAW_DIR / name
    if dest.exists() and _manifest_has(dest, url):
        return dest
    if not dest.exists():
        if offline:
            raise SystemExit(f"HATA: arşivde yok ve --offline verildi: {dest.relative_to(common.ROOT)}")
        import requests
        headers = {"User-Agent": common.USER_AGENT}
        if xhr:
            headers["X-Requested-With"] = "XMLHttpRequest"
        kw: dict = {"headers": headers, "timeout": timeout}
        if cacert:
            kw["verify"] = cacert
        if post is not None:
            headers["Content-Type"] = "application/json"
            r = requests.post(url, data=post.encode("utf-8"), **kw)
        else:
            r = requests.get(url, **kw)
        r.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.content)
    common.download(url, dest)  # dosya var: yalnız manifest kaydı
    return dest


def verify_archive(names: list[str]) -> list[str]:
    """Okunan belgelerin sha256'sı manifestteki son kayıtla aynı mı (uyarı listesi döndürür)."""
    warnings = []
    for name in names:
        dest = RAW_DIR / name
        rel = str(dest.relative_to(common.ROOT))
        recs = [r for r in _manifest_records(dest) if r.get("file") == rel]
        if not recs:
            warnings.append(f"manifest kaydı yok: {rel}")
        elif recs[-1].get("sha256") != common.sha256(dest):
            warnings.append(f"sha256 manifestle uyuşmuyor: {rel}")
    return warnings


# ---------------------------------------------------------------- belge metni

def _html_text(html: str) -> str:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return soup.get_text(" ")


@lru_cache(maxsize=None)
def pdf_pages(name: str) -> tuple[str, ...]:
    import pdfplumber
    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    with pdfplumber.open(RAW_DIR / name) as pdf:
        return tuple(p.extract_text() or "" for p in pdf.pages)


@lru_cache(maxsize=None)
def doc_text(name: str) -> str:
    """Arşiv belgesinin düz metni (boşluklar tekleştirilmiş)."""
    path = RAW_DIR / name
    suffix = path.suffix.lower()
    if suffix in (".html", ".htm"):
        text = _html_text(path.read_bytes().decode("utf-8", errors="replace"))
    elif suffix == ".json":  # TÜİK bülteni: data.content (HTML)
        text = _html_text(json.loads(path.read_text(encoding="utf-8"))["data"]["content"])
    elif suffix == ".pdf":
        text = "\n".join(pdf_pages(name))
    else:
        raise ValueError(f"metin çıkarılamaz: {name}")
    return re.sub(r"\s+", " ", text)


def find(name: str, pattern: str) -> re.Match:
    """Belgede düzenli ifadenin ilk eşleşmesi; yoksa dur."""
    m = re.search(pattern, doc_text(name))
    if not m:
        raise SystemExit(f"HATA: {name} içinde bulunamadı: {pattern!r}")
    return m


def require(name: str, phrase: str) -> None:
    if phrase not in doc_text(name):
        raise SystemExit(f"HATA: {name} içinde ifade yok: {phrase!r}")


def tr_num(s: str) -> float:
    """Türkçe sayı: '1.379' -> 1379, '80,60' -> 80.6."""
    return float(s.replace(".", "").replace(",", "."))


def pdf_table_rows(name: str, caption: str) -> dict[int, list[float]]:
    """PDF'te başlıktan sonraki ilk ardışık 'YYYY sayı sayı …' satır bloğu (yıl -> değerler)."""
    for page in pdf_pages(name):
        if caption not in page:
            continue
        rows: dict[int, list[float]] = {}
        for line in page.split(caption, 1)[1].splitlines():
            m = re.match(r"^(20\d\d)((?: \d+(?:\.\d+)?)+)$", line.strip())
            if m:
                rows[int(m[1])] = [float(x) for x in m[2].split()]
            elif rows:
                break
        if rows:
            return rows
    raise SystemExit(f"HATA: {name} içinde tablo bulunamadı: {caption!r}")


# ---------------------------------------------------------------- girdiler

def read_fleet() -> dict[str, int]:
    """TÜİK MKT Eylül 2026, sayfa t6: 2026 (Eylül sonu) satırı."""
    import xlrd
    sheet = xlrd.open_workbook(RAW_DIR / F_FLEET).sheet_by_name("t6")
    header = [str(x).strip() for x in sheet.row_values(3)]
    cols = {"toplam": header.index("Total"), "benzin": header.index("Gasoline"), "dizel": header.index("Diesel"),
            "lpg": header.index("LPG"), "hibrit": header.index("Hybrid (2)"), "elektrik": header.index("Electric"),
            "bilinmeyen": header.index("Unknown (3)")}
    for r in range(sheet.nrows):
        row = sheet.row_values(r)
        if str(row[0]).startswith("2026"):
            return {k: int(row[c]) for k, c in cols.items()}
    raise SystemExit("HATA: yakıt tablosunda 2026 satırı yok")


def read_ankara_prices() -> tuple[dict[str, float], dict[str, float]]:
    """Ankara 21.10.2026 pompa fiyatı (BirGün; Yeni Ankara ve BirGün 20.10 ile çapraz kontrol) + benzin indirimleri."""
    pat = r"Ankara (\d+,\d+) TL (\d+,\d+) TL (\d+,\d+) TL"
    prices = {}
    for name in (F_BIRGUN_21, F_YENIANKARA_21, F_BIRGUN_20):
        prices[name] = dict(zip(FUELS, (tr_num(x) for x in find(name, pat).groups())))
    if len({tuple(p.values()) for p in prices.values()}) != 1:
        raise SystemExit(f"HATA: haber kaynaklarında Ankara fiyatları farklı: {prices}")
    cuts = {
        "onceki_gun": tr_num(find(F_BIRGUN_21, r"benzine önceki gün (\d+,\d+) TL indirim")[1]),
        "22_ekim": tr_num(find(F_BIRGUN_21, r"22 Ekim 2026 Perşembe gününden itibaren geçerli olmak üzere "
                                            r"(\d+,\d+) TL'lik yeni bir indirim")[1]),
    }
    return prices[F_BIRGUN_21], cuts


def read_epdk_prices() -> tuple[dict[str, float], float]:
    """EPDK Fiyatlandırma Raporu Eylül 2026: İstanbul Avrupa aylık ortalama nihai fiyat (Tablo-1, -2, -5) + Brent."""
    last = lambda pat: tr_num(find(F_EPDK, pat)[1].split()[-1])  # noqa: E731 — satırın son sütunu: nihai fiyat
    prices = {
        "benzin": last(r"Eylül 2026 K\.Benzin 95 Oktan((?: \d+,\d+)+)"),
        "motorin": last(r"Eylül 2026 Motorin((?: \d+,\d+)+)"),
        "lpg": last(r"2026 Eylül((?: \d+,\d+)+)"),
    }
    brent = tr_num(find(F_EPDK, r"varil fiyatının ortalama (\d+,\d+) dolar")[1])
    return prices, brent


def read_consumption_and_mileage() -> tuple[dict[str, float], dict[int, dict[str, float]]]:
    """Yalılı Kılıç vd. 2021: Tablo 7 (otomobil L/100 km) ve Tablo 8 (otomobil ortalama yıllık km)."""
    t7 = pdf_table_rows(F_YALILI, "Tablo 7. Araç gruplarının 100 km")
    t8 = pdf_table_rows(F_YALILI, "Tablo 8. Araç gruplarının ortalama yıllık menzilleri")
    cons = {tuple(v[:3]) for v in t7.values()}
    if len(cons) != 1:
        raise SystemExit(f"HATA: Tablo 7 otomobil tüketimi yıllara göre değişiyor: {cons}")
    q = dict(zip(FUELS, cons.pop()))           # sütunlar: otomobil benzin, dizel, LPG
    km = {y: dict(zip(FUELS, v[:3])) for y, v in sorted(t8.items())}
    return q, km


def read_cpi() -> dict[str, float]:
    """TÜFE (2025=100), TÜİK DF_TUFE_SDMX_TT10."""
    with open(RAW_DIR / F_CPI, encoding="utf-8-sig") as f:
        rows = list(csv.reader(f, delimiter=";"))
    return {r[7]: float(r[8].replace(",", ".")) for r in rows[1:] if r[3] == "Endeks" and r[5] == "TÜFE"}


def read_kya() -> dict[str, float]:
    """TÜİK Kazanç Yapısı İstatistikleri 2023 bülteni (Kasım 2023 referans ayı)."""
    m = find(F_KYA, r"aylık brüt asgari ücret (\d+) bin (\d+,\d+) TL")
    w = find(F_KYA, r"aylık ortalama kişi başı brüt ücret-maaş (\d+) bin (\d+) TL")
    return {
        "min_month": int(m[1]) * 1000 + tr_num(m[2]),
        "avg_month": int(w[1]) * 1000 + tr_num(w[2]),
        "avg_hour": tr_num(find(F_KYA, r"Saatlik ortalama brüt ücret-maaş (\d+) TL")[1]),
        "paid_hours": tr_num(find(F_KYA, r"Aylık ücretli çalışılan saat (\d+) olarak")[1]),
    }


def read_tomtom() -> dict[str, float]:
    """TomTom Trafik Endeksi 2025, Ankara (Yeni Ankara aktarımı)."""
    t = find(F_TOMTOM, r"10 kilometrelik ortalama bir yolculuğun süresi de (\d+) dakika (\d+) saniye")
    return {
        "min_per_10km": int(t[1]) + int(t[2]) / 60,
        "peak_kmh": tr_num(find(F_TOMTOM, r"Yoğun saatlerde ortalama hız ise (\d+,\d+) kilometre")[1]),
        "evening_kmh": tr_num(find(F_TOMTOM, r"Akşam yoğunluğunda ortalama hız ise (\d+,\d+) kilometre")[1]),
        "congestion": tr_num(find(F_TOMTOM, r"ortalama trafik sıkışıklığı seviyesi yüzde (\d+,\d+)")[1]) / 100,
    }


def read_hospital_spending() -> tuple[float, float]:
    """TÜİK sağlık harcamaları 2024, sayfa t1 'Hospitals' satırı: (genel toplam, genel devlet), TL."""
    import xlrd
    sheet = xlrd.open_workbook(RAW_DIR / F_HEALTH_EXP).sheet_by_name("t1")
    years = sheet.row_values(6)
    if (years[3], years[6]) != (2024.0, 2024.0):
        raise SystemExit(f"HATA: sağlık harcaması tablosunda beklenmeyen yıl sütunları: {years}")
    for r in range(sheet.nrows):
        row = sheet.row_values(r)
        if str(row[1]).strip() == "Hospitals":
            return row[3] * 1e6, row[6] * 1e6  # milyon TL -> TL
    raise SystemExit("HATA: 'Hospitals' satırı yok")


def read_beds_2024() -> dict[str, int]:
    """2024 Türkiye hastane yatakları (SİY 2024 Haber Bülteni Tablo 7; saglik alanı tablosu)."""
    want = {"hospital_beds": "toplam", "hospital_beds_moh": "sb", "hospital_beds_university": "universite",
            "hospital_beds_private": "ozel"}
    out = {}
    with open(HEALTH_CSV, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if (r["indicator"] in want and r["year"] == "2024" and r["geography"] == "Türkiye"
                    and r["source_id"] == "saglik_sbsgm_bulten2024"):
                out[want[r["indicator"]]] = int(r["value"])
    if set(out) != set(want.values()) or out["sb"] + out["universite"] + out["ozel"] != out["toplam"]:
        raise SystemExit(f"HATA: 2024 yatak sayıları eksik/tutarsız: {out}")
    return out


def read_land_prices() -> dict[str, tuple[float, float]]:
    """TOKİ muhammen bedelleri (Yeni Ankara 184430) -> (bedel TL, alan m²)."""
    inc = find(F_TOKI, r"Gölbaşı İncek’te (\d+) bin (\d+) metrekare büyüklüğündeki taşınmaz için yaklaşık "
                       r"(\d+,\d+) milyon lira")
    sus = find(F_TOKI, r"Yenimahalle Susuz’da (\d+) bin (\d+,\d+) metrekarelik kentsel servis alanı niteliğindeki "
                       r"taşınmazın muhammen bedeli yaklaşık (\d+,\d+) milyon lira")
    tem = find(F_TOKI, r"yaklaşık (\d+) bin metrekare seviyesinde bulunurken, bazı parsellerin muhammen bedeli "
                       r"(\d+) milyon liranın üzerine")
    pec = find(F_TOKI, r"yaklaşık (\d+) bin ile (\d+) bin (\d+) metrekare arasında değişiyor ve bu grupta "
                       r"(\d+) milyon liraya")
    return {
        "Gölbaşı İncek": (tr_num(inc[3]) * 1e6, int(inc[1]) * 1000 + int(inc[2])),
        "Yenimahalle Susuz (kentsel servis alanı)": (tr_num(sus[3]) * 1e6, int(sus[1]) * 1000 + tr_num(sus[2])),
        "Sincan Temelli/Alcı (kaba)": (int(tem[2]) * 1e6, int(tem[1]) * 1000),
        "Sincan Yeni Peçenek, büyük parsel (kaba)": (int(pec[4]) * 1e6, int(pec[2]) * 1000 + int(pec[3])),
        "Sincan Yeni Peçenek, küçük parsel (kaba)": (int(pec[4]) * 1e6, int(pec[1]) * 1000),
    }


def current_visits() -> float | None:
    """access.hospital_visits_per_capita'nın güncel değeri (saglik alanı parçası)."""
    for path in sorted(common.PARAMS_DIR.glob("*.yaml")):
        for p in yaml.safe_load(path.read_text(encoding="utf-8")) or []:
            if isinstance(p, dict) and p.get("key") == "access.hospital_visits_per_capita":
                return p.get("value")
    return None


# ---------------------------------------------------------------- hesap

def fuel_calc(share: dict[str, float], price: dict[str, float], q: dict[str, float]):
    """(filo payı ağırlıklı fiyat, ort. tüketim L/100 km, c TL/km, litre ağırlıklı etkin fiyat)."""
    p_share = sum(share[k] * price[k] for k in share)
    q_avg = sum(share[k] * q[k] for k in share)
    c = sum(share[k] * price[k] * q[k] for k in share) / 100
    return p_share, q_avg, c, 100 * c / q_avg


def pv(T: float, r: float) -> float:
    return T if r == 0 else (1 - (1 + r) ** -T) / r


def derive() -> tuple[dict, list[str]]:
    """Tüm türetmeleri yapar; (değerler, yazdırılan satırlar) döndürür."""
    out: list[str] = []
    say = out.append
    V: dict = {}

    # --- filo payları (hibrit benzine; elektrik ve bilinmeyen dışarıda)
    stock = read_fleet()
    total = stock.pop("toplam")
    assert abs(sum(stock.values()) - total) <= 5, sum(stock.values())
    liq = dict(benzin=stock["benzin"] + stock["hibrit"], motorin=stock["dizel"], lpg=stock["lpg"])
    S = sum(liq.values())
    share = {k: v / S for k, v in liq.items()}
    say(f"sıvı yakıtlı otomobil: {S} pay: {({k: round(v, 5) for k, v in share.items()})} "
        f"dışarıda kalan (elektrik+bilinmeyen) payı: {round((stock['elektrik'] + stock['bilinmeyen']) / total, 4)}")

    # --- fiyatlar ve tüketim
    p, cuts = read_ankara_prices()
    p_epdk, brent = read_epdk_prices()
    q, km = read_consumption_and_mileage()
    for name, pr in (("Ankara 21.10.2026", p), ("EPDK Eylül 2026 İst. Avrupa ort.", p_epdk)):
        ps, qa, c, pe = fuel_calc(share, pr, q)
        say(f"{name}: pay-ağırlıklı fiyat {ps:.3f} TL/L; litre-ağırlıklı etkin fiyat {pe:.3f} TL/L; "
            f"ort. tüketim {qa:.4f} L/100km; c = {c:.4f} TL/km; pay-ağırlıklı fiyat × ort. tüketim = {ps*qa/100:.4f}")
        V[name] = dict(p_share=ps, q_avg=qa, c=c, p_eff=pe)
    V["p"], V["q"], V["share"] = p, q, share

    # --- TÜFE ve KGM taşıt işletme maliyeti
    cpi = read_cpi()
    avg = lambda y: sum(v for k, v in cpi.items() if k.startswith(y)) / 12  # noqa: E731
    c24, c13, s26 = avg("2024"), avg("2013"), cpi["2026-09"]
    say(f"TÜFE: 2013 ort {c13:.4f}; 2024 ort {c24:.4f}; 2023-11 {cpi['2023-11']:.4f}; 2026-09 {s26}; "
        f"çarpan 2024→2026-09 {s26/c24:.4f}; 2013→2026-09 {s26/c13:.3f}")
    require(F_BAGDATLI, "0.26029 TL/km")
    V["kgm"] = 0.26029 * s26 / c13
    say(f"KGM TİM 2013 → 2026-09 fiyatı: {V['kgm']:.3f} TL/km")

    # --- zaman değeri
    require(F_LABOUR_LAW, "haftada en çok kırkbeş saattir")
    w_h = MIN_WAGE_DAY_TL / (45 / WORK_DAYS_PER_WEEK)
    say(f"saatlik brüt asgari ücret {w_h:.2f} TL/sa; aylık (30 gün) {30*MIN_WAGE_DAY_TL:.0f}")
    assert math.isclose(30 * MIN_WAGE_DAY_TL / MONTH_HOURS, w_h)
    kya = read_kya()
    ratio = kya["avg_month"] / kya["min_month"]          # aylık / aylık: saat tabanından bağımsız
    V["vot_avg_wage"] = w_h * ratio
    vot_cpi = kya["avg_month"] / MONTH_HOURS * s26 / cpi["2023-11"]
    say(f"KYA 2023 aylık ortalama brüt ücret-maaş / aylık brüt asgari ücret = {kya['avg_month']:.0f} / "
        f"{kya['min_month']:.1f} = {ratio:.4f} → 2026 ortalama saatlik brüt (225 sa tabanı) ≈ "
        f"{V['vot_avg_wage']:.1f} TL/sa; TÜFE ile ({kya['avg_month']:.0f}/225 × TÜFE oranı): {vot_cpi:.1f}")
    old_ratio = kya["avg_hour"] / (kya["min_month"] / MONTH_HOURS)
    say(f"  (eski, karışık saat tabanı: {kya['avg_hour']:.0f} TL/sa [{kya['paid_hours']:.0f} sa tabanı] / "
        f"({kya['min_month']:.1f}/225) = {old_ratio:.4f} → {w_h*old_ratio:.1f}; TÜFE ile: "
        f"{kya['avg_hour']*s26/cpi['2023-11']:.1f}; kullanılmıyor)")
    vot = VOT_FRACTION * w_h
    V["w_h"], V["vot"] = w_h, vot
    say(f"VOT = {VOT_FRACTION} × {w_h:.2f} = {vot:.2f} TL/sa")

    # --- hız
    tt = read_tomtom()
    speed = 10 / (tt["min_per_10km"] / 60)
    free_flow = speed * (1 + tt["congestion"])
    V.update(speed=speed, free_flow=free_flow, peak=tt["peak_kmh"], evening=tt["evening_kmh"])
    say(f"ort. hız = 10 km / {tt['min_per_10km']:.4f} dk = {speed:.3f} km/sa; serbest akış (sıkışıklık %55,8) "
        f"≈ {free_flow:.2f}")

    # --- PV ve λ
    require(F_KALAYCI, "uyum ülkelerindeki büyük projeler için sosyal indirgeme oranı olarak %5")
    require(F_KALAYCI, "%5,06")
    for T, r in PV_CASES:
        say(f"PV(T={T}, r={r}) = {pv(T, r):.4f}")
    c = V["Ankara 21.10.2026"]["c"]
    visits = list(LEGACY_VISITS)
    for vv in visits:
        lam = pv(25, 0.05) * vv * 2 * (c + vot / speed)
        say(f"λ(v={vv}) = {pv(25,0.05):.4f} × {vv} × 2 × ({c:.4f} + {vot:.2f}/{speed:.3f}) = {lam:.1f} TL/(kişi·km);"
            f" yıllık kişi·km başı = {lam/pv(25,0.05):.2f}")

    # --- işletme gideri
    hosp, hosp_gov = read_hospital_spending()
    beds = read_beds_2024()
    beds_pub = beds["sb"] + beds["universite"]
    V["opex_2024"] = hosp / beds["toplam"]
    V["opex_2026"] = V["opex_2024"] * s26 / c24
    say(f"yatak başı yıllık (2024 TL): tümü {hosp/beds['toplam']:,.0f}; genel devlet/kamu+üni {hosp_gov/beds_pub:,.0f}")
    say(f"2026-09 fiyatlarıyla: tümü {V['opex_2026']:,.0f}; kamu {hosp_gov/beds_pub*s26/c24:,.0f}")

    # --- arsa örnekleri
    land = read_land_prices()
    V["land"] = {k: val / area for k, (val, area) in land.items()}
    for name in ("Gölbaşı İncek", "Yenimahalle Susuz (kentsel servis alanı)"):
        say(f"{name}: {V['land'][name]:,.0f} TL/m²")

    # ================= ek türetmeler (Faz 1 temizliği; eski derive.py'de yoktu)
    say("--- ek türetmeler")
    for name in ("Sincan Temelli/Alcı (kaba)", "Sincan Yeni Peçenek, büyük parsel (kaba)",
                 "Sincan Yeni Peçenek, küçük parsel (kaba)"):
        say(f"{name}: {V['land'][name]:,.0f} TL/m²")
    V["evren"] = tr_num(find(F_EVREN, r"konut imarlı arsaların metrekare birim fiyatları ortalama "
                                     r"(\d+\.\d+) TL")[1])
    say(f"Evren konut imarlı arsa ortalaması: {V['evren']:,.0f} TL/m²")

    # Varsayılan ağırlık: her yakıt türünde araç başı yıllık km aynı (araç-km payı = filo payı).
    # Alternatif: araç-km payı w_f = s_f·m_f / Σ s·m, m_f = Yalılı Kılıç Tablo 8 (otomobil ort. yıllık km).
    V["km_weighted"] = {}
    for year, m in km.items():
        W = sum(share[k] * m[k] for k in FUELS)
        wkm = {k: share[k] * m[k] / W for k in FUELS}
        ps, qa, ck, pe = fuel_calc(wkm, p, q)
        _, _, ck_epdk, _ = fuel_calc(wkm, p_epdk, q)
        V["km_weighted"][year] = dict(w=wkm, q_avg=qa, c=ck, p_eff=pe, c_epdk=ck_epdk, mean_km=W)
        say(f"Tablo 8 {year} km ağırlıklı (benzin/motorin/LPG km {m['benzin']:.0f}/{m['motorin']:.0f}/"
            f"{m['lpg']:.0f}): araç-km payı {({k: round(v, 5) for k, v in wkm.items()})}; ort. tüketim "
            f"{qa:.4f} L/100km; c = {ck:.4f} TL/km; litre-ağırlıklı fiyat {pe:.3f} TL/L; EPDK fiyatıyla c = "
            f"{ck_epdk:.4f}")
    V["km_year"] = max(km)

    # Tek günlük haber fiyatı; 22.10.2026 beklenen benzin indirimi uygulanırsa
    p_cut = dict(p, benzin=round(p["benzin"] - cuts["22_ekim"], 2))
    _, qa, c_cut, pe_cut = fuel_calc(share, p_cut, q)
    V["c_after_cut"], V["p_after_cut"] = c_cut, pe_cut
    say(f"22.10.2026 beklenen benzin indirimi −{cuts['22_ekim']:.2f} TL (önceki gün −{cuts['onceki_gun']:.2f}) → "
        f"benzin {p_cut['benzin']:.2f}: litre-ağırlıklı fiyat {pe_cut:.3f} TL/L; c = {c_cut:.4f} TL/km "
        f"({100*(c_cut/c - 1):+.1f}%)")
    say(f"EPDK raporu: Eylül 2026 Brent ortalaması {brent} $/varil")

    v_now = current_visits()
    lam_per_v = pv(25, 0.05) * 2 * (c + vot / speed)
    say(f"λ / v = PV(25; %5) × 2 × (c + VOT/hız) = {lam_per_v:.2f} TL/(kişi·km) başına başvuru/kişi/yıl"
        + (f"; güncel v = {v_now} ile λ = {lam_per_v * v_now:.1f}" if v_now else ""))
    return V, out


# ---------------------------------------------------------------- YAML denetimi

def load_params() -> dict[str, dict]:
    out = {}
    for d in PARAM_DOMAINS:
        for p in yaml.safe_load((common.PARAMS_DIR / f"{d}.yaml").read_text(encoding="utf-8")) or []:
            out[p["key"]] = p
    return out


def yaml_value(P: dict, key: str, alt: str | None = None):
    p = P[key]
    if alt is None:
        return p["value"]
    hits = [a for a in p.get("alternatives") or [] if alt in a["label"]]
    if len(hits) != 1:
        raise KeyError(f"{key}: '{alt}' etiketli {len(hits)} alternatif")
    return hits[0]["value"]


def check_yaml(V: dict) -> list[str]:
    """Türetilmiş değerler (YAML'daki yuvarlamayla) parametre parçalarındakiyle aynı mı."""
    P = load_params()
    A = V["Ankara 21.10.2026"]
    E = V["EPDK Eylül 2026 İst. Avrupa ort."]
    K = V["km_weighted"][V["km_year"]]
    F = "access.fuel_price_tl_per_l"
    L = "access.fuel_l_per_100km"
    C = "access.cost_tl_per_km"
    T = "access.value_of_time_tl_per_h"
    S = "access.speed_kmh"
    checks = [
        (F, None, A["p_eff"], 2), (F, "Filo payı ağırlıklı (Σ s_f·p_f", A["p_share"], 2),
        (F, "(İstanbul Avrupa), litre ağırlıklı", E["p_eff"], 2),
        (F, "(İstanbul Avrupa), filo payı ağırlıklı", E["p_share"], 2),
        (F, "Yalnız benzin", V["p"]["benzin"], 2), (F, "Yalnız motorin", V["p"]["motorin"], 2),
        (F, "Yalnız LPG", V["p"]["lpg"], 2), (F, "Tablo 8", K["p_eff"], 2),
        (L, None, A["q_avg"], 2), (L, "Benzinli", V["q"]["benzin"], 1), (L, "Dizel", V["q"]["motorin"], 1),
        (L, "LPG'li", V["q"]["lpg"], 1), (L, "Tablo 8", K["q_avg"], 2),
        (C, None, A["c"], 2), (C, "Filo payı ağırlıklı fiyat × ortalama tüketim", A["p_share"] * A["q_avg"] / 100, 2),
        (C, "EPDK Eylül 2026", E["c"], 2), (C, "KGM", V["kgm"], 2), (C, "Tablo 8", K["c"], 2),
        (T, None, V["vot"], 1), (T, "(f = 1)", V["w_h"], 1), (T, "ortalama saatlik brüt ücret", V["vot_avg_wage"], 1),
        (S, None, V["speed"], 1), (S, "Yoğun saat", V["peak"], 1), (S, "Akşam yoğun", V["evening"], 1),
        (S, "Serbest akış", V["free_flow"], 1),
        ("opex.per_bed_year", None, V["opex_2026"], -3), ("opex.per_bed_year", "2024 fiyatlarıyla", V["opex_2024"], -3),
        ("land.price_base_tl_m2", "Evren", V["evren"], 0),
        ("land.price_base_tl_m2", "Temelli", V["land"]["Sincan Temelli/Alcı (kaba)"], 0),
        ("land.price_base_tl_m2", "Susuz", V["land"]["Yenimahalle Susuz (kentsel servis alanı)"], 0),
    ]
    bad = []
    for key, alt, derived, nd in checks:
        try:
            have = yaml_value(P, key, alt)
        except KeyError as e:
            bad.append(str(e))
            continue
        want = round(derived, nd)
        if not math.isclose(float(have), want, rel_tol=0, abs_tol=1e-9):
            bad.append(f"{key}{' [' + alt + ']' if alt else ''}: YAML {have} ≠ türetilen {want} ({derived:.6g})")
    print(f"--- YAML denetimi: {len(checks)} değer, {len(bad)} uyuşmazlık")
    for b in bad:
        print("  UYUŞMAZLIK:", b)
    return bad


# ---------------------------------------------------------------- ana akış

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--offline", action="store_true", help="hiç indirme denemez")
    ap.add_argument("--fetch", nargs=2, metavar=("URL", "DOSYA_ADI"), help="tek belgeyi arşivler ve çıkar")
    ap.add_argument("--xhr", action="store_true", help="--fetch: X-Requested-With başlığı")
    ap.add_argument("--cacert", help="--fetch: CA dosyası")
    ap.add_argument("--post", help="--fetch: JSON gövdeli POST")
    a = ap.parse_args(argv)

    if a.fetch:
        url, name = a.fetch
        path = ensure(name, url, offline=a.offline, xhr=a.xhr, cacert=a.cacert, post=a.post)
        print(path, path.stat().st_size, common.sha256(path))
        return 0

    for name in DOWNLOADS:
        ensure(name, offline=a.offline)
    used = [F_FLEET, F_BIRGUN_21, F_BIRGUN_20, F_YENIANKARA_21, F_EPDK, F_YALILI, F_CPI, F_BAGDATLI, F_LABOUR_LAW,
            F_KYA, F_TOMTOM, F_KALAYCI, F_HEALTH_EXP, F_TOKI, F_EVREN]
    for w in verify_archive(used):
        print("UYARI:", w, file=sys.stderr)

    V, lines = derive()
    print("\n".join(lines))
    return 1 if check_yaml(V) else 0


if __name__ == "__main__":
    sys.exit(main())
