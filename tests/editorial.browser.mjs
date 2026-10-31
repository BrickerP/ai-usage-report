import assert from 'node:assert/strict'
import { createReadStream, existsSync, mkdirSync, readFileSync, statSync } from 'node:fs'
import { createServer } from 'node:http'
import { extname, join, normalize, resolve } from 'node:path'
import { chromium } from 'playwright'

const projectRoot = resolve(import.meta.dirname, '..')
const staticRoot = join(projectRoot, 'docs')
const resultDir = join(projectRoot, 'test-results', 'editorial')
const payload = JSON.parse(readFileSync(join(projectRoot, 'public', 'usage.json'), 'utf8'))

const TOOLS = ['codex', 'claude', 'cursor', 'oneapi']
const TOOL_LABELS = ['Codex', 'Claude Code', 'Cursor', 'One API']
const MIME = new Map([
  ['.css', 'text/css; charset=utf-8'],
  ['.html', 'text/html; charset=utf-8'],
  ['.js', 'text/javascript; charset=utf-8'],
  ['.json', 'application/json; charset=utf-8'],
  ['.svg', 'image/svg+xml'],
  ['.woff2', 'font/woff2'],
])

function addDays(date, days) {
  const value = new Date(`${date}T00:00:00Z`)
  value.setUTCDate(value.getUTCDate() + days)
  return value.toISOString().slice(0, 10)
}

function rowsForCalendarDays(days) {
  if (days === 'all') return payload.daily
  const end = payload.daily.at(-1).date
  const start = addDays(end, 1 - days)
  return payload.daily.filter((row) => row.date >= start && row.date <= end)
}

function findConsecutiveGap(length = 2) {
  const published = new Set(payload.daily.map((row) => row.date))
  const first = payload.daily[0].date
  const last = payload.daily.at(-1).date
  let run = []
  for (let date = first; date <= last; date = addDays(date, 1)) {
    if (published.has(date)) {
      run = []
      continue
    }
    run.push(date)
    if (run.length === length) return { from: run[0], to: run.at(-1) }
  }
  throw new Error(`Published ledger has no ${length}-day calendar gap`)
}

function summarize(rows, tool = null, model = null) {
  let tokens = 0
  let cost = 0
  for (const row of rows) {
    if (!tool) {
      tokens += TOOLS.reduce((sum, id) => sum + row[`${id}_tokens`], 0)
      cost += TOOLS.reduce((sum, id) => sum + row[`${id}_cost`], 0)
      continue
    }
    if (!model) {
      tokens += row[`${tool}_tokens`]
      cost += row[`${tool}_cost`]
      continue
    }
    const match = (row[`${tool}_models`] ?? []).find((candidate) => candidate.model === model)
    tokens += match?.tokens ?? 0
    cost += match?.cost ?? 0
  }
  return { tokens, cost }
}

function topModel(rows, tool) {
  const totals = new Map()
  for (const row of rows) {
    for (const model of row[`${tool}_models`] ?? []) {
      if (model.model === 'Legacy unknown') continue
      const current = totals.get(model.model) ?? { tokens: 0, cost: 0, dates: new Set() }
      current.tokens += model.tokens
      current.cost += model.cost
      current.dates.add(row.date)
      totals.set(model.model, current)
    }
  }
  const ranked = [...totals.entries()].sort(
    (a, b) => b[1].tokens - a[1].tokens || a[0].localeCompare(b[0]),
  )
  const winner = ranked.find(([, value]) => value.dates.size >= 2) ?? ranked[0]
  assert.ok(winner, `Expected at least one identified ${tool} model`)
  return { name: winner[0], tokens: winner[1].tokens, cost: winner[1].cost, days: winner[1].dates.size }
}

