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
import {
  findPeakGroup,
  findRecordDayIndex,
  findRunRecord,
  modelSeriesColor,
  stackSegment,
} from '../lib/chart'
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

function stackSegStyle(hex: string, cap: 'top' | 'mid' | 'bot') {
  return {
    color: hex,
    borderColor: cap === 'top' ? '#f1e8d2' : '#07131f',
    borderWidth: cap === 'top' ? 2 : 1,
    borderRadius: 0,
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
    // Daily stacked-token totals define the terrain and preserve every series.
    const totalDayTokens = daily.map((row) =>
      activeTool
        ? num(row, activeTool.tokenKey)
        : TOOLS.reduce((sum, tool) => sum + num(row, tool.tokenKey), 0),
    )
    const recordDailyTokens = focusedSeries && activeTool
      ? daily.map(
          (row) => modelSeriesPoint(row, activeTool.id, focusedSeries).tokens,
        )
      : totalDayTokens
    // The data stays linear and intact, but a record day is split across two
    // connected stage grids. Ordinary days remain readable below while the
    // exact record remainder continues into the upper stage. Nothing is
    // clipped or transformed.
    const peakGroup = findPeakGroup(totalDayTokens)
    const enablePeakSkyline = peakGroup.peakIndices.length > 0
    const recordHorizon = enablePeakSkyline ? peakGroup.recordFloor : undefined
    const peakDayIndices = peakGroup.peakIndices
    const peakDaySet = new Set(peakDayIndices)
    const recordDayIndex = findRecordDayIndex(recordDailyTokens)
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
          barCategoryGap: '18%',
          barMaxWidth: 30,
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
                    color: 'rgba(255,200,74,0.42)',
                    width: 1,
                    type: [5, 3],
                  },
                  label: { show: false },
                  data: [{ yAxis: recordHorizon }],
                }
              : undefined,
          data: stackValues.map((parts, dayIndex) => {
            const value = parts[index]?.[layer] ?? 0
            return peakDaySet.has(dayIndex) && value > 0
              ? {
                  value,
                  itemStyle: {
                    borderColor: '#ffc84a',
                    borderWidth: 2,
                    shadowBlur: layer === 'sky' ? 8 : 0,
                    shadowColor: 'rgba(255,200,74,0.35)',
                  },
                }
              : value
          }),
        }
      })
    const groundBars = makeBars('ground')
    const skyBase = enablePeakSkyline
      ? {
          id: 'sky-base',
          name: 'Record checkpoint base',
          type: 'bar' as const,
          stack: 'sky-tokens',
          xAxisIndex: tokenXAxisIndex - 1,
          yAxisIndex: tokenGridIndex - 1,
          barCategoryGap: '18%',
          barMaxWidth: 30,
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
    const lineColor = activeTool?.hex ?? '#f1e8d2'
    const latestRunIndex = totalDayTokens.reduce(
      (latest, value, index) => (value > 0 ? index : latest),
      -1,
    )
    const terrainMax = enablePeakSkyline
      ? recordHorizon ?? 1
      : Math.max(...totalDayTokens, 1)
    const dateLabelInterval =
      dates.length > 12 ? Math.ceil(dates.length / 6) - 1 : 0
    const recordPlaquePosition =
      recordDayIndex >= dates.length * 0.72 ? 'left' : 'right'
    const recordValue =
      recordDayIndex >= 0 ? recordDailyTokens[recordDayIndex] ?? 0 : 0
    const recordBeaconInSky =
      enablePeakSkyline && peakDaySet.has(recordDayIndex)
    const recordBeaconXAxisIndex = recordBeaconInSky
      ? tokenXAxisIndex - 1
      : tokenXAxisIndex
    const recordBeaconYAxisIndex = recordBeaconInSky
      ? tokenGridIndex - 1
      : tokenGridIndex
    const recordBeaconValue =
      recordDayIndex >= 0 ? totalDayTokens[recordDayIndex] ?? 0 : 0
    const reduceMotion = window.matchMedia(
      '(prefers-reduced-motion: reduce)',
    ).matches

    chart.setOption(
      {
        animation: !reduceMotion,
        textStyle: {
          fontFamily: 'ui-monospace, "SFMono-Regular", Consolas, monospace',
        },
        aria: {
          enabled: true,
          description: activeTool
            ? focusedSeries
              ? `${activeTool.label} daily model tokens, focused on ${focusedSeries.label}; other model tokens are dimmed and spend follows the focused model${enablePeakSkyline ? '; record days extend into the upper stage with their exact totals labeled' : ''}`
              : `${activeTool.label} daily model tokens and spend${enablePeakSkyline ? '; record days extend into the upper stage with their exact totals labeled' : ''}`
            : `Daily tokens and spend by AI coding tool${enablePeakSkyline ? '; record days extend into the upper stage with their exact totals labeled' : ''}`,
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
            shadowStyle: { color: 'rgba(255, 200, 74, 0.08)' },
          },
          borderRadius: 0,
          padding: [8, 10],
          backgroundColor: 'rgba(241,232,210,0.98)',
          borderColor: '#07131f',
          borderWidth: 2,
          textStyle: { color: '#07131f', fontSize: 12 },
          extraCssText:
            'box-shadow:4px 4px 0 rgba(7,19,31,0.28);max-height:60vh;overflow:auto;',
          formatter: (params: unknown) => {
            const list = params as Array<{ dataIndex: number }>
            if (!list?.length) return ''
            const row = daily[list[0].dataIndex]
            if (!row) return ''
            const lines = [
              `<strong>${escapeHtml(row.date)}${activeTool ? ` · ${escapeHtml(activeTool.label)}` : ''}</strong>`,
            ]
            if (
              recordDayIndex >= 0 && list[0].dataIndex === recordDayIndex
            ) {
              lines.push(
                `<span style="color:#8a5a00">RECORD: <strong>${fmtExact(recordDailyTokens[list[0].dataIndex])}</strong> tokens</span>`,
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
              {
                left: 58,
                right: 28,
                top: 28,
                height: '27%',
                show: true,
                backgroundColor: 'rgba(7,19,31,0.05)',
                borderColor: 'rgba(255,200,74,0.14)',
                borderWidth: 1,
              },
              {
                left: 58,
                right: 28,
                top: '34%',
                height: '31%',
                show: true,
                backgroundColor: 'rgba(7,19,31,0.1)',
                borderColor: 'rgba(241,232,210,0.12)',
                borderWidth: 1,
              },
              {
                left: 58,
                right: 28,
                top: '75%',
                height: '18%',
                show: true,
                backgroundColor: 'rgba(7,19,31,0.06)',
                borderColor: 'rgba(241,232,210,0.1)',
                borderWidth: 1,
              },
            ]
          : [
              {
                left: 58,
                right: 28,
                top: 28,
                height: '50%',
                show: true,
                backgroundColor: 'rgba(7,19,31,0.1)',
                borderColor: 'rgba(241,232,210,0.12)',
                borderWidth: 1,
              },
              {
                left: 58,
                right: 28,
                top: '68%',
                height: '22%',
                show: true,
                backgroundColor: 'rgba(7,19,31,0.06)',
                borderColor: 'rgba(241,232,210,0.1)',
                borderWidth: 1,
              },
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
                  axisLine: {
                    lineStyle: {
                      color: 'rgba(255,200,74,0.36)',
                      width: 1,
                      type: [4, 4],
                    },
                  },
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
            axisLine: {
              lineStyle: { color: 'rgba(241,232,210,0.22)', width: 1 },
            },
            axisTick: { show: false },
          },
          {
            type: 'category',
            boundaryGap: true,
            data: dates,
            gridIndex: spendGridIndex,
            axisLabel: {
              formatter: (value: string) => value.slice(5),
              interval: dateLabelInterval,
              hideOverlap: true,
              rotate: 0,
              fontSize: 10,
              color: 'rgba(158,170,173,0.72)',
              margin: 10,
            },
            axisLine: {
              lineStyle: { color: 'rgba(241,232,210,0.2)', width: 1 },
            },
            axisTick: {
              alignWithLabel: true,
              lineStyle: { color: 'rgba(241,232,210,0.18)' },
            },
          },
        ],
        yAxis: [
          ...(enablePeakSkyline
            ? [
                {
                  type: 'value' as const,
                  gridIndex: 0,
                  name: '',
                  axisLabel: {
                    formatter: (value: number) => fmtCompact(value),
                    color: 'rgba(255,200,74,0.72)',
                    fontSize: 10,
                  },
                  min: recordHorizon,
                  max: (recordHorizon ?? 0) + peakGroup.skyMax * 1.1,
                  splitNumber: 2,
                  splitLine: {
                    show: true,
                    lineStyle: { color: 'rgba(255,200,74,0.12)', type: [2, 4] },
                  },
                },
              ]
            : []),
          {
            type: 'value',
            gridIndex: tokenGridIndex,
            name: '',
            axisLabel: {
              formatter: (value: number) => fmtCompact(value),
              color: 'rgba(158,170,173,0.7)',
              fontSize: 10,
            },
            min: 0,
            max: enablePeakSkyline ? (recordHorizon ?? 0) * 1.06 : undefined,
            splitNumber: 4,
            splitLine: {
              show: true,
              lineStyle: { color: 'rgba(241,232,210,0.08)', type: [2, 4] },
            },
          },
          {
            type: 'value',
            gridIndex: spendGridIndex,
            name: '',
            axisLabel: {
              formatter: (value: number) => fmtAxisUsd(value),
              color: 'rgba(158,170,173,0.62)',
              fontSize: 10,
            },
            min: 0,
            splitNumber: 3,
            splitLine: {
              show: true,
              lineStyle: { color: 'rgba(241,232,210,0.06)', type: [2, 4] },
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
            smooth: false,
            step: 'middle',
            symbol: 'rect',
            symbolSize: dates.length > 72 ? 0 : 5,
            showSymbol: dates.length <= 72,
            lineStyle: { width: 2, color: lineColor },
            itemStyle: {
              color: lineColor,
              borderColor: '#07131f',
              borderWidth: 1,
            },
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
          ...(recordDayIndex >= 0
            ? [
                {
                  id: 'record-beacon',
                  name: 'Record day',
                  type: 'scatter' as const,
                  xAxisIndex: recordBeaconXAxisIndex,
                  yAxisIndex: recordBeaconYAxisIndex,
                  symbol: 'diamond' as const,
                  symbolSize: 11,
                  clip: false,
                  itemStyle: {
                    color: '#f4b740',
                    borderColor: '#fff7ed',
                    borderWidth: 2,
                    shadowBlur: 12,
                    shadowColor: 'rgba(217,154,43,0.5)',
                  },
                  silent: true,
                  tooltip: { show: false },
                  label: {
                    show: true,
                    position: recordPlaquePosition,
                    distance: 9,
                    align: recordPlaquePosition === 'left' ? 'right' : 'left',
                    verticalAlign: 'middle',
                    color: '#07131f',
                    fontSize: 12,
                    fontWeight: 700,
                    backgroundColor: '#ffc84a',
                    borderColor: '#07131f',
                    borderWidth: 2,
                    borderRadius: 0,
                    padding: [4, 7],
                    overflow: 'none',
                    formatter: () => `◆ RECORD\n${fmtExact(recordValue)}`,
                  },
                  z: 21,
                  data: [[recordDayIndex, recordBeaconValue]],
                },
              ]
            : []),
          {
            id: 'run-cursor',
            name: 'Current run position',
            type: 'scatter',
            xAxisIndex: tokenXAxisIndex,
            yAxisIndex: tokenGridIndex,
            symbol: 'rect',
            symbolSize: [20, 16],
            silent: true,
            tooltip: { show: false },
            itemStyle: {
              color: '#ffc84a',
              borderColor: '#07131f',
              borderWidth: 2,
            },
            label: {
              show: true,
              position: 'inside',
              color: '#07131f',
              fontFamily: 'ui-monospace, "SFMono-Regular", Consolas, monospace',
              fontSize: 11,
              fontWeight: 800,
              formatter: '>_',
            },
            z: 30,
            data:
              latestRunIndex >= 0
                ? [[latestRunIndex, terrainMax * 0.045]]
                : [],
          },
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
    ' Record days, when present, extend into the upper stage and show their exact totals; exact daily data remains available below.'
  const recordScope =
    focusedAccessibleSeries && activeTool
      ? `${activeTool.label} / ${focusedAccessibleSeries.label}`
      : activeTool?.label ?? 'All tools'
  const scopedDailyTokens = daily.map((row) =>
    activeTool && focusedAccessibleSeries
      ? modelSeriesPoint(row, activeTool.id, focusedAccessibleSeries).tokens
      : activeTool
        ? num(row, activeTool.tokenKey)
        : TOOLS.reduce((sum, tool) => sum + num(row, tool.tokenKey), 0),
  )
  const runRecord = findRunRecord(
    scopedDailyTokens,
    daily.map((row) => row.date),
  )
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
        className="chart-run-record"
        role="group"
        aria-label={`Run record for ${recordScope}`}
        data-record-state={runRecord ? 'recorded' : 'empty'}
      >
        <span className="chart-run-record__title">RUN RECORD</span>
        <strong className="chart-run-record__value">
          {fmtExact(runRecord?.value ?? 0)}
        </strong>
        <span className="chart-run-record__meta">
          {recordScope} · {runRecord?.date ?? 'No recorded day'}
        </span>
      </div>
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
