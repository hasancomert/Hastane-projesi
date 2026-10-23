/**
 * Tek parametre denetimi (şartname 6.3): kaydırıcı + sayı kutusu (Türkçe biçim 1.234,5), birim, durum rozeti,
 * bilgi kutusu (anlam, kaynak/gerekçe), "varsayılana dön", alternatif değer çipleri; seçenekler bölümlü denetim,
 * evet/hayır anahtar, profil salt okunur tablo. Değişen parametre solda renkli çizgi ve kalın etiketle işaretlenir.
 *
 * Değer akışı: kaydırıcı sürüklenirken yalnız taslak değişir, bırakınca (ya da ok tuşuyla) gönderilir; sürükleme
 * sürerken otomatik çözüm sayacı bekletilir (store/solver.ts holdAutoSolve). Sayı kutusu yazmayı 700 ms bekler,
 * Enter/odak kaybında hemen gönderir. Tanım kümesi dışı değer (ör. eksi) gönderilmez, hata yazılır; kaydırıcı
 * aralığı dışı değer için "aralık dışına izin ver" sorulur (sunucu uyarıyla kabul eder).
 *
 * Düzen (dar panelde de kaydırıcı geniş olsun): [etiket · bilgi · sıfırla] / [kaydırıcı — tam genişlik] /
 * [sayı kutusu · birim · durum rozeti].
 */
import { ActionIcon, Badge, Button, Group, NumberInput, SegmentedControl, Select, Slider, Switch, Table, Text, Tooltip, VisuallyHidden } from '@mantine/core';
import { useDebouncedCallback } from '@mantine/hooks';
import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react';

import type { ParamMeta, ParamValue } from '../api/types.ts';
import { ParamStatusBadge } from '../components/StatusBadges.tsx';
import { holdAutoSolve, releaseAutoSolve } from '../store/solver.ts';
import { fmtNumber, fmtParamValue } from '../format/number.ts';
import { PARAM_STATUS_UI } from '../theme/palette.ts';
import { IconReset } from '../ui/icons.tsx';
import { InfoPopover } from '../ui/InfoPopover.tsx';
import { ParamInfo } from './ParamInfo.tsx';
import {
  checkValue,
  choiceLabel,
  choiceOptions,
  fmtDisplay,
  fromDisplay,
  numberSpec,
  numericAlternatives,
  sameNumber,
  shortAltLabel,
  toDisplay,
  type NumberSpec,
  type Relevance,
  type ValueCheck,
} from './paramUtils.ts';

/** Tek parametre denetiminin sözleşmesi (iskeletteki alanlar + isteğe bağlı ekler). */
export interface ParamControlProps {
  param: ParamMeta;
  /** geçerli değer (override → senaryo → kayıt) */
  value: ParamValue | undefined;
  /** seçili senaryonun değerinden farklı mı ("varsayılana dön" görünür) */
  changed: boolean;
  /** kaydırıcı aralığı dışına izin var mı */
  allowOutsideRange: boolean;
  disabled?: boolean;
  onChange(value: ParamValue): void;
  onReset(): void;
  /** "aralık dışına izin ver" (depoda setAllowOutsideRange(true)) */
  onAllowOutsideRange?(): void;
  /** seçili senaryonun bu anahtar için değeri (kayıttan farklıysa) */
  scenarioValue?: ParamValue;
  scenarioTitle?: string;
  /** bu ayarlarda modele giriyor mu + not */
  relevance?: Relevance | null;
  /** sunucunun bu anahtar için doğrulama hatası (422) */
  serverError?: string | null;
  /** salt okunur özet satırı (bilgi amaçlı ya da üst bölümde düzenlenen anahtarlar) */
  readOnly?: boolean;
  /** salt okunur satırda değerin yanında gösterilecek not ("↑ Problem bölümünde") */
  readOnlyHint?: string;
}

const MAX_CHIPS = 3;

/* ------------------------------------------------------------------------------------------------ sayı */

