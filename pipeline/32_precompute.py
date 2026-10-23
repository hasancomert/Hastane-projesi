"""Faz 3 — önceden çözülen çıktılar: varsayılan çözümler, sınır kartları, p taraması (U eğrisi), dibin sıkı çözümü,
k karşılaştırması.

Alt komutlar (repo kökünden):
  .venv/bin/python pipeline/32_precompute.py defaults [--grids 5,3,2] [--start KM=yol.npz] [--time KM=sn] [--gap KM=g]
                                                                        → precomputed/default_{5,3,2}km.json
  .venv/bin/python pipeline/32_precompute.py bounds   [--grids 5,3,2]   → data/processed/sinir_kartlari.json
  .venv/bin/python pipeline/32_precompute.py sweep    [--grids 3,5]     → precomputed/p_sweep_{3,5}km.json
                                                                          (önce defaults: serbest p'li çözüm gerekir)
  .venv/bin/python pipeline/32_precompute.py refine   [--grids 5,3] [--start KM:P=yol.npz] [--save-dir klasör]
                                                     [--p 3:88;89] [--no-solve]
                                                                        → precomputed/p_sweep_{3,5}km.json (dip yeniden)
  .venv/bin/python pipeline/32_precompute.py kcheck                     → data/processed/solver_tuning/
                                                                          k_karsilastirma_3km.json

Varsayılan çözümler: varsayılan parametreler, uzun süre sınırı ve sıkı hedef gap (DEFAULT_RUNS) — solve() bu sürelerde
Lagrange → iyileştirme evresi (LP yuvarlaması, pencere araması, çekirdek MIP) → tam MILP yolunu izler. Başlangıç adayları:
aynı parametre ve yapıyla yazılmış önceki dosyanın çözümü ve --start ile verilen .npz çözümleri (ör. bağımsız
doğrulamanın bulduğu çözümler: open_j, cls, beds, x; aday/yay indisleri modelin yapısıyla aynı); en ucuzu MIP
başlangıcıdır, yeniden çalıştırma çözümü kötüleştirmez. Sonuç sözlüğü eksiksiz yazılır (hastaneler, atamalar, maliyet
kırılımı, KPI'lar, denetim raporu, sertifika cümlesi, iz, atama LP'sinin dual değerleri; 'precomputed' alanında
başlangıç adayları).

p taraması (şartname 7.7; kabul ölçütü "varsayılan parametrelerde p taramasının en iyisi = serbest p'li çözüm"): sabit_p
modunda serbest optimumun çevresinde sık, uzakta seyrek p değerleri; her nokta için açılış / erişim / toplam maliyet,
alt sınır, gap ve süre. Gap'i %1'i aşan noktalar eldeki çözüm ve aynı p'nin çarpanlarıyla 4 kat süreyle yeniden
çözülür. 'refine' dibi (n* ± 3, 3 km'de ayrıca p = 83) simetrik başlangıçlarla ve uzun sürelerle yeniden çözer
(REFINE), serbest alt sınırı her noktaya uygular, "fark gap içinde" işaretlerini ve güçlendirilmiş kabul denetimini
('acceptance') yazar; tests/test_phase3_outputs.py aynı denetimi saklanan dosya üzerinde yapar.
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
# ızgara → (süre sınırı sn, hedef gap). Uzun, çevrimdışı çözümler (doğrulama A5/A6 sonrası): 5 km kanıtlı optimuma
# (gap 1e-6), 3 / 2 km'de hedef gap'e ulaşılamaz (alt sınır tarafı) — süre tam MILP'in alt sınırını yükseltmeye gider
DEFAULT_RUNS = {5: (1800.0, 1e-6), 3: (1800.0, 1e-4), 2: (3600.0, 1e-4)}
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
# küme kapsama: modelin k listeleriyle (2 km'de ispat ≈20 dk: doğrulama C2'de 1.368 sn) / R içindeki tüm adaylarla (k = 0)
LSCP_TIME = {5: 120.0, 3: 120.0, 2: 2400.0}
LSCP_ALL_TIME = {5: 120.0, 3: 120.0, 2: 600.0}


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Yazıldı: {path.relative_to(ROOT)} ({path.stat().st_size / 1e6:.1f} MB)", flush=True)


def load_npz_solution(inst, path: Path):
    """Doğrulama betiklerinin .npz çözümü (open_j, cls, beds, x; aday ve yay indisleri modelin yapısıyla aynı) →
    Solution. Yay sayısı tutmazsa ya da denetimden geçmezse ValueError."""
    from backend.model import checks
    from backend.model.instance import Solution
    z = np.load(path)
    if len(z["x"]) != inst.nA:
        raise ValueError(f"{path}: yay sayısı {len(z['x'])} ≠ modelin {inst.nA}")
    o = np.argsort(z["open_j"])
    sol = Solution(np.asarray(z["open_j"])[o].astype(np.int64), np.asarray(z["cls"])[o].astype(np.int64),
                   np.asarray(z["beds"])[o].astype(float), np.asarray(z["x"], dtype=float), source="dis_baslangic")
    chk = checks.check(inst, sol)
    if not chk["ok"]:
        raise ValueError(f"{path}: çözüm denetimden geçmedi: {chk['violations']}")
    return sol


def _kv(arg: str | None, cast) -> dict:
    """'3=a,2=b' → {3: cast(a), 2: cast(b)}."""
    out = {}
    for part in (arg or "").split(","):
        if part.strip():
            k, v = part.split("=", 1)
            out[int(k)] = cast(v.strip())
    return out


def run_defaults(grids: list[int], starts: dict | None = None, times: dict | None = None,
                 gaps: dict | None = None) -> None:
    from backend.model import instance
    from backend.model.params import Params
    from backend.model.solve import solve
    for km in grids:
        tl, gap = DEFAULT_RUNS[km]
        tl = (times or {}).get(km, tl)
        gap = (gaps or {}).get(km, gap)
        inst = instance.build_instance(km)
        # başlangıç adayları: aynı parametre ve yapıyla yazılmış önceki dosyanın çözümü ve verilen .npz çözümleri;
        # en ucuzu MIP başlangıcı olur (yeniden çalıştırma çözümü kötüleştirmez)
        cands = []
        prev = PRECOMP / f"default_{km}km.json"
        if prev.exists():
            old = json.loads(prev.read_text(encoding="utf-8"))
            if (old.get("ok") and old.get("params_hash") == Params.load({"grid.cell_km": km}).hash()
                    and old.get("structure_hash") == inst.s.key):
                cands.append(("onceki_dosya", _solution_from_result(inst, old)))
        for path in (starts or {}).get(km, []):
            cands.append((f"npz:{Path(path).name}", load_npz_solution(inst, Path(path))))
        start_info = [{"source": src, "objective_tl": sol.objective_tl(inst), "n_hospitals": sol.n_open}
                      for src, sol in cands]
        start, start_src = None, None
        if cands:
            start_src, start = min(cands, key=lambda c: c[1].objective_tl(inst))
            for si in start_info:
                si["chosen"] = si["source"] == start_src
                print(f"{km} km: başlangıç adayı {si['source']}: {si['objective_tl'] / 1e9:.7f} milyar TL, "
                      f"{si['n_hospitals']} hastane{' (seçildi)' if si['chosen'] else ''}", flush=True)
        t0 = time.perf_counter()
        r = solve(km, {}, time_limit=tl, gap=gap, compute_duals=True, start=start)
        r["precomputed"] = {"generated_by": "pipeline/32_precompute.py defaults", "generated_at": _now(),
                            "time_limit_s": tl, "target_gap": gap, "wall_s": time.perf_counter() - t0,
                            "start_from_previous": start_src == "onceki_dosya", "start_source": start_src,
                            "start_candidates": start_info}
        b = r["bounds"]
        print(f"{km} km: {r['status']} UB {b['ub_tl'] / 1e9:.7f} LB {b['lb_tl'] / 1e9:.7f} gap {100 * b['gap']:.4f}% "
              f"({b['lb_source']}, {b['mip_kind']}), {r['kpis']['n_hospitals']} hastane {r['kpis']['count_by_class']}, "
              f"kaynak {r['solution_source']}, {r['times']['total_s']:.0f} sn, denetim {r['check']['ok']}", flush=True)
        _write(PRECOMP / f"default_{km}km.json", r)


def run_bounds(grids: list[int]) -> None:
    from backend.model import bounds, instance
    out = {"generated_by": "pipeline/32_precompute.py bounds", "generated_at": _now(), "grids": {}}
    if BOUNDS_OUT.exists():
        out["grids"] = json.loads(BOUNDS_OUT.read_text(encoding="utf-8")).get("grids", {})
    for km in grids:
        inst = instance.build_instance(km)
        t0 = time.perf_counter()
        bc = bounds.bound_cards(inst, lscp_time=LSCP_TIME[km], pmin_time=PMIN_TIME[km], lscp_all_time=LSCP_ALL_TIME[km])
        bc["time_s"] = time.perf_counter() - t0
        for key in ("coverage_lscp", "coverage_lscp_k_list"):
            bc[key]["n_open"] = len(bc[key].pop("open_pos", []))
        bc["grid_km"] = km
        bc["settings"] = {"lscp_time_s": LSCP_TIME[km], "lscp_all_time_s": LSCP_ALL_TIME[km],
                          "pmin_time_s": PMIN_TIME[km], "R_km": inst.s.R_km, "k_nearest": inst.s.k_nearest}
        pm, la, lk = bc["p_min"], bc["coverage_lscp"], bc["coverage_lscp_k_list"]

        def _ls(d):
            return f"{d['value']} ({'kanıtlı' if d['proven'] else 'alt sınır ' + str(d['lower_bound'])}, {d['time_s']:.0f} sn)"
        print(f"{km} km: |J| {bc['max_hospitals_grid']}, kapasite {bc['capacity']['with_city']} / "
              f"{bc['capacity']['without_city']}, LSCP tüm adaylar {_ls(la)}, k = {lk['k_nearest']} listeleriyle "
              f"{_ls(lk)}, p_min {pm['value']} (alt sınır {pm['lower_bound']}, "
              f"{'kanıtlı' if pm['proven'] else pm['status']}), {bc['time_s']:.0f} sn", flush=True)
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


# ---------------------------------------------------------------- U eğrisinin dibi (doğrulama B3/B4)
# n* ± REFINE_HALF_WIDTH (+ ek p'ler) yeniden, sıkı çözülür. Her p için başlangıç adayları SİMETRİKTİR (doğrulama B3:
# eskiden yalnız p = n* noktası serbest çözümle başlatılıyordu): taramadaki eski çözüm, verilen .npz çözümleri, serbest
# çözümden ve komşu p'lerin iyileştirilmiş çözümlerinden tek tek kapatma/açma ile türetilen çözümler; p = n*'da serbest
# çözümün kendisi. 3 km: Lagrange (G sıralaması) → büyük çekirdek MIP (300 aday, talep başına 12 yay; doğrulamada 3 km
# p = 88 / 89 / 90'ı 7–11 dk'da iyileştiren kurgu) → solve() (Lagrange sınırı + pencere araması + cila + denetim).
# 5 km: aynı, ardından solve() tam MILP ile (kök LP ≈10 sn; alt sınırı hedef gap'e taşır).
REFINE = {
    3: {"half_width": 3, "extra_p": [83], "big_core": (300, 12), "lag_time": 20.0, "core_time": 330.0,
        "solve_time": 240.0, "mip": "core", "gap": 2e-4},
    5: {"half_width": 3, "extra_p": [], "big_core": (300, 12), "lag_time": 10.0, "core_time": 60.0,
        "solve_time": 240.0, "mip": "full", "gap": 2e-4},
}
REFINE_TOL = 2e-4          # kabul denetimi: serbest çözümü bundan fazla (göreli) yenebileceği dışlanamayan p "çözülmemiş"
NEIGHBOUR_TRY = 8          # komşu başlangıç: her adımda kesin değerlendirilen en iyi yaklaşık hamle sayısı


def _solution_from_cells(inst, cells):
    """Açık hücre kimlikleri → sınıf seçimli kesin değerlendirme (evaluate) çözümü ya da None."""
    from backend.model.evaluate import evaluate
    pos = {str(c): j for j, c in enumerate(inst.s.cand_ids)}
    ev = evaluate(inst, np.array(sorted(pos[c] for c in cells), dtype=np.int64), gap=1e-4, time_limit=60.0)
    return ev.solution if ev.ok else None


def _neighbour(inst0, sol, p: int, approx=None):
    """sol'den tek tek kapatma (n > p) ya da açma (n < p) ile p hastaneli çözüm: her adımda yaklaşık modelin
    (heuristics.ApproxModel) en iyi NEIGHBOUR_TRY hamlesi sabit sınıflı LP + sınıf onarımıyla (lagrange.fast_eval) kesin
    değerlendirilir, en ucuzu alınır; sonda sınıf seçimli cila. inst0 serbest örnek (amaç sabit_p ile aynı)."""
    from backend.model.evaluate import evaluate
    from backend.model.heuristics import ApproxModel
    from backend.model.lagrange import fast_eval
    M = approx or ApproxModel(inst0)
    cur = sol
    while cur.n_open != p:
        om = np.zeros(inst0.nJ, dtype=bool)
        om[cur.open_j] = True
        st = M.state(om)
        drop = cur.n_open > p
        da, do = M.eval_drop(st, M.openable) if drop else M.eval_add(st, M.openable)
        score = da + do
        cand = np.flatnonzero(np.isfinite(score))
        cand = cand[np.argsort(score[cand], kind="stable")][:NEIGHBOUR_TRY]
        cls_of = dict(zip(cur.open_j.tolist(), cur.cls.tolist()))
        best = None
        for j in cand:
            om2 = om.copy()
            om2[j] = not drop
            oj = np.flatnonzero(om2)
            ev = fast_eval(inst0, oj, np.array([cls_of.get(int(c), 0) for c in oj]), approx=M, mip_time=5.0)
            if ev.ok and (best is None or ev.objective_tl < best.objective_tl):
                best = ev
        if best is None:
            return None
        cur = best.solution
    ev = evaluate(inst0, cur.open_j, gap=1e-4, time_limit=30.0)
    return ev.solution if ev.ok and ev.objective_tl < cur.objective_tl(inst0) else cur


def _big_core(inst_p, start, cfg: dict) -> tuple:
    """Lagrange G sıralamasıyla büyük çekirdek MIP (ayrı süreçte). (en iyi çözüm, bilgi, Lagrange alt sınırı)."""
    from backend.model import checks, lagrange
    from backend.model.milp import MilpModel
    t0 = time.perf_counter()
    lag = lagrange.run(inst_p, start=start, time_limit=cfg["lag_time"], target_gap=1e-5)
    info = {"lagrange_bound_tl": lag.bound_tl if lag.ok else None, "lagrange_s": time.perf_counter() - t0}
    if not lag.ok:
        return start, info, None
    n_keep, m = cfg["big_core"]
    core, sel = lagrange.core_instance(inst_p, lag, start, n_keep=n_keep, m=m)
    cm = MilpModel(core, linking="strong")
    res = cm.solve(time_limit=cfg["core_time"], gap=1e-6, start=lagrange.solution_to_core(start, sel), isolate=True,
                   deadline=time.perf_counter() + cfg["core_time"] + 5.0)
    info.update({"candidates": int((~core.lock_closed).sum()), "arcs": int(len(sel)), "status": res.status,
                 "core_primal_tl": res.primal_tl, "core_dual_tl": res.dual_tl, "time_s": res.time_s, "improved": False})
    best = start
    if res.solution is not None:
        full = lagrange.solution_from_core(res.solution, sel, inst_p.nA)
        if checks.check(inst_p, full)["ok"] and full.objective_tl(inst_p) < start.objective_tl(inst_p) * (1 - 1e-12):
            best, info["improved"] = full, True
    info["total_s"] = time.perf_counter() - t0
    return best, info, (lag.bound_tl if lag.ok else None)


def _point(r: dict, p: int, start_given: bool, tries: int) -> dict:
    b, c, k = r["bounds"], r["costs"], r["kpis"]
    return {"p": p, "ok": True, "status": r["status"], "ub_tl": b["ub_tl"], "lb_tl": b["lb_tl"], "gap": b["gap"],
            "lb_source": b["lb_source"], "opening_tl": c["acilis"], "access_tl": c["erisim"], "total_tl": c["toplam"],
            "count_by_class": k["count_by_class"], "total_beds": k["total_beds"],
            "pop_weighted_mean_km": k["pop_weighted_mean_km"], "time_s": r["times"]["total_s"],
            "solution_source": r["solution_source"], "check_ok": r["check"]["ok"], "start_given": start_given,
            "tries": tries, "hospitals": [h["cell_id"] for h in r["hospitals"]]}


def _free_from_default(d: dict) -> dict:
    return {"ub_tl": d["bounds"]["ub_tl"], "lb_tl": d["bounds"]["lb_tl"], "gap": d["bounds"]["gap"],
            "n_hospitals": d["kpis"]["n_hospitals"], "opening_tl": d["costs"]["acilis"],
            "access_tl": d["costs"]["erisim"], "count_by_class": d["kpis"]["count_by_class"],
            "status": d["status"], "lb_source": d["bounds"]["lb_source"]}


def apply_free_bound(free: dict, points: list[dict]) -> None:
    """Serbest problem her sabit p probleminin gevşetmesidir: serbest alt sınır her p için de geçerli alt sınırdır.
    lb_tl = max(kendi alt sınırı, serbest alt sınır); kendi değeri lb_own_tl'de saklanır."""
    for q in points:
        if not q.get("ok"):
            continue
        own = q.get("lb_own_tl", q["lb_tl"])
        own_src = q.get("lb_own_source", q["lb_source"])
        q["lb_own_tl"], q["lb_own_source"] = own, own_src
        if own is None or free["lb_tl"] > own:
            q["lb_tl"], q["lb_source"] = free["lb_tl"], "serbest_alt_sinir"
        else:
            q["lb_tl"], q["lb_source"] = own, own_src
        q["gap"] = (q["ub_tl"] - q["lb_tl"]) / q["ub_tl"]


