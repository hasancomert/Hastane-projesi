"""Bağımsız kısıt denetleyicisi (şartname 4.6, 10): her çözümde çalışır, sonucu çözüme eklenir.

Denetlenenler: her talep tam atanmış (Σx = 1), yalnız R içindeki yaylar üzerinden (yol mesafesi koordinatlardan YENİDEN
hesaplanır), atama yalnız açık hastanelere, hücre başına en fazla 1 hastane, sınıf açık ve yatak sınıf aralığında,
kapasite (atanan talep ≤ yatak), kilitler, hastane sayısı sınırları, bütçe. Amaç değeri ham parametrelerden SIFIRDAN
yeniden hesaplanır (Instance'ın maliyet vektörleri kullanılmaz) ve çözücünün değeriyle karşılaştırılır.
"""
from __future__ import annotations

import math

import numpy as np

from . import distance
from .instance import Instance, Solution
from .milp import BUDGET_EPS

SHARE_TOL = 1e-5        # atama payı toplamı
CAP_REL_TOL = 1e-6      # kapasite / sınıf sınırı (göreli)
OBJ_REL_TOL = 1e-5      # amaç karşılaştırması (göreli)


def _fmt(x: float, nd: int = 1) -> str:
    s = f"{x:,.{nd}f}"
    return s.replace(",", "_").replace(".", ",").replace("_", ".")


def _pv(T: float, r: float) -> float:
    return float(T) if r <= 0 else (1.0 - (1.0 + r) ** (-T)) / r


def recompute_costs(inst: Instance, sol: Solution) -> dict:
    """Maliyetleri ham parametre değerlerinden yeniden hesaplar (TL)."""
    v = inst.params.values
    vat = v["cost.vat_rate"] if v["cost.include_vat"] else 0.0
    mult = (1.0 + vat) * (1.0 + v["cost.infra_ratio"] + v["cost.equipment_ratio"])
    pv = _pv(v["access.horizon_years"], v["access.discount_rate"])
    opex_on = bool(v["opex.include"])
    codes = ("IV-C", "V-B", "V-C", "V-D")
    s = inst.s
    # arsa: p_j = taban · (yoğunluk / medyan yoğunluk_J)^β
    if v["land.include"] and s.nJ:
        med = float(np.median(s.density))
        if med > 0:
            price = v["land.price_base_tl_m2"] * np.power(np.maximum(s.density, 0) / med, v["land.price_beta"])
        else:
            price = np.full(s.nJ, v["land.price_base_tl_m2"])
        land_bed = price * v["land.m2_per_bed"]
    else:
        land_bed = np.zeros(s.nJ)
    opening = 0.0
    for j, k, b in zip(sol.open_j, sol.cls, sol.beds):
        code = codes[int(k)]
        u = v[f"cost.unit.{code}"]
        a = v["cost.m2_per_bed.city"] if code == "V-D" else v["cost.m2_per_bed.general"]
        fixed = mult * u * v["cost.core_area_m2"] + v["cost.fixed_per_hospital"]
        var = mult * u * a + land_bed[int(j)]
        if opex_on:
            fixed += pv * v["opex.fixed_per_hospital_year"]
            var += pv * v["opex.per_bed_year"]
        opening += fixed + var * float(b)
    lam = pv * v["access.hospital_visits_per_capita"] * v["access.round_trip_factor"] * (
        v["access.cost_tl_per_km"] + v["access.value_of_time_tl_per_h"] / v["access.speed_kmh"])
    used = np.flatnonzero(sol.x > 0)
    d = _road_km(inst, used)
    access = float(lam * np.sum(s.w[s.arc_i[used]] * np.power(d, v["access.alpha"]) * sol.x[used]))
    return {"opening_tl": opening, "access_tl": access, "lambda": lam}


def _road_km(inst: Instance, arcs: np.ndarray) -> np.ndarray:
    """Yay mesafelerini koordinatlardan yeniden hesaplar (yol km)."""
    s = inst.s
    if s.demand_xy is None or len(arcs) == 0:
        return s.arc_d[arcs]
    a = s.demand_xy[s.arc_i[arcs]]
    b = s.cand_xy[s.arc_j[arcs]]
    e = np.hypot(a[:, 0] - b[:, 0], a[:, 1] - b[:, 1]) / 1000.0
    return distance.road_km_pairs(e, s.cell_km, s.circ)


