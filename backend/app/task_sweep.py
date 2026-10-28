"""İş türü 'sweep' (Faz 6; şartname 7.7): p taraması → U eğrisi (toplam = açılış + erişim, hastane sayısına göre).

Her p için sabit_p modunda çözüm; noktalar temel çözümün hastane sayısından dışa doğru (|p − n| artan) çözülür ve her
noktanın başlangıcı en yakın çözülmüş p'nin çözümünden tek tek kapatma/açma ile türetilir (sıcak başlangıç). Her nokta
için alt / üst sınır, gap ve 'fark gap içinde' işareti (alt sınırı temel çözümün bulunan değerini aşmayan p, temel
çözümden ayırt edilemez). Temel çözüm toplam modundaysa onun alt sınırı her p için de geçerlidir (serbest problem her
sabit p probleminin gevşetmesidir).

Varsayılan parametrelerde precomputed/p_sweep_{3,5}km.json'un uzun çözülmüş (dibi simetrik başlangıçlarla yeniden
çözülmüş) noktaları yeniden çözülmez; istenen p'lerin hepsi dosyadaysa yanıt sunucu tarafında, kuyruğa girmeden döner.
İlerleme: her nokta bitince {phase: 'nokta', done, total, point}; nokta içindeki çözümün lb/ub'si 'inner' alanında.
İptal: o ana dek çözülen noktalar 'durduruldu' sonucuyla döner.
"""
from __future__ import annotations

import time

from pydantic import Field

from .config import PRECOMPUTED_DIR, engine_version
from .schemas import HospitalIn, LocksIn, ProblemIn
from .task_explain import (BaseBoundsIn, InnerProgress, base_from_payload, bounds_payload, capture_cache,
                           default_time_limit, hospitals_payload, load_problem)
from . import task_explain as TE
from .tasks import ApiValidationError, JobContext, JobType, Prepared, register, stable_key, time_bucket
from .validation import cache_params, check_cells, resolve_problem_and_locks

MAX_POINTS = 60
DEFAULT_HALF_WIDTH = 6
DEFAULT_GAP = 1e-3


class SweepIn(ProblemIn):
    locks: LocksIn = Field(default_factory=LocksIn)
    hospitals: list[HospitalIn] = Field(min_length=1, max_length=2000,
                                        description="Temel çözümün hastaneleri (referans ve sıcak başlangıç)")
    p_values: list[int] | None = Field(None, min_length=1, max_length=MAX_POINTS,
                                       description="Taranacak hastane sayıları; yoksa n ± half_width")
    half_width: int = Field(DEFAULT_HALF_WIDTH, ge=1, le=30)
    step: int = Field(1, ge=1, le=20)
    time_limit: float | None = Field(None, gt=0, le=600, description="Nokta başına süre sınırı (sn)")
    gap: float | None = Field(None, ge=0, le=1)
    base_bounds: BaseBoundsIn | None = None


def _p_list(req: SweepIn, n: int) -> list[int]:
    if req.p_values:
        return sorted({int(p) for p in req.p_values})
    return sorted({p for p in range(n - req.half_width, n + req.half_width + 1, 1) if p >= 1 and
                   (abs(p - n) % req.step == 0)})


def _default_sweep(rp, locks) -> dict | None:
    """Varsayılan parametrelerin (sabit_p anahtarları hariç) önceden hesaplanmış taraması."""
    from backend.model.params import Params
    if locks["open"] or locks["closed"]:
        return None
    ov = {k: v for k, v in rp.overrides.items() if k not in ("solver.objective_mode", "solver.p_fixed")}
    if Params.load(ov).hash() != Params.load({"grid.cell_km": rp.grid_km}).hash():
        return None
    for path, doc in TE.precomputed_docs("p_sweep"):
        if int(doc.get("grid_km") or 0) == rp.grid_km:
            return {"name": path.stem, "doc": doc}
    return None