function recordFor(rows, tool = null, model = null) {
  let peak = null
  for (const row of rows) {
    const value = model
      ? (row[`${tool}_models`] ?? []).find((candidate) => candidate.model === model)?.tokens ?? 0
      : tool
        ? row[`${tool}_tokens`]
        : row.total_tokens
    if (!peak || value > peak.tokens) peak = { date: row.date, tokens: value }
  }
  return peak
}

function cssAttribute(value) {
  return JSON.stringify(value)
}

function startStaticServer() {
  assert.ok(existsSync(join(staticRoot, 'index.html')), 'docs/index.html is missing; build before browser tests')
  const server = createServer((request, response) => {
    const url = new URL(request.url ?? '/', 'http://127.0.0.1')
    const pathname = decodeURIComponent(url.pathname)
    const relative = pathname === '/' ? 'index.html' : pathname.replace(/^\/+/, '')
    const candidate = normalize(join(staticRoot, relative))
    if (!candidate.startsWith(`${staticRoot}/`) || !existsSync(candidate) || !statSync(candidate).isFile()) {
      response.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' })
      response.end('Not found')
      return
    }
    response.writeHead(200, {
      'cache-control': 'no-store',
      'content-type': MIME.get(extname(candidate)) ?? 'application/octet-stream',
    })
    createReadStream(candidate).pipe(response)
  })
  return new Promise((resolvePromise, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      assert.ok(address && typeof address !== 'string')
      resolvePromise({ server, origin: `http://127.0.0.1:${address.port}` })
    })
  })
}

async function expectRaw(locator, expected, message) {
  await locator.waitFor({ state: 'visible' })
  const tolerance = Number.isInteger(expected) ? 0 : Math.max(1e-9, Math.abs(expected) * 1e-12)
  const deadline = Date.now() + 5_000
  let raw = null
  let actual = Number.NaN
  while (Date.now() < deadline) {
    raw = await locator.getAttribute('data-value')
    actual = Number(raw)
    if (raw !== null && Math.abs(actual - expected) <= tolerance) return
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 50))
  }
  assert.notEqual(raw, null, `${message}: missing data-value`)
  assert.ok(Math.abs(actual - expected) <= tolerance, `${message}: expected ${expected}, got ${actual}`)
}

async function expectUnavailable(locator, message) {
  await locator.waitFor({ state: 'visible' })
  const raw = await locator.getAttribute('data-value')
  assert.ok(raw === null || raw === '' || raw === 'null', `${message}: expected absent raw value, got ${raw}`)
  assert.match(
    await locator.innerText(),
    /—|unavailable|no recorded (?:data|cost|tokens?)/i,
    `${message}: expected unavailable display`,
  )
}

async function gotoClean(page, origin, query = '') {
  await page.goto(`${origin}/${query}`, { waitUntil: 'networkidle' })
  await page.locator('[data-testid="yearbook-root"]').waitFor({ state: 'visible' })
}

async function clickRange(page, name) {
  await page.getByRole('button', { name, exact: true }).click()
}

async function openDisclosure(page, testId, triggerName) {
  let panel = page.locator(`[data-testid="${testId}"]`)
  if (await panel.count()) {
    const tagName = await panel.evaluate((element) => element.tagName)
    if (tagName === 'DETAILS' && (await panel.getAttribute('open')) === null) {
      await panel.locator('summary').click()
    }
    if (await panel.isVisible()) return panel
  }
  const trigger = page.getByRole('button', { name: triggerName })
  if (await trigger.count()) await trigger.first().click()
  panel = page.locator(`[data-testid="${testId}"]`)
  if (await panel.count() && !(await panel.isVisible())) {
    const summary = panel.locator('summary')
    if (await summary.count()) await summary.click()
  }
  await panel.waitFor({ state: 'visible' })
  return panel
}