def check(inst: Instance, sol: Solution, *, solver_objective_tl: float | None = None) -> dict:
    """Tüm kısıtları denetler; {'ok', 'violations', 'checks', amaç karşılaştırması} döndürür."""
    s = inst.s
    viol: list[dict] = []
    checks: dict[str, bool] = {}

    def fail(name: str, msg: str, count: int = 1, worst: float | None = None) -> None:
        viol.append({"check": name, "message": msg, "count": int(count),
                     "worst": None if worst is None else float(worst)})

    # --- biçim
    x = np.asarray(sol.x, dtype=float)
    J = np.asarray(sol.open_j, dtype=np.int64)
    K = np.asarray(sol.cls, dtype=np.int64)
    B = np.asarray(sol.beds, dtype=float)
    shape_ok = (x.shape == (inst.nA,) and J.shape == K.shape == B.shape and np.isfinite(x).all()
                and np.isfinite(B).all() and ((J >= 0) & (J < inst.nJ)).all() and ((K >= 0) & (K < inst.nK)).all())
    checks["bicim"] = bool(shape_ok)
    if not shape_ok:
        fail("bicim", "Çözüm dizilerinin boyutu ya da değerleri geçersiz.")
        return {"ok": False, "violations": viol, "checks": checks}

    # --- payların aralığı
    bad = (x < -SHARE_TOL) | (x > 1 + SHARE_TOL)
    checks["pay_araligi"] = not bad.any()
    if bad.any():
        fail("pay_araligi", "Atama payı [0, 1] dışında.", int(bad.sum()), float(np.abs(x[bad]).max()))

    # --- tam atama
    tot = np.bincount(s.arc_i, x, minlength=inst.nI)
    dev = np.abs(tot - 1.0)
    checks["tam_atama"] = bool((dev <= SHARE_TOL).all())
    if not checks["tam_atama"]:
        bad_i = np.flatnonzero(dev > SHARE_TOL)
        fail("tam_atama", f"{len(bad_i)} talep hücresinin talebi tam atanmamış "
                          f"({_fmt(float(s.w[bad_i].sum()), 0)} kişi).", len(bad_i), float(dev.max()))

    # --- mesafe (R) — koordinatlardan yeniden
    used = np.flatnonzero(x > SHARE_TOL)
    d = _road_km(inst, used)
    far = d > s.R_km + 1e-6
    mism = np.abs(d - s.arc_d[used]) > 1e-6
    checks["mesafe_R"] = not far.any()
    checks["mesafe_tutarli"] = not mism.any()
    if far.any():
        fail("mesafe_R", f"{int(far.sum())} atama R = {_fmt(s.R_km)} km'den uzak.", int(far.sum()), float(d.max()))
    if mism.any():
        fail("mesafe_tutarli", "Yay mesafesi koordinatlardan hesaplanan yol mesafesiyle uyuşmuyor.", int(mism.sum()),
             float(np.abs(d - s.arc_d[used]).max()))

    # --- hücre başına en fazla 1 hastane; atama yalnız açık hastanelere
    uniq = len(np.unique(J)) == len(J)
    checks["hucre_basina_1"] = bool(uniq)
    if not uniq:
        fail("hucre_basina_1", "Aynı hücrede birden fazla hastane var.")
    is_open = np.zeros(inst.nJ, dtype=bool)
    is_open[J] = True
    to_closed = used[~is_open[s.arc_j[used]]]
    checks["acik_hastaneye"] = len(to_closed) == 0
    if len(to_closed):
        fail("acik_hastaneye", f"{len(to_closed)} atama kapalı hücreye yapılmış.", len(to_closed),
             float(x[to_closed].max()))

    # --- sınıf ve yatak sınırları
    on = inst.class_on[K]
    checks["sinif_acik"] = bool(on.all())
    if not on.all():
        fail("sinif_acik", "Kapalı bir boyut sınıfı kullanılmış.", int((~on).sum()))
    L, U = inst.L[K], inst.U[K]
    lo = B < L - CAP_REL_TOL * np.maximum(L, 1)
    hi = B > U + CAP_REL_TOL * np.maximum(U, 1)
    checks["sinif_sinirlari"] = not (lo.any() or hi.any())
    if lo.any() or hi.any():
        fail("sinif_sinirlari", f"{int(lo.sum() + hi.sum())} hastanenin yatak sayısı sınıf aralığı dışında.",
             int(lo.sum() + hi.sum()))

    # --- kapasite
    load = np.bincount(s.arc_j[used], inst.q[s.arc_i[used]] * x[used], minlength=inst.nJ)
    beds_full = np.zeros(inst.nJ)
    beds_full[J] = B
    over = load - beds_full
    cap_bad = over > CAP_REL_TOL * np.maximum(beds_full, 1.0) + 1e-6
    checks["kapasite"] = not cap_bad.any()
    if cap_bad.any():
        fail("kapasite", f"{int(cap_bad.sum())} hastanede atanan talep yatak sayısını aşıyor.", int(cap_bad.sum()),
             float(over.max()))

    # --- kilitler
    miss = inst.lock_open & ~is_open
    forb = inst.lock_closed & is_open
    checks["kilitler"] = not (miss.any() or forb.any())
    if miss.any():
        fail("kilitler", f"{int(miss.sum())} 'aç' kilitli hücrede hastane yok.", int(miss.sum()))
    if forb.any():
        fail("kilitler", f"{int(forb.sum())} yasaklı hücrede hastane açılmış.", int(forb.sum()))

    # --- sayı
    n = len(J)
    cnt_ok = n >= inst.count_min and (inst.count_max is None or n <= inst.count_max)
    checks["hastane_sayisi"] = bool(cnt_ok)
    if not cnt_ok:
        fail("hastane_sayisi", f"Hastane sayısı {n}, izinli aralık [{inst.count_min}, "
                               f"{inst.count_max if inst.count_max is not None else '∞'}].")

    # --- maliyetler (sıfırdan) ve bütçe
    rc = recompute_costs(inst, sol)
    if inst.budget is not None:
        b_ok = rc["opening_tl"] <= inst.budget * (1 + 1e-7) + 1.0
        checks["butce"] = bool(b_ok)
        if not b_ok:
            fail("butce", f"Açılış maliyeti {_fmt(rc['opening_tl'] / 1e9, 2)} milyar TL, bütçe "
                          f"{_fmt(inst.budget / 1e9, 2)} milyar TL.", 1, rc["opening_tl"] - inst.budget)
    if inst.mode == "butce":
        obj = rc["access_tl"]
        obj_model = rc["access_tl"] + BUDGET_EPS * rc["opening_tl"]
    else:
        obj = obj_model = rc["opening_tl"] + rc["access_tl"]
    out = {"ok": not viol, "violations": viol, "checks": checks,
           "objective_recomputed_tl": obj, "opening_recomputed_tl": rc["opening_tl"],
           "access_recomputed_tl": rc["access_tl"], "lambda_recomputed": rc["lambda"]}
    if solver_objective_tl is not None and math.isfinite(solver_objective_tl):
        rel = abs(obj_model - solver_objective_tl) / max(abs(obj_model), 1.0)
        out["objective_solver_tl"] = float(solver_objective_tl)
        out["objective_rel_diff"] = float(rel)
        checks["amac_tutarli"] = rel <= OBJ_REL_TOL
        if rel > OBJ_REL_TOL:
            viol.append({"check": "amac_tutarli", "message": "Yeniden hesaplanan amaç değeri çözücününkiyle uyuşmuyor.",
                         "count": 1, "worst": float(rel)})
            out["ok"] = False
    # kendi iç tutarlılığı: Instance vektörleriyle hesap
    inner = sol.opening_tl(inst) + sol.access_tl(inst)
    full = rc["opening_tl"] + rc["access_tl"]
    rel2 = abs(inner - full) / max(abs(full), 1.0)
    out["vector_rel_diff"] = float(rel2)
    checks["maliyet_vektorleri"] = rel2 <= OBJ_REL_TOL
    if rel2 > OBJ_REL_TOL:
        viol.append({"check": "maliyet_vektorleri", "message": "Model maliyet vektörleri parametrelerle uyuşmuyor.",
                     "count": 1, "worst": float(rel2)})
        out["ok"] = False
    out["n_checks"] = len(checks)
    out["summary_tr"] = ("Tüm kısıtlar sağlanıyor ({} denetim).".format(len(checks)) if out["ok"]
                         else "Kısıt ihlali: " + "; ".join(v["message"] for v in viol))
    return out
