import {
  buildReportViewSearch as buildLegacyReportViewSearch,
  parseReportView as parseLegacyReportView,
} from './interaction.ts'
import {
  analyseEditorialPattern,
  dateOrdinal,
  isoDate,
  type EditorialPattern,
} from './pattern.ts'
import {
  degradedSourceNotices,
  indexForPreset,
  modelSeriesPoint,
  num,
  selectModelSeries,
  summarizeRange,
  TOOLS,
} from './usage.ts'
import type {
  DailyRow,
  ModelSeriesSpec,
  SourceStatus,
  ToolId,
  UsagePayload,
} from './usage.ts'

export type ReportPreset = '7' | '30' | '90' | 'all' | 'custom'

export type ReportView = {
  preset: ReportPreset
  from: string | null
  to: string | null
  anchor: string | null
  tool: ToolId | null
  model: string | null
  day: string | null
}

export type CanonicalReportView = ReportView

export type TokenPartId =
  | 'input'
  | 'cache-read'
  | 'cache-write'
  | 'output'
  | 'reasoning'
  | 'unclassified'

export type TokenPart = {
  id: TokenPartId
  label: string
  tokens: number
}

export type TokenPartReport = {
  scope: 'all' | 'tool'
  tool: ToolId | null
  range: TokenPart[]
  date: TokenPart[] | null
  note: string
}

export type ReportSeries = {
  id: string
  label: string
  color: string
}

export type ReportBreakdown = {
  id: string
  label: string
  tokens: number | null
  cost: number | null
}

export type ReportDay = {
  date: string
  published: boolean
  tokens: number | null
  cost: number | null
  values: Array<number | null>
  breakdown: ReportBreakdown[]
  modelCoverage: 'not-applicable' | 'complete' | 'unavailable'
}

export type ReportRange = {
  preset: ReportPreset
  start: string | null
  end: string | null
  calendarDays: number
  recordedDays: number
  canShiftEarlier: boolean
  canShiftLater: boolean
}

export type ReportScope = {
  kind: 'all' | 'tool' | 'model'
  label: string
  tool: ToolId | null
  model: string | null
  tokens: number | null
  cost: number | null
  cache: number | null
  cacheShare: number | null
  availableDays: number
  activeDays: number
}

export type ToolReport = {
  id: ToolId
  label: string
  color: string
  coverage: string
  tokens: number | null
  cost: number | null
  /** Ratio from 0 to 1 within the selected range, or null without published rows. */
  share: number | null
  availableDays: number
  parts: TokenPart[]
  modelCount: number
}

export type ModelReportEntry = {
  name: string
  tokens: number
  cost: number
  /** Ratio from 0 to 1 of the selected tool's range tokens. */
  share: number
  kind: 'model' | 'legacy'
  selectable: boolean
  selected: boolean
}

export type OtherModelReport = {
  count: number
  tokens: number
  cost: number
  names: string[]
}

export type ModelReport = {
  selected: string | null
  modelCount: number
  hasLegacy: boolean
  top: ModelReportEntry[]
  full: ModelReportEntry[]
  legacy: ModelReportEntry | null
  other: OtherModelReport | null
  groups: ModelSeriesSpec[]
}

export type ReportBookmarks = {
  first: string | null
  record: string | null
  last: string | null
}

export type ReportNotes = {
  tokenBreakdown: string
  cost: string
  merge: string
}

export type ReportMeta = {
  generatedAt: string | null
  timezone: string | null
  machines: string[]
  fullSpan: string | null
  schemaVersion: number | string | null
  pricingVersion: number | string | null
}

export type ReportSource = {
  id: string
  label: string
  status: SourceStatus['status']
  retained: boolean
  message: string
  lastSuccessAt: string | null
  attemptedAt: string | null
  windowEnd: string | null
  lagDays: number | null
  error: string | null
}

export type ReportSources = {
  all: ReportSource[]
  degraded: ReportSource[]
}

