import { findPeakGroup } from './chart'
import { TOOLS, type DailyRow, type ToolId } from './usage'

export const RUN_SIGNATURE_VERSION = 1 as const

export type RunSignatureName =
  | 'Pulsar'
  | 'Sprinter'
  | 'Marathon'
  | 'Hopper'
  | 'Climber'
  | 'Trailblazer'
  | 'History still forming'

export type RunPointState = 'active' | 'zero' | 'unavailable'

export type RunLevelPoint = {
  index: number
  date: string
  state: RunPointState
  tokens: number | null
  cost: number | null
  groundTokens: number | null
  skyTokens: number | null
}

export type RunLevelLandmark = {
  index: number
  date: string
  tokens: number | null
}

export type RunLevelLandmarks = {
  firstSeen: RunLevelLandmark | null
  record: RunLevelLandmark | null
  lastSeen: RunLevelLandmark | null
  archiveEdge: RunLevelLandmark | null
}

export type RunLevelSplit = {
  enabled: boolean
  floor: number
  skyMax: number
}

export type RunSignature = {
  version: typeof RUN_SIGNATURE_VERSION
  name: RunSignatureName
  evidence: string
  forming: boolean
}

export type RunLevel = {
  toolId: ToolId
  model: string
  points: RunLevelPoint[]
  coverageComplete: boolean
  signature: RunSignature
  split: RunLevelSplit
  landmarks: RunLevelLandmarks
  defaultDay: string | null
}

const DAY_MS = 86_400_000
const ISO_DATE_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/

function dateOrdinal(value: string): number | null {
  const match = ISO_DATE_PATTERN.exec(value)
  if (!match) return null
  const ordinal = Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]))
  return new Date(ordinal).toISOString().slice(0, 10) === value
    ? ordinal
    : null
}

function nonNegativeNumber(value: unknown): number {
  const number = Number(value)
  return Number.isFinite(number) && number > 0 ? number : 0
}

function percent(value: number): string {
  return `${Math.round(value * 100)}%`
}

function landmark(point?: RunLevelPoint): RunLevelLandmark | null {
  return point
    ? { index: point.index, date: point.date, tokens: point.tokens }
    : null
}

function median(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b)
  const middle = Math.floor(sorted.length / 2)
  return sorted.length % 2
    ? sorted[middle]
    : (sorted[middle - 1] + sorted[middle]) / 2
}