async function assertNoHorizontalOverflow(page, label) {
  const report = await page.evaluate(() => {
    const root = document.querySelector('[data-testid="yearbook-root"]')
    if (!(root instanceof HTMLElement)) return { missingRoot: true }
    const rootRect = root.getBoundingClientRect()
    const offenders = [...root.querySelectorAll('*')]
      .filter((element) => {
        const style = getComputedStyle(element)
        if (style.position === 'fixed' || style.display === 'none' || style.visibility === 'hidden') return false
        const rect = element.getBoundingClientRect()
        return rect.width > 0 && (rect.left < rootRect.left - 1 || rect.right > rootRect.right + 1)
      })
      .slice(0, 8)
      .map((element) => ({ tag: element.tagName, testid: element.getAttribute('data-testid'), text: element.textContent?.trim().slice(0, 60) }))
    return {
      documentOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      rootOverflow: root.scrollWidth - root.clientWidth,
      offenders,
    }
  })
  assert.equal(report.missingRoot, undefined, `${label}: yearbook root missing`)
  assert.ok(report.documentOverflow <= 1, `${label}: document overflow ${JSON.stringify(report)}`)
  assert.ok(report.rootOverflow <= 1, `${label}: root overflow ${JSON.stringify(report)}`)
  assert.deepEqual(report.offenders, [], `${label}: overflowing elements ${JSON.stringify(report.offenders)}`)
}

const tests = []
function test(name, run) {
  tests.push({ name, run })
}

test('all-time and calendar presets match the published ledger', async ({ page, origin }) => {
  await gotoClean(page, origin)
  const heroTokens = page.locator('[data-testid="hero-total"]')
  const heroCost = page.locator('[data-testid="hero-cost"]')
  for (const [button, days] of [['All', 'all'], ['7 days', 7], ['30 days', 30], ['90 days', 90]]) {
    await clickRange(page, button)
    const expected = summarize(rowsForCalendarDays(days))
    await expectRaw(heroTokens, expected.tokens, `${button} tokens`)
    await expectRaw(heroCost, expected.cost, `${button} cost`)
  }
  await page.getByRole('img', { name: /usage chart/i }).waitFor({ state: 'visible' })
  const legend = page.getByRole('list', { name: 'Plotted series', exact: true })
  assert.equal(await legend.getByRole('listitem').count(), TOOLS.length)
  for (const label of TOOL_LABELS) {
    await legend.getByText(label, { exact: true }).waitFor({ state: 'visible' })
  }
  assert.doesNotMatch(await page.locator('.pattern-note').textContent(), /No active dates/i)
})

test('tool and exact model scopes preserve token and cost totals', async ({ page, origin }) => {
  const rows = rowsForCalendarDays(7)
  const tool = 'codex'
  const model = topModel(rows, tool)
  await gotoClean(page, origin, '?range=7')
  await page.locator(`[data-tool="${tool}"]`).click()
  const toolExpected = summarize(rows, tool)
  await expectRaw(page.locator('[data-testid="scope-total"]'), toolExpected.tokens, 'tool tokens')
  await expectRaw(page.locator('[data-testid="scope-cost"]'), toolExpected.cost, 'tool cost')

  const modelTrigger = page.getByRole('button', { name: /model index/i })
  await modelTrigger.click()
  const modelButton = page.locator(`#model-index-region [data-model=${cssAttribute(model.name)}]`).first()
  await modelButton.waitFor({ state: 'visible' })
  await modelButton.click()
  const modelExpected = summarize(rows, tool, model.name)
  await expectRaw(page.locator('[data-testid="scope-total"]'), modelExpected.tokens, 'model tokens')
  await expectRaw(page.locator('[data-testid="scope-cost"]'), modelExpected.cost, 'model cost')
})

