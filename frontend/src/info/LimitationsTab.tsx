/**
 * "Sınırlılıklar" sekmesi (şartname 7.13): modelin bilerek basitleştirdiği yerler, sonuca etkileri ve sayfada
 * nerede görüldükleri. Sayılar gösterilen çözümden, /api/meta'dan (parametre değerleri, gerekçe ve not metinleri,
 * hazır senaryoların önceden çözülmüş sonuçları) ve önceden çözülmüş p taramalarından gelir.
 */
import { Badge, Group, Paper, Stack, Table, Text, Title } from '@mantine/core';
import type { ReactNode } from 'react';

import type { SolveDiagnostics } from '../api/infoTypes.ts';
import { fmtBillion, fmtGap, fmtInt, fmtMoney, fmtNumber, fmtPercent } from '../format/number.ts';
import { paramValue, selectOkResult, useApp } from '../store/appStore.ts';
import { classCosts, districtsOf, gridComparison, plateauFacts, quoteWith, rangesText, ruralFacts, sentenceWith } from './facts.ts';
import type { JumpId } from './jumps.ts';
import { JumpButton, N, Prose } from './ui.tsx';
import { useParam, useSweep } from './useInfoData.ts';

function Card({ id, title, tag, children, jumps = [] }: { id: string; title: string; tag?: string; children: ReactNode; jumps?: JumpId[] }) {
  return (
    <Paper withBorder radius="md" p="md" id={id} data-testid={id}>
      <Stack gap={8} h="100%">
        <Group justify="space-between" gap="xs" wrap="nowrap" align="flex-start">
          <Title order={4} style={{ fontSize: '1.02rem', lineHeight: 1.3 }}>
            {title}
          </Title>
          {tag && (
            <Badge size="sm" variant="light" color="gray" tt="none" style={{ flexShrink: 0 }}>
              {tag}
            </Badge>
          )}
        </Group>
        <Prose>{children}</Prose>
        {jumps.length > 0 && (
          <Group gap={6} mt="auto">
            {jumps.map((j) => (
              <JumpButton key={j} id={j} variant="subtle" />
            ))}
          </Group>
        )}
      </Stack>
    </Paper>
  );
}

function firstSentence(t: string | null | undefined): string | null {
  if (!t) return null;
  const i = t.search(/\.(\s|$)/);
  return i > 0 ? t.slice(0, i + 1) : t;
}

