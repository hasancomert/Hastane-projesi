"""TÜİK resmî nüfus verileri: ADNKS 2025/2024/2023 Ankara il ve ilçe nüfusları + 2030 il projeksiyonu.

Kaynaklar (hepsi TÜİK; ayrıntı ve kaynak kimlikleri data/sources/nufus.yaml):
  - İstatistik Veri Portalı (veriportali.tuik.gov.tr):
      * ADNKS Sonuçları haber bültenleri 2025 (no 53899), 2024 (53783), 2023 (49684): bülten JSON'u (metin + metaveri)
        ve her bültenin kendi il tabloları ("bt_" dosyaları; yıl bazında sabit)
      * İstatistiksel tablo "İl ve ilçelere göre il/ilçe merkezi, belde/köy nüfusu ve yıllık nüfus artış hızı" (.xls,
        güncel = 2025) ve "İl ve cinsiyete göre … nüfus yoğunluğu, 2007-2025" ("st_" dosyaları; TÜİK her yıl yeniler)
      * Veri akışları DF_ADNKS_T22 (il/ilçe nüfusu, son 2 yıl: 2024-2025) ve DF_ADNKS_T38 (il/ilçe yıllık nüfus artış
        hızı ‰, 2024-2025) CSV ("df_" dosyaları)
      * Nüfus Projeksiyonları 2023-2100 bülteni (53699) ve "Yıllara göre il nüfusları, 2023-2030" tabloları
  - Nüfus İstatistikleri Portalı (nip.tuik.gov.tr) Ankara dışa aktarımları (çapraz kontrol)
  - Şartname kaynakları [1] Yeni Ankara ve [3] Karar haberleri (çapraz kontrol); [2] ATO PDF'i buradan erişilemiyor

Yöntem notu: TÜİK ilçe tabloları yalnız son yılı (veri akışı T22 son iki yılı) yayımlıyor; eski bültenlerin
istatistiksel tablo bağlantıları da güncel (2025) tabloya gidiyor. Bu yüzden 2023 ilçe nüfusu, resmî 2024 ilçe nüfusu
ve resmî 2024 yıllık artış hızından (r = 1000·ln(P2024/P2023)) tersine hesaplanır: P2023 = P2024 / exp(r/1000).
Aynı yöntem 2025 → 2024 için resmî T22 değerlerini birebir veriyor; 2023 ilçe toplamı resmî il toplamına eşit.

Çıktı:
  data/reference/tuik_adnks_ankara.csv   25 ilçe: ilce, ilce_key, nufus_2025, nufus_2024, nufus_2023, source_id

Çalıştırma (repo kökünden):  .venv/bin/python pipeline/10_tuik_population.py [--ato]
  --ato : ATO "Rakamlarla Ankara" PDF'ini bir kez denemek için (bu ortamda bağlantı sıfırlanıyor).
Ham dosyalar data/raw/nufus/tuik/ altında; varsa yeniden indirilmez (çevrimdışı yeniden üretilebilir).
"""
from __future__ import annotations

import csv
import json
import math
import sys
import time
from pathlib import Path

import requests
import xlrd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline import common  # noqa: E402

RAW_DIR = common.RAW / "nufus" / "tuik"
OUT_CSV = common.REFERENCE / "tuik_adnks_ankara.csv"
PARAMS_YAML = common.PARAMS_DIR / "nufus.yaml"

PORTAL = "https://veriportali.tuik.gov.tr"
PRESS_API = PORTAL + "/api/tr/press/{id}"            # JSON; 'X-Requested-With' başlığı olmadan 404 döner
DATAFLOW_CSV = PORTAL + "/api/tr/dataflows/{id}/file/csv"

ADNKS_PRESS = {2025: "53899", 2024: "53783", 2023: "49684"}   # yıl -> bülten no (31 Aralık nüfusu)
PROJ_PRESS = "53699"                                          # Nüfus Projeksiyonları, 2023-2100

# İstatistiksel tablolar (statisticalTables; güncel sürüm) ve bülten tabloları (tables; bültene özgü)
ST_DISTRICT = "İl ve İlçelere Göre İl/İlçe Merkezi, Belde/Köy Nüfusu ve Yıllık Nüfus Artış Hızı"
ST_DENSITY = "İl ve Cinsiyete Göre İl/İlçe Merkezi, Belde/ Köy Nüfusu ve Nüfus Yoğunluğu"
BT_DENSITY = "İl ve cinsiyete göre il/ilçe merkezi, belde/köy nüfusu ve nüfus yoğunluğu"
BT_GROWTH = "İllere göre il/ilçe merkezi, belde/köy nüfusu ve yıllık nüfus artış hızı"
ST_PROJ_YEARS = "Yıllara Göre İl Nüfusları"
ST_PROJ_GROWTH = "İllere Göre Nüfus ve Yıllık Ortalama Nüfus Artış Hızları"
BT_PROJ_YEARS = "Yıllara göre il nüfusları"

