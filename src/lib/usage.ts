export type ModelUsage = {
  model: string
  tokens: number
  cost: number
}

export type SourceStatus = {
  status: 'fresh' | 'stale' | 'failed'
  last_success_at?: string | null
  attempted_at?: string | null
  window_end?: string | null
  lag_days?: number | null
  error?: string | null
}

export type DegradedSourceNotice = {
  id: string
  label: string
  status: 'stale' | 'failed'
  retained: boolean
  message: string
}

export type DailyRow = {
  date: string
  codex_tokens: number
  claude_tokens: number
  opencode_tokens: number
  cursor_tokens: number
  oneapi_tokens: number
  codex_cost: number
  claude_cost: number
  opencode_cost: number
  cursor_cost: number
  oneapi_cost: number
  total_tokens: number
  total_cost: number
  codex_input: number
  codex_cache_read: number
  codex_output: number
  codex_reasoning: number
  claude_input: number
  claude_cache_create: number
  claude_cache_read: number
  claude_output: number
  opencode_input: number
  opencode_cache_create: number
  opencode_cache_read: number
  opencode_output: number
  cursor_input: number
  cursor_cache_write: number
  cursor_cache_read: number
  cursor_output: number
  oneapi_input: number
  oneapi_output: number
  oneapi_cache_read: number
  oneapi_cache_write: number
  oneapi_requests?: number
  codex_models?: ModelUsage[]
  claude_models?: ModelUsage[]
  opencode_models?: ModelUsage[]
  cursor_models?: ModelUsage[]
  oneapi_models?: ModelUsage[]
}

export type UsagePayload = {
  schema_version?: number | string
  pricing_version?: number | string
  generated_at?: string | null
  timezone?: string
  machine_id?: string
  machines?: string[]
  source_status?: Record<string, SourceStatus>
  tools?: Array<{
    tool: string
    history?: string
    cost?: number
    total_tokens?: number
    models?: ModelUsage[]
  }>
  timeline_meta?: { span?: string }
  daily: DailyRow[]
  notes?: {
    token_breakdown?: string
    cost?: string
    merge?: string
  }
}

export type ToolId = 'codex' | 'claude' | 'opencode' | 'cursor' | 'oneapi'

export type ModelSeriesSpec = {
  id: string
  label: string
  kind: 'model' | 'legacy' | 'other'
  models: string[]
  tokens: number
  cost: number
}

export type ModelSeriesSelection = {
  modelCount: number
  hasLegacy: boolean
  fullModels: ModelUsage[]
  series: ModelSeriesSpec[]
}

type BreakdownSpec = {
  key: keyof DailyRow
  label: string
  subsetOf?: keyof DailyRow
  excludeSubset?: keyof DailyRow
}

export type RangeDescription = {
  start: string
  end: string
  calendarDays: number
  recordedDays: number
}

export const TOOLS: Array<{
  id: ToolId
  label: string
  color: 'blue' | 'orange' | 'teal' | 'purple' | 'green'
  hex: string
  tokenKey: keyof DailyRow
  costKey: keyof DailyRow
  modelKey: keyof DailyRow
  breakdown: BreakdownSpec[]
  cacheKeys: Array<keyof DailyRow>
}> = [
  {
    id: 'codex',
    label: 'Codex',
    color: 'blue',
    hex: '#2563eb',
    tokenKey: 'codex_tokens',
    costKey: 'codex_cost',
    modelKey: 'codex_models',
    breakdown: [
      { key: 'codex_input', label: 'Input' },
      { key: 'codex_cache_read', label: 'Cache read' },
      {
        key: 'codex_output',
        label: 'Output (non-reasoning)',
        excludeSubset: 'codex_reasoning',
      },
      {
        key: 'codex_reasoning',
        label: 'Reasoning (of output)',
        subsetOf: 'codex_output',
      },
    ],
    cacheKeys: ['codex_cache_read'],
  },
  {
    id: 'claude',
    label: 'Claude Code',
    color: 'orange',
    hex: '#c2410c',
    tokenKey: 'claude_tokens',
    costKey: 'claude_cost',
    modelKey: 'claude_models',
    breakdown: [
      { key: 'claude_input', label: 'Input' },
      { key: 'claude_cache_create', label: 'Cache create' },
      { key: 'claude_cache_read', label: 'Cache read' },
      { key: 'claude_output', label: 'Output' },
    ],
    cacheKeys: ['claude_cache_create', 'claude_cache_read'],
  },
  {
    id: 'opencode',
    label: 'OpenCode',
    color: 'green',
    hex: '#16a34a',
    tokenKey: 'opencode_tokens',
    costKey: 'opencode_cost',
    modelKey: 'opencode_models',
    breakdown: [
      { key: 'opencode_input', label: 'Input' },
      { key: 'opencode_cache_create', label: 'Cache create' },
      { key: 'opencode_cache_read', label: 'Cache read' },
      { key: 'opencode_output', label: 'Output' },
    ],
    cacheKeys: ['opencode_cache_create', 'opencode_cache_read'],
  },
  {
    id: 'cursor',
    label: 'Cursor',
    color: 'teal',
    hex: '#0d9488',
    tokenKey: 'cursor_tokens',
    costKey: 'cursor_cost',
    modelKey: 'cursor_models',
    breakdown: [
      { key: 'cursor_input', label: 'Input' },
      { key: 'cursor_cache_write', label: 'Cache write' },
      { key: 'cursor_cache_read', label: 'Cache read' },
      { key: 'cursor_output', label: 'Output' },
    ],
    cacheKeys: ['cursor_cache_write', 'cursor_cache_read'],
  },
  {
    id: 'oneapi',
    label: 'One API',
    color: 'purple',
    hex: '#7c3aed',
    tokenKey: 'oneapi_tokens',
    costKey: 'oneapi_cost',
    modelKey: 'oneapi_models',
    breakdown: [
      { key: 'oneapi_input', label: 'Input' },
      { key: 'oneapi_cache_read', label: 'Cache read' },
      { key: 'oneapi_cache_write', label: 'Cache write' },
      { key: 'oneapi_output', label: 'Output' },
    ],
    cacheKeys: ['oneapi_cache_read', 'oneapi_cache_write'],
  },
]

