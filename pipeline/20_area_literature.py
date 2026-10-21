"""Faz 1 — alan-literatur: Ankara yüzölçümü kaynakları ve kaynakça doğrulaması.

Çıktılar (repo içinde):
  data/reference/ankara_alan_kaynaklari.csv   — kaynaklardaki yüzölçümü değerleri (birebir alıntı + sayfa)
  data/reference/literatur.yaml               — Crossref/Zenodo/JRC ile doğrulanmış kaynakça
  data/sources/alan_literatur.yaml            — bu alanın kaynak kayıtları (elle düzenlemeyin; bu betik üretir)
Ham belgeler: data/raw/alan/, data/raw/literatur/ (git dışı; _manifest.jsonl'de url/tarih/sha256).

Çalıştırma (repo kökünden):
  .venv/bin/python pipeline/20_area_literature.py
Arşivde bulunan dosyalar yeniden indirilmez: ham belgeler varsa betik çevrimdışı çalışır ve çıktıları
yeniden üretir. İndirilen dosyalar yalnız okunur (PDF metni, JSON/CSV/YAML); hiçbiri çalıştırılmaz.

TLS notu: webdosya.csb.gov.tr ara sertifikayı göndermiyor. Yaprak sertifikanın AIA adresi
(http://crt.sectigo.com/…) vekil sunucuda engelli (403/405). Ara sertifika (SSL2BUY EMEA RSA Organization
Validation Secure Server CA) repoda: pipeline/certs/ssl2buy_emea_rsa_ov_ca.pem. Kaynağı CCADB kaynaklı açık
yansılar (TLS_SOURCES; arşiv data/raw/alan/_tls/). Her çalıştırmada SHA-256 parmak izi CCADB değeriyle
(SSL2BUY_OV_SHA256) karşılaştırılır, kök (Sectigo Public Server Authentication Root R46) ile imzası
`openssl verify` ile temel CA dosyasına karşı doğrulanır; birleşik CA dosyası (temel CA + ara sertifika) yalnız
indirme gerektiğinde geçici bir dosyada kurulur ve REQUESTS_CA_BUNDLE ile verilir. TLS doğrulaması hiçbir yerde
kapatılmaz.
"""
from __future__ import annotations

import base64
import contextlib
import csv
import datetime as dt
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline import common  # noqa: E402

CERTS = common.ROOT / "pipeline" / "certs"
CSB_INTERMEDIATE = CERTS / "ssl2buy_emea_rsa_ov_ca.pem"
RAW_ALAN = common.RAW / "alan"
RAW_LIT = common.RAW / "literatur"
OUT_CSV = common.REFERENCE / "ankara_alan_kaynaklari.csv"
OUT_LIT = common.REFERENCE / "literatur.yaml"
OUT_SRC = common.SOURCES_DIR / "alan_literatur.yaml"
ACCESSED = dt.date(2026, 10, 21)

# CCADB (Mozilla) kaydındaki parmak izi — librehs/Intermediate-CA-Monitor 2024-08 farkında yayımlanmış
SSL2BUY_OV_SHA256 = "F7AD61C4F2A74D1BE7C0580A36B2C93DFD5E8BC15886071C25F4E66DC15BBBF8"
SSL2BUY_OV_SKI = "cb8a38645583a31b5f34a042f9dbd6b88cdaeb45"
TLS_SOURCES = {
    "domainscope_ski_cb.json":
        "https://raw.githubusercontent.com/halilibrahimd27/domainscope/main/assets/data/intermediates/ski/cb.json",
    "domainscope_manifest.json":
        "https://raw.githubusercontent.com/halilibrahimd27/domainscope/main/assets/data/intermediates/manifest.json",
    "ccadb_monitor_2024-08-ica.diff":
        "https://raw.githubusercontent.com/librehs/Intermediate-CA-Monitor/master/data/2024/2024-08-ica.diff",
}

CSB = "https://webdosya.csb.gov.tr/db/"
AREA_DOCS = {
    "csb_ref4_altindag_plan_20210524152714.pdf": CSB + "ankara/duyurular/plan-dosyasi-20210524152714.pdf",
    "csb_ref5_beypazari_macun_plan_20251117081655.pdf": CSB + "ankara/duyurular/plan-dosyasi-20251117081655.pdf",
    "csb_ref6_cankaya_plan_aciklama_20200624102357.pdf": CSB + "ankara/duyurular/plan-aciklama-raporu-20200624102357.pdf",
    "csb_plan_20250702092636.pdf": CSB + "ankara/duyurular/plan-dosyasi-20250702092636.pdf",
    "csb_plan_20201225092342.pdf": CSB + "ankara/duyurular/plan-dosyasi-20201225092342.pdf",
    "csb_valilik_cdr_2018.pdf": CSB + "ced/icerikler/ankara_2018_-cdr_son-20190903153406.pdf",
    "csb_valilik_cdr_2017.pdf": CSB + "ankara/menu/ankara-ili-2017-cevre-durum-raporu-5_20190225023932.pdf",
}
SECONDARY = {  # HGM tablosunun ikincil kopyaları (harita.gov.tr vekil sunucuda engelli)
    "ikincil/github_snrylmz_il-ilce.json": "https://raw.githubusercontent.com/snrylmz/il-ilce-json/master/js/il-ilce.json",
    "ikincil/github_snrylmz_README.md": "https://raw.githubusercontent.com/snrylmz/il-ilce-json/master/README.md",
    "ikincil/github_brkunver_provinces.csv":
        "https://raw.githubusercontent.com/brkunver/turkiye-provinces-dataset/main/data/provinces.csv",
    "ikincil/github_brkunver_sources.json":
        "https://raw.githubusercontent.com/brkunver/turkiye-provinces-dataset/main/metadata/sources.json",
    "ikincil/github_brkunver_DATA_SOURCES.md":
        "https://raw.githubusercontent.com/brkunver/turkiye-provinces-dataset/main/DATA_SOURCES.md",
}

# Diğer alanların arşivleri (yalnız okunur)
TUIK_2025_XLS = common.RAW / "nufus/tuik/bt_adnks2025_il_cinsiyet_nufus_yogunluk.xls"
TUIK_2023_XLS = common.RAW / "nufus/tuik/bt_adnks2023_il_cinsiyet_nufus_yogunluk.xls"
TUIK_2025_JSON = common.RAW / "nufus/tuik/veriportali_press_53899_adnks2025.json"
CODAB_ZIP = common.RAW / "sinirlar/hdx_codab/tur_admin_boundaries.geojson.zip"


# ----------------------------------------------------------------------------- yardımcılar

def manifest_lookup(folder: Path) -> dict:
    """_manifest.jsonl içindeki SON kaydı dosya adına göre döndürür."""
    out = {}
    mf = folder / "_manifest.jsonl"
    if mf.exists():
        for line in mf.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                out[rec["file"]] = rec
    return out


def fetch(url: str, dest: Path, bundle: Path | None = None) -> None:
    """Dosya yoksa common.download ile indirir (manifest kaydı yazılır); varsa dokunmaz (çevrimdışı çalışma)."""
    if dest.exists():
        return
    old = os.environ.get("REQUESTS_CA_BUNDLE")
    if bundle is not None:
        os.environ["REQUESTS_CA_BUNDLE"] = str(bundle)
    try:
        rec = common.download(url, dest, timeout=180)
        print("indirildi:", rec["file"], rec["bytes"])
    except Exception as e:  # noqa: BLE001
        print("HATA:", dest.name, type(e).__name__, e)
    finally:
        if old is None:
            os.environ.pop("REQUESTS_CA_BUNDLE", None)
        else:
            os.environ["REQUESTS_CA_BUNDLE"] = old


def rel(p: Path) -> str:
    return str(p.relative_to(common.ROOT))


# ----------------------------------------------------------------------------- 1. TLS

def base_ca_bundle() -> Path:
    """Temel CA dosyası: ortamın REQUESTS_CA_BUNDLE / SSL_CERT_FILE değeri, yoksa certifi'ninki."""
    for var in ("REQUESTS_CA_BUNDLE", "SSL_CERT_FILE"):
        v = os.environ.get(var)
        if v and Path(v).is_file():
            return Path(v)
    import certifi

    return Path(certifi.where())


