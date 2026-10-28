"""GET /api/meta ve GET /api/sources içerikleri (kayıttan, kaynak YAML'larından ve senaryo dosyasından üretilir).

Hiçbir sayı burada üretilmez: parametre değerleri, birimleri, durumları, kaynakları ve aralıkları
data/reference/parameters.json'dan; kaynak listesi data/sources/*.yaml'dan (data/SOURCES.md'nin üretildiği parçalar);
kaynakça data/reference/literatur.yaml'dan; senaryolar data/reference/senaryolar.yaml'dan okunur.
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path

import orjson
import yaml

from backend.model import params as P

from .config import (
    API_VERSION,
    DATA_DIR,
    GRID_DIR,
    GRID_SIZES,
    PRECOMPUTED_DIR,
    RECOMMENDED_TIME_LIMIT_S as _RTL,
    REFERENCE_DIR,
    SCENARIOS_YAML,
    SOURCES_DIR,
    engine_version,
)
from .schemas import ProblemIn
from .tasks import ApiValidationError
from .validation import allowed_info, resolve_problem

log = logging.getLogger("hastane.meta")

STATUS_TR = {"kaynakli": "kaynaklı", "hesap": "hesap", "varsayim": "varsayım", "model_secimi": "model seçimi"}
KIND_TR = {"resmi": "resmî", "akademik": "akademik", "veri": "veri", "haber": "haber", "ikincil": "ikincil",
           "hesap": "hesap"}
# Şartname 4.4 grupları A–I (kayıttaki 'group' alanı); kayıttaki "Nüfus" grubu şartnamede A altında ("nüfus senaryosu")
GROUPS = (
    ("A", "A. Talep", ("Nüfus", "A. Talep")),
    ("B", "B. Hastane boyutu", ("B. Boyut",)),
    ("C", "C. İnşaat", ("C. İnşaat",)),
    ("D", "D. Arsa", ("D. Arsa",)),
    ("E", "E. Sabit açılış maliyeti", ("E. Sabit",)),
    ("F", "F. İşletme (isteğe bağlı)", ("F. İşletme",)),
    ("G", "G. Erişim", ("G. Erişim",)),
    ("H", "H. Izgara ve çözücü", ("H. Izgara/çözücü",)),
    ("I", "I. Amaç modu", ("I. Amaç modu",)),
)
# Izgara başına önerilen canlı süre sınırı (sn; backend/app/config.py): süre sınırı verilmeyen solve isteğinde kullanılır
RECOMMENDED_TIME_LIMIT_S = {str(k): v for k, v in _RTL.items()}
PRECOMPUTED_NAMES = tuple(f"default_{k}km" for k in GRID_SIZES) + ("p_sweep_3km", "p_sweep_5km",
                                                                    "scenarios")
SCENARIO_FILE_RE = re.compile(r"^scenario_[a-z0-9_]{1,40}_[235]km$")
# Faz 6 açıklama katmanının önceden hesaplanmış dosyaları (pipeline/35_explanations.py): varsayılan parametreler
# explain_3km, pareto_5km …; hazır senaryolar explain_<senaryo>_3km …
PHASE6_PREFIXES = ("explain", "pareto", "tornado")
PHASE6_FILE_RE = re.compile(r"^(explain|pareto|tornado)_([a-z0-9_]{1,40}_)?[235]km$")
SCENARIO_INDEX = PRECOMPUTED_DIR / "scenarios.json"


def _param_entry(key: str, e: dict) -> dict:
    return {
        "key": key, "label": e.get("label"), "value": e.get("value"), "unit": e.get("unit"),
        "status": e.get("status"), "status_tr": STATUS_TR.get(e.get("status"), e.get("status")),
        "sources": [{k: s.get(k) for k in ("id", "title", "publisher", "url", "accessed", "kind")}
                    for s in (e.get("sources") or [])],
        "derivation": e.get("derivation"), "rationale": e.get("rationale"), "notes": e.get("notes"),
        "range": e.get("range"), "range_note": e.get("range_note"), "alternatives": e.get("alternatives") or [],
        "group": e.get("group"), "domain": e.get("domain"), "allowed": allowed_info(key),
        "structure": key in P.STRUCTURE_KEYS,
    }


def _scenario_index() -> dict:
    try:
        return orjson.loads(SCENARIO_INDEX.read_bytes())
    except (OSError, orjson.JSONDecodeError):
        return {}


def _scenario_grids(sid: str, idx: dict, live_rows: list[dict]) -> dict:
    """Izgara → önceden çözülmüş dosya (varsa) ve canlı denetim sonucu (pipeline/34_scenarios.py live)."""
    pre = ((idx.get("scenarios") or {}).get(sid) or {}).get("grids") or {}
    out = {}
    for km in GRID_SIZES:
        p = pre.get(str(km))
        live = next((r for r in live_rows if r.get("scenario") == sid and r.get("grid_km") == km), None)
        out[str(km)] = {
            "precomputed": None if p is None else {k: p.get(k) for k in ("name", "url", "status", "ub_tl", "lb_tl",
                                                                         "gap", "n_hospitals", "time_limit_s")},
            "live_check": None if live is None else {k: live.get(k) for k in ("works_live", "status", "gap",
                                                                              "t_to_1pct_s", "time_limit_s",
                                                                              "wall_s")},
        }
    return out


@lru_cache(maxsize=1)
def scenarios() -> list[dict]:
    """senaryolar.yaml → doğrulanmış senaryo listesi (geçersiz olan atlanır, günlüğe yazılır). Her senaryoda ızgara
    başına önceden çözülmüş sonuç (anında gösterilir; aynı parametrelerle istek önbellekten döner) ve canlı denetim
    (önbelleksiz çözüm önerilen süre sınırında olurlu ve fark ≤ %5 mi; ölçüm ortamı meta 'scenario_live_check')."""
    try:
        raw = yaml.safe_load(SCENARIOS_YAML.read_text(encoding="utf-8")) or []
    except (OSError, yaml.YAMLError) as ex:
        log.warning("senaryolar okunamadı: %s", ex)
        return []
    idx = _scenario_index()
    live_rows = ((idx.get("live_check") or {}).get("rows")) or []
    out = []
    for s in raw:
        try:
            rp = resolve_problem(ProblemIn(overrides=dict(s.get("overrides") or {})))
        except (ApiValidationError, Exception) as ex:
            log.warning("senaryo '%s' geçersiz, atlandı: %s", s.get("id"), getattr(ex, "errors", ex))
            continue
        grids = _scenario_grids(s["id"], idx, live_rows)
        out.append({"id": s["id"], "title": s.get("title"), "status": s.get("status"),
                    "status_tr": STATUS_TR.get(s.get("status"), s.get("status")),
                    "overrides": dict(s.get("overrides") or {}), "description_tr": s.get("description_tr"),
                    "rationale": s.get("rationale"), "derivation": s.get("derivation"),
                    "source_keys": s.get("source_keys") or [], "mode": rp.params.objective_mode,
                    "warnings": rp.warnings, "grids": grids,
                    "precomputed_grids": [int(k) for k, v in grids.items() if v["precomputed"]],
                    "live_grids": [int(k) for k, v in grids.items() if (v["live_check"] or {}).get("works_live")]})
    return out


def scenario_live_check() -> dict | None:
    lc = _scenario_index().get("live_check")
    if not lc:
        return None
    return {k: lc.get(k) for k in ("measured_at", "by", "environment", "rule_tr")}


def access_rings() -> dict:
    """Varsayılan parametrelerle R halkası: R yol km'si ve haritada çizilecek kuş uçuşu yarıçapı (dolambaç tersi)."""
    from backend.model.distance import Circuity
    d = P.Params.load()
    R = float(d["access.max_distance_km"])
    e = Circuity.from_params(d.values).euclid_for_road_km(R)
    return {"R_km": R, "R_crowfly_km": round(e, 3),
            "note_tr": "R yol mesafesidir; harita halkası kuş uçuşu yarıçapla çizilir. Parametre değişince her "
                       "çözüm sonucunda layers.rings güncel değeri verir."}