def p_ranges(ps: list[int]) -> str:
    """[64, 65, 67, 68, 70] → '64–65, 67–68, 70'."""
    out, ps = [], sorted(ps)
    i = 0
    while i < len(ps):
        j = i
        while j + 1 < len(ps) and ps[j + 1] == ps[j] + 1:
            j += 1
        out.append(f"{ps[i]}–{ps[j]}" if j > i else f"{ps[i]}")
        i = j + 1
    return ", ".join(out)


def acceptance_refined(free: dict, points: list[dict], tol: float = REFINE_TOL) -> dict:
    """Güçlendirilmiş kabul denetimi (doğrulama B3): (1) taramanın en iyi üst sınırı p = n*'da (başka hiçbir p, simetrik
    başlangıçlara rağmen, serbest çözümden ucuz değil); (2) hiçbir p serbest alt sınırın altında değil; (3) düzlük
    dürüstçe: alt sınırı serbest çözümün değerini aşmayan p'ler 'fark gap içinde' (n*'dan ayırt edilemez); bunlardan
    alt sınırı serbest çözümün tol kadar altında kalanlar 'çözülmemiş' (n*'ın tek optimum olduğu kanıtlanamadı)."""
    ok_pts = [q for q in points if q.get("ok")]
    n_star, ub_free, lb_free = free["n_hospitals"], free["ub_tl"], free["lb_tl"]
    best = min(ok_pts, key=lambda q: (q["ub_tl"], q["p"] != n_star))
    at_free = next((q for q in ok_pts if q["p"] == n_star), None)
    for q in ok_pts:
        q["referans"] = q["p"] == n_star
        q["fark_gap_icinde"] = bool(q["lb_tl"] <= ub_free * (1 + 1e-12)) if q["p"] != n_star else None
        q["fark_tl"] = q["ub_tl"] - ub_free
    plateau = [q["p"] for q in ok_pts if q["lb_tl"] <= ub_free * (1 + 1e-12)]
    unresolved = [q["p"] for q in ok_pts if q["p"] != n_star and q["lb_tl"] < ub_free * (1 - tol)]
    refined = [q for q in ok_pts if q.get("refined")]
    no_beat = all(q["ub_tl"] >= lb_free * (1 - 1e-12) for q in ok_pts)
    best_is_free = best["p"] == n_star
    at_free_ok = at_free is not None and abs(at_free["ub_tl"] - ub_free) <= 1e-9 * ub_free
    tol_s = f"{100 * tol:.2f}".replace(".", ",")
    others = [q for q in plateau if q != n_star]
    summary = (f"Taramanın en iyisi p = {best['p']}, serbest optimum {n_star} hastane. Alt sınırı serbest çözümün "
               f"değerini aşmayan (n*'dan ayırt edilemeyen, 'fark gap içinde') başka p değerleri: "
               f"{p_ranges(others) if others else 'yok'} ({len(others)} nokta).")
    if unresolved:
        summary += (f" Bunlardan {len(unresolved)} noktada alt sınır serbest çözümün %{tol_s}'den fazla altında: "
                    f"n*'ın tek en iyi p olduğu bu gap'lerle kanıtlanamıyor (düz dip).")
    else:
        summary += f" Başka hiçbir p serbest çözümden %{tol_s}'den fazla ucuz olamaz (kanıtlı)."
    return {
        "best_p": best["p"], "best_ub_tl": best["ub_tl"], "free_n": n_star, "free_ub_tl": ub_free, "free_lb_tl": lb_free,
        "tolerance": tol, "rel_diff_best_vs_free": (best["ub_tl"] - ub_free) / ub_free,
        "best_p_is_free_n": best_is_free, "at_free_matches_default": at_free_ok,
        "no_p_beats_free_lb": no_beat, "plateau_p": plateau, "free_n_in_plateau": n_star in plateau,
        "unresolved_p": unresolved, "free_n_unique_within_tol": not unresolved,
        "refined_p": [q["p"] for q in refined],
        "refined_max_gap": max((q["gap"] for q in refined), default=None),
        "summary_tr": summary,
        "passed": bool(best_is_free and at_free_ok and no_beat and n_star in plateau),
    }


