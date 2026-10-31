<script lang="ts">
  import { scaleLinear } from 'd3-scale'
  import { line } from 'd3-shape'
  import { fmtCompact, fmtExactTokens, fmtExactUsd, fmtUsd } from '../lib/format'
  import type { ReportDay, ReportSeries } from '../lib/report'

  interface Props {
    daily: ReportDay[]
    series: ReportSeries[]
    selectedDate: string | null
    onSelectDate: (date: string) => void
  }

  let { daily, series, selectedDate, onSelectDate }: Props = $props()
  let width = $state(640)
  let hoverIndex = $state<number | null>(null)
  let gesture: { pointerId: number; x: number; y: number } | null = null

  const height = 460
  const left = 72
  const right = 18
  const top = 38
  const tokenBottom = 292
  const costTop = 350
  const costBottom = 414

  const chartWidth = $derived(Math.max(180, width || 640))
  const plotWidth = $derived(Math.max(1, chartWidth - left - right))
  const x = $derived(
    scaleLinear()
      .domain([0, Math.max(1, daily.length - 1)])
      .range([left + 4, chartWidth - right - 4]),
  )
  const maximumTokens = $derived(Math.max(0, ...daily.map((day) => day.tokens ?? 0)))
  const maximumCost = $derived(Math.max(0, ...daily.map((day) => day.cost ?? 0)))
  const minimumCost = $derived(Math.min(0, ...daily.map((day) => day.cost ?? 0)))
  const tokenScale = $derived(
    scaleLinear().domain([0, maximumTokens || 1]).nice(3).range([tokenBottom, top]),
  )
  const costScale = $derived(
    scaleLinear()
      .domain([minimumCost, maximumCost || 1])
      .nice(2)
      .range([costBottom, costTop]),
  )
  const barWidth = $derived(Math.min(22, (plotWidth / Math.max(1, daily.length)) * 0.76))
  const selectedIndex = $derived(Math.max(0, daily.findIndex((day) => day.date === selectedDate)))
  const activeIndex = $derived(
    hoverIndex !== null && hoverIndex < daily.length ? hoverIndex : selectedIndex,
  )
  const hoveredDay = $derived(hoverIndex === null ? null : daily[hoverIndex] ?? null)
  const activeDay = $derived(daily[activeIndex] ?? null)
  const tokenTicks = $derived(tokenScale.ticks(3))
  const costTicks = $derived(costScale.ticks(2))
  const tickIndices = $derived.by(() => {
    const count = Math.min(daily.length, chartWidth < 500 ? 4 : 6)
    return Array.from({ length: count }, (_, index) =>
      count === 1 ? 0 : Math.round((index * (daily.length - 1)) / (count - 1)),
    )
  })
  const bars = $derived.by(() =>
    daily.flatMap((day, index) => {
      if (day.tokens === null) return []
      let total = 0
      return day.values.flatMap((value, seriesIndex) => {
        if (value === null || value <= 0) return []
        const bottom = tokenScale(total)
        total += value
        const y = tokenScale(total)
        return [{
          id: `${day.date}:${seriesIndex}`,
          x: x(index) - barWidth / 2,
          y,
          height: Math.max(0, bottom - y),
          color: series[seriesIndex]?.color ?? '#86766c',
        }]
      })
    }),
  )
  const costPath = $derived(
    line<ReportDay>()
      .defined((day) => day.cost !== null)
      .x((_day, index) => x(index))
      .y((day) => costScale(day.cost ?? 0))(daily) ?? '',
  )
  const recordIndex = $derived.by(() => {
    let index = -1
    let maximum = 0
    daily.forEach((day, candidate) => {
      if (day.tokens !== null && day.tokens > maximum) {
        maximum = day.tokens
        index = candidate
      }
    })
    return index
  })
  const tooltipLeft = $derived(
    Math.max(0, Math.min(x(activeIndex) - 110, chartWidth - Math.min(270, chartWidth))),
  )

  function pointerIndex(event: PointerEvent): number {
    const svg = event.currentTarget as SVGSVGElement
    const bounds = svg.getBoundingClientRect()
    const local = ((event.clientX - bounds.left) / bounds.width) * chartWidth
    return Math.max(0, Math.min(daily.length - 1, Math.round(x.invert(local))))
  }

  function previewDate(event: PointerEvent) {
    if (daily.length && event.pointerType !== 'touch') hoverIndex = pointerIndex(event)
  }

  function startGesture(event: PointerEvent) {
    gesture = { pointerId: event.pointerId, x: event.clientX, y: event.clientY }
  }

  function selectDate(event: PointerEvent) {
    const start = gesture
    gesture = null
    if (!start || start.pointerId !== event.pointerId) return
    if (Math.hypot(event.clientX - start.x, event.clientY - start.y) > 8) return
    if (!daily.length) return
    const index = pointerIndex(event)
    hoverIndex = null
    onSelectDate(daily[index].date)
  }