function NumberField({
  param,
  value,
  spec,
  disabled,
  allowOutsideRange,
  onChange,
  baseValue,
  labelId,
  setCheck,
  aside,
}: {
  param: ParamMeta;
  value: number | null;
  spec: NumberSpec;
  disabled?: boolean;
  allowOutsideRange: boolean;
  onChange(v: number): void;
  baseValue: number | null;
  labelId: string;
  setCheck(c: ValueCheck & { pending?: number }): void;
  /** sayı kutusunun yanında: birim ve durum rozetleri */
  aside?: ReactNode;
}) {
  const [draft, setDraft] = useState<number | ''>(value === null ? '' : toDisplay(spec, value));
  const draftRef = useRef(draft);
  draftRef.current = draft;
  const editing = useRef(false);
  const dragging = useRef(false);
  // sürükleme sırasında bileşen kalkarsa otomatik çözümü serbest bırak
  useEffect(
    () => () => {
      if (dragging.current) {
        dragging.current = false;
        releaseAutoSolve();
      }
    },
    [],
  );

  // dışarıdan gelen değişiklik (sıfırlama, senaryo, alternatif çip) taslağı günceller — yazarken değil
  useEffect(() => {
    if (!editing.current) {
      setDraft(value === null ? '' : toDisplay(spec, value));
      setCheck({});
    }
  }, [value, spec, setCheck]);

  const commit = (d: number | '') => {
    if (d === '' || !Number.isFinite(d)) {
      setCheck({ error: 'Bir değer girin.' });
      return;
    }
    const raw = fromDisplay(spec, d);
    const c = checkValue(param, raw, spec);
    if (c.error) {
      setCheck(c);
      return;
    }
    if (c.outside && !allowOutsideRange) {
      setCheck({ outside: true, pending: raw });
      return;
    }
    setCheck(c);
    if (value !== null && sameNumber(raw, value)) return;
    onChange(raw);
  };
  const debounced = useDebouncedCallback((d: number | '') => commit(d), 700);

  const sliderVal = typeof draft === 'number' && spec.slider ? Math.min(spec.slider[1], Math.max(spec.slider[0], draft)) : spec.slider?.[0] ?? 0;
  const baseDisplay = baseValue === null ? null : toDisplay(spec, baseValue);
  const pct = spec.displayUnit?.startsWith('%');
  // gösterim birimi (ör. "milyon TL") kutunun içinde, sağda: değer ve birimi birlikte okunur
  const suffix = spec.displayUnit && !pct ? spec.displayUnit : null;

  const input = (
    <NumberInput
      className="pp-input"
      size="sm"
      value={draft}
      disabled={disabled}
      hideControls
      decimalSeparator=","
      thousandSeparator="."
      allowedDecimalSeparators={[',']}
      decimalScale={Math.max(spec.decimals, spec.integer ? 0 : 4)}
      allowDecimal={!spec.integer}
      // eksi işareti kabul edilir ki "-5" sessizce "5" olmasın; tanım kümesi denetimi hatayı yazar
      allowNegative
      step={spec.step}
      clampBehavior="none"
      prefix={pct ? '%' : undefined}
      rightSection={
        suffix ? (
          <Text size="xs" c="dimmed" pr={8} style={{ whiteSpace: 'nowrap' }}>
            {suffix}
          </Text>
        ) : null
      }
      rightSectionWidth="auto"
      rightSectionPointerEvents="none"
      aria-label={`${param.label}${spec.displayUnit ? ` (${spec.displayUnit})` : ` (${param.unit})`}`}
      onFocus={() => {
        editing.current = true;
      }}
      onBlur={() => {
        editing.current = false;
        debounced.cancel();
        commit(draftRef.current);
      }}
      onKeyDown={(e) => {
        if (e.key === 'Enter') {
          debounced.cancel();
          commit(draftRef.current);
        }
        if (e.key === 'Escape' && value !== null) {
          debounced.cancel();
          setDraft(toDisplay(spec, value));
          setCheck({});
        }
      }}
      onChange={(v) => {
        const n = typeof v === 'number' ? v : v === '' || v === '-' ? '' : Number(String(v).replace(/\./g, '').replace(',', '.'));
        const d = n === '' || Number.isFinite(n) ? n : '';
        setDraft(d);
        draftRef.current = d;
        debounced(d);
      }}
    />
  );

  return (
    <>
      {spec.slider && (
        <div className="pp-row pp-dim pp-slider-row">
          <Slider
            min={spec.slider[0]}
            max={spec.slider[1]}
            step={spec.step}
            value={sliderVal}
            disabled={disabled}
            size="md"
            thumbSize={18}
            label={(v) => fmtDisplay(spec, fromDisplay(spec, v))}
            marks={baseDisplay !== null && baseDisplay >= spec.slider[0] && baseDisplay <= spec.slider[1] ? [{ value: baseDisplay }] : []}
            onChange={(v) => {
              editing.current = true;
              if (!dragging.current) {
                dragging.current = true;
                holdAutoSolve();
              }
              setDraft(v);
            }}
            onChangeEnd={(v) => {
              editing.current = false;
              commit(v);
              if (dragging.current) {
                dragging.current = false;
                releaseAutoSolve();
              }
            }}
            thumbLabel={`${param.label} kaydırıcısı`}
            thumbValueText={(v: number) => fmtDisplay(spec, fromDisplay(spec, v))}
            aria-labelledby={labelId}
          />
        </div>
      )}
      <div className="pp-input-row">
        <div className="pp-dim pp-input-wrap" data-suffix={suffix ? true : undefined}>
          {input}
        </div>
        {aside}
      </div>
      <Text size="xs" c="dimmed" className="pp-unit pp-dim" lineClamp={2} title={param.unit} mt={3}>
        {suffix ? (
          <>
            birim: <b>{suffix}</b> · {param.unit}
          </>
        ) : pct ? (
          <>
            birim: <b>yüzde</b> · {param.unit}
          </>
        ) : (
          <>birim: {param.unit}</>
        )}
      </Text>
    </>
  );
}

