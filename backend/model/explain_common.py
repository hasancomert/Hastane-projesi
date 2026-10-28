"""Açıklama katmanının hafif ortak parçaları (Faz 6): strateji sabitleri, Türkçe biçimlendirme, referansa göre farklar
ve özet cümleleri. Yalnız standart kütüphane + parametre kaydı (numpy/scipy/highspy yok): sunucu süreci (prepare, meta)
bunları çözücüyü içe aktarmadan kullanır. Hesaplar backend/model/explain.py'dedir.
"""
from __future__ import annotations

import math
from typing import Mapping

# ---------------------------------------------------------------- strateji sabitleri (model parametresi değil)
# Hızlı kapatma tahmini: kapatılan hastaneye kuş uçuşu en yakın CLOSE_NEIGHBOURS açık hastanede sınıf serbest (küçük MIP),
# diğerlerinde sınıf sabit, yataklar serbest. Ölçüm (3 km varsayılan çözüm, 64 olurlu kapatma, 2026-10-23): 12 komşuyla
# toplam 48 sn (ortalama 0,75 sn), tüm hastanelerde sınıf seçimli kesin değerlendirme 450 sn (ortalama 7 sn); iki tahmin
# arasındaki fark ortalama 5,5 milyon TL, en çok 113 milyon TL (komşu sınırlaması hızlı tahmini yalnız yukarı iter:
# kısıtlı problem kesin değerlendirmenin bir kısıtlamasıdır). 16 komşu biraz daha sıkı.
CLOSE_NEIGHBOURS = 16
CLOSE_EXACT_TIME = 30.0          # kesin değerlendirme MIP'inin süre sınırı (sn; evaluate varsayılanı)
NEIGHBOUR_TRY = 8                # N ± 1 başlangıcı: her adımda kesin değerlendirilen en iyi yaklaşık hamle sayısı
LAMBDA_KEY = "access.hospital_visits_per_capita"   # λ doğrusal olarak v'ye bağlı: v·f ⇒ λ·f (params.lam)
PARETO_FACTORS = (0.25, 0.35, 0.5, 0.7, 0.85, 1.0, 1.2, 1.5, 2.0, 2.5, 3.0, 4.0)
TORNADO_DELTA = 0.2
# Tornado parametreleri (şartname 7.9: "her önemli parametre ±%20"). Seçim ölçütü: toplam maliyete doğrudan giren ve
# varsayım/model seçimi durumundaki ya da kaynaklı olup belirsizliği olan büyüklükler. cost.unit.* tebliğin dört sınıfı
# birlikte ölçeklenir (tek grup); V-A modelde kullanılmaz. Etiketler kayıttan okunur.
TORNADO_SPECS = (
    ("cost.fixed_per_hospital", ("cost.fixed_per_hospital",)),
    ("cost.core_area_m2", ("cost.core_area_m2",)),
    ("cost.m2_per_bed.general", ("cost.m2_per_bed.general",)),
    ("cost.unit", ("cost.unit.IV-C", "cost.unit.V-B", "cost.unit.V-C", "cost.unit.V-D")),
    ("cost.equipment_ratio", ("cost.equipment_ratio",)),
    ("cost.infra_ratio", ("cost.infra_ratio",)),
    ("cost.vat_rate", ("cost.vat_rate",)),
    ("demand.beds_per_1000", ("demand.beds_per_1000",)),
    ("access.hospital_visits_per_capita", ("access.hospital_visits_per_capita",)),
    ("access.discount_rate", ("access.discount_rate",)),
    ("access.horizon_years", ("access.horizon_years",)),
    ("access.value_of_time_tl_per_h", ("access.value_of_time_tl_per_h",)),
    ("access.cost_tl_per_km", ("access.cost_tl_per_km",)),
    ("access.speed_kmh", ("access.speed_kmh",)),
    ("access.circuity_scale", ("access.circuity_scale",)),
    ("access.max_distance_km", ("access.max_distance_km",)),
)
GROUP_LABEL_TR = {"cost.unit": "Yapı birim maliyetleri (2026 tebliği; IV-C, V-B, V-C, V-D birlikte)"}
REL_TOL = 1e-12