export type ReportModel = {
  meta: ReportMeta
  view: CanonicalReportView
  range: ReportRange
  scope: ReportScope
  daily: ReportDay[]
  series: ReportSeries[]
  tools: ToolReport[]
  models: ModelReport | null
  parts: TokenPartReport
  pattern: EditorialPattern | null
  bookmarks: ReportBookmarks
  selectedDay: ReportDay | null
  sources: ReportSources
  notes: ReportNotes
}

type ResolvedPeriod = {
  view: CanonicalReportView
  range: ReportRange
  rows: DailyRow[]
}

const DAY_MS = 86_400_000
const SERIES_COLORS = ['#315f71', '#a04e31', '#477065', '#725879', '#ad7d2c']
const UNCLASSIFIED_COLOR = '#8a8276'

export const DEFAULT_REPORT_VIEW: ReportView = {
  preset: 'all',
  from: null,
  to: null,
  anchor: null,
  tool: null,
  model: null,
  day: null,
}

function normalizedRows(payload: UsagePayload): DailyRow[] {
  return payload.daily
    .filter((row) => dateOrdinal(row.date) !== null)
    .slice()
    .sort((left, right) => left.date.localeCompare(right.date))
}

function isPreset(value: unknown): value is ReportPreset {
  return value === '7' || value === '30' || value === '90' || value === 'all' || value === 'custom'
}

function boundedOrdinal(value: string | null, first: number, last: number): number | null {
  if (!value) return null
  const ordinal = dateOrdinal(value)
  if (ordinal === null) return null
  return Math.max(first, Math.min(last, ordinal))
}

function datesBetween(start: string | null, end: string | null): string[] {
  if (!start || !end) return []
  const first = dateOrdinal(start)
  const last = dateOrdinal(end)
  if (first === null || last === null || last < first) return []
  const dates: string[] = []
  for (let ordinal = first; ordinal <= last; ordinal += DAY_MS) {
    dates.push(isoDate(ordinal))
  }
  return dates
}

function emptyPeriod(view: ReportView): ResolvedPeriod {
  return {
    view: {
      ...DEFAULT_REPORT_VIEW,
      ...view,
      preset: 'all',
      from: null,
      to: null,
      anchor: null,
      day: null,
    },
    range: {
      preset: 'all',
      start: null,
      end: null,
      calendarDays: 0,
      recordedDays: 0,
      canShiftEarlier: false,
      canShiftLater: false,
    },
    rows: [],
  }
}

function resolvedAll(rows: DailyRow[], view: ReportView): ResolvedPeriod {
  if (!rows.length) return emptyPeriod(view)
  const start = rows[0].date
  const end = rows.at(-1)?.date ?? start
  return {
    view: {
      ...view,
      preset: 'all',
      from: start,
      to: end,
      anchor: end,
    },
    range: {
      preset: 'all',
      start,
      end,
      calendarDays: datesBetween(start, end).length,
      recordedDays: rows.length,
      canShiftEarlier: false,
      canShiftLater: false,
    },
    rows,
  }
}

