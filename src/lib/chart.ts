function mixChannel(channel: number, amount: number) {
  return Math.round(channel + (255 - channel) * amount)
}

function mixHex(hex: string, amount: number) {
  const value = hex.replace('#', '')
  const red = mixChannel(parseInt(value.slice(0, 2), 16), amount)
  const green = mixChannel(parseInt(value.slice(2, 4), 16), amount)
  const blue = mixChannel(parseInt(value.slice(4, 6), 16), amount)
  return `#${[red, green, blue]
    .map((channel) => channel.toString(16).padStart(2, '0'))
    .join('')}`
}

export function modelSeriesColor(
  base: string,
  index: number,
  kind: 'model' | 'legacy' | 'other',
) {
  if (kind === 'legacy') return '#64748b'
  if (kind === 'other') return mixHex(base, 0.66)
  return mixHex(base, [0, 0.18, 0.34, 0.48][index] ?? 0.56)
}

type SkylineLayer = 'ground' | 'sky'

export type PeakGroup = {
  recordFloor: number
  peakIndices: number[]
  skyMax: number
}

export function findRecordDayIndex(values: number[]) {
  if (!values.length) return -1
  return values.indexOf(Math.max(...values))
}

export type RunRecord = {
  index: number
  date: string
  value: number
}

export function findRunRecord(
  values: number[],
  dates: string[],
): RunRecord | null {
  const index = findRecordDayIndex(values)
  const value = values[index] ?? 0
  const date = dates[index]
  if (index < 0 || value <= 0 || !date) return null
  return { index, date, value }
}

export function findPeakGroup(values: number[]): PeakGroup {
  const peakTokens = Math.max(...values, 0)
  const defaultFloor = Math.max(peakTokens * 1.16, 1)
  const sorted = values
    .filter((value) => value > 0)
    .sort((a, b) => a - b)

  if (sorted.length < 2) {
    return { recordFloor: defaultFloor, peakIndices: [], skyMax: 0 }
  }

  const upperLimit = Math.max(2, Math.ceil(sorted.length * 0.25))
  let gapIndex = -1
  for (let index = 1; index < sorted.length; index += 1) {
    const lowerCount = index
    const upperCount = sorted.length - index
    if (lowerCount < 2 || upperCount > upperLimit) continue

    const ratio = sorted[index] / Math.max(sorted[index - 1], Number.EPSILON)
    if (ratio >= 2.25) {
      gapIndex = index
      break
    }
  }

  if (gapIndex < 0) {
    return { recordFloor: defaultFloor, peakIndices: [], skyMax: 0 }
  }

  const bodyMax = sorted[gapIndex - 1]
  const recordFloor = Math.max(bodyMax * 1.16, 1)
  const peakIndices = values
    .map((value, index) => (value > recordFloor ? index : -1))
    .filter((index) => index >= 0)
  const skyMax = Math.max(
    ...values.map((value) => Math.max(value - recordFloor, 0)),
    0,
  )

  return { recordFloor, peakIndices, skyMax }
}

export function stackSegment(
  values: number[],
  index: number,
  floor: number,
  layer: SkylineLayer,
) {
  let start = 0
  for (let seriesIndex = 0; seriesIndex < index; seriesIndex += 1) {
    start += values[seriesIndex] ?? 0
  }
  const end = start + (values[index] ?? 0)

  if (layer === 'ground') {
    return Math.max(Math.min(end, floor) - Math.min(start, floor), 0)
  }
  return Math.max(end - Math.max(start, floor), 0)
}
