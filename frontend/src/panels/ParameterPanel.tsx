/**
 * Parametre paneli (şartname 6.3, 4.4).
 *
 *   [Hazır senaryo kartları]
 *   [Problem: ızgara 2/3/5 km · nüfus yılı 2025/2030 · amaç modu (+ bütçe / p)]
 *   [Otomatik çöz · Şimdi çöz · aralık dışına izin]
 *   [Değişen parametreler (çipler) · Tümünü sıfırla]
 *   [Ara … · durum süzgeci: kaynaklı / hesap / varsayım / model seçimi]
 *   [A–I akordeon: her parametre ParamControl — kaydırıcı + sayı kutusu, birim, durum, bilgi, varsayılana dön]
 *   [Kilitli / yasak hücreler · manuel mod (Faz 7)]
 *
 * Değişiklik depoya yazılır; otomatik çözüm 600 ms sonra başlar (store/solver.ts). Üst bölümde düzenlenen anahtarlar
 * (QUICK_KEYS) gruplarında salt okunur özet satırı olarak görünür; bilgi amaçlı anahtarlar (INFO_ONLY_KEYS) da salt
 * okunurdur. Seçili moda göre kullanılmayan parametreler soluk ve nedeniyle gösterilir (paramUtils.relevance).
 */
import {
  Accordion,
  Alert,
  Badge,
  Button,
  CloseButton,
  Group,
  ScrollArea,
  SegmentedControl,
  Stack,
  Switch,
  Text,
  TextInput,
  Title,
  Tooltip,
} from '@mantine/core';
import { useMemo, useState } from 'react';

import type { GridKm, Meta, ObjectiveMode, ParamStatus, ParamValue } from '../api/types.ts';
import { fmtInt } from '../format/number.ts';
import { baseValue, changedKeys, paramValue, selectCanSolve, selectGrid, useApp, type AppState } from '../store/appStore.ts';
import { solveNow } from '../store/solver.ts';
import { PARAM_STATUS_UI } from '../theme/palette.ts';
import { IconPlay, IconReset, IconSearch } from '../ui/icons.tsx';
import { InfoPopover } from '../ui/InfoPopover.tsx';
import { LockList } from './LockList.tsx';
import { ParamControl } from './ParamControl.tsx';
import { ParamInfo } from './ParamInfo.tsx';
import { INFO_ONLY_KEYS, QUICK_KEYS, normalizeTr, relevance } from './paramUtils.ts';
import { ScenarioPicker } from './ScenarioPicker.tsx';
import './panels.css';

export { ParamControl } from './ParamControl.tsx';
export type { ParamControlProps } from './ParamControl.tsx';

export interface ParameterPanelProps {
  /** dar ekranda çekmece içinde gösteriliyor (başlık ve kaydırma çekmecede) */
  inDrawer?: boolean;
}

const STATUS_ORDER: ParamStatus[] = ['kaynakli', 'hesap', 'varsayim', 'model_secimi'];

/** Sunucunun 422 doğrulama hatalarından anahtar → ileti. */
function serverErrorsByKey(error: AppState['error']): Record<string, string> {
  const out: Record<string, string> = {};
  for (const it of error?.items ?? []) if (it.key) out[it.key] = it.message_tr;
  return out;
}

function demandCellsText(meta: Meta, gridKm: GridKm, source: ParamValue | undefined, year: ParamValue | undefined): string {
  const g = meta.grids.find((x) => x.km === gridKm);
  if (!g) return '';
  const k = `${String(source ?? '').startsWith('ghs') ? 'ghs' : 'wp'}_${year ?? 2025}`;
  const nd = g.n_demand_cells[k];
  return `${fmtInt(g.n_cells)} hücre · ${nd ? `${fmtInt(nd)} nüfuslu talep hücresi · ` : ''}önerilen süre ${fmtInt(g.recommended_time_limit_s)} sn`;
}

