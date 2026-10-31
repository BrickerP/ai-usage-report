export type EditorialPointInput = {
  date: string
  tokens: number | null
  cost: number | null
}

export type EditorialPoint = EditorialPointInput & {
  state: 'active' | 'zero' | 'unavailable'
  isActive: boolean
}

export type EditorialSignatureName =
  | 'Pulsar'
  | 'Sprinter'
  | 'Marathon'
  | 'Hopper'
  | 'Climber'
  | 'Trailblazer'
  | 'History still forming'

export type EditorialSignature = {
  version: 1
  name: EditorialSignatureName
  kind:
    | 'pulsar'
    | 'sprinter'
    | 'marathon'
    | 'hopper'
    | 'climber'
    | 'trailblazer'
    | 'forming'
  evidence: string
  forming: boolean
}

export type EditorialCoverage = {
  complete: boolean
  totalDates: number
  availableDates: number
  unavailableDates: number
  activeDates: number
  zeroDates: number
  calendarStart: string | null
  calendarEnd: string | null
}

export type EditorialPattern = {
  signature: EditorialSignature
  firstDate: string | null
  firstTokens: number | null
  lastDate: string | null
  lastTokens: number | null
  recordDate: string | null
  recordTokens: number | null
  coverage: EditorialCoverage
  points: EditorialPoint[]
}

const DAY_MS = 86_400_000
const ISO_DATE_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/

export function dateOrdinal(value: string): number | null {
  const match = ISO_DATE_PATTERN.exec(value)
  if (!match) return null
  const ordinal = Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]))
  return new Date(ordinal).toISOString().slice(0, 10) === value
    ? ordinal
    : null
}

export function isoDate(ordinal: number): string {
  return new Date(ordinal).toISOString().slice(0, 10)
}

function nonNegativeNumber(value: unknown): number {
  const number = Number(value)
  return Number.isFinite(number) && number > 0 ? number : 0
}

function percentage(value: number): string {
  return `${Math.round(value * 100)}%`
}

function median(values: number[]): number {
  const sorted = [...values].sort((left, right) => left - right)
  const middle = Math.floor(sorted.length / 2)
  return sorted.length % 2
    ? sorted[middle]
    : (sorted[middle - 1] + sorted[middle]) / 2
}

function signature(
  name: EditorialSignatureName,
  evidence: string,
  forming: boolean,
): EditorialSignature {
  const kind =
    name === 'History still forming'
      ? 'forming'
      : (name.toLowerCase() as EditorialSignature['kind'])
  return { version: 1, name, kind, evidence, forming }
}

function normalizePoints(input: ReadonlyArray<EditorialPointInput>): EditorialPoint[] {
  const byDate = new Map<string, EditorialPointInput>()
  for (const point of input) {
    if (!point || dateOrdinal(point.date) === null) continue
    const current = byDate.get(point.date)
    const unavailable = point.tokens === null
    const costUnavailable = point.cost === null
    if (!current) {
      byDate.set(point.date, {
        date: point.date,
        tokens: unavailable ? null : nonNegativeNumber(point.tokens),
        cost:
          unavailable || costUnavailable ? null : nonNegativeNumber(point.cost),
      })
      continue
    }
    if (current.tokens === null || unavailable) {
      current.tokens = null
      current.cost = null
      continue
    }
    current.tokens += nonNegativeNumber(point.tokens)
    current.cost =
      current.cost === null || costUnavailable
        ? null
        : current.cost + nonNegativeNumber(point.cost)
  }

  const ordinals = [...byDate.keys()]
    .map(dateOrdinal)
    .filter((ordinal): ordinal is number => ordinal !== null)
  if (!ordinals.length) return []

  const first = Math.min(...ordinals)
  const last = Math.max(...ordinals)
  const points: EditorialPoint[] = []
  for (let ordinal = first; ordinal <= last; ordinal += DAY_MS) {
    const date = isoDate(ordinal)
    const source = byDate.get(date)
    const tokens = source?.tokens ?? null
    const cost = source?.cost ?? null
    points.push({
      date,
      tokens,
      cost,
      state: tokens === null ? 'unavailable' : tokens > 0 ? 'active' : 'zero',
      isActive: tokens !== null && tokens > 0,
    })
  }
  return points
}