</script>

<figure class="usage-chart" bind:clientWidth={width}>
  {#if daily.length}
    <div class="chart-canvas">
      <svg
        viewBox={`0 0 ${chartWidth} ${height}`}
        role="img"
        aria-label="Usage chart"
        aria-describedby="usage-chart-help"
        onpointermove={previewDate}
        onpointerleave={() => { hoverIndex = null; gesture = null }}
        onpointerdown={startGesture}
        onpointerup={selectDate}
        onpointercancel={() => { gesture = null }}
      >
        <title>Daily recorded tokens and estimated cost</title>
        <desc>Series: {series.map((item) => item.label).join(', ')}. Stacked token columns use a continuous linear scale. Costs use their own aligned USD scale. Unavailable dates remain gaps; exact values are in the date inspection below.</desc>
        <text class="axis-heading" x={left} y="15">RECORDED TOKENS</text>
        {#each tokenTicks as value (value)}
          <line class="grid-line" x1={left} x2={chartWidth - right} y1={tokenScale(value)} y2={tokenScale(value)} />
          <text class="axis-tick" x={left - 9} y={tokenScale(value) + 4} text-anchor="end">{fmtCompact(value)}</text>
        {/each}
        {#each bars as bar (bar.id)}
          <rect x={bar.x} y={bar.y} width={barWidth} height={bar.height} fill={bar.color} />
        {/each}
        {#each daily as day, index (day.date)}
          {#if day.tokens === null}
            <line class="missing-day" x1={x(index)} x2={x(index)} y1={tokenBottom - 7} y2={tokenBottom} />
          {/if}
        {/each}
        {#if recordIndex >= 0}
          <text
            class="record-label"
            x={x(recordIndex)}
            y={Math.max(29, tokenScale(maximumTokens) - 9)}
            text-anchor={x(recordIndex) > chartWidth * 0.68 ? 'end' : 'start'}
          >RECORD {fmtCompact(maximumTokens)}</text>
        {/if}
        <text class="axis-heading" x={left} y={costTop - 20}>ESTIMATED COST · USD</text>
        {#each costTicks as value (value)}
          <line class="cost-grid" x1={left} x2={chartWidth - right} y1={costScale(value)} y2={costScale(value)} />
          <text class="axis-tick" x={left - 9} y={costScale(value) + 4} text-anchor="end">{fmtUsd(value)}</text>
        {/each}
        <path class="cost-line" d={costPath} />
        {#if daily.length <= 90}
          {#each daily as day, index (day.date)}
            {#if day.cost !== null}
              <circle class="cost-point" cx={x(index)} cy={costScale(day.cost)} r="2.5" />
            {/if}
          {/each}
        {/if}
        {#if activeDay}
          <line class="date-guide" x1={x(activeIndex)} x2={x(activeIndex)} y1={top} y2={costBottom} />
          {#if activeDay.cost !== null}
            <circle class="active-point" cx={x(activeIndex)} cy={costScale(activeDay.cost)} r="4" />
          {/if}
        {/if}
        {#each tickIndices as index, tick (index)}
          <text
            class="axis-tick"
            x={x(index)}
            y={height - 24}
            text-anchor={tick === 0 ? 'start' : tick === tickIndices.length - 1 ? 'end' : 'middle'}
          >{daily[index].date.slice(5)}</text>
        {/each}
        <text class="axis-heading" x={(left + chartWidth - right) / 2} y={height - 4} text-anchor="middle">CALENDAR DATE</text>
      </svg>
      {#if hoveredDay}
        <div class="chart-tooltip" role="tooltip" style:left={`${tooltipLeft}px`}>
          <strong>{hoveredDay.date}</strong>
          {#if hoveredDay.tokens === null}
            <span>No published record</span>
          {:else}
            {#each hoveredDay.breakdown as item (item.id)}
              <span class="tooltip-row"><span>{item.label}</span><b>{fmtExactTokens(item.tokens)}</b></span>
            {/each}
            <span class="tooltip-total">{fmtExactTokens(hoveredDay.tokens)} tokens · {fmtExactUsd(hoveredDay.cost)}</span>
          {/if}
        </div>
      {/if}
    </div>
  {:else}
    <p class="chart-empty">No published usage in this window.</p>
  {/if}
  <figcaption id="usage-chart-help">Select a date in the chart, or use the date index below. Missing publication dates remain gaps.</figcaption>
</figure>

<style>
  .usage-chart { width: 100%; min-width: 0; margin: 0; }
  .chart-canvas { position: relative; min-width: 0; }
  svg { display: block; width: 100%; height: auto; overflow: visible; touch-action: pan-y; }
  svg text { fill: #6e6b61; font: 11px/1 Arial, sans-serif; }
  svg .axis-heading { fill: #171713; font-size: 11px; font-weight: 600; letter-spacing: 1px; }
  .grid-line { stroke: #c5bfb0; stroke-width: 1; }
  .cost-grid { stroke: #d8d1c2; stroke-width: 0.6; }
  .cost-line { fill: none; stroke: #171713; stroke-width: 1.4; }
  .cost-point { fill: #171713; }
  .active-point { fill: #f1eee4; stroke: #a73d2e; stroke-width: 1.5; }
  .date-guide { stroke: #a73d2e; stroke-width: 1; stroke-dasharray: 3 3; pointer-events: none; }
  .missing-day { stroke: #a89c87; stroke-width: 1; }
  svg .record-label { fill: #a73d2e; font-size: 11px; font-weight: 600; letter-spacing: 0.4px; }
  .chart-tooltip { position: absolute; top: 38px; z-index: 2; width: 270px; max-width: 100%; box-sizing: border-box; padding: 12px; color: #171713; background: #f8f4e9; border: 1px solid #b9ad97; box-shadow: 0 4px 18px #17171314; font: 12px/1.5 Arial, sans-serif; pointer-events: none; }
  .chart-tooltip strong, .chart-tooltip > span { display: block; }
  .chart-tooltip strong { margin-bottom: 6px; }
  .tooltip-row { display: flex !important; justify-content: space-between; gap: 12px; }
  .tooltip-row span { min-width: 0; overflow-wrap: anywhere; }
  .tooltip-row b { white-space: nowrap; font-weight: 500; font-variant-numeric: tabular-nums; }
  .tooltip-total { padding-top: 6px; margin-top: 6px; border-top: 1px solid #d8d1c2; font-variant-numeric: tabular-nums; }
  figcaption { padding-top: 10px; color: #6e6b61; font: 11px/1.5 Arial, sans-serif; }
  .chart-empty { min-height: 180px; display: grid; place-items: center; }
  @media (forced-colors: active) { .grid-line, .cost-grid, .cost-line, .date-guide { stroke: CanvasText; } svg text { fill: CanvasText; } .chart-tooltip { border-color: CanvasText; background: Canvas; color: CanvasText; } }
</style>