export function ParameterPanel({ inDrawer }: ParameterPanelProps) {
  const meta = useApp((s) => s.meta);
  const problem = useApp((s) => s.problem);
  const openGroups = useApp((s) => s.ui.openGroups);
  const autoSolve = useApp((s) => s.ui.autoSolve);
  const manualMode = useApp((s) => s.ui.manualMode);
  const error = useApp((s) => s.error);
  const online = useApp((s) => s.connection.mode === 'online');
  const canSolve = useApp(selectCanSolve);
  const grid = useApp(selectGrid);
  const solving = useApp((s) => !!s.job && s.job.phase !== 'debounce');
  const setUi = useApp((s) => s.setUi);
  const setParam = useApp((s) => s.setParam);
  const resetParam = useApp((s) => s.resetParam);
  const resetAll = useApp((s) => s.resetAllParams);
  const applyScenario = useApp((s) => s.applyScenario);
  const setGridKm = useApp((s) => s.setGridKm);
  const setMode = useApp((s) => s.setMode);
  const setLock = useApp((s) => s.setLock);
  const clearLocks = useApp((s) => s.clearLocks);
  const select = useApp((s) => s.select);
  const setAllowOutsideRange = useApp((s) => s.setAllowOutsideRange);

  const [query, setQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<ParamStatus[]>([]);

  const statusCounts = useMemo(() => {
    const c: Record<ParamStatus, number> = { kaynakli: 0, hesap: 0, varsayim: 0, model_secimi: 0 };
    if (meta) for (const p of Object.values(meta.parameters)) c[p.status] += 1;
    return c;
  }, [meta]);

  if (!meta) {
    return (
      <Stack p="md" gap="xs" data-testid="parameter-panel">
        <Title order={2}>Parametreler</Title>
        <Text size="sm" c="dimmed">
          Parametre kaydı yükleniyor…
        </Text>
      </Stack>
    );
  }

  const state = { meta, problem };
  const get = (k: string) => paramValue(state, k);
  const changed = new Set(changedKeys(state));
  const scenario = meta.scenarios.find((s) => s.id === problem.scenarioId);
  const serverErrors = serverErrorsByKey(error);
  const disabled = !online;
  const ctx = { overrides: problem.overrides, meta, gridKm: problem.gridKm };
  const mode = (get('solver.objective_mode') as ObjectiveMode | undefined) ?? 'toplam';

  const control = (k: string, opts: { readOnly?: boolean; readOnlyHint?: string } = {}) => {
    const p = meta.parameters[k];
    if (!p) return null;
    const scenarioHas = !!scenario && k in scenario.overrides;
    const readOnly = opts.readOnly ?? INFO_ONLY_KEYS.has(k);
    return (
      <ParamControl
        key={k}
        param={p}
        value={get(k)}
        changed={changed.has(k)}
        allowOutsideRange={problem.allowOutsideRange}
        disabled={disabled}
        onChange={(v) => setParam(k, v)}
        onReset={() => resetParam(k)}
        onAllowOutsideRange={() => setAllowOutsideRange(true)}
        scenarioValue={scenarioHas ? baseValue(meta, problem.scenarioId, k) : undefined}
        scenarioTitle={scenario?.title}
        relevance={relevance(k, get, ctx)}
        serverError={serverErrors[k] ?? null}
        readOnly={readOnly}
        readOnlyHint={opts.readOnlyHint}
      />
    );
  };

  const groupKeys = (keys: string[]) => keys.filter((k) => meta.parameters[k]);
  const renderGroupKey = (k: string) => (QUICK_KEYS.has(k) ? control(k, { readOnly: true, readOnlyHint: '↑ “Problem” bölümünde değiştirilir' }) : control(k));

  /* ---------------- arama / durum süzgeci */
  const q = normalizeTr(query.trim());
  const filtering = q.length > 0 || statusFilter.length > 0;
  const matches = filtering
    ? meta.groups.flatMap((g) =>
        groupKeys(g.keys)
          .filter((k) => {
            const p = meta.parameters[k];
            if (statusFilter.length && !statusFilter.includes(p.status)) return false;
            if (!q) return true;
            return normalizeTr(`${p.label} ${p.key} ${p.unit}`).includes(q);
          })
          .map((k) => ({ k, g })),
      )
    : [];

  /* ---------------- üst "Problem" bölümü */
  const gridParam = meta.parameters['grid.cell_km'];
  const yearParam = meta.parameters['population.scenario_year'];
  const modeParam = meta.parameters['solver.objective_mode'];
  const modeTitle = meta.modes.find((m) => m.id === mode)?.title ?? mode;
  const quick = (
    <Stack gap={8} data-testid="problem-quick">
      <Text size="sm" fw={600}>
        Problem
      </Text>
      <div className="pp-quick-row">
        <div className="pp-quick-label">
          <Text size="sm" id="pp-grid-label">
            Izgara
          </Text>
          {gridParam && (
            <InfoPopover label="Izgara hücre boyutu: açıklama" size="xs">
              <ParamInfo param={gridParam} value={problem.gridKm} />
            </InfoPopover>
          )}
        </div>
        <SegmentedControl
          fullWidth
          size="sm"
          value={String(problem.gridKm)}
          onChange={(v) => setGridKm(Number(v) as GridKm)}
          data={meta.grids.map((g) => ({ value: String(g.km), label: `${g.km} km` }))}
          aria-labelledby="pp-grid-label"
          disabled={disabled}
        />
      </div>
      <Text size="xs" c="dimmed">
        {demandCellsText(meta, problem.gridKm, get('population.grid_source'), get('population.scenario_year'))}
      </Text>
      {yearParam && (
        <div className="pp-quick-row" data-param-key="population.scenario_year">
          <div className="pp-quick-label">
            <Text size="sm" id="pp-year-label" fw={changed.has('population.scenario_year') ? 700 : 400}>
              Nüfus yılı
            </Text>
            <InfoPopover label="Nüfus senaryosu: açıklama" size="xs">
              <ParamInfo param={yearParam} value={get('population.scenario_year')} />
            </InfoPopover>
          </div>
          <SegmentedControl
            fullWidth
            size="sm"
            value={String(get('population.scenario_year'))}
            onChange={(v) => setParam('population.scenario_year', Number(v))}
            data={meta.scenario_years.map((y) => ({ value: String(y), label: String(y) }))}
            aria-labelledby="pp-year-label"
            disabled={disabled}
          />
        </div>
      )}
      {modeParam && (
        <div className="pp-quick-row" data-param-key="solver.objective_mode">
          <div className="pp-quick-label">
            <Text size="sm" id="pp-mode-label" fw={changed.has('solver.objective_mode') ? 700 : 400}>
              Amaç
            </Text>
            <InfoPopover label="Amaç modu: açıklama" size="xs">
              <ParamInfo param={modeParam} value={mode} />
            </InfoPopover>
          </div>
          <SegmentedControl
            fullWidth
            size="sm"
            value={mode}
            onChange={(v) => setMode(v as ObjectiveMode)}
            data={meta.modes.map((m) => ({ value: m.id, label: m.id === 'toplam' ? 'Toplam' : m.id === 'butce' ? 'Bütçe' : 'Sabit p' }))}
            aria-labelledby="pp-mode-label"
            disabled={disabled}
          />
        </div>
      )}
      <Text size="xs" c="dimmed">
        {modeTitle}
      </Text>
      {mode === 'butce' && control('solver.budget_tl')}
      {mode === 'sabit_p' && control('solver.p_fixed')}
    </Stack>
  );

  const body = (
    <Stack gap="sm" p="md" data-testid="parameter-panel">
      {!inDrawer && (
        <Group justify="space-between" gap="xs">
          <Title order={2}>Parametreler</Title>
          <Text size="xs" c="dimmed">
            {Object.keys(meta.parameters).length} parametre
          </Text>
        </Group>
      )}
      {!online && (
        <Alert color="orange" variant="light" p="xs">
          <Text size="xs">Çevrimdışı mod: parametreler gösterilir ama değiştirilemez (canlı çözüm kapalı).</Text>
        </Alert>
      )}

      <ScenarioPicker scenarios={meta.scenarios} value={problem.scenarioId} modified={changed.size > 0} gridKm={problem.gridKm} onChange={applyScenario} disabled={disabled} />

      {quick}

      <Group justify="space-between" gap="xs" wrap="nowrap">
        <Switch label="Otomatik çöz" checked={autoSolve} onChange={(e) => setUi({ autoSolve: e.currentTarget.checked })} size="sm" styles={{ label: { whiteSpace: 'nowrap' } }} />
        <Button
          size="compact-sm"
          variant={autoSolve ? 'light' : 'filled'}
          leftSection={<IconPlay size={14} />}
          style={{ flexShrink: 0 }}
          onClick={() => void solveNow()}
          disabled={!canSolve || solving}
          data-testid="solve-now"
        >
          Şimdi çöz
        </Button>
      </Group>
      <Tooltip label="Açıkken kaydırıcı aralığı dışındaki (ama modelin tanım kümesindeki) değerler sunucuya gönderilir; sunucu uyarıyla kabul eder.">
        <div>
          <Switch
            label="Kaydırıcı aralığı dışına izin ver"
            checked={problem.allowOutsideRange}
            onChange={(e) => setAllowOutsideRange(e.currentTarget.checked)}
            size="xs"
            disabled={disabled}
          />
        </div>
      </Tooltip>

      {changed.size > 0 && (
        <Stack gap={4} data-testid="changed-params">
          <Group justify="space-between" gap="xs" wrap="nowrap">
            <Badge variant="filled" tt="none">
              {changed.size} parametre değişti
            </Badge>
            <Tooltip label={scenario && Object.keys(scenario.overrides).length ? `Tüm değişiklikleri sil, “${scenario.title}” senaryosunun değerlerine dön` : 'Tüm değişiklikleri sil, kayıttaki varsayılanlara dön'}>
              <Button size="compact-xs" variant="subtle" leftSection={<IconReset size={13} />} onClick={resetAll} disabled={disabled}>
                Tümünü sıfırla
              </Button>
            </Tooltip>
          </Group>
          <Group gap={4}>
            {[...changed].map((k) => (
              <Badge
                key={k}
                variant="light"
                tt="none"
                size="sm"
                style={{ maxWidth: '100%' }}
                rightSection={
                  <CloseButton size="xs" aria-label={`${meta.parameters[k]?.label ?? k}: varsayılana dön`} onClick={() => resetParam(k)} disabled={disabled} />
                }
              >
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{meta.parameters[k]?.label ?? k}</span>
              </Badge>
            ))}
          </Group>
        </Stack>
      )}

      <Stack gap={6}>
        <TextInput
          size="sm"
          placeholder="Parametre ara (ad, anahtar, birim)…"
          aria-label="Parametre ara"
          value={query}
          onChange={(e) => setQuery(e.currentTarget.value)}
          leftSection={<IconSearch size={15} />}
          rightSection={query ? <CloseButton size="sm" aria-label="Aramayı temizle" onClick={() => setQuery('')} /> : null}
        />
        <div className="pp-status-filter" role="group" aria-label="Duruma göre süz">
          {STATUS_ORDER.map((st) => {
            const on = statusFilter.includes(st);
            return (
              <Tooltip key={st} label={`${PARAM_STATUS_UI[st].hint} Tıklayınca yalnız bu durumdakiler listelenir.`}>
                <Button
                  size="compact-xs"
                  radius="xl"
                  variant={on ? 'filled' : 'light'}
                  color={PARAM_STATUS_UI[st].color}
                  aria-pressed={on}
                  onClick={() => setStatusFilter((f) => (on ? f.filter((x) => x !== st) : [...f, st]))}
                >
                  {PARAM_STATUS_UI[st].label} {statusCounts[st]}
                </Button>
              </Tooltip>
            );
          })}
        </div>
      </Stack>

      {filtering ? (
        <Stack gap={2} data-testid="param-search-results">
          <Group justify="space-between">
            <Text size="xs" c="dimmed">
              {matches.length} parametre bulundu
            </Text>
            <Button
              size="compact-xs"
              variant="subtle"
              onClick={() => {
                setQuery('');
                setStatusFilter([]);
              }}
            >
              Süzgeci kaldır
            </Button>
          </Group>
          {matches.map(({ k, g }) => (
            <div key={k}>
              <Text size="xs" c="dimmed" ml={4} mt={4}>
                {g.title}
              </Text>
              {renderGroupKey(k)}
            </div>
          ))}
        </Stack>
      ) : (
        // keepMountedMode 'display-none': React Activity ile gizleme, açılan grubun kaydırıcısında ilk tıklamayı ara sıra
        // yutuyordu (içerik bir an dokunulamaz kalıyordu); display:none ile içerik ve taslak değerler korunur
        <Accordion
          multiple
          value={openGroups}
          onChange={(v) => setUi({ openGroups: v })}
          variant="separated"
          chevronPosition="left"
          keepMountedMode="display-none"
          data-testid="param-groups"
        >
          {meta.groups.map((g) => {
            const keys = groupKeys(g.keys);
            const nChanged = keys.filter((k) => changed.has(k)).length;
            const nErr = keys.filter((k) => serverErrors[k]).length;
            return (
              <Accordion.Item key={g.id} value={g.id} data-group={g.id}>
                <Accordion.Control>
                  <Group justify="space-between" wrap="nowrap" gap="xs">
                    <Text size="sm" fw={600}>
                      {g.title}
                    </Text>
                    <Group gap={4} wrap="nowrap">
                      {nErr > 0 && (
                        <Badge size="xs" color="red" tt="none">
                          hata
                        </Badge>
                      )}
                      {nChanged > 0 && (
                        <Tooltip label={`${nChanged} parametre değişti`}>
                          <Badge size="sm" variant="filled" circle aria-label={`${nChanged} parametre değişti`}>
                            {nChanged}
                          </Badge>
                        </Tooltip>
                      )}
                      <Text size="xs" c="dimmed">
                        {keys.length}
                      </Text>
                    </Group>
                  </Group>
                </Accordion.Control>
                <Accordion.Panel>
                  <Stack gap={0}>{keys.map(renderGroupKey)}</Stack>
                </Accordion.Panel>
              </Accordion.Item>
            );
          })}
        </Accordion>
      )}

      <LockList
        locks={problem.locks}
        grid={grid}
        gridKm={problem.gridKm}
        disabled={disabled}
        onRemove={(c) => setLock(c, null)}
        onClear={clearLocks}
        onSelect={select}
        manualMode={manualMode}
        onManualMode={(v) => setUi({ manualMode: v })}
      />
    </Stack>
  );
  return inDrawer ? body : <ScrollArea h="100%" type="auto" offsetScrollbars>{body}</ScrollArea>;
}

export default ParameterPanel;