def _grids() -> list[dict]:
    try:
        summ = orjson.loads((GRID_DIR / "grid_summary.json").read_bytes())
        sizes = summ.get("sizes", {})
    except (OSError, orjson.JSONDecodeError):
        sizes = {}
    out = []
    for km in GRID_SIZES:
        sz = sizes.get(f"{km}km", {})
        out.append({
            "km": km, "cells_url": f"/api/grid/{km}", "bounds_url": f"/api/bounds/{km}",
            "n_cells": sz.get("n_cells"), "n_demand_cells": sz.get("n_demand_cells"),
            "recommended_time_limit_s": RECOMMENDED_TIME_LIMIT_S.get(str(km)),
            "precomputed": {n: f"/api/precomputed/{n}" for n in PRECOMPUTED_NAMES
                            if n.endswith(f"_{km}km") and (PRECOMPUTED_DIR / f"{n}.json").exists()},
            "precomputed_scenarios": {p.stem: f"/api/precomputed/{p.stem}"
                                      for p in sorted(PRECOMPUTED_DIR.glob(f"scenario_*_{km}km.json"))
                                      if SCENARIO_FILE_RE.match(p.stem)},
            "precomputed_explanations": {p.stem: f"/api/precomputed/{p.stem}"
                                         for pre in PHASE6_PREFIXES
                                         for p in sorted(PRECOMPUTED_DIR.glob(f"{pre}_*{km}km.json"))
                                         if PHASE6_FILE_RE.match(p.stem) and p.stem.endswith(f"_{km}km")},
        })
    return out


