"""İş türü 'explain' — TASLAK (Faz 6 doldurur): şartname 7.3 "Neden N hastane?" (N−1 / N+1 karşılaştırması) ve 7.4
"bu hastane kapatılırsa toplam maliyet ne kadar artar". Bunlar ek çözüm gerektirir (Faz 4 incelemesi ölçümü, 3 km, 89
hastane: bir hastane kapatılınca kalan 88'in değerlendirilmesi olurlu değilse 0,12 sn, olurluysa 10,7 sn), bu yüzden
tıklama başına senkron hesaplanmaz; arka plan işi olarak ilerlemeyle çalışır.

Altyapı hazır: istek modeli ve doğrulama (parametreler, kilitler, hücreler), önbellek anahtarı, bekçi süresi, öğe öğe
ilerleme olayları, iptal denetimi ve sonuç biçimi. Faz 6 yalnız COMPUTE sözlüğündeki üç fonksiyonun gövdesini yazar ve
JobType'taki available=False'u kaldırır. O zamana dek POST /api/explain ve POST /api/jobs/explain 501 döner (Türkçe
ileti); GET /api/meta → job_types.explain.available = false.

Sonuç biçimi (Faz 6 bu sözleşmeyi korumalı):
  {"ok": true, "status": "tamam" | "durduruldu", "grid_km", "mode", "params_hash", "base": {"objective_tl",
   "n_hospitals"}, "items": [ {"kind": "hastane_kapat", "cell_id", "feasible": bool, "objective_tl" | null,
   "delta_tl" | null, "message_tr"}, {"kind": "n_eksi_1" | "n_arti_1", "p", "status", "objective_tl",
   "opening_tl", "access_tl", "lb_tl", "gap", "delta_tl", "delta_opening_tl", "delta_access_tl", "message_tr"} ],
   "times": {...}}
"""
from __future__ import annotations

import time
from typing import Literal

from pydantic import Field

from .config import engine_version
from .schemas import CellId, HospitalIn, LocksIn, ProblemIn
from .tasks import ApiValidationError, JobContext, JobError, JobType, Prepared, register, stable_key
from .validation import check_cells, resolve_problem_and_locks

KIND_TR = {"hastane_kapat": "Hastane kapatılırsa", "n_eksi_1": "N − 1 hastaneyle en iyi çözüm",
           "n_arti_1": "N + 1 hastaneyle en iyi çözüm"}
# öğe başına bekçi payı (sn): kapatma = bir değerlendirme MIP'i (3 km ölçümü ≈11 sn; motorun evaluate süre sınırı 30 sn);
# N ± 1 = sabit_p modunda bir çözüm (ızgaranın önerilen süre sınırı)
ITEM_RUNTIME_S = {"hastane_kapat": 30.0, "n_eksi_1": 60.0, "n_arti_1": 60.0}
UNAVAILABLE_TR = ("Açıklama işleri (N − 1 / N + 1 karşılaştırması, 'bu hastane kapatılırsa') Faz 6'da eklenecek; şimdilik "
                  "kullanılamıyor.")


class ExplainIn(ProblemIn):
    locks: LocksIn = Field(default_factory=LocksIn)
    hospitals: list[HospitalIn] = Field(min_length=1, max_length=2000,
                                        description="Açıklanan çözümün hastaneleri (sonuçtaki cell_id, class, beds)")
    what: list[Literal["hastane_kapat", "n_eksi_1", "n_arti_1"]] = Field(min_length=1, max_length=3)
    close: list[CellId] = Field(default_factory=list, max_length=2000,
                                description="hastane_kapat: kapatılacak hastanelerin hücreleri (her biri ayrı öğe)")
    time_limit: float | None = Field(None, gt=0, le=600, description="Öğe başına süre sınırı (sn)")


