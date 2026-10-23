"""Faz 3 — çözücü kıyaslaması: varsayılan parametrelerle 5 / 3 / 2 km canlı çözüm süreleri (şartname 4.6 hedefleri).

Her çalıştırma AYRI bir süreçte yapılır (soğuk başlangıç: ızgara yükleme + yapı kurulumu + Lagrange çarpanları sıfırdan).
Aynı süreçte ardından bir "ılık" çözüm yapılır: yapı önbellekte, kullanıcı bir maliyet kaydırıcısını oynatmış
(hastane başı sabit maliyet +%10) — sunumdaki tipik canlı yeniden çözüm.

Ölçülenler (her çalıştırma): örnek/yapı kurulumu, sezgisel, Lagrange 1. evre, çekirdek MIP, cila, Lagrange 2. evre,
toplam süre; ilk olurlu çözüm zamanı; izden (trace) gap ≤ %2 / %1 / %0,5'e ilk ulaşma zamanı (solve() hedef gap'e
ulaşınca durmaz, süre içinde gap/2'ye dek sürer; şartname hedefi için ölçüt %1'e ulaşma zamanıdır); son alt/üst sınır, gap,
durum, alt sınırın kaynağı; hastane sayısı ve sınıflar; denetim sonucu. Karşılaştırma: data/processed/solver_baseline.json
(Faz 3 çekirdeğinin ilk sürümü: yalnız sezgisel + tam güçlü MILP).

Süre sınırları: solver.time_limit_s (15 sn) 5 ve 3 km'de; 2 km'de şartname hedefi 60 sn. Hedef gap solver.mip_gap (%1).
Başarı: izden gap ≤ %1'e hedef süre içinde ulaşılması (success_rate); süre sınırı serttir (toplam ≤ sınır + 0,5 sn).
Yeni bir kıyaslama yazılırken dosyadaki eski özetler 'previous' anahtarı altında saklanır.

Çalıştırma (repo kökünden):
  .venv/bin/python pipeline/31_benchmark.py                 # 3 çalıştırma × 3 ızgara → data/processed/solver_benchmark.json
  .venv/bin/python pipeline/31_benchmark.py --runs 5 --grids 3
  .venv/bin/python pipeline/31_benchmark.py --runs-by-grid 5=5,3=10,2=10 --no-warm   # yalnız soğuk çalıştırmalar
  .venv/bin/python pipeline/31_benchmark.py --reaggregate   # yalnız özetleri yeniden hesapla
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT = ROOT / "data" / "processed" / "solver_benchmark.json"
BASELINE = ROOT / "data" / "processed" / "solver_baseline.json"
TARGET_S = {3: 15.0, 2: 60.0}          # şartname 4.6: 3 km ≲ 15 sn, 2 km ≲ 60 sn
TIME_LIMIT = {5: None, 3: None, 2: 60.0}   # None → solver.time_limit_s
GAP_LEVELS = (0.02, 0.01, 0.005)
HARD_LIMIT_SLACK_S = 0.5               # sert süre sınırı payı (toplam ≤ süre sınırı + bu)


def time_to_gaps(trace: list[dict], levels=GAP_LEVELS) -> dict:
    """İzden: en iyi üst sınır (her olurlu çözüm) ve en iyi KÜRESEL alt sınır (Lagrange / tam MILP) ile gap ≤ seviye'ye
    ilk ulaşılan an (sn). Çekirdek MIP'in alt sınırı küresel olmadığından izde 'dual' alanı boştur."""
    best_p, best_d = float("inf"), -float("inf")
    out = {str(g): None for g in levels}
    first_primal = None
    for e in sorted(trace, key=lambda e: e.get("t_total", 0.0)):
        p, d = e.get("primal"), e.get("dual")
        if p is not None:
            best_p = min(best_p, p)
            if first_primal is None:
                first_primal = e["t_total"]
        if d is not None:
            best_d = max(best_d, d)
        if best_p < float("inf") and best_d > -float("inf"):
            g = (best_p - best_d) / best_p
            for lv in levels:
                if out[str(lv)] is None and g <= lv + 1e-12:
                    out[str(lv)] = e["t_total"]
    return {"first_feasible_s": first_primal, "time_to_gap_s": out}


