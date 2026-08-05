import { useId, useMemo } from 'react'
import {
  buildChroniclePeriods,
  summarizeLifetime,
} from '../lib/chronicle'
import { fmtCompact } from '../lib/format'
import { TOOLS, type DailyRow, type ToolId } from '../lib/usage'

export type UsageSkylineProps = {
  daily: DailyRow[]
  selectedTool?: ToolId | null
  compact?: boolean
  onSelectTool?: (toolId: ToolId) => void
}

type SegmentGeometry = {
  periodKey: string
  periodLabel: string
  toolId: ToolId
  x: number
  y: number
  width: number
  height: number
  tokens: number
}

const WIDE_LAYOUT = {
  width: 960,
  height: 236,
  plotTop: 18,
  plotBottom: 198,
  plotLeft: 10,
  plotRight: 950,
  peakY: 12,
  dateY: 222,
  labelFontSize: 11,
} as const
const COMPACT_LAYOUT = {
  width: 360,
  height: 236,
  plotTop: 32,
  plotBottom: 194,
  plotLeft: 8,
  plotRight: 352,
  peakY: 18,
  dateY: 224,
  labelFontSize: 12,
} as const
const exactNumber = new Intl.NumberFormat('en-US', {
  maximumFractionDigits: 0,
})
const shortDate = new Intl.DateTimeFormat('en-US', {
  month: 'short',
  day: 'numeric',
  timeZone: 'UTC',
})

function compactRangeLabel(startDate: string, endDate: string) {
  const start = new Date(`${startDate}T00:00:00Z`)
  const end = new Date(`${endDate}T00:00:00Z`)
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) {
    return `${startDate}–${endDate}`
  }
  const year = end.getUTCFullYear()
  if (start.getUTCFullYear() !== year) {
    return `${shortDate.format(start)}, ${start.getUTCFullYear()}–${shortDate.format(end)}, ${year}`
  }
  if (
    start.getUTCMonth() === end.getUTCMonth()
  ) {
    return `${shortDate.format(start).replace(/ \d+$/, '')} ${start.getUTCDate()}–${end.getUTCDate()}, ${year}`
  }
  return `${shortDate.format(start)}–${shortDate.format(end)}, ${year}`
}