const SOURCE_LABELS: Record<string, string> = {
  claude: 'Claude Code',
  claudecode: 'Claude Code',
  opencode: 'OpenCode',
  cursor: 'Cursor',
  oneapi: 'One API',
  codex: 'Codex',
}

function normalizedSourceId(id: string): string {
  return id.toLowerCase().replace(/[\s_-]+/g, '')
}

function sourceLabel(id: string): string {
  return SOURCE_LABELS[normalizedSourceId(id)] ?? 'Data source'
}

function boundedDate(value?: string | null): string | null {
  if (!value || value.length > 64) return null
  const match = /^(\d{4}-\d{2}-\d{2})(?:T.*)?$/.exec(value)
  return match?.[1] ?? null
}

function statusDetails(source: SourceStatus): string[] {
  const details: string[] = []
  const lagDays = Number(source.lag_days)
  if (Number.isFinite(lagDays) && lagDays > 0) {
    const days = Math.trunc(lagDays)
    details.push(
      days > 999
        ? '999+ days behind'
        : `${days} ${days === 1 ? 'day' : 'days'} behind`,
    )
  }
  const windowEnd = boundedDate(source.window_end)
  if (windowEnd) details.push(`data through ${windowEnd}`)
  const lastSuccess = boundedDate(source.last_success_at)
  if (lastSuccess) {
    details.push(`last successful refresh ${lastSuccess}`)
  }
  const attempted = boundedDate(source.attempted_at)
  if (attempted) details.push(`last attempted ${attempted}`)
  return details
}

export function degradedSourceNotices(
  sourceStatus?: Record<string, SourceStatus>,
): DegradedSourceNotice[] {
  if (!sourceStatus) return []

  return Object.entries(sourceStatus).flatMap(([id, source]) => {
    if (!source || source.status === 'fresh') return []

    const label = sourceLabel(id)
    const retained = normalizedSourceId(id) === 'oneapi'
    const retainedCopy = retained
      ? '; previously saved data is retained and still shown'
      : ''
    const summary =
      source.status === 'stale'
        ? `${label} is stale${retainedCopy}`
        : `${label} refresh failed${retainedCopy}`
    const details = statusDetails(source)

    return [
      {
        id,
        label,
        status: source.status,
        retained,
        message: `${summary}.${details.length ? ` ${details.join('; ')}.` : ''}`,
      },
    ]
  })
}

export function num(row: DailyRow, key: keyof DailyRow): number {
  return Number(row[key]) || 0
}

export function breakdownValue(
  row: DailyRow,
  part: BreakdownSpec,
): number {
  const value = num(row, part.key)
  if (part.subsetOf) {
    return Math.min(Math.max(0, value), Math.max(0, num(row, part.subsetOf)))
  }
  if (part.excludeSubset) {
    const subset = Math.min(
      Math.max(0, num(row, part.excludeSubset)),
      Math.max(0, value),
    )
    return Math.max(0, value - subset)
  }
  return value
}