function resolvePeriod(payload: UsagePayload, requested: ReportView): ResolvedPeriod {
  const rows = normalizedRows(payload)
  if (!rows.length) return emptyPeriod(requested)

  const fullStart = rows[0].date
  const fullEnd = rows.at(-1)?.date ?? fullStart
  const fullStartOrdinal = dateOrdinal(fullStart) as number
  const fullEndOrdinal = dateOrdinal(fullEnd) as number
  const preset = isPreset(requested.preset) ? requested.preset : 'all'

  if (preset === 'all') return resolvedAll(rows, requested)

  if (preset === 'custom') {
    const rawStart = dateOrdinal(requested.from ?? '')
    const rawEnd = dateOrdinal(requested.to ?? '')
    if (rawStart === null || rawEnd === null) return resolvedAll(rows, requested)
    const boundedStart = Math.max(
      fullStartOrdinal,
      Math.min(fullEndOrdinal, rawStart),
    )
    const boundedEnd = Math.max(
      fullStartOrdinal,
      Math.min(fullEndOrdinal, rawEnd),
    )
    const lower = Math.min(boundedStart, boundedEnd)
    const upper = Math.max(boundedStart, boundedEnd)
    const candidateRows = rows.filter((row) => {
      const ordinal = dateOrdinal(row.date) as number
      return ordinal >= lower && ordinal <= upper
    })
    const start = isoDate(lower)
    const end = isoDate(upper)
    return {
      view: {
        ...requested,
        preset: 'custom',
        from: start,
        to: end,
        anchor: end,
      },
      range: {
        preset: 'custom',
        start,
        end,
        calendarDays: datesBetween(start, end).length,
        recordedDays: candidateRows.length,
        canShiftEarlier: start > fullStart,
        canShiftLater: end < fullEnd,
      },
      rows: candidateRows,
    }
  }

  const length = Number(preset)
  const requestedAnchor = boundedOrdinal(
    requested.anchor,
    fullStartOrdinal,
    fullEndOrdinal,
  )
  const endOrdinal = requestedAnchor ?? fullEndOrdinal
  const startOrdinal = Math.max(fullStartOrdinal, endOrdinal - (length - 1) * DAY_MS)
  const start = isoDate(startOrdinal)
  const end = isoDate(endOrdinal)
  const candidateRows =
    end === fullEnd
      ? (() => {
          const [firstIndex, lastIndex] = indexForPreset(rows, preset)
          return rows.slice(firstIndex, lastIndex + 1)
        })()
      : rows.filter((row) => row.date >= start && row.date <= end)
  const shifted = end !== fullEnd
  const canonicalPreset: ReportPreset = shifted ? 'custom' : preset
  return {
    view: {
      ...requested,
      preset: canonicalPreset,
      from: start,
      to: end,
      anchor: end,
    },
    range: {
      preset: canonicalPreset,
      start,
      end,
      calendarDays: datesBetween(start, end).length,
      recordedDays: candidateRows.length,
      canShiftEarlier: start > fullStart,
      canShiftLater: end < fullEnd,
    },
    rows: candidateRows,
  }
}

export function parseReportView(search: string): ReportView {
  const parsed = parseLegacyReportView(search)
  return {
    preset: parsed.from && parsed.to ? 'custom' : parsed.preset ?? 'all',
    from: parsed.from,
    to: parsed.to,
    anchor: parsed.to,
    tool: parsed.tool,
    model: parsed.model,
    day: parsed.tool && parsed.model ? parsed.day : null,
  }
}

export function buildReportSearch(
  currentSearch: string,
  view: CanonicalReportView,
): string {
  return buildLegacyReportViewSearch(currentSearch, {
    tool: view.tool,
    model: view.tool ? view.model : null,
    preset:
      view.preset === 'custom'
        ? null
        : view.preset === 'all'
          ? 'all'
          : view.preset,
    from: view.preset === 'custom' ? view.from : null,
    to: view.preset === 'custom' ? view.to : null,
    day: view.tool && view.model ? view.day : null,
  })
}

export function periodDates(payload: UsagePayload, view: ReportView): ReportRange {
  return resolvePeriod(payload, view).range
}

