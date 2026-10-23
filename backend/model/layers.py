"""Harita katmanları ve hastane kartı ayrıntıları (şartname 6.2, 6.4, 7.4–7.5) — çözümden türetilen, ek çözüm
gerektirmeyen veriler. solve() ve manuel değerlendirme (backend/app/task_evaluate.py) sonuca 'layers' olarak ekler;
önceden çözülmüş dosyalara pipeline/34_scenarios.py enrich ekler.

Hücre dizilerinin sırası:
  * access.*            ızgaranın TÜM hücreleri, ızgara sırasıyla (GET /api/grid/{km} GeoJSON'undaki özellik sırası =
                        backend.model.grid_io ızgara sırası; 'grid_index' bu sıradaki konum);
  * demand.*            modelin talep hücreleri (nüfusu > 0; duals.demand_cell ile aynı sıra);
  * candidates.*        modelin aday hücreleri (yapının aday sırası; hastanelerin 'cand_pos' alanı bu sıradaki konum).

Erişim ısı haritası (6.2): her hücrenin EN YAKIN açık hastaneye yol mesafesi — modelin mesafe tanımıyla aynı (kuş uçuşu
× dolambaç k(e) × ölçek; aynı hücrede hücre içi ortalama mesafe; backend/model/distance.py). Yol mesafesi kuş uçuşuyla
arttığından (tests/test_phase2_grids.py) en yakın hastane kuş uçuşu en yakın olandır. Atama ile karıştırılmamalı:
kapasite yüzünden bir hücre en yakın hastaneye değil başkasına atanmış olabilir (assignment.km atanan hastaneye olan
mesafedir).

R halkaları (6.2): R yol km'sidir; haritada çizilecek kuş uçuşu yarıçap dolambaç profilinin tersidir
(Circuity.euclid_for_road_km). Varsayılan profilde R = 30 km yol ≈ 25 km kuş uçuşu: R km'lik bir daire %20 büyük
çizilirdi.
"""
from __future__ import annotations

import numpy as np

from . import distance
from .instance import Instance, Solution

ROUND_KM = 3            # gösterim katmanında mesafe yuvarlaması (1 m); KPI'lar yuvarlanmamış yay mesafelerinden


def r_rings(inst: Instance) -> dict:
    """R'nin yol ve kuş uçuşu karşılığı (harita halkaları için)."""
    s = inst.s
    R = float(s.R_km)
    e = float(s.circ.euclid_for_road_km(R))
    return {"R_km": R, "R_crowfly_km": round(e, ROUND_KM), "factor": round(R / e, 4) if e > 0 else None,
            "note_tr": f"R = {_tr(R)} km yol mesafesidir; haritadaki halka yarıçapı kuş uçuşu {_tr(round(e, 1))} km "
                       f"(dolambaç profili ile ters çevrildi)."}


def _tr(x: float) -> str:
    return f"{x:g}".replace(".", ",")


def size_info(inst: Instance, sol: Solution) -> list[dict]:
    """Hastane başına boyut ayrıntısı (7.5 "neden bu boyut?"): sınıfın yatak aralığı, yatak sınıf sınırında mı,
    yatak başı (değişken) ve hastane başı (sabit) açılış maliyeti, komşu sınıfların eşik değerindeki açılış maliyeti.
    Sınıf maliyet sıçraması tebliğ birim fiyatlarından gelir (data/reference/parameters.json)."""
    out = []
    K = inst.nK
    for j, k, b in zip(sol.open_j, sol.cls, sol.beds):
        j, k, b = int(j), int(k), float(b)
        L, U = float(inst.L[k]), float(inst.U[k])
        tol = 1e-6 * max(1.0, U)
        cur = float(inst.fixed[j, k] + inst.var[j, k] * b)
        d = {"class_min_beds": L, "class_max_beds": U,
             "at_bound": "ust" if b >= U - tol else ("alt" if b <= L + tol else None),
             "fixed_tl": float(inst.fixed[j, k]), "per_bed_tl": float(inst.var[j, k]), "opening_tl": cur}
        for name, kk, beds in (("next_class", k + 1, None), ("prev_class", k - 1, None)):
            if 0 <= kk < K and inst.class_on[kk] and inst.y_ub[j, kk]:
                bb = float(inst.L[kk]) if name == "next_class" else float(inst.U[kk])
                bb = max(bb, float(inst.L[kk]))
                d[name] = {"class": inst.classes[kk].code, "beds": bb,
                           "opening_tl": float(inst.fixed[j, kk] + inst.var[j, kk] * bb),
                           "per_bed_tl": float(inst.var[j, kk])}
        out.append(d)
    return out