def fmt(x: float, nd: int = 1) -> str:
    """Türkçe sayı biçimi: 1.234,5 (eksi işareti '−')."""
    s = f"{abs(x):,.{nd}f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return ("−" if x < 0 and float(f"{abs(x):.{nd}f}") != 0 else "") + s


def tl(x: float | None, signed: bool = False) -> str:
    """TL tutarı: |x| ≥ 1 milyar → 'a,bc milyar TL', değilse 'a,b milyon TL'."""
    if x is None or not math.isfinite(x):
        return "—"
    sign = "+" if signed and x > 0 else ""
    if abs(x) >= 1e9:
        return f"{sign}{fmt(x / 1e9, 2)} milyar TL"
    return f"{sign}{fmt(x / 1e6, 1)} milyon TL"


def pct(x: float | None, nd: int = 2, signed: bool = False) -> str:
    """Yüzde: %3,30; eksi '−%0,95'; signed=True ise artı '+%3,30'."""
    if x is None or not math.isfinite(x):
        return "—"
    v = fmt(abs(100 * x), nd)
    if x < 0 and float(f"{abs(100 * x):.{nd}f}") != 0:
        return f"−%{v}"
    return f"+%{v}" if signed and x > 0 else f"%{v}"


def apply_reference(point: dict, ref: Mapping) -> dict:
    """Referans (temel) çözüme göre farklar ve 'fark gap içinde' işareti. ref: {n, ub_tl, lb_tl, objective_tl,
    opening_tl, access_tl, pop_weighted_mean_km, free_lb_valid}. free_lb_valid (temel çözüm toplam modunda) ise temel
    alt sınır bu noktanın da alt sınırıdır: lb = max(kendi, temel)."""
    if not point.get("ok"):
        return point
    own = point.get("lb_own_tl", point.get("lb_tl"))
    point["lb_own_tl"] = own
    lb_ref = ref.get("lb_tl") if ref.get("free_lb_valid") else None
    if lb_ref is not None and (own is None or lb_ref > own):
        point["lb_tl"], point["lb_source"] = float(lb_ref), "serbest_alt_sinir"
    else:
        point["lb_tl"] = own
    ub = float(point["ub_tl"])
    point["gap"] = (ub - point["lb_tl"]) / ub if point["lb_tl"] is not None and ub else None
    ub_ref = float(ref["ub_tl"])
    point["referans"] = int(point.get("p", -1)) == int(ref.get("n", -2)) and bool(ref.get("free_lb_valid"))
    point["fark_tl"] = ub - ub_ref
    point["fark_gap_icinde"] = (None if point["referans"] else
                                bool(point["lb_tl"] is not None and point["lb_tl"] <= ub_ref * (1 + REL_TOL)))
    # gerçek fark aralığı: [lb(p) − ub_ref, ub(p) − lb_ref]
    point["fark_lo_tl"] = (point["lb_tl"] - ub_ref) if point["lb_tl"] is not None else None
    point["fark_hi_tl"] = (ub - float(ref["lb_tl"])) if ref.get("lb_tl") is not None else None
    if ref.get("opening_tl") is not None:
        point["delta_opening_tl"] = point["opening_tl"] - float(ref["opening_tl"])
    if ref.get("access_tl") is not None:
        point["delta_access_tl"] = point["access_tl"] - float(ref["access_tl"])
    if ref.get("pop_weighted_mean_km") is not None and point.get("pop_weighted_mean_km") is not None:
        point["delta_mean_km"] = point["pop_weighted_mean_km"] - float(ref["pop_weighted_mean_km"])
    if ref.get("cells") is not None and point.get("hospitals") is not None:
        rc, pc = set(ref["cells"]), set(point["hospitals"])
        point["closed_vs_ref"] = sorted(rc - pc)
        point["opened_vs_ref"] = sorted(pc - rc)
    return point


