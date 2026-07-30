import {
  num,
  rowCache,
  TOOLS,
  type DailyRow,
  type ToolId,
} from './usage.ts'

export type ChronicleDailyRow = Partial<DailyRow> & Pick<DailyRow, 'date'>

export type LifetimeSummary = {
  recordedTokens: number
  cacheTokens: number
  cacheRatio: number
  recordedDays: number
  firstDate: string | null
  lastDate: string | null
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/
const MONTHS = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
] as const

export type ChronicleGranularity = 'day' | 'week'

export type ChronicleSegment = {
  toolId: ToolId
  label: string
  color: string
  tokens: number
  emphasis: 'neutral' | 'selected' | 'muted'
}

export type ChroniclePeriod = {
  key: string
  startDate: string
  endDate: string
  label: string
  totalTokens: number
  segments: ChronicleSegment[]
}

type MutablePeriod = {
  startDate: string
  endDate: string
  tokensByTool: Record<ToolId, number>
}

function parseDate(date: string): Date | null {
  if (!ISO_DATE.test(date)) return null
  const parsed = new Date(`${date}T00:00:00Z`)
  return formatIsoDate(parsed) === date ? parsed : null
}

function formatIsoDate(date: Date): string {
  return [
    date.getUTCFullYear(),
    String(date.getUTCMonth() + 1).padStart(2, '0'),
    String(date.getUTCDate()).padStart(2, '0'),
  ].join('-')
}

function addDays(date: Date, days: number): Date {
  const next = new Date(date)
  next.setUTCDate(next.getUTCDate() + days)
  return next
}

function startOfNaturalWeek(date: Date): Date {
  const daysSinceMonday = (date.getUTCDay() + 6) % 7
  return addDays(date, -daysSinceMonday)
}

function formatDateLabel(date: string): string {
  const parsed = parseDate(date)
  if (!parsed) return date
  return `${MONTHS[parsed.getUTCMonth()]} ${parsed.getUTCDate()}, ${parsed.getUTCFullYear()}`
}

function emptyToolTotals(): Record<ToolId, number> {
  return Object.fromEntries(TOOLS.map((tool) => [tool.id, 0])) as Record<
    ToolId,
    number
  >
}

export function summarizeLifetime(
  daily: ChronicleDailyRow[],
): LifetimeSummary {
  let recordedTokens = 0
  let cacheTokens = 0
  const dates = new Set<string>()

  for (const partialRow of daily) {
    const row = partialRow as DailyRow
    for (const tool of TOOLS) recordedTokens += num(row, tool.tokenKey)
    cacheTokens += rowCache(row)
    if (parseDate(row.date)) dates.add(row.date)
  }

  const orderedDates = [...dates].sort()
  return {
    recordedTokens,
    cacheTokens,
    cacheRatio: recordedTokens > 0 ? cacheTokens / recordedTokens : 0,
    recordedDays: orderedDates.length,
    firstDate: orderedDates[0] ?? null,
    lastDate: orderedDates.at(-1) ?? null,
  }
}

export function buildChroniclePeriods(
  daily: ChronicleDailyRow[],
  options: {
    granularity: ChronicleGranularity
    selectedTool?: ToolId | null
  },
): ChroniclePeriod[] {
  const periods = new Map<string, MutablePeriod>()

  for (const partialRow of daily) {
    const date = parseDate(partialRow.date)
    if (!date) continue
    const start =
      options.granularity === 'week' ? startOfNaturalWeek(date) : date
    const end = options.granularity === 'week' ? addDays(start, 6) : start
    const startDate = formatIsoDate(start)
    const period = periods.get(startDate) ?? {
      startDate,
      endDate: formatIsoDate(end),
      tokensByTool: emptyToolTotals(),
    }
    const row = partialRow as DailyRow
    for (const tool of TOOLS) {
      period.tokensByTool[tool.id] += num(row, tool.tokenKey)
    }
    periods.set(startDate, period)
  }

  return [...periods.values()]
    .sort((a, b) => a.startDate.localeCompare(b.startDate))
    .map((period) => {
      const segments: ChronicleSegment[] = TOOLS.map((tool) => ({
        toolId: tool.id,
        label: tool.label,
        color: tool.hex,
        tokens: period.tokensByTool[tool.id],
        emphasis:
          options.selectedTool == null
            ? 'neutral'
            : options.selectedTool === tool.id
              ? 'selected'
              : 'muted',
      }))
      return {
        key: period.startDate,
        startDate: period.startDate,
        endDate: period.endDate,
        label:
          options.granularity === 'week'
            ? `${formatDateLabel(period.startDate)} – ${formatDateLabel(period.endDate)}`
            : formatDateLabel(period.startDate),
        totalTokens: segments.reduce(
          (total, segment) => total + segment.tokens,
          0,
        ),
        segments,
      }
    })
}