def run_refine(grids: list[int], pstarts: dict | None = None, save_dir: Path | None = None,
               only_p: dict | None = None, no_solve: bool = False, times: dict | None = None,
               gaps: dict | None = None) -> None:
    from backend.model import checks, instance
    from backend.model.heuristics import ApproxModel
    from backend.model.params import Params
    from backend.model.solve import solve
    for km in grids:
        cfg = dict(REFINE[km])
        if km in (times or {}):
            cfg["solve_time"] = times[km]          # ikinci geçiş: daha sıkı gap / daha uzun süre (ör. 5 km tam MILP)
        if km in (gaps or {}):
            cfg["gap"] = gaps[km]
        d = json.loads((PRECOMP / f"default_{km}km.json").read_text(encoding="utf-8"))
        spath = PRECOMP / f"p_sweep_{km}km.json"
        sw = json.loads(spath.read_text(encoding="utf-8"))
        free = _free_from_default(d)
        n_star = free["n_hospitals"]
        inst0 = instance.build_instance(km)
        free_sol = _solution_from_result(inst0, d)
        M = ApproxModel(inst0)
        ps = list(range(n_star - cfg["half_width"], n_star + cfg["half_width"] + 1)) + list(cfg["extra_p"])
        # sıra: n*'dan dışa doğru (komşu başlangıçlar iyileştirilmiş çözümlerden türesin)
        ps = sorted(set(ps), key=lambda q: (abs(q - n_star), q))
        if only_p and km in only_p:
            ps = [q for q in ps if q in only_p[km]] + [q for q in only_p[km] if q not in ps]
        pts = {q["p"]: q for q in sw["points"]}
        solved: dict[int, object] = {}
        if save_dir is not None:
            for q in pts:
                f = save_dir / f"refine_{km}km_p{q}.npz"
                if f.exists():
                    try:
                        solved[q] = load_npz_solution(inst0, f)
                    except ValueError:
                        pass
        t_all = time.perf_counter()
        for p in ([] if no_solve else ps):
            t0 = time.perf_counter()
            ov = {"solver.objective_mode": "sabit_p", "solver.p_fixed": p}
            inst_p = instance.build_instance(km, Params.load(ov))
            assert inst_p.s.key == inst0.s.key
            cands = []
            old = pts.get(p)
            if old is not None and old.get("ok"):
                cands.append(("tarama", _solution_from_cells(inst_p, old["hospitals"])))
            for path in (pstarts or {}).get((km, p), []):
                cands.append((f"npz:{Path(path).name}", load_npz_solution(inst_p, Path(path))))
            if p in solved:
                cands.append(("onceki_iyilestirme", solved[p]))
            if p == n_star:
                cands.append(("serbest", free_sol))
            elif abs(p - n_star) <= 2 * cfg["half_width"]:
                cands.append(("serbest_komsu", _neighbour(inst0, free_sol, p, M)))
            for q in (p - 1, p + 1):
                if q in solved:
                    cands.append((f"komsu_p{q}", _neighbour(inst0, solved[q], p, M)))
            start_info, good = [], []
            for src, sol in cands:
                ok = sol is not None and sol.n_open == p and checks.check(inst_p, sol)["ok"]
                start_info.append({"source": src, "ok": bool(ok),
                                   "objective_tl": sol.objective_tl(inst_p) if sol is not None else None})
                if ok:
                    good.append((src, sol))
            start_src, start = min(good, key=lambda c: c[1].objective_tl(inst_p)) if good else (None, None)
            t_starts = time.perf_counter() - t0
            print(f"  p = {p}: başlangıçlar " + ", ".join(
                f"{si['source']} {si['objective_tl'] / 1e9:.5f}" if si["ok"] else f"{si['source']} —"
                for si in start_info) + f" → {start_src} ({t_starts:.0f} sn)", flush=True)
            core_info, lag_lb = None, None
            if cfg["big_core"] and start is not None:
                start, core_info, lag_lb = _big_core(inst_p, start, cfg)
                print(f"    büyük çekirdek: {core_info.get('status')} {start.objective_tl(inst_p) / 1e9:.5f} "
                      f"(iyileşti: {core_info.get('improved')}), Lagrange {((lag_lb or 0) / 1e9):.4f}, "
                      f"{core_info.get('total_s', 0):.0f} sn", flush=True)
            r = solve(km, ov, time_limit=cfg["solve_time"], gap=cfg["gap"], start=start, mip=cfg["mip"])
            if not r.get("ok"):
                print(f"  p = {p}: {r['status']} — {r.get('message_tr')}", flush=True)
                continue
            q = _point(r, p, start is not None, 1)
            if lag_lb is not None and (q["lb_tl"] is None or lag_lb > q["lb_tl"]) and lag_lb <= q["ub_tl"]:
                q["lb_tl"], q["lb_source"] = lag_lb, "lagrange"
                q["gap"] = (q["ub_tl"] - q["lb_tl"]) / q["ub_tl"]
            q["refined"] = True
            q["refine"] = {"starts": start_info, "start_source": start_src, "starts_s": t_starts,
                           "big_core": core_info, "solve_time_limit_s": cfg["solve_time"], "target_gap": cfg["gap"],
                           "mip": cfg["mip"], "bounds_mip_kind": r["bounds"]["mip_kind"],
                           "wall_s": time.perf_counter() - t0}
            q["time_s"] = time.perf_counter() - t0
            pts[p] = q
            solved[p] = _solution_from_result(inst0, r)
            if save_dir is not None:
                save_dir.mkdir(parents=True, exist_ok=True)
                sol = solved[p]
                np.savez(save_dir / f"refine_{km}km_p{p}.npz", open_j=sol.open_j, cls=sol.cls, beds=sol.beds, x=sol.x)
            print(f"  p = {p}: {r['status']} UB {q['ub_tl'] / 1e9:.7f} LB {q['lb_tl'] / 1e9:.5f} "
                  f"gap {100 * q['gap']:.3f}% ({q['lb_source']}) {q['count_by_class']} {q['time_s']:.0f} sn", flush=True)
            if q["ub_tl"] < free["ub_tl"] * (1 - 1e-12):
                print(f"  UYARI: p = {p} çözümü serbest çözümden ucuz ({q['ub_tl'] / 1e9:.7f} < "
                      f"{free['ub_tl'] / 1e9:.7f}); varsayılan çözüm bu başlangıçla yeniden çözülmeli.", flush=True)
        points = [pts[q] for q in sorted(pts)]
        apply_free_bound(free, points)
        acc = acceptance_refined(free, points)
        sw.update({"generated_at": _now(), "free": free, "points": points, "acceptance": acc})
        sw["acceptance_old_rule"] = acceptance(free, points)
        sw.setdefault("refine", {})
        if not no_solve and "settings" in sw["refine"] and "passes" not in sw["refine"]:
            sw["refine"]["passes"] = [{"at": sw["refine"].get("generated_at"), "settings": sw["refine"]["settings"],
                                       "wall_s": sw["refine"].get("wall_s_last")}]     # önceki (ilk) geçiş
        sw["refine"].update({"generated_by": "pipeline/32_precompute.py refine", "generated_at": _now(),
                             "settings": {k: v for k, v in cfg.items()}, "tolerance": REFINE_TOL,
                             "wall_s_last": time.perf_counter() - t_all})
        if not no_solve:
            sw["refine"].setdefault("passes", []).append({"at": _now(), "p": ps, "settings": dict(cfg),
                                                          "wall_s": time.perf_counter() - t_all})
        print(f"{km} km: {acc['summary_tr']} geçti: {acc['passed']}", flush=True)
        _write(spath, sw)