def _openssl(*args: str) -> str:
    return subprocess.run(["openssl", *args], capture_output=True, text=True, check=True).stdout


def verify_csb_intermediate() -> dict:
    """Repodaki ara sertifikayı doğrular: SHA-256 = CCADB değeri, arşivlenen CCADB yansılarıyla aynı, köke zincirli."""
    pem = CSB_INTERMEDIATE.read_text(encoding="ascii")
    der = base64.b64decode("".join(ln for ln in pem.splitlines() if ln and not ln.startswith("-----")))
    fp = hashlib.sha256(der).hexdigest().upper()
    if fp != SSL2BUY_OV_SHA256:
        raise RuntimeError(f"ara sertifika parmak izi uyuşmuyor: {fp}")
    fp_ossl = _openssl("x509", "-in", str(CSB_INTERMEDIATE), "-noout", "-fingerprint", "-sha256")
    if fp_ossl.split("=", 1)[1].strip().replace(":", "") != fp:
        raise RuntimeError("openssl parmak izi Python hesabıyla uyuşmuyor")
    tls_raw = RAW_ALAN / "_tls"
    for name, url in TLS_SOURCES.items():
        fetch(url, tls_raw / name)
    shard_p, diff_p = tls_raw / "domainscope_ski_cb.json", tls_raw / "ccadb_monitor_2024-08-ica.diff"
    if shard_p.exists():
        shard = json.loads(shard_p.read_text(encoding="utf-8"))
        if base64.b64decode(shard[SSL2BUY_OV_SKI][0]["der"]) != der:
            raise RuntimeError("ara sertifika CCADB yansısındaki DER ile aynı değil")
    else:
        print("UYARI: CCADB yansısı arşivde yok; DER karşılaştırması atlandı")
    if diff_p.exists():
        if SSL2BUY_OV_SHA256 not in diff_p.read_text(encoding="utf-8", errors="replace"):
            raise RuntimeError("CCADB farkında parmak izi bulunamadı")
    else:
        print("UYARI: CCADB farkı arşivde yok; parmak izi yalnız sabit değerle karşılaştırıldı")
    ver = subprocess.run(["openssl", "verify", "-CAfile", str(base_ca_bundle()), str(CSB_INTERMEDIATE)],
                         capture_output=True, text=True)
    if ver.returncode != 0:
        raise RuntimeError(f"openssl verify başarısız: {ver.stderr or ver.stdout}")
    subj = _openssl("x509", "-in", str(CSB_INTERMEDIATE), "-noout", "-subject", "-issuer", "-dates")
    return {"sha256": fp, "subject": subj.strip()}


