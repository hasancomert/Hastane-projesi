"""İş türü 'explain' (Faz 6; şartname 7.3–7.6): "neden bu çözüm?" açıklamaları — arka plan işi, öğe öğe ilerleme.

Öğeler (istekteki `what`):
  * hastane_kapat   7.4 "bu hastane kapatılırsa": `close` listesindeki (ya da close_all ile tüm) hastaneler için hızlı
                    tahmin = evaluate(açık küme − j): konumlar sabit, yeniden atama + yeniden boyutlandırma (en yakın
                    komşularda sınıf seçimi). Kalan hastanelerle olurlu bir çözüm olduğundan gerçek (konumları yeniden
                    optimize edilmiş) artışın ÜST sınırıdır. exact=true: tüm hastanelerde sınıf seçimli kesin
                    değerlendirme; reoptimize=true: j yasakken tam yeniden çözüm (başlangıç hızlı tahmin). Hangi
                    hastanelerin hastaları devraldığı (`absorbers`), sınıfı/yatağı değişenler, R içinde açık hastanesi
                    kalmayan hücreler (R'nin zorladığı hastaneler) raporlanır.
  * n_eksi_1/n_arti_1  7.3 "neden N?": sabit_p modunda N ∓ 1 hastaneyle en iyi çözüm (başlangıç mevcut çözümden bir
                    hastane kapatma/açma ile), açılış / erişim / toplam farkları, alt ve üst sınır, 'fark gap içinde'.
                    Varsayılan parametrelerde precomputed/p_sweep_*.json'un uzun çözülmüş noktaları kullanılır.
  * boyut           7.5 "neden bu boyut?" (her hastane): atanan talep, doluluk, sınıf eşiğindeki sıçrama, 1 ek yatağın
                    erişim değeri ↔ yatak başı maliyet, neden kodu (ust_esik · ust_sinir · alt_sinir · ic_denge).
  * golge_fiyat     7.6 gölge fiyatlar: atama LP'sinin dual değerleri (hücre başına marjinal hizmet maliyeti, hastane
                    başına 1 ek yatağın değeri), özet, Türkçe yorum ve uyarı (dejenere LP'de dual tek değildir).
  * model           model düzeyindeki nedenler (sınıf maliyet tablosu, bölme karşılaştırması, eşiklerde yığılma, R'nin
                    zorladığı hastaneler, şehir hastanesi, uzmanlaşmanın modellenmediği).

Hesaplar motor yardımcılarındadır (backend/model/explain.py). Önceden hesaplanmış açıklamalar (precomputed/explain_*.json,
pipeline/35_explanations.py) aynı problem ve aynı temel çözüm için hem işçide hem — sunucu tarafında, kuyruğa
girmeden — önbellek yoluyla anında döner. Sonuç sözleşmesi docs/API.md §11.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Literal

import orjson
from pydantic import Field

from .config import PRECOMPUTED_DIR, RECOMMENDED_TIME_LIMIT_S, engine_version
from .schemas import CellId, HospitalIn, LocksIn, ProblemIn, _Base
from .tasks import ApiValidationError, JobContext, JobError, JobType, Prepared, register, stable_key, time_bucket
from .validation import cache_params, check_cells, resolve_problem_and_locks

log = logging.getLogger("hastane.explain")

KIND_TR = {"hastane_kapat": "Hastane kapatılırsa", "n_eksi_1": "N − 1 hastaneyle en iyi çözüm",
           "n_arti_1": "N + 1 hastaneyle en iyi çözüm", "boyut": "Neden bu boyut?", "golge_fiyat": "Gölge fiyatlar",
           "model": "Model düzeyindeki nedenler"}
WHAT = ("hastane_kapat", "n_eksi_1", "n_arti_1", "boyut", "golge_fiyat", "model")
DEFAULT_GAP = 1e-3            # N ± 1 ve yeniden çözümlerin hedef gap'i (süre sınırına dek iyileştirir)
# bekçi payları (sn, öğe başına): hızlı kapatma ≈0,1–1 sn (3 km), kesin değerlendirme ≤ 2 × 30 sn
CLOSE_QUICK_S, CLOSE_EXACT_S, MISC_S = 10.0, 65.0, 15.0


class BaseBoundsIn(_Base):
    """Açıklanan çözümün çözücü sınırları (sonuçtaki bounds): 'fark gap içinde' işaretleri için."""

    ub_tl: float | None = Field(None, gt=0)
    lb_tl: float | None = Field(None, gt=0)


class ExplainIn(ProblemIn):
    locks: LocksIn = Field(default_factory=LocksIn)
    hospitals: list[HospitalIn] = Field(min_length=1, max_length=2000,
                                        description="Açıklanan çözümün hastaneleri (sonuçtaki cell_id, class, beds)")
    what: list[Literal["hastane_kapat", "n_eksi_1", "n_arti_1", "boyut", "golge_fiyat", "model"]] = Field(
        min_length=1, max_length=6)
    close: list[CellId] = Field(default_factory=list, max_length=2000,
                                description="hastane_kapat: kapatılacak hastanelerin hücreleri (her biri ayrı öğe)")
    close_all: bool = Field(False, description="hastane_kapat: çözümün TÜM hastaneleri (close yok sayılır)")
    exact: bool = Field(False, description="hastane_kapat: tüm hastanelerde sınıf seçimli kesin değerlendirme")
    reoptimize: bool = Field(False, description="hastane_kapat: j yasakken tam yeniden çözüm (öğe başına time_limit)")
    time_limit: float | None = Field(None, gt=0, le=600, description="N ± 1 / yeniden çözüm başına süre sınırı (sn)")
    gap: float | None = Field(None, ge=0, le=1, description="N ± 1 / yeniden çözüm hedef gap'i")
    base_bounds: BaseBoundsIn | None = Field(None, description="Açıklanan çözümün sınırları (yoksa önbellekten)")


# ---------------------------------------------------------------------- ortak yardımcılar (Faz 6 iş türleri)
_CACHE = None                 # sunucu açılışında seed() ile yakalanan önbellek (prepare'de başvuru alt sınırı için)
_PRE_LOCK = threading.Lock()
_PRE_INDEX: dict[str, tuple] = {}


def capture_cache(cache) -> None:
    global _CACHE
    _CACHE = cache


def reference_lb(params, locks: dict) -> float | None:
    """Aynı problemin (parametreler + kilitler) önbellekteki solve kayıtlarının en büyük alt sınırı: problemin her
    çözümü için geçerli alt sınırdır (hangi çözüm açıklanırsa açıklansın)."""
    if _CACHE is None:
        return None
    from .task_solve import solve_key
    try:
        lbs = [float(e.variant["lb"]) for e in _CACHE.entries(solve_key(params, locks))
               if e.variant.get("lb") is not None]
    except Exception:
        return None
    return max(lbs) if lbs else None


def precomputed_docs(prefix: str) -> list[tuple[Path, dict]]:
    """precomputed/<prefix>_*.json belgeleri (değiştirilme zamanına göre bellekte; okunamayan atlanır)."""
    out = []
    for path in sorted(PRECOMPUTED_DIR.glob(f"{prefix}_*.json")):
        try:
            st = path.stat()
            sig = (st.st_mtime_ns, st.st_size)
            with _PRE_LOCK:
                hit = _PRE_INDEX.get(str(path))
            if hit is None or hit[0] != sig:
                doc = orjson.loads(path.read_bytes())
                with _PRE_LOCK:
                    _PRE_INDEX[str(path)] = (sig, doc)
            else:
                doc = hit[1]
            out.append((path, doc))
        except (OSError, orjson.JSONDecodeError) as ex:
            log.warning("%s okunamadı: %s", path.name, ex)
    return out


def match_precomputed(prefix: str, params, locks: dict) -> tuple[Path, dict] | None:
    """Aynı parametre özeti (params.hash) ve kilitlerle önceden hesaplanmış belge."""
    h = params.hash()
    for path, doc in precomputed_docs(prefix):
        if doc.get("params_hash") == h and _same_locks(doc.get("locks"), locks):
            return path, doc
    return None


def _same_locks(a: dict | None, b: dict | None) -> bool:
    a, b = a or {}, b or {}
    return sorted(a.get("open") or []) == sorted(b.get("open") or []) and \
        sorted(a.get("closed") or []) == sorted(b.get("closed") or [])


def same_base(doc_hospitals: list[dict], req_hospitals: list[dict]) -> bool:
    """İstekteki temel çözüm belgedekiyle aynı mı (hücreler, sınıflar; yataklar 0,01 yatak toleransla)."""
    if len(doc_hospitals) != len(req_hospitals):
        return False
    a = {h["cell_id"]: h for h in doc_hospitals}
    for h in req_hospitals:
        d = a.get(h["cell_id"])
        if d is None:
            return False
        if h.get("class") is not None and h["class"] != d.get("class"):
            return False
        if h.get("beds") is not None and abs(float(h["beds"]) - float(d.get("beds") or 0)) > 0.01:
            return False
    return True


def seed_defaults(prefix: str, make_prepared) -> int:
    """Varsayılan parametrelerin (her ızgara) önceden hesaplanmış sonucu varsa varsayılan isteği hazırlar: prepare()
    yanıtı önbelleğe koyar. Döner: önbellekte bulunan/eklenen kayıt sayısı."""
    from backend.model.params import Params

    from .config import GRID_SIZES
    n = 0
    for km in GRID_SIZES:
        try:
            if match_precomputed(prefix, Params.load({"grid.cell_km": km}), {"open": [], "closed": []}) is None:
                continue
            prep = make_prepared(km)
            if prep.cache_key and _CACHE is not None and _CACHE.has(prep.cache_key, dict(prep.variant, pre=True)):
                n += 1
        except Exception as ex:
            log.warning("%s %d km önbelleğe tohumlanamadı: %s", prefix, km, ex)
    return n


class InnerProgress:
    """Bir taramanın içindeki motor çözümünün ilerlemesini (lb, ub, evre) dış olaya ekler (seyrekleştirilmiş)."""

    def __init__(self, ctx: JobContext, outer: dict) -> None:
        self.ctx, self.outer = ctx, outer
        self.lb = self.ub = None
        self.lock = threading.Lock()

    def __call__(self, ev: dict) -> None:
        kind = ev.get("kind")
        with self.lock:
            if kind in ("cozum", "polish") and ev.get("primal") is not None:
                self.ub = ev["primal"] if self.ub is None else min(self.ub, ev["primal"])
            elif kind in ("lagrange", "butce_lagrange"):
                d = ev.get("dual", ev.get("lb"))
                if d is not None:
                    self.lb = d if self.lb is None else max(self.lb, d)
            else:
                return
            inner = {"lb": self.lb, "ub": self.ub,
                     "gap": (self.ub - self.lb) / self.ub if self.lb is not None and self.ub else None}
        self.ctx.emit({**self.outer, "inner": inner})


def default_time_limit(km: int) -> float:
    return float(RECOMMENDED_TIME_LIMIT_S.get(km, 15.0))


def load_problem(payload: dict, extra_open: list[str] | None = None):
    """İşçide: parametreler + örnek (aday olmayan temel hücreler 'aç' kilidiyle eklenir; yapı değişir)."""
    from backend.model.instance import build_instance
    from backend.model.params import Params
    params = Params.load(payload["overrides"])
    locks = {"open": list(payload["locks"]["open"]), "closed": list(payload["locks"]["closed"])}
    inst = build_instance(int(payload["grid_km"]), params, locks)
    if extra_open:
        pos = {str(c) for c in inst.s.cand_ids}
        add = sorted({c for c in extra_open if c not in pos})
        if add:
            locks["open"] = sorted(set(locks["open"]) | set(add))
            inst = build_instance(int(payload["grid_km"]), params, locks)
            inst.warnings.append(f"{len(add)} hücre aday kuralına göre aday değil; 'aç' kilidiyle eklendi: "
                                 f"{', '.join(add[:5])}")
    return params, inst


def base_from_payload(inst, payload: dict, ctx: JobContext | None = None):
    """Temel çözüm (EvalResult) ve referans özeti."""
    from backend.model import explain as X
    ev = X.hospital_solution(inst, payload["hospitals"], cancel_event=ctx.cancel_event if ctx else None)
    if not ev.ok:
        raise JobError(f"Açıklanan çözüm bu parametrelerle değerlendirilemedi: {ev.message}")
    bb = dict(payload.get("base_bounds") or {})
    ref = X.reference_of(inst, ev.solution, bb)
    return ev, ref


def hospitals_payload(hs: list[HospitalIn]) -> list[dict]:
    return [{"cell_id": h.cell_id, "class": h.class_, "beds": h.beds} for h in hs]


def bounds_payload(req, rp, locks) -> dict:
    """İstekteki base_bounds; alt sınır yoksa önbellekteki aynı problemin en büyük alt sınırı."""
    bb = req.base_bounds.model_dump() if req.base_bounds is not None else {}
    if bb.get("lb_tl") is None:
        lb = reference_lb(rp.params, locks)
        if lb is not None:
            bb["lb_tl"] = lb
            bb["lb_from"] = "onbellek"
    return bb


# ---------------------------------------------------------------------- istek → yük
def _items(req: ExplainIn) -> list[dict]:
    items: list[dict] = []
    n = len(req.hospitals)
    if "hastane_kapat" in req.what:
        cells = [h.cell_id for h in req.hospitals] if req.close_all else list(dict.fromkeys(req.close))
        items += [{"kind": "hastane_kapat", "cell_id": c} for c in cells]
    for k in ("n_eksi_1", "n_arti_1"):
        if k in req.what:
            items.append({"kind": k, "p": n + (1 if k == "n_arti_1" else -1)})
    for k in ("boyut", "golge_fiyat", "model"):
        if k in req.what:
            items.append({"kind": k})
    return items


def prepare(req: ExplainIn) -> Prepared:
    rp, locks = resolve_problem_and_locks(req, req.locks)
    km = rp.grid_km
    cells = [h.cell_id for h in req.hospitals]
    errors = check_cells(cells, km, ["body", "hospitals"], ["cell_id"])
    errors += check_cells(req.close, km, ["body", "close"])
    open_set = set(cells)
    if len(open_set) != len(cells):
        errors.append({"loc": ["body", "hospitals"], "key": None, "type": "tekrar",
                       "message_tr": "Aynı hücrede birden fazla hastane olamaz."})
    for i, c in enumerate(req.close):
        if c not in open_set:
            errors.append({"loc": ["body", "close", i], "key": None, "value": c, "type": "acik_degil",
                           "message_tr": f"'{c}' açıklanan çözümün hastanelerinden biri değil."})
    if "hastane_kapat" in req.what and not req.close and not req.close_all:
        errors.append({"loc": ["body", "close"], "key": None, "type": "eksik",
                       "message_tr": "hastane_kapat için en az bir hücre (close) ya da close_all=true gerekli."})
    if rp.params.objective_mode == "butce" and ({"n_eksi_1", "n_arti_1"} & set(req.what)):
        errors.append({"loc": ["body", "what"], "key": "solver.objective_mode", "type": "mod_uyumsuz",
                       "message_tr": "N − 1 / N + 1 karşılaştırması bütçe modunda tanımsız (hastane sayısını bütçe "
                                     "belirler); toplam ya da sabit p modunda çözün."})
    if errors:
        raise ApiValidationError(errors, "Açıklama isteği doğrulanamadı.")
    items = _items(req)
    tl = float(req.time_limit) if req.time_limit is not None else default_time_limit(km)
    gap = float(req.gap) if req.gap is not None else DEFAULT_GAP
    hs = hospitals_payload(req.hospitals)
    bb = bounds_payload(req, rp, locks)
    payload = {"grid_km": km, "overrides": rp.overrides, "locks": locks, "hospitals": hs, "items": items,
               "time_limit": tl, "gap": gap, "exact": bool(req.exact), "reoptimize": bool(req.reoptimize),
               "base_bounds": bb}
    key = stable_key("explain", {"engine": engine_version(), "params": cache_params(rp.params), "locks": locks,
                                 "hospitals": sorted((h["cell_id"], h["class"] or "", h["beds"] or 0) for h in hs),
                                 "items": items, "exact": bool(req.exact), "reopt": bool(req.reoptimize),
                                 "bounds": [bb.get("ub_tl"), bb.get("lb_tl")]})
    variant = {"tl": time_bucket(tl), "gap": gap}
    runtime = 0.0
    for it in items:
        k = it["kind"]
        if k == "hastane_kapat":
            runtime += CLOSE_EXACT_S if req.exact else CLOSE_QUICK_S
            runtime += (tl + 15.0) if req.reoptimize else 0.0
        elif k in ("n_eksi_1", "n_arti_1"):
            runtime += tl + 30.0
        else:
            runtime += MISC_S
    pre = _precomputed_answer(rp.params, locks, hs, items, bool(req.exact), bool(req.reoptimize))
    if pre is not None and _CACHE is not None:
        # tüm öğeler önceden hesaplanmış: sonuç önbelleğe konur, iş kuyruğa girmeden döner
        try:
            v = dict(variant, **summarize(pre))
            if not _CACHE.has(key, v):
                _CACHE.put(key, v, pre, job_type="explain")
        except Exception as ex:
            log.warning("önceden hesaplanmış açıklama önbelleğe konamadı: %s", ex)
    return Prepared(payload=payload, cache_key=key, variant=variant, max_runtime_s=runtime + 30.0,
                    warnings=rp.warnings, info={"grid_km": km, "items": len(items), "what": list(req.what),
                                                "precomputed": pre is not None})


def satisfies(c: dict, r: dict) -> bool:
    return c.get("tl", 0) >= r.get("tl", 0) and c.get("gap", 1) <= r.get("gap", 1) + 1e-12 or bool(c.get("pre"))


def summarize(res: dict) -> dict:
    return {"pre": bool(res.get("precomputed"))}


# ---------------------------------------------------------------------- önceden hesaplanmış açıklamalar
def _precomputed_answer(params, locks: dict, hs: list[dict], items: list[dict], exact: bool, reopt: bool,
                        partial: bool = False) -> dict | None:
    """Aynı problem ve aynı temel çözüm için precomputed/explain_*.json'dan yanıt. partial=False: tüm öğeler belgede
    yoksa None; partial=True: bulunanlar 'items'ta, eksikler 'missing'de."""
    m = match_precomputed("explain", params, locks)
    if m is None:
        return None
    path, doc = m
    if not same_base(doc.get("base", {}).get("hospitals") or [], hs):
        return None
    by_cell = {it["cell_id"]: it for it in doc.get("items", []) if it.get("kind") == "hastane_kapat"}
    other = {it["kind"]: it for it in doc.get("items", []) if it.get("kind") != "hastane_kapat"}
    out, missing = [], []
    for it in items:
        k = it["kind"]
        got = by_cell.get(it.get("cell_id")) if k == "hastane_kapat" else other.get(k)
        if got is not None and k == "hastane_kapat":
            if exact and got.get("feasible") and got.get("method") != "kesin_sinif_secimi":
                got = None
            elif reopt and got.get("feasible") and not got.get("reopt"):
                got = None
        if got is None:
            missing.append(it)
        else:
            out.append(got)
    if missing and not partial:
        return None
    res = {"ok": True, "status": "tamam", "grid_km": doc.get("grid_km"), "mode": doc.get("mode"),
           "params_hash": doc.get("params_hash"), "base": doc.get("base_summary") or {}, "items": out,
           "precomputed": {"name": path.stem, "generated_at": doc.get("generated_at"),
                           "generated_by": doc.get("generated_by"), "settings": doc.get("settings")},
           "p_sweep": doc.get("p_sweep"), "times": {"total_s": 0.0}}
    if sum(1 for x in out if x.get("kind") == "hastane_kapat") > 1:
        res["closure_summary"] = closure_summary([x for x in out if x.get("kind") == "hastane_kapat"],
                                                 doc.get("mode") or "toplam")
    if partial:
        res["missing"] = missing
    return res


def closure_summary(items: list[dict], mode: str = "toplam") -> dict:
    """Birden çok kapatma öğesinin özeti: R'nin zorladıkları, artış dağılımı, gap içinde kalanlar, sıralama. Bütçe
    modunda karşılaştırılan büyüklük erişim maliyetidir."""
    from backend.model.explain_common import tl
    feas = [x for x in items if x.get("feasible")]
    forced = [x for x in items if x.get("feasible") is False and x.get("reason") == "R"]
    forced_k = [x for x in items if x.get("feasible") is False and x.get("reason") == "k_listesi"]
    d = sorted(feas, key=lambda x: x["delta_tl"])
    within = [x for x in feas if x.get("fark_gap_icinde")]
    out = {"n": len(items), "n_feasible": len(feas), "n_r_forced": len(forced),
           "r_forced_cells": [x["cell_id"] for x in forced], "n_k_list_forced": len(forced_k),
           "k_list_forced_cells": [x["cell_id"] for x in forced_k], "n_within_gap": len(within),
           "delta_min_tl": d[0]["delta_tl"] if d else None, "delta_max_tl": d[-1]["delta_tl"] if d else None,
           "delta_median_tl": d[len(d) // 2]["delta_tl"] if d else None,
           "lowest": [{"cell_id": x["cell_id"], "district": x.get("district"), "delta_tl": x["delta_tl"]} for x in d[:5]],
           "highest": [{"cell_id": x["cell_id"], "district": x.get("district"), "delta_tl": x["delta_tl"]}
                       for x in d[-5:][::-1]]}
    txt = f"{len(items)} hastaneden {len(forced)} tanesi R kısıtı yüzünden kapatılamaz (bazı hücrelerin R içinde başka " \
          f"hastanesi kalmaz)."
    if forced_k:
        txt += (f" {len(forced_k)} tanesi de modelin en yakın aday listesi (k kırpması) yüzünden kapatılamaz (R içinde "
                f"başka hastane var ama listenin dışında).")
    if d:
        what = "erişim maliyeti" if mode == "butce" else "toplam maliyet"
        txt += (f" Kalan {len(feas)} hastanenin her biri kapatılınca (konumlar sabit) {what} {tl(d[0]['delta_tl'])}"
                f" – {tl(d[-1]['delta_tl'])} (ortanca {tl(out['delta_median_tl'])}) artar: hiçbir hastanenin tek başına "
                f"kapatılması maliyeti düşürmüyor.")
        if d[0]["delta_tl"] < 0:
            txt = txt.replace("hiçbir hastanenin tek başına kapatılması maliyeti düşürmüyor",
                              "bazı hastanelerin kapatılması maliyeti düşürüyor (temel çözüm optimum değil)")
    if within:
        txt += (f" {len(within)} hastanede artışın üst sınırı bile çözücü gap'inden küçük: bunların gerekliliği bu "
                f"sınırlarla kanıtlanamıyor (fark gap içinde).")
    out["summary_tr"] = txt.replace(" 0 tanesi", " hiçbiri")
    return out


# ---------------------------------------------------------------------- işçide
def _strip(d: dict) -> dict:
    return {k: v for k, v in d.items() if not k.startswith("_")}


def run(ctx: JobContext, payload: dict) -> dict:
    from backend.model import explain as X
    from backend.model.solve import _jsonable

    T0 = time.perf_counter()
    items = payload["items"]
    params, inst = load_problem(payload, [h["cell_id"] for h in payload["hospitals"]])
    pre = _precomputed_answer(params, payload["locks"], payload["hospitals"], items, payload["exact"],
                              payload["reoptimize"], partial=True)
    pre_items: dict = {}
    if pre is not None:
        for it in pre["items"]:
            pre_items[(it["kind"], it.get("cell_id"))] = it
    ctx.emit({"phase": "temel", "phase_tr": "Temel çözüm değerlendiriliyor", "done": 0, "total": len(items)},
             force=True)
    ev, ref = base_from_payload(inst, payload, ctx)
    sol = ev.solution
    base_obj = float(ev.objective_tl)
    bb = dict(payload.get("base_bounds") or {})
    bb["ub_tl"] = min(float(bb["ub_tl"]) if bb.get("ub_tl") is not None else ref["ub_tl"], base_obj)
    pos = {int(j): t for t, j in enumerate(sol.open_j)}
    cpos = X.cell_positions(inst)
    duals = None
    sweep_pts = _sweep_points(params, payload)
    out: list[dict] = []
    status = "tamam"
    for n, item in enumerate(items):
        if ctx.cancelled():
            status = "durduruldu"
            break
        k = item["kind"]
        outer = {"phase": k, "phase_tr": KIND_TR[k], "done": n, "total": len(items), "item": item}
        ctx.emit(outer, force=True)
        got = pre_items.get((k, item.get("cell_id")))
        if got is not None:
            res = dict(got, source="onceden")
        elif k == "hastane_kapat":
            t = pos.get(cpos[item["cell_id"]])
            res = X.closure_effect(inst, sol, t, base_obj=base_obj, base_bounds=bb, exact=payload["exact"],
                                   cancel_event=ctx.cancel_event)
            if payload["reoptimize"] and res.get("feasible") and not ctx.cancelled():
                res["reopt"] = X.reoptimize_closed(inst, sol, t, res, time_limit=payload["time_limit"],
                                                   gap=payload["gap"], base_bounds=bb, cancel_event=ctx.cancel_event,
                                                   progress_cb=InnerProgress(ctx, outer))
            res = _strip(res)
        elif k in ("n_eksi_1", "n_arti_1"):
            res = _n_item(ctx, inst, sol, item, ref, payload, sweep_pts, outer)
        elif k == "boyut":
            duals = duals or _duals(inst, sol)
            res = X.size_explanations(inst, sol, duals)
        elif k == "golge_fiyat":
            duals = duals or _duals(inst, sol)
            res = X.shadow_prices(inst, sol, duals)
        else:
            res = X.model_reasons(inst, sol)
        res = {**res, "kind": k}
        out.append(res)
        ctx.emit({**outer, "done": n + 1, "last": _brief(res)}, force=True)
    ctx.emit({"phase": "bitti", "phase_tr": "Bitti", "done": len(out), "total": len(items)}, force=True)
    base = {"objective_tl": base_obj, "n_hospitals": sol.n_open, "ub_tl": bb.get("ub_tl"), "lb_tl": bb.get("lb_tl"),
            "lb_from": bb.get("lb_from", "istek" if bb.get("lb_tl") is not None else None),
            "gap": ((bb["ub_tl"] - bb["lb_tl"]) / bb["ub_tl"]) if bb.get("lb_tl") is not None else None,
            "opening_tl": ref["opening_tl"], "access_tl": ref["access_tl"],
            "pop_weighted_mean_km": ref["pop_weighted_mean_km"], "count_by_class": ref["count_by_class"],
            "mode": inst.mode}
    res = {"ok": True, "status": status, "grid_km": int(payload["grid_km"]), "mode": inst.mode,
           "params_hash": params.hash(), "base": base, "items": out, "warnings": list(inst.warnings),
           "times": {"total_s": time.perf_counter() - T0}}
    closes = [x for x in out if x.get("kind") == "hastane_kapat"]
    if len(closes) > 1:
        res["closure_summary"] = closure_summary(closes, inst.mode)
    if sweep_pts is not None:
        res["p_sweep"] = sweep_pts.get("_meta")
    return _jsonable(res)


def _brief(res: dict) -> dict:
    keys = ("kind", "cell_id", "p", "feasible", "reason", "delta_tl", "fark_gap_icinde", "status")
    return {k: res.get(k) for k in keys if k in res}


def _duals(inst, sol):
    from backend.model.evaluate import assignment_duals
    return assignment_duals(inst, sol)


def _sweep_points(params, payload) -> dict | None:
    """Varsayılan parametrelerin uzun çözülmüş p taraması (precomputed/p_sweep_{km}km.json): {p: nokta, '_meta'}."""
    from backend.model.params import Params
    km = int(payload["grid_km"])
    if payload["locks"]["open"] or payload["locks"]["closed"]:
        return None
    if params.objective_mode not in ("toplam", "sabit_p"):
        return None
    free = Params.load({k: v for k, v in params.overrides.items()
                        if k not in ("solver.objective_mode", "solver.p_fixed")})
    if free.hash() != Params.load({"grid.cell_km": km}).hash():
        return None
    path = PRECOMPUTED_DIR / f"p_sweep_{km}km.json"
    try:
        doc = orjson.loads(path.read_bytes())
    except (OSError, orjson.JSONDecodeError):
        return None
    pts = {int(p["p"]): p for p in doc.get("points", []) if p.get("ok")}
    acc = doc.get("acceptance") or {}
    pts["_meta"] = {"name": path.stem, "plateau_p": acc.get("plateau_p"), "summary_tr": acc.get("summary_tr"),
                    "free": doc.get("free"), "generated_at": doc.get("generated_at")}
    return pts


def _n_item(ctx, inst, sol, item, ref, payload, sweep_pts, outer) -> dict:
    from backend.model import explain as X
    p = int(item["p"])
    if p < max(1, inst.count_min if inst.mode != "sabit_p" else 1):
        return {"p": p, "ok": False, "status": "tanimsiz", "message_tr": f"{p} hastaneli çözüm tanımsız."}
    pre = (sweep_pts or {}).get(p)
    if pre is not None:
        pt = {k: v for k, v in pre.items() if k not in ("refine",)}
        pt["source"] = "p_taramasi"
        pt["source_tr"] = "önceden hesaplanmış p taraması (uzun çözüm)"
        X.apply_reference(pt, ref)
    else:
        pt, _ = X.best_with_p(inst, sol, p, ref=ref, time_limit=payload["time_limit"], gap=payload["gap"],
                              cancel_event=ctx.cancel_event, progress_cb=InnerProgress(ctx, outer))
    pt["message_tr"] = X.n_message(pt, ref)
    # eski sözleşme alanları (Faz 4 taslağı): objective_tl, delta_tl
    if pt.get("ok"):
        pt.setdefault("objective_tl", pt.get("ub_tl"))
        pt["delta_tl"] = pt.get("fark_tl")
    return pt


def seed(cache) -> int:
    """Önbelleği yakalar (prepare: başvuru alt sınırı ve önceden hesaplanmış yanıtlar). Önceden hesaplanmış açıklamalar
    istek başına anahtarlandığı için açılışta değil, ilk istekte önbelleğe konur."""
    capture_cache(cache)
    return 0


register(JobType(
    name="explain", request_model=ExplainIn, prepare=prepare, run=run,
    description_tr="Açıklama: 'bu hastane kapatılırsa', N − 1 / N + 1 hastaneyle en iyi çözüm, 'neden bu boyut?', "
                   "gölge fiyatlar ve model düzeyindeki nedenler (şartname 7.3–7.6).",
    satisfies=satisfies, summarize=summarize, seed=seed,
    cache_result=lambda res: bool(res.get("ok")) and res.get("status") == "tamam",
))