DATAFLOWS = {
    "T22": ("DF_ADNKS_T22+V1.1", "df_DF_ADNKS_T22_il_ilce_nufus_2024_2025.csv"),
    "T38": ("DF_ADNKS_T38+V1.1", "df_DF_ADNKS_T38_il_ilce_artis_hizi_2024_2025.csv"),
}

NIP = "https://nip.tuik.gov.tr/Export/"
NIP_FILES = {
    "nip_ankara_cinsiyete_gore_nufus.xlsx": NIP + "ExportExcel?name=CinsiyeteGoreNufus&value=ANKARA",
    "nip_ankara_nufus_yogunlugu.xlsx": NIP + "ExportExcel?name=NufusYogunlugu&value=ANKARA",
    "nip_ankara_il_gosterge_karti.pdf": NIP + "ExportPdf?name=IlGostergeKartlari&value=ANKARA",
}
NEWS_FILES = {
    "haber_yeniankara_155001.html":
        "https://www.yeniankara.com.tr/guncel/turkiyenin-nufus-tablosu-degisti-ankara-rekora-kosuyor-155001",
    "haber_karar_2027851.html":
        "https://www.karar.com/guncel-haberler/son-dakika-tuik-acikladi-turkiye-nufusu-86-milyon-92-bin-168e-yukseldi-2027851",
}
ATO_URL = ("https://atonet.org.tr/Uploads/Birimler/Internet/Flash%20Tan%C4%B1t%C4%B1m%20Alan%C4%B1/"
           "2026-08-04-RakamlarlaAnkara/2026-08-03-Rakamlarla-Ankara.pdf")

PROVINCE = "Ankara"
N_DISTRICTS = 25
SPEC_PROVINCE_2025 = common.TUIK_PROVINCE_POP_2025            # 5.910.320 (şartname [1], [2])
SPEC_DISTRICT_2025 = {"Çankaya": 952_198, "Keçiören": 931_722}  # şartname [3]

# CSV 'source_id' sütunu: 2025 · 2024 · 2023 (hesap) değerlerinin kaynakları, ';' ile
SRC_IDS = "tuik_st_il_ilce_2025;tuik_df_adnks_t22;tuik_df_adnks_t38"


# ---------------------------------------------------------------- indirme

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


def _retry(fn, tries: int = 6, wait: float = 3.0):
    """TÜİK sunucuları TLS el sıkışmasını ara sıra kesiyor (SSL_ERROR_SYSCALL); birkaç kez dene."""
    for k in range(tries):
        try:
            return fn()
        except (requests.ConnectionError, requests.Timeout) as exc:
            if k == tries - 1:
                raise
            print(f"  bağlantı hatası ({type(exc).__name__}), yeniden deneniyor…")
            time.sleep(wait * (k + 1))


def ensure(url: str, dest: Path, timeout: int = 120) -> Path:
    """Dosya + manifest kaydı yoksa common.download ile indirir."""
    if not _manifest_has(dest, url):
        _retry(lambda: common.download(url, dest, timeout=timeout))
        print("  indirildi:", dest.relative_to(common.ROOT))
    return dest


XLS_MAGIC = b"\xd0\xcf\x11\xe0"   # OLE2 (.xls)


def _looks_valid(body: bytes, suffix: str) -> bool:
    if suffix == ".xls":
        return body.startswith(XLS_MAGIC)
    if suffix == ".csv":
        return body.lstrip(b"\xef\xbb\xbf").startswith(b'"') and b"<html" not in body[:500].lower()
    if suffix == ".json":
        try:
            return "data" in json.loads(body)
        except ValueError:
            return False
    return True