def prepare(req: SweepIn) -> Prepared:
    rp, locks = resolve_problem_and_locks(req, req.locks)
    km = rp.grid_km
    errors = check_cells([h.cell_id for h in req.hospitals], km, ["body", "hospitals"], ["cell_id"])
    if rp.params.objective_mode == "butce":
        errors.append({"loc": ["body", "mode"], "key": "solver.objective_mode", "type": "mod_uyumsuz",
                       "message_tr": "p taraması toplam ya da sabit p modunda tanımlıdır (bütçe modunda hastane "
                                     "sayısını bütçe belirler)."})
    n = len(req.hospitals)
    ps = _p_list(req, n)
    if len(ps) > MAX_POINTS:
        errors.append({"loc": ["body", "p_values"], "key": None, "type": "too_long",
                       "message_tr": f"En çok {MAX_POINTS} nokta taranabilir."})
    bad = [p for p in ps if p < 1]
    if bad:
        errors.append({"loc": ["body", "p_values"], "key": None, "type": "aralik_disi",
                       "message_tr": "Hastane sayısı en az 1 olmalı."})
    if errors:
        raise ApiValidationError(errors, "p taraması isteği doğrulanamadı.")
    tl = float(req.time_limit) if req.time_limit is not None else default_time_limit(km)
    gap = float(req.gap) if req.gap is not None else DEFAULT_GAP
    hs = hospitals_payload(req.hospitals)
    bb = bounds_payload(req, rp, locks)
    pre = _default_sweep(rp, locks)
    pre_ps = sorted(int(q["p"]) for q in pre["doc"]["points"] if q.get("ok")) if pre else []
    payload = {"grid_km": km, "overrides": rp.overrides, "locks": locks, "hospitals": hs, "p_values": ps,
               "time_limit": tl, "gap": gap, "base_bounds": bb, "precomputed": pre["name"] if pre else None}
    key = stable_key("sweep", {"engine": engine_version(), "params": cache_params(rp.params), "locks": locks,
                               "hospitals": sorted((h["cell_id"], h["class"] or "", h["beds"] or 0) for h in hs),
                               "p": ps, "bounds": [bb.get("ub_tl"), bb.get("lb_tl")]})
    variant = {"tl": time_bucket(tl), "gap": gap}
    live = [p for p in ps if p not in pre_ps and p != n]
    if pre is not None and not live and bb.get("ub_tl") is not None and TE._CACHE is not None:
        res = _answer_from_file(pre, ps, n, hs, bb, rp.params.objective_mode, km)
        try:
            if not TE._CACHE.has(key, dict(variant, pre=True)):
                TE._CACHE.put(key, dict(variant, pre=True), res, job_type="sweep")
        except Exception:
            pass
    return Prepared(payload=payload, cache_key=key, variant=variant,
                    max_runtime_s=len(live) * (tl + 30.0) + 60.0, warnings=rp.warnings,
                    info={"grid_km": km, "points": len(ps), "live_points": len(live), "time_limit_s": tl})


def _answer_from_file(pre: dict, ps: list[int], n: int, hs: list[dict], bb: dict, mode: str, km: int) -> dict:
    """İstenen p'lerin hepsi dosyada: referans farkları ve işaretler sunucuda hesaplanır (çözüm yok)."""
    from backend.model import explain_common as X
    doc = pre["doc"]
    by_p = {int(q["p"]): q for q in doc["points"] if q.get("ok")}
    free = doc.get("free") or {}
    ref = {"n": n, "ub_tl": float(bb["ub_tl"]), "lb_tl": bb.get("lb_tl"), "free_lb_valid": mode == "toplam" and
           bb.get("lb_tl") is not None, "cells": [h["cell_id"] for h in hs]}
    if n in by_p:
        q = by_p[n]
        ref.update(opening_tl=q.get("opening_tl"), access_tl=q.get("access_tl"),
                   pop_weighted_mean_km=q.get("pop_weighted_mean_km"), objective_tl=q.get("ub_tl"))
    elif free.get("n_hospitals") == n:
        ref.update(opening_tl=free.get("opening_tl"), access_tl=free.get("access_tl"))
    pts = []
    for p in ps:
        q = dict(by_p[p]) if p in by_p else None
        if q is None:
            continue
        q.pop("refine", None)
        q["source"] = "onceden"
        X.apply_reference(q, ref)
        pts.append(q)
    ok = [q for q in pts if q.get("ok")]
    best = min(ok, key=lambda q: (q["ub_tl"], q["p"] != n)) if ok else None
    res = {"status": "tamam", "points": pts, "reference": {k: v for k, v in ref.items() if k != "cells"},
           "best_p": best["p"] if best else None, "best_ub_tl": best["ub_tl"] if best else None,
           "plateau_p": [q["p"] for q in ok if q.get("referans") or q.get("fark_gap_icinde")]}
    res["summary_tr"] = X.sweep_summary(res, ref)
    acc = doc.get("acceptance") or {}
    res.update(ok=True, grid_km=km, mode=mode, precomputed={"name": pre["name"], "generated_at": doc.get("generated_at"),
                                                            "summary_tr": acc.get("summary_tr"),
                                                            "plateau_p": acc.get("plateau_p")},
               times={"total_s": 0.0})
    return res


