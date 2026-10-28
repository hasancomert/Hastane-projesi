"""İş türü 'sensitivity' (Faz 6; şartname 7.9): tornado — her önemli parametre ±%20 → toplam maliyet ve hastane sayısı.

Parametreler backend/model/explain.py TORNADO_SPECS'te (16 satır; tebliğ birim maliyetleri tek grup). Her satır için iki
çalışma (taban × 0,8 ve × 1,2; tanım kümesi dışına çıkan değer sınıra çekilir ve işaretlenir): AYNI süre sınırı, AYNI
gap ve AYNI sıcak başlangıç (temel çözüm o parametrelerle yeniden değerlendirilir) — iki yön simetrik. Temel değer:
istekteki çözümün değeri (hospitals; yoksa aynı ayarlarla temel çözüm de çözülür). Her çalışmanın gerçek farkı
[L(çalışma) − U(temel), U(çalışma) − L(temel)] aralığındadır; aralık sıfırı içeriyorsa farkın işareti kesin değildir
(`sign_certain: false`). Önceden hesaplanmış sonuçlar: precomputed/tornado_*.json (pipeline/35_explanations.py).
"""
from __future__ import annotations

import time

from pydantic import Field

from .config import engine_version
from .schemas import HospitalIn, LocksIn, ProblemIn
from . import task_explain as TE
from .task_explain import BaseBoundsIn, InnerProgress, bounds_payload, default_time_limit, hospitals_payload, load_problem
from .tasks import ApiValidationError, JobContext, JobError, JobType, Prepared, register, stable_key, time_bucket
from .validation import cache_params, check_cells, resolve_problem_and_locks

DEFAULT_GAP = 2e-3


class SensitivityIn(ProblemIn):
    locks: LocksIn = Field(default_factory=LocksIn)
    keys: list[str] | None = Field(None, min_length=1, max_length=40,
                                   description="Tornado satırları (kimlikler GET /api/meta → tornado_params); yoksa hepsi")
    delta: float = Field(0.2, gt=0, le=0.5, description="Göreli değişim (0,2 = ±%20)")
    hospitals: list[HospitalIn] | None = Field(None, max_length=2000, description="Temel çözüm (yoksa çözülür)")
    base_bounds: BaseBoundsIn | None = None
    time_limit: float | None = Field(None, gt=0, le=600, description="Çalışma başına süre sınırı (sn)")
    gap: float | None = Field(None, ge=0, le=1)


def spec_ids() -> list[str]:
    from backend.model.explain_common import TORNADO_SPECS
    return [s for s, _ in TORNADO_SPECS]


def _answer_from_file(rp, locks, ids: list[str], delta: float) -> dict | None:
    m = TE.match_precomputed("tornado", rp.params, locks)
    if m is None:
        return None
    path, doc = m
    if abs(float(doc.get("delta", -1)) - delta) > 1e-12:
        return None
    rows = {r["id"]: r for r in doc.get("rows", [])}
    if not all(i in rows for i in ids):
        return None
    res = {k: v for k, v in doc.items() if k != "rows"}
    res["rows"] = sorted((rows[i] for i in ids), key=lambda r: -(r.get("swing_pct") or 0.0))
    res["precomputed"] = {"name": path.stem, "generated_at": doc.get("generated_at"),
                          "generated_by": doc.get("generated_by"), "settings": doc.get("settings")}
    res["ok"], res["status"] = True, doc.get("status", "tamam")
    return res


def prepare(req: SensitivityIn) -> Prepared:
    rp, locks = resolve_problem_and_locks(req, req.locks)
    km = rp.grid_km
    errors = check_cells([h.cell_id for h in (req.hospitals or [])], km, ["body", "hospitals"], ["cell_id"])
    all_ids = spec_ids()
    ids = list(dict.fromkeys(req.keys)) if req.keys else all_ids
    unknown = [k for k in ids if k not in all_ids]
    if unknown:
        errors.append({"loc": ["body", "keys"], "key": None, "type": "bilinmeyen_parametre", "value": unknown[:10],
                       "message_tr": f"Tornado satırı değil: {', '.join(unknown[:5])}. İzinli: {', '.join(all_ids)}.",
                       "suggestions": all_ids})
    if errors:
        raise ApiValidationError(errors, "Duyarlılık isteği doğrulanamadı.")
    ids = [i for i in all_ids if i in ids]                 # kararlı sıra
    tl = float(req.time_limit) if req.time_limit is not None else default_time_limit(km)
    gap = float(req.gap) if req.gap is not None else DEFAULT_GAP
    hs = hospitals_payload(req.hospitals) if req.hospitals else None
    bb = bounds_payload(req, rp, locks) if hs else {}
    payload = {"grid_km": km, "overrides": rp.overrides, "locks": locks, "keys": ids, "delta": float(req.delta),
               "hospitals": hs, "base_bounds": bb, "time_limit": tl, "gap": gap}
    key = stable_key("sensitivity", {"engine": engine_version(), "params": cache_params(rp.params), "locks": locks,
                                     "keys": ids, "delta": float(req.delta),
                                     "hospitals": sorted((h["cell_id"], h["class"] or "", h["beds"] or 0) for h in hs)
                                     if hs else None})
    variant = {"tl": time_bucket(tl), "gap": gap}
    pre = _answer_from_file(rp, locks, ids, float(req.delta))
    if pre is not None and TE._CACHE is not None:
        try:
            if not TE._CACHE.has(key, dict(variant, pre=True)):
                TE._CACHE.put(key, dict(variant, pre=True), pre, job_type="sensitivity")
        except Exception:
            pass
    runs = 2 * len(ids) + (0 if hs else 1)
    return Prepared(payload=payload, cache_key=key, variant=variant, max_runtime_s=runs * (tl + 30.0) + 60.0,
                    warnings=rp.warnings, info={"grid_km": km, "rows": len(ids), "runs": runs, "time_limit_s": tl,
                                                "precomputed": pre is not None})


