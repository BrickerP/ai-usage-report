<script lang="ts">
  import { onMount, tick } from 'svelte'
  import UsageChart from './components/UsageChart.svelte'
  import {
    buildReport,
    buildReportSearch,
    parseReportView,
    shiftReportView,
    type ModelReportEntry,
    type ReportModel,
    type ReportPreset,
    type ReportView,
  } from './lib/report'
  import { loadUsage, type ToolId, type UsagePayload } from './lib/usage'

  const initialSearch = typeof window === 'undefined' ? '' : window.location.search
  const initialView = parseReportView(initialSearch)
  const PRESETS: Array<Exclude<ReportPreset, 'custom'>> = ['7', '30', '90', 'all']

  let payload = $state<UsagePayload | null>(null)
  let view = $state<ReportView>(initialView)
  let status = $state<'loading' | 'ready' | 'error'>('loading')
  let loadError = $state('')
  let requestId = 0
  let customFrom = $state(initialView.from ?? '')
  let customTo = $state(initialView.to ?? '')
  let modelIndexOpen = $state(false)
  let ledgerPage = $state(0)
  let reportDetailsOpen = $state(false)
  let reportDetailsElement = $state<HTMLDetailsElement | null>(null)
  let modelIndexTrigger = $state<HTMLButtonElement | null>(null)
  let previousScopeKey = ''

  let report: ReportModel | null = $derived(
    payload ? buildReport(payload, view) : null,
  )
  let selectedDay = $derived(report?.selectedDay ?? null)
  let selectedIndex = $derived(
    report && selectedDay
      ? Math.max(
          0,
          report.daily.findIndex((day) => day.date === selectedDay?.date),
        )
      : 0,
  )
  let ledgerPages = $derived(
    report ? Math.max(1, Math.ceil(report.daily.length / 50)) : 1,
  )
  let ledgerRows = $derived(
    report
      ? [...report.daily]
          .reverse()
          .slice(ledgerPage * 50, ledgerPage * 50 + 50)
      : [],
  )
  let displayedParts = $derived(
    report
      ? (report.parts.date ??
          report.parts.range.map((part) => ({ ...part, tokens: Number.NaN })))
      : [],
  )
  let allToolsTokens = $derived.by(() => {
    if (!report || report.tools.every((tool) => tool.tokens === null)) return null
    return report.tools.reduce((sum, tool) => sum + (tool.tokens ?? 0), 0)
  })
  let sliderValueText = $derived.by(() => {
    if (!selectedDay) return 'No date selected'
    if (!selectedDay.published || selectedDay.tokens === null) {
      return `${selectedDay.date}, unavailable, no published record`
    }
    const stateLabel = selectedDay.tokens === 0 ? 'published zero' : 'recorded'
    return `${selectedDay.date}, ${stateLabel}, ${exact(selectedDay.tokens)} tokens, ${usd(selectedDay.cost, true)}`
  })
  let liveStatus = $derived(
    report && selectedDay
      ? `${report.scope.label}. ${sliderValueText}`
      : 'Usage report loading',
  )

  $effect(() => {
    if (!report || typeof window === 'undefined') return
    const next = buildReportSearch(window.location.search, report.view)
    if (next !== window.location.search) {
      window.history.replaceState(
        window.history.state,
        '',
        `${window.location.pathname}${next}${window.location.hash}`,
      )
    }
  })

  $effect(() => {
    if (!report) return
    const key = [
      report.view.preset,
      report.range.start,
      report.range.end,
      report.scope.tool ?? 'all',
      report.scope.model ?? 'all',
    ].join('|')
    if (key === previousScopeKey) return
    previousScopeKey = key
    ledgerPage = 0
    modelIndexOpen = false
    customFrom = report.view.from ?? ''
    customTo = report.view.to ?? ''
  })

  onMount(() => {
    void refresh()
  })

  async function refresh() {
    const current = ++requestId
    status = 'loading'
    loadError = ''
    try {
      const next = await loadUsage()
      if (current !== requestId) return
      payload = next
      status = 'ready'
    } catch (error) {
      if (current !== requestId) return
      loadError = error instanceof Error ? error.message : 'Unable to load usage data.'
      status = 'error'
    }
  }

  function handlePopstate() {
    view = parseReportView(window.location.search)
    customFrom = view.from ?? ''
    customTo = view.to ?? ''
    ledgerPage = 0
  }

  function setRange(preset: Exclude<ReportPreset, 'custom'>) {
    const current = report?.view ?? view
    view = {
      ...current,
      preset,
      from: null,
      to: null,
      anchor: null,
      day: null,
    }
  }

  function applyCustomRange() {
    const current = report?.view ?? view
    view = {
      ...current,
      preset: 'custom',
      from: customFrom || null,
      to: customTo || null,
      anchor: null,
      day: null,
    }
  }

  function shiftRange(direction: -1 | 1) {
    if (!payload || !report) return
    view = shiftReportView(payload, report.view, direction)
  }

  function resetWindow() {
    if (!report) return
    view = {
      ...report.view,
      anchor: null,
      day: null,
      from: report.view.preset === 'custom' ? null : report.view.from,
      to: report.view.preset === 'custom' ? null : report.view.to,
      preset: report.view.preset === 'custom' ? 'all' : report.view.preset,
    }
  }

  function selectTool(tool: ToolId | null) {
    const current = report?.view ?? view
    view = { ...current, tool, model: null, day: null }
    modelIndexOpen = false
  }

  function selectModel(entry: ModelReportEntry) {
    if (!entry.selectable || !report) return
    view = { ...report.view, model: entry.name, day: null }
    modelIndexOpen = false
  }

  function clearModel() {
    if (!report) return
    view = { ...report.view, model: null, day: null }
  }

  function selectDay(date: string) {
    if (!report?.daily.some((day) => day.date === date)) return
    view = { ...report.view, day: date }
  }

  function selectDayAt(index: number) {
    if (!report?.daily.length) return
    const clamped = Math.max(0, Math.min(report.daily.length - 1, index))
    selectDay(report.daily[clamped].date)
  }

  function jumpTo(mark: 'first' | 'record' | 'last') {
    const date = report?.bookmarks[mark]
    if (date) selectDay(date)
  }

  function handleSlider(event: Event) {
    selectDayAt((event.currentTarget as HTMLInputElement).valueAsNumber)
  }

  async function toggleModelIndex() {
    if (modelIndexOpen) {
      modelIndexOpen = false
      await tick()
      modelIndexTrigger?.focus()
      return
    }
    modelIndexOpen = true
    await tick()
    document
      .querySelector<HTMLButtonElement>(
        '#model-index-region button[data-model]:not(:disabled)',
      )
      ?.focus()
  }

  function handleModelIndexKeydown(event: KeyboardEvent) {
    if (!['ArrowDown', 'ArrowRight', 'ArrowUp', 'ArrowLeft', 'Home', 'End'].includes(event.key)) {
      return
    }
    const button = event.currentTarget as HTMLButtonElement
    const region = button.closest('[data-model-index-region]')
    const buttons = Array.from(
      region?.querySelectorAll<HTMLButtonElement>('button[data-model]:not(:disabled)') ?? [],
    )
    const index = buttons.indexOf(button)
    if (index < 0 || !buttons.length) return
    event.preventDefault()
    const next =
      event.key === 'Home'
        ? 0
        : event.key === 'End'
          ? buttons.length - 1
          : (index + (['ArrowDown', 'ArrowRight'].includes(event.key) ? 1 : -1) + buttons.length) %
            buttons.length
    buttons[next]?.focus()
  }

  function handleGlobalKeydown(event: KeyboardEvent) {
    if (event.key !== 'Escape') return
    if (modelIndexOpen) {
      modelIndexOpen = false
      modelIndexTrigger?.focus()
      event.preventDefault()
      return
    }
    if (report?.scope.kind === 'model') {
      clearModel()
      event.preventDefault()
      return
    }
    if (report?.scope.kind === 'tool') {
      selectTool(null)
      event.preventDefault()
    }
  }

  function openReportDetails() {
    reportDetailsOpen = true
    requestAnimationFrame(() => reportDetailsElement?.scrollIntoView({ block: 'nearest' }))
  }

  function sourceStatus(tool: ToolId) {
    return report?.sources.all.find((source) => source.id === tool)
  }

  function compact(value: number | null) {
    if (value === null || !Number.isFinite(value)) return '—'
    if (value >= 1e9) return `${(value / 1e9).toFixed(value >= 1e11 ? 1 : 2)}B`
    if (value >= 1e6) return `${(value / 1e6).toFixed(2)}M`
    if (value >= 1e3) return `${(value / 1e3).toFixed(1)}K`
    return Math.round(value).toLocaleString()
  }

  function exact(value: number | null) {
    return value === null ? '—' : Math.round(value).toLocaleString()
  }

  function usd(value: number | null, precise = false) {
    if (value === null || !Number.isFinite(value)) return '—'
    const digits = precise ? 8 : Math.abs(value) > 0 && Math.abs(value) < 0.01 ? 4 : 2
    return `$${value.toLocaleString(undefined, {
      minimumFractionDigits: precise ? 2 : digits,
      maximumFractionDigits: digits,
    })}`
  }

  function percentage(value: number | null) {
    return value === null ? '—' : `${(value * 100).toFixed(1)}%`
  }

  function displayTimestamp(value?: string | null) {
    return value ? value.replace('T', ' ').slice(0, 16) : 'Unknown'
  }
