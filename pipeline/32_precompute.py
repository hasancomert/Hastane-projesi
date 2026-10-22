"""Faz 3 — önceden çözülen çıktılar: varsayılan çözümler, sınır kartları, p taraması (U eğrisi).

Alt komutlar (repo kökünden):
  .venv/bin/python pipeline/32_precompute.py defaults [--grids 5,3,2]   → precomputed/default_{5,3,2}km.json
  .venv/bin/python pipeline/32_precompute.py bounds   [--grids 5,3,2]   → data/processed/sinir_kartlari.json
  .venv/bin/python pipeline/32_precompute.py sweep    [--grids 3,5]     → precomputed/p_sweep_{3,5}km.json
                                                                          (önce defaults: serbest p'li çözüm gerekir)

Varsayılan çözümler: varsayılan parametreler, uzun süre sınırı ve sıkı hedef gap (DEFAULT_RUNS) — solve() bu sürelerde
Lagrange → çekirdek MIP → tam MILP yolunu izler. Aynı parametre ve yapıyla yazılmış önceki dosya varsa onun çözümü
MIP başlangıcı olarak verilir (yeniden çalıştırma çözümü kötüleştirmez). Sonuç sözlüğü eksiksiz yazılır (hastaneler, atamalar, maliyet kırılımı,
KPI'lar, denetim raporu, sertifika cümlesi, iz, atama LP'sinin dual değerleri).

p taraması (şartname 7.7; kabul ölçütü "varsayılan parametrelerde p taramasının en iyisi = serbest p'li çözüm"): sabit_p
modunda serbest optimumun çevresinde sık, uzakta seyrek p değerleri. p = n_serbest noktasında serbest çözüm MIP başlangıcı
olarak verilir (bilinen olurlu çözüm). Her nokta için açılış / erişim / toplam maliyet, alt sınır, gap ve süre saklanır;
dosyanın 'acceptance' alanı ölçütün sayısal denetimini içerir (tests/test_phase3_outputs.py aynı denetimi yapar).
Gap'i %1'i aşan noktalar (optimumdan uzak p) eldeki çözüm ve aynı p'nin çarpanlarıyla 4 kat süreyle yeniden çözülür.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

PRECOMP = ROOT / "precomputed"
BOUNDS_OUT = ROOT / "data" / "processed" / "sinir_kartlari.json"
# ızgara → (süre sınırı sn, hedef gap)
DEFAULT_RUNS = {5: (600.0, 0.001), 3: (1200.0, 0.002), 2: (1800.0, 0.002)}
SWEEP = {
    3: {"p": [40, 50, 60, 70, 75, 78, 80, 82, 84] + list(range(85, 101)) + [102, 104, 106, 108, 110, 115, 120, 130,
                                                                            140, 150],
        "time_limit": 60.0, "gap": 0.003},
    5: {"p": [35, 40, 45, 50, 55, 58, 60, 62] + list(range(64, 77)) + [78, 80, 82, 85, 90, 95, 100, 110, 120],
        "time_limit": 30.0, "gap": 0.002},
}
SWEEP_RETRY_GAP = 0.01
SWEEP_RETRY_FACTOR = 4.0
PMIN_TIME = {5: 300.0, 3: 300.0, 2: 600.0}
LSCP_TIME = {5: 60.0, 3: 60.0, 2: 600.0}


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Yazıldı: {path.relative_to(ROOT)} ({path.stat().st_size / 1e6:.1f} MB)", flush=True)


def run_defaults(grids: list[int]) -> None:
    from backend.model import instance
    from backend.model.params import Params
    from backend.model.solve import solve
    for km in grids:
        tl, gap = DEFAULT_RUNS[km]
        # aynı parametre ve yapıyla önceden yazılmış çözüm varsa MIP başlangıcı olur (yeniden çalıştırma yalnız iyileştirir)
        start, prev = None, PRECOMP / f"default_{km}km.json"
        if prev.exists():
            old = json.loads(prev.read_text(encoding="utf-8"))
            inst = instance.build_instance(km)
            if (old.get("ok") and old.get("params_hash") == Params.load({"grid.cell_km": km}).hash()
                    and old.get("structure_hash") == inst.s.key):
                start = _solution_from_result(inst, old)
                print(f"{km} km: önceki çözüm başlangıç ({old['bounds']['ub_tl'] / 1e9:.3f} milyar TL)", flush=True)
        t0 = time.perf_counter()
        r = solve(km, {}, time_limit=tl, gap=gap, compute_duals=True, start=start)
        r["precomputed"] = {"generated_by": "pipeline/32_precompute.py defaults", "generated_at": _now(),
                            "time_limit_s": tl, "target_gap": gap, "wall_s": time.perf_counter() - t0,
                            "start_from_previous": start is not None}
        b = r["bounds"]
        print(f"{km} km: {r['status']} UB {b['ub_tl'] / 1e9:.3f} LB {b['lb_tl'] / 1e9:.3f} gap {100 * b['gap']:.3f}% "
              f"({b['lb_source']}), {r['kpis']['n_hospitals']} hastane {r['kpis']['count_by_class']}, "
              f"{r['times']['total_s']:.0f} sn, denetim {r['check']['ok']}", flush=True)
        _write(PRECOMP / f"default_{km}km.json", r)


def run_bounds(grids: list[int]) -> None:
    from backend.model import bounds, instance
    out = {"generated_by": "pipeline/32_precompute.py bounds", "generated_at": _now(), "grids": {}}
    if BOUNDS_OUT.exists():
        out["grids"] = json.loads(BOUNDS_OUT.read_text(encoding="utf-8")).get("grids", {})
    for km in grids:
        inst = instance.build_instance(km)
        t0 = time.perf_counter()
        bc = bounds.bound_cards(inst, lscp_time=LSCP_TIME[km], pmin_time=PMIN_TIME[km])
        bc["time_s"] = time.perf_counter() - t0
        bc["coverage_lscp"].pop("open_pos", None)
        bc["grid_km"] = km
        bc["settings"] = {"lscp_time_s": LSCP_TIME[km], "pmin_time_s": PMIN_TIME[km], "R_km": inst.s.R_km,
                          "k_nearest": inst.s.k_nearest}
        pm = bc["p_min"]
        print(f"{km} km: |J| {bc['max_hospitals_grid']}, kapasite {bc['capacity']['with_city']} / "
              f"{bc['capacity']['without_city']}, LSCP {bc['coverage_lscp']['value']} "
              f"({'kanıtlı' if bc['coverage_lscp']['proven'] else 'kanıtsız'}), p_min {pm['value']} "
              f"(alt sınır {pm['lower_bound']}, {'kanıtlı' if pm['proven'] else pm['status']}), {bc['time_s']:.0f} sn",
              flush=True)
        out["grids"][str(km)] = bc
        out["generated_at"] = _now()
        _write(BOUNDS_OUT, out)


def _solution_from_result(inst, r):
    """Sonuç sözlüğünden Solution (hastaneler + atama listesi; yay indisleri yapıdan bulunur)."""
    from backend.model.instance import Solution
    s = inst.s
    pos = np.array([h["cand_pos"] for h in r["hospitals"]], dtype=np.int64)
    code = {c.code: k for k, c in enumerate(inst.classes)}
    cls = np.array([code[h["class"]] for h in r["hospitals"]], dtype=np.int64)
    beds = np.array([h["beds"] for h in r["hospitals"]])
    did = {str(c): i for i, c in enumerate(s.demand_ids)}
    x = np.zeros(inst.nA)
    for dc, hidx, share in zip(r["assignment"]["demand_cell"], r["assignment"]["hospital"], r["assignment"]["share"]):
        i = did[dc]
        js = s.arc_j[s.arc_ptr[i]:s.arc_ptr[i + 1]]
        x[s.arc_ptr[i] + int(np.flatnonzero(js == pos[hidx])[0])] = share
    order = np.argsort(pos)
    return Solution(pos[order], cls[order], beds[order], x, source="serbest")


def acceptance(free: dict, points: list[dict]) -> dict:
    """Kabul ölçütü: taramanın en iyisi = serbest p'li çözüm (gap'ler içinde)."""
    ok_pts = [p for p in points if p.get("ok")]
    best = min(ok_pts, key=lambda p: p["ub_tl"])
    at_free = next((p for p in ok_pts if p["p"] == free["n_hospitals"]), best)
    # tolerans: serbest çözümün, taramanın en iyi noktasının ve p = n_serbest noktasının gap'lerinin en büyüğü
    tol = max(free["gap"], best["gap"], at_free["gap"])
    lb_free, ub_free = free["lb_tl"], free["ub_tl"]
    # serbest p'li optimumun hastane sayısı dışlanamaz: LB(p) ≤ UB_serbest olan p'ler (gap içindeki düzlük)
    plateau = [p["p"] for p in ok_pts if p["lb_tl"] is not None and p["lb_tl"] <= ub_free * (1 + 1e-12)]
    return {
        "best_p": best["p"], "best_ub_tl": best["ub_tl"], "free_n": free["n_hospitals"], "free_ub_tl": ub_free,
        "free_lb_tl": lb_free, "tolerance": tol,
        "rel_diff_best_vs_free": (best["ub_tl"] - ub_free) / ub_free,
        "no_p_beats_free_lb": all(p["ub_tl"] >= lb_free * (1 - 1e-12) for p in ok_pts),
        "free_within_tol_of_best": ub_free <= best["ub_tl"] * (1 + tol),
        "best_within_tol_of_free": best["ub_tl"] <= ub_free * (1 + tol),
        "plateau_p": plateau, "free_n_in_plateau": free["n_hospitals"] in plateau,
        "passed": bool(all(p["ub_tl"] >= lb_free * (1 - 1e-12) for p in ok_pts)
                       and abs(best["ub_tl"] - ub_free) <= tol * ub_free and free["n_hospitals"] in plateau),
    }


def run_sweep(grids: list[int]) -> None:
    from backend.model import instance
    from backend.model.params import Params
    from backend.model.solve import solve
    for km in grids:
        dpath = PRECOMP / f"default_{km}km.json"
        if not dpath.exists():
            raise SystemExit(f"{dpath.relative_to(ROOT)} yok: önce 'defaults' alt komutunu çalıştırın.")
        d = json.loads(dpath.read_text(encoding="utf-8"))
        free = {"ub_tl": d["bounds"]["ub_tl"], "lb_tl": d["bounds"]["lb_tl"], "gap": d["bounds"]["gap"],
                "n_hospitals": d["kpis"]["n_hospitals"], "opening_tl": d["costs"]["acilis"],
                "access_tl": d["costs"]["erisim"], "count_by_class": d["kpis"]["count_by_class"],
                "status": d["status"], "lb_source": d["bounds"]["lb_source"]}
        cfg = SWEEP[km]
        ps = sorted(set(cfg["p"]) | {free["n_hospitals"]})
        points = []
        t_all = time.perf_counter()
        for p in ps:
            ov = {"solver.objective_mode": "sabit_p", "solver.p_fixed": p}
            start = None
            if p == free["n_hospitals"]:
                inst = instance.build_instance(km, Params.load(ov))
                start = _solution_from_result(inst, d)
            r = solve(km, ov, time_limit=cfg["time_limit"], gap=cfg["gap"], start=start)
            tries = 1
            # optimumdan uzak p'lerde (kapasite/kapsama zorlayınca) Lagrange sınırı yavaş iyileşir: gap %1'i aşarsa
            # eldeki çözümle ve önbellekteki (aynı p'nin) çarpanlarla SWEEP_RETRY_FACTOR kat süreyle yeniden çöz
            if r.get("ok") and r["bounds"]["gap"] > SWEEP_RETRY_GAP:
                inst = instance.build_instance(km, Params.load(ov))
                r2 = solve(km, ov, time_limit=SWEEP_RETRY_FACTOR * cfg["time_limit"], gap=cfg["gap"],
                           start=_solution_from_result(inst, r))
                tries = 2
                if r2.get("ok") and r2["bounds"]["gap"] < r["bounds"]["gap"]:
                    r2["times"]["total_s"] += r["times"]["total_s"]
                    r = r2
            if not r.get("ok"):
                points.append({"p": p, "ok": False, "status": r["status"], "message": r.get("message_tr")})
                print(f"  p = {p}: {r['status']} — {r.get('message_tr')}", flush=True)
                continue
            b, c, k = r["bounds"], r["costs"], r["kpis"]
            points.append({"p": p, "ok": True, "status": r["status"], "ub_tl": b["ub_tl"], "lb_tl": b["lb_tl"],
                           "gap": b["gap"], "lb_source": b["lb_source"], "opening_tl": c["acilis"],
                           "access_tl": c["erisim"], "total_tl": c["toplam"], "count_by_class": k["count_by_class"],
                           "total_beds": k["total_beds"], "pop_weighted_mean_km": k["pop_weighted_mean_km"],
                           "time_s": r["times"]["total_s"], "solution_source": r["solution_source"],
                           "check_ok": r["check"]["ok"], "start_given": start is not None, "tries": tries,
                           "hospitals": [h["cell_id"] for h in r["hospitals"]]})
            print(f"  p = {p}: {r['status']} toplam {c['toplam'] / 1e9:.3f} (açılış {c['acilis'] / 1e9:.2f} + erişim "
                  f"{c['erisim'] / 1e9:.2f}) LB {b['lb_tl'] / 1e9:.3f} gap {100 * b['gap']:.2f}% "
                  f"{r['times']['total_s']:.0f} sn", flush=True)
        acc = acceptance(free, points)
        out = {"generated_by": "pipeline/32_precompute.py sweep", "generated_at": _now(), "grid_km": km,
               "settings": {"time_limit_s": cfg["time_limit"], "target_gap": cfg["gap"],
                            "mode": "sabit_p", "start_at_free_n": True},
               "free": free, "points": points, "acceptance": acc, "wall_s": time.perf_counter() - t_all}
        print(f"{km} km: en iyi p = {acc['best_p']} ({acc['best_ub_tl'] / 1e9:.3f}), serbest n = {acc['free_n']} "
              f"({free['ub_tl'] / 1e9:.3f}), fark {100 * acc['rel_diff_best_vs_free']:.3f}%, tolerans "
              f"{100 * acc['tolerance']:.2f}%, düzlük p = {acc['plateau_p']}, geçti: {acc['passed']}", flush=True)
        _write(PRECOMP / f"p_sweep_{km}km.json", out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=("defaults", "bounds", "sweep"))
    ap.add_argument("--grids", default=None)
    args = ap.parse_args()
    default_grids = {"defaults": "5,3,2", "bounds": "5,3,2", "sweep": "3,5"}[args.what]
    grids = [int(g) for g in (args.grids or default_grids).split(",")]
    {"defaults": run_defaults, "bounds": run_bounds, "sweep": run_sweep}[args.what](grids)


if __name__ == "__main__":
    main()