def n_message(point: Mapping, ref: Mapping) -> str:
    """'Neden N?' cümlesi (N ± 1 noktası, referansa göre)."""
    p, n = int(point["p"]), int(ref["n"])
    side = "bir eksik" if p < n else "bir fazla"
    if not point.get("ok"):
        return f"{p} hastaneyle ({side}) olurlu çözüm bulunamadı: {point.get('message_tr') or point.get('status')}."
    d = point["fark_tl"]
    msg = (f"{p} hastaneyle ({side}) en iyi bulunan çözüm {tl(point['ub_tl'])}: açılış "
           f"{tl(point.get('delta_opening_tl'), True)}, erişim {tl(point.get('delta_access_tl'), True)} → net "
           f"{tl(d, True)}.")
    if point.get("fark_gap_icinde"):
        msg += (f" Fark çözücü gap'i içinde (alt sınır {tl(point['lb_tl'])} ≤ {n} hastanenin bulunan değeri "
                f"{tl(ref['ub_tl'])}): {p} ile {n} hastane bu sınırlarla ekonomik olarak ayırt edilemiyor.")
    elif point.get("fark_lo_tl") is not None and point["fark_lo_tl"] > 0:
        msg += f" {p} hastane kanıtlı olarak en az {tl(point['fark_lo_tl'])} daha pahalı."
    if d < 0:
        msg += f" {p} hastaneli çözüm daha ucuz: {n} hastaneli çözüm kanıtlı optimum değildi (fark gap içinde)."
    return msg


def p_ranges(ps) -> str:
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


def sweep_summary(res: Mapping, ref: Mapping) -> str:
    n0 = int(ref["n"])
    bp = res.get("best_p")
    if bp is None:
        return "Taramada olurlu nokta yok."
    others = [p for p in res.get("plateau_p", []) if p != n0]
    msg = f"Taramanın en iyisi p = {bp} ({tl(res['best_ub_tl'])}); temel çözüm {n0} hastane ({tl(ref['ub_tl'])})."
    if others:
        msg += (f" Alt sınırı temel çözümün değerini aşmayan ('fark gap içinde', {n0}'dan ayırt edilemeyen) p değerleri: "
                f"{p_ranges(others)} — U eğrisinin dibi bu sınırlarla düz.")
    else:
        msg += f" Taranan diğer her p'nin alt sınırı temel çözümün değerinden büyük: {n0} taranan p'ler arasında tek en iyi."
    if bp != n0:
        msg += f" (p = {bp} temel çözümden {tl(ref['ub_tl'] - res['best_ub_tl'])} ucuz: temel çözüm kanıtlı optimum değildi.)"
    return msg