def ensure_portal(url: str, dest: Path, tries: int = 8) -> Path:
    """veriportali dosyaları: (1) JSON/CSV API'si 'X-Requested-With' başlığı istiyor, (2) art arda indirmede dosya
    yerine 'n saniye sonra tekrar dosya indirebilirsiniz' HTML sayfası dönüyor. common.download yalnız User-Agent
    gönderip içeriği denetlemediği için dosyayı önce burada indirip doğrularız; ardından common.download, dosya
    varken yeniden indirmez ve yalnız manifest kaydını (url, tarih, boyut, sha256) yazar."""
    if _manifest_has(dest, url) and _looks_valid(dest.read_bytes()[:4096] if dest.suffix != ".json"
                                                 else dest.read_bytes(), dest.suffix):
        return dest
    for k in range(tries):
        r = _retry(lambda: requests.get(url, headers={"User-Agent": common.USER_AGENT,
                                                      "X-Requested-With": "XMLHttpRequest"}, timeout=180))
        r.raise_for_status()
        if _looks_valid(r.content, dest.suffix):
            break
        print(f"  portal hız sınırı/geçersiz yanıt ({len(r.content)} B), {6 * (k + 1)} sn bekleniyor…")
        time.sleep(6 * (k + 1))
    else:
        raise RuntimeError(f"geçerli dosya alınamadı: {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(r.content)
    common.download(url, dest)  # dosya var: yalnız manifest kaydı
    print("  indirildi:", dest.relative_to(common.ROOT))
    time.sleep(6)  # portalın dosya indirme aralığı
    return dest


def load_press(press_id: str, name: str) -> dict:
    dest = ensure_portal(PRESS_API.format(id=press_id), RAW_DIR / name)
    return json.loads(dest.read_text(encoding="utf-8"))["data"]


def table_url(press: dict, title: str, kind: str) -> str:
    """kind='st' istatistiksel tablo (güncel sürüm), 'bt' bülten tablosu (bültene özgü)."""
    for t in press["statisticalTables" if kind == "st" else "tables"]:
        if t["title"].strip() == title:
            return PORTAL + t["url"]
    raise KeyError(f"bültende tablo yok: {title!r} (bülten {press['id']}, {press['period']})")


def fetch_all(try_ato: bool) -> dict:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    f: dict = {}
    for year, pid in ADNKS_PRESS.items():
        press = load_press(pid, f"veriportali_press_{pid}_adnks{year}.json")
        assert press["period"] == str(year), (pid, press["period"])
        f[("bt_density", year)] = ensure_portal(table_url(press, BT_DENSITY, "bt"),
                                                RAW_DIR / f"bt_adnks{year}_il_cinsiyet_nufus_yogunluk.xls")
        f[("bt_growth", year)] = ensure_portal(table_url(press, BT_GROWTH, "bt"),
                                               RAW_DIR / f"bt_adnks{year}_il_nufus_artis_hizi.xls")
        if year == 2025:  # istatistiksel tablolar hep güncel yılı verir; yalnız 2025 bülteninden alınır
            f["st_district"] = ensure_portal(table_url(press, ST_DISTRICT, "st"),
                                             RAW_DIR / "st_il_ilce_nufus_artis_hizi_2025.xls")
            f["st_density"] = ensure_portal(table_url(press, ST_DENSITY, "st"),
                                            RAW_DIR / "st_il_cinsiyet_nufus_yogunluk_2007_2025.xls")
    for key, (df_id, name) in DATAFLOWS.items():
        f[key] = ensure_portal(DATAFLOW_CSV.format(id=df_id), RAW_DIR / name)
    proj = load_press(PROJ_PRESS, f"veriportali_press_{PROJ_PRESS}_projeksiyon_2023_2100.json")
    f["proj_years"] = ensure_portal(table_url(proj, ST_PROJ_YEARS, "st"),
                                    RAW_DIR / "st_projeksiyon_yillara_gore_il_nufuslari_2023_2030.xls")
    f["proj_growth"] = ensure_portal(table_url(proj, ST_PROJ_GROWTH, "st"),
                                     RAW_DIR / "st_projeksiyon_illere_gore_nufus_artis_hizi_2023_2030.xls")
    f["proj_years_bt"] = ensure_portal(table_url(proj, BT_PROJ_YEARS, "bt"),
                                       RAW_DIR / "bt_projeksiyon_yillara_gore_il_nufuslari_2023_2030.xls")
    for name, url in {**NIP_FILES, **NEWS_FILES}.items():
        try:
            f[name] = ensure(url, RAW_DIR / name)
        except Exception as exc:  # çapraz kontrol dosyaları; ana veri değil
            print("  İNDİRİLEMEDİ (çapraz kontrol):", url, exc)
    if try_ato:
        try:
            ensure(ATO_URL, RAW_DIR / "ato_rakamlarla_ankara_2026-08-03.pdf", timeout=30)
        except Exception as exc:
            print("  ATO PDF'i erişilemedi (beklenen):", type(exc).__name__, str(exc)[:120])
    else:
        print("  ATO 'Rakamlarla Ankara' PDF'i atlandı (bu ortamda bağlantı sıfırlanıyor; denemek için --ato)")
    return f


# ---------------------------------------------------------------- ayrıştırma

def _rows(path: Path) -> list[list]:
    sh = xlrd.open_workbook(str(path)).sheet_by_index(0)
    return [[sh.cell_value(r, c) for c in range(sh.ncols)] for r in range(sh.nrows)]


def _int(v) -> int:
    assert isinstance(v, float) and v == int(v), v
    return int(v)


def _row(path: Path, name: str, col: int = 0) -> list:
    return next(r for r in _rows(path) if str(r[col]).strip() == name)


def parse_st_district(path: Path, provinces: set[str]) -> tuple[int, list[tuple[str, int, float]]]:
    """İl satırı + ardından gelen ilçeler (bir sonraki il adına kadar): (ad, nüfus, artış hızı ‰)."""
    rows = _rows(path)
    start = next(i for i, r in enumerate(rows) if str(r[0]).strip() == PROVINCE)
    total = _int(rows[start][1])
    out = []
    for r in rows[start + 1:]:
        name = str(r[0]).strip()
        if name in provinces or not isinstance(r[1], float):
            break
        out.append((name, _int(r[1]), float(r[5])))
    return total, out


def parse_st_density(path: Path) -> dict:
    """2007-2025 serisi: yıl -> {il: (nüfus, yoğunluk)}. Yıl hücresi Excel tarihi (31.12.YYYY), yalnız ilk satırda."""
    out: dict = {}
    year = None
    for r in _rows(path):
        if isinstance(r[0], float):
            year = xlrd.xldate_as_tuple(r[0], 0)[0]
        name = str(r[1]).strip()
        if year and name and isinstance(r[2], float):
            out.setdefault(year, {})[name] = (_int(r[2]), float(r[13]))
    return out


def parse_dataflow(path: Path, name_col: int, provinces: set[str]) -> dict:
    """Veri akışı CSV'si (';' ayraçlı, ondalık virgül): Ankara bloğu {ilçe: {yıl: değer}} + il satırı.
    İlçeler il satırından sonra gelir; aynı adlı ilçeler 'Ankara Gölbaşı' gibi il önekli yazılır."""
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh, delimiter=";"))[1:]
    if name_col == 4:  # T22: yalnız 'Toplam' yerleşim türü
        rows = [r for r in rows if r[3] == "Toplam"]
    names = list(dict.fromkeys(r[name_col] for r in rows))
    i = names.index(PROVINCE)
    block = []
    for n in names[i + 1:]:
        if n in provinces:
            break
        block.append(n)
    vals: dict = {}
    for r in rows:
        if r[name_col] in block or r[name_col] == PROVINCE:
            key = r[name_col].removeprefix(PROVINCE + " ")
            vals.setdefault(key, {})[int(r[5])] = float(r[6].replace(",", "."))
    return vals