def summarize(r: dict) -> dict:
    if not r.get("ok"):
        return {"status": r.get("status"), "message": r.get("message_tr"), "times": r.get("times")}
    b, k = r["bounds"], r["kpis"]
    lag = r.get("lagrange") or {}
    core = r.get("core_mip") or {}
    imp = r.get("improve") or {}
    lp = imp.get("lp_round") or {}
    return {
        "status": r["status"], "ub_tl": b["ub_tl"], "lb_tl": b["lb_tl"], "gap": b["gap"], "lb_source": b["lb_source"],
        "solution_source": r["solution_source"], "n_hospitals": k["n_hospitals"], "count_by_class": k["count_by_class"],
        "total_beds": k["total_beds"], "times": r["times"], **time_to_gaps(r["trace"]),
        "lagrange": {key: lag.get(key) for key in ("iters", "sub_ms", "init_s", "init_source", "lh_calls", "time_s")},
        "lagrange_phase2": lag.get("phase2"),
        "core_mip": {key: core.get(key) for key in ("candidates", "arcs", "status", "time_s", "improved",
                                                     "primal_tl")} if core else None,
        "core_attempts": [{key: a.get(key) for key in ("attempt", "n_keep", "m", "status", "time_s", "improved",
                                                        "improved_start", "stop_reason", "first_dual_s")}
                          for a in (core.get("attempts") or [])],
        "lp_round": {"lp_s": lp.get("lp_s"), "tries": lp.get("tries")} if lp else None,
        "windows": imp.get("windows"), "mip_kind": b.get("mip_kind"),
        "check_ok": r["check"]["ok"], "instance": {key: r["instance"][key] for key in ("I", "J", "arcs")},
        "structure_from_cache": r["instance"]["structure_from_cache"],
    }


def worker(km: int, tl: float | None, gap: float, keep_trace: bool, warm_run: bool = True) -> None:
    from backend.model.params import Params
    from backend.model.solve import solve
    t0 = time.perf_counter()
    r = solve(km, {}, time_limit=tl, gap=gap)
    cold = summarize(r)
    cold["wall_s"] = time.perf_counter() - t0
    if keep_trace:
        cold["trace"] = [{key: e.get(key) for key in ("t_total", "kind", "phase", "primal", "dual")}
                         for e in r["trace"] if e.get("primal") is not None or e.get("dual") is not None
                         or e.get("kind") == "phase"]
    out = {"cold": cold}
    if warm_run:
        f0 = Params.load()["cost.fixed_per_hospital"]
        t0 = time.perf_counter()
        r2 = solve(km, {"cost.fixed_per_hospital": 1.1 * f0}, time_limit=tl, gap=gap)
        warm = summarize(r2)
        warm["wall_s"] = time.perf_counter() - t0
        warm["change"] = {"cost.fixed_per_hospital": 1.1 * f0}
        out["warm"] = warm
    print("@@" + json.dumps(out, ensure_ascii=False))


def _stats(xs: list) -> dict | None:
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    return {"n": len(xs), "median": statistics.median(xs), "min": min(xs), "max": max(xs)}


def aggregate(km: int, runs: list[dict], tl: float, gap: float) -> dict:
    out = {}
    for kind in ("cold", "warm"):
        rs = [r[kind] for r in runs if kind in r]
        if not rs:
            continue
        tgt = TARGET_S.get(km)
        cert_in = [r.get("status") == "optimal" and (tgt is None or r["times"]["total_s"] <= tgt) for r in rs]
        # %1'e ulaşma: solve() hedef gap'e ulaşınca durmaz, süre içinde gap/2'ye dek iyileştirmeye devam eder; şartname
        # hedefi için ölçüt %1 sertifikasının (izden) hedef süre içinde gelmesi
        t1 = [r["time_to_gap_s"]["0.01"] for r in rs]
        reach_in = [t is not None and (tgt is None or t <= tgt) for t in t1]
        out[kind] = {
            "total_s": _stats([r["times"].get("total_s") for r in rs]),
            "time_to_1pct_s": _stats([r["time_to_gap_s"]["0.01"] for r in rs]),
            "time_to_0.5pct_s": _stats([r["time_to_gap_s"]["0.005"] for r in rs]),
            "first_feasible_s": _stats([r.get("first_feasible_s") for r in rs]),
            "final_gap": _stats([r.get("gap") for r in rs]),
            "ub_tl": _stats([r.get("ub_tl") for r in rs]),
            "lb_tl": _stats([r.get("lb_tl") for r in rs]),
            "n_hospitals": _stats([r.get("n_hospitals") for r in rs]),
            "phase_s": {p: _stats([r["times"].get(p) for r in rs])
                        for p in ("instance_s", "heuristic_s", "lagrange_s", "core_mip_s", "polish_s", "lagrange2_s",
                                  "mip_s")},
            "certified_within_target": sum(cert_in), "reached_1pct_within_target": sum(reach_in), "runs": len(rs),
            "success_rate": sum(reach_in) / len(rs),
            "final_gap_le_target": sum(1 for r in rs if r.get("gap") is not None and r["gap"] <= gap + 1e-9),
            "within_hard_limit": sum(1 for r in rs if r["times"].get("total_s", 1e9) <= tl + HARD_LIMIT_SLACK_S),
            "status_counts": {st: sum(1 for r in rs if r.get("status") == st) for st in sorted({r.get("status")
                                                                                              for r in rs})},
            "all_checks_ok": all(r.get("check_ok") for r in rs),
        }
    out["target_s"] = TARGET_S.get(km)
    out["time_limit_s"] = tl
    out["target_gap"] = gap
    return out


