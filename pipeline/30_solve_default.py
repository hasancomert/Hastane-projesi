"""Faz 3 — optimizasyon çekirdeğini komut satırından çalıştırır; sonucu yazdırır ve (isteğe bağlı) JSON'a kaydeder.

Örnekler (repo kökünden):
  .venv/bin/python pipeline/30_solve_default.py --grid 3 --time-limit 15
  .venv/bin/python pipeline/30_solve_default.py --grid 5 --set access.alpha=1.5 --set size.city_enabled=false
  .venv/bin/python pipeline/30_solve_default.py --grid 3 --mode sabit_p --set solver.p_fixed=60 --out /tmp/p60.json
  .venv/bin/python pipeline/30_solve_default.py --baseline        # 5 km (120 sn), 3 km (300 sn) temel ölçüm ve
                                                                  # 2 km (60 sn) ek ölçüm
                                                                  # → data/processed/solver_baseline.json
--baseline ayrıca her ızgarada güçlü LP gevşetmesini ayrı olarak çözer (kök LP süresi ölçümü) ve sınır kartlarını
(LSCP, gerçek p_min) hesaplar. --baseline Lagrange'sız çalışır (Faz 3 ilk sürümünün ölçümü; canlı akışın kıyaslaması
pipeline/31_benchmark.py'dedir).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import platform
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from backend.model import bounds, instance, milp, solve as solve_mod  # noqa: E402
from backend.model.params import Params  # noqa: E402

OUT_BASELINE = ROOT / "data" / "processed" / "solver_baseline.json"


def fmt(x: float, nd: int = 1) -> str:
    s = f"{x:,.{nd}f}"
    return s.replace(",", "_").replace(".", ",").replace("_", ".")


def parse_value(v: str):
    lv = v.lower()
    if lv in ("true", "evet"):
        return True
    if lv in ("false", "hayir", "hayır"):
        return False
    try:
        return json.loads(v)
    except json.JSONDecodeError:
        return v


def print_result(r: dict) -> None:
    print(f"durum: {r['status']} — {r.get('status_tr') or r.get('message_tr')}")
    if not r.get("ok"):
        print(r.get("message_tr"))
        return
    b, t, k, c = r["bounds"], r["times"], r["kpis"], r["costs"]
    m = r.get("model") or {}
    print(r["message_tr"])
    print(f"model: {m.get('rows')} satır, {m.get('cols')} sütun, {m.get('nnz')} sıfırdan farklı, "
          f"{m.get('binaries')} ikili; |I| = {r['instance']['I']}, |J| = {r['instance']['J']}, yay = {r['instance']['arcs']}")
    print(f"üst sınır {fmt(b['ub_tl'] / 1e9, 3)} milyar TL; alt sınır "
          f"{fmt(b['lb_tl'] / 1e9, 3) if b['lb_tl'] is not None else '—'} ({b['lb_source']}); "
          f"gap {fmt(100 * b['gap'], 3) + ' %' if b['gap'] is not None else '—'}; kaynak {r['solution_source']}")
    print("süreler (sn): " + ", ".join(f"{key} {fmt(val, 2)}" for key, val in t.items()))
    print(f"ilk çözüm (MIP içi) {r.get('first_incumbent_s')}, ilk anlamlı alt sınır {r.get('first_dual_s')}")
    print(f"sınıflar {k['count_by_class']}, toplam yatak {fmt(k['total_beds'], 1)} (talep {fmt(k['total_demand_beds'], 1)}), "
          f"ort. mesafe {fmt(k['pop_weighted_mean_km'], 2)} km, en uzak {fmt(k['max_km'], 1)} km, "
          f"doluluk {fmt(100 * k['mean_occupancy'], 1)} %")
    print("nüfus payı: " + ", ".join(f"{d} km içinde %{fmt(100 * v, 1)}" for d, v in k["share_pop_within_km"].items()))
    print("maliyet (milyar TL): " + ", ".join(f"{key} {fmt(val / 1e9, 2)}" for key, val in c.items()))
    print(f"denetim: {r['check']['summary_tr']} (amaç farkı {r['check'].get('objective_rel_diff')})")


def compact(r: dict) -> dict:
    out = {k: v for k, v in r.items() if k not in ("assignment",)}
    return out


def lp_relaxation(inst, linking="strong", solver="simplex", time_limit=600.0) -> dict:
    m = milp.MilpModel(inst, linking=linking)
    r = m.solve(time_limit=time_limit, relax=True, options={"solver": solver})
    return {"linking": linking, "solver": solver, "status": r.status, "objective_tl": r.primal_tl,
            "time_s": r.time_s, "simplex_iterations": int(m.h.getInfo().simplex_iteration_count), **m.size()}


def run_summary(r: dict, extra: dict) -> dict:
    """Raporlama özeti: model boyutu, süreler, ilk çözüm, kök LP, sınırlar, sınıflar, maliyet, KPI."""
    h = r["heuristic"] or {}
    t0_mip = next((e["t_total"] for e in r["trace"] if e.get("phase") == "mip"), None)
    incs = [e for e in r["trace"] if e.get("kind") == "incumbent"]
    better = [e for e in incs if h.get("objective_tl") and e["primal"] < h["objective_tl"] * (1 - 1e-9)]
    lp = extra.get("lp_strong_simplex") or {}
    return {
        "model": r.get("model"), "instance": {k: r["instance"][k] for k in ("I", "J", "K", "arcs")},
        "build_s": {"instance": r["times"].get("instance_s"), "structure": r["instance"]["structure_build_s"],
                    "model": r["times"].get("model_build_s")},
        "heuristic": {"time_s": h.get("time_s"), "objective_tl": h.get("objective_tl")},
        "first_incumbent_total_s": r["times"].get("instance_s", 0) + (h.get("time_s") or 0),
        "first_highs_improvement": None if not better else {
            "t_total_s": better[0]["t_total"], "objective_tl": better[0]["primal"]},
        "root_lp": {"mip_first_meaningful_bound_s": r.get("first_dual_s"),
                    "mip_first_meaningful_bound_total_s": None if r.get("first_dual_s") is None or t0_mip is None
                    else t0_mip + r["first_dual_s"],
                    "standalone_strong_lp_s": lp.get("time_s"), "standalone_strong_lp_tl": lp.get("objective_tl"),
                    "standalone_weak_lp_s": (extra.get("lp_weak_simplex") or {}).get("time_s"),
                    "standalone_weak_lp_tl": (extra.get("lp_weak_simplex") or {}).get("objective_tl")},
        "final": {"status": r["status"], "ub_tl": r["bounds"]["ub_tl"], "lb_tl": r["bounds"]["lb_tl"],
                  "gap": r["bounds"]["gap"], "lb_source": r["bounds"]["lb_source"],
                  "source": r["solution_source"], "total_s": r["times"]["total_s"]},
        "count_by_class": r["kpis"]["count_by_class"], "total_beds": r["kpis"]["total_beds"],
        "costs_tl": r["costs"], "kpis": r["kpis"], "check_ok": r["check"]["ok"],
    }


def baseline(args) -> None:
    runs = []
    for km, tl, full in ((5, 120.0, True), (3, 300.0, True), (2, 60.0, False)):
        print(f"\n=== {km} km, süre sınırı {tl:g} sn ===", flush=True)
        # temel ölçüm Faz 3 çekirdeğinin ilk sürümünü yeniden üretir: Lagrange yok, yalnız sezgisel + tam güçlü MILP
        r = solve_mod.solve(km, time_limit=tl, gap=args.gap, use_lagrange=False)
        print_result(r)
        inst = instance.build_instance(km)
        extra = {}
        if full:
            print("güçlü LP gevşetmesi (ayrı çözüm, dual simpleks) ...", flush=True)
            extra["lp_strong_simplex"] = lp_relaxation(inst, "strong", "simplex", 600.0)
            print(f"  {extra['lp_strong_simplex']['status']} {fmt(extra['lp_strong_simplex']['time_s'], 1)} sn, "
                  f"{fmt((extra['lp_strong_simplex']['objective_tl'] or 0) / 1e9, 3)} milyar TL", flush=True)
        extra["lp_weak_simplex"] = lp_relaxation(inst, "weak", "simplex", 120.0)
        print(f"  zayıf LP: {fmt(extra['lp_weak_simplex']['time_s'], 2)} sn, "
              f"{fmt((extra['lp_weak_simplex']['objective_tl'] or 0) / 1e9, 3)} milyar TL", flush=True)
        if full:
            t = time.perf_counter()
            bc = bounds.bound_cards(inst, lscp_time=30, pmin_time=60)
            bc["time_s"] = time.perf_counter() - t
            print(f"  sınır kartları: |J| {bc['max_hospitals_grid']}, kapasite {bc['capacity']['with_city']} / "
                  f"{bc['capacity']['without_city']}, LSCP {bc['coverage_lscp']['value']}, p_min "
                  f"{bc['p_min']['value']} (alt {bc['p_min']['lower_bound']}, {fmt(bc['time_s'], 1)} sn)", flush=True)
            bc["coverage_lscp"].pop("open_pos", None)
            extra["bound_cards"] = bc
        runs.append({"grid_km": km, "time_limit_s": tl, "summary": run_summary(r, extra), "result": compact(r),
                     "measurements": extra})
    out = {"generated_by": "pipeline/30_solve_default.py --baseline",
           "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
           "machine": {"python": sys.version.split()[0], "platform": platform.platform(),
                       "processor": platform.processor(), "note": "geliştirme konteyneri (4 çekirdek); sunum dizüstü "
                                                                    "değil"},
           "params_hash": Params.load().hash(), "target_gap": args.gap, "runs": runs}
    OUT_BASELINE.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nYazıldı: {OUT_BASELINE.relative_to(ROOT)}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--grid", type=int, default=None, help="hücre boyutu (2/3/5 km)")
    ap.add_argument("--time-limit", type=float, default=None)
    ap.add_argument("--gap", type=float, default=0.01)
    ap.add_argument("--mode", choices=("toplam", "butce", "sabit_p"), default=None)
    ap.add_argument("--set", action="append", default=[], metavar="ANAHTAR=DEĞER", help="parametre değişikliği")
    ap.add_argument("--lock-open", action="append", default=[], metavar="HÜCRE")
    ap.add_argument("--lock-closed", action="append", default=[], metavar="HÜCRE")
    ap.add_argument("--linking", default="strong", help="strong | weak | r (en yakın r yay)")
    ap.add_argument("--duals", action="store_true")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--baseline", action="store_true")
    args = ap.parse_args()
    if args.baseline:
        baseline(args)
        return
    ov = {}
    for item in args.set:
        k, _, v = item.partition("=")
        ov[k.strip()] = parse_value(v.strip())
    link = int(args.linking) if args.linking.isdigit() else args.linking
    progress_t = [0.0]

    def progress(ev: dict) -> None:
        if ev.get("kind") in ("heuristic", "incumbent") or ev.get("t_total", 0) - progress_t[0] > 10:
            progress_t[0] = ev.get("t_total", 0)
            p, d = ev.get("primal"), ev.get("dual")
            print(f"  [{fmt(ev.get('t_total', 0), 1)} sn] {ev.get('kind')}: üst "
                  f"{fmt(p / 1e9, 3) if p else '—'} alt {fmt(d / 1e9, 3) if d else '—'}", flush=True)

    r = solve_mod.solve(args.grid, ov, {"open": args.lock_open, "closed": args.lock_closed}, args.mode,
                        args.time_limit, args.gap, progress, linking=link, compute_duals=args.duals)
    print_result(r)
    if args.out:
        args.out.write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")
        print(f"Yazıldı: {args.out}")


if __name__ == "__main__":
    main()