def parse_bt_density(path: Path) -> tuple[int, float]:
    r = _row(path, PROVINCE)
    return _int(r[1]), float(r[12])


def parse_bt_growth(path: Path) -> tuple[int, int, float]:
    """(önceki yıl nüfusu, bu yıl nüfusu, bu yılın artış hızı ‰)."""
    r = _row(path, PROVINCE)
    return _int(r[1]), _int(r[2]), float(r[11])


def parse_projection(path: Path) -> dict:
    rows = _rows(path)
    hdr = next(r for r in rows if str(r[0]).startswith("İl-Province"))
    years = [int(h) if isinstance(h, float) else int(str(h).split("(")[0]) for h in hdr[1:]]
    row = next(r for r in rows if str(r[0]).strip() == PROVINCE)
    notes = [str(r[0]).strip() for r in rows if "senaryo" in str(r[0]).lower()]
    return {"values": dict(zip(years, (_int(v) for v in row[1:]))), "notes": notes}


def parse_nip(files: dict) -> dict:
    import openpyxl
    out: dict = {}
    for key, name in (("pop", "nip_ankara_cinsiyete_gore_nufus.xlsx"), ("density", "nip_ankara_nufus_yogunlugu.xlsx")):
        p = files.get(name)
        if not p:
            continue
        ws = openpyxl.load_workbook(p, read_only=True, data_only=True).worksheets[0]
        out[key] = {int(r[0]): r[2] for r in ws.iter_rows(min_row=3, values_only=True)
                    if r[0] is not None and str(r[0]).isdigit() and r[2] is not None}
    return out


