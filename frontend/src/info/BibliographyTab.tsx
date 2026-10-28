/**
 * "Kaynakça" sekmesi (şartname 7.14): klasik literatür (data/reference/literatur.yaml — künyeler Crossref vb. üst
 * verisinden doğrulandı, DOI bağlantılı), yöntem kaynakları (Lagrange gevşetmesi; docs/FAZ3_RAPOR.md §12'deki
 * doğrulanmış künyeler) ve veri kaynakları (data/sources/*.yaml, alan alan; yayımcı, yayım ve erişim tarihi, hangi
 * değerler için kullanıldığı, hangi parametrelerde). Hepsi GET /api/sources'tan (sunucu kapalıyken derlemedeki kopya).
 *
 * Her satırın çapası `kaynak-<id>`: parametre bilgi kutusu ve Veri tablosundaki kaynak düğmeleri buraya atlar; ilgili
 * alan akordeonu kendiliğinden açılır.
 */
import { Accordion, Anchor, Badge, Group, Spoiler, Stack, Text, TextInput, Title, Tooltip, UnstyledButton } from '@mantine/core';
import { useEffect, useMemo, useState } from 'react';

import type { LiteratureEntry, SourceEntry } from '../api/types.ts';
import { fmtDate, fmtInt } from '../format/number.ts';
import { normalizeTr } from '../panels/paramUtils.ts';
import { useApp } from '../store/appStore.ts';
import { IconSearch } from '../ui/icons.tsx';
import { useInfo } from './infoStore.ts';
import { jumpToParam } from './jumps.ts';
import { LoadError, Loading, Prose } from './ui.tsx';
import { useSources } from './useInfoData.ts';

/**
 * Lagrange gevşetmesi ve alt gradyan yöntemlerinin künyeleri — docs/FAZ3_RAPOR.md §12 ("Crossref'te doğrulandı,
 * ayar deneyi T3") ile backend/model/lagrange.py başındaki atıflar. Rapor başlık vermediği için başlık yazılmaz.
 * /api/sources literatüründe aynı DOI varsa tekrar gösterilmez.
 */
const METHOD_REFS: { id: string; cite: string; venue: string; doi: string; use: string }[] = [
  { id: 'geoffrion_mcbride1978', cite: 'Geoffrion & McBride (1978)', venue: 'AIIE Transactions 10:40–47', doi: '10.1080/05695557808975181', use: 'Kapasiteli tesis yerleşiminde Lagrange gevşetmesi (alt problem aday başına ayrışır).' },
  { id: 'cornuejols1991', cite: 'Cornuéjols, Sridharan & Thizy (1991)', venue: 'European Journal of Operational Research 50:280–297', doi: '10.1016/0377-2217(91)90261-S', use: 'Kapasiteli tesis yerleşimi gevşetmelerinin karşılaştırması (Lagrange duali ≥ güçlü LP).' },
  { id: 'held1974', cite: 'Held, Wolfe & Crowder (1974)', venue: 'Mathematical Programming 6:62–88', doi: '10.1007/BF01580223', use: 'Alt gradyan yöntemi ile çarpan güncelleme.' },
  { id: 'barahona_anbil2000', cite: 'Barahona & Anbil (2000)', venue: 'Mathematical Programming 87:385–399', doi: '10.1007/s101070050002', use: 'Hacim algoritması (çarpan güncellemede kullanılan yöntem).' },
];

/** Alanların gösterim sırası (dosya adı); listede olmayanlar sonda, sunucunun sırasıyla. */
const DOMAIN_ORDER = ['nufus', 'nufus_izgara', 'sinirlar', 'saglik', 'maliyet', 'yatirim', 'erisim', 'arazi_osm', 'izgara', 'alan_literatur'];

function authorsText(a: string[]): string {
  if (a.length > 6) return `${a.slice(0, 3).join(', ')} vd. (${a.length} yazar)`;
  if (a.length <= 2) return a.join(' & ');
  return `${a.slice(0, -1).join(', ')} & ${a[a.length - 1]}`;
}

function LitRow({ l }: { l: LiteratureEntry }) {
  const doiUrl = l.doi ? `https://doi.org/${l.doi}` : l.url;
  return (
    <div className="lit-row" id={`kaynak-${l.id}`} data-testid={`lit-${l.id}`}>
      <Text size="sm" style={{ lineHeight: 1.45 }}>
        <b>{authorsText(l.authors)}</b> ({l.year}). {l.title.replace(/\.+$/, '')}. <i>{l.journal || l.publisher}</i>
        {l.volume && !(l.journal ?? '').includes(`vol. ${l.volume}`) ? ` ${l.volume}` : ''}
        {l.issue ? `(${l.issue})` : ''}
        {l.pages ? `, ${l.pages}` : ''}.{' '}
        {doiUrl && (
          <Anchor href={doiUrl} target="_blank" rel="noopener noreferrer" size="sm" title="DOI (internet gerekir)">
            {l.doi ? `doi:${l.doi}` : doiUrl}
          </Anchor>
        )}
      </Text>
      {(l.notes || l.verified_via) && (
        <Spoiler maxHeight={38} showLabel="devamı" hideLabel="kısalt">
          {l.notes && (
            <Text size="xs" c="dimmed" style={{ lineHeight: 1.4 }}>
              {l.notes}
            </Text>
          )}
          {l.verified_via && (
            <Text size="xs" c="dimmed" style={{ lineHeight: 1.4 }}>
              Doğrulama: {l.verified_via}
            </Text>
          )}
        </Spoiler>
      )}
    </div>
  );
}