@contextlib.contextmanager
def csb_bundle():
    """Geçici birleşik CA dosyası (temel CA + ara sertifika); yalnız webdosya.csb.gov.tr indirmesi sırasında."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "ca-bundle-plus-csb.crt"
        base = base_ca_bundle().read_text(encoding="utf-8", errors="replace")
        p.write_text(base.rstrip("\n") + "\n" + CSB_INTERMEDIATE.read_text(encoding="ascii"), encoding="utf-8")
        yield p


# ----------------------------------------------------------------------------- 2. alan belgeleri

def page_text(name: str, page: int) -> str:
    import pdfplumber

    with pdfplumber.open(RAW_ALAN / name) as pdf:
        t = pdf.pages[page - 1].extract_text() or ""
    return re.sub(r"\s+", " ", t.replace("\n", " ")).strip()


def quote(name: str, page: int, pattern: str) -> str:
    """Sayfa metninde deseni arar; eşleşen metni birebir (boşluklar normalize) döndürür."""
    t = page_text(name, page)
    m = re.search(pattern, t)
    if not m:
        raise ValueError(f"{name} s.{page}: desen yok: {pattern}")
    return m.group(0).strip()


def tuik_row(xls: Path) -> tuple[float, float]:
    import xlrd

    sh = xlrd.open_workbook(str(xls)).sheet_by_index(0)
    for r in range(sh.nrows):
        row = sh.row_values(r)
        if str(row[0]).strip() == "Ankara":
            vals = [x for x in row if x != ""]
            return float(vals[1]), float(vals[-1])
    raise ValueError("Ankara satırı yok")


def tuik_metadata_sentence() -> str:
    raw = html.unescape(TUIK_2025_JSON.read_text(encoding="utf-8"))
    raw = re.sub(r"<[^>]+>|\\n", " ", raw)
    raw = re.sub(r"\s+", " ", raw)
    m = re.search(r"Harita Genel Komutanlığı.{0,80}?1/1\.000\.000.*?769\.604 km2.dir\.", raw)
    return m.group(0)


def codab_area() -> float:
    with zipfile.ZipFile(CODAB_ZIP) as z:
        gj = json.loads(z.read("tur_admin1.geojson"))
    for f in gj["features"]:
        if f["properties"].get("adm1_pcode") == "TUR006":
            return float(f["properties"]["area_sqkm"])
    raise ValueError("TUR006 yok")


def build_area_rows() -> list[dict]:
    rows = []

    def add(label, value_km2, scope, sid, excerpt, page, status, note=""):
        # CSV sütun adları Türkçe (veri sözlüğü): kaynak, deger_km2, kapsam, alinti, sayfa, notlar
        rows.append({"kaynak": label, "deger_km2": value_km2, "kapsam": scope, "source_id": sid,
                     "alinti": excerpt, "sayfa": page, "status": status, "notlar": note})

    f4 = "csb_ref4_altindag_plan_20210524152714.pdf"
    add("ÇŞB Ankara İl Müd. ilanı — Altındağ Gümüşdere 19734 ada 9 parsel 1/5000 NİP değişikliği açıklama raporu (Ankara, 2020) [spec 4]",
        24521, "göller hariç ('Göller dışında')", "alan_literatur_csb_ref4_altindag",
        quote(f4, 2, r"Göller dışında 24\.521 km²’lik yüzölçümü ile Türkiye yüzölçümü içerisinde % 3,19’luk bir paya sahiptir\."),
        "PDF s. 2 (aynı metin s. 32)", "kaynakli",
        "24.521 / 769.604 (TÜİK metaverisindeki Türkiye, göller hariç) = %3,186 → metindeki %3,19 ile tutarlı (hesap).")
    add("ÇŞB Ankara İl Müd. ilanı — Altındağ plan açıklama raporu [spec 4]",
        25706, "belirtilmemiş (aynı sayfada 'göller dışında 24.521' ile birlikte)", "alan_literatur_csb_ref4_altindag",
        quote(f4, 2, r"Ankara ilinin yüz ölçümü 25\.706 km² 'dir\."), "PDF s. 2 (aynı metin s. 32)", "kaynakli",
        "Göller dahil olduğu kaynakta yazmıyor; 25.706 − 24.521 = 1.185 km² (hesap).")
    f5 = "csb_ref5_beypazari_macun_plan_20251117081655.pdf"
    add("ÇŞB Ankara İl Müd. ilanı — Beypazarı Macun 116 ada 2-3 parsel GES amaçlı 1/5000 NİP açıklama raporu (2025) [spec 5]",
        25632, "belirtilmemiş", "alan_literatur_csb_ref5_beypazari",
        quote(f5, 3, r"25\.632 km2 lik yüzölçümü ile Türkiye’nin üçüncü büyük ili konumundadır\."),
        "PDF s. 3 (basılı s. 1; aynı metin PDF s. 41)", "kaynakli",
        "25.632 km²'nin birincil dayanağı: resmî belgede birebir yazıyor. Valilik ÇDR 2018 Çizelge E.85'teki dört arazi "
        "sınıfı da tam 25.632.000 da = 25.632 km²'ye toplanıyor (aşağıdaki satır). İkincil kopyalara göre HGM ile aynı "
        "(bkz. HGM satırları; HGM tablosu doğrudan okunamadı).")
    f6 = "csb_ref6_cankaya_plan_aciklama_20200624102357.pdf"
    add("ÇŞB Ankara İl Müd. ilanı — Çankaya Yukarı Bahçelievler 7289 ada 5 parsel (Milli Kütüphane) 1/1000 UİP değişikliği açıklama raporu (2020) [spec 6]",
        25978, "belirtilmemiş ('Ankara şehrinin' deniyor)", "alan_literatur_csb_ref6_cankaya",
        quote(f6, 3, r"Yüzölçümü 25\.978 kilometrekare olan Ankara şehrinin merkezinin önemli bir bölümünün üzerinde kurulu olduğu Ankara Ovası’nın denizden yüksekliği 830 metre ile 850 metre civarındadır\."),
        "PDF s. 3 (basılı s. 3)", "kaynakli", "Kaynak göstermiyor; başka hiçbir belgede bu değere rastlanmadı.")
    fp = "csb_plan_20250702092636.pdf"
    add("ÇŞB Ankara İl Müd. ilanı — Polatlı Zafer Mah. 2970 ada 4 ve 7 parsel 1/5000 NİP açıklama raporu (Haziran 2025)",
        26897, "belirtilmemiş", "alan_literatur_csb_polatli_2025",
        quote(fp, 3, r"İlin yüzölçümü 26\.897 km2, rakımı 890 metredir\."), "PDF s. 3 (aynı metin s. 31)", "kaynakli",
        "Kaynak göstermiyor. Valilik ÇDR 2018'deki 'Ankara İlinin Bioçeşitlilik Atlası' alanıyla (26.897 km²) aynı sayı.")
    fk = "csb_plan_20201225092342.pdf"
    add("ÇŞB Ankara İl Müd. ilanı — Çankaya Karakusunlar 13911 ada 11 ve 13 parsel 1/5000 NİP değişikliği açıklama raporu (2020)",
        24521, "göller hariç ('Göller dışında')", "alan_literatur_csb_karakusunlar_2020",
        quote(fk, 2, r"Göller dışında 24\.521 km ²'lik yüzölçümü ile Türkiye yüzölçümü içerisinde % 3,19'luk bir paya sahiptir\."),
        "PDF s. 2 (aynı metin s. 14)", "kaynakli", "[4] ile aynı cümle: plan raporları bu paragrafı birbirinden kopyalıyor.")
    fc = "csb_valilik_cdr_2018.pdf"
    q85 = quote(fc, 172, r"Ankara ilimizin toplam alanı içerisinde %46 lik dilimle 11\.839\.649 da lık bir alan tarım alanı olarak kullanılmaktadır\. Çayır-mera alanı 4\.124\.040 da alanla %16 lük dilimi oluştururken, 4\.412\.420 da alan ile ormanlık ve diğer alanlar %17 lik dilimi, yerleşim yerleri ve diğer alanlar olarak 5\.255\.891 da \(%21\)’lik oluşturmaktadır\.")
    tot_da = 11_839_649 + 4_124_040 + 4_412_420 + 5_255_891
    add("Ankara Valiliği Çevre ve Şehircilik İl Müd. — Ankara İli 2018 Yılı Çevre Durum Raporu, Çizelge E.85 (Tarım ve Orman İl Müd., 2018)",
        tot_da / 1000, "arazi kullanım sınıflarının toplamı (tarım + çayır-mera + orman/diğer + yerleşim/diğer); göl ayrımı yok",
        "alan_literatur_valilik_cdr_2018", q85, "PDF s. 172 (basılı s. 159); tablo: Çizelge E.85, PDF s. 171 (basılı s. 158)", "hesap",
        f"Toplam = 11.839.649 + 4.124.040 + 4.412.420 + 5.255.891 = {common.fmt_tr(tot_da)} da = {common.fmt_tr(tot_da/1000, 3)} km² (1 km² = 1.000 da). "
        "Çizelgede toplam satırı yok, toplama bizim hesabımız. [5]'te birebir yazan 25.632 km² ile aynı: bu değerin ikinci resmî "
        "dayanağı. İkincil kopyalara göre HGM ile aynı.")
    q86 = quote(fc, 171, r"TOPLAM 2\.547\.625,24 100,00 2\.547\.624,79 100,00 2\.565\.474,80 100,00 2\.565\.474,8 100,00")
    add("Ankara Valiliği ÇDR 2018, Çizelge E.86 — CORINE arazi sınıflandırması (Tarım ve Orman Bak., Corine) — 1990",
        round(2_547_625.24 / 100, 4), "CORINE poligonları toplamı (su yapıları ve sulak alanlar dahil)",
        "alan_literatur_valilik_cdr_2018", q86, "PDF s. 171 (basılı s. 158), Çizelge E.86, sütun 1990", "hesap",
        "2.547.625,24 ha ÷ 100 (birim dönüşümü); 2000: 2.547.624,79 ha. Aynı tablo ÇDR 2017 Çizelge E.44'te (PDF s. 152).")
    add("Ankara Valiliği ÇDR 2018, Çizelge E.86 — CORINE arazi sınıflandırması — 2006 ve 2012",
        round(2_565_474.80 / 100, 4), "CORINE poligonları toplamı (su yapıları ve sulak alanlar dahil)",
        "alan_literatur_valilik_cdr_2018", q86, "PDF s. 171 (basılı s. 158), Çizelge E.86, sütun 2006/2012", "hesap",
        "2.565.474,80 ha ÷ 100 (birim dönüşümü). CORINE 2012: su yapıları 65.267,11 ha + sulak alanlar 25.865,77 ha.")
    add("Ankara Valiliği ÇDR 2018 — 'Ankara İlinin Bioçeşitlilik Atlası' çalışma alanı",
        26897, "atlasın kapsadığı alan (il yüzölçümü olarak verilmemiş)", "alan_literatur_valilik_cdr_2018",
        quote(fc, 144, r"Proje ekibi tarafından gerçekleştirilen 180 günlük arazi çalışması ile 26\.897 km²’lik “Ankara İlinin Bioçeşitlilik Atlası” hazırlanmıştır\."),
        "PDF s. 144 (basılı s. 131)", "kaynakli", "İl yüzölçümü olarak verilmemiş; Polatlı 2025 raporundaki 'ilin yüzölçümü 26.897 km2' ile aynı sayı.")
    # TÜİK — yoğunluktan örtük alan (nufus alanının arşivi)
    meta = tuik_metadata_sentence()
    p25, d25 = tuik_row(TUIK_2025_XLS)
    p23, d23 = tuik_row(TUIK_2023_XLS)
    add("TÜİK ADNKS 2023 bülten tablosu — nüfus / nüfus yoğunluğu (örtük alan)",
        round(p23 / d23, 2), "doğal göller ve baraj gölleri hariç (TÜİK metaverisi; HGK 1/1.000.000 harita)",
        "tuik_bt_adnks2023_yogunluk",
        f"Ankara | {int(p23)} | … | {d23!r}  ·  Metaveri: {meta}",
        "Sayfa 't3', satır 11 (nüfus sütun B, yoğunluk son sütun); metaveri: veriportali press 53899 JSON", "hesap",
        f"{common.fmt_tr(p23)} / {common.fmt_tr(d23, 4)} = {common.fmt_tr(p23/d23, 2)} km². Spec [4]'teki 'göller hariç 24.521' ile birebir.")
    add("TÜİK ADNKS 2025 (ve 2024) bülten tablosu — nüfus / nüfus yoğunluğu (örtük alan)",
        round(p25 / d25, 2), "doğal göller ve baraj gölleri hariç (TÜİK metaverisi; HGK 1/1.000.000 harita)",
        "tuik_bt_adnks2025_yogunluk",
        f"Ankara | {int(p25)} | … | {d25!r}  ·  Metaveri: {meta}",
        "Sayfa 't3', satır 11; metaveri: veriportali press 53899 JSON", "hesap",
        f"{common.fmt_tr(p25)} / {common.fmt_tr(d25, 4)} = {common.fmt_tr(p25/d25, 2)} km². TÜİK 2024 bülteninden itibaren yeni alan "
        "esası (+321,6 km²); TÜİK değişikliği açıklamıyor (bkz. nufus alanı notları).")
    ca = codab_area()
    add("HDX COD-AB Türkiye (kaynak: Harita Genel Komutanlığı, 1:1.000.000) — tur_admin1 'area_sqkm' özniteliği",
        round(ca, 3), "idari sınır poligonu alanı (göl ayrımı yok; öznitelik OCHA/UNHCR tarafından poligondan üretilmiş olabilir)",
        "sinirlar_hdx_codab_tur",
        f"adm1_pcode TUR006 (Ankara): area_sqkm = {ca}", "tur_admin1.geojson, öznitelik", "kaynakli",
        "Resmî bir yüzölçümü tablosu değil, HGK 1:1.000.000 sınırından türeyen veri özniteliği; [5] ve Valilik ÇDR 2018 "
        "Çizelge E.85'teki 25.632 km² ile 0,24 km² farkla uyumlu (ikincil kopyalara göre HGM ile aynı).")
    sj = json.loads((RAW_ALAN / "ikincil/github_snrylmz_il-ilce.json").read_text(encoding="utf-8"))
    ank = next(x for x in sj["data"] if x["il_adi"] == "Ankara")
    readme = (RAW_ALAN / "ikincil/github_snrylmz_README.md").read_text(encoding="utf-8")
    rq = re.search(r"Yüzölçümü bilgileri \S+ adresinden alınmıştır", readme).group(0)
    add("HGM 'il_ilce_alanlari.pdf' — İKİNCİL kopya: GitHub snrylmz/il-ilce-json (js/il-ilce.json)",
        float(ank["yuzolcumu"].replace(".", "").replace(",", ".")), "belirtilmemiş (HGM tablosu)",
        "alan_literatur_hgm_ikincil_snrylmz", f"\"il_adi\": \"Ankara\", … \"yuzolcumu\": \"{ank['yuzolcumu']}\"  ·  README: {rq}",
        "js/il-ilce.json, Ankara kaydı", "kaynakli_ikincil",
        "harita.gov.tr vekil sunucuda engelli (403) → HGM tablosu doğrudan okunamadı. Yalnız 'ikincil kopyalara göre HGM ile "
        "aynı' notunun dayanağı; 25.632'nin birincil dayanağı resmî belgeler ([5], Valilik ÇDR 2018 Çizelge E.85).")
    bj = json.loads((RAW_ALAN / "ikincil/github_brkunver_sources.json").read_text(encoding="utf-8"))
    bnote = bj["Ankara"]["geo_admin"]["note"]
    with open(RAW_ALAN / "ikincil/github_brkunver_provinces.csv", encoding="utf-8") as f:
        brow = next(r for r in csv.DictReader(f) if r["province_name"] == "Ankara")
    add("HGM — İKİNCİL kopya: GitHub brkunver/turkiye-provinces-dataset (data/provinces.csv)",
        float(brow["area_km2"]), "belirtilmemiş (HGM)", "alan_literatur_hgm_ikincil_brkunver",
        f"area_km2 = {brow['area_km2']}  ·  metadata/sources.json: \"{bnote}\"", "data/provinces.csv, Ankara satırı",
        "kaynakli_ikincil", "DATA_SOURCES.md: " + re.search(r"Resmi yüzölçümleri; çelişkide HGM önceliklidir",
        (RAW_ALAN / "ikincil/github_brkunver_DATA_SOURCES.md").read_text(encoding="utf-8")).group(0) + ". "
        "Yalnız 'ikincil kopyalara göre HGM ile aynı' notunun dayanağı; birincil dayanak [5] ve Valilik ÇDR 2018 Çizelge E.85.")
    return rows


def write_csv(rows: list[dict]) -> None:
    cols = ["kaynak", "deger_km2", "kapsam", "source_id", "alinti", "sayfa", "status", "notlar"]
    with open(OUT_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            v = r["deger_km2"]
            w.writerow({**r, "deger_km2": int(v) if float(v).is_integer() else v})
    print("yazıldı:", rel(OUT_CSV), len(rows), "satır")


# ----------------------------------------------------------------------------- 3. kaynakça

# beklenen = görev metnindeki künye; doğrulama sonucu buna göre karşılaştırılır
LIT = [
    dict(id="hakimi1964", doi="10.1287/opre.12.3.450", expect=dict(journal="Operations Research", volume="12", issue="3", pages="450-459", year=1964),
         notes="p-medyan ve mutlak merkez probleminin klasik kaynağı."),
    dict(id="revelle_swain1970", doi="10.1111/j.1538-4632.1970.tb00142.x", expect=dict(journal="Geographical Analysis", volume="2", issue="1", pages="30-42", year=1970, title="Central facilities location"),
         notes="p-medyanın tamsayılı programlama formülasyonu."),
    dict(id="toregas1971", doi="10.1287/opre.19.6.1363", expect=dict(journal="Operations Research", volume="19", issue="6", pages="1363-1373", year=1971),
         notes="Küme kapsama (LSCP) problemi."),
    dict(id="church_revelle1974", doi="10.1007/BF01942293", expect=dict(journal="Papers of the Regional Science Association", volume="32", pages="101-118", year=1974),
         notes="Maksimal kapsama (MCLP) problemi."),
    dict(id="hill_burton1946", doi=None, expect={},
         notes="ABD Hospital Survey and Construction Act (Hill-Burton Act)."),
    dict(id="ravaghi2020", doi="10.1186/s12913-020-5023-z", expect={},
         notes="Yatak ihtiyacı modellerinin sistematik taraması (Hill-Burton formülünün girdileri için)."),
    dict(id="boscoe2012", doi="10.1080/00330124.2011.583586", expect=dict(year=2012),
         notes="Kuş uçuşu ve karayolu mesafesi karşılaştırması (dolambaç katsayısı)."),
    dict(id="ballou2002", doi="10.1016/S0965-8564(01)00044-1", expect=dict(year=2002),
         notes="Ülke bazında dolambaç (circuity) katsayıları."),
    dict(id="daskin2013", doi="10.1002/9781118537015", expect=dict(publisher="Wiley", year=2013),
         notes="Ders kitabı: ağ ve kesikli yerleşim modelleri."),
    dict(id="ahmadi_javid2017", doi="10.1016/j.cor.2016.05.018", expect=dict(journal="European Journal of Operational Research", year=2017),
         notes="Sağlık tesisi yerleşimi literatür taraması."),
    dict(id="huangfu_hall2018", doi="10.1007/s12532-017-0130-5", expect=dict(journal="Mathematical Programming Computation", year=2018),
         notes="HiGHS'in ikili (dual) simpleks çekirdeği; HiGHS'in önerdiği atıf."),
    dict(id="schiavina2023_ghs_pop", doi="10.2905/2FF68A52-5B5B-4A22-8F40-C41DA8332CFE", expect=dict(year=2023),
         notes="Nüfus ızgarası (ana kaynak)."),
    dict(id="pesaresi2024", doi="10.1080/17538947.2024.2390454", expect=dict(journal="International Journal of Digital Earth", year=2024),
         notes="GHSL yöntem makalesi."),
    dict(id="runfola2020", doi="10.1371/journal.pone.0231866", expect=dict(journal="PLOS ONE", year=2020),
         notes="geoBoundaries idari sınırlar (ana sınır kümesi)."),
    dict(id="zanaga2022", doi="10.5281/zenodo.7254221", expect=dict(year=2022),
         notes="ESA WorldCover 10 m 2021 v200 (su maskesi)."),
]


def cr_path(eid: str) -> Path:
    return RAW_LIT / f"crossref_{eid}.json"


# Alanlar arası yinelenen kayıtlar bu alanın kimliklerinde birleştirildi (arazi_osm kayıtları kaldırıldı).
# Her biri için: eski kimlik, diğer alanın aynı (sha256 eşit) arşiv kopyası, eklenen kullanım ve not.
MERGED = {
    "zanaga2022": dict(
        old_id="arazi_osm_worldcover_2021_v200", copy=common.RAW / "arazi" / "zenodo_7254221_record.json",
        script="pipeline/13_landcover.py", users="arazi_osm alanı (WorldCover su maskesi)",
        used_for=["Arazi örtüsü sınıfları 80 (kalıcı su), 90 (otsu sulak alan), 50 (yapılaşmış alan) — aday hücre su "
                  "filtresi (karolar: arazi_osm_worldcover_tiles_s3; pipeline/13_landcover.py)"],
        notes=("Kayıt açıklaması: 2020 (v100) ve 2021 (v200) haritaları farklı algoritma sürümleriyle üretildiği için "
               "yıllar arası farklar dikkatle yorumlanmalı. Lisans karo GeoTIFF etiketlerinde de yazıyor (\"CC-BY 4.0\"). "
               "Gösterimde atıf: \"© ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021) "
               "processed by ESA WorldCover consortium\" (karo 'copyright' etiketi). Konsorsiyum: VITO, Brockmann Consult, "
               "Gamma Remote Sensing, IIASA, Wageningen Üniv.")),
    "boscoe2012": dict(
        old_id="arazi_osm_boscoe_2012", copy=common.RAW / "osm" / "crossref_boscoe_2012.json",
        script="pipeline/15_circuity.py", users="arazi_osm alanı ve access.circuity notu",
        used_for=["Kaynakça: hastaneye sürüş mesafesi ile kuş uçuşu mesafe karşılaştırması (dolambaç katsayısı "
                  "literatürü; access.circuity notu)"],
        notes=("Denenen tam metin adresleri pmc.ncbi.nlm.nih.gov (PMC3835347), www.ebi.ac.uk (Europe PMC), api.openalex.org, "
               "api.semanticscholar.org vekil tarafından reddedildi (403); doi.org yönlendirmesi http://www.tandfonline.com "
               "çözümlenemedi. Bu nedenle makalenin raporladığı oran/katsayı DEĞERİ KAYDEDİLMEDİ (uydurma yok).")),
    "ballou2002": dict(
        old_id="arazi_osm_ballou_2002", copy=common.RAW / "osm" / "crossref_ballou_2002.json",
        script="pipeline/15_circuity.py", users="arazi_osm alanı ve access.circuity notu",
        used_for=["Kaynakça: ülke bazında dolambaç katsayıları (circuity factor) kavramı (access.circuity notu)"],
        notes=("Tam metin açık erişimde bulunamadı; linkinghub.elsevier.com, arxiv.org (ikincil kaynak olarak Boeing 2019), "
               "dspace.mit.edu, hcup-us.ahrq.gov vekil tarafından reddedildi (403). Katsayı DEĞERİ KAYDEDİLMEDİ.")),
}


def merge_duplicate(rec: dict, eid: str, path: Path) -> dict:
    """Birleştirilen kayda diğer alanın kullanımını, notunu ve yayım tarihini ekler; arşiv kopyasının aynılığını denetler."""
    m = MERGED[eid]
    if path.name.startswith("crossref_"):
        pub = _date(json.loads(path.read_text(encoding="utf-8"))["message"], "published-print")
    else:
        pub = dt.date.fromisoformat(json.loads(path.read_text(encoding="utf-8"))["metadata"]["publication_date"])
    copy_note = ""
    if m["copy"].exists():
        same = common.sha256(m["copy"]) == rec["sha256"]
        print(f"  birleştirme {m['old_id']} → {rec['id']}: {rel(m['copy'])} sha256 {'aynı' if same else 'FARKLI'}")
        if not same:
            raise RuntimeError(f"{rel(m['copy'])} arşivi {rec['archived']} ile aynı değil")
        copy_note = f" Aynı dosya {m['script']} tarafından {rel(m['copy'])} olarak da arşivlendi (sha256 aynı)."
    out = {}
    for k, v in rec.items():   # şema sırası: published, accessed'ten hemen sonra
        out[k] = v
        if k == "accessed":
            out["published"] = pub
    out["used_for"] = rec["used_for"] + m["used_for"]
    out["notes"] = (f"{rec['notes']} {m['notes']}{copy_note} Alanlar arası yinelenen kayıt {m['old_id']} bu kimlikte "
                    f"birleştirildi ({m['users']} bu kimliği kullanır).")
    return out


def fetch_literature() -> None:
    for e in LIT:
        if e["doi"] and not e["doi"].startswith(("10.2905/", "10.5281/")):
            fetch("https://api.crossref.org/works/" + e["doi"], cr_path(e["id"]))
    # DataCite DOI'leri: handle kaydı (doi.org) + yayıncı üst verisi
    fetch("https://doi.org/api/handles/10.2905/2FF68A52-5B5B-4A22-8F40-C41DA8332CFE", RAW_LIT / "doi_handle_ghs_pop_r2023a.json")
    fetch("https://doi.org/api/handles/10.2905/JRC.CXKEDRR", RAW_LIT / "doi_handle_ghs_pop_jrc_cxkedrr.json")
    fetch("https://doi.org/api/handles/10.5281/zenodo.7254221", RAW_LIT / "doi_handle_worldcover_v200.json")
    fetch("https://data.jrc.ec.europa.eu/dataset/2ff68a52-5b5b-4a22-8f40-c41da8332cfe.rdf", RAW_LIT / "jrc_ghs_pop_r2023a.rdf")
    fetch("https://zenodo.org/api/records/7254221", RAW_LIT / "zenodo_7254221_worldcover_v200.json")
    # Hill-Burton: Statutes at Large künyesi + ABD Kanunu notu (resmî metnin GitHub yansıları)
    fetch("https://raw.githubusercontent.com/unitedstates/legisworks-historical-statutes/master/data/060.yaml",
          RAW_LIT / "ikincil/legisworks_statutes_060.yaml")
    fetch("https://raw.githubusercontent.com/TheJoshuaEvans/united-states-code/main/usc/42/1592c.xml",
          RAW_LIT / "ikincil/uscode_42_1592c.xml")


def _people(lst) -> list[str]:
    out = []
    for a in lst or []:
        if a.get("family"):
            out.append(f"{a['family']}, {a.get('given', '')}".strip().rstrip(","))
        elif a.get("name"):
            out.append(a["name"])
    return out


def _year(m: dict) -> int:
    for k in ("published-print", "journal-issue", "issued", "published-online", "published"):
        v = m.get(k)
        if isinstance(v, dict) and k == "journal-issue":
            v = v.get("published-print")
        if v and v.get("date-parts") and v["date-parts"][0] and v["date-parts"][0][0]:
            return int(v["date-parts"][0][0])
    raise ValueError("yıl yok")


def _date(m: dict, key: str) -> str | None:
    v = m.get(key)
    if v and v.get("date-parts") and v["date-parts"][0][0]:
        return "-".join(f"{x:02d}" if i else str(x) for i, x in enumerate(v["date-parts"][0]))
    return None


def build_literature() -> tuple[list[dict], list[str]]:
    entries, issues = [], []
    for e in LIT:
        eid, doi, ex = e["id"], e["doi"], e["expect"]
        rec = {"id": eid}
        if eid == "hill_burton1946":
            sy = (RAW_LIT / "ikincil/legisworks_statutes_060.yaml").read_text(encoding="utf-8")
            m = re.search(r"- volume: 60\n  page: (\d+)\n  npages: (\d+)\n  file: (\S+)\n  congress: 79\n  type: publaw\n"
                          r"  number: 725\n  citation: ([^\n]+)\n  topic: ([^\n]+)\n  title: (.+?)\n- volume", sy, re.S)
            page, npages, pdf, cit, topic, title = m.groups()
            title = re.sub(r"\s+", " ", title)
            ux = (RAW_LIT / "ikincil/uscode_42_1592c.xml").read_text(encoding="utf-8")
            un = re.sub(r"<[^>]+>", "", re.search(r"<p[^>]*>Public Law 725, Seventy-ninth Congress.*?</p>", ux, re.S).group(0))
            rec.update(authors=["79th United States Congress"], year=1946,
                       title=f"{topic} — {title}",
                       journal=None, publisher="United States Statutes at Large, vol. 60",
                       volume="60", issue=None, pages=f"{page}–{int(page) + int(npages) - 1}", doi=None,
                       url=f"https://www.govinfo.gov/content/pkg/STATUTE-60/pdf/{pdf}",
                       citation=f"{cit} ({topic}), Aug. 13, 1946, ch. 958, 60 Stat. {page}",
                       verified_via=("unitedstates/legisworks-historical-statutes data/060.yaml (Statutes at Large künye dizini) + "
                                     "ABD Kanunu (OLRC) 42 U.S.C. 1592c 'References in Text' notunun GitHub yansısı; "
                                     "govinfo.gov vekil sunucuda engelli, kanun PDF'i okunamadı"),
                       notes=(f"{e['notes']} OLRC notu: \"{un.strip()}\" — dikkat: legisworks dizininde yasa 60 Stat. {page}'da "
                              f"başlıyor ({npages} sayfa, {pdf}); OLRC notları 60 Stat. 1041 yazıyor. Kanun metni okunamadığı "
                              "için yatak formülünü içerip içermediği DOĞRULANMADI."),
                       source_ids=["alan_literatur_lit_hill_burton1946"])
            entries.append(rec)
            continue
        if doi and doi.startswith("10.2905/"):
            rdf = (RAW_LIT / "jrc_ghs_pop_r2023a.rdf").read_text(encoding="utf-8")
            creators = re.search(r"<dc:creator[^>]*>(.*?)</dc:creator>", rdf).group(1)
            cit = re.search(r"<dct:bibliographicCitation[^>]*>(.*?)</dct:bibliographicCitation>", rdf).group(1)
            issued = re.search(r"<dct:issued[^>]*>(.*?)</dct:issued>", rdf).group(1)
            title = re.search(r"<dct:title[^>]*>(.*?)</dct:title>", rdf).group(1)
            hd = json.loads((RAW_LIT / "doi_handle_ghs_pop_r2023a.json").read_text())
            hurl = next(v["data"]["value"] for v in hd["values"] if v["type"] == "URL")
            hts = next(v["timestamp"] for v in hd["values"] if v["type"] == "URL")
            year = int(issued[:4])
            foaf = re.findall(r"<foaf:name[^>]*>(.*?)</foaf:name>", rdf)
            authors = []
            for a in (x.strip() for x in creators.split(";")):
                if "," not in a:  # 'Ad Soyad' -> katalogdaki 'Soyad, Ad' biçimi
                    a = next((f for f in foaf if all(t in f for t in a.split())), a)
                authors.append(a)
            rec.update(authors=authors, year=year, title=title,
                       journal=None, publisher="European Commission, Joint Research Centre (JRC) [Dataset]",
                       volume=None, issue=None, pages=None, doi=doi, url="https://data.jrc.ec.europa.eu/dataset/2ff68a52-5b5b-4a22-8f40-c41da8332cfe",
                       verified_via=f"JRC Veri Kataloğu DCAT-RDF (dct:issued {issued}) + doi.org handle kaydı (URL {hurl}, {hts[:10]})",
                       notes=(f"{e['notes']} Katalog bugün şu atfı öneriyor: \"{cit}\" — yani yıl 2026 ve ek DOI 10.2905/JRC.CXKEDRR; "
                              f"veri seti yayım tarihi {issued}, DOI 2023'te kaydedilmiş. Spec'teki 'Schiavina vd. 2023' korunur. "
                              "Crossref'te kayıt yok (DataCite DOI); api.datacite.org ve data.crosscite.org vekil sunucuda engelli."),
                       source_ids=["alan_literatur_lit_schiavina2023"])
            if year != ex.get("year"):
                issues.append(f"{eid}: yıl {year} ≠ beklenen {ex.get('year')}")
            issues.append(f"{eid}: JRC kataloğunun önerdiği atıf yılı 2026 ve ek DOI 10.2905/JRC.CXKEDRR (spec: 2023)")
            entries.append(rec)
            continue
        if doi and doi.startswith("10.5281/"):
            z = json.loads((RAW_LIT / "zenodo_7254221_worldcover_v200.json").read_text(encoding="utf-8"))
            md = z["metadata"]
            year = int(md["publication_date"][:4])
            rec.update(authors=[c["name"] for c in md["creators"]], year=year, title=md["title"], journal=None,
                       publisher="Zenodo (ESA WorldCover konsorsiyumu)", volume=None, issue=None, pages=None,
                       doi=z.get("doi") or md.get("doi"), url=f"https://doi.org/{doi}",
                       verified_via=f"Zenodo REST API kaydı {z['id']} (yayım {md['publication_date']}, sürüm {md.get('version')}, lisans {md.get('license', {}).get('id')})",
                       notes=f"{e['notes']} {len(md['creators'])} yazar.", source_ids=["alan_literatur_lit_zanaga2022"])
            if year != ex.get("year"):
                issues.append(f"{eid}: yıl {year} ≠ beklenen {ex.get('year')}")
            entries.append(rec)
            continue
        m = json.loads(cr_path(eid).read_text(encoding="utf-8"))["message"]
        authors = _people(m.get("author")) or _people(m.get("editor"))
        journal = html.unescape((m.get("container-title") or [None])[0] or "") or None
        year = _year(m)
        pages = m.get("page") or m.get("article-number")
        title = html.unescape((m.get("title") or [""])[0])
        rec.update(authors=authors, year=year, title=title, journal=journal, publisher=m.get("publisher"),
                   volume=m.get("volume"), issue=m.get("issue"), pages=(pages or "").replace("-", "–") or None,
                   doi=(doi if doi.lower() == m.get("DOI", "").lower() else m.get("DOI")),
                   url=f"https://doi.org/{doi}",
                   verified_via=(f"Crossref REST API (api.crossref.org/works/{m.get('DOI')}); tür {m.get('type')}; "
                                 f"basılı {_date(m, 'published-print') or '—'}, çevrimiçi {_date(m, 'published-online') or '—'}"),
                   notes=e["notes"], source_ids=[f"alan_literatur_lit_{eid}"])
        if m.get("ISBN"):
            rec["isbn"] = m["ISBN"]
        # karşılaştırma
        for k, v in ex.items():
            got = {"journal": journal, "volume": m.get("volume"), "issue": m.get("issue"), "pages": m.get("page"),
                   "year": year, "title": title, "publisher": m.get("publisher")}[k]
            ok = (str(got).lower().startswith(str(v).lower()) if k in ("publisher",) else
                  (str(got).lower() == str(v).lower()))
            if not ok:
                issues.append(f"{eid}: {k} = {got!r} (beklenen {v!r})")
        if eid == "ravaghi2020":
            ab = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.get("abstract", ""))).strip()
            sent = re.search(r"Common determinants of the required number of hospital beds[^.]*\.", ab).group(0)
            rec["notes"] = (f"{e['notes']} Crossref özetinden birebir: \"{sent}\" Tam metin (link.springer.com, PMC) vekil "
                            "sunucuda engelli → makalenin Hill-Burton formülünü adıyla anıp anmadığı DOĞRULANAMADI. "
                            "Formülün kendisi (yatak = nüfus × yatış oranı × ort. kalış / (365 × hedef doluluk)) bu nedenle "
                            "okunmuş bir akademik kaynağa bağlanamadı; formül, yatılan gün = yatak × 365 × doluluk tanımının "
                            "(SİY bülteni tanımı, saglik alanı) cebirsel tersidir.")
        if eid == "daskin2013" and m.get("type") == "edited-book":
            rec["notes"] = (e["notes"] + " Crossref türü 'edited-book' ve kişi 'editor' alanında; kitap tek yazarlıdır "
                            "(Mark S. Daskin) — atıfta yazar olarak verilir.")
        if eid == "huangfu_hall2018":
            rec["notes"] = (e["notes"] + f" Çevrimiçi yayım {_date(m, 'published-online')}, basılı sayı {_date(m, 'published-print')}"
                            " → künye yılı 2018.")
        if eid == "church_revelle1974":
            rec["notes"] = (e["notes"] + f" Crossref yayıncı alanı '{m.get('publisher')}' (dergi bugün Papers in Regional "
                            "Science adıyla yayımlanıyor); sayı 1.")
        entries.append(rec)
    return entries, issues


def _dump(obj) -> str:
    """YAML'ı takma ad (&id001) kullanmadan, Türkçe karakterlerle yazar."""
    import yaml

    class NoAlias(yaml.SafeDumper):
        def ignore_aliases(self, data):
            return True

    return yaml.dump(obj, Dumper=NoAlias, allow_unicode=True, sort_keys=False, width=118)