def news_checks(files: dict) -> dict:
    """Haber sayfalarında beklenen ifadeler geçiyor mu (yalnız çapraz kontrol)."""
    from bs4 import BeautifulSoup
    want = {
        "haber_yeniankara_155001.html": ["5 milyon 910 bin 320"],
        "haber_karar_2027851.html": ["5 milyon 910 bin 320", "Çankaya (Ankara) 952 bin 198",
                                     "Keçiören (Ankara) 931 bin 722"],
    }
    res = {}
    for name, phrases in want.items():
        p = files.get(name)
        if not p:
            res[name] = "dosya yok"
            continue
        txt = " ".join(BeautifulSoup(p.read_bytes(), "lxml").get_text(" ").split())
        res[name] = {ph: (ph in txt) for ph in phrases}
    return res


def ok(cond: bool) -> str:
    return "TAMAM" if cond else "ÇELİŞKİ"


# ---------------------------------------------------------------- ana akış

def main() -> None:
    files = fetch_all(try_ato="--ato" in sys.argv)
    fmt = common.fmt_tr

    series = parse_st_density(files["st_density"])
    provinces = set(series[2025]) - {"Toplam-Total"}
    assert len(provinces) == 81, len(provinces)

    # --- 2025: istatistiksel tablo (xls)
    tot25, st = parse_st_district(files["st_district"], provinces)
    assert len(st) == N_DISTRICTS, len(st)
    names = [n for n, _, _ in st]
    p25 = {n: v for n, v, _ in st}
    r25_xls = {n: r for n, _, r in st}

    # --- 2024: veri akışı T22; 2023: T22 2024 + T38 2024 artış hızından tersine hesap
    t22 = parse_dataflow(files["T22"], 4, provinces)
    t38 = parse_dataflow(files["T38"], 3, provinces)
    assert set(t22) - {PROVINCE} == set(names) == set(t38) - {PROVINCE}, "ilçe adları kaynaklar arasında farklı"
    p24 = {n: int(t22[n][2024]) for n in names}
    p23_raw = {n: p24[n] / math.exp(t38[n][2024] / 1000) for n in names}
    p23 = {n: round(v) for n, v in p23_raw.items()}
    max_frac = max(abs(p23_raw[n] - p23[n]) for n in names)
    inv24 = {n: p25[n] / math.exp(t38[n][2025] / 1000) for n in names}

    # --- il toplamları (bülten tabloları; yıl bazında sabit)
    bt = {y: parse_bt_density(files[("bt_density", y)]) for y in ADNKS_PRESS}
    gr = {y: parse_bt_growth(files[("bt_growth", y)]) for y in ADNKS_PRESS}
    prov = {y: bt[y][0] for y in ADNKS_PRESS}

    print("\nKontroller")
    print(f"  il nüfusu 2025 = {fmt(tot25)} (şartname {fmt(SPEC_PROVINCE_2025)}):", ok(tot25 == SPEC_PROVINCE_2025))
    for n, v in SPEC_DISTRICT_2025.items():
        print(f"  {n} 2025 = {fmt(p25[n])} (şartname {fmt(v)}):", ok(p25[n] == v))
    checks = [
        ("2025 ilçe toplamı = il (xls)", sum(p25.values()) == tot25 == prov[2025]),
        ("2025 xls = T22 2025 (25 ilçe + il)", all(int(t22[n][2025]) == p25[n] for n in names)
         and int(t22[PROVINCE][2025]) == tot25),
        ("2025 xls artış hızı = T38 2025", all(abs(r25_xls[n] - t38[n][2025]) < 1e-9 for n in names)),
        ("2024 ilçe toplamı (T22) = 2024 bülteni il nüfusu", sum(p24.values()) == prov[2024] == int(t22[PROVINCE][2024])),
        ("2025→2024 tersine hesap = T22 2024 (yöntem doğrulaması)",
         all(round(inv24[n]) == p24[n] and abs(inv24[n] - p24[n]) < 1e-3 for n in names)),
        (f"2023 tersine hesap tam sayıya yakın (en büyük sapma {max_frac:.1e})", max_frac < 1e-3),
        ("2023 ilçe toplamı = 2023 bülteni il nüfusu", sum(p23.values()) == prov[2023]),
        ("bülten artış tabloları il nüfusları ile tutarlı",
         all(gr[y][1] == prov[y] for y in ADNKS_PRESS) and gr[2025][0] == prov[2024] and gr[2024][0] == prov[2023]),
        ("2025 bülteninin 2007-2025 serisi önceki bültenlerle aynı (revizyon yok)",
         all(series[y][PROVINCE][0] == prov[y] for y in ADNKS_PRESS)),
    ]
    for label, cond in checks:
        print(f"  {label}:", ok(cond))
    assert all(c for _, c in checks), "kontrol başarısız"
    nip = parse_nip(files)
    for y in ADNKS_PRESS:
        if y in nip.get("pop", {}):
            print(f"  NİP {y} il nüfusu {fmt(nip['pop'][y])}:", ok(nip["pop"][y] == prov[y]))
    print(f"  il nüfusu: 2025 {fmt(prov[2025])} · 2024 {fmt(prov[2024])} · 2023 {fmt(prov[2023])}")

    # --- yoğunluk ve TÜİK'in kullandığı alan (nüfus / yoğunluk; HGM 1/1.000.000, doğal + baraj gölleri hariç)
    print("\nNüfus yoğunluğu (kişi/km²) ve örtük alan (km² = nüfus / yoğunluk)")
    for y in ADNKS_PRESS:
        p, d = bt[y]
        print(f"  {y} bülteni tablosu: {fmt(d, 4)} → {fmt(p / d, 2)} km²")
    for y in (2025, 2024, 2023):
        p, d = series[y][PROVINCE]
        print(f"  2007-2025 serisi (2025 sürümü), {y}: {fmt(d, 4)} → {fmt(p / d, 2)} km²")
    if "density" in nip:
        print("  NİP (yuvarlanmış):", ", ".join(f"{y}: {fmt(float(nip['density'][y]), 1)}"
                                              for y in sorted(nip["density"])[-3:]))

    # --- 2030 projeksiyonu
    proj = parse_projection(files["proj_years"])
    proj_bt = parse_projection(files["proj_years_bt"])
    pv = proj["values"]
    g = _row(files["proj_growth"], PROVINCE)
    assert pv == proj_bt["values"] and pv[2023] == prov[2023] and _int(g[2]) == pv[2030]
    print("\nTÜİK Nüfus Projeksiyonları 2023-2100 (Ankara; yıl sonu)")
    print("  ", ", ".join(f"{y}: {fmt(v)}" for y, v in pv.items()))
    print("  dipnot:", " / ".join(proj["notes"]))
    print(f"  2023-2030 yıllık ortalama artış hızı: ‰{fmt(float(g[3]), 2)}")
    print(f"  projeksiyonun 2025'i {fmt(pv[2025])} vs ADNKS 2025 {fmt(prov[2025])}:"
          f" fark {fmt(prov[2025] - pv[2025])} (%{fmt(100 * (prov[2025] / pv[2025] - 1), 2)})")
    print(f"  yeniden tabanlanmış 2030 = ADNKS 2025 × P2030/P2025 = {fmt(prov[2025] * pv[2030] / pv[2025], 1)}")

    print("\nHaber çapraz kontrolü:", json.dumps(news_checks(files), ensure_ascii=False))

    # --- CSV (TÜİK tablosundaki sıra ve yazım)
    keys = [common.norm_name(n) for n in names]
    assert len(set(keys)) == N_DISTRICTS
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["ilce", "ilce_key", "nufus_2025", "nufus_2024", "nufus_2023", "source_id"])
        for n, k in zip(names, keys):
            w.writerow([n, k, p25[n], p24[n], p23[n], SRC_IDS])
    print(f"\nyazıldı: {OUT_CSV.relative_to(common.ROOT)} ({len(names)} ilçe; "
          f"toplamlar {fmt(sum(p25.values()))} / {fmt(sum(p24.values()))} / {fmt(sum(p23.values()))})")

    # --- elle yazılan parametre dosyası yeniden üretilen sayılarla aynı mı
    if PARAMS_YAML.exists():
        import yaml
        params = {p["key"]: p for p in yaml.safe_load(PARAMS_YAML.read_text(encoding="utf-8"))}
        for key, val in (("population.province_2025", prov[2025]), ("population.province_2030", pv[2030])):
            got = params.get(key, {}).get("value")
            print(f"  {key}: YAML {got} / veri {val}:", ok(got == val))


if __name__ == "__main__":
    main()
