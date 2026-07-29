import { useEffect, useRef } from 'react'
import * as echarts from 'echarts/core'
import { BarChart, LineChart } from 'echarts/charts'
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  AxisPointerComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { DailyRow } from '../lib/usage'
import { num, TOOLS } from '../lib/usage'
import { fmtCompact, fmtUsd } from '../lib/format'

echarts.use([
  BarChart,
  LineChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  AxisPointerComponent,
  CanvasRenderer,
])

type Props = {
  daily: DailyRow[]
  range: [number, number]
  onRangeChange: (next: [number, number]) => void
}

function mixChannel(c: number, t: number) {
  return Math.round(c + (255 - c) * t)
}

function stackSegStyle(hex: string, cap: 'top' | 'mid' | 'bot') {
  const h = hex.replace('#', '')
  const r = parseInt(h.slice(0, 2), 16)
  const g = parseInt(h.slice(2, 4), 16)
  const b = parseInt(h.slice(4, 6), 16)
  const topRgb = `${mixChannel(r, 0.15)},${mixChannel(g, 0.15)},${mixChannel(b, 0.15)}`
  const rad = 6
  const borderRadius =
    cap === 'top' ? [rad, rad, 0, 0] : cap === 'bot' ? [0, 0, rad, rad] : [0, 0, 0, 0]
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

export function UsageCharts({ daily, range, onRangeChange }: Props) {
  const hostRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<echarts.EChartsType | null>(null)
  const rangeRef = useRef(range)
  rangeRef.current = range

  useEffect(() => {
    if (!hostRef.current) return
    const chart = echarts.init(hostRef.current, undefined, { renderer: 'canvas' })
    chartRef.current = chart

    const onZoom = () => {
      const opt = chart.getOption() as {
        dataZoom?: Array<{ startValue?: number | string; endValue?: number | string; start?: number; end?: number }>
      }
      const dz = opt.dataZoom?.[1] || opt.dataZoom?.[0]
      if (!dz) return
      const dates = daily.map((r) => r.date)
      let i0 = 0
      let i1 = dates.length - 1
      if (typeof dz.startValue === 'string' || typeof dz.endValue === 'string') {
        i0 = Math.max(0, dates.indexOf(String(dz.startValue)))
        i1 = Math.max(0, dates.indexOf(String(dz.endValue)))
      } else if (dz.start != null && dz.end != null && dates.length > 1) {
        i0 = Math.round((dz.start / 100) * (dates.length - 1))
        i1 = Math.round((dz.end / 100) * (dates.length - 1))
      }
      if (i1 < i0) [i0, i1] = [i1, i0]
      const cur = rangeRef.current
      if (cur[0] !== i0 || cur[1] !== i1) onRangeChange([i0, i1])
    }

    chart.on('dataZoom', onZoom)
    const onResize = () => chart.resize()
    window.addEventListener('resize', onResize)

    return () => {
      window.removeEventListener('resize', onResize)
      chart.off('dataZoom', onZoom)
      chart.dispose()
      chartRef.current = null
    }
  }, [daily, onRangeChange])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart || !daily.length) return

    const dates = daily.map((r) => r.date)
    const rotate = dates.length > 36 ? 32 : 0
    const xAxisCommon = {
      type: 'category' as const,
      boundaryGap: true,
      data: dates,
      axisLabel: { rotate, fontSize: 11, color: '#64748b' },
      axisLine: { lineStyle: { color: '#e2e8f0' } },
      axisTick: { alignWithLabel: true, lineStyle: { color: '#e2e8f0' } },
    }

    const stackBar = {
      type: 'bar' as const,
      barCategoryGap: '42%',
      barMaxWidth: 34,
      emphasis: {
        focus: 'series' as const,
        blurScope: 'coordinateSystem' as const,
        itemStyle: {
          shadowBlur: 10,
          shadowColor: 'rgba(15,23,42,0.08)',
          shadowOffsetY: 1,
        },
      },
    }

    const totalDaySpend = daily.map(
      (r) =>
        num(r, 'codex_cost') +
        num(r, 'claude_cost') +
        num(r, 'cursor_cost') +
        num(r, 'oneapi_cost'),
    )

    chart.setOption(
      {
        animation: true,
        textStyle: {
          fontFamily: 'ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif',
        },
        axisPointer: { link: [{ xAxisIndex: [0, 1, 2] }], snap: true },
        tooltip: {
          trigger: 'axis',
          axisPointer: {
            type: 'shadow',
            shadowStyle: { color: 'rgba(15, 23, 42, 0.05)' },
          },
          borderRadius: 10,
          padding: [12, 14],
          backgroundColor: 'rgba(255,255,255,0.98)',
          borderColor: '#e2e8f0',
          borderWidth: 1,
          textStyle: { color: '#0f172a', fontSize: 12 },
          extraCssText: 'box-shadow:0 12px 40px rgba(15,23,42,0.08);',
          formatter: (params: unknown) => {
            const list = params as Array<{ dataIndex: number }>
            if (!list?.length) return ''
            const idx = list[0].dataIndex
            const row = daily[idx]
            if (!row) return ''
            const lines = [`<strong>${row.date}</strong>`]
            for (const tool of TOOLS) {
              lines.push(
                `<div style="margin-top:8px"><span style="color:#64748b">${tool.label}</span></div>`,
              )
              lines.push(
                `Total ${fmtCompact(num(row, tool.tokenKey))} · ${fmtUsd(num(row, tool.costKey))}`,
              )
              for (const part of tool.breakdown) {
                const v = num(row, part.key)
                if (v) lines.push(`${part.label}: ${fmtCompact(v)}`)
              }
            }
            lines.push(
              `<div style="margin-top:8px">Daily spend (all tools): <strong>${fmtUsd(totalDaySpend[idx] || 0)}</strong></div>`,
            )
            return lines.join('<br/>')
          },
        },
        legend: {
          data: [
            'Codex',
            'Claude',
            'Cursor',
            'One API',
            'Codex cache',
            'Claude cache',
            'Cursor cache',
            'One API cache',
            'Daily spend (all tools)',
          ],
          type: 'scroll',
          top: 6,
          left: 'center',
          itemGap: 14,
          itemWidth: 10,
          itemHeight: 10,
          icon: 'circle',
          textStyle: { color: '#64748b', fontSize: 11 },
        },
        grid: [
          { left: 56, right: 48, top: 88, height: '22%' },
          { left: 56, right: 48, top: '40%', height: '22%' },
          { left: 56, right: 48, top: '68%', height: '18%' },
        ],
        xAxis: [
          { ...xAxisCommon, gridIndex: 0, axisLabel: { ...xAxisCommon.axisLabel, margin: 10 } },
          { ...xAxisCommon, gridIndex: 1, axisLabel: { show: false } },
          { ...xAxisCommon, gridIndex: 2, axisLabel: { ...xAxisCommon.axisLabel, margin: 10 } },
        ],
        yAxis: [
          {
            type: 'value',
            gridIndex: 0,
            name: 'Total tokens / day',
            nameTextStyle: { fontSize: 11, color: '#94a3b8', padding: [0, 0, 0, 8] },
            axisLabel: { formatter: (v: number) => fmtCompact(v), color: '#64748b' },
            min: 0,
            splitLine: {
              show: true,
              lineStyle: { color: 'rgba(148,163,184,0.2)', type: [4, 4] },
            },
          },
          {
            type: 'value',
            gridIndex: 1,
            name: 'Cache tokens / day',
            nameTextStyle: { fontSize: 11, color: '#94a3b8', padding: [0, 0, 0, 8] },
            axisLabel: { formatter: (v: number) => fmtCompact(v), color: '#64748b' },
            min: 0,
            splitLine: {
              show: true,
              lineStyle: { color: 'rgba(148,163,184,0.16)', type: [4, 4] },
            },
          },
          {
            type: 'value',
            gridIndex: 2,
            name: 'Total spend / day',
            nameTextStyle: { fontSize: 11, color: '#94a3b8', padding: [0, 0, 0, 8] },
            axisLabel: { formatter: (v: number) => `$${v}`, color: '#64748b' },
            min: 0,
            splitLine: {
              show: true,
              lineStyle: { color: 'rgba(148,163,184,0.14)', type: [4, 4] },
            },
          },
        ],
        dataZoom: [
          {
            type: 'inside',
            xAxisIndex: [0, 1, 2],
            filterMode: 'none',
            zoomOnMouseWheel: false,
            moveOnMouseMove: false,
            moveOnMouseWheel: false,
          },
          {
            type: 'slider',
            xAxisIndex: [0, 1, 2],
            filterMode: 'none',
            height: 36,
            bottom: 20,
            showDetail: true,
            textStyle: { fontSize: 12, color: '#475569' },
            borderColor: '#e2e8f0',
            backgroundColor: '#f8fafc',
            fillerColor: 'rgba(13, 148, 136, 0.14)',
            handleStyle: { color: '#fff', borderColor: '#0f766e', borderWidth: 2 },
          },
        ],
        series: [
          {
            name: 'Codex',
            ...stackBar,
            stack: 'tokens',
            xAxisIndex: 0,
            yAxisIndex: 0,
            itemStyle: stackSegStyle(TOOLS[0].hex, 'bot'),
            data: daily.map((r) => num(r, 'codex_tokens')),
          },
          {
            name: 'Claude',
            ...stackBar,
            stack: 'tokens',
            xAxisIndex: 0,
            yAxisIndex: 0,
            itemStyle: stackSegStyle(TOOLS[1].hex, 'mid'),
            data: daily.map((r) => num(r, 'claude_tokens')),
          },
          {
            name: 'Cursor',
            ...stackBar,
            stack: 'tokens',
            xAxisIndex: 0,
            yAxisIndex: 0,
            itemStyle: stackSegStyle(TOOLS[2].hex, 'mid'),
            data: daily.map((r) => num(r, 'cursor_tokens')),
          },
          {
            name: 'One API',
            ...stackBar,
            stack: 'tokens',
            xAxisIndex: 0,
            yAxisIndex: 0,
            itemStyle: stackSegStyle(TOOLS[3].hex, 'top'),
            data: daily.map((r) => num(r, 'oneapi_tokens')),
          },
          {
            name: 'Codex cache',
            ...stackBar,
            stack: 'cache',
            xAxisIndex: 1,
            yAxisIndex: 1,
            itemStyle: stackSegStyle(TOOLS[0].hex, 'bot'),
            data: daily.map((r) => num(r, 'codex_cache_read')),
          },
          {
            name: 'Claude cache',
            ...stackBar,
            stack: 'cache',
            xAxisIndex: 1,
            yAxisIndex: 1,
            itemStyle: stackSegStyle(TOOLS[1].hex, 'mid'),
            data: daily.map(
              (r) => num(r, 'claude_cache_create') + num(r, 'claude_cache_read'),
            ),
          },
          {
            name: 'Cursor cache',
            ...stackBar,
            stack: 'cache',
            xAxisIndex: 1,
            yAxisIndex: 1,
            itemStyle: stackSegStyle(TOOLS[2].hex, 'mid'),
            data: daily.map(
              (r) => num(r, 'cursor_cache_write') + num(r, 'cursor_cache_read'),
            ),
          },
          {
            name: 'One API cache',
            ...stackBar,
            stack: 'cache',
            xAxisIndex: 1,
            yAxisIndex: 1,
            itemStyle: stackSegStyle(TOOLS[3].hex, 'top'),
            data: daily.map(
              (r) => num(r, 'oneapi_cache_read') + num(r, 'oneapi_cache_write'),
            ),
          },
          {
            name: 'Daily spend (all tools)',
            type: 'line',
            xAxisIndex: 2,
            yAxisIndex: 2,
            smooth: 0.35,
            symbol: 'circle',
            symbolSize: dates.length > 72 ? 0 : 5,
            showSymbol: dates.length <= 72,
            lineStyle: { width: 2.4, color: '#334155' },
            itemStyle: { color: '#334155', borderWidth: 0 },
            areaStyle: {
              color: {
                type: 'linear',
                x: 0,
                y: 0,
                x2: 0,
                y2: 1,
                colorStops: [
                  { offset: 0, color: 'rgba(51,65,85,0.2)' },
                  { offset: 1, color: 'rgba(51,65,85,0.02)' },
                ],
              },
            },
            data: totalDaySpend,
          },
        ],
      },
      { notMerge: true },
    )

    const n = dates.length
    if (n > 0) {
      const i0 = Math.max(0, Math.min(n - 1, range[0]))
      const i1 = Math.max(i0, Math.min(n - 1, range[1]))
      if (n === 1) {
        chart.dispatchAction({ type: 'dataZoom', start: 0, end: 100, xAxisIndex: [0, 1, 2] })
      } else {
        const start = (i0 / (n - 1)) * 100
        const end = (i1 / (n - 1)) * 100
        chart.dispatchAction({
          type: 'dataZoom',
          start,
          end: Math.max(start, end),
          xAxisIndex: [0, 1, 2],
        })
      }
    }
  }, [daily, range])

  return <div className="chart-host" ref={hostRef} />
}