def write_literature(entries: list[dict], issues: list[str]) -> None:
    header = ("# Kaynakça (spec Bölüm 7 madde 14) — alan: alan-literatur\n"
              "# Her künye arşivlenmiş üst veriden okunur (Crossref / Zenodo / JRC kataloğu / ABD kanun dizini); elle yazılmadı.\n"
              "# Ham üst veri: data/raw/literatur/ (git dışı). Kaynak kayıtları: data/sources/alan_literatur.yaml\n"
              f"# Doğrulama tarihi: {ACCESSED.isoformat()}\n"
              "# Görev künyesinden farklar:\n" + "".join(f"#   - {i}\n" for i in issues) + "\n")
    OUT_LIT.write_text(header + _dump(entries), encoding="utf-8")
    print("yazıldı:", rel(OUT_LIT), len(entries), "künye")


# ----------------------------------------------------------------------------- 4. kaynak kayıtları

def src(sid, title, publisher, url, kind, path: Path, used_for, notes, published=None):
    mf = manifest_lookup(path.parent)
    rec = mf.get(rel(path))
    d = {"id": sid, "title": title, "publisher": publisher, "url": url or (rec["url"] if rec else None),
         "accessed": ACCESSED}
    if published:
        d["published"] = published
    d.update(kind=kind, archived=rel(path), sha256=(rec["sha256"] if rec else common.sha256(path)),
             used_for=used_for, notes=notes)
    return d