export function shiftReportView(
  payload: UsagePayload,
  view: ReportView,
  direction: -1 | 1,
): ReportView {
  const rows = normalizedRows(payload)
  const period = resolvePeriod(payload, view)
  if (
    !rows.length ||
    period.range.preset === 'all' ||
    !period.range.start ||
    !period.range.end
  ) {
    return period.view
  }
  const firstOrdinal = dateOrdinal(rows[0].date) as number
  const lastOrdinal = dateOrdinal(rows.at(-1)?.date ?? rows[0].date) as number
  const startOrdinal = dateOrdinal(period.range.start) as number
  const endOrdinal = dateOrdinal(period.range.end) as number
  const span = endOrdinal - startOrdinal
  let nextStart = startOrdinal + direction * DAY_MS
  let nextEnd = endOrdinal + direction * DAY_MS
  if (nextStart < firstOrdinal) {
    nextStart = firstOrdinal
    nextEnd = Math.min(lastOrdinal, nextStart + span)
  }
  if (nextEnd > lastOrdinal) {
    nextEnd = lastOrdinal
    nextStart = Math.max(firstOrdinal, nextEnd - span)
  }
  return {
    ...period.view,
    preset: 'custom',
    from: isoDate(nextStart),
    to: isoDate(nextEnd),
    anchor: isoDate(nextEnd),
  }
}

function partId(label: string): Exclude<TokenPartId, 'unclassified'> | null {
  if (label === 'Input') return 'input'
  if (label === 'Cache read') return 'cache-read'
  if (label === 'Cache write' || label === 'Cache create') return 'cache-write'
  if (label === 'Output' || label === 'Output (non-reasoning)') return 'output'
  if (label === 'Reasoning (of output)') return 'reasoning'
  return null
}

function tokenParts(rows: DailyRow[], toolId: ToolId | null): TokenPart[] {
  const summary = summarizeRange(rows)
  const totals = new Map<Exclude<TokenPartId, 'unclassified'>, number>([
    ['input', 0],
    ['cache-read', 0],
    ['cache-write', 0],
    ['output', 0],
    ['reasoning', 0],
  ])
  const tools = toolId
    ? summary.byTool.filter((tool) => tool.id === toolId)
    : summary.byTool
  let tokenTotal = 0
  for (const tool of tools) {
    tokenTotal += tool.tokens
    for (const part of tool.parts) {
      const id = partId(part.label)
      if (id) totals.set(id, (totals.get(id) ?? 0) + part.value)
    }
  }
  const known = [...totals.values()].reduce((sum, value) => sum + value, 0)
  const labels: Record<TokenPartId, string> = {
    input: 'Input',
    'cache-read': 'Cache read',
    'cache-write': 'Cache write / create',
    output: 'Output (non-reasoning)',
    reasoning: 'Reasoning (of output)',
    unclassified: 'Unclassified',
  }
  const result: TokenPart[] = [...totals.entries()].map(([id, tokens]) => ({
    id,
    label: labels[id],
    tokens,
  }))
  result.push({
    id: 'unclassified',
    label: labels.unclassified,
    tokens: Math.max(0, tokenTotal - known),
  })
  return result
}

function sourceLabel(id: string): string {
  const normalized = id.toLowerCase().replace(/[\s_-]+/g, '')
  return (
    TOOLS.find((tool) => tool.id.replace(/[\s_-]+/g, '') === normalized)?.label ??
    (normalized === 'claudecode' ? 'Claude Code' : 'Data source')
  )
}

function reportSources(payload: UsagePayload): ReportSources {
  const degraded = new Map(
    degradedSourceNotices(payload.source_status).map((notice) => [notice.id, notice]),
  )
  const all = Object.entries(payload.source_status ?? {}).map(([id, source]) => {
    const notice = degraded.get(id)
    const lag = Number(source.lag_days)
    return {
      id,
      label: notice?.label ?? sourceLabel(id),
      status: source.status,
      retained: notice?.retained ?? id.toLowerCase().replace(/[\s_-]+/g, '') === 'oneapi',
      message: notice?.message ?? `${sourceLabel(id)} is fresh.`,
      lastSuccessAt: source.last_success_at ?? null,
      attemptedAt: source.attempted_at ?? null,
      windowEnd: source.window_end ?? null,
      lagDays: Number.isFinite(lag) ? lag : null,
      error: source.error ?? null,
    } satisfies ReportSource
  })
  const order = new Map(TOOLS.map((tool, index) => [tool.id, index]))
  all.sort(
    (left, right) =>
      (order.get(left.id as ToolId) ?? 99) -
        (order.get(right.id as ToolId) ?? 99) ||
      left.id.localeCompare(right.id),
  )
  return {
    all,
    degraded: all.filter((source) => source.status !== 'fresh'),
  }
}