function deriveSignature(points: EditorialPoint[]): EditorialSignature {
  const available = points.filter(
    (point): point is EditorialPoint & { tokens: number } => point.tokens !== null,
  )
  const active = available.filter((point) => point.tokens > 0)
  const unavailableCount = points.length - available.length
  const totalTokens = active.reduce((sum, point) => sum + point.tokens, 0)
  const firstOrdinal = active.length ? dateOrdinal(active[0].date) : null
  const lastOrdinal = active.length ? dateOrdinal(active[active.length - 1].date) : null
  const calendarSpan =
    firstOrdinal !== null && lastOrdinal !== null
      ? Math.floor((lastOrdinal - firstOrdinal) / DAY_MS) + 1
      : 0

  if (!points.length) {
    return signature(
      'History still forming',
      'No published dates in this scope',
      true,
    )
  }
  if (unavailableCount) {
    return signature(
      'History still forming',
      `${unavailableCount} of ${points.length} dates unavailable`,
      true,
    )
  }
  if (!(totalTokens > 0)) {
    return signature(
      'History still forming',
      'No recorded tokens in this scope',
      true,
    )
  }
  if (active.length < 6) {
    return signature(
      'History still forming',
      `${active.length} active dates · 6 needed`,
      true,
    )
  }
  if (calendarSpan < 7) {
    return signature(
      'History still forming',
      `${calendarSpan} calendar days · 7 needed`,
      true,
    )
  }

  let activeRuns = 0
  let longestRun = 0
  let currentRun = 0
  let previousOrdinal: number | null = null
  for (const point of active) {
    const ordinal = dateOrdinal(point.date)
    if (ordinal === null) continue
    if (previousOrdinal === null || ordinal - previousOrdinal !== DAY_MS) {
      activeRuns += 1
      currentRun = 1
    } else {
      currentRun += 1
    }
    longestRun = Math.max(longestRun, currentRun)
    previousOrdinal = ordinal
  }

  const density = active.length / calendarSpan
  const descendingTokens = active
    .map((point) => point.tokens)
    .sort((left, right) => right - left)
  const topTwoShare =
    ((descendingTokens[0] ?? 0) + (descendingTokens[1] ?? 0)) / totalTokens
  const activeMedian = median(active.map((point) => point.tokens))
  const pulsePoints = active.filter(
    (point) => point.tokens >= 2 * activeMedian,
  )
  const pulseTokens = pulsePoints.reduce(
    (sum, point) => sum + point.tokens,
    0,
  )
  const pulseShare = pulseTokens / totalTokens
  let pulseGroups = 0
  let previousPulseOrdinal: number | null = null
  for (const point of pulsePoints) {
    const ordinal = dateOrdinal(point.date)
    if (ordinal === null) continue
    if (
      previousPulseOrdinal === null ||
      ordinal - previousPulseOrdinal > DAY_MS
    ) {
      pulseGroups += 1
    }
    previousPulseOrdinal = ordinal
  }

  const earlyDays = Math.ceil(calendarSpan / 2)
  const lateDays = calendarSpan - earlyDays
  const lateStart = (firstOrdinal as number) + earlyDays * DAY_MS
  let earlyTokens = 0
  let lateTokens = 0
  let lateActiveDates = 0
  for (const point of active) {
    const ordinal = dateOrdinal(point.date)
    if (ordinal !== null && ordinal >= lateStart) {
      lateTokens += point.tokens
      lateActiveDates += 1
    } else {
      earlyTokens += point.tokens
    }
  }
  const earlyMean = earlyTokens / earlyDays
  const lateMean = lateDays ? lateTokens / lateDays : 0

  if (pulseGroups >= 3 && pulseShare >= 0.5) {
    return signature(
      'Pulsar',
      `${pulseGroups} pulse groups · ${percentage(pulseShare)} of tokens on pulse dates`,
      false,
    )
  }
  if (topTwoShare >= 0.65) {
    return signature(
      'Sprinter',
      `Top 2 dates hold ${percentage(topTwoShare)} of tokens`,
      false,
    )
  }
  if (
    density >= 0.7 &&
    longestRun >= 7 &&
    longestRun >= calendarSpan / 2
  ) {
    return signature(
      'Marathon',
      `${active.length} of ${calendarSpan} dates active · longest run ${longestRun} days`,
      false,
    )
  }
  if (activeRuns >= 3 && density <= 0.4) {
    return signature(
      'Hopper',
      `${activeRuns} active runs · ${percentage(density)} density`,
      false,
    )
  }
  if (lateMean >= 1.5 * earlyMean && lateActiveDates >= 3) {
    const comparison =
      earlyMean > 0
        ? `${Math.round((lateMean / earlyMean) * 10) / 10}× early half`
        : `late mean ${Math.round(lateMean)} · early mean 0`
    return signature(
      'Climber',
      `${comparison} · ${lateActiveDates} late active dates`,
      false,
    )
  }
  return signature(
    'Trailblazer',
    `${active.length} of ${calendarSpan} dates active · longest run ${longestRun} days`,
    false,
  )
}

export function analyseEditorialPattern(
  input: ReadonlyArray<EditorialPointInput>,
): EditorialPattern {
  const points = normalizePoints(input)
  const available = points.filter(
    (point): point is EditorialPoint & { tokens: number } => point.tokens !== null,
  )
  const active = available.filter((point) => point.tokens > 0)
  const zero = available.filter((point) => point.tokens === 0)
  let record: (EditorialPoint & { tokens: number }) | null = null
  for (const point of active) {
    if (!record || point.tokens > record.tokens) record = point
  }

  return {
    signature: deriveSignature(points),
    firstDate: active[0]?.date ?? null,
    firstTokens: active[0]?.tokens ?? null,
    lastDate: active.at(-1)?.date ?? null,
    lastTokens: active.at(-1)?.tokens ?? null,
    recordDate: record?.date ?? null,
    recordTokens: record?.tokens ?? null,
    coverage: {
      complete: points.length > 0 && available.length === points.length,
      totalDates: points.length,
      availableDates: available.length,
      unavailableDates: points.length - available.length,
      activeDates: active.length,
      zeroDates: zero.length,
      calendarStart: points[0]?.date ?? null,
      calendarEnd: points.at(-1)?.date ?? null,
    },
    points,
  }
}