export function rowCache(row: DailyRow): number {
  return (
    num(row, 'codex_cache_read') +
    num(row, 'claude_cache_create') +
    num(row, 'claude_cache_read') +
    num(row, 'opencode_cache_create') +
    num(row, 'opencode_cache_read') +
    num(row, 'cursor_cache_write') +
    num(row, 'cursor_cache_read') +
    num(row, 'oneapi_cache_read') +
    num(row, 'oneapi_cache_write')
  )
}

export function summarizeRange(rows: DailyRow[]) {
  let tokens = 0
  let cache = 0
  let cost = 0
  const byTool = TOOLS.map((tool) => {
    let t = 0
    let c = 0
    const parts = tool.breakdown.map((b) => ({ label: b.label, value: 0 }))
    const modelMap = new Map<string, ModelUsage>()
    for (const row of rows) {
      t += num(row, tool.tokenKey)
      c += num(row, tool.costKey)
      tool.breakdown.forEach((b, i) => {
        parts[i].value += breakdownValue(row, b)
      })
      const models = row[tool.modelKey]
      if (Array.isArray(models)) {
        for (const model of models as ModelUsage[]) {
          const current = modelMap.get(model.model) ?? {
            model: model.model,
            tokens: 0,
            cost: 0,
          }
          current.tokens += Number(model.tokens) || 0
          current.cost += Number(model.cost) || 0
          modelMap.set(model.model, current)
        }
      }
    }
    tokens += t
    cost += c
    const models = [...modelMap.values()].sort(
      (a, b) => b.tokens - a.tokens || a.model.localeCompare(b.model),
    )
    return { ...tool, tokens: t, cost: c, parts, models }
  })
  for (const row of rows) cache += rowCache(row)
  return { tokens, cache, cost, byTool }
}

export function selectModelSeries(
  rows: Array<Partial<DailyRow>>,
  toolId: ToolId,
  pinnedModel?: string | null,
): ModelSeriesSelection {
  const tool = TOOLS.find((candidate) => candidate.id === toolId)
  if (!tool) {
    return { modelCount: 0, hasLegacy: false, fullModels: [], series: [] }
  }

  const modelMap = new Map<string, ModelUsage>()
  for (const row of rows) {
    const models = row[tool.modelKey]
    if (!Array.isArray(models)) continue
    for (const model of models as ModelUsage[]) {
      const name = String(model.model || '').trim()
      if (!name) continue
      const current = modelMap.get(name) ?? { model: name, tokens: 0, cost: 0 }
      current.tokens += Number(model.tokens) || 0
      current.cost += Number(model.cost) || 0
      modelMap.set(name, current)
    }
  }

  const fullModels = [...modelMap.values()]
    .filter((model) => model.tokens || model.cost)
    .sort((a, b) => b.tokens - a.tokens || a.model.localeCompare(b.model))
  const legacy = fullModels.find((model) => model.model === 'Legacy unknown')
  const identified = fullModels.filter((model) => model.model !== 'Legacy unknown')
  let visible = identified.slice(0, 4)
  const pinnedName = String(pinnedModel ?? '').trim()
  const pinned =
    pinnedName && pinnedName !== 'Legacy unknown'
      ? identified.find((model) => model.model === pinnedName) ?? {
          model: pinnedName,
          tokens: 0,
          cost: 0,
        }
      : undefined
  if (pinned && !visible.some((model) => model.model === pinned.model)) {
    visible = [...visible.slice(0, 3), pinned]
  }

  const visibleNames = new Set(visible.map((model) => model.model))
  const otherModels = identified.filter((model) => !visibleNames.has(model.model))
  const series: ModelSeriesSpec[] = visible.map((model) => ({
    id: `model:${model.model}`,
    label: model.model,
    kind: 'model',
    models: [model.model],
    tokens: model.tokens,
    cost: model.cost,
  }))
  if (legacy) {
    series.push({
      id: 'model:legacy-unknown',
      label: legacy.model,
      kind: 'legacy',
      models: [legacy.model],
      tokens: legacy.tokens,
      cost: legacy.cost,
    })
  }
  if (otherModels.length === 1) {
    const model = otherModels[0]
    series.push({
      id: `model:${model.model}`,
      label: model.model,
      kind: 'model',
      models: [model.model],
      tokens: model.tokens,
      cost: model.cost,
    })
  } else if (otherModels.length > 1) {
    series.push({
      id: 'model:other',
      label: `Other · ${otherModels.length}`,
      kind: 'other',
      models: otherModels.map((model) => model.model),
      tokens: otherModels.reduce((sum, model) => sum + model.tokens, 0),
      cost: otherModels.reduce((sum, model) => sum + model.cost, 0),
    })
  }

  return {
    modelCount: identified.length,
    hasLegacy: Boolean(legacy),
    fullModels,
    series,
  }
}