def build_sources(area_rows: list[dict], lit: list[dict], tls: dict) -> list[dict]:
    S = []
    byid = {}
    for r in area_rows:
        byid.setdefault(r["source_id"], []).append(r)

    def uf(sid):
        return [f"Ankara yüzölçümü: {common.fmt_tr(r['deger_km2'], 2 if r['deger_km2'] % 1 else 0)} km² — {r['kapsam']} [{r['sayfa']}]"
                for r in byid.get(sid, [])]

    tls_note = (f"webdosya.csb.gov.tr eksik TLS zinciri gönderiyor; ara sertifika ({tls['subject'].splitlines()[0]}) "
                f"CCADB yansısından alındı ve repoda {rel(CSB_INTERMEDIATE)} olarak saklanıyor, SHA-256 {tls['sha256'][:16]}… "
                "CCADB değeriyle aynı, kök R46 ile openssl verify OK; birleşik CA dosyası çalışma sırasında kurulup "
                "REQUESTS_CA_BUNDLE ile verildi (doğrulama kapatılmadı). Belge ÇŞB Ankara İl Müdürlüğü "
                "sitesinde plan askı ilanı ekidir; raporu planlama bürosu hazırlamıştır, alan değeri için kaynak göstermez.")
    A = RAW_ALAN
    S.append(src("alan_literatur_csb_ref4_altindag",
                 "Ankara İli, Altındağ İlçesi, Gümüşdere Mahallesi 19734 Ada 9 Parsele Ait 1/5000 Ölçekli Nazım İmar Planı Değişikliği Plan Açıklama Raporu (Ankara, 2020)",
                 "T.C. Çevre, Şehircilik ve İklim Değişikliği Bakanlığı — Ankara İl Müdürlüğü (webdosya.csb.gov.tr, plan ilanı eki)",
                 AREA_DOCS["csb_ref4_altindag_plan_20210524152714.pdf"], "resmi", A / "csb_ref4_altindag_plan_20210524152714.pdf",
                 uf("alan_literatur_csb_ref4_altindag"), "Spec kaynağı [4]. Yükleme 24.05.2021 (Last-Modified). 63 sayfa. " + tls_note,
                 published=dt.date(2021, 5, 24)))
    S.append(src("alan_literatur_csb_ref5_beypazari",
                 "Ankara İli, Beypazarı İlçesi, Macun Mahallesi 116 Ada 2-3 Nolu Parsellere İlişkin Enerji Üretim Alanı Amaçlı (GES) 1/5000 Ölçekli Nazım İmar Planı Açıklama Raporu",
                 "T.C. Çevre, Şehircilik ve İklim Değişikliği Bakanlığı — Ankara İl Müdürlüğü (webdosya.csb.gov.tr, plan ilanı eki)",
                 AREA_DOCS["csb_ref5_beypazari_macun_plan_20251117081655.pdf"], "resmi", A / "csb_ref5_beypazari_macun_plan_20251117081655.pdf",
                 uf("alan_literatur_csb_ref5_beypazari"),
                 "Spec kaynağı [5]. 25.632 km²'yi birebir yazan resmî belge: Valilik ÇDR 2018 Çizelge E.85 ile birlikte bu "
                 "değerin birincil dayanağı; ikincil kopyalara göre HGM ile aynı (HGM tablosu doğrudan okunamadı). "
                 "URL zaman damgası 17.11.2025. 83 sayfa. Aynı TLS çözümü.",
                 published=dt.date(2025, 11, 17)))
    S.append(src("alan_literatur_csb_ref6_cankaya",
                 "Ankara İli, Çankaya İlçesi, Yukarı Bahçelievler Mahallesi 7289 Ada 5 Parsel (Milli Kütüphane) 1/1000 Ölçekli Uygulama İmar Planı Değişikliği Plan Açıklama Raporu",
                 "MİA Planlama Mim. Müh. Müş. Tic. ve San. Ltd. Şti.; yayımlayan ÇŞB Ankara İl Müdürlüğü (webdosya.csb.gov.tr)",
                 AREA_DOCS["csb_ref6_cankaya_plan_aciklama_20200624102357.pdf"], "resmi", A / "csb_ref6_cankaya_plan_aciklama_20200624102357.pdf",
                 uf("alan_literatur_csb_ref6_cankaya"), "Spec kaynağı [6]. URL zaman damgası 24.06.2020. 11 sayfa. Aynı TLS çözümü.",
                 published=dt.date(2020, 6, 24)))
    S.append(src("alan_literatur_csb_polatli_2025",
                 "Ankara İli, Polatlı İlçesi, Zafer Mahallesi 2970 Ada 4 ve 7 Parsel 1/5.000 Ölçekli Nazım İmar Planı Plan Açıklama Raporu (Haziran 2025)",
                 "ÇŞB Ankara İl Müdürlüğü (webdosya.csb.gov.tr, plan ilanı eki)",
                 AREA_DOCS["csb_plan_20250702092636.pdf"], "resmi", A / "csb_plan_20250702092636.pdf",
                 uf("alan_literatur_csb_polatli_2025"), "Çapraz kontrol (arama motoruyla bulundu, indirilip okundu). 62 sayfa.",
                 published=dt.date(2025, 7, 2)))
    S.append(src("alan_literatur_csb_karakusunlar_2020",
                 "Ankara İli, Çankaya İlçesi, Karakusunlar Mahallesi 13911 Ada 11 ve 13 Parseller 1/5000 Ölçekli Nazım İmar Planı Değişikliği Plan Açıklama Raporu (Ankara, 2020)",
                 "ÇŞB Ankara İl Müdürlüğü (webdosya.csb.gov.tr, plan ilanı eki)",
                 AREA_DOCS["csb_plan_20201225092342.pdf"], "resmi", A / "csb_plan_20201225092342.pdf",
                 uf("alan_literatur_csb_karakusunlar_2020"), "Çapraz kontrol: [4]'teki 'göller dışında 24.521' cümlesinin başka raporda tekrarı. 27 sayfa.",
                 published=dt.date(2020, 12, 25)))
    S.append(src("alan_literatur_valilik_cdr_2018",
                 "Ankara İli 2018 Yılı Çevre Durum Raporu",
                 "T.C. Ankara Valiliği Çevre ve Şehircilik İl Müdürlüğü (webdosya.csb.gov.tr/db/ced)",
                 AREA_DOCS["csb_valilik_cdr_2018.pdf"], "resmi", A / "csb_valilik_cdr_2018.pdf",
                 uf("alan_literatur_valilik_cdr_2018"),
                 "Valiliğin resmî raporu; il yüzölçümünü tek sayı olarak yazmıyor, arazi kullanım tablosu (Çizelge E.85, kaynak "
                 "Tarım ve Orman İl Müd.) 25.632.000 da'ya toplanıyor (= 25.632 km²; toplama bizim hesabımız): [5] ile birlikte "
                 "25.632 km²'nin resmî dayanağı; ikincil kopyalara göre HGM ile aynı. ankara.gov.tr ve ankara.bel.tr vekil "
                 "sunucuda engelli (403). 182 sayfa.",
                 published=dt.date(2019, 9, 3)))
    S.append(src("alan_literatur_valilik_cdr_2017",
                 "Ankara İli 2017 Yılı Çevre Durum Raporu (Ankara, 2018)",
                 "T.C. Ankara Valiliği Çevre ve Şehircilik İl Müdürlüğü, ÇED Şube Müdürlüğü (webdosya.csb.gov.tr)",
                 AREA_DOCS["csb_valilik_cdr_2017.pdf"], "resmi", A / "csb_valilik_cdr_2017.pdf",
                 ["Çapraz kontrol: CORINE tablosu (Çizelge E.44, PDF s. 152) ÇDR 2018 Çizelge E.86 ile aynı (1990: 2.547.625,24 ha; 2006/2012: 2.565.474,80 ha)"],
                 "İl yüzölçümü sayısı yok; ormanlık alan 471.975,7 ha = il yüzölçümünün '% 18,3'ü (PDF s. 147) → ≈25.791 km² (yuvarlamalı, kullanılmadı).",
                 published=dt.date(2019, 2, 25)))
    S.append(src("alan_literatur_hgm_ikincil_snrylmz",
                 "il-ilce-json (GitHub snrylmz) — illerin yüzölçümü; README: kaynak https://www.harita.gov.tr/images/urun/il_ilce_alanlari.pdf",
                 "GitHub kullanıcısı snrylmz (ikincil; HGM tablosundan aktarım)",
                 SECONDARY["ikincil/github_snrylmz_il-ilce.json"], "ikincil", A / "ikincil/github_snrylmz_il-ilce.json",
                 uf("alan_literatur_hgm_ikincil_snrylmz"),
                 "HGM'nin kendi sayfası (www.harita.gov.tr/il-ve-ilce-yuzolcumleri) ve PDF'i vekil sunucuda engelli (403), web.archive.org da "
                 "engelli → HGM değeri yalnız ikincil kopyalardan. README arşivi: data/raw/alan/ikincil/github_snrylmz_README.md. "
                 "Yalnız 'ikincil kopyalara göre HGM ile aynı' notunun dayanağı; 25.632 km²'nin birincil dayanağı resmî belgeler "
                 "(alan_literatur_csb_ref5_beypazari, alan_literatur_valilik_cdr_2018)."))
    S.append(src("alan_literatur_hgm_ikincil_brkunver",
                 "turkiye-provinces-dataset (GitHub brkunver) — data/provinces.csv area_km2 + metadata/sources.json",
                 "GitHub kullanıcısı brkunver (ikincil; kaynak olarak HGM gösteriliyor)",
                 SECONDARY["ikincil/github_brkunver_provinces.csv"], "ikincil", A / "ikincil/github_brkunver_provinces.csv",
                 uf("alan_literatur_hgm_ikincil_brkunver"),
                 "sources.json (data/raw/alan/ikincil/github_brkunver_sources.json) Ankara notu: 'Area 25632 HGM (26897/25437/24521 rejected)'. "
                 "Yalnız 'ikincil kopyalara göre HGM ile aynı' notunun dayanağı; 25.632 km²'nin birincil dayanağı resmî belgeler "
                 "(alan_literatur_csb_ref5_beypazari, alan_literatur_valilik_cdr_2018)."))
    # kaynakça kayıtları
    for e in lit:
        sid = e["source_ids"][0]
        if e["id"] == "hill_burton1946":
            S.append(src(sid, e["title"], "United States Statutes at Large, vol. 60 (künye dizini: unitedstates/legisworks-historical-statutes)",
                         None, "ikincil", RAW_LIT / "ikincil/legisworks_statutes_060.yaml",
                         [f"Künye: {e['citation']}"],
                         f"Resmî PDF adresi {e['url']} — kanun PDF'i (govinfo.gov) ve loc.gov vekil sunucuda engelli; künye GitHub'daki Statutes at Large dizininden ve "
                         "ABD Kanunu notunun yansısından (data/raw/literatur/ikincil/uscode_42_1592c.xml) doğrulandı."))
            continue
        if e["id"] == "schiavina2023_ghs_pop":
            path = RAW_LIT / "jrc_ghs_pop_r2023a.rdf"
            kind = "veri"
        elif e["id"] == "zanaga2022":
            path = RAW_LIT / "zenodo_7254221_worldcover_v200.json"
            kind = "veri"
        else:
            path = cr_path(e["id"])
            kind = "akademik"
        au = "; ".join(e["authors"][:4]) + (" vd." if len(e["authors"]) > 4 else "")
        venue = e.get("journal") or e.get("publisher")
        vol = f" {e['volume']}" if e.get("volume") else ""
        iss = f"({e['issue']})" if e.get("issue") else ""
        pg = f": {e['pages']}" if e.get("pages") else ""
        rec = src(sid, e["title"], f"{au} — {venue}{vol}{iss}{pg} ({e['year']})" + (f"; doi:{e['doi']}" if e.get("doi") else ""),
                  None, kind, path, [f"Kaynakça künyesi ({e['id']}) — yalnız üst veri; bu kaynaktan sayı alınmadı"],
                  f"Doğrulama: {e['verified_via']}. Tam metin okunmadı (yayıncı siteleri vekil sunucuda engelli)."
                  if kind == "akademik" else f"Doğrulama: {e['verified_via']}.")
        S.append(merge_duplicate(rec, e["id"], path) if e["id"] in MERGED else rec)
    return S