def prepare(req: ExplainIn) -> Prepared:
    rp, locks = resolve_problem_and_locks(req, req.locks)
    km = rp.grid_km
    cells = [h.cell_id for h in req.hospitals]
    errors = check_cells(cells, km, ["body", "hospitals"], ["cell_id"])
    errors += check_cells(req.close, km, ["body", "close"])
    open_set = set(cells)
    for i, c in enumerate(req.close):
        if c not in open_set:
            errors.append({"loc": ["body", "close", i], "key": None, "value": c, "type": "acik_degil",
                           "message_tr": f"'{c}' açıklanan çözümün hastanelerinden biri değil."})
    if "hastane_kapat" in req.what and not req.close:
        errors.append({"loc": ["body", "close"], "key": None, "type": "eksik",
                       "message_tr": "hastane_kapat için en az bir hücre (close) gerekli."})
    if errors:
        raise ApiValidationError(errors, "Açıklama isteği doğrulanamadı.")
    items = [{"kind": "hastane_kapat", "cell_id": c} for c in req.close if "hastane_kapat" in req.what]
    items += [{"kind": k, "p": len(cells) + (1 if k == "n_arti_1" else -1)} for k in ("n_eksi_1", "n_arti_1")
              if k in req.what]
    hs = [{"cell_id": h.cell_id, "class": h.class_, "beds": h.beds} for h in req.hospitals]
    payload = {"grid_km": km, "overrides": rp.overrides, "locks": locks, "hospitals": hs, "items": items,
               "time_limit": req.time_limit}
    key = stable_key("explain", {"engine": engine_version(), "params": rp.params.effective_values(), "locks": locks,
                                 "hospitals": sorted((h["cell_id"], h["class"] or "", h["beds"] or 0) for h in hs),
                                 "items": items, "tl": req.time_limit})
    runtime = sum(ITEM_RUNTIME_S[it["kind"]] if req.time_limit is None else req.time_limit for it in items)
    return Prepared(payload=payload, cache_key=key, max_runtime_s=runtime + 30.0, warnings=rp.warnings,
                    info={"grid_km": km, "items": len(items), "what": list(req.what)})


# ---------------------------------------------------------------------- Faz 6: hesaplar (işçide)
def _close_hospital(ctx: JobContext, payload: dict, item: dict, base: dict) -> dict:
    """Faz 6: açık kümeden item['cell_id'] çıkarılır; kalan küme backend.model.evaluate.evaluate ile (cancel_event=
    ctx.cancel_event) değerlendirilir. Olurlu değilse (R içinde hastanesi kalmayan talep, kapasite) feasible=false ve
    motorun Türkçe iletisi; olurluysa objective_tl ve delta_tl = objective_tl − base['objective_tl']."""
    raise JobError(UNAVAILABLE_TR)


def _best_with_p(ctx: JobContext, payload: dict, item: dict, base: dict) -> dict:
    """Faz 6: sabit_p modunda (solver.objective_mode = sabit_p, solver.p_fixed = item['p']) backend.model.solve.solve;
    başlangıç adayı mevcut çözüm (bir hastane eksik/fazla). Açılış / erişim maliyeti farkları base'e göre."""
    raise JobError(UNAVAILABLE_TR)


COMPUTE = {"hastane_kapat": _close_hospital, "n_eksi_1": _best_with_p, "n_arti_1": _best_with_p}


def run(ctx: JobContext, payload: dict) -> dict:
    T0 = time.perf_counter()
    items = payload["items"]
    base: dict = {"n_hospitals": len(payload["hospitals"]), "objective_tl": None}   # Faz 6: temel çözümün değeri
    out = []
    status = "tamam"
    for n, item in enumerate(items):
        if ctx.cancelled():
            status = "durduruldu"
            break
        ctx.emit({"phase": item["kind"], "phase_tr": KIND_TR[item["kind"]], "done": n, "total": len(items),
                  "item": item}, force=True)
        out.append({**item, **COMPUTE[item["kind"]](ctx, payload, item, base)})
    ctx.emit({"phase": "bitti", "phase_tr": "Bitti", "done": len(out), "total": len(items)}, force=True)
    return {"ok": True, "status": status, "grid_km": payload["grid_km"], "base": base, "items": out,
            "times": {"total_s": time.perf_counter() - T0}}


register(JobType(
    name="explain", request_model=ExplainIn, prepare=prepare, run=run,
    description_tr="Açıklama: N − 1 / N + 1 hastaneyle en iyi çözüm ve 'bu hastane kapatılırsa' maliyet farkı "
                   "(şartname 7.3–7.4; Faz 6).",
    available=False, unavailable_tr=UNAVAILABLE_TR,
    cache_result=lambda res: bool(res.get("ok")) and res.get("status") == "tamam",
))