function scopeCache(rows: DailyRow[], toolId: ToolId | null): number {
  const tools = toolId ? TOOLS.filter((tool) => tool.id === toolId) : TOOLS
  return rows.reduce(
    (sum, row) =>
      sum +
      tools.reduce(
        (toolSum, tool) =>
          toolSum +
          tool.cacheKeys.reduce((cache, key) => cache + num(row, key), 0),
        0,
      ),
    0,
  )
}

function individualModels(row: DailyRow, toolId: ToolId): ReportBreakdown[] | null {
  const tool = TOOLS.find((candidate) => candidate.id === toolId)
  if (!tool) return []
  const models = row[tool.modelKey]
  if (!Array.isArray(models)) return null
  const totals = new Map<string, { tokens: number; cost: number }>()
  for (const model of models) {
    const name = String(model.model ?? '').trim()
    if (!name) continue
    const current = totals.get(name) ?? { tokens: 0, cost: 0 }
    current.tokens += Number(model.tokens) || 0
    current.cost += Number(model.cost) || 0
    totals.set(name, current)
  }
  return [...totals.entries()]
    .map(([name, value]) => ({ id: `model:${name}`, label: name, ...value }))
    .sort(
      (left, right) => right.tokens - left.tokens || left.label.localeCompare(right.label),
    )
}

function modelReport(
  allRows: DailyRow[],
  rows: DailyRow[],
  toolId: ToolId,
  selectedModel: string | null,
): ModelReport {
  const universe = selectModelSeries(allRows, toolId).fullModels
  const rangeSelection = selectModelSeries(rows, toolId, selectedModel)
  const rangeValues = new Map(
    rangeSelection.fullModels.map((model) => [model.model, model]),
  )
  const toolTotal = summarizeRange(rows).byTool.find((tool) => tool.id === toolId)?.tokens ?? 0
  const full = universe
    .map((model) => {
      const value = rangeValues.get(model.model)
      const tokens = value?.tokens ?? 0
      const cost = value?.cost ?? 0
      const legacy = model.model === 'Legacy unknown'
      return {
        name: model.model,
        tokens,
        cost,
        share: toolTotal > 0 ? tokens / toolTotal : 0,
        kind: legacy ? ('legacy' as const) : ('model' as const),
        selectable: !legacy,
        selected: !legacy && model.model === selectedModel,
      }
    })
    .sort((left, right) => right.tokens - left.tokens || left.name.localeCompare(right.name))
  const identified = full.filter((model) => model.kind === 'model')
  const byName = new Map(identified.map((model) => [model.name, model]))
  const top = rangeSelection.series
    .filter(
      (group) => group.kind === 'model' && group.models.length === 1,
    )
    .slice(0, 4)
    .flatMap((group) => {
      const entry = byName.get(group.models[0])
      return entry ? [entry] : []
    })
  const otherGroup = rangeSelection.series.find((group) => group.kind === 'other')
  return {
    selected: selectedModel,
    modelCount: identified.length,
    hasLegacy: full.some((model) => model.kind === 'legacy'),
    top,
    full,
    legacy: full.find((model) => model.kind === 'legacy') ?? null,
    other: otherGroup
      ? {
          count: otherGroup.models.length,
          tokens: otherGroup.tokens,
          cost: otherGroup.cost,
          names: otherGroup.models,
        }
      : null,
    groups: rangeSelection.series,
  }
}

function hasUnclassifiedModelTokens(rows: DailyRow[], toolId: ToolId): boolean {
  const tool = TOOLS.find((candidate) => candidate.id === toolId)
  if (!tool) return false
  return rows.some((row) => {
    const toolTokens = num(row, tool.tokenKey)
    const models = row[tool.modelKey]
    if (!Array.isArray(models)) return toolTokens > 0
    const modelTokens = models.reduce(
      (sum, model) => sum + (Number(model.tokens) || 0),
      0,
    )
    return toolTokens > modelTokens
  })
}

