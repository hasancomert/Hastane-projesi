/**
 * "Model" sekmesi (şartname 4.5, 7.11): uygulanan formülasyon KaTeX ile — kümeler, parametreler, değişkenler,
 * amaç, her kısıt (sade Türkçe açıklamasıyla), maliyet katsayıları (Sabit_k, Değişken_jk, λ, PV) — ve iki
 * açıklama bölümü:
 *   "Modelin yapısı sonucu nasıl şekillendiriyor?" (sınıf maliyetleri, eşikte yığılma, R'nin zorladığı kırsal
 *    hastaneler, şehir hastanesi, U eğrisi) ve
 *   "Alt sınır nereden geliyor?" (Lagrange gevşetmesi → aday başına sırt çantası → hacim algoritması; üst sınır
 *    tarafı ve HiGHS; L ≤ optimum ≤ U sertifikası).
 * Formüller backend/model/milp.py, params.py, lagrange.py ve docs/FAZ3_RAPOR.md ile birebir; sayılar gösterilen
 * çözümden (result.instance, result.lagrange, result.bounds …) ve ŞU ANKİ parametre değerlerinden.
 */
import { Badge, Group, Paper, Stack, Table, Text } from '@mantine/core';
import type { ReactNode } from 'react';

import type { LagrangeDiag, ModelDiag, SolveDiagnostics } from '../api/infoTypes.ts';
import type { ObjectiveMode, SolveResultOk } from '../api/types.ts';
import { fmtBillion, fmtDuration, fmtFixed, fmtGap, fmtInt, fmtMoney, fmtNumber, fmtPercent } from '../format/number.ts';
import { paramValue, selectOkResult, selectUiTargetGap, useApp } from '../store/appStore.ts';
import { CLASS_NAME_TR } from '../theme/palette.ts';
import { classCosts, districtsOf, plateauFacts, rangesText, ruralFacts, sizePiles } from './facts.ts';
import { useSweep } from './useInfoData.ts';
import { Tex } from './Tex.tsx';
import { InfoSection, JumpButton, N, Prose } from './ui.tsx';

/** TeX içinde Türkçe sayı: ondalık virgül {,} (KaTeX virgülden sonra boşluk koymasın). */
const tn = (x: number | null | undefined, digits = 2) => (x === null || x === undefined || !Number.isFinite(x) ? '?' : fmtNumber(x, digits).replace(/,/g, '{,}').replace(/−/g, '-'));

const LB_SOURCE_TR: Record<string, string> = {
  lagrange: 'Lagrange gevşetmesi',
  lagrange_butce: 'bütçe satırının Lagrange gevşetmesi',
  mip: 'dal-sınır (HiGHS)',
  zayif_lp: 'zayıf LP gevşetmesi',
};

/** solve.py `solution_source` parçaları ("pencere+cila" gibi birleşik olabilir). */
const SOURCE_TR: Record<string, string> = {
  baslangic: 'verilen başlangıç çözümü',
  onceki_cozum: 'önceki çözümden sıcak başlangıç',
  sezgisel: 'açgözlü sezgisel + yerel arama',
  lagrange: 'Lagrange sezgiseli',
  lagrange_butce: 'bütçe Lagrange sezgiseli',
  lp_yuvarlama: 'çekirdek LP yuvarlaması',
  cekirdek_mip: 'çekirdek MIP (HiGHS)',
  pencere: 'pencere araması (HiGHS)',
  mip: 'tam MILP (HiGHS)',
  cila: 'cila',
};

function sourceText(src: unknown): string {
  if (typeof src !== 'string' || !src) return '—';
  return src
    .split('+')
    .map((p) => SOURCE_TR[p] ?? (p.startsWith('lagrange') ? SOURCE_TR.lagrange : p))
    .join(' + ');
}

function Row({ tex, children, label }: { tex: string; children: ReactNode; label?: string }) {
  return (
    <div className="model-row">
      <Tex tex={tex} block label={label} />
      <Text size="sm" className="info-prose">
        {children}
      </Text>
    </div>
  );
}