test('model focus does not serialize a date until the visitor inspects one', async ({ page, origin }) => {
  const rows = rowsForCalendarDays(7)
  const tool = 'codex'
  const model = topModel(rows, tool)
  const peak = recordFor(rows, tool, model.name)
  await gotoClean(page, origin, '?range=7')
  await page.locator(`[data-tool="${tool}"]`).click()
  await page.getByRole('button', { name: /model index/i }).click()
  await page.locator(`#model-index-region [data-model=${cssAttribute(model.name)}]`).first().click()
  await page.waitForURL((url) =>
    url.searchParams.get('tool') === tool &&
    url.searchParams.get('model') === model.name &&
    !url.searchParams.has('day'),
  )

  const slider = page.getByRole('slider', { name: /inspect date/i })
  await slider.press('End')
  const inspectedDate = await page.locator('[data-testid="selected-date"]').getAttribute('data-value')
  assert.ok(inspectedDate, 'Keyboard inspection should select a concrete date')
  await page.waitForURL((url) => url.searchParams.get('day') === inspectedDate)

  await page.getByRole('button', { name: /jump to peak/i }).click()
  await page.waitForURL((url) => url.searchParams.get('day') === peak.date)
  assert.equal(await page.locator('[data-testid="selected-date"]').getAttribute('data-value'), peak.date)
})

test('custom dates apply atomically and match the selected calendar span', async ({ page, origin }) => {
  const from = payload.daily.at(-6).date
  const to = payload.daily.at(-3).date
  const rows = payload.daily.filter((row) => row.date >= from && row.date <= to)
  const expected = summarize(rows)
  await gotoClean(page, origin, '?range=7')
  await page.getByRole('button', { name: 'Custom', exact: true }).click()
  await page.getByLabel('From', { exact: true }).fill(from)
  await page.getByLabel('To', { exact: true }).fill(to)
  await page.getByRole('button', { name: /apply custom range/i }).click()
  await expectRaw(page.locator('[data-testid="hero-total"]'), expected.tokens, 'custom tokens')
  await expectRaw(page.locator('[data-testid="hero-cost"]'), expected.cost, 'custom cost')
  await page.waitForURL((url) => url.searchParams.get('from') === from && url.searchParams.get('to') === to)
})

test('a gap-only custom range remains unavailable instead of becoming zero or all-time', async ({ page, origin }) => {
  const { from, to } = findConsecutiveGap(2)
  await gotoClean(page, origin)
  await page.getByRole('button', { name: 'Custom', exact: true }).click()
  await page.getByLabel('From', { exact: true }).fill(from)
  await page.getByLabel('To', { exact: true }).fill(to)
  await page.getByRole('button', { name: /apply custom range/i }).click()
  await page.waitForURL((url) =>
    url.searchParams.get('from') === from &&
    url.searchParams.get('to') === to &&
    !url.searchParams.has('range'),
  )

  await expectUnavailable(page.locator('[data-testid="hero-total"]'), 'gap-only hero tokens')
  await expectUnavailable(page.locator('[data-testid="scope-total"]'), 'gap-only exact tokens')
  await expectUnavailable(page.locator('[data-testid="hero-cost"]'), 'gap-only hero cost')
  await expectUnavailable(page.locator('[data-testid="scope-cost"]'), 'gap-only exact cost')
  assert.equal(await page.getByRole('button', { name: 'All', exact: true }).getAttribute('aria-pressed'), 'false')
})

test('date inspector supports keyboard movement, peak jump, and Escape unwind', async ({ page, origin }) => {
  const rows = rowsForCalendarDays(7)
  const tool = 'codex'
  const peak = recordFor(rows, tool)
  await gotoClean(page, origin, '?range=7')
  await page.locator(`[data-tool="${tool}"]`).click()
  const slider = page.getByRole('slider', { name: /inspect date/i })
  await slider.waitFor({ state: 'visible' })
  const before = await page.locator('[data-testid="selected-date"]').getAttribute('data-value')
  await slider.press('ArrowLeft')
  const after = await page.locator('[data-testid="selected-date"]').getAttribute('data-value')
  assert.notEqual(after, before, 'ArrowLeft should change the inspected date')
  await page.getByRole('button', { name: /jump to peak/i }).click()
  assert.equal(await page.locator('[data-testid="selected-date"]').getAttribute('data-value'), peak.date)

  const modelTrigger = page.getByRole('button', { name: /model index/i })
  await modelTrigger.click()
  assert.equal(await modelTrigger.getAttribute('aria-expanded'), 'true')
  await page.keyboard.press('Escape')
  assert.equal(await modelTrigger.getAttribute('aria-expanded'), 'false')
})

