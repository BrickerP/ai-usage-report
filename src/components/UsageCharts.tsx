import { useEffect, useRef } from 'react'
import * as echarts from 'echarts/core'
import { BarChart, LineChart, ScatterChart } from 'echarts/charts'
import {
  AriaComponent,
  AxisPointerComponent,
  GridComponent,
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
      const seriesId = String(params.seriesId || '')
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
    // Daily stacked-token totals (for peak-cap detection + peak labels).
    const totalDayTokens = daily.map((row) =>
      activeTool && focusedSeries
        ? modelSeriesPoint(row, activeTool.id, focusedSeries).tokens
        : activeTool
          ? num(row, activeTool.tokenKey)
          : TOOLS.reduce((sum, tool) => sum + num(row, tool.tokenKey), 0),
    )
    // Peak-cap (broken-bar) logic: when the max day dwarfs the rest of the
    // distribution, cap the y-axis so normal days stay readable. The capped
    // peak day is then "crowned" — a gold tiara marker + true-value badge on
    // top — so the extreme day becomes a deliberate show-off focal point
    // instead of a wall that flattens every other bar.
    const sortedTotals = [...totalDayTokens].sort((a, b) => a - b)
    const peakTokens = sortedTotals.at(-1) ?? 0
    const secondPeak = sortedTotals.at(-2) ?? 0
    const p90Tokens = sortedTotals.length
      ? sortedTotals[Math.min(
          sortedTotals.length - 1,
          Math.floor(sortedTotals.length * 0.9),
        )]
      : 0
    const enablePeakCap =
      peakTokens > 0 &&
      secondPeak > 0 &&
      peakTokens / Math.max(p90Tokens, 1) > 3 &&
      peakTokens / secondPeak > 1.8
    // Cap at the top of the second-highest day so exactly the outliers that
    // dwarf everything else get truncated; normal days keep their real height.
    const peakCap = enablePeakCap ? secondPeak * 1.06 : undefined
    const peakDayIndices = enablePeakCap
      ? totalDayTokens
          .map((value, index) =>
            value > (peakCap ?? Number.POSITIVE_INFINITY) ? index : -1,
          )
          .filter((index) => index >= 0)
      : []
    const peakDaySet = new Set(peakDayIndices)
    const specs = activeTool ? modelSelection?.series ?? [] : []
    const capTotalFor = (dayIndex: number): number => {
      const total = totalDayTokens[dayIndex] ?? 0
      const cap = peakCap ?? 0
      return enablePeakCap && peakDaySet.has(dayIndex) && total > cap
        ? cap / Math.max(total, 1)
        : 1
    }
    const bars = activeTool
      ? specs.map((spec, index) => {
          const isFocused = hasFocusedModel && spec.id === focusedSeries?.id
          const isDimmed = hasFocusedModel && !isFocused
          return {
            id: spec.id,
            name: spec.label,
            type: 'bar' as const,
            stack: 'tokens',
            cursor: spec.kind === 'other' ? 'pointer' : 'default',
            barCategoryGap: '42%',
            barMaxWidth: 34,
            xAxisIndex: 0,
            yAxisIndex: 0,
            emphasis: {
              focus: 'series' as const,
              blurScope: 'coordinateSystem' as const,
            },
            itemStyle: {
              ...stackSegStyle(
                modelSeriesColor(activeTool.hex, index, spec.kind),
                capFor(index, specs.length),
              ),
              opacity: isDimmed ? 0.22 : 1,
            },
            data: daily.map((row, dayIndex) =>
              // Truncate every stack segment of a capped day proportionally
              // so the top of the stack lands exactly on the cap.
              modelSeriesPoint(row, activeTool.id, spec).tokens *
              capTotalFor(dayIndex),
            ),
          }
        })
      : TOOLS.map((tool, index) => ({
          id: `tool:${tool.id}`,
          name: tool.label,
          type: 'bar' as const,
          stack: 'tokens',
          cursor: 'pointer',
          barCategoryGap: '42%',
          barMaxWidth: 34,
          xAxisIndex: 0,
          yAxisIndex: 0,
          emphasis: {
            focus: 'series' as const,
            blurScope: 'coordinateSystem' as const,
          },
          itemStyle: stackSegStyle(tool.hex, capFor(index, TOOLS.length)),
          data: daily.map((row, dayIndex) =>
            num(row, tool.tokenKey) * capTotalFor(dayIndex),
          ),
        }))
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
              ? `${activeTool.label} daily model tokens, focused on ${focusedSeries.label}; other model tokens are dimmed and spend follows the focused model`
              : `${activeTool.label} daily model tokens and spend`
            : 'Daily tokens and spend by AI coding tool',
        },
        axisPointer: { link: [{ xAxisIndex: [0, 1] }], snap: true },
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
        grid: [
          { left: 58, right: 28, top: 28, height: '50%' },
          { left: 58, right: 28, top: '68%', height: '22%' },
        ],
        xAxis: [
          {
            type: 'category',
            boundaryGap: true,
            data: dates,
            gridIndex: 0,
            axisLabel: { show: false },
            axisLine: { lineStyle: { color: '#e2e8f0' } },
            axisTick: { show: false },
          },
          {
            type: 'category',
            boundaryGap: true,
            data: dates,
            gridIndex: 1,
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
          {
            type: 'value',
            gridIndex: 0,
            name: enablePeakCap ? 'Tokens / day (peak capped)' : 'Tokens / day',
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
            max: enablePeakCap ? (peakCap ?? 0) * 1.18 : undefined,
            splitNumber: 4,
            splitLine: {
              show: true,
              lineStyle: { color: 'rgba(148,163,184,0.2)', type: [4, 4] },
            },
          },
          {
            type: 'value',
            gridIndex: 1,
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
            xAxisIndex: 1,
            yAxisIndex: 1,
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
          ...(enablePeakCap
            ? [
                {
                  id: 'peak-crown',
                  name: 'Peak day',
                  type: 'scatter' as const,
                  xAxisIndex: 0,
                  yAxisIndex: 0,
                  symbol: 'rect' as const,
                  symbolSize: [46, 3],
                  symbolOffset: [0, -8],
                  itemStyle: { color: '#f59e0b', opacity: 0.9 },
                  silent: true,
                  tooltip: { show: false },
                  z: 20,
                  data: totalDayTokens.map((_value, index) =>
                    peakDaySet.has(index)
                      ? [index, (peakCap ?? 0) * 0.98]
                      : null,
                  ),
                },
                {
                  id: 'peak-crown-label',
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
                    color: '#b45309',
                    fontSize: 12,
                    fontWeight: 700,
                    backgroundColor: 'rgba(255,251,235,0.96)',
                    borderColor: 'rgba(245,158,11,0.5)',
                    borderWidth: 1,
                    borderRadius: 5,
                    padding: [3, 6],
                    formatter: (params: { dataIndex: number }) => {
                      const value = totalDayTokens[params.dataIndex] ?? 0
                      return `♛ ${fmtCompact(value)}`
                    },
                  },
                  z: 21,
                  data: totalDayTokens.map((_value, index) =>
                    peakDaySet.has(index)
                      ? [index, (peakCap ?? 0) * 0.98]
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
      <div className="chart-host" ref={hostRef} role="img" aria-label={label} />
      <div className="visually-hidden">
        <table>
          <caption>{label}, exact daily data</caption>
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