def build_meta(job_types: dict) -> dict:
    reg = P.registry()
    params = {k: _param_entry(k, e) for k, e in reg.items()}
    groups = []
    for gid, title, reg_groups in GROUPS:
        keys = [k for k, e in reg.items() if e.get("group") in reg_groups]
        groups.append({"id": gid, "title": title, "keys": keys})
    listed = {k for g in groups for k in g["keys"]}
    rest = [k for k in reg if k not in listed]
    if rest:
        groups.append({"id": "Z", "title": "Diğer", "keys": rest})
    d = P.Params.load()
    return {
        "app": {"title": "Ankara için optimum hastane yerleşimi", "api_version": API_VERSION,
                "engine_version": engine_version()},
        "defaults": {"grid_km": int(d["grid.cell_km"]), "mode": d.objective_mode, "scenario_year": d.scenario_year,
                     "time_limit_s": float(d["solver.time_limit_s"]), "gap": float(d["solver.mip_gap"]),
                     "recommended_time_limit_s": RECOMMENDED_TIME_LIMIT_S,
                     "recommended_time_limit_note_tr": "Şartname 4.6 hedef süreleri (3 km ≲ 15 sn, 2 km ≲ 60 sn); "
                                                       "5 km için kayıttaki varsayılan (15 sn). Süre sınırı "
                                                       "verilmeyen solve isteği ızgaranın önerilen süresini kullanır."},
        "access_rings": access_rings(),
        "grids": _grids(),
        "parameters": params,
        "groups": groups,
        "derived": d.derived_summary(),
        "classes": [{"code": c.code, "name": c.name, "L": c.L, "U": c.U, "enabled": c.enabled} for c in d.classes],
        "modes": [{"id": "toplam", "title": "Toplam maliyeti en küçükle"},
                  {"id": "butce", "title": "Bütçe sınırı altında erişim maliyetini en küçükle"},
                  {"id": "sabit_p", "title": "Hastane sayısı p sabitken en iyi konum ve boyut"}],
        "scenario_years": list(P.SCENARIO_YEARS),
        "scenarios": scenarios(),
        "scenario_live_check": scenario_live_check(),
        "status_labels": STATUS_TR,
        "job_types": {n: {"description_tr": jt.description_tr, "url": f"/api/jobs/{n}", "available": jt.available,
                          "unavailable_tr": jt.unavailable_tr or None}
                      for n, jt in sorted(job_types.items())},
        "explain": explain_meta(d),
        "files": {"boundaries": "/api/boundaries", "measures": "/api/measures", "sources": "/api/sources",
                  "basemap": "/basemap/ankara.pmtiles", "glyphs": "/basemap/fonts/{fontstack}/{range}.pbf",
                  "sprites": "/basemap/sprites/v4/"},
    }