test('exact ledger paginates and source details expose published metadata', async ({ page, origin }) => {
  await gotoClean(page, origin, '?range=all')
  const ledger = await openDisclosure(page, 'exact-ledger', /exact ledger/i)
  const rows = ledger.locator('[data-testid="ledger-row"]')
  assert.ok((await rows.count()) > 0, 'Expected visible exact ledger rows')
  const firstDate = await rows.first().getAttribute('data-date')
  const newest = payload.daily.at(-1)
  assert.equal(firstDate, payload.daily.at(-1).date)
  assert.equal(
    Number(await rows.first().getAttribute('data-tokens')),
    TOOLS.reduce((sum, id) => sum + newest[`${id}_tokens`], 0),
  )
  assert.equal(
    Number(await rows.first().getAttribute('data-cost')),
    TOOLS.reduce((sum, id) => sum + newest[`${id}_cost`], 0),
  )
  const next = ledger.getByRole('button', { name: /next page/i })
  await next.click()
  const secondPageDate = await rows.first().getAttribute('data-date')
  assert.equal(secondPageDate, addDays(payload.daily.at(-1).date, -50))
  assert.notEqual(secondPageDate, firstDate, 'Next page should replace the visible ledger slice')

  const details = await openDisclosure(page, 'source-details', /report details|source details/i)
  assert.match(await details.innerText(), /source freshness/i)
  assert.match(await details.innerText(), new RegExp(payload.timezone.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
  for (const label of TOOL_LABELS) assert.match(await details.innerText(), new RegExp(label, 'i'))
})

test('desktop and mobile layouts remain within the viewport with dense panels open', async ({ page, origin }) => {
  for (const viewport of [{ width: 1280, height: 900 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport)
    await gotoClean(page, origin, '?range=all&tool=codex')
    const modelTrigger = page.getByRole('button', { name: /model index/i })
    await modelTrigger.click()
    await openDisclosure(page, 'exact-ledger', /exact ledger/i)
    await assertNoHorizontalOverflow(page, `${viewport.width}px with model index and ledger open`)
  }
})

mkdirSync(resultDir, { recursive: true })
const { server, origin } = await startStaticServer()
const browser = await chromium.launch({ headless: true })
const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, reducedMotion: 'reduce' })
const page = await context.newPage()
page.setDefaultTimeout(10_000)
const runtimeErrors = []
page.on('pageerror', (error) => runtimeErrors.push(error.message))
page.on('console', (message) => {
  if (message.type() === 'error') runtimeErrors.push(`console: ${message.text()}`)
})
await context.tracing.start({ screenshots: true, snapshots: true, sources: true })

let failed = false
try {
  for (const [index, entry] of tests.entries()) {
    runtimeErrors.length = 0
    process.stdout.write(`browser: ${entry.name} ... `)
    try {
      await entry.run({ page, origin })
      assert.deepEqual(runtimeErrors, [], `${entry.name}: browser runtime errors`)
      console.log('ok')
    } catch (error) {
      failed = true
      console.log('failed')
      await page.screenshot({
        path: join(resultDir, `failure-${index + 1}.png`),
        fullPage: true,
      }).catch(() => {})
      console.error(error)
    }
  }
  if (failed) await context.tracing.stop({ path: join(resultDir, 'trace.zip') })
  else await context.tracing.stop()
} finally {
  await browser.close()
  await new Promise((resolvePromise) => server.close(resolvePromise))
}

if (failed) process.exitCode = 1