/* ------------------------------------------------------------------------------------------------ ana denetim */

export function ParamControl(props: ParamControlProps) {
  const {
    param: p,
    value,
    changed,
    allowOutsideRange,
    disabled,
    onChange,
    onReset,
    onAllowOutsideRange,
    scenarioValue,
    scenarioTitle,
    relevance,
    serverError,
    readOnly,
    readOnlyHint,
  } = props;
  const labelId = useId();
  const [check, setCheckState] = useState<ValueCheck & { pending?: number }>({});
  const setCheck = useMemo(() => (c: ValueCheck & { pending?: number }) => setCheckState(c), []);
  const [showAllChips, setShowAllChips] = useState(false);
  const kind = p.allowed.kind;
  const isNumber = (kind === 'number' || kind === 'integer') && !Array.isArray(p.value);
  const spec = useMemo(() => (isNumber ? numberSpec(p) : null), [p, isNumber]);
  const inactive = relevance ? !relevance.active : false;
  const base = scenarioValue !== undefined ? scenarioValue : p.value;

  const commitRaw = (raw: number) => {
    if (!spec) return;
    const c = checkValue(p, raw, spec);
    if (c.error) {
      setCheck(c);
      return;
    }
    if (c.outside && !allowOutsideRange) {
      setCheck({ outside: true, pending: raw });
      return;
    }
    setCheck(c);
    onChange(raw);
  };

  /* ---------------- birim + durum (sayı alanında kutunun yanında, diğerlerinde ayrı satır) */
  const unitLine = spec?.displayUnit && !spec.displayUnit.startsWith('%') ? (
    <>
      <b>{spec.displayUnit}</b> · {p.unit}
    </>
  ) : spec?.displayUnit ? (
    <>
      <b>yüzde</b> · {p.unit}
    </>
  ) : (
    p.unit
  );
  // sayı alanında birim kutunun içinde ve altında (NumberField); burada yalnız rozetler
  const metaRow = (
    <div className="pp-meta">
      {!spec && kind !== 'bool' && kind !== 'choice' && (
        <Text size="xs" c="dimmed" className="pp-unit" lineClamp={2} title={p.unit}>
          {unitLine}
        </Text>
      )}
      <span className="pp-badges">
        {p.structure && (
          <Tooltip label="Yapı parametresi: değişince talep/aday kümeleri ve mesafe listeleri yeniden kurulur; çözüm daha uzun sürer.">
            <Badge size="xs" variant="outline" color="gray" tt="none">
              yapı
            </Badge>
          </Tooltip>
        )}
        <ParamStatusBadge status={p.status} size="xs" />
      </span>
    </div>
  );

  /* ---------------- gövde */
  let body: ReactNode = null;
  if (readOnly) {
    body = null;
  } else if (spec) {
    body = (
      <NumberField
        param={p}
        value={typeof value === 'number' ? value : null}
        spec={spec}
        disabled={disabled}
        allowOutsideRange={allowOutsideRange}
        onChange={onChange}
        baseValue={typeof base === 'number' ? base : null}
        labelId={labelId}
        setCheck={setCheck}
        aside={metaRow}
      />
    );
  } else if (kind === 'bool') {
    body = (
      <div className="pp-row pp-dim">
        <Switch
          checked={value === true}
          disabled={disabled}
          onChange={(e) => onChange(e.currentTarget.checked)}
          label={value === true ? 'evet' : 'hayır'}
          size="md"
          aria-labelledby={labelId}
        />
      </div>
    );
  } else if (kind === 'choice') {
    const opts = choiceOptions(p);
    const data = opts.map((o) => ({ value: String(o), label: choiceLabel(p, o) }));
    const toValue = (s: string) => opts.find((o) => String(o) === s) ?? s;
    body = (
      <div className="pp-row pp-row-full pp-dim">
        {opts.length <= 3 ? (
          <SegmentedControl
            fullWidth
            size="xs"
            data={data}
            value={String(value)}
            disabled={disabled}
            onChange={(s) => onChange(toValue(s))}
            aria-labelledby={labelId}
            style={{ flex: 1 }}
          />
        ) : (
          <Select data={data} value={String(value)} disabled={disabled} onChange={(s) => s && onChange(toValue(s))} allowDeselect={false} aria-labelledby={labelId} style={{ flex: 1 }} />
        )}
      </div>
    );
  } else if (kind === 'profile' && Array.isArray(value)) {
    body = (
      <Table className="pp-dim" withRowBorders={false} verticalSpacing={1} horizontalSpacing={6} fz="xs" mt={4} aria-labelledby={labelId}>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>kuş uçuşu (km)</Table.Th>
            <Table.Th>katsayı k</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {(value as number[][]).map((row, i) => (
            <Table.Tr key={i}>
              <Table.Td style={{ fontVariantNumeric: 'tabular-nums' }}>{fmtNumber(row[0], 2)}</Table.Td>
              <Table.Td style={{ fontVariantNumeric: 'tabular-nums' }}>{fmtNumber(row[1], 4)}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    );
  }

  /* ---------------- alternatif çipleri (yalnız sayılar) */
  const alts = spec && !readOnly ? numericAlternatives(p) : [];
  const chips = alts.length
    ? [{ label: 'Kayıttaki varsayılan', value: p.value as number, status: p.status, isDefault: true }, ...alts.map((a) => ({ ...a, isDefault: false }))]
        .filter((c, i, arr) => arr.findIndex((d) => sameNumber(d.value, c.value)) === i)
    : [];
  const visibleChips = showAllChips ? chips : chips.slice(0, MAX_CHIPS + 1);

  /* ---------------- değer metni (salt okunur) */
  const readOnlyValue = spec && typeof value === 'number' ? fmtDisplay(spec, value) : kind === 'choice' || kind === 'bool' ? choiceLabel(p, value) : fmtParamValue(value, p.unit);

  const errorText = check.error ?? serverError ?? null;

  return (
    <div
      className="pp-param"
      data-param-key={p.key}
      data-changed={changed || undefined}
      data-inactive={inactive || undefined}
      data-error={errorText ? true : undefined}
      role="group"
      aria-labelledby={labelId}
    >
      <div className="pp-head">
        <Text id={labelId} size="sm" fw={changed ? 700 : 500} className="pp-label">
          {p.label}
          {changed && <VisuallyHidden> (değiştirildi)</VisuallyHidden>}
        </Text>
        <div className="pp-tools">
          <InfoPopover label={`${p.label}: anlamı, kaynağı ve gerekçesi`} size="sm" testId={`param-info-${p.key}`}>
            <ParamInfo param={p} value={value} scenarioValue={scenarioValue} scenarioTitle={scenarioTitle} relevance={relevance} />
          </InfoPopover>
          {!readOnly && (
            <Tooltip label={scenarioValue !== undefined ? `Senaryo değerine dön (${scenarioTitle})` : 'Varsayılana dön'}>
              <ActionIcon
                variant="subtle"
                color="gray"
                size="sm"
                aria-label={`${p.label}: varsayılana dön`}
                onClick={onReset}
                disabled={!changed || disabled}
                style={{ visibility: changed ? 'visible' : 'hidden' }}
              >
                <IconReset size={15} />
              </ActionIcon>
            </Tooltip>
          )}
        </div>
      </div>

      {readOnly ? (
        <Group gap={6} mt={2} wrap="wrap" className="pp-dim">
          <ParamStatusBadge status={p.status} size="xs" />
          <Text size="sm" fw={650} style={{ fontVariantNumeric: 'tabular-nums' }}>
            {readOnlyValue}
          </Text>
          {!spec?.displayUnit && kind !== 'choice' && kind !== 'bool' && kind !== 'profile' && (
            <Text size="xs" c="dimmed">
              {p.unit}
            </Text>
          )}
          {readOnlyHint && (
            <Text size="xs" c="dimmed">
              {readOnlyHint}
            </Text>
          )}
        </Group>
      ) : (
        body
      )}

      {!readOnly && !spec && metaRow}

      {relevance && (
        <Text
          size="xs"
          mt={2}
          style={{
            lineHeight: 1.35,
            color: readOnly || relevance.active ? 'var(--mantine-color-dimmed)' : 'var(--mantine-color-orange-text)',
            fontStyle: readOnly ? 'italic' : undefined,
          }}
        >
          {relevance.note}
        </Text>
      )}

      {errorText && (
        <Text size="xs" c="red" mt={2} role="alert">
          {errorText}
        </Text>
      )}

      {check.outside && !check.error && (
        <Group gap={6} mt={4} wrap="wrap">
          <Text size="xs" style={{ lineHeight: 1.35, color: 'var(--mantine-color-orange-text)' }}>
            {allowOutsideRange
              ? 'Kaydırıcı aralığının dışında: sunucu uyarıyla kabul eder.'
              : `Kaydırıcı aralığının (${spec && p.range ? `${fmtDisplay(spec, p.range[0])} – ${fmtDisplay(spec, p.range[1])}` : '—'}) dışında.`}
          </Text>
          {!allowOutsideRange && onAllowOutsideRange && check.pending !== undefined && (
            <Button
              size="compact-xs"
              variant="light"
              color="orange"
              onClick={() => {
                const raw = check.pending!;
                onAllowOutsideRange();
                setCheck({ outside: true });
                onChange(raw);
              }}
            >
              İzin ver ve uygula
            </Button>
          )}
        </Group>
      )}

      {chips.length > 0 && (
        <div className="pp-chips pp-dim" aria-label={`${p.label}: hazır değerler`}>
          {visibleChips.map((c, i) => {
            const active = sameNumber(c.value, value);
            return (
              <Tooltip
                key={i}
                label={
                  <span>
                    {c.label}
                    {c.status ? ` · ${PARAM_STATUS_UI[c.status]?.label ?? c.status}` : ''}
                  </span>
                }
              >
                <Button
                  size="compact-xs"
                  radius="xl"
                  variant={active ? 'light' : 'default'}
                  aria-pressed={active}
                  disabled={disabled}
                  onClick={() => commitRaw(c.value)}
                  aria-label={`${c.label}: ${fmtDisplay(spec!, c.value)}`}
                >
                  {fmtDisplay(spec!, c.value)}
                  <Text span size="xs" c="dimmed" ml={4}>
                    {c.isDefault ? 'varsayılan' : shortAltLabel(c.label)}
                  </Text>
                </Button>
              </Tooltip>
            );
          })}
          {chips.length > MAX_CHIPS + 1 && (
            <Button size="compact-xs" variant="subtle" radius="xl" onClick={() => setShowAllChips((v) => !v)} aria-expanded={showAllChips}>
              {showAllChips ? 'daha az' : `+${chips.length - MAX_CHIPS - 1} değer`}
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