def explain_meta(d: P.Params) -> dict:
    """Faz 6 açıklama işlerinin seçenekleri: tornado satırları (etiket kayıttan), Pareto'nun λ çarpanları."""
    from backend.model import explain_common as X
    return {"tornado_params": [{k: r[k] for k in ("id", "keys", "label_tr", "unit", "status", "base")}
                               for r in X.tornado_specs(d)],
            "tornado_delta": X.TORNADO_DELTA, "pareto_factors": list(X.PARETO_FACTORS),
            "pareto_lambda_key": X.LAMBDA_KEY,
            "precomputed_patterns": ["explain_<km>km", "explain_<senaryo>_<km>km", "pareto_<km>km", "tornado_<km>km"]}


# ---------------------------------------------------------------------- kaynaklar
def _domain_titles() -> dict[str, str]:
    """data/SOURCES.md başlıkları: '### 1.x Başlık' + 'Dosya: `data/sources/<alan>.yaml`'."""
    out: dict[str, str] = {}
    try:
        lines = (DATA_DIR / "SOURCES.md").read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    title = None
    for ln in lines:
        m = re.match(r"^### 1\.\d+ (.+)$", ln)
        if m:
            title = m.group(1).strip()
            continue
        m = re.match(r"^Dosya: `data/sources/([A-Za-z0-9_]+)\.yaml`", ln)
        if m and title:
            out[m.group(1)] = title
            title = None
    return out


@lru_cache(maxsize=1)
def build_sources() -> dict:
    titles = _domain_titles()
    reg = P.registry()
    used_by: dict[str, list[str]] = {}
    for k, e in reg.items():
        for sid in e.get("source_ids") or []:
            used_by.setdefault(sid, []).append(k)
        for a in e.get("alternatives") or []:
            for sid in a.get("source_ids") or []:
                if k not in used_by.setdefault(sid, []):
                    used_by[sid].append(k)
    domains = []
    n = 0
    for path in sorted(SOURCES_DIR.glob("*.yaml")):
        try:
            items = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        except (OSError, yaml.YAMLError) as ex:
            log.warning("%s okunamadı: %s", path.name, ex)
            continue
        rows = []
        for s in items:
            rows.append({"id": s.get("id"), "title": s.get("title"), "publisher": s.get("publisher"),
                         "url": s.get("url"), "accessed": str(s.get("accessed")) if s.get("accessed") else None,
                         "published": str(s.get("published")) if s.get("published") else None,
                         "kind": s.get("kind"), "kind_tr": KIND_TR.get(s.get("kind"), s.get("kind")),
                         "used_for": s.get("used_for") or [], "notes": s.get("notes"),
                         "parameters": used_by.get(s.get("id"), [])})
        n += len(rows)
        domains.append({"id": path.stem, "title": titles.get(path.stem, path.stem),
                        "file": f"data/sources/{path.name}", "sources": rows})
    lit_path = REFERENCE_DIR / "literatur.yaml"
    try:
        lit = yaml.safe_load(lit_path.read_text(encoding="utf-8")) or []
    except (OSError, yaml.YAMLError):
        lit = []
    literature = [{k: (str(v) if k == "year" else v) for k, v in item.items()
                   if k in ("id", "authors", "year", "title", "journal", "publisher", "volume", "issue", "pages",
                            "doi", "url", "notes", "verified_via")} for item in lit]
    return {"generated_from": ["data/sources/*.yaml", "data/reference/literatur.yaml", "data/SOURCES.md"],
            "n_sources": n, "domains": domains, "literature": literature}


def build_literature() -> dict:
    """GET /api/literature: data/reference/literatur.yaml (kaynakça, doğrulanmış künyeler) JSON olarak."""
    src = build_sources()
    return {"generated_from": "data/reference/literatur.yaml", "n": len(src["literature"]),
            "literature": src["literature"]}


def precomputed_path(name: str) -> Path | None:
    """Beyaz listedeki ya da senaryo / açıklama ad kalıbına uyan önceden çözülmüş dosya (başka ad → None)."""
    if name not in PRECOMPUTED_NAMES and not SCENARIO_FILE_RE.match(name) and not PHASE6_FILE_RE.match(name):
        return None
    p = PRECOMPUTED_DIR / f"{name}.json"
    return p if p.is_file() else None