function SourceRow({ s, paramLabel }: { s: SourceEntry; paramLabel: (k: string) => string }) {
  const http = /^https?:\/\//i.test(s.url ?? '');
  return (
    <div className="kaynak-row" id={`kaynak-${s.id}`} data-testid={`kaynak-${s.id}`}>
      <Text size="sm" fw={600} style={{ lineHeight: 1.35 }}>
        {http ? (
          <Anchor href={s.url} target="_blank" rel="noopener noreferrer" size="sm" fw={600} title="Kaynağın kendisi (internet gerekir)">
            {s.title}
          </Anchor>
        ) : (
          s.title
        )}
      </Text>
      <Group gap={6} mt={2} wrap="wrap">
        <Badge size="xs" variant="outline" color="gray" tt="none">
          {s.kind_tr ?? s.kind}
        </Badge>
        <Text size="xs" c="dimmed">
          {s.publisher}
          {s.published ? ` · yayım ${fmtDate(s.published)}` : ''}
          {s.accessed ? ` · erişim ${fmtDate(s.accessed)}` : ''}
          {!http && s.url ? ` · ${s.url}` : ''}
        </Text>
        <Text size="xs" c="dimmed" ff="monospace">
          {s.id}
        </Text>
      </Group>
      {s.used_for && s.used_for.length > 0 && (
        <Spoiler maxHeight={44} showLabel="devamı" hideLabel="kısalt" mt={4}>
          <Stack gap={2}>
            {s.used_for.map((u, i) => (
              <Text key={i} size="xs" style={{ lineHeight: 1.4 }}>
                • {u}
              </Text>
            ))}
          </Stack>
        </Spoiler>
      )}
      {s.parameters && s.parameters.length > 0 && (
        <Group gap={4} mt={4} wrap="wrap">
          <Text size="xs" c="dimmed">
            Parametreler:
          </Text>
          {s.parameters.map((k) => (
            <Tooltip key={k} label={`${paramLabel(k)} — Veri sekmesindeki satırı`} openDelay={300}>
              <UnstyledButton onClick={() => jumpToParam(k)} aria-label={`${paramLabel(k)} parametresine git`}>
                <Badge size="xs" variant="light" color="blue" tt="none" style={{ cursor: 'pointer' }}>
                  {k}
                </Badge>
              </UnstyledButton>
            </Tooltip>
          ))}
        </Group>
      )}
    </div>
  );
}

