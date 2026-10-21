"""Mesafeye bağlı dolambaç katsayısı profili k(d) (öğrenci kararı, docs/KARARLAR.md #7).

Girdi : data/reference/circuity_ankara.csv (pipeline/15_circuity.py çıktısı; OSM yol ağı ölçümü)
Çıktı : data/reference/params/dolambac_profil.yaml
        access.circuity_mode, access.circuity_profile, access.circuity_scale

Yöntem: her kuş uçuşu mesafe bandının nüfus ağırlıklı ortalama katsayısı (pw_mean), bandın ortalama kuş uçuşu
mesafesine (mean_euclid_km) yerleştirilir. Örnekleme gürültüsü yüzünden oluşan küçük artışlar, çift sayısıyla
ağırlıklı izotonik (artmayan) regresyonla düzeltilir. Ara mesafelerde doğrusal enterpolasyon, uçlarda sabit değer.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import yaml

from pipeline import common

IN_CSV = common.REFERENCE / "circuity_ankara.csv"
OUT_PARAM = common.PARAMS_DIR / "dolambac_profil.yaml"
SRC = ["arazi_osm_circuity_hesap", "arazi_osm_osm_turkey_pbf"]


def pava_nonincreasing(y: np.ndarray, w: np.ndarray) -> np.ndarray:
    """Ağırlıklı izotonik regresyon (artmayan), havuzlama yöntemi."""
    blocks = [[float(v), float(wt), 1] for v, wt in zip(y, w)]  # [ortalama, ağırlık, eleman sayısı]
    i = 0
    while i < len(blocks) - 1:
        if blocks[i][0] < blocks[i + 1][0]:  # artış var: havuzla
            m0, w0, n0 = blocks[i]
            m1, w1, n1 = blocks[i + 1]
            blocks[i] = [(m0 * w0 + m1 * w1) / (w0 + w1), w0 + w1, n0 + n1]
            del blocks[i + 1]
            i = max(i - 1, 0)
        else:
            i += 1
    return np.concatenate([[m] * n for m, _, n in blocks])


def profile() -> tuple[list[list[float]], pd.DataFrame]:
    tab = pd.read_csv(IN_CSV, comment="#")
    b = tab[tab["group_type"] == "band"].copy()
    b = b.sort_values("mean_euclid_km").reset_index(drop=True)
    b["k_iso"] = pava_nonincreasing(b["pw_mean"].to_numpy(), b["n_pairs"].to_numpy())
    pts = [[round(float(d), 2), round(float(k), 4)] for d, k in zip(b["mean_euclid_km"], b["k_iso"])]
    return pts, b


def circuity_at(d_km, pts) -> np.ndarray:
    """k(d): profil noktaları arasında doğrusal enterpolasyon, uçlarda sabit."""
    xs = np.array([p[0] for p in pts])
    ks = np.array([p[1] for p in pts])
    return np.interp(np.asarray(d_km, dtype=float), xs, ks)


def main() -> None:
    pts, b = profile()
    rows = "; ".join(f"{common.fmt_tr(r.mean_euclid_km, 1)} km → {common.fmt_tr(r.pw_mean, 3)}"
                     + (f" (izotonik {common.fmt_tr(r.k_iso, 3)})" if abs(r.k_iso - r.pw_mean) > 1e-9 else "")
                     for r in b.itertuples())
    params = [
        {
            "key": "access.circuity_mode",
            "label": "Dolambaç katsayısı modu",
            "value": "mesafeye_bagli",
            "unit": "seçenek",
            "status": "model_secimi",
            "source_ids": SRC,
            "rationale": (
                "Öğrenci kararı (2026-10-21, docs/KARARLAR.md #7). Ankara OSM ölçümünde katsayı mesafe kısaldıkça "
                "artıyor (0–5 km bandında 1,38, 40 km üstünde ≈1,19); tek katsayı (1,22) kısa şehir içi yolculukları "
                "≈%11 eksik ölçer. 'sabit' modunda access.circuity kullanılır."
            ),
            "alternatives": [{"label": "Tek katsayı (access.circuity)", "value": "sabit", "status": "model_secimi",
                              "source_ids": SRC}],
        },
        {
            "key": "access.circuity_profile",
            "label": "Mesafeye bağlı dolambaç profili k(d)",
            "value": pts,
            "unit": "[kuş uçuşu km, katsayı] çiftleri",
            "status": "hesap",
            "source_ids": SRC,
            "derivation": (
                "data/reference/circuity_ankara.csv bant satırları: bandın ortalama kuş uçuşu mesafesi → nüfus "
                f"ağırlıklı ortalama katsayı: {rows}. Artmayan ağırlıklı izotonik regresyon (ağırlık = çift sayısı); "
                "ara mesafelerde doğrusal enterpolasyon, ilk noktadan kısa ve son noktadan uzun mesafelerde uç "
                "değer. Üretim: pipeline/21_circuity_profile.py."
            ),
            "notes": (
                "Yol mesafesi d_ij = kuş uçuşu_ij × k(kuş uçuşu_ij) × access.circuity_scale. Aynı hücre içi "
                "yolculukta kuş uçuşu mesafe hücre içi ortalama mesafe olarak alınır (Faz 2 ızgara betiği)."
            ),
        },
        {
            "key": "access.circuity_scale",
            "label": "Dolambaç profili çarpanı",
            "value": 1.0,
            "unit": "çarpan (boyutsuz)",
            "status": "model_secimi",
            "source_ids": [],
            "rationale": (
                "1,0 = ölçülen profil aynen kullanılır. Arayüzde duyarlılık için kaydırıcıdır (ör. 1,1 = tüm yol "
                "mesafeleri %10 uzun)."
            ),
            "range": [0.8, 1.3],
        },
    ]
    OUT_PARAM.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PARAM, "w", encoding="utf-8") as f:
        f.write("# Alan: arazi-osm — mesafeye bağlı dolambaç profili. Üreten: pipeline/21_circuity_profile.py "
                "(elle düzenlemeyin)\n")
        yaml.safe_dump(params, f, allow_unicode=True, sort_keys=False, width=110)
    print("profil:", pts)
    for d in (1, 3, 5, 10, 20, 30, 50, 100):
        print(f"  k({d} km) = {circuity_at(d, pts):.4f}")


if __name__ == "__main__":
    main()