def run(ctx: JobContext, payload: dict) -> dict:
    from backend.model import explain as X
    from backend.model.solve import _jsonable

    T0 = time.perf_counter()
    params, inst = load_problem(payload, [h["cell_id"] for h in payload["hospitals"]])
    ps = payload["p_values"]
    ctx.emit({"phase": "temel", "phase_tr": "Temel çözüm değerlendiriliyor", "done": 0, "total": len(ps)}, force=True)
    ev, ref = base_from_payload(inst, payload, ctx)
    bb = dict(payload.get("base_bounds") or {})
    ref["ub_tl"] = min(float(bb.get("ub_tl") or ref["ub_tl"]), float(ev.objective_tl))
    pre_points = None
    meta = None
    if payload.get("precomputed"):
        path = PRECOMPUTED_DIR / f"{payload['precomputed']}.json"
        for p_, doc in TE.precomputed_docs("p_sweep"):
            if p_ == path:
                pre_points = {int(q["p"]): {k: v for k, v in q.items() if k != "refine"}
                              for q in doc["points"] if q.get("ok")}
                acc = doc.get("acceptance") or {}
                meta = {"name": path.stem, "generated_at": doc.get("generated_at"),
                        "summary_tr": acc.get("summary_tr"), "plateau_p": acc.get("plateau_p")}

    outer = {"phase": "nokta", "phase_tr": "p taraması", "done": 0, "total": len(ps)}

    def on_point(pt: dict, done: int, total: int) -> None:
        brief = {k: pt.get(k) for k in ("p", "ok", "status", "ub_tl", "lb_tl", "gap", "opening_tl", "access_tl",
                                         "total_tl", "fark_tl", "fark_gap_icinde", "source", "count_by_class")}
        outer["done"] = done                    # nokta içi ('inner') olaylar da tamamlanan nokta sayısını taşır
        ctx.emit({"phase": "nokta", "phase_tr": f"p = {pt.get('p')} çözüldü", "done": done, "total": total,
                  "point": brief}, force=True)

    res = X.p_sweep(inst, ev.solution, ps, ref=ref, time_limit=payload["time_limit"], gap=payload["gap"],
                    precomputed=pre_points, cancel_event=ctx.cancel_event, on_point=on_point,
                    progress_cb=InnerProgress(ctx, outer))
    if ctx.cancelled():
        res["status"] = "durduruldu"
    ctx.emit({"phase": "bitti", "phase_tr": "Bitti", "done": len(res["points"]), "total": len(ps)}, force=True)
    res.update(ok=True, grid_km=int(payload["grid_km"]), mode=inst.mode, params_hash=params.hash(),
               precomputed=meta, warnings=list(inst.warnings), times={"total_s": time.perf_counter() - T0})
    return _jsonable(res)


def satisfies(c: dict, r: dict) -> bool:
    return bool(c.get("pre")) or (c.get("tl", 0) >= r.get("tl", 0) and c.get("gap", 1) <= r.get("gap", 1) + 1e-12)


def seed(cache) -> int:
    capture_cache(cache)
    return 0


register(JobType(
    name="sweep", request_model=SweepIn, prepare=prepare, run=run,
    description_tr="p taraması (U eğrisi): her hastane sayısı için en iyi çözüm, alt/üst sınır ve 'fark gap içinde' "
                   "işareti (şartname 7.7).",
    satisfies=satisfies, seed=seed,
    cache_result=lambda res: bool(res.get("ok")) and res.get("status") == "tamam",
))
