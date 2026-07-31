import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const CARD_WIDTH = 560
const CARD_HEIGHT = 160
const DAY_MS = 24 * 60 * 60 * 1000

const TOOLS = [
  { id: 'codex', label: 'Codex', key: 'codex_tokens', color: '#2563eb' },
  {
    id: 'claude',
    label: 'Claude Code',
    key: 'claude_tokens',
    color: '#c2410c',
  },
  { id: 'cursor', label: 'Cursor', key: 'cursor_tokens', color: '#0d9488' },
  { id: 'oneapi', label: 'One API', key: 'oneapi_tokens', color: '#7c3aed' },
]

const CACHE_KEYS = [
  'codex_cache_read',
  'claude_cache_create',
  'claude_cache_read',
  'cursor_cache_write',
  'cursor_cache_read',
  'oneapi_cache_read',
  'oneapi_cache_write',
]

const THEMES = {
  light: {
    background: '#ffffff',
    border: '#dbe3ec',
    text: '#0f172a',
    muted: '#64748b',
    grid: '#e2e8f0',
  },
  dark: {
    background: '#0f172a',
    border: '#334155',
    text: '#f8fafc',
    muted: '#94a3b8',
    grid: '#334155',
  },
}

function escapeXml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&apos;')
}

function tokenValue(value) {
  const number = Number(value)
  return Number.isFinite(number) && number > 0 ? number : 0
}

function parsedDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(value))) return null
  const timestamp = Date.parse(`${value}T00:00:00Z`)
  if (!Number.isFinite(timestamp)) return null
  const canonical = new Date(timestamp).toISOString().slice(0, 10)
  return canonical === value ? timestamp : null
}

function normalizedRows(daily) {
  if (!Array.isArray(daily)) return []
  return daily
    .flatMap((row) => {
      const timestamp = parsedDate(row?.date)
      if (timestamp === null) return []
      const tools = TOOLS.map((tool) => tokenValue(row[tool.key]))
      return [
        {
          date: row.date,
          timestamp,
          tools,
          total: tools.reduce((sum, value) => sum + value, 0),
          cache: CACHE_KEYS.reduce(
            (sum, key) => sum + tokenValue(row[key]),
            0,
          ),
        },
      ]
    })
    .sort((left, right) => left.timestamp - right.timestamp)
}

function compactNumber(value) {
  const scales = [
    [1e12, 'T'],
    [1e9, 'B'],
    [1e6, 'M'],
    [1e3, 'K'],
  ]
  for (const [size, suffix] of scales) {
    if (value >= size) {
      const scaled = value / size
      const digits = scaled < 100 ? 1 : 0
      return `${scaled.toFixed(digits).replace(/\.0$/, '')}${suffix}`
    }
  }
  return Math.round(value).toLocaleString('en-US')
}

function shortDate(timestamp, withYear) {
  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    ...(withYear ? { year: 'numeric' } : {}),
    timeZone: 'UTC',
  }).format(new Date(timestamp))
}

function dateSpan(rows) {
  const recorded = rows.filter((row) => row.total > 0)
  if (!recorded.length) return 'No recorded dates'
  const first = recorded[0].timestamp
  const last = recorded.at(-1).timestamp
  if (first === last) return shortDate(first, true)
  const sameYear =
    new Date(first).getUTCFullYear() === new Date(last).getUTCFullYear()
  return sameYear
    ? `${shortDate(first, false)} – ${shortDate(last, true)}`
    : `${shortDate(first, true)} – ${shortDate(last, true)}`
}

function mondayFor(timestamp) {
  const date = new Date(timestamp)
  const daysSinceMonday = (date.getUTCDay() + 6) % 7
  return timestamp - daysSinceMonday * DAY_MS
}

function weeklyTotals(rows) {
  const weeks = new Map()
  for (const row of rows) {
    const monday = mondayFor(row.timestamp)
    const values = weeks.get(monday) ?? TOOLS.map(() => 0)
    row.tools.forEach((value, index) => {
      values[index] += value
    })
    weeks.set(monday, values)
  }
  return [...weeks.entries()]
    .sort(([left], [right]) => left - right)
    .map(([monday, tools]) => ({ monday, tools }))
}

