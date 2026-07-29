export type ModelUsage = {
  model: string
  tokens: number
  cost: number
}

export type DailyRow = {
  date: string
  codex_tokens: number
  claude_tokens: number
  cursor_tokens: number
  oneapi_tokens: number
  codex_cost: number
  claude_cost: number
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
  cursor_models?: ModelUsage[]
  oneapi_models?: ModelUsage[]
}

export type UsagePayload = {
  generated_at?: string | null
  timezone?: string
  machine_id?: string
  machines?: string[]
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

export type ToolId = 'codex' | 'claude' | 'cursor' | 'oneapi'

export const TOOLS: Array<{
  id: ToolId
  label: string
  color: 'blue' | 'orange' | 'teal' | 'purple'
  hex: string
  tokenKey: keyof DailyRow
  costKey: keyof DailyRow
  modelKey: keyof DailyRow
  breakdown: Array<{ key: keyof DailyRow; label: string }>
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
      { key: 'codex_output', label: 'Output' },
      { key: 'codex_reasoning', label: 'Reasoning' },
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

export function num(row: DailyRow, key: keyof DailyRow): number {
  return Number(row[key]) || 0
}

export function rowCache(row: DailyRow): number {
  return (
    num(row, 'codex_cache_read') +
    num(row, 'claude_cache_create') +
    num(row, 'claude_cache_read') +
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
        parts[i].value += num(row, b.key)
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

export function indexForPreset(
  daily: DailyRow[],
  preset: '7' | '30' | '90' | 'all',
): [number, number] {
  const n = daily.length
  if (!n) return [0, -1]
  if (preset === 'all') return [0, n - 1]
  const days = Number(preset)
  const i0 = Math.max(0, n - days)
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