function reportSeries(
  rows: DailyRow[],
  toolId: ToolId | null,
  selectedModel: string | null,
  models: ModelReport | null,
): ReportSeries[] {
  if (!toolId) {
    return TOOLS.map((tool, index) => ({
      id: `tool:${tool.id}`,
      label: tool.label,
      color: SERIES_COLORS[index],
    }))
  }
  const tool = TOOLS.find((candidate) => candidate.id === toolId)
  if (!tool) return []
  if (selectedModel) {
    return [
      {
        id: `model:${selectedModel}`,
        label: selectedModel,
        color: SERIES_COLORS[0],
      },
    ]
  }
  const groups = models?.groups ?? []
  const result = groups.map((group, index) => ({
    id: group.id,
    label: group.kind === 'legacy' ? 'Unattributed (legacy)' : group.label,
    color: SERIES_COLORS[index % SERIES_COLORS.length],
  }))
  if (hasUnclassifiedModelTokens(rows, toolId)) {
    result.push({
      id: 'model:unclassified',
      label: 'Unclassified / attribution unavailable',
      color: UNCLASSIFIED_COLOR,
    })
  }
  return result
}

function denseDaily(
  period: ResolvedPeriod,
  toolId: ToolId | null,
  selectedModel: string | null,
  models: ModelReport | null,
  series: ReportSeries[],
): ReportDay[] {
  const rowsByDate = new Map(period.rows.map((row) => [row.date, row]))
  const tool = toolId ? TOOLS.find((candidate) => candidate.id === toolId) : undefined
  const groups = models?.groups ?? []
  return datesBetween(period.range.start, period.range.end).map((date) => {
    const row = rowsByDate.get(date)
    if (!row) {
      return {
        date,
        published: false,
        tokens: null,
        cost: null,
        values: series.map(() => null),
        breakdown: series.map((item) => ({
          id: item.id,
          label: item.label,
          tokens: null,
          cost: null,
        })),
        modelCoverage: toolId ? 'unavailable' : 'not-applicable',
      }
    }

    if (!toolId || !tool) {
      return {
        date,
        published: true,
        tokens: TOOLS.reduce((sum, item) => sum + num(row, item.tokenKey), 0),
        cost: TOOLS.reduce((sum, item) => sum + num(row, item.costKey), 0),
        values: TOOLS.map((item) => num(row, item.tokenKey)),
        breakdown: TOOLS.map((item) => ({
          id: `tool:${item.id}`,
          label: item.label,
          tokens: num(row, item.tokenKey),
          cost: num(row, item.costKey),
        })),
        modelCoverage: 'not-applicable',
      }
    }

    const rawModels = row[tool.modelKey]
    const modelCoverage = Array.isArray(rawModels) ? 'complete' : 'unavailable'
    if (selectedModel) {
      if (!Array.isArray(rawModels)) {
        return {
          date,
          published: true,
          tokens: null,
          cost: null,
          values: [null],
          breakdown: [
            {
              id: `model:${selectedModel}`,
              label: selectedModel,
              tokens: null,
              cost: null,
            },
          ],
          modelCoverage,
        }
      }
      const spec: ModelSeriesSpec = {
        id: `model:${selectedModel}`,
        label: selectedModel,
        kind: 'model',
        models: [selectedModel],
        tokens: 0,
        cost: 0,
      }
      const point = modelSeriesPoint(row, toolId, spec)
      return {
        date,
        published: true,
        tokens: point.tokens,
        cost: point.cost,
        values: [point.tokens],
        breakdown: [{ id: spec.id, label: spec.label, ...point }],
        modelCoverage,
      }
    }

    const toolTokens = num(row, tool.tokenKey)
    const toolCost = num(row, tool.costKey)
    const hasUnclassified = series.some((item) => item.id === 'model:unclassified')
    if (!Array.isArray(rawModels)) {
      const values: Array<number | null> = groups.map(() => null)
      if (hasUnclassified) values.push(toolTokens)
      return {
        date,
        published: true,
        tokens: toolTokens,
        cost: toolCost,
        values,
        breakdown: [
          {
            id: 'model:unclassified',
            label: 'Model attribution unavailable',
            tokens: toolTokens,
            cost: toolCost,
          },
        ],
        modelCoverage,
      }
    }

    const groupValues = groups.map((group) => modelSeriesPoint(row, toolId, group).tokens)
    const modelBreakdown = individualModels(row, toolId) ?? []
    const knownTokens = modelBreakdown.reduce((sum, item) => sum + (item.tokens ?? 0), 0)
    const knownCost = modelBreakdown.reduce((sum, item) => sum + (item.cost ?? 0), 0)
    const gapTokens = Math.max(0, toolTokens - knownTokens)
    if (hasUnclassified) groupValues.push(gapTokens)
    const rawCostGap = toolCost - knownCost
    const costGap = Math.abs(rawCostGap) <= 1e-9 ? 0 : rawCostGap
    if (gapTokens > 0 || costGap !== 0) {
      modelBreakdown.push({
        id: 'model:unclassified',
        label: 'Unclassified',
        tokens: gapTokens,
        cost: costGap,
      })
    }
    return {
      date,
      published: true,
      tokens: toolTokens,
      cost: toolCost,
      values: groupValues,
      breakdown: modelBreakdown,
      modelCoverage,
    }
  })
}

