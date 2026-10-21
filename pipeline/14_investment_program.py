"""2026 Yılı Kamu Yatırım Programı (SBB) — sağlık sektörü hastane projeleri ve sabit maliyet regresyonu.

Spec 4.2 madde 1: `tutar = F0 + v · yatak` regresyonu (aynı yıl fiyatları, ≥10 proje).

Çıktılar:
  data/reference/yatirim_programi_2026_saglik.csv   sağlık sektörünün tüm proje satırları + dahil/dışlama nedeni
  data/processed/sabit_maliyet_regresyon.json       regresyon sonuçları ve sağlamlık kontrolleri
  data/processed/sabit_maliyet_regresyon.png        uyum + artık grafiği

Çalıştırma (repo kökünden):  .venv/bin/python pipeline/14_investment_program.py
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline import common  # noqa: E402

RAW_DIR = common.RAW / "yatirim"
OUT_CSV = common.REFERENCE / "yatirim_programi_2026_saglik.csv"
OUT_JSON = common.PROCESSED / "sabit_maliyet_regresyon.json"
OUT_PNG = common.PROCESSED / "sabit_maliyet_regresyon.png"

KYP_PDF = "2026-Yili-Kamu-Yatirim-Programi-26012026.pdf"
DOWNLOADS = [
    # (url, yerel ad) — hepsi SBB / Resmî Gazete resmi belgeleri
    ("https://www.sbb.gov.tr/wp-content/uploads/2026/01/2026-Yili-Kamu-Yatirim-Programi-26012026.pdf",
     KYP_PDF),
    ("https://www.sbb.gov.tr/wp-content/uploads/2026/01/"
     "2026-Yili-Kamu-Yatirim-Programinin-Kabulu-ve-Uygulanmasina-Dair-Karar.pdf",
     "2026-Yili-KYP-Kabulu-ve-Uygulanmasina-Dair-Karar.pdf"),
    ("https://www.sbb.gov.tr/wp-content/uploads/2025/09/2026-2028_Donemi_Yatirim_Programi_Hazirlama_Rehberi.pdf",
     "2026-2028_Donemi_Yatirim_Programi_Hazirlama_Rehberi.pdf"),
    ("https://www.resmigazete.gov.tr/eskiler/2026/01/20260115M1-1.pdf", "RG_20260115M1-1.pdf"),
    ("https://www.sbb.gov.tr/2026-yili-kamu-yatirim-programi-yayimlandi/", "sbb_2026_kyp_duyuru.html"),
]

# Tebliğ değerleri yalnız karşılaştırma için (Bölüm 3 tablosu; doğrulaması maliyet alanında)
TEBLIG_TL_M2 = {"IV-C (<200 yatak)": 40_500, "V-B (200-399)": 43_850, "V-C (>=400)": 48_750,
                "V-D (şehir h.)": 53_500}
M2_PER_BED_MIN = 130  # Bölüm 3 [11]: 1200 yataklı hastaneler için asgari

PROJ_RE = re.compile(r"^\d{4}[A-Z]\d{2}-\d+$")
YEARS_RE = re.compile(r"^(\d{4})-(\d{4})$")
NUM_RE = re.compile(r"^\(?(\d{1,3}(?:\.\d{3})*)\)?$")
FIN_LABELS = ("Yurtiçi Hibe", "AB Hibesi", "Döner Sermaye", "Özel Ödenek", "Japon", "Arap Ekonomik",
              "Katar Hayır", "Fonu", "(JICA)", "(IPA)", "Dış Kredi", "Avrupa", "Dünya Bankası")


# ---------------------------------------------------------------- indirme

def ensure_downloads() -> None:
    """Eksik belgeleri indirir; var olanları atlar (manifest ilk indirmede yazıldı)."""
    for url, name in DOWNLOADS:
        dest = RAW_DIR / name
        if dest.exists():
            continue
        try:
            rec = common.download(url, dest, timeout=900)
            print("indirildi:", rec["file"], rec["bytes"])
        except Exception as exc:  # ağ hatası: ana PDF yoksa devam edilemez
            print("İNDİRİLEMEDİ:", url, exc)
            if name == KYP_PDF:
                raise


# ---------------------------------------------------------------- PDF ayrıştırma

def find_health_pages(pdf_path: Path) -> list[int]:
    """'SEKTÖRÜ: SAĞLIK' başlıklı sayfaların 0 tabanlı indeksleri (pypdf ile hızlı tarama)."""
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    pages = []
    for i, p in enumerate(reader.pages):
        t = p.extract_text() or ""
        if "SSEEKKTTÖÖRRÜÜ:: SSAAĞĞLLIIKK" in t:
            pages.append(i)
    return pages


def dedouble(s: str) -> str:
    """Sahte kalın yazı (her karakter iki kez basılmış): 'SSAAĞĞLLIIKK' -> 'SAĞLIK'."""
    out = []
    for tok in s.split():
        if len(tok) >= 2 and len(tok) % 2 == 0 and all(tok[i] == tok[i + 1] for i in range(0, len(tok), 2)):
            out.append(tok[::2])
        else:
            out.append(tok)
    return " ".join(out)


def is_doubled(tok: str) -> bool:
    return len(tok) >= 2 and len(tok) % 2 == 0 and all(tok[i] == tok[i + 1] for i in range(0, len(tok), 2))


def page_lines(page) -> list[list[dict]]:
    """90° döndürülmüş tablo sayfasını satırlara çevirir.

    Her karakter için: satır konumu = x0, satır içi konum (soldan sağa) = sayfa yüksekliği − bottom.
    """
    H = page.height
    chars = [c for c in page.chars if not c["upright"] and c["text"].strip()]
    chars.sort(key=lambda c: c["x0"])
    groups: list[list[dict]] = []
    for c in chars:
        if groups and abs(c["x0"] - groups[-1][0]["x0"]) <= 1.6:
            groups[-1].append(c)
        else:
            groups.append([c])
    lines = []
    for g in groups:
        items = [{"a0": H - c["bottom"], "a1": H - c["top"], "t": c["text"], "pos": c["x0"]} for c in g]
        items.sort(key=lambda d: d["a0"])
        lines.append(items)
    return lines


def join_chars(items: list[dict], gap: float = 1.0) -> str:
    out, prev = [], None
    for it in items:
        if prev is not None and it["a0"] - prev > gap:
            out.append(" ")
        out.append(it["t"])
        prev = it["a1"] if prev is None else max(prev, it["a1"])
    return "".join(out).strip()


def tokens(items: list[dict], gap: float = 2.5) -> list[dict]:
    """Karakterleri büyük boşluklardan ayırarak parçalara böler (sayı sütunları için)."""
    toks: list[dict] = []
    for it in items:
        if toks and it["a0"] - toks[-1]["a1"] <= gap:
            toks[-1]["a1"] = max(toks[-1]["a1"], it["a1"])
            toks[-1]["t"] += it["t"]
        else:
            toks.append({"a0": it["a0"], "a1": it["a1"], "t": it["t"]})
    return toks


# Sütun sınırları (satır içi konum, pt). Başlık satırından doğrulanır.
COL_BOUNDS = {"no": (0, 118), "ad": (118, 238), "yer": (238, 294), "kar": (294, 404.5), "yil": (404.5, 442)}
NUM_FIELDS = ["proje_tutari_dis_kredi_tl", "proje_tutari_tl", "kumulatif_2025_dis_kredi_tl",
              "kumulatif_2025_tl", "odenek_2026_dis_kredi_tl", "odenek_2026_tl"]


def split_columns(items: list[dict], num_centers: list[float]) -> tuple[dict, list]:
    cols: dict[str, list] = defaultdict(list)
    numeric: list[dict] = []
    for it in items:
        mid = (it["a0"] + it["a1"]) / 2
        if mid >= COL_BOUNDS["yil"][1]:
            numeric.append(it)
            continue
        for k, (lo, hi) in COL_BOUNDS.items():
            if lo <= mid < hi:
                cols[k].append(it)
                break
    text = {k: join_chars(v) for k, v in cols.items()}
    nums = []
    for tok in tokens(numeric):
        mid = (tok["a0"] + tok["a1"]) / 2
        j = min(range(len(num_centers)), key=lambda i: abs(num_centers[i] - mid))
        nums.append((j, tok["t"]))
    return text, nums


def parse_number(s: str):
    s = s.strip()
    if s in ("-", ""):
        return None
    m = NUM_RE.match(s)
    if not m:
        return None
    return int(m.group(1).replace(".", ""))


def parse_health_sector(pdf_path: Path, page_idx: list[int]) -> tuple[list[dict], dict]:
    import pdfplumber

    rows: list[dict] = []
    footnotes: dict[str, str] = {}
    owner = section = subsection = None
    cur: dict | None = None
    default_centers = [471.8, 536.5, 588.4, 650.0, 701.8, 763.4]
    with pdfplumber.open(str(pdf_path)) as pdf:
        for pi in page_idx:
            page = pdf.pages[pi]
            lines = page_lines(page)
            # sayı sütunu merkezleri: başlıktaki 'DIŞ KREDİ / TOPLAM' satırı (sayılar sağa yaslı → +11 pt)
            centers = default_centers
            printed = None
            in_header = True
            in_foot = False
            last_fn = None
            for items in lines:
                full = join_chars(items)
                if not full:
                    continue
                if in_header:
                    if "DDIIŞŞ" in full and "TTOOPPLLAAMM" in full:
                        toks = [t for t in tokens(items, gap=4.0)]
                        if len(toks) == 6:
                            # başlık ortası ile sağa yaslı sayının ortası arasındaki fark ≈ +8…+14 pt
                            centers = [(t["a0"] + t["a1"]) / 2 + 9 for t in toks]
                        in_header = False
                    continue
                if re.fullmatch(r"\d{1,3}", full):
                    printed = int(full)
                    continue
                if full.startswith("-----"):
                    in_foot = True
                    continue
                if in_foot:
                    m = re.match(r"^\[(\d+)\]\s*(.*)$", full)
                    if m:
                        last_fn = m.group(1)
                        footnotes[last_fn] = m.group(2)
                    elif last_fn:
                        footnotes[last_fn] += " " + full
                    continue
                first = full.split()[0]
                # kurum toplam satırı (kalın, çift basılmış ad)
                if is_doubled(first) and not PROJ_RE.match(first):
                    name_part = re.split(r"\s(?=[-\d])", full, maxsplit=1)[0]
                    owner = dedouble(name_part).strip()
                    section = subsection = None
                    cur = None
                    continue
                m = re.match(r"^([ABC])\)\s", full)
                if m:
                    section = {"A": "A) Etüd-proje işleri", "B": "B) Devam eden projeler",
                               "C": "C) Yeni projeler"}[m.group(1)]
                    subsection = None
                    cur = None
                    continue
                m = re.match(r"^([ab])\)\s", full)
                if m:
                    subsection = {"a": "a) 2026 yılında bitenler",
                                  "b": "b) 2026 yılından sonraya kalanlar"}[m.group(1)]
                    cur = None
                    continue
                text, nums = split_columns(items, centers)
                pno = text.get("no", "")
                yrs = YEARS_RE.match(text.get("yil", ""))
                is_fin = (text.get("ad", "").startswith(FIN_LABELS)
                          or (not text.get("ad") and nums and all(n[1].startswith("(") for n in nums)))
                if PROJ_RE.match(pno) or (yrs and not is_fin and cur is not None):
                    parent = None
                    if not PROJ_RE.match(pno):  # alt proje satırı (numarasız)
                        parent = cur["proje_no"] if not cur.get("ust_proje_no") else cur["ust_proje_no"]
                    cur = {
                        "kurum": owner, "bolum": section, "alt_bolum": subsection,
                        "proje_no": pno if PROJ_RE.match(pno) else "",
                        "ust_proje_no": parent or "",
                        "proje_adi": text.get("ad", ""), "yeri": text.get("yer", ""),
                        "karakteristik": text.get("kar", ""),
                        "baslama": int(yrs.group(1)) if yrs else None,
                        "bitis": int(yrs.group(2)) if yrs else None,
                        **{f: None for f in NUM_FIELDS},
                        "finansman_notu": "", "pdf_sayfa": pi + 1, "_fin": False,
                    }
                    for j, s in nums:
                        v = parse_number(s)
                        if v is not None and not s.startswith("("):
                            cur[NUM_FIELDS[j]] = v
                    rows.append(cur)
                    continue
                if cur is None:
                    continue
                if is_fin or cur["_fin"]:
                    cur["_fin"] = True
                    label = text.get("ad", "")
                    vals = [f"{NUM_FIELDS[j]}={s}" for j, s in nums]
                    piece = " ".join(x for x in [label] + vals if x)
                    cur["finansman_notu"] = (cur["finansman_notu"] + "; " + piece).strip("; ")
                    continue
                # devam satırı: ad / yer / karakteristik sürer
                for k, f in (("ad", "proje_adi"), ("yer", "yeri"), ("kar", "karakteristik")):
                    if text.get(k):
                        cur[f] = (cur[f] + " " + text[k]).strip()
                for j, s in nums:
                    v = parse_number(s)
                    if v is not None and not s.startswith("(") and cur[NUM_FIELDS[j]] is None:
                        cur[NUM_FIELDS[j]] = v
            for r in rows:
                if r["pdf_sayfa"] == pi + 1:
                    r["basili_sayfa"] = printed
    for r in rows:
        r.pop("_fin", None)
        r["karakteristik"] = re.sub(r"\s+", " ", r["karakteristik"]).replace("m² )", "m²)")
        refs = re.findall(r"\[(\d+)\]", r["proje_adi"])
        r["dipnotlar"] = ",".join(refs)
        r["proje_adi"] = re.sub(r"\s*\[\d+\]", "", r["proje_adi"]).strip()
        r["dipnot_metni"] = " | ".join(f"[{k}] {footnotes.get(k, '')}" for k in refs)
    return rows, footnotes


# ---------------------------------------------------------------- hastane satırları

def tr_int(s: str) -> int:
    return int(s.replace(".", ""))


def split_top_level(s: str) -> list[str]:
    """Parantez dışındaki virgüllerden böler: 'ADSM (20 ünit), Hastane İnşaatı (135 yatak, …)'."""
    parts, depth, buf = [], 0, ""
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(buf.strip())
            buf = ""
        else:
            buf += ch
    if buf.strip():
        parts.append(buf.strip())
    return parts


def classify(r: dict) -> None:
    """Yatak/alan çıkarır ve regresyona dahil edilip edilmeyeceğini gerekçesiyle yazar."""
    kar = r["karakteristik"]
    comps = split_top_level(kar)
    hosp = [c for c in comps if re.match(r"^(Hastane İnşaatı|Entegre İlçe Hastanesi|Devlet Hastanesi)\s*\(", c)]
    beds = area = None
    for comp in hosp:  # birden çok hastane bileşeni varsa (toplu proje) toplanır
        inner = comp[comp.index("(") + 1:].rstrip(")")
        mb = re.search(r"(\d{1,3}(?:\.\d{3})*)\s*yatak", inner)
        ma = re.search(r"(\d{1,3}(?:\.\d{3})*)\s*m²", inner)
        if mb:
            beds = (beds or 0) + tr_int(mb.group(1))
        if ma:
            area = (area or 0) + tr_int(ma.group(1))
    r["yatak"] = beds
    r["alan_m2"] = area
    tut = r["proje_tutari_tl"]
    r["m2_yatak"] = round(area / beds, 1) if beds and area else None
    r["tl_yatak"] = round(tut / beds) if beds and tut else None
    r["tl_m2"] = round(tut / area) if area and tut else None

    name = r["proje_adi"]
    owner = r["kurum"] or ""
    r["kurum_turu"] = ("bakanlik" if owner.startswith("SAĞLIK BAKANLIĞI")
                       else "universite" if "ÜNİVERSİTE" in owner else "diger")
    r["sehir_hastanesi"] = "Şehir Hastanesi" in name
    # etüt-proje, müşavirlik ve sismik izolatör binanın parçasıdır; diğer bileşenler paket sayılır
    extra = [c for c in comps if c not in hosp
             and not re.match(r"^(Etüt-Proje|Müşavirlik|Sismik İzolatör)", c)]
    reason = ""
    if r["ust_proje_no"]:
        reason = "alt proje satırı (ana projenin parçası)"
    elif beds is None:
        reason = "yatak sayılı hastane inşaatı bileşeni yok (hastane binası değil ya da yatak belirtilmemiş)"
    elif r["kurum_turu"] == "diger":
        reason = "Sağlık Bakanlığı / üniversite dışı kurum projesi"
    elif re.search(r"\d+\s*İl\b", r["yeri"]) or "," in r["yeri"] or len(hosp) > 1:
        reason = "çok yerli/toplu proje (birden çok il ya da tesis)"
    elif "Kullanım Bedeli" in name:
        reason = "KÖİ şehir hastanesi kira/kullanım bedeli (bütçe yatırımı değil)"
    elif r["bolum"] and r["bolum"].startswith("A)"):
        reason = "etüd-proje işleri bölümünde (yapım henüz programlanmamış)"
    elif extra:
        reason = "paket proje: hastane dışında bileşen var (" + "; ".join(extra) + ")"
    elif " ile " in name:
        reason = "birden çok tesis tek satırda"
    elif re.search(r"Ek Bina|Ek Binası|Renovasyon|Tadilat|Onarım|Güçlendirme", name):
        reason = "ek bina / yenileme (mevcut kampüse ek; tek başına yeni hastane değil)"
    elif re.search(r"Acil Durum Hastanesi|Pandemi", name):
        reason = "acil durum / pandemi hastanesi (özel amaçlı hızlı inşa; alan/yatak standart dışı)"
    elif area is None:
        reason = "kapalı alan belirtilmemiş"
    elif not tut:
        reason = "proje tutarı yok"
    elif tut / area < 10_000:
        reason = "proje tutarı yalnız etüt/proje bedeli düzeyinde (< 10.000 TL/m²)"
    r["hastane_satiri"] = beds is not None and not r["ust_proje_no"]
    r["dislama_nedeni"] = reason
    r["dahil_ana"] = (reason == "" and r["kurum_turu"] == "bakanlik")
    r["dahil_genis"] = (reason == "")  # üniversiteler dahil


# ---------------------------------------------------------------- regresyon

def ols(df, x="yatak", y="proje_tutari_tl", label="", names=("F0_tl", "v_tl_per_yatak")):
    import numpy as np
    import statsmodels.api as sm

    X = sm.add_constant(df[x].astype(float))
    m = sm.OLS(df[y].astype(float), X).fit()
    hc3 = m.get_robustcov_results(cov_type="HC3")
    ci = m.conf_int(0.05)
    return m, {
        "etiket": label, "n": int(m.nobs),
        names[0]: float(m.params["const"]), names[1]: float(m.params[x]),
        "se_kesisim": float(m.bse["const"]), "se_egim": float(m.bse[x]),
        "ci95_kesisim": [float(ci.loc["const", 0]), float(ci.loc["const", 1])],
        "ci95_egim": [float(ci.loc[x, 0]), float(ci.loc[x, 1])],
        "p_kesisim": float(m.pvalues["const"]), "p_egim": float(m.pvalues[x]),
        "se_kesisim_HC3": float(hc3.bse[0]), "se_egim_HC3": float(hc3.bse[1]),
        "p_kesisim_HC3": float(hc3.pvalues[0]),
        "r2": float(m.rsquared), "r2_adj": float(m.rsquared_adj),
        "rmse_tl": float(np.sqrt(m.mse_resid)),
    }


def run_regressions(rows: list[dict]) -> dict:
    import numpy as np
    import pandas as pd
    import statsmodels.api as sm

    df = pd.DataFrame(rows)
    main = df[df["dahil_ana"]].copy()
    wide = df[df["dahil_genis"]].copy()
    res: dict = {"n_ana": int(len(main))}

    m_main, res["ana"] = ols(main, label="Sağlık Bakanlığı, tek binalı yeni hastane (bütçe finansmanlı şehir hastaneleri dahil)")
    infl = m_main.get_influence()
    main["artik"] = m_main.resid
    main["std_artik"] = infl.resid_studentized_external
    main["cook_d"] = infl.cooks_distance[0]
    res["ana"]["etkili_gozlemler"] = [
        {"proje_no": r.proje_no, "proje_adi": r.proje_adi, "yatak": int(r.yatak),
         "tutar_tl": int(r.proje_tutari_tl), "std_artik": round(float(r.std_artik), 2),
         "cook_d": round(float(r.cook_d), 3)}
        for r in main.itertuples() if r.cook_d > 4 / len(main) or abs(r.std_artik) > 2.5]

    rob = {}
    keep = main[(main["cook_d"] <= 4 / len(main)) & (main["std_artik"].abs() <= 2.5)]
    _, rob["aykiri_haric"] = ols(keep, label="Cook D > 4/n ve |stud. artık| > 2,5 hariç")
    gen = main[~main["sehir_hastanesi"] & (main["yatak"] < 1000)]
    _, rob["yalniz_genel"] = ols(gen, label="Şehir hastaneleri ve ≥1000 yatak hariç (genel hastane sınıfı)")
    new = main[main["baslama"] >= 2025]
    if len(new) >= 5:
        _, rob["yeni_2025_2026"] = ols(new, label="Başlama yılı 2025–2026 (ihale öncesi tahmini maliyetler)")
    old = main[main["baslama"] <= 2024]
    _, rob["baslama_2024_oncesi"] = ols(old, label="Başlama yılı ≤2024 (çoğu ihale edilmiş, güncellenmiş maliyet)")
    _, rob["universite_dahil"] = ols(wide, label="Üniversite hastaneleri de dahil")
    formula = main["tl_m2"].isin([60_000, 70_000])
    _, rob["formul_disi"] = ols(main[~formula], label="TL/m² tam 60.000 ya da 70.000 olan (formülle hesaplanmış) satırlar hariç")
    ekb = df[(df["kurum_turu"] == "bakanlik") & df["dislama_nedeni"].str.startswith("ek bina")
             & df["alan_m2"].notna() & df["proje_tutari_tl"].notna()]
    _, rob["ek_bina_dahil"] = ols(pd.concat([main, ekb]), label="Ek bina projeleri de dahil")

    # log-log: ln(tutar) = a + b ln(yatak)  (b<1 → ölçek ekonomisi)
    X = sm.add_constant(np.log(main["yatak"].astype(float)))
    ml = sm.OLS(np.log(main["proje_tutari_tl"].astype(float)), X).fit()
    ci = ml.conf_int()
    rob["log_log"] = {"etiket": "ln(tutar) = a + b·ln(yatak)", "n": int(ml.nobs),
                      "a": float(ml.params["const"]), "b_esneklik": float(ml.params["yatak"]),
                      "se_b": float(ml.bse["yatak"]), "ci95_b": [float(ci.loc["yatak", 0]), float(ci.loc["yatak", 1])],
                      "r2": float(ml.rsquared)}
    # alan üzerinden: tutar = c0 + c1·m²  ve  m² = A0 + a·yatak
    _, rob["tutar_alan"] = ols(main, x="alan_m2", label="tutar = c0 + c1·kapalı alan (m²)",
                                names=("c0_tl", "c1_tl_per_m2"))
    _, rob["alan_yatak"] = ols(main, x="yatak", y="alan_m2", label="kapalı alan (m²) = A0 + a·yatak",
                                names=("A0_m2", "a_m2_per_yatak"))
    _, rob["alan_yatak_genel"] = ols(gen, x="yatak", y="alan_m2",
                                     label="kapalı alan (m²) = A0 + a·yatak, yalnız genel hastaneler",
                                     names=("A0_m2", "a_m2_per_yatak"))
    res["saglamlik"] = rob

    # birim maliyet dağılımı
    tlm2 = main["tl_m2"].astype(float)
    res["tl_m2_dagilim"] = {"medyan": float(tlm2.median()), "min": float(tlm2.min()), "max": float(tlm2.max()),
                            "q25": float(tlm2.quantile(.25)), "q75": float(tlm2.quantile(.75)),
                            "tam_70000_sayisi": int((main["tl_m2"] == 70_000).sum()),
                            "tam_60000_sayisi": int((main["tl_m2"] == 60_000).sum())}
    m2b = main["m2_yatak"].astype(float)
    res["m2_yatak_dagilim"] = {"medyan": float(m2b.median()), "min": float(m2b.min()), "max": float(m2b.max()),
                               "ortalama": float(m2b.mean()),
                               "tam_200_ya_da_250_sayisi": int(main["m2_yatak"].isin([200.0, 250.0]).sum())}
    tly = main["tl_yatak"].astype(float)
    res["tl_yatak_dagilim"] = {"medyan": float(tly.median()), "min": float(tly.min()), "max": float(tly.max()),
                               "ortalama": float(tly.mean())}
    res["_df_main"] = main
    res["_model_main"] = m_main
    res["_gen"] = gen
    return res


def plot(res: dict) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    main = res["_df_main"]
    m = res["_model_main"]
    blue, orange, ink, muted, grid = "#2a78d6", "#eb6834", "#0b0b0b", "#52514e", "#e6e5e1"
    fig, axes = plt.subplots(1, 3, figsize=(17, 5.2), dpi=150)
    for ax in axes:
        ax.set_facecolor("#fcfcfb")
        ax.grid(True, color=grid, linewidth=0.8)
        ax.set_axisbelow(True)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        for s in ("left", "bottom"):
            ax.spines[s].set_color(muted)
        ax.tick_params(colors=muted)
    fig.patch.set_facecolor("#fcfcfb")
    sehir = main["sehir_hastanesi"].values
    ax = axes[0]
    ax.scatter(main.loc[~sehir, "yatak"], main.loc[~sehir, "proje_tutari_tl"] / 1e9, s=36, color=blue,
               edgecolor="#fcfcfb", linewidth=1.5, label="Devlet / EAH / dal hastanesi", zorder=3)
    ax.scatter(main.loc[sehir, "yatak"], main.loc[sehir, "proje_tutari_tl"] / 1e9, s=36, color=orange,
               edgecolor="#fcfcfb", linewidth=1.5, label="Şehir hastanesi (bütçe)", zorder=3)
    xs = np.linspace(0, main["yatak"].max() * 1.03, 100)
    a = res["ana"]
    ax.plot(xs, (a["F0_tl"] + a["v_tl_per_yatak"] * xs) / 1e9, color=ink, linewidth=2,
            label=f"OLS: F0 = {common.fmt_tr(a['F0_tl'] / 1e6)} mn TL, v = {common.fmt_tr(a['v_tl_per_yatak'] / 1e6, 2)} mn TL/yatak")
    ax.set_xlabel("Yatak sayısı", color=ink)
    ax.set_ylabel("Proje tutarı (milyar TL, 2026 fiyatları)", color=ink)
    ax.set_title(f"2026 KYP — Sağlık Bakanlığı yeni hastane projeleri (n = {a['n']}, R² = {common.fmt_tr(a['r2'], 3)})",
                 color=ink, fontsize=10, loc="left")
    ax.legend(frameon=False, fontsize=8, loc="upper left", labelcolor=ink)
    ax.set_xlim(left=0)
    ax.set_ylim(bottom=0)
    ax = axes[1]
    fitted = m.fittedvalues / 1e9
    resid = m.resid / 1e9
    ax.axhline(0, color=muted, linewidth=1)
    ax.scatter(fitted[~sehir], resid[~sehir], s=36, color=blue, edgecolor="#fcfcfb", linewidth=1.5, zorder=3,
               label="Devlet / EAH / dal hastanesi")
    ax.scatter(fitted[sehir], resid[sehir], s=36, color=orange, edgecolor="#fcfcfb", linewidth=1.5, zorder=3,
               label="Şehir hastanesi (bütçe)")
    for r in main.itertuples():
        if r.cook_d > 4 / len(main):
            ax.annotate(r.proje_adi.replace(" Devlet Hastanesi", " DH")[:28],
                        (m.fittedvalues[r.Index] / 1e9, m.resid[r.Index] / 1e9),
                        fontsize=6.5, color=muted, xytext=(4, 3), textcoords="offset points")
    # genel hastane sınıfı yakın görünüm (şehir hastaneleri hariç, <1000 yatak)
    ax = axes[2]
    gen = res["_gen"]
    g = res["saglamlik"]["yalniz_genel"]
    ax.scatter(gen["yatak"], gen["proje_tutari_tl"] / 1e9, s=36, color=blue, edgecolor="#fcfcfb",
               linewidth=1.5, zorder=3, label="Devlet / EAH / dal hastanesi")
    xs = np.linspace(0, 1000, 50)
    ax.plot(xs, (g["F0_tl"] + g["v_tl_per_yatak"] * xs) / 1e9, color=ink, linewidth=2,
            label=f"OLS: F0 = {common.fmt_tr(g['F0_tl'] / 1e6)} mn TL "
                  f"(%95 GA {common.fmt_tr(g['ci95_kesisim'][0] / 1e6)}…{common.fmt_tr(g['ci95_kesisim'][1] / 1e6)}), "
                  f"v = {common.fmt_tr(g['v_tl_per_yatak'] / 1e6, 2)} mn TL/yatak")
    ax.set_xlim(0, 1000)
    ax.set_ylim(bottom=0)
    ax.set_xlabel("Yatak sayısı", color=ink)
    ax.set_ylabel("Proje tutarı (milyar TL)", color=ink)
    ax.set_title(f"Yalnız genel hastane sınıfı (n = {g['n']}, R² = {common.fmt_tr(g['r2'], 3)})",
                 color=ink, fontsize=10, loc="left")
    ax.legend(frameon=False, fontsize=7.5, loc="upper left", labelcolor=ink)
    ax = axes[1]
    ax.set_xlabel("Tahmin (milyar TL)", color=ink)
    ax.set_ylabel("Artık (milyar TL)", color=ink)
    ax.set_title("Artıklar (etiketli: Cook D > 4/n)", color=ink, fontsize=10, loc="left")
    ax.legend(frameon=False, fontsize=8, loc="lower left", labelcolor=ink)
    fig.text(0.01, 0.005, "Kaynak: SBB, 2026 Yılı Kamu Yatırım Programı (RG 15.01.2026/33138 Mük.), Sağlık sektörü "
             "s. 331–337. Tutarlar 2026 fiyatlarıyla, TL.", fontsize=7, color=muted)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    OUT_PNG.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT_PNG, facecolor=fig.get_facecolor())
    plt.close(fig)


# ---------------------------------------------------------------- ana akış

def main() -> None:
    import pandas as pd

    ensure_downloads()
    pdf_path = RAW_DIR / KYP_PDF
    pages = find_health_pages(pdf_path)
    print(f"sağlık sektörü sayfaları (PDF): {pages[0] + 1}–{pages[-1] + 1} ({len(pages)} sayfa)")
    rows, footnotes = parse_health_sector(pdf_path, pages)
    for r in rows:
        classify(r)
    cols = ["kurum", "kurum_turu", "bolum", "alt_bolum", "proje_no", "ust_proje_no", "proje_adi", "yeri",
            "karakteristik", "baslama", "bitis", *NUM_FIELDS, "finansman_notu", "dipnotlar", "dipnot_metni",
            "pdf_sayfa", "basili_sayfa", "yatak", "alan_m2", "m2_yatak", "tl_yatak", "tl_m2", "sehir_hastanesi",
            "hastane_satiri", "dahil_ana", "dahil_genis", "dislama_nedeni"]
    df = pd.DataFrame(rows)[cols]
    for c in ["baslama", "bitis", "yatak", "alan_m2", "tl_yatak", "tl_m2", "basili_sayfa", *NUM_FIELDS]:
        df[c] = df[c].astype("Int64")
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_CSV, index=False, encoding="utf-8")
    print(f"{len(df)} satır -> {OUT_CSV.relative_to(common.ROOT)}")

    # Ayrıştırma kontrolü: kurum toplamlarını program özet tablosuyla karşılaştır
    main_rows = df[df["ust_proje_no"] == ""]
    chk = (main_rows.groupby("kurum")[["proje_tutari_tl", "odenek_2026_tl"]].sum()
           .loc[lambda d: d.index.str.startswith("SAĞLIK BAKANLIĞI")])
    print(chk)

    res = run_regressions(rows)
    plot(res)
    main_df = res.pop("_df_main")
    res.pop("_model_main")
    res.pop("_gen")
    a = res["ana"]
    v = a["v_tl_per_yatak"]
    lo = M2_PER_BED_MIN * min(TEBLIG_TL_M2.values())
    hi = M2_PER_BED_MIN * TEBLIG_TL_M2["V-C (>=400)"]
    out = {
        "kaynak": {"belge": "2026 Yılı Kamu Yatırım Programı (SBB)", "dosya": f"data/raw/yatirim/{KYP_PDF}",
                   "sha256": common.sha256(pdf_path), "saglik_sektoru_pdf_sayfalari": [pages[0] + 1, pages[-1] + 1],
                   "fiyat_bazi": "2026 yılı fiyatları, TL (program açıklaması, PDF s. 26 = basılı s. 1)",
                   "birim": "TL (Bin TL değil)"},
        "model": "proje_tutari_tl = F0 + v · yatak (OLS, statsmodels)",
        "ornekler": {"ana": a["n"], "aday_hastane_satiri": int(df["hastane_satiri"].sum())},
        **res,
        "degerlendirme": {
            "F0_pozitif": a["F0_tl"] > 0,
            "F0_anlamli_yuzde5": a["p_kesisim"] < 0.05,
            "F0_tum_tanimlarda_anlamsiz": all(r.get("p_kesisim", 1) >= 0.05 for k, r in res["saglamlik"].items()
                                               if "F0_tl" in r),
            "F0_kullanilabilir": bool(a["F0_tl"] > 0 and a["p_kesisim"] < 0.05),
            "aciklama": ("Kesişim (F0) sıfırdan istatistiksel olarak ayırt edilemiyor; proje tutarları yatak sayısıyla "
                         "orantılı (log-log esneklik ≈ 1). Tutarların önemli kısmı plan normlarıyla (alan × birim fiyat) "
                         "üretildiğinden sabit maliyet bu veriden tanımlanamıyor → F_sabit varsayım olarak seçilmeli. "
                         f"Eğim v makul: yatak başı ≈ {common.fmt_tr(res['saglamlik']['alan_yatak']['a_m2_per_yatak'])} m² × "
                         f"≈ {common.fmt_tr(res['saglamlik']['tutar_alan']['c1_tl_per_m2'])} TL/m² (KDV dahil)."),
        },
        "karsilastirma": {
            "teblig_yatak_basi_insaat_tl": [lo, hi],
            "aciklama": "130 m²/yatak × 40.500–48.750 TL/m² (Bölüm 3; KDV, altyapı, donanım hariç)",
            "v_oran_teblig_ust": v / hi, "v_oran_teblig_alt": v / lo,
        },
        "ana_orneklem": [
            {k: (None if pd.isna(getattr(r, k)) else (int(getattr(r, k)) if k in ("yatak", "alan_m2",
             "proje_tutari_tl", "baslama", "bitis", "basili_sayfa") else getattr(r, k)))
             for k in ("proje_no", "proje_adi", "yeri", "yatak", "alan_m2", "proje_tutari_tl", "baslama", "bitis",
                       "sehir_hastanesi", "pdf_sayfa", "basili_sayfa")}
            for r in main_df.itertuples()],
    }

    def clean(o):
        if isinstance(o, dict):
            return {k: clean(v) for k, v in o.items()}
        if isinstance(o, list):
            return [clean(v) for v in o]
        if hasattr(o, "item"):
            return o.item()
        return o

    OUT_JSON.write_text(json.dumps(clean(out), ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: clean(res[k]) for k in ("ana",)}, ensure_ascii=False, indent=1))
    for k, r in res["saglamlik"].items():
        print(k, {kk: (round(vv, 4) if isinstance(vv, float) else vv) for kk, vv in r.items()
                  if kk in ("n", "F0_tl", "v_tl_per_yatak", "c0_tl", "c1_tl_per_m2", "A0_m2", "a_m2_per_yatak",
                            "se_kesisim", "ci95_kesisim", "p_kesisim", "r2", "b_esneklik", "ci95_b")})


if __name__ == "__main__":
    main()