export function UsageSkyline({
  daily,
  selectedTool = null,
  compact = false,
  onSelectTool,
}: UsageSkylineProps) {
  const titleId = useId()
  const summaryId = useId()
  const granularity = compact ? 'week' : 'day'
  const layout = compact ? COMPACT_LAYOUT : WIDE_LAYOUT
  const periods = useMemo(
    () => buildChroniclePeriods(daily, { granularity, selectedTool }),
    [daily, granularity, selectedTool],
  )
  const lifetime = useMemo(() => summarizeLifetime(daily), [daily])

  const { peakTokens, geometry, totalsByTool } = useMemo(() => {
    const peak = Math.max(0, ...periods.map((period) => period.totalTokens))
    const scalePeak = peak || 1
    const plotHeight = layout.plotBottom - layout.plotTop
    const plotWidth = layout.plotRight - layout.plotLeft
    const step = periods.length ? plotWidth / periods.length : plotWidth
    const gap = Math.min(2, step * 0.18)
    const barWidth = Math.max(0.75, step - gap)
    const totals = Object.fromEntries(
      TOOLS.map((tool) => [tool.id, 0]),
    ) as Record<ToolId, number>
    const shapes: SegmentGeometry[] = []

    periods.forEach((period, periodIndex) => {
      let stackedTokens = 0
      period.segments.forEach((segment) => {
        totals[segment.toolId] += segment.tokens
        const height = (segment.tokens / scalePeak) * plotHeight
        shapes.push({
          periodKey: period.key,
          periodLabel: period.label,
          toolId: segment.toolId,
          x: layout.plotLeft + periodIndex * step + gap / 2,
          y:
            layout.plotBottom -
            ((stackedTokens + segment.tokens) / scalePeak) * plotHeight,
          width: barWidth,
          height,
          tokens: segment.tokens,
        })
        stackedTokens += segment.tokens
      })
    })

    return { peakTokens: peak, geometry: shapes, totalsByTool: totals }
  }, [layout, periods])

  const firstPeriod = periods[0]
  const lastPeriod = periods.at(-1)
  const periodSummary =
    granularity === 'week'
      ? `${periods.length} natural weeks`
      : `${periods.length} daily points`
  const accessibleSummary =
    lifetime.firstDate && lifetime.lastDate
      ? `${exactNumber.format(lifetime.recordedTokens)} recorded tokens across ${lifetime.recordedDays} recorded days, from ${lifetime.firstDate} to ${lifetime.lastDate}; shown as ${periodSummary}.`
      : 'No recorded usage.'

  if (!periods.length) {
    return (
      <figure className="usage-skyline usage-skyline--empty">
        <figcaption>All-history AI usage skyline. No recorded usage.</figcaption>
      </figure>
    )
  }

  return (
    <figure
      className={`usage-skyline${compact ? ' usage-skyline--compact' : ''}`}
      data-granularity={granularity}
    >
      <svg
        aria-labelledby={`${titleId} ${summaryId}`}
        className="usage-skyline__chart"
        role="img"
        viewBox={`0 0 ${layout.width} ${layout.height}`}
      >
        <title id={titleId}>All-history AI usage skyline</title>
        <desc id={summaryId}>{accessibleSummary}</desc>
        <line
          aria-hidden="true"
          stroke="currentColor"
          strokeOpacity="0.14"
          x1={layout.plotLeft}
          x2={layout.plotRight}
          y1={layout.plotBottom}
          y2={layout.plotBottom}
        />
        <text
          aria-hidden="true"
          className="usage-skyline__peak"
          fill="currentColor"
          fontSize={layout.labelFontSize}
          opacity="0.62"
          x={layout.plotLeft}
          y={layout.peakY}
        >
          Peak {fmtCompact(peakTokens)}
        </text>
        {TOOLS.map((tool) => {
          const emphasis =
            firstPeriod.segments.find(
              (segment) => segment.toolId === tool.id,
            )?.emphasis ?? 'neutral'
          return (
            <g
              aria-hidden="true"
              className={`usage-skyline__series usage-skyline__series--${emphasis}`}
              key={tool.id}
              opacity={
                emphasis === 'muted'
                  ? 0.2
                  : emphasis === 'selected'
                    ? 1
                    : 0.84
              }
            >
              {geometry
                .filter(
                  (shape) =>
                    shape.toolId === tool.id && shape.height > 0,
                )
                .map((shape) => (
                  <rect
                    fill={tool.hex}
                    height={shape.height}
                    key={`${shape.periodKey}:${tool.id}`}
                    rx={Math.min(1.5, shape.width / 3)}
                    stroke={
                      emphasis === 'selected'
                        ? 'rgba(255,255,255,0.82)'
                        : 'none'
                    }
                    strokeWidth={emphasis === 'selected' ? 0.7 : 0}
                    width={shape.width}
                    x={shape.x}
                    y={shape.y}
                  >
                    <title>
                      {shape.periodLabel}: {tool.label},{' '}
                      {exactNumber.format(shape.tokens)} tokens
                    </title>
                  </rect>
                ))}
            </g>
          )
        })}
        <text
          aria-hidden="true"
          className="usage-skyline__date"
          fill="currentColor"
          fontSize={layout.labelFontSize}
          opacity="0.62"
          textAnchor="start"
          x={layout.plotLeft}
          y={layout.dateY}
        >
          {compact
            ? compactRangeLabel(firstPeriod.startDate, firstPeriod.endDate)
            : firstPeriod.label}
        </text>
        {lastPeriod && lastPeriod.key !== firstPeriod.key ? (
          <text
            aria-hidden="true"
            className="usage-skyline__date"
            textAnchor="end"
            x={layout.plotRight}
            y={layout.dateY}
          >
            {compact
              ? compactRangeLabel(lastPeriod.startDate, lastPeriod.endDate)
              : lastPeriod.label}
          </text>
        ) : null}
      </svg>
      <figcaption className="usage-skyline__legend">
        {TOOLS.map((tool) => (
          <button
            aria-label={`${selectedTool === tool.id ? 'Viewing' : 'View'} ${tool.label} models, ${exactNumber.format(totalsByTool[tool.id])} lifetime recorded tokens`}
            aria-pressed={selectedTool === tool.id}
            className="usage-skyline__legend-item"
            disabled={!onSelectTool}
            key={tool.id}
            onClick={() => onSelectTool?.(tool.id)}
            type="button"
          >
            <span
              aria-hidden="true"
              className="usage-skyline__legend-swatch"
              style={{ backgroundColor: tool.hex }}
            />
            {tool.label}
          </button>
        ))}
      </figcaption>
    </figure>
  )
}