</script>

<svelte:window onpopstate={handlePopstate} onkeydown={handleGlobalKeydown} />

{#if status === 'loading'}
  <main class="state-page" aria-busy="true" aria-live="polite">
    <div class="state-block">
      <p class="yearbook-kicker">Personal systems almanac</p>
      <h1>Preparing the ledger…</h1>
      <div class="loading-rule" aria-hidden="true"></div>
    </div>
  </main>
{:else if status === 'error'}
  <main class="state-page" role="alert">
    <div class="state-block">
      <p class="yearbook-kicker">Archive unavailable</p>
      <h1>The ledger could not be opened.</h1>
      <p>{loadError}</p>
      <button class="ink-button" onclick={refresh}>Retry loading</button>
    </div>
  </main>
{:else if !payload || !report || !payload.daily.length}
  <main class="state-page">
    <div class="state-block">
      <p class="yearbook-kicker">Empty archive</p>
      <h1>No published usage dates.</h1>
      <p>The collector returned an empty daily ledger.</p>
    </div>
  </main>
{:else}
  <main class="yearbook" data-testid="yearbook-root">
    <header class="masthead">
      <div>
        <p class="yearbook-kicker">Personal systems almanac</p>
        <div class="publication-name">The Compute Ledger</div>
      </div>
      <div class="issue-meta">
        Archive edition<br />
        {displayTimestamp(payload.generated_at)}<br />
        {report.sources.degraded.length
          ? `${report.sources.degraded.length} sources delayed`
          : 'All sources current'}
      </div>
    </header>

    <section class="lead-section">
      <div class="scope-stat">
        <p class="section-label">Current scope</p>
        <p class="scope-name">{report.scope.label} · {report.range.preset === 'all' ? 'All time' : report.range.preset === 'custom' ? 'Custom range' : `Last ${report.range.preset} days`}</p>
        <p class="hero-total" data-testid="hero-total" data-value={report.scope.tokens ?? ''}>{compact(report.scope.tokens)}</p>
        <p class="exact-total" data-testid="scope-total" data-value={report.scope.tokens ?? ''}>
          {report.scope.tokens === null ? 'No recorded data' : `${exact(report.scope.tokens)} recorded tokens`}
        </p>
        <div class="paired-stats">
          <div>
            <strong data-testid="hero-cost" data-value={report.scope.cost ?? ''}>{usd(report.scope.cost)}</strong>
            <span>Estimated cost</span>
            <span class="raw-value" data-testid="scope-cost" data-value={report.scope.cost ?? ''}>
              {report.scope.cost === null ? 'No recorded cost' : `${usd(report.scope.cost, true)} exact`}
            </span>
          </div>
          <div>
            <strong>{percentage(report.scope.cacheShare)}</strong>
            <span>{report.scope.kind === 'model' ? 'Tool-level cache share' : 'Cache share'}</span>
          </div>
        </div>
      </div>

      <div class="range-editor">
        <p class="section-label">Reading window</p>
        <h1>Usage, arranged<br />as a living record.</h1>
        <div class="range-presets" aria-label="Time range">
          {#each PRESETS as preset}
            <button
              type="button"
              aria-pressed={report.range.preset === preset}
              onclick={() => setRange(preset)}
            >
              {preset === 'all' ? 'All' : `${preset} days`}
            </button>
          {/each}
          <button
            type="button"
            aria-pressed={report.range.preset === 'custom'}
            onclick={applyCustomRange}
          >Custom</button>
        </div>
        <p class="range-meta">
          {report.range.start} — {report.range.end} · {report.range.calendarDays} calendar days ·
          {report.range.recordedDays} recorded days
        </p>
        <div class="window-actions">
          <button type="button" disabled={!report.range.canShiftEarlier} onclick={() => shiftRange(-1)}>← Earlier</button>
          <button type="button" disabled={!report.range.canShiftLater} onclick={() => shiftRange(1)}>Later →</button>
          <button type="button" onclick={resetWindow}>Reset window</button>
        </div>
        <div class="custom-range">
          <label>
            From
            <input
              aria-label="From"
              type="date"
              min={payload.daily[0]?.date}
              max={payload.daily.at(-1)?.date}
              bind:value={customFrom}
            />
          </label>
          <label>
            To
            <input
              aria-label="To"
              type="date"
              min={payload.daily[0]?.date}
              max={payload.daily.at(-1)?.date}
              bind:value={customTo}
            />
          </label>
          <button type="button" class="ink-button" onclick={applyCustomRange}>Apply custom range</button>
        </div>
      </div>
    </section>

    <section class="instrument-register" aria-labelledby="instrument-heading">
      <div class="tool-strip">
        <h2 id="instrument-heading">Instrument register</h2>
        <button
          type="button"
          class="tool-choice"
          aria-pressed={report.scope.tool === null}
          onclick={() => selectTool(null)}
        >
          <strong>All tools</strong>
          <span>{compact(allToolsTokens)}</span>
        </button>
        {#each report.tools as tool}
          <button
            type="button"
            class="tool-choice"
            data-tool={tool.id}
            aria-pressed={report.scope.tool === tool.id}
            onclick={() => selectTool(tool.id)}
          >
            <strong>{tool.label}</strong>
            <span>{compact(tool.tokens)} · {usd(tool.cost)}</span>
            {#if sourceStatus(tool.id)?.status !== 'fresh'}
              <small>{sourceStatus(tool.id)?.status}</small>
            {/if}
          </button>
        {/each}
      </div>
    </section>

    {#if report.models}
      <section class="model-folio" aria-labelledby="model-heading">
        <div class="model-heading-row">
          <div>
            <p class="section-label">Model folio</p>
            <h2 id="model-heading">{report.models.selected ?? `${report.scope.label} models`}</h2>
          </div>
          {#if report.models.selected}
            <button type="button" class="line-button" onclick={clearModel}>Clear model focus</button>
          {/if}
        </div>
        <div class="top-models">
          {#each report.models.top as model}
            <button
              type="button"
              class="model-chip"
              data-model={model.name}
              aria-pressed={model.selected}
              disabled={!model.selectable}
              onclick={() => selectModel(model)}
            >
              {model.name} · {compact(model.tokens)}
            </button>
          {/each}
          <button
            bind:this={modelIndexTrigger}
            type="button"
            class="model-chip"
            aria-expanded={modelIndexOpen}
            aria-controls="model-index-region"
            onclick={toggleModelIndex}
          >Model index</button>
        </div>
        {#if modelIndexOpen}
          <section
            id="model-index-region"
            class="model-index"
            data-model-index-region
            aria-label="Model index"
          >
            {#each report.models.full as model}
              <button
                type="button"
                data-model={model.name}
                aria-pressed={model.selected}
                disabled={!model.selectable}
                onclick={() => selectModel(model)}
                onkeydown={handleModelIndexKeydown}
              >
                <span class="model-name">{model.name}{model.selected ? ' · Selected' : ''}</span>
                <span>{exact(model.tokens)} · {usd(model.cost, true)}</span>
                <span class="model-share">{percentage(model.share)}</span>
              </button>
            {/each}
          </section>
        {/if}
        {#if report.models.other}
          <p class="other-models">
            Other groups {report.models.other.count} models · {exact(report.models.other.tokens)} tokens ·
            {usd(report.models.other.cost, true)}
          </p>
        {/if}
      </section>
    {/if}

    <section class="analysis-section">
      <aside class="pattern-note">
        <p class="section-label">Pattern note</p>
        <h2>{report.pattern?.signature.name ?? 'History still forming'}</h2>
        <p>{report.pattern?.signature.evidence ?? 'No active dates in this scope.'}</p>
        <ul>
          <li><button type="button" onclick={() => jumpTo('first')} disabled={!report.bookmarks.first}><b>First</b><span>{report.bookmarks.first ?? '—'}</span></button></li>
          <li><button type="button" onclick={() => jumpTo('record')} disabled={!report.bookmarks.record}><b>Record</b><span>{report.bookmarks.record ?? '—'}</span></button></li>
          <li><button type="button" onclick={() => jumpTo('last')} disabled={!report.bookmarks.last}><b>Last</b><span>{report.bookmarks.last ?? '—'}</span></button></li>
        </ul>
      </aside>
      <div class="chart-column">
        <div class="chart-heading">
          <div>
            <p class="section-label">Daily register</p>
            <h2>{report.scope.label}</h2>
          </div>
          <div class="scope-subtotal">
            <strong>{report.scope.tokens === null ? 'No recorded data' : `${exact(report.scope.tokens)} tokens`}</strong>
            <span>{report.scope.cost === null ? '—' : usd(report.scope.cost, true)}</span>
            <span>{percentage(report.scope.cacheShare)} {report.scope.kind === 'model' ? 'tool cache share' : 'cache share'}</span>
          </div>
        </div>
        <UsageChart
          daily={report.daily}
          series={report.series}
          selectedDate={selectedDay?.date ?? null}
          onSelectDate={selectDay}
        />
        <div class="date-navigation">
          <label for="inspect-date">Inspect date</label>
          <input
            id="inspect-date"
            aria-label="Inspect date"
            type="range"
            min="0"
            max={Math.max(0, report.daily.length - 1)}
            value={selectedIndex}
            aria-valuetext={sliderValueText}
            oninput={handleSlider}
          />
          <div>
            <button type="button" onclick={() => selectDayAt(0)}>First date</button>
            <button type="button" onclick={() => selectDayAt(selectedIndex - 1)}>Previous day</button>
            <button type="button" onclick={() => selectDayAt(selectedIndex + 1)}>Next day</button>
            <button type="button" onclick={() => jumpTo('record')}>Jump to peak</button>
            <button type="button" onclick={() => selectDayAt(report.daily.length - 1)}>Latest date</button>
          </div>
        </div>
      </div>
    </section>

    <section class="date-inspector">
      <div>
        <p class="section-label">Selected date · exact record</p>
        <div class="selected-date" data-testid="selected-date" data-value={selectedDay?.date ?? ''}>{selectedDay?.date ?? '—'}</div>
        <p>
          {#if !selectedDay?.published}
            Unavailable · no published row
          {:else if selectedDay.tokens === 0}
            Published zero · 0 tokens · $0.00
          {:else}
            Recorded · {exact(selectedDay?.tokens ?? null)} tokens · {usd(selectedDay?.cost ?? null, true)}
          {/if}
        </p>
      </div>
      <div>
        <p class="section-label">
          Token composition · selected date{report.scope.kind === 'model' ? ' · tool scope' : ''}
        </p>
        <div class="token-parts">
          {#each displayedParts as part}
            <div style={`--part:${report.series.find((series) => series.id === part.id)?.color ?? '#8c8270'}`}>
              <strong>{Number.isFinite(part.tokens) ? compact(part.tokens) : '—'}</strong>
              <span>{part.label}</span>
            </div>
          {/each}
        </div>
        <p class="parts-note">{report.parts.note}</p>
        <details class="date-breakdown" open>
          <summary>Exact source/model breakdown</summary>
          <div class="table-scroll">
            <table>
              <thead><tr><th>Series</th><th>Recorded tokens</th><th>Estimated cost</th></tr></thead>
              <tbody>
                {#each selectedDay?.breakdown ?? [] as row}
                  <tr><td>{row.label}</td><td>{exact(row.tokens)}</td><td>{usd(row.cost, true)}</td></tr>
                {/each}
              </tbody>
            </table>
          </div>
        </details>
      </div>
    </section>

    <section class="disclosures">
      <details data-testid="exact-ledger">
        <summary>Exact ledger</summary>
        <div class="table-scroll">
          <table>
            <thead><tr><th>Date</th><th>State</th><th>Recorded tokens</th><th>Estimated cost</th></tr></thead>
            <tbody>
              {#each ledgerRows as day}
                <tr
                  data-testid="ledger-row"
                  data-date={day.date}
                  data-tokens={day.tokens ?? ''}
                  data-cost={day.cost ?? ''}
                >
                  <td>{day.date}</td>
                  <td>{!day.published ? 'Unavailable' : day.tokens === 0 ? 'Published zero' : 'Recorded'}</td>
                  <td>{exact(day.tokens)}</td>
                  <td>{usd(day.cost, true)}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
        <div class="pagination">
          <button type="button" aria-label="Previous page" disabled={ledgerPage === 0} onclick={() => (ledgerPage -= 1)}>← Previous 50</button>
          <span>Page {ledgerPage + 1} / {ledgerPages} · {report.daily.length} rows</span>
          <button type="button" aria-label="Next page" disabled={ledgerPage >= ledgerPages - 1} onclick={() => (ledgerPage += 1)}>Next 50 →</button>
        </div>
      </details>
      <details
        bind:this={reportDetailsElement}
        bind:open={reportDetailsOpen}
        data-testid="source-details"
      >
        <summary>Report details</summary>
        <div class="report-details">
          <div>
            <h3>Source freshness</h3>
            <ul>
              {#each report.sources.all as source}
                <li>
                  <strong>{source.label}</strong> · {source.status} · through {source.windowEnd ?? '—'} ·
                  lag {source.lagDays ?? '—'}d · last success {source.lastSuccessAt ?? '—'} · attempted
                  {source.attemptedAt ?? '—'}{source.error ? ` · ${source.error}` : ''}
                </li>
              {/each}
            </ul>
          </div>
          <div><h3>Coverage</h3><p>{report.scope.tool ? report.tools.find((tool) => tool.id === report.scope.tool)?.coverage : 'Four mutually exclusive tool series. Missing publication dates remain unavailable rather than zero.'}</p></div>
          <div><h3>Token methodology</h3><p>{report.notes.tokenBreakdown}</p></div>
          <div><h3>Cost methodology</h3><p>{report.notes.cost}</p></div>
          <div><h3>Machines & timezone</h3><p>{payload.timezone ?? '—'} · {(payload.machines ?? []).join(', ') || '—'}</p></div>
          <div><h3>Merge notes</h3><p>{report.notes.merge}</p></div>
        </div>
      </details>
    </section>

    <footer class="yearbook-footer">
      <span>Usage history is not a measure of output, productivity or quality.</span>
      <button type="button" class="line-button" onclick={openReportDetails}>
        {report.sources.degraded.length ? `${report.sources.degraded.length} sources delayed · ` : ''}Report details
      </button>
    </footer>
    <div class="visually-hidden" aria-live="polite" aria-atomic="true">{liveStatus}</div>
  </main>
{/if}