def pareto_summary(res: Mapping) -> str:
    ok = [q for q in res["points"] if q.get("ok")]
    if len(ok) < 2:
        return "Pareto eğrisi için yeterli olurlu nokta yok."
    lo = min(ok, key=lambda q: q["factor"])
    hi = max(ok, key=lambda q: q["factor"])
    cur = next((q for q in ok if q["factor"] == 1.0), None)
    msg = (f"Erişim ağırlığı λ {fmt(lo['factor'], 2)}×'ten {fmt(hi['factor'], 2)}×'e çıkarılınca açılış maliyeti "
           f"{tl(lo['opening_tl'])} → {tl(hi['opening_tl'])}, nüfus ağırlıklı ortalama yol "
           f"{fmt(lo['pop_weighted_mean_km'], 2)} → {fmt(hi['pop_weighted_mean_km'], 2)} km, hastane sayısı "
           f"{lo['n_hospitals']} → {hi['n_hospitals']}.")
    nmin = min(ok, key=lambda q: (q["n_hospitals"], abs(q["factor"] - 1.0)))
    if nmin["n_hospitals"] < min(lo["n_hospitals"], hi["n_hospitals"]):
        msg += (f" Hastane sayısı tekdüze değil: en az {nmin['n_hospitals']} hastane λ × {fmt(nmin['factor'], 2)}'de; "
                f"erişim ucuzlayınca model hastaneleri azaltmak yerine yatağı daha küçük (yatak başı daha ucuz) sınıflara bölüyor.")
    if cur is not None:
        msg += (f" Mevcut çözüm (λ = 1×): {cur['n_hospitals']} hastane, açılış {tl(cur['opening_tl'])}, ortalama "
                f"{fmt(cur['pop_weighted_mean_km'], 2)} km.")
        if res.get("current_is_min_total"):
            msg += " Asıl λ ile toplam maliyet eğrinin bu noktasında en küçük (gap içinde): optimum bu ödünleşimin dengesi."
        elif res.get("current_total_gap_tl") is not None:
            msg += (f" Not: λ = {fmt(res['best_total_factor'], 2)}× noktasının asıl toplamı "
                    f"{tl(res['current_total_gap_tl'])} daha düşük (canlı çözümlerin gap'i içinde olabilir).")
    nd = sum(1 for q in ok if q.get("dominated"))
    if nd:
        msg += f" {nd} nokta başka bir noktanın baskısında (çözücü gap'i kaynaklı)."
    return msg


def _sint(v) -> str:
    """İşaretli tamsayı: +3, −2, 0; yoksa '—'."""
    if v is None:
        return "—"
    return f"+{v}" if v > 0 else (f"−{abs(v)}" if v < 0 else "0")


def tornado_summary(res: Mapping) -> str:
    rows = [r for r in res["rows"] if r.get("swing_pct") is not None]
    if not rows:
        return "Tornado için tamamlanmış çalışma yok."
    d = res["delta"]
    top = rows[:3]
    parts = []
    for r in top:
        lo, hi = r.get("low") or {}, r.get("high") or {}
        parts.append(f"{r['label_tr']} (−%{fmt(100 * d, 0)}: {pct(lo.get('delta_pct'), 2, True) if lo.get('ok') else '—'}, "
                     f"+%{fmt(100 * d, 0)}: {pct(hi.get('delta_pct'), 2, True) if hi.get('ok') else '—'})")
    msg = f"Toplam maliyete en çok etki eden parametreler (±%{fmt(100 * d, 0)}): " + "; ".join(parts) + "."
    dn = [(r["label_tr"], (r.get("low") or {}).get("delta_n"), (r.get("high") or {}).get("delta_n")) for r in rows]
    big = [x for x in dn if any(v is not None and abs(v) >= 5 for v in x[1:])]
    if big:
        msg += (" Hastane sayısını en çok değiştirenler: " +
                "; ".join(f"{a} ({_sint(b)} / {_sint(c)})" for a, b, c in big[:3]) + ".")
    unc = sum(1 for r in res["rows"] for s in (r.get("low"), r.get("high")) if s and s.get("ok") and not s.get("sign_certain"))
    if unc:
        msg += (f" {unc} çalışmada farkın işareti çözücü sınırlarıyla kesin değil (gerçek fark aralığı sıfırı içeriyor); "
                f"hastane sayısındaki küçük değişimler U eğrisinin düz dibinde gap içinde kalabilir.")
    return msg


def tornado_specs(params, keys=None) -> list[dict]:
    """Tornado satırları: id, anahtarlar, etiket (kayıttan), taban değerler. keys verilirse yalnız onlar."""
    from .params import registry
    reg = registry()
    out = []
    for sid, ks in TORNADO_SPECS:
        if keys is not None and sid not in keys:
            continue
        label = GROUP_LABEL_TR.get(sid) or (reg.get(ks[0]) or {}).get("label") or sid
        out.append({"id": sid, "keys": list(ks), "label_tr": label, "unit": (reg.get(ks[0]) or {}).get("unit"),
                    "status": (reg.get(ks[0]) or {}).get("status"), "base": {k: params[k] for k in ks}})
    return out
