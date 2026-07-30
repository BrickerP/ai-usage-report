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

const VIEWBOX_WIDTH = 960
const VIEWBOX_HEIGHT = 236
const PLOT_TOP = 18
const PLOT_BOTTOM = 198
const PLOT_LEFT = 10
const PLOT_RIGHT = 950
const exactNumber = new Intl.NumberFormat('en-US', {
  maximumFractionDigits: 0,
})

export function UsageSkyline({
  daily,
  selectedTool = null,
  compact = false,
  onSelectTool,
}: UsageSkylineProps) {
  const titleId = useId()
  const summaryId = useId()
  const granularity = compact ? 'week' : 'day'
  const periods = useMemo(
    () => buildChroniclePeriods(daily, { granularity, selectedTool }),
    [daily, granularity, selectedTool],
  )
  const lifetime = useMemo(() => summarizeLifetime(daily), [daily])

  const { peakTokens, geometry, totalsByTool } = useMemo(() => {
    const peak = Math.max(0, ...periods.map((period) => period.totalTokens))
    const scalePeak = peak || 1
    const plotHeight = PLOT_BOTTOM - PLOT_TOP
    const plotWidth = PLOT_RIGHT - PLOT_LEFT
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
          x: PLOT_LEFT + periodIndex * step + gap / 2,
          y:
            PLOT_BOTTOM -
            ((stackedTokens + segment.tokens) / scalePeak) * plotHeight,
          width: barWidth,
          height,
          tokens: segment.tokens,
        })
        stackedTokens += segment.tokens
      })
    })

    return { peakTokens: peak, geometry: shapes, totalsByTool: totals }
  }, [periods])

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
        viewBox={`0 0 ${VIEWBOX_WIDTH} ${VIEWBOX_HEIGHT}`}
      >
        <title id={titleId}>All-history AI usage skyline</title>
        <desc id={summaryId}>{accessibleSummary}</desc>
        <line
          aria-hidden="true"
          stroke="currentColor"
          strokeOpacity="0.14"
          x1={PLOT_LEFT}
          x2={PLOT_RIGHT}
          y1={PLOT_BOTTOM}
          y2={PLOT_BOTTOM}
        />
        <text
          aria-hidden="true"
          className="usage-skyline__peak"
          fill="currentColor"
          fontSize="11"
          opacity="0.62"
          x={PLOT_LEFT}
          y={12}
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
          fontSize="11"
          opacity="0.62"
          textAnchor="start"
          x={PLOT_LEFT}
          y={222}
        >
          {firstPeriod.label}
        </text>
        {lastPeriod && lastPeriod.key !== firstPeriod.key ? (
          <text
            aria-hidden="true"
            className="usage-skyline__date"
            fill="currentColor"
            fontSize="11"
            opacity="0.62"
            textAnchor="end"
            x={PLOT_RIGHT}
            y={222}
          >
            {lastPeriod.label}
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