def run(ctx: JobContext, payload: dict) -> dict:
    from backend.model import explain as X
    from backend.model.solve import _jsonable, solve

    T0 = time.perf_counter()
    km = int(payload["grid_km"])
    total = 2 * len(payload["keys"])
    ctx.emit({"phase": "temel", "phase_tr": "Temel çözüm", "done": 0, "total": total}, force=True)
    params, inst = load_problem(payload, [h["cell_id"] for h in payload["hospitals"] or []])
    if payload.get("hospitals"):
        ev, ref = TE.base_from_payload(inst, payload, ctx)
        bb = dict(payload.get("base_bounds") or {})
        sol = ev.solution
        base = {"objective_tl": min(float(ev.objective_tl), float(bb.get("ub_tl") or ev.objective_tl)),
                "lb_tl": bb.get("lb_tl"), "n_hospitals": sol.n_open, "source": "istek",
                "opening_tl": ref["opening_tl"], "access_tl": ref["access_tl"],
                "hospitals": [str(c) for c in inst.s.cand_ids[sol.open_j]],
                "classes": [inst.classes[int(k)].code for k in sol.cls]}
    else:
        r = solve(instance=inst, time_limit=payload["time_limit"], gap=payload["gap"], cancel_event=ctx.cancel_event,
                  progress_cb=InnerProgress(ctx, {"phase": "temel", "phase_tr": "Temel çözüm", "total": total}))
        if not r.get("ok"):
            raise JobError(f"Temel çözüm bulunamadı: {r.get('message_tr')}")
        base = {"objective_tl": r["bounds"]["ub_tl"], "lb_tl": r["bounds"]["lb_tl"],
                "n_hospitals": r["kpis"]["n_hospitals"], "source": "cozuldu", "opening_tl": r["costs"]["acilis"],
                "access_tl": r["costs"]["erisim"], "hospitals": [h["cell_id"] for h in r["hospitals"]],
                "classes": [h["class"] for h in r["hospitals"]]}

    outer = {"phase": "calisma", "phase_tr": "Tornado", "done": 0, "total": total}

    def on_run(rr: dict, done: int, total_: int) -> None:
        brief = {k: rr.get(k) for k in ("id", "side", "factor", "ok", "status", "delta_pct", "delta_n", "n_hospitals",
                                         "objective_tl", "gap", "sign_certain")}
        outer["done"] = done
        ctx.emit({"phase": "calisma", "phase_tr": f"{rr['id']} ({'−' if rr['side'] == 'dusuk' else '+'}"
                                                  f"%{X.fmt(100 * payload['delta'], 0)})", "done": done,
                  "total": total_, "run": brief}, force=True)

    res = X.tornado(km, payload["overrides"], payload["locks"], delta=payload["delta"], keys=payload["keys"],
                    time_limit=payload["time_limit"], gap=payload["gap"], base=base, cancel_event=ctx.cancel_event,
                    on_run=on_run, progress_cb=InnerProgress(ctx, outer))
    if ctx.cancelled():
        res["status"] = "durduruldu"
    ctx.emit({"phase": "bitti", "phase_tr": "Bitti", "done": total, "total": total}, force=True)
    res.update(ok=True, grid_km=km, mode=inst.mode, params_hash=params.hash(), locks=payload["locks"],
               settings={"time_limit_s": payload["time_limit"], "gap": payload["gap"]},
               times={"total_s": time.perf_counter() - T0})
    return _jsonable(res)


def satisfies(c: dict, r: dict) -> bool:
    return bool(c.get("pre")) or (c.get("tl", 0) >= r.get("tl", 0) and c.get("gap", 1) <= r.get("gap", 1) + 1e-12)


def seed(cache) -> int:
    TE.capture_cache(cache)
    return TE.seed_defaults("tornado", lambda km: prepare(SensitivityIn(grid_km=km)))


register(JobType(
    name="sensitivity", request_model=SensitivityIn, prepare=prepare, run=run,
    description_tr="Duyarlılık (tornado): önemli parametreler ±%20 → toplam maliyet ve hastane sayısı değişimi "
                   "(şartname 7.9).",
    satisfies=satisfies, seed=seed,
    cache_result=lambda res: bool(res.get("ok")) and res.get("status") == "tamam",
))