def write_sources(S: list[dict]) -> None:
    header = ("# Alan: alan-literatur — Ankara yüzölçümü kaynakları (spec 3 'Ankara yüzölçümü', 4.3 madde 3) ve kaynakça (spec 7 madde 14)\n"
              "# Üreten: pipeline/20_area_literature.py (elle düzenlemeyin); ham belgeler: data/raw/alan/, data/raw/literatur/ (git dışı;\n"
              "# _manifest.jsonl'de url/tarih/sha256). Tablo: data/reference/ankara_alan_kaynaklari.csv ; künyeler: data/reference/literatur.yaml\n"
              "# Not: TÜİK satırları nufus alanının (tuik_bt_adnks20xx_yogunluk), COD-AB satırı sinirlar alanının (sinirlar_hdx_codab_tur) kaydına bağlanır.\n"
              "# Birleştirilen kayıtlar (alanlar arası aynı belge): " +
              "; ".join(f"{m['old_id']} → alan_literatur_lit_{k}" for k, m in MERGED.items()) + "\n\n")
    body = _dump(S)
    body = re.sub(r"\n- id:", "\n\n- id:", body)
    OUT_SRC.write_text(header + body, encoding="utf-8")
    print("yazıldı:", rel(OUT_SRC), len(S), "kayıt")


def main() -> None:
    tls = verify_csb_intermediate()
    print("ara sertifika doğrulandı:", tls["subject"].splitlines()[0], "SHA-256", tls["sha256"][:16] + "…")
    missing = {n: u for n, u in AREA_DOCS.items() if not (RAW_ALAN / n).exists()}
    if missing:
        with csb_bundle() as bundle:
            for name, url in missing.items():
                fetch(url, RAW_ALAN / name, bundle=bundle)
    for name, url in SECONDARY.items():
        fetch(url, RAW_ALAN / name)
    fetch_literature()
    rows = build_area_rows()
    write_csv(rows)
    lit, issues = build_literature()
    write_literature(lit, issues)
    write_sources(build_sources(rows, lit, tls))
    print("\nKünye farkları:")
    for i in issues:
        print("  -", i)


if __name__ == "__main__":
    main()