export default function BibliographyTab() {
  const st = useSources();
  const meta = useApp((s) => s.meta);
  const pending = useInfo((s) => s.pendingAnchor);
  const [open, setOpen] = useState<string[]>([]);
  const [query, setQuery] = useState('');

  const data = st.data;
  const domains = useMemo(() => {
    const ds = [...(data?.domains ?? [])];
    const rank = (id: string) => {
      const i = DOMAIN_ORDER.indexOf(id);
      return i < 0 ? DOMAIN_ORDER.length : i;
    };
    return ds.sort((a, b) => rank(a.id) - rank(b.id));
  }, [data]);

  // bir kaynağa atlanıyorsa: aramayı temizle, alanını aç
  useEffect(() => {
    if (!pending?.startsWith('kaynak-') || !data) return;
    const id = pending.slice('kaynak-'.length);
    const d = data.domains.find((x) => x.sources.some((s) => s.id === id));
    setQuery('');
    if (d) setOpen((o) => (o.includes(d.id) ? o : [...o, d.id]));
  }, [pending, data]);

  const q = normalizeTr(query.trim());
  const match = (s: SourceEntry) =>
    !q || q.split(/\s+/).every((w) => normalizeTr([s.id, s.title, s.publisher, ...(s.used_for ?? []), ...(s.parameters ?? [])].join(' ')).includes(w));
  const litMatch = (l: LiteratureEntry) => !q || q.split(/\s+/).every((w) => normalizeTr([l.id, l.title, l.journal, l.doi, ...l.authors].join(' ')).includes(w));
  const paramLabel = (k: string) => meta?.parameters[k]?.label ?? k;

  if (st.status === 'loading' || st.status === 'idle') return <Loading what="Kaynakça" />;
  if (!data) return <LoadError what="Kaynakça" error={st.error} file="data/sources/*.yaml, data/reference/literatur.yaml (GET /api/sources)" />;

  const litDois = new Set(data.literature.map((l) => (l.doi ?? '').toLowerCase()));
  const methods = METHOD_REFS.filter((m) => !litDois.has(m.doi.toLowerCase()));
  const lastAccess = data.domains
    .flatMap((d) => d.sources.map((s) => s.accessed ?? ''))
    .filter(Boolean)
    .sort()
    .pop();
  const lits = data.literature.filter(litMatch);
  const shownDomains = domains.map((d) => ({ ...d, shown: d.sources.filter(match) })).filter((d) => d.shown.length > 0);
  const openIds = q ? shownDomains.map((d) => d.id) : open;

  return (
    <Stack gap="lg" id="info-kaynakca" data-testid="bibliography-tab">
      <Group justify="space-between" align="flex-end" wrap="wrap" gap="sm">
        <Prose>
          <b>{fmtInt(data.n_sources)}</b> veri kaynağı ({fmtInt(data.domains.length)} alan) ve <b>{fmtInt(data.literature.length)}</b> literatür künyesi
          {lastAccess ? <> · son erişim {fmtDate(lastAccess)}</> : null}
          {st.fromBundle ? ' · sunucu kapalı: derlemedeki kopya' : ''}. Bağlantılar kaynağın kendisine gider (internet gerekir); künyeler çevrimdışı da okunur.
        </Prose>
        <TextInput
          value={query}
          onChange={(e) => setQuery(e.currentTarget.value)}
          placeholder="Kaynaklarda ara…"
          leftSection={<IconSearch size={15} />}
          aria-label="Kaynaklarda ara"
          style={{ flex: '0 1 22rem' }}
          data-testid="source-search"
        />
      </Group>

      <Stack gap="xs">
        <Title order={3} style={{ fontSize: '1.15rem' }}>
          Klasik literatür
        </Title>
        {lits.length ? (
          <Stack gap={6}>
            {lits.map((l) => (
              <LitRow key={l.id} l={l} />
            ))}
          </Stack>
        ) : (
          <Text size="sm" c="dimmed">
            Aramaya uyan künye yok.
          </Text>
        )}
      </Stack>

      {methods.length > 0 && !q && (
        <Stack gap="xs">
          <Title order={3} style={{ fontSize: '1.15rem' }}>
            Çözüm yöntemi (Lagrange gevşetmesi)
          </Title>
          <Stack gap={6}>
            {methods.map((m) => (
              <div className="lit-row" key={m.id} id={`kaynak-${m.id}`}>
                <Text size="sm">
                  <b>{m.cite}</b>. <i>{m.venue}</i>.{' '}
                  <Anchor href={`https://doi.org/${m.doi}`} target="_blank" rel="noopener noreferrer" size="sm" title="DOI (internet gerekir)">
                    doi:{m.doi}
                  </Anchor>
                </Text>
                <Text size="xs" c="dimmed">
                  {m.use}
                </Text>
              </div>
            ))}
          </Stack>
          <Text size="xs" c="dimmed">
            Künyeler docs/FAZ3_RAPOR.md §12’den (Crossref’te doğrulandı); başlıklar raporda verilmediği için yazılmadı.
          </Text>
        </Stack>
      )}

      <Stack gap="xs">
        <Title order={3} style={{ fontSize: '1.15rem' }}>
          Veri kaynakları
        </Title>
        {shownDomains.length === 0 ? (
          <Text size="sm" c="dimmed">
            Aramaya uyan kaynak yok.
          </Text>
        ) : (
          <Accordion multiple value={openIds} onChange={(v) => !q && setOpen(v)} variant="separated" radius="md" chevronPosition="left" data-testid="source-domains">
            {shownDomains.map((d) => (
              <Accordion.Item key={d.id} value={d.id}>
                <Accordion.Control>
                  <Group gap="xs" wrap="nowrap" justify="space-between">
                    <Text fw={650} size="sm">
                      {d.title}
                    </Text>
                    <Group gap={6} wrap="nowrap">
                      <Badge size="sm" variant="light" color="gray" tt="none">
                        {q ? `${fmtInt(d.shown.length)} / ${fmtInt(d.sources.length)}` : fmtInt(d.sources.length)} kaynak
                      </Badge>
                      <Text size="xs" c="dimmed" ff="monospace" visibleFrom="md">
                        {d.file}
                      </Text>
                    </Group>
                  </Group>
                </Accordion.Control>
                <Accordion.Panel>
                  {d.shown.map((s) => (
                    <SourceRow key={s.id} s={s} paramLabel={paramLabel} />
                  ))}
                </Accordion.Panel>
              </Accordion.Item>
            ))}
          </Accordion>
        )}
      </Stack>
    </Stack>
  );
}