function num(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

function BoundLine({ lb, ub }: { lb: number; ub: number }) {
  // L ve U'yu ekseninin %15–%85'ine yerleştir (fark çok küçük olsa da okunsun: ölçekli değil, şematik)
  return (
    <div className="bound-line" aria-label={`Alt sınır ${fmtBillion(lb)}, bulunan çözüm ${fmtBillion(ub)}`} data-testid="bound-line">
      <div className="axis" />
      <div className="band" style={{ left: '15%', width: '70%' }} />
      <div className="tick" style={{ left: '15%' }}>
        <b>L</b> = {fmtBillion(lb)} <span style={{ color: 'var(--mantine-color-dimmed)' }}>· alt sınır</span>
      </div>
      <div className="tick" style={{ left: '85%' }}>
        <b>U</b> = {fmtBillion(ub)} <span style={{ color: 'var(--mantine-color-dimmed)' }}>· bulunan çözüm</span>
      </div>
      <div className="tick below" style={{ left: '50%' }}>
        optimum z* bu aralıkta bir yerde · fark (U − L) / U = <b>{fmtGap((ub - lb) / ub)}</b>{' '}
        <span style={{ color: 'var(--mantine-color-dimmed)' }}>(şematik, ölçeksiz)</span>
      </div>
    </div>
  );
}

export default function ModelTab() {
  const r = useApp(selectOkResult);
  const meta = useApp((s) => s.meta);
  const problem = useApp((s) => s.problem);
  const uiGap = useApp(selectUiTargetGap);
  const st = { meta, problem };
  const pv = (k: string) => num(paramValue(st, k));
  const mode = (paramValue(st, 'solver.objective_mode') as ObjectiveMode | undefined) ?? 'toplam';
  const diag = (r ?? {}) as SolveResultOk & SolveDiagnostics;
  const lag = (diag.lagrange ?? null) as LagrangeDiag | null;
  const model = (diag.model ?? null) as ModelDiag | null;
  const inst = r?.instance;
  const costs = classCosts(r, meta);
  const enabled = costs.rows.filter((c) => c.enabled);
  const piles = sizePiles(r);
  const rural = ruralFacts(r, meta);
  const gridKm = problem.gridKm;
  const sweepSt = useSweep(gridKm === 5 ? 5 : 3);
  const plateau = (gridKm === 3 || gridKm === 5) && sweepSt.data?.grid_km === gridKm ? plateauFacts(sweepSt.data) : null;

  // ŞU ANKİ parametreler (formüllerin yanında)
  const vat = pv('cost.vat_rate');
  const incVat = paramValue(st, 'cost.include_vat') === true;
  const infra = pv('cost.infra_ratio');
  const equip = pv('cost.equipment_ratio');
  const mult = vat !== null && infra !== null && equip !== null ? (1 + (incVat ? vat : 0)) * (1 + infra + equip) : null;
  const A0 = pv('cost.core_area_m2');
  const aGen = pv('cost.m2_per_bed.general');
  const aCity = pv('cost.m2_per_bed.city');
  const F = pv('cost.fixed_per_hospital');
  const T = pv('access.horizon_years');
  const rate = pv('access.discount_rate');
  const v = pv('access.hospital_visits_per_capita');
  const g = pv('access.round_trip_factor');
  const c = pv('access.cost_tl_per_km');
  const vot = pv('access.value_of_time_tl_per_h');
  const speed = pv('access.speed_kmh');
  const alpha = pv('access.alpha');
  const R = pv('access.max_distance_km');
  const k = pv('grid.k_nearest');
  const b = pv('demand.beds_per_1000');
  const safety = pv('demand.safety_margin');
  const landOn = paramValue(st, 'land.include') === true;
  const opexOn = paramValue(st, 'opex.include') === true;
  const PV = inst?.pv_factor ?? meta?.derived.pv_factor ?? null;
  const lam = inst?.lambda_tl_per_person_km ?? meta?.derived.lambda_tl_per_person_km ?? null;
  const nI = inst?.I ?? model?.I ?? null;
  const nJ = inst?.J ?? model?.J ?? null;
  const nA = inst?.arcs ?? model?.arcs ?? null;

  // sınıf eşiğindeki sıçrama: 199 yatak IV-C ↔ 200 yatak V-B (sınıf satırlarından)
  const jumps = enabled.slice(0, -1).flatMap((lo, i) => {
    const hi = enabled[i + 1];
    if (!hi || hi.code === 'V-D' || lo.code === 'V-D' || hi.L !== lo.U + 1) return [];
    const a = lo.fixedTl + lo.U * lo.perBedTl;
    const bb = hi.fixedTl + hi.L * hi.perBedTl;
    return [{ lo, hi, a, b: bb }];
  });
  const vd = costs.rows.find((x) => x.code === 'V-D');
  const nCity = r?.kpis.count_by_class['V-D'] ?? 0;
  const lbBy = (diag.bounds as SolveDiagnostics['bounds'])?.lb_by_source ?? {};

  return (
    <Stack gap="xl" data-testid="model-tab">
      <Stack gap={6}>
        <Prose size="md">
          <b>Problem tipi:</b> çok sınıflı (modüler) kapasiteli tesis yerleşimi + azami mesafe (kapsama) kısıtı, karma tamsayılı doğrusal program (MILP). Her ızgara
          hücresi hem talep noktası hem aday hastane yeri; model hangi hücrede hangi sınıfta kaç yataklı hastane açılacağına ve her hücrenin talebinin hangi
          hastanelere gideceğine aynı anda karar verir; amaç toplam maliyeti (açılış + {T !== null ? `${fmtInt(T)} yıllık` : 'planlama ufku boyunca'} erişim) en küçüklemek.
        </Prose>
        {(model || inst) && (
          <Group gap={6} wrap="wrap">
            {nI !== null && <Badge variant="light" color="gray" tt="none">|I| = {fmtInt(nI)} talep hücresi</Badge>}
            {nJ !== null && <Badge variant="light" color="gray" tt="none">|J| = {fmtInt(nJ)} aday</Badge>}
            {nA !== null && <Badge variant="light" color="gray" tt="none">{fmtInt(nA)} atama yayı</Badge>}
            {model?.binaries != null && <Badge variant="light" color="gray" tt="none">{fmtInt(model.binaries)} ikili değişken</Badge>}
            {model?.rows != null && model.cols != null && (
              <Badge variant="light" color="gray" tt="none">
                {fmtInt(model.rows)} satır × {fmtInt(model.cols)} sütun
              </Badge>
            )}
            {model?.nnz != null && <Badge variant="light" color="gray" tt="none">{fmtInt(model.nnz)} sıfırdan farklı</Badge>}
            <Text size="xs" c="dimmed">
              (gösterilen çözümün modeli)
            </Text>
          </Group>
        )}
      </Stack>

      <InfoSection id="model-kumeler" kicker="Formülasyon 1/5" title="Kümeler">
        <Paper withBorder radius="md" p="sm">
          <Row tex="i \in I">
            <b>Talep hücreleri</b>: nüfusu sıfırdan büyük ızgara hücreleri{nI !== null ? <> (<N>{fmtInt(nI)}</N>)</> : null}.
          </Row>
          <Row tex="j \in J">
            <b>Aday hücreler</b>{nJ !== null ? <> (<N>{fmtInt(nJ)}</N>)</> : null}: il içinde, büyük kısmı su olmayan, nüfusu yeterli ya da yerleşime yakın hücreler.{' '}
            <b>Mevcut hastaneler J’ye girmez</b> — sıfırdan planlama (hocanın 6. maddesi). |J| = ızgaraya sığan en fazla hastane.
          </Row>
          <Row tex="k \in K = \{\text{IV-C},\ \text{V-B},\ \text{V-C},\ \text{V-D}\}">
            <b>Boyut sınıfları</b>: 2026 Yapı Yaklaşık Birim Maliyetleri tebliğinin hastane sınıfları ({costs.rows.map((x) => `${x.code} ${CLASS_NAME_TR[x.code]} ${fmtInt(x.L)}–${fmtInt(x.U)}`).join(' · ')}{' '}
            yatak). Yatak sayısı sınıf içinde süreklidir.
          </Row>
          <Row tex="N_i = \{\, j \in J : d_{ij} \le R \,\}">
            Hücre i’nin gidebileceği adaylar: yol mesafesi R{R !== null ? <> = <N>{fmtNumber(R)} km</N></> : null} içindekilerden{' '}
            {k ? <>en yakın <N>{fmtInt(k)}</N> tanesi</> : 'hepsi'} (seyrek yapı{nA !== null ? <>; toplam <N>{fmtInt(nA)}</N> yay</> : null}).
          </Row>
        </Paper>
      </InfoSection>

      <InfoSection id="model-parametreler" kicker="Formülasyon 2/5" title="Parametreler ve değişkenler">
        <Paper withBorder radius="md" p="sm">
          <Row tex="w_i,\quad q_i = w_i \cdot \tfrac{b}{1000}\cdot(1+s)">
            <b>w<sub>i</sub></b>: hücre nüfusu (TÜİK’e kalibre). <b>q<sub>i</sub></b>: yatak talebi; b = <N>{b !== null ? fmtNumber(b, 2) : '—'}</N> yatak / 1000 kişi, emniyet
            payı s = <N>{safety !== null ? fmtPercent(safety, 0) : '—'}</N>
            {inst?.total_demand_beds != null ? <> → toplam <N>{fmtNumber(inst.total_demand_beds, 1)}</N> yatak</> : null}.
          </Row>
          <Row tex="d_{ij} = d^{\text{kuş}}_{ij}\cdot k\!\left(d^{\text{kuş}}_{ij}\right)\cdot s_{\text{dol}}">
            <b>Yol mesafesi</b> (km): kuş uçuşu mesafe × mesafeye bağlı dolambaç katsayısı (Ankara OSM yol ağından ölçüldü; kısa yolculukta büyük, uzunda küçük).
          </Row>
          <Row tex="[L_k,\ U_k],\quad \text{Sabit}_k,\quad \text{Değişken}_{jk},\quad \lambda,\quad \alpha">
            Sınıf yatak sınırları; hastane başı sabit ve yatak başı değişken açılış maliyeti (aşağıda); λ: erişimin TL / (kişi·km) değeri; α
            {alpha !== null ? <> = <N>{fmtNumber(alpha, 2)}</N></> : null}: mesafe üssü (1 = verimlilik, &gt; 1 uzak mesafeyi daha çok cezalandırır → eşitlik).
          </Row>
          <Row tex="y_{jk}\in\{0,1\},\quad B_{jk}\ge 0,\quad x_{ij}\in[0,1]">
            <b>Karar değişkenleri</b>: j’de k sınıfı hastane açılır mı; yatak sayısı; i’nin talebinin j’ye giden payı (talep bölünebilir). Uygulamada ayrıca z<sub>j</sub> = Σ
            <sub>k</sub> y<sub>jk</sub> yardımcı değişkeni var (aynı model, daha seyrek matris).
          </Row>
        </Paper>
      </InfoSection>

      <InfoSection id="model-amac" kicker="Formülasyon 3/5" title="Amaç: toplam maliyet">
        <Tex
          block
          tex={String.raw`\min\; \underbrace{\sum_{j\in J}\sum_{k\in K}\big(\text{Sabit}_k\, y_{jk} + \text{Değişken}_{jk}\, B_{jk}\big)}_{\text{açılış (yatırım)}} \;+\; \underbrace{\lambda \sum_{i\in I}\sum_{j\in N_i} w_i\, d_{ij}^{\,\alpha}\, x_{ij}}_{\text{erişim (${T !== null ? fmtInt(T) : 'T'} yılın bugünkü değeri)}}`}
          label="Amaç fonksiyonu"
        />
        <Prose>
          İlk terim açılış maliyetidir: sabit kısım her açılan hastane için bir kez, değişken kısım yatak başına. İkinci terim erişim maliyetidir: nüfusun hastaneye
          gidiş-dönüş yolculuklarının planlama ufku boyunca bugünkü değeri. <b>Nüfusla ağırlıklıdır</b> (w<sub>i</sub>): kalabalık hücreyi uzağa bırakmak pahalıdır,
          bu yüzden hastaneler yoğun bölgelerde sıklaşır (hocanın 7. maddesi). Daha çok hastane açılış terimini artırır, erişim terimini azaltır — optimum bu iki
          terimin toplamının en küçük olduğu yerdir (U eğrisi).
          {r && mode !== 'butce' ? (
            <>
              {' '}
              Gösterilen çözümde açılış <N>{fmtBillion(r.costs.acilis)}</N> + erişim <N>{fmtBillion(r.costs.erisim)}</N> = <N>{fmtBillion(r.costs.toplam)}</N>.
            </>
          ) : null}
          {mode === 'butce' ? <> Bütçe modunda amaç yalnız erişim terimidir; açılış maliyeti bütçe kısıtına taşınır.</> : null}
        </Prose>
      </InfoSection>

      <InfoSection id="model-kisitlar" kicker="Formülasyon 4/5" title="Kısıtlar">
        <Paper withBorder radius="md" p="sm">
          <Row tex={String.raw`\sum_{j\in N_i} x_{ij} = 1 \quad \forall i\in I`}>
            <b>Atama:</b> her hücrenin talebinin tamamı R içindeki adaylara atanır (pay bölünebilir). R kapsama kısıtı buradadır: N<sub>i</sub> dışındaki hastaneye atama
            değişkeni yoktur — bu yüzden uzak kırsal hücreler kendi yakınlarında hastane ister.
          </Row>
          <Row tex={String.raw`\sum_{k\in K} y_{jk} \le 1 \quad \forall j\in J`}>
            <b>Her hücrede en fazla bir hastane</b> (tek sınıf). Arazi bağlayıcı değil: bu bir modelleme kararıdır (Veri → Ankara ölçüleri).
          </Row>
          <Row tex={String.raw`L_k\, y_{jk} \;\le\; B_{jk} \;\le\; U_k\, y_{jk} \quad \forall j,k`}>
            <b>Sınıf boyut sınırları:</b> yatak sayısı seçilen sınıfın aralığında; hastane açılmazsa yatak 0.{' '}
            {enabled.length ? <>Şu an: {enabled.map((x) => `${x.code} ${fmtInt(x.L)}–${fmtInt(x.U)}`).join(' · ')}.</> : null}
          </Row>
          <Row tex={String.raw`\sum_{i:\, j\in N_i} q_i\, x_{ij} \;\le\; \sum_{k\in K} B_{jk} \quad \forall j\in J`}>
            <b>Kapasite:</b> hastaneye atanan yatak talebi yatak sayısını aşamaz.
          </Row>
          <Row tex={String.raw`x_{ij} \le \sum_{k\in K} y_{jk} \quad \forall i,\ j\in N_i`}>
            <b>Güçlü bağlantı:</b> kapalı hücreye talep atanamaz. Kapasite kısıtı bunu zaten gerektirir; satır, LP gevşetmesini (dolayısıyla alt sınırı) sıkılaştırmak için
            var.
          </Row>
          <Row tex={String.raw`x_{ij} \le \sum_{k\in K} \min\!\left(1, \tfrac{U_k}{q_i}\right) y_{jk}`}>
            <b>Sınıf başına bağlantı</b> (geçerli eşitsizlik, yalnız q<sub>i</sub> en küçük U<sub>k</sub>’dan büyükse): j’de k sınıfı açıksa i’nin oraya gidebilecek payı U
            <sub>k</sub>/q<sub>i</sub>’yi aşamaz. Tamsayı çözümleri değiştirmez, alt sınırı yükseltir.
          </Row>
          <Row tex={String.raw`\sum_{j}\sum_{k} y_{jk} = p \qquad\big(\text{ya da } p_{\min} \le \textstyle\sum y \le p_{\max}\big)`}>
            <b>Hastane sayısı</b> (isteğe bağlı): “Sabit p” modu ve U eğrisi taraması bunu kullanır{mode === 'sabit_p' ? ' — şu an etkin' : ''}.
          </Row>
          <Row tex={String.raw`y_{jk}=1\ (\text{zorunlu}),\qquad y_{jk}=0\ (\text{yasak})`}>
            <b>Kilitler:</b> haritada zorunlu kılınan / yasaklanan hücreler; kalan yerleşim yeniden optimize edilir.
          </Row>
          <Row tex={String.raw`\sum_{j}\sum_{k}\big(\text{Sabit}_k\, y_{jk} + \text{Değişken}_{jk}\, B_{jk}\big) \le \text{Bütçe}`}>
            <b>Bütçe</b> (yalnız bütçe modunda{mode === 'butce' ? ' — şu an etkin' : ''}): açılış maliyeti bütçeyi aşamaz; amaç erişim maliyetini en küçüklemektir.
          </Row>
        </Paper>
      </InfoSection>

      <InfoSection id="model-maliyet" kicker="Formülasyon 5/5 · hocanın 9. maddesi" title="Maliyet katsayıları">
        <Paper withBorder radius="md" p="sm">
          <Row tex={String.raw`m = (1+\text{kdv})\,(1+\varphi_{\text{alt}}+\varphi_{\text{don}}) = ${tn(mult, 3)}`}>
            İnşaat çarpanı: tebliğ birim maliyeti KDV, altyapı + çevre düzenlemesi ve tıbbi donanımı içermez; KDV {vat !== null ? fmtPercent(vat, 0) : '—'}
            {incVat ? '' : ' (hariç tutuldu)'}, altyapı {infra !== null ? fmtPercent(infra, 0) : '—'}, donanım {equip !== null ? fmtPercent(equip, 0) : '—'}.
          </Row>
          <Row tex={String.raw`\text{Sabit}_k = m\cdot u_k\cdot A_0 + F_{\text{sabit}}${opexOn ? String.raw` + \mathrm{PV}\cdot O_{\text{sabit}}` : String.raw`\;[+\,\mathrm{PV}\cdot O_{\text{sabit}}]`}`}>
            Hastane başı (yataktan bağımsız): çekirdek alan A<sub>0</sub> = <N>{A0 !== null ? `${fmtInt(A0)} m²` : '—'}</N> (acil, görüntüleme, ameliyathane, laboratuvar, teknik
            merkez…) × sınıfın birim maliyeti u<sub>k</sub> × m, artı hastane başı sabit F<sub>sabit</sub> = <N>{F !== null ? fmtMoney(F) : '—'}</N> ({meta?.parameters['cost.fixed_per_hospital']?.status_tr ?? 'varsayım'}). İşletme{' '}
            {opexOn ? 'dahil' : 'varsayılan olarak kapalı'}.
          </Row>
          <Row tex={String.raw`\text{Değişken}_{jk} = m\cdot u_k\cdot a_k ${landOn ? '+ p_j\\cdot \\ell' : '\\;[+\\,p_j\\cdot\\ell]'}${opexOn ? ' + \\mathrm{PV}\\cdot o_{\\text{yatak}}' : ''}`}>
            Yatak başı: yatak başına kapalı alan a<sub>k</sub> (genel <N>{aGen !== null ? fmtNumber(aGen, 2) : '—'} m²</N>, şehir hastanesi{' '}
            <N>{aCity !== null ? fmtNumber(aCity, 1) : '—'} m²</N>) × u<sub>k</sub> × m. Arsa (p<sub>j</sub>: konuma bağlı birim fiyat, ℓ: yatak başı arsa){' '}
            {landOn ? 'dahil.' : 'varsayılan olarak dahil değil (kamu arazisi varsayımı).'}
          </Row>
          <Row tex={String.raw`\mathrm{PV}(T,r) = \frac{1-(1+r)^{-T}}{r} = ${tn(PV, 2)}`}>
            Planlama ufkunun bugünkü değer çarpanı: T = <N>{T !== null ? `${fmtInt(T)} yıl` : '—'}</N>, r = <N>{rate !== null ? fmtPercent(rate, 1) : '—'}</N> (reel).
          </Row>
          <Row tex={String.raw`\lambda = \mathrm{PV}\cdot v\cdot g\cdot\Big(c + \frac{\text{zaman değeri}}{\text{hız}}\Big) = ${tn(lam, 1)}\ \tfrac{\text{TL}}{\text{kişi}\cdot\text{km}}`}>
            Kişi başı yıllık hastane başvurusu v = <N>{v !== null ? fmtNumber(v, 3) : '—'}</N>, gidiş-dönüş g = <N>{g !== null ? fmtNumber(g) : '—'}</N>, araç km maliyeti c ={' '}
            <N>{c !== null ? `${fmtNumber(c, 2)} TL` : '—'}</N>, zaman değeri <N>{vot !== null ? `${fmtNumber(vot, 1)} TL/saat` : '—'}</N>, hız{' '}
            <N>{speed !== null ? `${fmtNumber(speed, 1)} km/saat` : '—'}</N>.
          </Row>
        </Paper>
        {costs.rows.length > 0 && (
          <Table.ScrollContainer minWidth={760}>
            <Table className="info-table" verticalSpacing={5} fz="sm" data-testid="class-cost-table">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Sınıf</Table.Th>
                  <Table.Th className="num">Yatak aralığı</Table.Th>
                  <Table.Th className="num">u_k (TL/m²)</Table.Th>
                  <Table.Th className="num">Sabit_k (hastane başı)</Table.Th>
                  <Table.Th className="num">Değişken_k (yatak başı)</Table.Th>
                  <Table.Th className="num">En büyük boyutta yatak başı ortalama</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {costs.rows.map((x) => {
                  const u = num(paramValue(st, `cost.unit.${x.code}`));
                  return (
                    <Table.Tr key={x.code} style={{ opacity: x.enabled ? 1 : 0.55 }}>
                      <Table.Td>
                        {x.code} {CLASS_NAME_TR[x.code]}
                        {!x.enabled && (
                          <Text size="xs" c="dimmed">
                            {x.disabledReason}
                          </Text>
                        )}
                      </Table.Td>
                      <Table.Td className="num">
                        {fmtInt(x.L)}–{fmtInt(x.U)}
                      </Table.Td>
                      <Table.Td className="num">{u !== null ? fmtInt(u) : '—'}</Table.Td>
                      <Table.Td className="num">{fmtNumber(x.fixedTl / 1e9, 3)} milyar TL</Table.Td>
                      <Table.Td className="num">{fmtFixed(x.perBedTl / 1e6, 2)} milyon TL</Table.Td>
                      <Table.Td className="num">{fmtFixed((x.fixedTl + x.U * x.perBedTl) / x.U / 1e6, 2)} milyon TL</Table.Td>
                    </Table.Tr>
                  );
                })}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        )}
        <Text size="xs" c="dimmed">
          {costs.fromResult ? 'Gösterilen çözümün ayarlarıyla' : 'Varsayılan parametrelerle'}; son sütun = (Sabit<sub>k</sub> + U<sub>k</sub>·Değişken<sub>k</sub>) / U<sub>k</sub>. Arsa ve
          işletme hariç.
        </Text>
      </InfoSection>

      <InfoSection id="model-neden" kicker="Açıklama · neden bu çözüm?" title="Modelin yapısı sonucu nasıl şekillendiriyor?" aside={<JumpButton id="u_egrisi" variant="subtle" />}>
        <Stack gap="sm">
          {enabled.length > 1 && (
            <Prose>
              <b>Çok sayıda küçük hastane.</b> Yatak başı maliyet sınıf büyüdükçe artıyor (
              {enabled.map((x, i) => (
                <span key={x.code}>
                  {i ? ' → ' : ''}
                  {x.code} <N>{fmtFixed(x.perBedTl / 1e6, 2)}</N>
                </span>
              ))}{' '}
              milyon TL), çünkü tebliğde m² maliyeti üst sınıflarda yüksek{aCity !== null && aGen !== null ? ' ve şehir hastanesinin yatak başı alanı daha büyük' : ''}.
              Ölçek ekonomisi yalnız hastane başı sabit kısımdan gelir (
              <N>
                {fmtNumber(Math.min(...enabled.map((x) => x.fixedTl)) / 1e9, 2)}–{fmtNumber(Math.max(...enabled.map((x) => x.fixedTl)) / 1e9, 2)} milyar TL
              </N>
              ) ve bu, en büyük boyutta bile yatak başı ortalamayı sınıfla birlikte artmaktan kurtarmaz (tablodaki son sütun). Hastaneyi büyütmek yerine bölmek ucuz; buna
              bir de erişim kazancı eklenir.
            </Prose>
          )}
          {jumps.length > 0 && (
            <Prose>
              <b>Sınıf eşiğinde yığılma.</b>{' '}
              {jumps.map((j, i) => (
                <span key={j.lo.code}>
                  {i ? ' ' : ''}
                  {fmtInt(j.lo.U)} yataklı {j.lo.code} {fmtBillion(j.a)}, {fmtInt(j.hi.L)} yataklı {j.hi.code} {fmtBillion(j.b)}: tek yatak için{' '}
                  <N>+{fmtNumber((j.b - j.a) / 1e6, 0)} milyon TL</N>.
                </span>
              ))}{' '}
              Model bir üst sınıfa geçmek yerine talebi komşu hastaneye kaydırır
              {piles.upper.length ? (
                <>
                  : gösterilen çözümde{' '}
                  {piles.upper.map((p, i) => (
                    <span key={p.code}>
                      {i ? ', ' : ''}
                      <N>{fmtInt(p.n)}</N> hastane tam {fmtInt(p.beds)} yatakta
                    </span>
                  ))}
                </>
              ) : null}
              .
            </Prose>
          )}
          {rural.atMin.length > 0 && (
            <Prose>
              <b>R’nin zorladığı kırsal hastaneler.</b> Atama kısıtı her hücreye R = <N>{rural.R !== null ? `${fmtNumber(rural.R)} km` : '—'}</N> içinde bir hastane şart
              koşar; seyrek ilçelerde bu, az nüfusa hizmet eden küçük hastaneler demektir: <N>{fmtInt(rural.atMin.length)}</N> hastane sınıfın en küçük boyutunda (
              {fmtInt(rural.minBeds)} yatak) — {districtsOf(rural.atMin).join(', ')}.
              {rural.lowest ? (
                <>
                  {' '}
                  En düşük doluluk <N>{fmtPercent(rural.lowest.occupancy, 0)}</N> ({rural.lowest.district}, {fmtInt(rural.lowest.population)} kişiye hizmet).
                </>
              ) : null}{' '}
              Bu bir eşitlik–verimlilik ödünleşimidir (Sınırlılıklar).
            </Prose>
          )}
          {vd && (
            <Prose>
              <b>Şehir hastanesi {nCity > 0 ? `seçildi (${fmtInt(nCity)})` : 'seçilmiyor'}.</b> V-D’nin yatak başı maliyeti <N>{fmtFixed(vd.perBedTl / 1e6, 2)} milyon TL</N>
              {enabled[0] ? <> (IV-C {fmtFixed(enabled[0].perBedTl / 1e6, 2)})</> : null}; model yalnız yatak, mesafe ve açılış maliyetini görür.{' '}
              <b>Uzmanlaşmanın (üçüncü basamak hizmetin) faydası modellenmedi</b> — büyük hastanenin tıbbi üstünlüğü amaçta yok, bu yüzden model onu seçmek için bir neden
              bulamaz.
            </Prose>
          )}
          <Prose>
            <b>Kaç hastane?</b> Bir hastane daha açmak sabit kısım ve en az L<sub>k</sub> yatak getirir, erişimi kısaltır; optimum bu ikisinin dengesidir (U eğrisi).
            {plateau ? (
              plateau.unique ? (
                <>
                  {' '}
                  {plateau.km} km’de (varsayılan parametrelerle) p = {fmtInt(plateau.freeN)} taranan p’ler arasında kanıtlı tek en iyi.
                </>
              ) : (
                <>
                  {' '}
                  {plateau.km} km’de (varsayılan parametrelerle) dip düz: p = <N>{rangesText(plateau.plateau)}</N> arası “fark gap içinde” — komşu hastane sayıları
                  toplam maliyette ancak çözücünün farkı kadar ayrışır; {fmtInt(plateau.freeN)} bulunan en iyisidir ama tek optimum olduğu kanıtlanamıyor.
                </>
              )
            ) : null}
          </Prose>
          <Group gap={6}>
            <JumpButton id="sinir_duz_dip" variant="subtle" />
            <JumpButton id="kac_nerede" variant="subtle" label="Haritada çözüm" />
          </Group>
        </Stack>
      </InfoSection>

      <InfoSection id="model-alt-sinir" kicker="Açıklama · neden optimum?" title="Alt sınır nereden geliyor?" aside={<JumpButton id="sertifika" variant="subtle" />}>
        <Prose>
          Optimum maliyeti z* bilinmez; ama iki sayı bilinir: bulunan en iyi çözümün maliyeti <b>U</b> (gerçek bir yerleşim, bağımsız denetimden geçmiş — üst sınır) ve
          hiçbir yerleşimin altına inemeyeceği <b>L</b> (alt sınır). <b>L ≤ z* ≤ U</b> olduğundan fark (U − L) / U, bulunan çözümün optimumdan en çok ne kadar kötü
          olabileceğidir. Alt sınır olmadan “optimum” denemez; canlı çözümde L şu dört adımla hesaplanır.
        </Prose>
        {r?.bounds.lb_tl != null && r.bounds.ub_tl != null && <BoundLine lb={r.bounds.lb_tl} ub={r.bounds.ub_tl} />}

        <Paper withBorder radius="md" p="sm">
          <Stack gap="xs">
            <Text fw={700}>1 · Zor kısıtı gevşet (Lagrange gevşetmesi)</Text>
            <Prose>
              Hücreleri birbirine bağlayan tek kısıt atamadır (Σ x = 1). Onu kaldırıp ihlalini bir “fiyat” μ<sub>i</sub> ile amaca ekleriz:
            </Prose>
            <Tex
              block
              tex={String.raw`\mathcal{L}(\mu) = \min_{(y,B,x)\in X}\; \sum_{j,k}\big(\text{Sabit}_k y_{jk} + \text{Değişken}_{jk} B_{jk}\big) + \sum_{i}\sum_{j\in N_i} c_{ij}x_{ij} + \sum_{i}\mu_i\Big(1-\sum_{j\in N_i}x_{ij}\Big)`}
            />
            <Prose>
              X: kalan kısıtlar (hücre başına bir hastane, sınıf sınırları, kapasite, bağlantılar, kilitler); c<sub>ij</sub> = λ w<sub>i</sub> d<sub>ij</sub>
              <sup>α</sup>. Her olurlu yerleşimde parantez sıfırdır ve gevşetilmiş problem daha geniş bir kümede en küçükler; bu yüzden <b>her μ için 𝓛(μ) ≤ z*</b> (zayıf
              dualite). Çarpanlar kötü seçilse bile sınır geçerlidir — yalnız gevşek olur.
            </Prose>
          </Stack>
        </Paper>

        <Paper withBorder radius="md" p="sm">
          <Stack gap="xs">
            <Text fw={700}>2 · Aday başına sırt çantasına ayrış</Text>
            <Prose>Atama kısıtı kalkınca adaylar birbirinden bağımsızlaşır; problem her aday için ayrı, küçük bir probleme ayrışır:</Prose>
            <Tex block tex={String.raw`\mathcal{L}(\mu) = \sum_{i}\mu_i + \sum_{j\in J}\min\big(0,\ G_j(\mu)\big),\qquad G_j(\mu)=\min_{k} G_{jk}(\mu)`} />
            <Tex block tex={String.raw`G_{jk}(\mu) = \text{Sabit}_k + \text{Değişken}_{jk}\cdot\max(L_k,\, b) - P_j(b),\qquad \rho_{ij} = \frac{\mu_i - c_{ij}}{q_i}`} />
            <Prose>
              μ<sub>i</sub>, hücre i’ye hizmet etmenin bu turdaki fiyatıdır. Aday j, fiyatı erişim maliyetinden yüksek (μ<sub>i</sub> &gt; c<sub>ij</sub>) hücreleri yatak
              başına kârlarına (ρ) göre sıralar ve kapasite U<sub>k</sub>’ya kadar doldurur — <b>kesirli sırt çantası</b>: ilk L<sub>k</sub> yatak zaten ödenir, ek yatak
              ancak kârı Değişken<sub>jk</sub>’dan büyükse alınır (b: alınan yatak, P<sub>j</sub>(b): toplanan kâr). G<sub>j</sub> &lt; 0 olan adaylar “açılır”. Alt problemde
              y tamsayı kaldığı için bu sınır güçlü LP gevşetmesinden en az onun kadar sıkıdır (Geoffrion &amp; McBride 1978; Cornuéjols, Sridharan &amp; Thizy 1991).
              {lag?.sub_ms != null ? (
                <>
                  {' '}
                  Bir tur vektörel hesaplanır: bu çözümde ortalama <N>{fmtNumber(lag.sub_ms, 1)} ms</N>.
                </>
              ) : null}
            </Prose>
          </Stack>
        </Paper>

        <Paper withBorder radius="md" p="sm">
          <Stack gap="xs">
            <Text fw={700}>3 · Fiyatları yükselt (alt gradyan, hacim algoritması)</Text>
            <Prose>
              En iyi alt sınır max<sub>μ</sub> 𝓛(μ)’dür. 𝓛 içbükey ama türevlenemez; yönü alt gradyan verir: g<sub>i</sub> = 1 − Σ<sub>j</sub> x<sub>ij</sub>(μ). Hiçbir adayın
              almadığı hücrenin fiyatı artar (g<sub>i</sub> = 1), birden çok adayın kapıştığı hücrenin fiyatı düşer.
            </Prose>
            <Tex
              block
              tex={String.raw`\mu^{t+1} = \mu^{t} + \theta_t\,\frac{U-\mathcal{L}(\mu^{t})}{\lVert \bar v^{t}\rVert^{2}}\;\bar v^{t},\qquad \bar v^{t} = a\,g^{t} + (1-a)\,\bar v^{t-1}`}
            />
            <Prose>
              Hacim algoritması (Barahona &amp; Anbil 2000): alt gradyanların üstel ortalaması yönünde Polyak adımı; iyileşme oldukça θ büyür, olmadıkça küçülür. Başlangıç
              çarpanları zayıf LP gevşetmesinin dual değerleri ya da aynı yapıdaki önceki çözümün çarpanları.
              {lag?.iters != null ? (
                <>
                  {' '}
                  Bu çözümde: <N>{fmtInt(lag.iters)}</N> yineleme, <N>{fmtDuration(lag.time_s)}</N>
                  {lag.init_source ? <>, başlangıç {lag.init_source === 'zayif_lp' ? 'zayıf LP dualleri' : lag.init_source}</> : null}
                  {lag.bound_tl != null ? (
                    <>
                      , Lagrange sınırı <N>{fmtBillion(lag.bound_tl)}</N>
                    </>
                  ) : null}
                  .
                </>
              ) : null}
            </Prose>
          </Stack>
        </Paper>

        <Paper withBorder radius="md" p="sm">
          <Stack gap="xs">
            <Text fw={700}>4 · Üst sınırı iyileştir; HiGHS</Text>
            <Prose>
              Alt sınır tek başına yerleşim vermez. U tarafı: açgözlü sezgisel + yerel arama → Lagrange sezgiseli (açılan adaylardan olurlu çözüm) → çekirdek LP yuvarlaması →{' '}
              <b>pencere araması</b> (bir merkezin çevresindeki adaylar serbest, gerisi sabit; küçük MILP’i HiGHS kesin çözer) → <b>çekirdek MIP</b> (en umut verici adaylar
              ve yakın yaylarla küçültülmüş MILP, HiGHS, ayrı süreçte) → sınıf seçimli cila. Her aday çözüm bağımsız denetçiden geçer (tam atama, kapasite, sınıf, R, hücre
              başına bir hastane, maliyetin sıfırdan hesabı). Önceden çözülen senaryolarda ayrıca <b>tam MILP</b> HiGHS ile dal-sınırla çözülür; HiGHS’in alt sınırı (kök LP
              + kesmeler) Lagrange sınırından yüksekse o kullanılır.
              {r ? (
                <>
                  {' '}
                  Bu çözüm: son çözümün kaynağı <N>{sourceText(diag.solution_source)}</N>
                  {lbBy.lagrange != null ? (
                    <>
                      ; alt sınırlar — Lagrange <N>{fmtBillion(lbBy.lagrange)}</N>
                    </>
                  ) : null}
                  {lbBy.mip != null ? (
                    <>
                      , HiGHS <N>{fmtBillion(lbBy.mip)}</N>
                    </>
                  ) : null}
                  ; kullanılan: {r.bounds.lb_source ? LB_SOURCE_TR[r.bounds.lb_source] ?? r.bounds.lb_source : '—'}.
                </>
              ) : null}
            </Prose>
          </Stack>
        </Paper>

        <Paper withBorder radius="md" p="sm" data-testid="certificate-logic">
          <Stack gap="xs">
            <Text fw={700}>Sertifika cümlesi nasıl okunur?</Text>
            {r?.certificate_tr && (
              <Text size="sm" fs="italic" className="info-prose">
                “{r.certificate_tr}”
              </Text>
            )}
            <Prose>
              L bir alt sınır, U gerçek bir çözüm olduğundan optimum z* ikisinin arasındadır: daha iyi bir yerleşim varsa bile U’dan en çok (U − L) / U kadar ucuzdur. Fark
              arayüzün hedefinin ({fmtGap(uiGap)}) içindeyse sonuç “optimum (kanıtlı fark …)” diye etiketlenir; değilse “en iyi bulunan çözüm”. “k en yakın aday
              listesiyle kurulan model için” notu, sertifikanın N<sub>i</sub> listeleri k ile kırpılmış modele ait olduğunu söyler (R içindeki tüm adaylarla kurulan modelin
              optimumu bundan biraz daha ucuz olabilir). Hedef farktan küçük maliyet farkları (ör. U eğrisinin dibinde komşu hastane sayıları) anlamlı sayılmamalıdır.
            </Prose>
          </Stack>
        </Paper>
      </InfoSection>
    </Stack>
  );
}
