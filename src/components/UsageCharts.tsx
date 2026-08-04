import { useEffect, useRef } from 'react'
import * as echarts from 'echarts/core'
import { BarChart, LineChart, ScatterChart } from 'echarts/charts'
import {
  AriaComponent,
  AxisPointerComponent,
  GridComponent,
  MarkLineComponent,
  TooltipComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type {
  DailyRow,
  ModelSeriesSelection,
  ToolId,
} from '../lib/usage'
import {
  modelSeriesMembers,
  modelSeriesPoint,
  num,
  TOOLS,
} from '../lib/usage'
import { modelSeriesColor } from '../lib/chart'
import { fmtCompact } from '../lib/format'

echarts.use([
  AriaComponent,
  BarChart,
  LineChart,
  ScatterChart,
  GridComponent,
  TooltipComponent,
  AxisPointerComponent,
  MarkLineComponent,
  CanvasRenderer,
])

type Props = {
  daily: DailyRow[]
  selectedTool: ToolId | null
  focusedModel: string | null
  modelSelection: ModelSeriesSelection | null
  onSelectTool: (toolId: ToolId) => void
  onOpenModelList: () => void
}

function mixChannel(c: number, t: number) {
  return Math.round(c + (255 - c) * t)
}

function stackSegStyle(hex: string, cap: 'top' | 'mid' | 'bot') {
  const value = hex.replace('#', '')
  const r = parseInt(value.slice(0, 2), 16)
  const g = parseInt(value.slice(2, 4), 16)
  const b = parseInt(value.slice(4, 6), 16)
  const topRgb = `${mixChannel(r, 0.15)},${mixChannel(g, 0.15)},${mixChannel(b, 0.15)}`
  const radius = 6
  const borderRadius =
    cap === 'top'
      ? [radius, radius, 0, 0]
      : cap === 'bot'
        ? [0, 0, radius, radius]
        : [0, 0, 0, 0]
  return {
    color: {
      type: 'linear' as const,
      x: 0,
      y: 0,
      x2: 0,
      y2: 1,
      colorStops: [
        { offset: 0, color: `rgb(${topRgb})` },
        { offset: 1, color: hex },
      ],
    },
    borderColor: 'rgba(15,23,42,0.06)',
    borderWidth: 1,
    borderRadius,
  }
}

function capFor(index: number, count: number): 'top' | 'mid' | 'bot' {
  if (count <= 1 || index === count - 1) return 'top'
  if (index === 0) return 'bot'
  return 'mid'
}

type SkylineLayer = 'ground' | 'sky'

type PeakGroup = {
  recordFloor: number
  peakIndices: number[]
  skyMax: number
}

function findPeakGroup(values: number[]): PeakGroup {
  const peakTokens = Math.max(...values, 0)
  const defaultFloor = Math.max(peakTokens * 1.16, 1)
  const sorted = values
    .filter((value) => value > 0)
    .sort((a, b) => a - b)

  if (sorted.length < 2) {
    return { recordFloor: defaultFloor, peakIndices: [], skyMax: 0 }
  }

  let gapIndex = -1
  let gapRatio = 1
  for (let index = 1; index < sorted.length; index += 1) {
    const ratio = sorted[index] / Math.max(sorted[index - 1], Number.EPSILON)
    if (ratio >= gapRatio) {
      gapIndex = index
      gapRatio = ratio
    }
  }

  const upperCount = gapIndex >= 0 ? sorted.length - gapIndex : 0
  const upperLimit = Math.max(2, Math.ceil(sorted.length * 0.25))
  const hasPeakGroup =
    gapIndex > 0 && gapRatio >= 2.25 && upperCount <= upperLimit

  if (!hasPeakGroup) {
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

function stackSegment(
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

function escapeHtml(value: string) {
  return value.replace(
    /[&<>"']/g,
    (character) =>
      ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;',
      })[character] ?? character,
  )
}

function fmtExact(value: number) {
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 }).format(value)
}

function fmtTooltipUsd(value: number) {
  const digits = Math.abs(value) > 0 && Math.abs(value) < 0.01 ? 4 : 2
  return `$${value.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`
}

function fmtAxisUsd(value: number) {
  const absolute = Math.abs(value)
  if (absolute >= 1000) return `$${fmtCompact(value)}`
  const digits = absolute > 0 && absolute < 1 ? 2 : absolute < 10 ? 1 : 0
  return `$${value.toLocaleString('en-US', {
    minimumFractionDigits: 0,
    maximumFractionDigits: digits,
  })}`
}

export function UsageCharts({
  daily,
  selectedTool,
  focusedModel,
  modelSelection,
  onSelectTool,
  onOpenModelList,
}: Props) {
  const hostRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<echarts.EChartsType | null>(null)
  const stateRef = useRef({ selectedTool, onSelectTool, onOpenModelList })
  stateRef.current = { selectedTool, onSelectTool, onOpenModelList }

  useEffect(() => {
    if (!hostRef.current) return
    const chart = echarts.init(hostRef.current, undefined, { renderer: 'canvas' })
    chartRef.current = chart

    const onClick = (rawParams: unknown) => {
      const params = rawParams as { seriesId?: string }
      const rawSeriesId = String(params.seriesId || '')
      const seriesId = rawSeriesId.replace(/^(?:ground|sky):/, '')
      const state = stateRef.current
      if (!state.selectedTool && seriesId.startsWith('tool:')) {
        const toolId = seriesId.slice('tool:'.length) as ToolId
        if (TOOLS.some((tool) => tool.id === toolId)) state.onSelectTool(toolId)
      } else if (state.selectedTool && seriesId === 'model:other') {
        state.onOpenModelList()
      }
    }

    chart.on('click', onClick)
    const onResize = () => chart.resize()
    window.addEventListener('resize', onResize)

    return () => {
      window.removeEventListener('resize', onResize)
      chart.off('click', onClick)
      chart.dispose()
      chartRef.current = null
    }
  }, [])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    if (!daily.length) {
      chart.clear()
      return
    }

    const dates = daily.map((row) => row.date)
    const activeTool = selectedTool
      ? TOOLS.find((tool) => tool.id === selectedTool)
      : undefined
    const focusedSeries = activeTool && focusedModel
      ? modelSelection?.series.find(
          (series) =>
            series.kind === 'model' &&
            series.models.length === 1 &&
            series.models[0] === focusedModel,
        )
      : undefined
    const hasFocusedModel = Boolean(focusedSeries)
    const totalDaySpend = daily.map((row) =>
      activeTool && focusedSeries
        ? modelSeriesPoint(row, activeTool.id, focusedSeries).cost
        : activeTool
          ? num(row, activeTool.costKey)
          : TOOLS.reduce((sum, tool) => sum + num(row, tool.costKey), 0),
    )
    // Daily stacked-token totals (for record-skyline detection + labels).
    const totalDayTokens = daily.map((row) =>
      activeTool
        ? num(row, activeTool.tokenKey)
        : TOOLS.reduce((sum, tool) => sum + num(row, tool.tokenKey), 0),
    )
    // Token Skyline: the data stays linear and intact, but a record day is
    // split across a ground grid and a sky grid. The lower part remains in
    // the ordinary daily distribution; the exact remainder is rendered above
    // the record horizon. Nothing is clipped or transformed.
    const peakGroup = findPeakGroup(totalDayTokens)
    const enablePeakSkyline = peakGroup.peakIndices.length > 0
    const recordHorizon = enablePeakSkyline ? peakGroup.recordFloor : undefined
    const peakDayIndices = peakGroup.peakIndices
    const peakDaySet = new Set(peakDayIndices)
    const specs = activeTool ? modelSelection?.series ?? [] : []
    const barSpecs = activeTool
      ? specs.map((spec, index) => ({
          id: spec.id,
          name: spec.label,
          kind: spec.kind,
          cursor: spec.kind === 'other' ? 'pointer' : 'default',
          color: modelSeriesColor(activeTool.hex, index, spec.kind),
          valueFor: (row: DailyRow) =>
            modelSeriesPoint(row, activeTool.id, spec).tokens,
        }))
      : TOOLS.map((tool) => ({
          id: `tool:${tool.id}`,
          name: tool.label,
          kind: 'model' as const,
          cursor: 'pointer',
          color: tool.hex,
          valueFor: (row: DailyRow) => num(row, tool.tokenKey),
        }))
    const stackValues = daily.map((row, dayIndex) => {
      const values = barSpecs.map((spec) => spec.valueFor(row))
      return values.map((value, index) => ({
        ground:
          enablePeakSkyline && peakDaySet.has(dayIndex)
            ? stackSegment(values, index, recordHorizon ?? 0, 'ground')
            : value,
        sky:
          enablePeakSkyline && peakDaySet.has(dayIndex)
            ? stackSegment(values, index, recordHorizon ?? 0, 'sky')
            : 0,
      }))
    })
    const tokenGridIndex = enablePeakSkyline ? 1 : 0
    const spendGridIndex = enablePeakSkyline ? 2 : 1
    const tokenXAxisIndex = tokenGridIndex
    const spendXAxisIndex = spendGridIndex
    const makeBars = (layer: 'ground' | 'sky') =>
      barSpecs.map((spec, index) => {
        const isFocused = hasFocusedModel && spec.id === focusedSeries?.id
        const isDimmed = hasFocusedModel && !isFocused
        const seriesId = `${layer}:${spec.id}`
        return {
          id: seriesId,
          name: spec.name,
          type: 'bar' as const,
          stack: `${layer}-tokens`,
          cursor: spec.cursor,
          barCategoryGap: '42%',
          barMaxWidth: 34,
          xAxisIndex: layer === 'sky' ? tokenXAxisIndex - 1 : tokenXAxisIndex,
          yAxisIndex: layer === 'sky' ? tokenGridIndex - 1 : tokenGridIndex,
          emphasis: {
            focus: 'series' as const,
            blurScope: 'coordinateSystem' as const,
          },
          itemStyle: {
            ...stackSegStyle(
              spec.color,
              capFor(index, barSpecs.length),
            ),
            opacity: isDimmed ? 0.22 : 1,
          },
          markLine:
            layer === 'ground' && index === 0 && recordHorizon
              ? {
                  silent: true,
                  symbol: ['none', 'none'],
                  lineStyle: {
                    color: '#d99a2b',
                    width: 1.5,
                    type: 'dashed',
                  },
                  label: {
                    show: true,
                    position: 'insideEndTop',
                    color: '#9a6a19',
                    fontSize: 10,
                    formatter: 'RECORD HORIZON',
                  },
                  data: [{ yAxis: recordHorizon }],
                }
              : undefined,
          data: stackValues.map((parts, dayIndex) =>
            parts[index]?.[layer] ?? 0,
          ),
        }
      })
    const groundBars = makeBars('ground')
    const skyBase = enablePeakSkyline
      ? {
          id: 'sky-base',
          name: 'Record horizon base',
          type: 'bar' as const,
          stack: 'sky-tokens',
          xAxisIndex: tokenXAxisIndex - 1,
          yAxisIndex: tokenGridIndex - 1,
          barCategoryGap: '42%',
          barMaxWidth: 34,
          itemStyle: { color: 'transparent' },
          emphasis: { disabled: true },
          silent: true,
          tooltip: { show: false },
          data: daily.map((_row, index) =>
            peakDaySet.has(index) ? recordHorizon ?? 0 : 0,
          ),
        }
      : undefined
    const skyBars = enablePeakSkyline ? makeBars('sky') : []
    const bars = enablePeakSkyline
      ? [...(skyBase ? [skyBase] : []), ...skyBars, ...groundBars]
      : groundBars
    const lineColor = activeTool?.hex ?? '#334155'
    const rotate = dates.length > 24 ? 32 : 0
    const reduceMotion = window.matchMedia(
      '(prefers-reduced-motion: reduce)',
    ).matches

    chart.setOption(
      {
        animation: !reduceMotion,
        textStyle: {
          fontFamily: 'ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif',
        },
        aria: {
          enabled: true,
          description: activeTool
            ? focusedSeries
              ? `${activeTool.label} daily model tokens, focused on ${focusedSeries.label}; other model tokens are dimmed and spend follows the focused model${enablePeakSkyline ? '; record days continue above the record horizon' : ''}`
              : `${activeTool.label} daily model tokens and spend${enablePeakSkyline ? '; record days continue above the record horizon' : ''}`
            : `Daily tokens and spend by AI coding tool${enablePeakSkyline ? '; record days continue above the record horizon' : ''}`,
        },
        axisPointer: {
          link: [
            {
              xAxisIndex: enablePeakSkyline ? [0, 1, 2] : [0, 1],
            },
          ],
          snap: true,
        },
        tooltip: {
          trigger: 'axis',
          confine: true,
          axisPointer: {
            type: 'shadow',
            shadowStyle: { color: 'rgba(15, 23, 42, 0.05)' },
          },
          borderRadius: 8,
          padding: [8, 10],
          backgroundColor: 'rgba(255,255,255,0.98)',
          borderColor: '#e2e8f0',
          borderWidth: 1,
          textStyle: { color: '#0f172a', fontSize: 12 },
          extraCssText:
            'box-shadow:0 8px 24px rgba(15,23,42,0.08);max-height:60vh;overflow:auto;',
          formatter: (params: unknown) => {
            const list = params as Array<{ dataIndex: number }>
            if (!list?.length) return ''
            const row = daily[list[0].dataIndex]
            if (!row) return ''
            const lines = [
              `<strong>${escapeHtml(row.date)}${activeTool ? ` · ${escapeHtml(activeTool.label)}` : ''}</strong>`,
            ]
            if (enablePeakSkyline && peakDaySet.has(list[0].dataIndex)) {
              lines.push(
                `<span style="color:#9a6a19">Record sky: <strong>${fmtExact(totalDayTokens[list[0].dataIndex])}</strong> tokens</span>`,
              )
            }

            if (activeTool) {
              const tooltipSeries = focusedSeries ? [focusedSeries] : specs
              for (const spec of tooltipSeries) {
                const point = modelSeriesPoint(row, activeTool.id, spec)
                if (!focusedSeries && !point.tokens && !point.cost) continue
                const label =
                  spec.kind === 'legacy' ? 'Unattributed' : spec.label
                lines.push(
                  `${escapeHtml(label)}: <strong>${fmtExact(point.tokens)}</strong> · ${fmtTooltipUsd(point.cost)}`,
                )
                if (spec.kind === 'other') {
                  for (const model of modelSeriesMembers(
                    row,
                    activeTool.id,
                    spec,
                  )) {
                    lines.push(
                      `<span style="padding-left:10px;color:#475569">↳ ${escapeHtml(model.model)}: <strong>${fmtExact(model.tokens)}</strong> · ${fmtTooltipUsd(model.cost)}</span>`,
                    )
                  }
                }
              }
              lines.push(
                `<div style="margin-top:8px">Tool total: <strong>${fmtExact(num(row, activeTool.tokenKey))}</strong> · ${fmtTooltipUsd(num(row, activeTool.costKey))}</div>`,
              )
              return lines.join('<br/>')
            }

            for (const tool of TOOLS) {
              lines.push(
                `<div style="margin-top:8px"><strong>${escapeHtml(tool.label)}</strong>: ${fmtExact(num(row, tool.tokenKey))} · ${fmtTooltipUsd(num(row, tool.costKey))}</div>`,
              )
            }
            const dayTokens = TOOLS.reduce(
              (sum, tool) => sum + num(row, tool.tokenKey),
              0,
            )
            lines.push(
              `<div style="margin-top:8px">All tools: <strong>${fmtExact(dayTokens)}</strong> · ${fmtTooltipUsd(totalDaySpend[list[0].dataIndex] || 0)}</div>`,
            )
            return lines.join('<br/>')
          },
        },
        grid: enablePeakSkyline
          ? [
              { left: 58, right: 28, top: 28, height: '24%' },
              { left: 58, right: 28, top: '36%', height: '29%' },
              { left: 58, right: 28, top: '75%', height: '18%' },
            ]
          : [
              { left: 58, right: 28, top: 28, height: '50%' },
              { left: 58, right: 28, top: '68%', height: '22%' },
            ],
        xAxis: [
          ...(enablePeakSkyline
            ? [
                {
                  type: 'category' as const,
                  boundaryGap: true,
                  data: dates,
                  gridIndex: 0,
                  axisLabel: { show: false },
                  axisLine: { lineStyle: { color: '#f3e3bf' } },
                  axisTick: { show: false },
                },
              ]
            : []),
          {
            type: 'category',
            boundaryGap: true,
            data: dates,
            gridIndex: tokenGridIndex,
            axisLabel: { show: false },
            axisLine: { lineStyle: { color: '#e2e8f0' } },
            axisTick: { show: false },
          },
          {
            type: 'category',
            boundaryGap: true,
            data: dates,
            gridIndex: spendGridIndex,
            axisLabel: {
              formatter: (value: string) => value.slice(5),
              hideOverlap: true,
              rotate,
              fontSize: 11,
              color: '#64748b',
              margin: 10,
            },
            axisLine: { lineStyle: { color: '#e2e8f0' } },
            axisTick: {
              alignWithLabel: true,
              lineStyle: { color: '#e2e8f0' },
            },
          },
        ],
        yAxis: [
          ...(enablePeakSkyline
            ? [
                {
                  type: 'value' as const,
                  gridIndex: 0,
                  name: 'RECORD SKY',
                  nameTextStyle: {
                    fontSize: 10,
                    color: '#b1812d',
                    padding: [0, 0, 0, 8],
                  },
                  axisLabel: {
                    formatter: (value: number) => fmtCompact(value),
                    color: '#9a6a19',
                  },
                  min: recordHorizon,
                  max: (recordHorizon ?? 0) + peakGroup.skyMax * 1.1,
                  splitNumber: 2,
                  splitLine: {
                    show: true,
                    lineStyle: { color: 'rgba(217,154,43,0.18)', type: [4, 4] },
                  },
                },
              ]
            : []),
          {
            type: 'value',
            gridIndex: tokenGridIndex,
            name: enablePeakSkyline ? 'GROUND / TOKENS PER DAY' : 'Tokens / day',
            nameTextStyle: {
              fontSize: 11,
              color: '#94a3b8',
              padding: [0, 0, 0, 8],
            },
            axisLabel: {
              formatter: (value: number) => fmtCompact(value),
              color: '#64748b',
            },
            min: 0,
            max: enablePeakSkyline ? (recordHorizon ?? 0) * 1.06 : undefined,
            splitNumber: 4,
            splitLine: {
              show: true,
              lineStyle: { color: 'rgba(148,163,184,0.2)', type: [4, 4] },
            },
          },
          {
            type: 'value',
            gridIndex: spendGridIndex,
            name: 'Spend / day',
            nameTextStyle: {
              fontSize: 11,
              color: '#94a3b8',
              padding: [0, 0, 0, 8],
            },
            axisLabel: {
              formatter: (value: number) => fmtAxisUsd(value),
              color: '#64748b',
            },
            min: 0,
            splitNumber: 3,
            splitLine: {
              show: true,
              lineStyle: { color: 'rgba(148,163,184,0.14)', type: [4, 4] },
            },
          },
        ],
        series: [
          ...bars,
          {
            id: 'spend',
            name: activeTool
              ? `${focusedSeries?.label ?? activeTool.label} spend`
              : 'Daily spend (all tools)',
            type: 'line',
            xAxisIndex: spendXAxisIndex,
            yAxisIndex: spendGridIndex,
            smooth: 0.35,
            symbol: 'circle',
            symbolSize: dates.length > 72 ? 0 : 5,
            showSymbol: dates.length <= 72,
            lineStyle: { width: 2.4, color: lineColor },
            itemStyle: { color: lineColor, borderWidth: 0 },
            areaStyle: {
              color: {
                type: 'linear',
                x: 0,
                y: 0,
                x2: 0,
                y2: 1,
                colorStops: [
                  { offset: 0, color: `${lineColor}33` },
                  { offset: 1, color: `${lineColor}05` },
                ],
              },
            },
            data: totalDaySpend,
          },
          ...(enablePeakSkyline
            ? [
                {
                  id: 'record-beacon',
                  name: 'Peak day',
                  type: 'scatter' as const,
                  xAxisIndex: 0,
                  yAxisIndex: 0,
                  symbol: 'diamond' as const,
                  symbolSize: 11,
                  itemStyle: {
                    color: '#f4b740',
                    borderColor: '#fff7ed',
                    borderWidth: 2,
                    shadowBlur: 12,
                    shadowColor: 'rgba(217,154,43,0.5)',
                  },
                  silent: true,
                  tooltip: { show: false },
                  z: 20,
                  data: totalDayTokens.map((_value, index) =>
                    peakDaySet.has(index)
                      ? [index, totalDayTokens[index]]
                      : null,
                  ),
                },
                {
                  id: 'record-label',
                  name: 'Peak day',
                  type: 'scatter' as const,
                  xAxisIndex: 0,
                  yAxisIndex: 0,
                  symbol: 'none' as const,
                  silent: true,
                  tooltip: { show: false },
                  label: {
                    show: true,
                    position: 'top',
                    distance: 10,
                    color: '#9a6a19',
                    fontSize: 12,
                    fontWeight: 700,
                    backgroundColor: 'rgba(255,251,235,0.96)',
                    borderColor: 'rgba(245,158,11,0.5)',
                    borderWidth: 1,
                    borderRadius: 5,
                    padding: [3, 6],
                    formatter: (params: { dataIndex: number }) => {
                      const value = totalDayTokens[params.dataIndex] ?? 0
                      return `RECORD · ${fmtCompact(value)}`
                    },
                  },
                  z: 21,
                  data: totalDayTokens.map((_value, index) =>
                    peakDaySet.has(index)
                      ? [index, totalDayTokens[index]]
                      : null,
                  ),
                },
              ]
            : []),
        ],
      },
      { notMerge: true },
    )
  }, [daily, focusedModel, modelSelection, selectedTool])

  const activeTool = selectedTool
    ? TOOLS.find((tool) => tool.id === selectedTool)
    : undefined
  const focusedAccessibleSeries =
    activeTool && focusedModel
      ? modelSelection?.series.find(
          (series) =>
            series.kind === 'model' &&
            series.models.length === 1 &&
            series.models[0] === focusedModel,
        )
      : undefined
  const label = activeTool
    ? focusedAccessibleSeries
      ? `${activeTool.label} daily model usage, focused on ${focusedAccessibleSeries.label}; other model tokens are dimmed and spend follows the focused model`
      : `${activeTool.label} daily model usage`
    : 'Daily usage by AI coding tool'
  const skylineAccessibilityNote =
    ' Record days, when present, continue above a labeled record horizon; exact daily totals remain available below.'
  const accessibleSeries = activeTool
    ? modelSelection?.series ?? []
    : TOOLS.map((tool) => ({
        id: `tool:${tool.id}`,
        label: tool.label,
        kind: 'model' as const,
        models: [],
        tokens: 0,
        cost: 0,
      }))

  return (
    <>
      <div
        className="chart-host"
        ref={hostRef}
        role="img"
        aria-label={`${label}.${skylineAccessibilityNote}`}
      />
      <div className="visually-hidden">
        <table>
          <caption>{label}.{skylineAccessibilityNote} Exact daily data.</caption>
          <thead>
            <tr>
              <th scope="col">Date</th>
              {accessibleSeries.map((series) => (
                <th scope="col" key={series.id}>
                  {series.kind === 'legacy'
                    ? 'Unattributed legacy tokens'
                    : `${series.label} tokens`}
                </th>
              ))}
              <th scope="col">
                {activeTool
                  ? `${focusedAccessibleSeries?.label ?? activeTool.label} spend`
                  : 'All tools spend'}
              </th>
            </tr>
          </thead>
          <tbody>
            {daily.map((row) => (
              <tr key={row.date}>
                <th scope="row">{row.date}</th>
                {accessibleSeries.map((series) => (
                  <td key={series.id}>
                    {fmtExact(
                      activeTool
                        ? modelSeriesPoint(row, activeTool.id, series).tokens
                        : num(
                            row,
                            TOOLS.find(
                              (tool) => `tool:${tool.id}` === series.id,
                            )?.tokenKey ?? 'total_tokens',
                          ),
                    )}
                  </td>
                ))}
                <td>
                  {fmtTooltipUsd(
                    activeTool && focusedAccessibleSeries
                      ? modelSeriesPoint(
                          row,
                          activeTool.id,
                          focusedAccessibleSeries,
                        ).cost
                      : activeTool
                        ? num(row, activeTool.costKey)
                      : TOOLS.reduce(
                          (sum, tool) => sum + num(row, tool.costKey),
                          0,
                        ),
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}