function reportTools(rows: DailyRow[]): ToolReport[] {
  const summary = summarizeRange(rows)
  const availableDays = rows.length
  return summary.byTool.map((tool, index) => ({
    id: tool.id,
    label: tool.label,
    color: SERIES_COLORS[index],
    coverage: tool.coverage,
    tokens: availableDays ? tool.tokens : null,
    cost: availableDays ? tool.cost : null,
    share:
      !availableDays ? null : summary.tokens > 0 ? tool.tokens / summary.tokens : 0,
    availableDays,
    parts: tokenParts(rows, tool.id),
    modelCount: selectModelSeries(rows, tool.id).modelCount,
  }))
}

function selectedModelName(
  rows: DailyRow[],
  toolId: ToolId | null,
  requested: string | null,
): string | null {
  if (!toolId || !requested || requested === 'Legacy unknown') return null
  return selectModelSeries(rows, toolId).fullModels.some(
    (model) => model.model === requested,
  )
    ? requested
    : null
}

function bookmarksForDays(
  daily: ReportDay[],
  pattern: EditorialPattern | null,
): ReportBookmarks {
  if (pattern) {
    return {
      first: pattern.firstDate,
      record: pattern.recordDate,
      last: pattern.lastDate,
    }
  }
  const active = daily.filter(
    (day): day is ReportDay & { tokens: number } => day.tokens !== null && day.tokens > 0,
  )
  let record: (ReportDay & { tokens: number }) | null = null
  for (const day of active) {
    if (!record || day.tokens > record.tokens) record = day
  }
  return {
    first: active[0]?.date ?? null,
    record: record?.date ?? null,
    last: active.at(-1)?.date ?? null,
  }
}