# ---------------------------------------------------------------- k karşılaştırması (doğrulama E2)
KCHECK = {"km": 3, "runs": [(30, 600.0), (10, 300.0)], "gap": 0.002}
KCHECK_OUT = ROOT / "data" / "processed" / "solver_tuning" / "k_karsilastirma_3km.json"


def run_kcheck() -> None:
    """3 km'de kısa aday listeleriyle (k = 30, 10) uzun çözüm; varsayılan (k = 50) çözümle kıyas. Yay kümesi alt küme
    olduğundan opt(k) ≥ opt(50); fark [LB_k − UB_50, UB_k − LB_50] aralığında: 'kanıtlı en az' (LB_k − UB_50) ve
    'bulunan' (UB_k − UB_50) artış yazılır. Eski 'k = 30 → +%1,2' iddiası (T2) 60 sn'lik, alt sınırı zayıf LP olan bir
    çözümün üst sınırıydı (doğrulama E2)."""
    from backend.model.solve import solve
    km = KCHECK["km"]
    d = json.loads((PRECOMP / f"default_{km}km.json").read_text(encoding="utf-8"))
    b50 = d["bounds"]
    out = {"generated_by": "pipeline/32_precompute.py kcheck", "generated_at": _now(), "grid_km": km,
           "settings": KCHECK, "k50_reference": {"file": f"precomputed/default_{km}km.json", "ub_tl": b50["ub_tl"],
                                                 "lb_tl": b50["lb_tl"], "arcs": d["instance"]["arcs"]}, "runs": []}
    for k, tl in KCHECK["runs"]:
        r = solve(km, {"grid.k_nearest": k}, time_limit=tl, gap=KCHECK["gap"])
        b = r["bounds"]
        row = {"k_nearest": k, "time_limit_s": tl, "status": r["status"], "ub_tl": b["ub_tl"], "lb_tl": b["lb_tl"],
               "gap": b["gap"], "lb_source": b["lb_source"], "mip_kind": b["mip_kind"],
               "n_hospitals": r["kpis"]["n_hospitals"], "count_by_class": r["kpis"]["count_by_class"],
               "arcs": r["instance"]["arcs"], "time_s": r["times"]["total_s"], "check_ok": r["check"]["ok"],
               "proven_min_increase": (b["lb_tl"] - b50["ub_tl"]) / b50["ub_tl"],
               "found_increase": (b["ub_tl"] - b50["ub_tl"]) / b50["ub_tl"],
               "max_increase_bound": (b["ub_tl"] - b50["lb_tl"]) / b50["ub_tl"]}
        out["runs"].append(row)
        print(f"k = {k}: {r['status']} UB {b['ub_tl'] / 1e9:.4f} LB {b['lb_tl'] / 1e9:.4f}; k = 50 UB "
              f"{b50['ub_tl'] / 1e9:.4f} → kanıtlı en az {100 * row['proven_min_increase']:+.3f}%, bulunan "
              f"{100 * row['found_increase']:+.3f}%", flush=True)
        out["generated_at"] = _now()
        _write(KCHECK_OUT, out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=("defaults", "bounds", "sweep", "refine", "kcheck"))
    ap.add_argument("--grids", default=None)
    ap.add_argument("--start", action="append", default=[],
                    help="defaults: KM=yol.npz (doğrulama çözümü; MIP başlangıcı adayı); refine: KM:P=yol.npz")
    ap.add_argument("--time", default=None, help="defaults / refine: süre sınırı, ör. 3=1800,2=3600")
    ap.add_argument("--gap", default=None, help="defaults / refine: hedef gap, ör. 5=1e-6")
    ap.add_argument("--p", default=None, help="refine: yalnız bu p'ler, ör. 3:88;89,5:70")
    ap.add_argument("--save-dir", default=None, help="refine: iyileştirilen çözümlerin .npz klasörü (repo dışı)")
    ap.add_argument("--no-solve", action="store_true", help="refine: çözmeden kabul denetimini yeniden hesapla")
    args = ap.parse_args()
    default_grids = {"defaults": "5,3,2", "bounds": "5,3,2", "sweep": "3,5", "refine": "5,3", "kcheck": "3"}[
        args.what]
    grids = [int(g) for g in (args.grids or default_grids).split(",")]
    if args.what == "defaults":
        starts: dict = {}
        for a in args.start:
            k, v = a.split("=", 1)
            starts.setdefault(int(k), []).append(v)
        run_defaults(grids, starts, _kv(args.time, float), _kv(args.gap, float))
    elif args.what == "refine":
        pstarts: dict = {}
        for a in args.start:
            k, v = a.split("=", 1)
            km_, p_ = k.split(":")
            pstarts.setdefault((int(km_), int(p_)), []).append(v)
        only = {}
        for part in (args.p or "").split(","):
            if part.strip():
                km_, lst = part.split(":")
                only[int(km_)] = [int(x) for x in lst.split(";")]
        run_refine(grids, pstarts, Path(args.save_dir) if args.save_dir else None, only or None, args.no_solve,
                   _kv(args.time, float), _kv(args.gap, float))
    elif args.what == "kcheck":
        run_kcheck()
    else:
        {"bounds": run_bounds, "sweep": run_sweep}[args.what](grids)


if __name__ == "__main__":
    main()