def map_layers(inst: Instance, sol: Solution) -> dict:
    """Harita katmanları (bkz. modül notu). Oyuncak örneklerde ızgara olmadığından erişim katmanı yalnız talep
    hücreleri içindir."""
    s = inst.s
    toy = s.source == "toy"
    hxy = s.cand_xy[sol.open_j] if s.cand_xy is not None else None
    out: dict = {"grid_km": float(s.cell_km), "rings": r_rings(inst)}
    R = float(s.R_km)
    if hxy is not None and len(hxy):
        pts = s.demand_xy if toy else s.grid.xy
        idx, e_km = distance.nearest_euclid(pts, hxy)
        d = distance.road_km_pairs(e_km, s.cell_km, s.circ)
        d = np.round(d, ROUND_KM)
        out["access"] = {"order": "demand" if toy else "grid", "n": int(len(pts)),
                         "nearest_km": d.tolist(), "nearest_hospital": idx.astype(int).tolist(),
                         "beyond_R_cells": int((d > R + 1e-9).sum()),
                         "note_tr": "En yakın açık hastaneye yol mesafesi (atanan hastaneye değil)."}
        if not toy:
            dem_d = d[s.demand_cells]
            out["access"]["beyond_R_population"] = float(s.w[dem_d > R + 1e-9].sum())
    # talep hücreleri: nüfus, yatak talebi, hizmet bölgesi (en büyük paylı hastane)
    hpos = np.full(inst.nJ, -1, dtype=np.int64)
    hpos[sol.open_j] = np.arange(sol.n_open)
    a = np.flatnonzero((sol.x > 0) & (hpos[s.arc_j] >= 0))
    prim = np.full(inst.nI, -1, dtype=np.int64)
    share = np.zeros(inst.nI)
    if len(a):
        ia, xa = s.arc_i[a], sol.x[a]
        order = np.lexsort((-xa, ia))               # her talep hücresinde en büyük pay önce
        first = np.ones(len(order), dtype=bool)
        first[1:] = ia[order][1:] != ia[order][:-1]
        sel = order[first]
        prim[ia[sel]] = hpos[s.arc_j[a[sel]]]
        share[ia[sel]] = xa[sel]
    out["demand"] = {"cell_id": [str(c) for c in s.demand_ids],
                     "grid_index": [int(c) for c in s.demand_cells],
                     "population": s.w.astype(float).tolist(),
                     "demand_beds": inst.q.astype(float).tolist(),
                     "service_hospital": prim.tolist(),
                     "service_share": np.round(share, 6).tolist(),
                     "note_tr": "service_hospital: talebin en büyük payını alan hastane (hospitals dizisindeki sıra); "
                                "service_share < 1 ise talep birden fazla hastaneye bölünmüştür (assignment)."}
    open_mask = np.zeros(inst.nJ, dtype=bool)
    open_mask[sol.open_j] = True
    out["candidates"] = {"cell_id": [str(c) for c in s.cand_ids],
                         "grid_index": [int(c) for c in s.cand_cells],
                         "origin": [int(o) for o in s.cand_origin],
                         "locked_open": np.flatnonzero(inst.lock_open).astype(int).tolist(),
                         "locked_closed": np.flatnonzero(inst.lock_closed).astype(int).tolist(),
                         "open": open_mask.astype(int).tolist(),
                         "origin_tr": {"0": "aday kuralı", "1": "kapsama onarımı", "2": "kullanıcı 'aç' kilidi"}}
    return out


def flow_population(inst: Instance, sol: Solution, arcs: np.ndarray) -> list[float]:
    """Verilen yaylarda taşınan nüfus (kişi = hücre nüfusu × pay): hasta akışı yayları (ArcLayer) için."""
    s = inst.s
    return (s.w[s.arc_i[arcs]] * sol.x[arcs]).astype(float).tolist()