export function buildReport(payload: UsagePayload, requested: ReportView): ReportModel {
  const allRows = normalizedRows(payload)
  const period = resolvePeriod(payload, { ...DEFAULT_REPORT_VIEW, ...requested })
  const toolId = TOOLS.some((tool) => tool.id === period.view.tool)
    ? period.view.tool
    : null
  const model = selectedModelName(allRows, toolId, period.view.model)
  const models = toolId ? modelReport(allRows, period.rows, toolId, model) : null
  const series = reportSeries(period.rows, toolId, model, models)
  const daily = denseDaily(period, toolId, model, models, series)
  const summary = summarizeRange(period.rows)
  const selectedTool = toolId
    ? summary.byTool.find((tool) => tool.id === toolId)
    : undefined
  const modelTokens = daily.reduce(
    (sum, day) => sum + (day.tokens === null ? 0 : day.tokens),
    0,
  )
  const modelCost = daily.reduce(
    (sum, day) => sum + (day.cost === null ? 0 : day.cost),
    0,
  )
  const availableDays = daily.filter((day) => day.tokens !== null).length
  const activeDays = daily.filter(
    (day) => day.tokens !== null && day.tokens > 0,
  ).length
  const knownCache = scopeCache(period.rows, toolId)
  const cache = availableDays > 0 ? knownCache : null
  const cacheDenominator = toolId ? selectedTool?.tokens ?? 0 : summary.tokens
  const scope: ReportScope = {
    kind: model ? 'model' : toolId ? 'tool' : 'all',
    label:
      model ??
      TOOLS.find((tool) => tool.id === toolId)?.label ??
      'All tools',
    tool: toolId,
    model,
    tokens:
      availableDays > 0
        ? model
          ? modelTokens
          : selectedTool?.tokens ?? summary.tokens
        : null,
    cost:
      availableDays > 0
        ? model
          ? modelCost
          : selectedTool?.cost ?? summary.cost
        : null,
    cache,
    cacheShare:
      cache !== null && cacheDenominator > 0 ? cache / cacheDenominator : null,
    availableDays,
    activeDays,
  }
  const pattern = daily.length
    ? analyseEditorialPattern(
        daily.map((day) => ({ date: day.date, tokens: day.tokens, cost: day.cost })),
      )
    : null
  const requestedDay = requested.day
    ? daily.find((day) => day.date === requested.day) ?? null
    : null
  const fallbackDate = model
    ? pattern?.firstDate ?? period.range.start
    : period.range.end
  const selectedDay = requestedDay ?? daily.find((day) => day.date === fallbackDate) ?? null
  const canonicalDay = requestedDay?.date ?? null
  const partTool = toolId
  const dateRow = selectedDay?.published
    ? period.rows.find((row) => row.date === selectedDay.date) ?? null
    : null
  const parts: TokenPartReport = {
    scope: partTool ? 'tool' : 'all',
    tool: partTool,
    range: tokenParts(period.rows, partTool),
    date: dateRow ? tokenParts([dateRow], partTool) : null,
    note: model
      ? 'Token categories are published at tool level; the selected model publishes tokens and cost only.'
      : 'Reasoning is a subset of output and is removed from non-reasoning output.',
  }
  const canonicalView: CanonicalReportView = {
    ...period.view,
    tool: toolId,
    model,
    day: canonicalDay,
  }
  const notes: ReportNotes = {
    tokenBreakdown:
      payload.notes?.token_breakdown ??
      'Input, cache and output categories follow the published ledger.',
    cost:
      payload.notes?.cost ??
      'Costs are published estimates and retain their source precision.',
    merge: payload.notes?.merge ?? '',
  }
  const fullSpan =
    payload.timeline_meta?.span ??
    (allRows.length
      ? `${allRows[0].date} — ${allRows.at(-1)?.date ?? allRows[0].date}`
      : null)

  return {
    meta: {
      generatedAt: payload.generated_at ?? null,
      timezone: payload.timezone ?? null,
      machines: payload.machines?.slice() ?? [],
      fullSpan,
      schemaVersion: payload.schema_version ?? null,
      pricingVersion: payload.pricing_version ?? null,
    },
    view: canonicalView,
    range: period.range,
    scope,
    daily,
    series,
    tools: reportTools(period.rows),
    models,
    parts,
    pattern,
    bookmarks: bookmarksForDays(daily, pattern),
    selectedDay,
    sources: reportSources(payload),
    notes,
  }
}