export default function LimitationsTab() {
  const meta = useApp((s) => s.meta);
  const problem = useApp((s) => s.problem);
  const r = useApp(selectOkResult);
  const st = { meta, problem };
  const s3 = useSweep(3);
  const s5 = useSweep(5);
  const prof = paramValue(st, 'access.circuity_profile');
  const mode = paramValue(st, 'access.circuity_mode');
  const R = paramValue(st, 'access.max_distance_km');
  const k = paramValue(st, 'grid.k_nearest');
  const F = useParam('cost.fixed_per_hospital');
  const A0 = useParam('cost.core_area_m2');
  const src = useParam('population.grid_source');
  const p25 = useParam('population.province_2025');
  const p30 = useParam('population.province_2030');
  const crowR = r?.layers?.rings?.R_crowfly_km ?? meta?.access_rings?.R_crowfly_km ?? null;
  const costs = classCosts(r, meta).rows;
  const vd = costs.find((c) => c.code === 'V-D');
  const ivc = costs.find((c) => c.code === 'IV-C');
  const rural = ruralFacts(r, meta);
  const grids = gridComparison(meta);
  const pl3 = plateauFacts(s3.data);
  const pl5 = plateauFacts(s5.data);
  const coverage = quoteWith(p25.p?.notes, 'geçici koruma') ?? quoteWith(p30.p?.notes, 'geçici koruma');
  const target = (typeof problem.overrides['solver.mip_gap'] === 'number' ? (problem.overrides['solver.mip_gap'] as number) : null) ?? meta?.defaults.gap ?? null;
  const live3 = meta?.defaults.recommended_time_limit_s?.['3'] ?? null;
  const diag = (r ?? {}) as SolveDiagnostics;
  const nCity = r?.kpis.count_by_class['V-D'] ?? null;

  return (
    <Stack gap="md" id="info-sinirliliklar" data-testid="limitations-tab">
      <Text size="sm" c="dimmed" className="info-prose">
        Model bir karar desteğidir, Ankara’nın sağlık sisteminin tam bir kopyası değil. Aşağıdakiler bilerek yapılan basitleştirmelerdir; her birinin sonucu hangi yönde
        etkilediği ve sayfada nerede görüldüğü yazılıdır.
      </Text>
      <div className="sinir-grid">
        <Card id="sinir-mesafe" title="Kuş uçuşu × dolambaç, yol ağı değil" tag="mesafe" jumps={['erisim_harita']}>
          d<sub>ij</sub> = kuş uçuşu mesafe × mesafeye bağlı dolambaç katsayısı
          {mode === 'mesafeye_bagli' && Array.isArray(prof) && prof.length ? (
            <>
              {' '}
              (Ankara OSM yol ağından ölçüldü: {(prof as number[][]).map((p) => `${fmtNumber(p[0], 1)} km → ${fmtNumber(p[1], 3)}`).join(' · ')})
            </>
          ) : null}
          . Gerçek yol ağı, köprü/otoyol kısa yolları, dağlar, trafik ve toplu taşıma yok; yolculuk süresi ortalama hızla. R = <N>{typeof R === 'number' ? fmtNumber(R) : '—'} km</N>{' '}
          yol mesafesidir — haritadaki halka kuş uçuşu ≈<N>{crowR !== null ? fmtNumber(crowR, 1) : '—'} km</N>. Yol ağı mesafesiyle karşılaştırma (OSRM) yapılmadı.
        </Card>

        <Card id="sinir-basamak" title="Uzmanlık / basamak ayrımı yok" tag="talep" jumps={['model_neden']}>
          Her hastane aynı “genel hastane” hizmetini verir; sınıflar yalnız boyut ve maliyet sınıfıdır. <b>Uzmanlaşmanın (üçüncü basamak hizmetin) faydası modellenmedi</b>:
          büyük hastanenin tıbbi üstünlüğü amaçta yok. Bu yüzden şehir hastanesi (V-D)
          {vd && ivc ? (
            <>
              , yatak başı <N>{fmtNumber(vd.perBedTl / 1e6, 2)}</N> milyon TL (IV-C {fmtNumber(ivc.perBedTl / 1e6, 2)}) ile,
            </>
          ) : null}{' '}
          {nCity === 0 ? 'seçilmiyor' : nCity ? `${fmtInt(nCity)} kez seçildi` : 'seçilmeyebilir'}. İki basamaklı (genel + üçüncü basamak) model bonus olarak kaldı.
        </Card>

        <Card id="sinir-komsu" title="Komşu illerle hasta akışı yok" tag="sınır">
          Talep ve adaylar yalnız il sınırı içinde: il sınırına yakın hücrelerin talebi yalnız Ankara’daki adaylara atanır; komşu illerin hastaneleri ve başka illerden
          Ankara’ya gelen hastalar hesapta yok. Etkisi: sınır ilçelerinde komşu ilin daha yakın hastanesini kullananlar Ankara talebi sayılır; il dışından gelen hastalar ise
          talepte görünmez.
        </Card>

        <Card id="sinir-izgara" title="2025 nüfus ızgarası bir model çıktısı" tag="veri" jumps={['nufus_karti']}>
          Hücre nüfusu doğrudan sayım değil, bir modelin dağıtımıdır. {sentenceWith(src.p?.rationale, 'kalibrasyon öncesi') ?? ''}{' '}
          {sentenceWith(src.p?.rationale, 'projeksiyondur') ?? ''} {sentenceWith(src.p?.rationale, 'alfa') ?? ''} İl ve ilçe toplamları TÜİK ile birebir (her
          ilçe kendi toplamına ölçeklenir); ilçe İÇİ dağılım modeldir.
        </Card>

        <Card id="sinir-tuik" title="TÜİK nüfusu herkesi kapsamaz" tag="veri">
          {coverage ? <>“{coverage}”</> : 'ADNKS kapsamı: ülkede ikamet eden vatandaşlar ve kayıtlı yabancılar.'} Bu kişilerin hastane kullanımı talepte yok; gerçek talep
          {typeof p25.value === 'number' ? <> <N>{fmtInt(p25.value)}</N> kişinin</> : null} ima ettiğinden yüksek olabilir.
        </Card>

        <Card id="sinir-sabit" title="Sabit maliyet bir varsayım" tag="maliyet" jumps={['tornado', 'model_maliyet']}>
          Hastane başı F<sub>sabit</sub> = <N>{typeof F.value === 'number' ? fmtMoney(F.value) : '—'}</N> ({F.p?.status_tr ?? '—'}
          {F.p?.range ? (
            <>
              ; kaydırıcı {fmtMoney(F.p.range[0])} – {fmtMoney(F.p.range[1])}
            </>
          ) : null}
          ). {firstSentence(F.p?.rationale)} Çekirdek alan A<sub>0</sub> = <N>{typeof A0.value === 'number' ? `${fmtInt(A0.value)} m²` : '—'}</N> ({A0.p?.status_tr}) de
          istatistiksel olarak sıfırdan ayırt edilemiyor. Sabit kısım büyüdükçe daha az ve daha büyük hastane açılır; tornado grafiği etkisini gösterir.
        </Card>

        <Card id="sinir-kirsal" title="Kırsal hastaneler düşük dolulukta (eşitlik–verimlilik)" tag="sonuç" jumps={['erisim_harita']}>
          {rural.atMin.length ? (
            <>
              R = <N>{rural.R !== null ? `${fmtNumber(rural.R)} km` : '—'}</N> kapsama kısıtı seyrek ilçelerde küçük hastane açtırır: <N>{fmtInt(rural.atMin.length)}</N>{' '}
              hastane en küçük boyutta ({fmtInt(rural.minBeds)} yatak; {districtsOf(rural.atMin).join(', ')}).
              {rural.lowest ? (
                <>
                  {' '}
                  En düşük doluluk <N>{fmtPercent(rural.lowest.occupancy, 0)}</N> ({rural.lowest.district}).
                </>
              ) : null}{' '}
            </>
          ) : (
            'Gösterilen çözümde sınıfın en küçük boyutunda açılan hastane yok. '
          )}
          Verimlilik bunları kapatırdı; eşitlik (herkese R içinde hastane) açık tutar. R’yi büyütmek bu hastaneleri azaltabilir; α &gt; 1 uzak mesafeyi daha çok cezalandırarak eşitliği artırır.
        </Card>

        <Card id="sinir-hucre" title="Hücre boyutu sonucu etkiler" tag="ızgara">
          Her hücre hem talep noktası hem tek bir aday yeri; hücre küçüldükçe hücre içi yolculuk kısalır ve hastaneler talebe yaklaşır. Varsayılan parametrelerle
          önceden çözülen sonuçlar:
          {grids.length ? (
            <Table className="info-table" verticalSpacing={3} fz="sm" mt={6} data-testid="grid-compare">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Izgara</Table.Th>
                  <Table.Th className="num">Hastane</Table.Th>
                  <Table.Th className="num">Toplam maliyet</Table.Th>
                  <Table.Th className="num">Fark</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {grids.map((g) => (
                  <Table.Tr key={g.km}>
                    <Table.Td>{g.km} km</Table.Td>
                    <Table.Td className="num">
                      <N>{fmtInt(g.n)}</N>
                    </Table.Td>
                    <Table.Td className="num">{fmtBillion(g.ubTl)}</Table.Td>
                    <Table.Td className="num">{fmtGap(g.gap)}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          ) : null}
          Hastane sayısı kısmen ayrıklaştırmanın sonucudur: “kaç hastane?” sorusunun yanıtı ızgara ile birlikte okunmalı.
        </Card>

        <Card id="sinir-duz-dip" title="U eğrisinin dibi düz" tag="optimallik" jumps={['u_egrisi']}>
          {pl3 ? (
            <>
              {pl3.km} km’de serbest optimum <N>{fmtInt(pl3.freeN)}</N> hastane (L = {fmtBillion(pl3.freeLb)}, U = {fmtBillion(pl3.freeUb)}, fark {fmtGap(pl3.freeGap)}).
              p = <N>{rangesText(pl3.plateau)}</N> arasındaki her hastane sayısının alt sınırı serbest çözümün değerinin altında: bunların {fmtInt(pl3.freeN)}’dan kötü olduğu
              kanıtlanamıyor (“fark gap içinde”).
              {pl3.neighbors.length ? (
                <>
                  {' '}
                  Komşular:{' '}
                  {pl3.neighbors.map((n, i) => (
                    <span key={n.p}>
                      {i ? ', ' : ''}p = {n.p} için <N>+{fmtNumber(n.diffTl / 1e6, 1)} milyon TL</N>
                    </span>
                  ))}{' '}
                  — toplamın yanında çok küçük.
                </>
              ) : null}{' '}
              Dürüst yanıt: “{fmtInt(pl3.freeN)} bulunan en iyisi; komşu sayılar ekonomik olarak eşdeğer.”
            </>
          ) : s3.status === 'error' ? (
            'p taraması yüklenemedi.'
          ) : (
            'p taraması yükleniyor… '
          )}
          {pl5 ? (
            <>
              {' '}
              {pl5.km} km’de ise sonuç kesin: {pl5.unique ? <>p = {fmtInt(pl5.freeN)} taranan p’ler arasında kanıtlı tek en iyi.</> : <>dip p = {rangesText(pl5.plateau)}.</>}
            </>
          ) : null}
        </Card>

        <Card id="sinir-canli" title="Canlı sertifika Lagrange alt sınırıyla" tag="çözücü" jumps={['model_alt_sinir', 'dal_sinir']}>
          Canlı çözümde (3 km’de önerilen süre {live3 !== null ? `${fmtInt(live3)} sn` : '—'}) alt sınır Lagrange gevşetmesinden gelir; hedef fark{' '}
          <N>{target !== null ? fmtGap(target) : '—'}</N>. Önceden çözülen senaryolarda tam MILP (HiGHS dal-sınır) çalışır ve sınır daha sıkıdır. Aynı ayarla iki canlı çözüm
          hedef farktan küçük ölçüde farklı çıkabilir: bu fark yöntemin gürültüsüdür, anlamlı değildir.
          {r ? (
            <>
              {' '}
              Gösterilen sonuç: alt sınır {r.bounds.lb_source === 'mip' ? 'HiGHS dal-sınırdan' : r.bounds.lb_source?.startsWith('lagrange') ? 'Lagrange gevşetmesinden' : r.bounds.lb_source ?? '—'}, fark{' '}
              <N>{fmtGap(r.bounds.gap)}</N>
              {diag.bounds?.mip_kind ? <> (çalışan MIP: {diag.bounds.mip_kind})</> : null}.
            </>
          ) : null}
        </Card>

        <Card id="sinir-k" title="Aday listeleri k ile kırpılır" tag="çözücü">
          Her talep hücresi R içindeki adaylardan yalnız en yakın <N>{typeof k === 'number' && k > 0 ? fmtInt(k) : 'tümü'}</N> tanesine atanabilir (seyrek model, hız). Sertifika
          bu modele aittir (“k en yakın aday listesiyle kurulan model için”); R içindeki tüm adaylarla kurulan modelin optimumu biraz daha ucuz olabilir. Parametre panelinde k =
          0 “R içindeki tümü” demektir.
        </Card>

        <Card id="sinir-statik" title="Tek dönem, belirlenimci talep" tag="model">
          Yatak talebi nüfusla sabit oranlı (q<sub>i</sub> = w<sub>i</sub> · b / 1000); mevsimsellik, talep belirsizliği, aşamalı yatırım, personel ve ameliyathane gibi
          kapasiteler yok. Hastane kapasitesi yalnız yatak sayısıdır. Erişim maliyeti planlama ufku boyunca sabit yıllık başvuru ile hesaplanır.
        </Card>
      </div>
    </Stack>
  );
}