def baseline_summary() -> dict:
    if not BASELINE.exists():
        return {}
    b = json.loads(BASELINE.read_text(encoding="utf-8"))
    out = {}
    for run in b["runs"]:
        s = run["summary"]
        tr = run["result"].get("trace", [])
        out[str(run["grid_km"])] = {"time_limit_s": run["time_limit_s"], "final": s["final"],
                                    "root_lp": s["root_lp"], "heuristic": s["heuristic"],
                                    **time_to_gaps(tr)}
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--grids", default="5,3,2")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--worker", nargs="+", metavar="ARG", help="KM TL GAP TRACE [WARM]")
    ap.add_argument("--runs-by-grid", default=None, help="ör. 5=5,3=10,2=10 (--runs'ı ızgara başına geçersiz kılar)")
    ap.add_argument("--no-warm", action="store_true", help="yalnız soğuk çalıştırmalar")
    ap.add_argument("--reaggregate", action="store_true", help="saklanan çalıştırmalardan özetleri yeniden hesapla")
    args = ap.parse_args()
    if args.reaggregate:
        res = json.loads(args.out.read_text(encoding="utf-8"))
        for km, g in res["grids"].items():
            a = g["aggregate"]
            g["aggregate"] = aggregate(int(km), g["runs"], a["time_limit_s"], a["target_gap"])
        args.out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Özetler yeniden hesaplandı: {args.out}")
        return
    if args.worker:
        km, tl, gap, tr = args.worker[:4]
        warm_run = (args.worker[4] == "1") if len(args.worker) > 4 else True
        worker(int(km), None if tl == "None" else float(tl), float(gap), tr == "1", warm_run)
        return
    from backend.model.params import Params
    import highspy
    p = Params.load()
    gap = float(p["solver.mip_gap"])
    grids = [int(g) for g in args.grids.split(",")]
    result = {"generated_by": "pipeline/31_benchmark.py", "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
              "machine": {"python": sys.version.split()[0], "platform": platform.platform(),
                          "processor": platform.processor(), "cpu_count": os.cpu_count(),
                          "highspy": getattr(highspy, "__version__", "1.15.1"),
                          "note": "geliştirme konteyneri; sunum dizüstü (Windows, i7, Python 3.14) değil"},
              "params_hash": p.hash(), "targets_s": TARGET_S, "grids": {}, "baseline": baseline_summary()}
    runs_by = {int(a): int(b) for a, b in (kv.split("=") for kv in args.runs_by_grid.split(","))} \
        if args.runs_by_grid else {}
    if args.out.exists():
        old = json.loads(args.out.read_text(encoding="utf-8"))
        prev = {"generated_at": old.get("generated_at"), "machine": old.get("machine"),
                "note": "önceki kıyaslama (bu dosyanın önceki sürümü); yalnız özetler ve çalıştırma başına kısa kayıt",
                "grids": {km: {"aggregate": g.get("aggregate"),
                               "runs": [{kind: {key: r[kind].get(key) for key in ("status", "gap", "ub_tl", "lb_tl",
                                                                                 "time_to_gap_s")}
                                        | {"total_s": r[kind].get("times", {}).get("total_s")}
                                         for kind in ("cold", "warm") if kind in r} for r in g.get("runs", [])]}
                          for km, g in old.get("grids", {}).items()}}
        result["previous"] = prev
    for km in grids:
        tl = TIME_LIMIT.get(km) or float(p["solver.time_limit_s"])
        runs = []
        for i in range(runs_by.get(km, args.runs)):
            cmd = [sys.executable, str(Path(__file__).resolve()), "--worker", str(km), str(tl), str(gap),
                   "1" if i == 0 else "0", "0" if args.no_warm else "1"]
            t0 = time.perf_counter()
            pr = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
            line = next((ln for ln in pr.stdout.splitlines() if ln.startswith("@@")), None)
            if line is None:
                raise RuntimeError(f"{km} km çalıştırma {i + 1} başarısız:\n{pr.stdout[-2000:]}\n{pr.stderr[-4000:]}")
            rec = json.loads(line[2:])
            rec["process_wall_s"] = time.perf_counter() - t0
            runs.append(rec)
            c, w = rec["cold"], rec.get("warm")
            line = (f"{km} km #{i + 1}: soğuk {c['status']} gap {100 * (c['gap'] or 0):.2f}% "
                    f"toplam {c['times']['total_s']:.1f} sn (%1: {c['time_to_gap_s']['0.01']}) UB {c['ub_tl'] / 1e9:.3f}")
            if w is not None:
                line += (f" | ılık {w['status']} gap {100 * (w['gap'] or 0):.2f}% "
                         f"toplam {w['times']['total_s']:.1f} sn")
            print(line, flush=True)
        result["grids"][str(km)] = {"aggregate": aggregate(km, runs, tl, gap), "runs": runs}
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Yazıldı: {args.out.relative_to(ROOT) if args.out.is_relative_to(ROOT) else args.out}")


if __name__ == "__main__":
    main()
