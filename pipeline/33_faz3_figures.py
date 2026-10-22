"""Faz 3 — rapor şekilleri (docs/screenshots/, ≈1600 px genişlik, projeksiyonda okunacak yazı boyutları).

Girdiler
  precomputed/default_3km.json         varsayılan çözüm (pipeline/32_precompute.py defaults)
  precomputed/p_sweep_3km.json         p taraması (pipeline/32_precompute.py sweep)
  data/processed/solver_benchmark.json canlı çözüm izi (pipeline/31_benchmark.py; 3 km, 1. çalıştırma)
  ızgaralar, il/ilçe sınırları (pipeline/24_static_maps.py yardımcıları)

Çıktılar
  faz3_varsayilan_cozum_3km.png  hastaneler (alan ∝ yatak, renk = boyut sınıfı), hizmet bölgeleri (her talep hücresi
                                 talebinin en büyük payını alan hastanenin bölgesinde; bölgeler ince çizgiyle ayrılır),
                                 ilçe sınırları; il geneli + merkez büyütmesi
  faz3_u_egrisi_3km.png          p taraması: açılış, erişim ve toplam maliyet; toplamda alt sınır bandı; optimum işaretli
  faz3_dal_sinir_3km.png         üst sınır (incumbent) ve alt sınır zamanla: canlı çözüm (Lagrange + çekirdek MIP) ve
                                 Faz 3 ilk sürümünün yalnız HiGHS dal-sınır çözümü (data/processed/solver_baseline.json)

Çalıştırma (repo kökünden):  .venv/bin/python pipeline/33_faz3_figures.py [harita] [u] [dal]   (varsayılan: hepsi)
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline import common  # noqa: E402

sm = importlib.import_module("pipeline.24_static_maps")
plt = sm.plt
from matplotlib.collections import LineCollection  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

ROOT = common.ROOT
PRECOMP = ROOT / "precomputed"
BENCH = common.PROCESSED / "solver_benchmark.json"
BASELINE = common.PROCESSED / "solver_baseline.json"
INK, INK2, MUTED, HAIRLINE, SURFACE, LAND = sm.INK, sm.INK2, sm.MUTED, sm.HAIRLINE, sm.SURFACE, sm.LAND
HALO = sm.HALO
# Sınıf renkleri: kategorik paletin ilk üç tonu (tüm çiftlerde renk körlüğü ayrımı doğrulanmış) + şehir hastanesi için
# 4. ton (sarı) ikincil kodlamayla (kare işaret) — dördü aynı anda haritada pek görünmez (V-D varsayılanda yok)
CLASS_COLOR = {"IV-C": "#2a78d6", "V-B": "#eb6834", "V-C": "#1baf7a", "V-D": "#eda100"}
SIZE_ZOOM = 0.85          # merkez haritasında nokta² / yatak
CLASS_MARKER = {"IV-C": "o", "V-B": "o", "V-C": "o", "V-D": "s"}
CLASS_LABEL = {"IV-C": "IV-C küçük (50–199)", "V-B": "V-B orta (200–399)", "V-C": "V-C büyük (400–999)",
               "V-D": "V-D şehir hastanesi (1.000–4.000)"}
# hizmet bölgesi dolguları: açık, düşük doygunluklu dört ton (yalnız bölgeleri ayırır; kimlik taşımaz)
AREA_TINTS = ["#e4ecf6", "#f3e9df", "#e3f0ea", "#efedf5"]
AREA_EDGE = "#7d7b76"
COL_TOTAL, COL_OPEN, COL_ACC = "#0b0b0b", "#2a78d6", "#eb6834"


def tr(x: float, d: int = 0) -> str:
    return common.fmt_tr(x, d)


def btl(x: float, d: int = 1) -> str:
    return tr(x / 1e9, d)


# ---------------------------------------------------------------- 1) varsayılan çözüm haritası
def service_areas(g, res: dict) -> tuple[np.ndarray, np.ndarray]:
    """Her ızgara hücresi için baskın hastane indisi (talep hücresi değilse −1) ve hastanelerin ızgara indisleri."""
    id2i = {str(c): i for i, c in enumerate(g.id.tolist())}
    h_gi = np.array([h["grid_index"] for h in res["hospitals"]], dtype=np.int64)
    owner = np.full(g.n, -1, dtype=np.int64)
    best = np.zeros(g.n)
    a = res["assignment"]
    for dc, hidx, sh in zip(a["demand_cell"], a["hospital"], a["share"]):
        i = id2i[dc]
        if sh > best[i]:
            best[i], owner[i] = sh, hidx
    return owner, h_gi


def color_areas(g, owner: np.ndarray, n_h: int) -> np.ndarray:
    """Komşu hizmet bölgeleri farklı tonda olsun diye açgözlü çizge boyama (4 ton yeter; olmazsa en az çakışan)."""
    pos = {(int(x), int(y)): i for i, (x, y) in enumerate(zip(g.ix, g.iy))}
    adj = [set() for _ in range(n_h)]
    for i in np.flatnonzero(owner >= 0):
        for dx, dy in ((1, 0), (0, 1)):
            j = pos.get((int(g.ix[i]) + dx, int(g.iy[i]) + dy))
            if j is not None and owner[j] >= 0 and owner[j] != owner[i]:
                adj[owner[i]].add(int(owner[j]))
                adj[owner[j]].add(int(owner[i]))
    col = np.full(n_h, -1)
    for h in sorted(range(n_h), key=lambda h: -len(adj[h])):
        used = {col[n] for n in adj[h] if col[n] >= 0}
        free = [c for c in range(len(AREA_TINTS)) if c not in used]
        col[h] = free[0] if free else min(range(len(AREA_TINTS)), key=lambda c: sum(col[n] == c for n in adj[h]))
    return col


def area_edges(g, owner: np.ndarray) -> list[np.ndarray]:
    """Farklı hastaneye bağlı komşu talep hücreleri arasındaki kenarlar (UTM)."""
    s = g.cell_km * 1000.0
    pos = {(int(x), int(y)): i for i, (x, y) in enumerate(zip(g.ix, g.iy))}
    segs = []
    for i in np.flatnonzero(owner >= 0):
        x0, y0 = int(g.ix[i]) * s, int(g.iy[i]) * s
        for dx, dy in ((1, 0), (0, 1)):
            j = pos.get((int(g.ix[i]) + dx, int(g.iy[i]) + dy))
            if j is None or owner[j] < 0 or owner[j] == owner[i]:
                continue
            if dx:
                segs.append(np.array([[x0 + s, y0], [x0 + s, y0 + s]]))
            else:
                segs.append(np.array([[x0, y0 + s], [x0 + s, y0 + s]]))
    return segs


def marker_size(beds: np.ndarray, scale: float) -> np.ndarray:
    return scale * beds      # nokta² ∝ yatak (alan ∝ yatak)


def draw_solution(ax, ctx, g, res, owner, h_gi, tint_idx, extent, *, size_scale, label_big: bool) -> None:
    path = sm.geom_path(ctx.province)
    sm.base_axes(ax, extent)
    sm.draw_land(ax, ctx, path)
    vals = np.where(owner >= 0, tint_idx[np.maximum(owner, 0)].astype(float), np.nan)
    from matplotlib.colors import BoundaryNorm, ListedColormap
    cmap = ListedColormap(AREA_TINTS)
    sm.draw_cells(ax, g, vals, cmap, BoundaryNorm(np.arange(-0.5, len(AREA_TINTS) + 0.5), len(AREA_TINTS)), path)
    lc = LineCollection(area_edges(g, owner), colors=AREA_EDGE, linewidths=0.6, zorder=3)
    sm.clip_to(ax, lc, path)
    ax.add_collection(lc)
    sm.draw_boundaries(ax, ctx, district_lw=0.9, province_lw=1.8, district_color=INK2)
    xy = g.xy[h_gi]
    beds = np.array([h["beds"] for h in res["hospitals"]])
    cls = [h["class"] for h in res["hospitals"]]
    order = np.argsort(-beds)                                       # büyükler altta
    for c in CLASS_COLOR:
        sel = [t for t in order if cls[t] == c]
        if sel:
            ax.scatter(xy[sel, 0], xy[sel, 1], s=marker_size(beds[sel], size_scale), c=CLASS_COLOR[c],
                       marker=CLASS_MARKER[c], edgecolors=SURFACE, linewidths=1.6, zorder=8, alpha=0.95)
    if label_big:
        for t in order[:3]:
            ax.annotate(f"{tr(beds[t])} yatak", xy[t], xytext=(10, -14), textcoords="offset points", fontsize=11,
                        color=INK, zorder=9, path_effects=HALO)


def fig_default_solution(ctx) -> Path:
    res = json.loads((PRECOMP / "default_3km.json").read_text(encoding="utf-8"))
    g = ctx.grids[3]
    owner, h_gi = service_areas(g, res)
    tint = color_areas(g, owner, len(h_gi))
    k, c, b = res["kpis"], res["costs"], res["bounds"]
    fig = plt.figure(figsize=(sm.FIG_W, 10.8))
    ax1 = fig.add_axes([0.01, 0.06, 0.56, 0.78])
    ax2 = fig.add_axes([0.585, 0.30, 0.405, 0.54])
    draw_solution(ax1, ctx, g, res, owner, h_gi, tint, sm.province_extent(ctx), size_scale=0.5, label_big=False)
    sm.zoom_frame(ax1)
    sm.draw_district_names(ax1, ctx, skip=sm.CENTRAL, fontsize=10.5, avoid=sm.ZOOM)
    sm.scale_bar(ax1, ctx.province.bounds[0] + 2_000, ctx.province.bounds[1] + 4_000, 40)
    draw_solution(ax2, ctx, g, res, owner, h_gi, tint, sm.ZOOM, size_scale=SIZE_ZOOM, label_big=False)
    sm.frame_axes(ax2)
    sm.draw_district_names(ax2, ctx, fontsize=11, box=sm.ZOOM, min_km2=sm.ZOOM_LABEL_MIN_KM2)
    sm.draw_kizilay(ax2, ctx, ms=15, fontsize=12)
    # açıklama: sınıflar ve boyut
    cnt = k["count_by_class"]
    handles = [Line2D([], [], marker=CLASS_MARKER[cc], ls="", ms=12, mfc=CLASS_COLOR[cc], mec=SURFACE,
                      label=f"{CLASS_LABEL[cc]}: {cnt.get(cc, 0)}") for cc in CLASS_COLOR]
    handles.append(Patch(fc=AREA_TINTS[0], ec=AREA_EDGE, lw=0.6, label="hizmet bölgesi (talep hücreleri)"))
    handles.append(Line2D([], [], color=INK, lw=1.6, ls=(0, (5, 3)), label="kesikli kutu: sağdaki merkez haritası"))
    leg = fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.59, 0.285), frameon=False, fontsize=12,
                     title="Hastane sınıfı (tebliğ, yatak)", title_fontsize=12.5, alignment="left")
    leg.get_title().set_color(INK)
    # boyut ölçeği
    axs = fig.add_axes([0.83, 0.075, 0.16, 0.19])
    axs.set_xlim(0, 1)
    axs.set_ylim(0, 1)
    axs.axis("off")
    axs.text(0.0, 0.97, "Boyut (merkez haritası)", fontsize=12.5, color=INK, va="top")
    for t, bb in enumerate((50, 199, 399)):
        yy = 0.68 - t * 0.27
        axs.scatter([0.15], [yy], s=marker_size(np.array([bb]), SIZE_ZOOM), c="none", edgecolors=INK2, linewidths=1.2)
        axs.text(0.42, yy, f"{tr(bb)} yatak", va="center", fontsize=12, color=INK2)
    within = k["share_pop_within_km"]
    sm.header(fig, f"Varsayılan çözüm, 3 km: {k['n_hospitals']} hastane, toplam {btl(c['toplam'])} milyar TL",
              f"Açılış {btl(c['acilis'])} + erişim {btl(c['erisim'])} milyar TL (25 yıl, bugünkü değer) · "
              f"{tr(k['total_beds'])} yatak (talep {tr(k['total_demand_beds'])}) · nüfus ağırlıklı ortalama yol "
              f"{tr(k['pop_weighted_mean_km'], 1)} km; 10 km içinde %{tr(100 * within['10'], 1)}, en uzak "
              f"{tr(k['max_km'], 1)} km\n"
              f"Alt sınır {btl(b['lb_tl'], 2)}, bulunan {btl(b['ub_tl'], 2)} milyar TL → fark "
              f"%{tr(100 * b['gap'], 2)} · nokta alanı ∝ yatak sayısı\n"
              f"Çoğu hastane küçük, çünkü yatak başı maliyet sınıfla artıyor (IV-C 15,5 → V-B 16,8 → V-C 18,7 → "
              f"V-D 25,1 milyon TL / yatak); hastane başı sabit kısım ≈479–552 milyon TL")
    sm.footer(fig, "Kaynak: TÜİK ADNKS 2025 (WorldPop R2025A ile hücrelere dağıtılmış), 2026 tebliğ birim maliyetleri, "
                   "parameters.json. UTM 36N. Üreten: pipeline/32_precompute.py, pipeline/33_faz3_figures.py")
    return sm.save(fig, "faz3_varsayilan_cozum_3km.png")


# ---------------------------------------------------------------- 2) U eğrisi
def style_axes(ax) -> None:
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    for sp in ("left", "bottom"):
        ax.spines[sp].set_color(HAIRLINE)
    ax.grid(True, axis="y", color=HAIRLINE, lw=0.8)
    ax.set_axisbelow(True)
    ax.tick_params(colors=INK2, labelsize=12, length=0)


def tr_axis(ax, d: int = 0) -> None:
    from matplotlib.ticker import FuncFormatter
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: tr(v, d)))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: tr(v, 0)))


def fig_u_curve() -> Path:
    sw = json.loads((PRECOMP / "p_sweep_3km.json").read_text(encoding="utf-8"))
    pts = [p for p in sw["points"] if p.get("ok")]
    P = np.array([p["p"] for p in pts])
    tot = np.array([p["ub_tl"] for p in pts]) / 1e9
    lb = np.array([p["lb_tl"] for p in pts]) / 1e9
    op = np.array([p["opening_tl"] for p in pts]) / 1e9
    ac = np.array([p["access_tl"] for p in pts]) / 1e9
    acc, free = sw["acceptance"], sw["free"]
    fig = plt.figure(figsize=(sm.FIG_W, 8.8))
    axA = fig.add_axes([0.06, 0.11, 0.40, 0.64])
    axB = fig.add_axes([0.555, 0.11, 0.43, 0.64])
    for ax in (axA, axB):
        style_axes(ax)
        tr_axis(ax, 0 if ax is axA else 1)
        ax.set_xlabel("hastane sayısı p (sabit)", fontsize=13, color=INK2)
    axA.plot(P, tot, color=COL_TOTAL, lw=2.4, marker="o", ms=4.5, label="toplam")
    axA.plot(P, op, color=COL_OPEN, lw=2.0, marker="o", ms=4, label="açılış (inşaat + sabit + arsa)")
    axA.plot(P, ac, color=COL_ACC, lw=2.0, marker="o", ms=4, label="erişim (25 yıl yolculuk)")
    axA.set_ylim(0, max(tot.max(), op.max()) * 1.08)
    axA.set_ylabel("milyar TL (bugünkü değer)", fontsize=13, color=INK2)
    for y, lab, col in ((tot[-1], "toplam", COL_TOTAL), (op[-1], "açılış", COL_OPEN), (ac[-1], "erişim", COL_ACC)):
        axA.annotate(lab, (P[-1], y), xytext=(6, 0), textcoords="offset points", va="center", fontsize=12.5,
                     color=INK, fontweight="bold")
    axA.axvline(acc["best_p"], color=MUTED, lw=1.2, ls=(0, (4, 3)), zorder=0)
    axA.set_title("Bileşenler: açılış ve erişim maliyeti", loc="left", fontsize=14, color=INK, pad=10)
    axA.set_xlim(P.min() - 3, P.max() + 14)
    # B: toplam (yakın plan) + alt sınır bandı
    cert = np.array([p["status"] == "optimal" for p in pts])
    axB.fill_between(P, lb, tot, color="#cde2fb", lw=0, label="alt sınır – bulunan çözüm aralığı", zorder=1)
    axB.plot(P, tot, color=COL_TOTAL, lw=2.0, zorder=3)
    axB.plot(P[cert], tot[cert], ls="", marker="o", ms=6, color=COL_TOTAL, zorder=4,
             label=f"bulunan çözüm (hedef gap %{tr(100 * sw['settings']['target_gap'], 1)} içinde)")
    axB.plot(P[~cert], tot[~cert], ls="", marker="o", ms=6, mfc=SURFACE, mec=COL_TOTAL, mew=1.6, zorder=4,
             label="bulunan çözüm (süre doldu, gap daha büyük)")
    axB.plot(P, lb, color="#2a78d6", lw=1.4, ls=(0, (4, 2)), label="alt sınır (Lagrange)", zorder=2)
    axB.axvline(acc["best_p"], color=MUTED, lw=1.2, ls=(0, (4, 3)), zorder=0)
    axB.scatter([free["n_hospitals"]], [free["ub_tl"] / 1e9], s=180, marker="*", c="#eb6834", edgecolors=SURFACE,
                linewidths=1.4, zorder=5, label=f"serbest p'li optimum ({free['n_hospitals']} hastane)")
    bp = acc["best_p"]
    win = (P >= bp - 25) & (P <= bp + 27)
    axB.set_xlim(bp - 25, bp + 27)
    axB.set_ylim(lb[win].min() - 0.4, min(tot[win].max(), tot.min() * 1.013) + 0.2)
    axB.set_ylabel("toplam maliyet, milyar TL", fontsize=13, color=INK2)
    axB.annotate(f"en iyi p = {acc['best_p']}: {tr(acc['best_ub_tl'] / 1e9, 2)} milyar TL\n"
                 f"= serbest p'li optimum",
                 (acc["best_p"], acc["best_ub_tl"] / 1e9), xytext=(bp + 5, lb[win].min() - 0.1), textcoords="data",
                 fontsize=12.5, color=INK, path_effects=HALO, va="bottom",
                 arrowprops=dict(arrowstyle="-", color=INK2, lw=1))
    axB.set_title("Toplam maliyet: geniş ve düz bir dip", loc="left", fontsize=14, color=INK, pad=10)
    leg = axB.legend(loc="upper center", frameon=False, fontsize=11, ncol=1, bbox_to_anchor=(0.5, 1.0))
    for t in leg.get_texts():
        t.set_color(INK2)
    lg = axA.legend(loc="center left", frameon=False, fontsize=11.5, bbox_to_anchor=(0.02, 0.42))
    for t in lg.get_texts():
        t.set_color(INK2)
    s = sw["settings"]
    sm.header(fig, "U eğrisi, 3 km: hastane sayısı p sabitken en iyi çözüm",
              f"Her p için sabit_p modunda çözüm (süre {tr(s['time_limit_s'])} sn, hedef gap "
              f"%{tr(100 * s['target_gap'], 1)}); taramanın en iyisi p = {acc['best_p']}, serbest p'li optimum "
              f"{free['n_hospitals']} hastane (fark %{tr(100 * acc['rel_diff_best_vs_free'], 3)}; tolerans "
              f"%{tr(100 * acc['tolerance'], 2)}).\nDip düz: p = {min(acc['plateau_p'])}–{max(acc['plateau_p'])} "
              f"aralığı alt sınırlarla dışlanamıyor.\nAz hastanede hem erişim hem açılış pahalı (hastaneler büyük "
              f"sınıfa çıkar, yatak başı maliyet artar); çok hastanede her ek hastane sabit maliyet ve en az 50 yatak "
              f"getirir.")
    sm.footer(fig, "Üreten: pipeline/32_precompute.py sweep, pipeline/33_faz3_figures.py · maliyetler 2026 fiyatları, "
                   "erişim 25 yıl %5 iskontolu")
    return sm.save(fig, "faz3_u_egrisi_3km.png")


# ---------------------------------------------------------------- 3) dal-sınır yakınsaması
def curves(trace: list[dict]):
    """İzden en iyi üst sınır ve en iyi küresel alt sınırın basamak eğrileri (sn, milyar TL) + evre başlangıçları."""
    ev = sorted(trace, key=lambda e: e.get("t_total", 0.0))
    tp, up, td, dn, phases = [], [], [], [], []
    bp, bd = np.inf, -np.inf
    for e in ev:
        t = e.get("t_total", 0.0)
        if e.get("kind") == "phase":
            phases.append((t, e.get("phase")))
        p, d = e.get("primal"), e.get("dual")
        if p is not None and p < bp:
            bp = p
            tp.append(t)
            up.append(bp / 1e9)
        if d is not None and d > bd:
            bd = d
            td.append(t)
            dn.append(bd / 1e9)
    return np.array(tp), np.array(up), np.array(td), np.array(dn), phases


PHASE_TR = {"ornek": "örnek", "lagrange": "Lagrange", "cekirdek_mip": "çekirdek MIP", "lagrange_2": "Lagrange 2",
            "mip": "tam MILP (HiGHS)"}


def draw_conv(ax, trace, t_end, title, *, logx=False, ylim=None, legend_loc="lower right") -> None:
    tp, up, td, dn, phases = curves(trace)
    style_axes(ax)
    tr_axis(ax, 1)
    if len(tp):
        ax.step(np.r_[tp, t_end], np.r_[up, up[-1]], where="post", color=COL_ACC, lw=2.4,
                label="üst sınır (en iyi çözüm)")
    if len(td):
        ax.step(np.r_[td, t_end], np.r_[dn, dn[-1]], where="post", color=COL_OPEN, lw=2.4,
                label="alt sınır (küresel)")
    if len(tp) and len(td):
        ax.fill_between([max(tp[-1], td[-1]), t_end], dn[-1], up[-1], color="#cde2fb", lw=0, zorder=0)
        gap = (up[-1] - dn[-1]) / up[-1]
        ax.annotate(f"son fark %{tr(100 * gap, 2)}", (t_end, dn[-1]), xytext=(-4, -8),
                    textcoords="offset points", ha="right", va="top", fontsize=12.5, color=INK,
                    path_effects=HALO)
    y0, y1 = ylim if ylim else ax.get_ylim()
    ax.set_ylim(y0, y1)
    for t, ph in phases:
        if ph in PHASE_TR and ph != "ornek":
            ax.axvline(max(t, 1e-2), color=HAIRLINE if not logx else MUTED, lw=1.0, ls=(0, (3, 3)), zorder=0)
            ax.text(max(t, 1e-2), y0 + 0.3, " " + PHASE_TR[ph], rotation=90, va="bottom", ha="left",
                    fontsize=10.5, color=INK2, path_effects=HALO)
    if logx:
        ax.set_xscale("log")
        from matplotlib.ticker import FuncFormatter
        ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: tr(v, 0) if v >= 1 else tr(v, 1)))
    ax.set_xlabel("süre (sn)" + (", logaritmik" if logx else ""), fontsize=13, color=INK2)
    ax.set_ylabel("milyar TL", fontsize=13, color=INK2)
    ax.set_title(title, loc="left", fontsize=14, color=INK, pad=10)
    lg = ax.legend(loc=legend_loc, frameon=False, fontsize=11.5, bbox_to_anchor=(1.0, 0.05)
                   if legend_loc == "lower right" else (0.08, 0.05))
    for t in lg.get_texts():
        t.set_color(INK2)


def fig_branch_bound() -> Path:
    bench = json.loads(BENCH.read_text(encoding="utf-8"))
    live = bench["grids"]["3"]["runs"][0]["cold"]
    base = json.loads(BASELINE.read_text(encoding="utf-8"))
    b3 = next(r for r in base["runs"] if r["grid_km"] == 3)["result"]
    pre = json.loads((PRECOMP / "default_3km.json").read_text(encoding="utf-8"))
    best = pre["bounds"]["ub_tl"] / 1e9
    fig = plt.figure(figsize=(sm.FIG_W, 8.8))
    axA = fig.add_axes([0.06, 0.11, 0.42, 0.62])
    axB = fig.add_axes([0.56, 0.11, 0.42, 0.62])
    ylim = (354.0, 388.0)
    draw_conv(axA, live["trace"], live["times"]["total_s"],
              f"Bu fazın canlı çözümü: Lagrange + çekirdek MIP ({tr(live['times']['total_s'], 1)} sn)", ylim=ylim)
    draw_conv(axB, b3["trace"], b3["times"]["total_s"],
              f"Yalnız HiGHS dal-sınır, tam güçlü model ({tr(b3['times']['total_s'])} sn)", logx=True, ylim=ylim,
              legend_loc="lower left")
    for ax in (axA, axB):
        ax.axhline(best, color=INK, lw=1.0, ls=(0, (1, 2)), zorder=0)
    axA.annotate(f"bilinen en iyi çözüm {tr(best, 2)}", (0.02, best), xycoords=("axes fraction", "data"),
                 xytext=(0, 5), textcoords="offset points", fontsize=11, color=INK2)
    t1 = live["time_to_gap_s"]["0.01"]
    sm.header(fig, "Dal-sınır yakınsaması, 3 km: üst sınır (bulunan çözüm) ve alt sınır zamanla",
              "Sol: alt sınır Lagrange gevşetmesinden (atama kısıtları gevşetilir; aday başına sırt çantası); üst sınır "
              "sezgisel, Lagrange sezgiseli,\nçekirdek MIP ve cila. "
              f"%1 farka {tr(t1, 1) if t1 else '—'} sn'de ulaşıldı. "
              "Sağ: Faz 3'ün ilk sürümü — HiGHS tam modeli çözerken alt sınır kök LP bitene dek\n(≈146 sn) yalnız "
              "önsüzgeç sınırıdır (eksende görünmez); bu yüzden canlı çözüm Lagrange sınırıyla sertifikalanıyor.")
    sm.footer(fig, "Üreten: pipeline/31_benchmark.py (canlı, 1. çalıştırma), pipeline/30_solve_default.py --baseline, "
                   "pipeline/33_faz3_figures.py · 4 çekirdekli geliştirme konteyneri; noktalı çizgi: önceden çözülen "
                   "varsayılan çözüm")
    return sm.save(fig, "faz3_dal_sinir_3km.png")


def main() -> None:
    which = set(sys.argv[1:]) or {"harita", "u", "dal"}
    figs = []
    if "harita" in which:
        ctx = sm.load_context()
        figs.append(lambda: fig_default_solution(ctx))
    if "u" in which:
        figs.append(fig_u_curve)
    if "dal" in which:
        figs.append(fig_branch_bound)
    for f in figs:
        p = f()
        print(f"Yazıldı: {p.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