function deriveSignature(points: RunLevelPoint[]): RunSignature {
  const available = points.filter(
    (point): point is RunLevelPoint & { tokens: number } => point.tokens !== null,
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

  const forming = (evidence: string): RunSignature => ({
    version: RUN_SIGNATURE_VERSION,
    name: 'History still forming',
    evidence,
    forming: true,
  })

  if (!points.length) return forming('No published dates in this scope')
  if (unavailableCount) {
    return forming(
      `${unavailableCount} of ${points.length} dates unavailable`,
    )
  }
  if (!(totalTokens > 0)) return forming('No recorded tokens in this scope')
  if (active.length < 6) {
    return forming(`${active.length} active dates · 6 needed`)
  }
  if (calendarSpan < 7) {
    return forming(`${calendarSpan} calendar days · 7 needed`)
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
    .sort((a, b) => b - a)
  const topTwoShare =
    ((descendingTokens[0] ?? 0) + (descendingTokens[1] ?? 0)) / totalTokens
  const activeMedian = median(active.map((point) => point.tokens))
  const pulsePoints = active.filter(
    (point) => point.tokens >= 2 * activeMedian,
  )
  const pulseTokens = pulsePoints.reduce((sum, point) => sum + point.tokens, 0)
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

  const signature = (
    name: Exclude<RunSignatureName, 'History still forming'>,
    evidence: string,
  ): RunSignature => ({
    version: RUN_SIGNATURE_VERSION,
    name,
    evidence,
    forming: false,
  })

  if (pulseGroups >= 3 && pulseShare >= 0.5) {
    return signature(
      'Pulsar',
      `${pulseGroups} pulse groups · ${percent(pulseShare)} of tokens on pulse dates`,
    )
  }
  if (topTwoShare >= 0.65) {
    return signature(
      'Sprinter',
      `Top 2 dates hold ${percent(topTwoShare)} of tokens`,
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
    )
  }
  if (activeRuns >= 3 && density <= 0.4) {
    return signature(
      'Hopper',
      `${activeRuns} active runs · ${percent(density)} density`,
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
    )
  }
  return signature(
    'Trailblazer',
    `${active.length} of ${calendarSpan} dates active · longest run ${longestRun} days`,
  )
}

export function deriveRunLevel(
  rows: ReadonlyArray<Partial<DailyRow>>,
  toolId: ToolId,
  model: string,
): RunLevel {
  const tool = TOOLS.find((candidate) => candidate.id === toolId)
  const modelName = model.trim()
  const rowsByDate = new Map<
    string,
    Array<Partial<DailyRow> & { date: string }>
  >()
  for (const row of rows) {
    if (typeof row.date !== 'string' || dateOrdinal(row.date) === null) continue
    const dateRows = rowsByDate.get(row.date)
    if (dateRows) dateRows.push(row as Partial<DailyRow> & { date: string })
    else rowsByDate.set(row.date, [row as Partial<DailyRow> & { date: string }])
  }

  const ordinals = [...rowsByDate.keys()]
    .map((date) => dateOrdinal(date))
    .filter((ordinal): ordinal is number => ordinal !== null)
  const minOrdinal = ordinals.length ? Math.min(...ordinals) : null
  const maxOrdinal = ordinals.length ? Math.max(...ordinals) : null
  const calendarDates: string[] = []
  if (minOrdinal !== null && maxOrdinal !== null) {
    for (let ordinal = minOrdinal; ordinal <= maxOrdinal; ordinal += DAY_MS) {
      calendarDates.push(new Date(ordinal).toISOString().slice(0, 10))
    }
  }

  const basePoints = calendarDates.map((date, index) => {
    const dateRows = rowsByDate.get(date)
    const modelArrays = dateRows?.map((row) =>
      tool ? row[tool.modelKey] : undefined,
    )
    if (
      !modelArrays?.length ||
      modelArrays.some((models) => !Array.isArray(models))
    ) {
      return {
        index,
        date,
        state: 'unavailable' as const,
        tokens: null,
        cost: null,
      }
    }

    const tokenValues: number[] = []
    const costValues: number[] = []
    for (const models of modelArrays) {
      if (!Array.isArray(models)) continue
      for (const item of models) {
        if (String(item.model ?? '').trim() !== modelName) continue
        tokenValues.push(nonNegativeNumber(item.tokens))
        costValues.push(nonNegativeNumber(item.cost))
      }
    }
    const tokens = tokenValues
      .sort((a, b) => a - b)
      .reduce((sum, value) => sum + value, 0)
    const cost = costValues
      .sort((a, b) => a - b)
      .reduce((sum, value) => sum + value, 0)
    return {
      index,
      date,
      state: tokens > 0 ? ('active' as const) : ('zero' as const),
      tokens,
      cost,
    }
  })

  const splitInput = basePoints.map((point) => point.tokens ?? 0)
  const peakGroup = findPeakGroup(splitInput)
  const splitEnabled = peakGroup.peakIndices.length > 0
  const points: RunLevelPoint[] = basePoints.map((point) => {
    if (point.tokens === null) {
      return { ...point, groundTokens: null, skyTokens: null }
    }
    const groundTokens = splitEnabled
      ? Math.min(point.tokens, peakGroup.recordFloor)
      : point.tokens
    return {
      ...point,
      groundTokens,
      skyTokens: point.tokens - groundTokens,
    }
  })

  const activePoints = points.filter(
    (point): point is RunLevelPoint & { tokens: number } =>
      point.tokens !== null && point.tokens > 0,
  )
  let recordPoint: (RunLevelPoint & { tokens: number }) | undefined
  for (const point of activePoints) {
    if (!recordPoint || point.tokens > recordPoint.tokens) recordPoint = point
  }
  const archiveEdgePoint = points[points.length - 1]

  return {
    toolId,
    model: modelName,
    points,
    coverageComplete:
      points.length > 0 && points.every((point) => point.state !== 'unavailable'),
    signature: deriveSignature(points),
    split: {
      enabled: splitEnabled,
      floor: peakGroup.recordFloor,
      skyMax: peakGroup.skyMax,
    },
    landmarks: {
      firstSeen: landmark(activePoints[0]),
      record: landmark(recordPoint),
      lastSeen: landmark(activePoints[activePoints.length - 1]),
      archiveEdge: landmark(archiveEdgePoint),
    },
    defaultDay: activePoints[0]?.date ?? null,
  }
}