function chartMarkup(weeks, theme) {
  const x = 310
  const top = 40
  const width = 230
  const height = 86
  const step = weeks.length ? width / weeks.length : width
  const gap = weeks.length > 1 ? Math.min(1.5, step * 0.18) : 0
  const barWidth = Math.max(0.1, step - gap)
  const maximum = Math.max(
    1,
    ...weeks.map((week) => week.tools.reduce((sum, value) => sum + value, 0)),
  )
  const bars = weeks
    .flatMap((week, weekIndex) => {
      let bottom = top + height
      return week.tools.flatMap((value, toolIndex) => {
        if (!value) return []
        const segmentHeight = (value / maximum) * height
        bottom -= segmentHeight
        return [
          `<rect x="${(x + weekIndex * step + gap / 2).toFixed(2)}" y="${bottom.toFixed(2)}" width="${barWidth.toFixed(2)}" height="${segmentHeight.toFixed(2)}" fill="${TOOLS[toolIndex].color}"/>`,
        ]
      })
    })
    .join('')

  return [
    `<line x1="${x}" y1="${top + height}" x2="${x + width}" y2="${top + height}" stroke="${theme.grid}"/>`,
    `<circle cx="${x}" cy="${top + height}" r="3" fill="#D9684B" aria-hidden="true"/>`,
    `<g aria-label="${escapeXml('Weekly token volume, stacked by tool')}">${bars}</g>`,
  ].join('')
}

function legendMarkup() {
  const positions = [20, 89, 191, 262]
  return TOOLS.map(
    (tool, index) =>
      `<circle cx="${positions[index]}" cy="137" r="4" fill="${tool.color}"/><text x="${positions[index] + 8}" y="141" class="legend">${escapeXml(tool.label)}</text>`,
  ).join('')
}

function renderCard(rows, mode) {
  const theme = THEMES[mode]
  const total = rows.reduce((sum, row) => sum + row.total, 0)
  const cache = rows.reduce((sum, row) => sum + row.cache, 0)
  const ratio = total ? (cache / total) * 100 : 0
  const weeks = weeklyTotals(rows)
  const totalLabel = `${compactNumber(total)} recorded tokens`
  const cacheLabel = `${compactNumber(cache)} cached context · ${ratio.toFixed(1)}% of traffic`
  const spanLabel = dateSpan(rows)

  return `<svg xmlns="http://www.w3.org/2000/svg" width="${CARD_WIDTH}" height="${CARD_HEIGHT}" viewBox="0 0 ${CARD_WIDTH} ${CARD_HEIGHT}" role="img" aria-labelledby="title desc">
  <title id="title">${escapeXml('Ledger 02 — AI Usage Chronicle')}</title>
  <desc id="desc">${escapeXml(`${totalLabel}; ${spanLabel}; ${cacheLabel}. Weekly tokens are stacked by tool.`)}</desc>
  <style>
    text { font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; fill: ${theme.text}; }
    .identity { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 11px; font-weight: 700; letter-spacing: .11em; }
    .total { font-size: 24px; font-weight: 720; letter-spacing: -.02em; }
    .meta { font-size: 11px; fill: ${theme.muted}; }
    .chart-title { font-size: 10px; font-weight: 600; fill: ${theme.muted}; }
    .legend { font-size: 10px; fill: ${theme.muted}; }
  </style>
  <rect x=".5" y=".5" width="559" height="159" rx="12" fill="${theme.background}" stroke="${theme.border}"/>
  <text x="20" y="26" class="identity">${escapeXml('LEDGER 02 / AI USAGE CHRONICLE')}</text>
  <text x="20" y="59" class="total">${escapeXml(totalLabel)}</text>
  <text x="20" y="82" class="meta">${escapeXml(spanLabel)}</text>
  <text x="20" y="102" class="meta">${escapeXml(cacheLabel)}</text>
  <text x="310" y="26" class="chart-title">${escapeXml('Weekly tokens · stacked by tool')}</text>
  ${chartMarkup(weeks, theme)}
  ${legendMarkup()}
</svg>
`
}

function main() {
  const projectRoot = dirname(dirname(fileURLToPath(import.meta.url)))
  const inputPath = resolve(process.argv[2] ?? join(projectRoot, 'public', 'usage.json'))
  const outputDirectory = resolve(process.argv[3] ?? join(projectRoot, 'public'))
  const payload = JSON.parse(readFileSync(inputPath, 'utf8'))
  const rows = normalizedRows(payload.daily)

  mkdirSync(outputDirectory, { recursive: true })
  for (const mode of Object.keys(THEMES)) {
    writeFileSync(
      join(outputDirectory, `ai-usage-card-${mode}.svg`),
      renderCard(rows, mode),
    )
  }
}

main()