export function modelSeriesPoint(
  row: Partial<DailyRow>,
  toolId: ToolId,
  series: ModelSeriesSpec,
): Pick<ModelUsage, 'tokens' | 'cost'> {
  return modelSeriesMembers(row, toolId, series).reduce(
    (total, model) => ({
      tokens: total.tokens + model.tokens,
      cost: total.cost + model.cost,
    }),
    { tokens: 0, cost: 0 },
  )
}

export function modelSeriesMembers(
  row: Partial<DailyRow>,
  toolId: ToolId,
  series: ModelSeriesSpec,
): ModelUsage[] {
  const tool = TOOLS.find((candidate) => candidate.id === toolId)
  if (!tool) return []
  const models = row[tool.modelKey]
  if (!Array.isArray(models)) return []

  const members = new Set(series.models)
  const result = new Map<string, ModelUsage>()
  for (const model of models as ModelUsage[]) {
    const name = String(model.model || '').trim()
    if (!members.has(name)) continue
    const current = result.get(name) ?? { model: name, tokens: 0, cost: 0 }
    current.tokens += Number(model.tokens) || 0
    current.cost += Number(model.cost) || 0
    result.set(name, current)
  }
  return [...result.values()].sort(
    (a, b) => b.tokens - a.tokens || a.model.localeCompare(b.model),
  )
}

export function percentageTenths(values: number[]): number[] {
  const weights = values.map((value) =>
    Number.isFinite(value) && value > 0 ? value : 0,
  )
  const total = weights.reduce((sum, value) => sum + value, 0)
  if (!(total > 0) || !Number.isFinite(total)) {
    return weights.map(() => 0)
  }

  const exact = weights.map((value) => (value / total) * 1000)
  const allocated = exact.map(Math.floor)
  let remaining = 1000 - allocated.reduce((sum, value) => sum + value, 0)
  const order = exact
    .map((value, index) => ({ index, remainder: value - allocated[index] }))
    .sort((a, b) => b.remainder - a.remainder || a.index - b.index)

  for (let index = 0; index < remaining; index += 1) {
    allocated[order[index % order.length].index] += 1
  }
  return allocated
}

const ISO_DATE_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/
const DAY_MS = 86_400_000

function dateOrdinal(value: string): number | null {
  const match = ISO_DATE_PATTERN.exec(value)
  if (!match) return null
  const ordinal = Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]))
  return new Date(ordinal).toISOString().slice(0, 10) === value
    ? ordinal
    : null
}

function shiftedDate(value: string, days: number): string | null {
  const ordinal = dateOrdinal(value)
  if (ordinal === null) return null
  return new Date(ordinal + days * DAY_MS).toISOString().slice(0, 10)
}

export function describeRange(
  rows: Array<Pick<DailyRow, 'date'>>,
  preset: '7' | '30' | '90' | 'all',
): RangeDescription | null {
  if (!rows.length) return null
  const end = rows[rows.length - 1].date
  const start =
    preset === 'all'
      ? rows[0].date
      : shiftedDate(end, 1 - Number(preset)) ?? rows[0].date
  const startOrdinal = dateOrdinal(start)
  const endOrdinal = dateOrdinal(end)
  const calendarDays =
    startOrdinal !== null && endOrdinal !== null && endOrdinal >= startOrdinal
      ? Math.floor((endOrdinal - startOrdinal) / DAY_MS) + 1
      : rows.length
  return { start, end, calendarDays, recordedDays: rows.length }
}

export function indexForPreset(
  daily: DailyRow[],
  preset: '7' | '30' | '90' | 'all',
): [number, number] {
  const n = daily.length
  if (!n) return [0, -1]
  if (preset === 'all') return [0, n - 1]
  const days = Number(preset)
  const firstDate = shiftedDate(daily[n - 1].date, 1 - days)
  if (!firstDate) return [Math.max(0, n - days), n - 1]
  const candidate = daily.findIndex((row) => row.date >= firstDate)
  const i0 = candidate < 0 ? n - 1 : candidate
  return [i0, n - 1]
}

export async function loadUsage(): Promise<UsagePayload> {
  const url = `${import.meta.env.BASE_URL}usage.json`
  const res = await fetch(url)
  if (!res.ok) throw new Error(`Failed to load ${url}: ${res.status}`)
  const data = (await res.json()) as UsagePayload
  if (!Array.isArray(data.daily)) {
    throw new Error('usage.json missing daily[]')
  }
  return data
}
