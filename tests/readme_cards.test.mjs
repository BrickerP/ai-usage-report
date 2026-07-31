import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'

const generator = new URL('../scripts/generate_readme_cards.mjs', import.meta.url)
const packageJson = JSON.parse(
  readFileSync(new URL('../package.json', import.meta.url), 'utf8'),
)
const publishSource = readFileSync(
  new URL('../scripts/publish.sh', import.meta.url),
  'utf8',
)

function dailyRow(date, tools, cache = {}) {
  const [codex, claude, cursor, oneapi] = tools
  return {
    date,
    codex_tokens: codex,
    claude_tokens: claude,
    cursor_tokens: cursor,
    oneapi_tokens: oneapi,
    total_tokens: codex + claude + cursor + oneapi,
    codex_cache_read: 0,
    claude_cache_create: 0,
    claude_cache_read: 0,
    cursor_cache_write: 0,
    cursor_cache_read: 0,
    oneapi_cache_read: 0,
    oneapi_cache_write: 0,
    ...cache,
  }
}

function generateCards(payload) {
  const root = mkdtempSync(join(tmpdir(), 'readme-cards-'))
  const input = join(root, 'usage.json')
  const output = join(root, 'out')
  writeFileSync(input, `${JSON.stringify(payload)}\n`)
  execFileSync(process.execPath, [generator.pathname, input, output])

  return {
    light: readFileSync(join(output, 'ai-usage-card-light.svg'), 'utf8'),
    dark: readFileSync(join(output, 'ai-usage-card-dark.svg'), 'utf8'),
    cleanup: () => rmSync(root, { recursive: true, force: true }),
  }
}

test('CLI generates fixed light and dark cards with recorded usage semantics', () => {
  const cards = generateCards({
    daily: [
      dailyRow(
        '2026-01-01',
        [40, 20, 10, 30],
        {
          codex_cache_read: 20,
          claude_cache_create: 5,
          claude_cache_read: 5,
          cursor_cache_write: 2,
          cursor_cache_read: 3,
          oneapi_cache_read: 10,
          oneapi_cache_write: 5,
        },
      ),
      dailyRow(
        '2026-01-08',
        [100, 100, 100, 100],
        {
          codex_cache_read: 25,
          claude_cache_read: 25,
          cursor_cache_read: 25,
          oneapi_cache_read: 25,
        },
      ),
    ],
  })

  try {
    for (const svg of [cards.light, cards.dark]) {
      assert.match(svg, /<svg[^>]*width="560"[^>]*height="160"/)
      assert.match(svg, />LEDGER 02 \/ AI USAGE CHRONICLE</)
      assert.match(svg, />500 recorded tokens</)
      assert.match(svg, />Jan 1 – Jan 8, 2026</)
      assert.match(svg, />150 cached context · 30\.0% of traffic</)
      assert.match(svg, />Weekly tokens · stacked by tool</)
      assert.match(svg, /#2563eb/)
      assert.match(svg, /#c2410c/)
      assert.match(svg, /#0d9488/)
      assert.match(svg, /#7c3aed/)
      assert.doesNotMatch(
        svg,
        /\b(?:spend|models?|machines?|rank|controls?|endpoint)\b/i,
      )
    }
    assert.notEqual(cards.light, cards.dark)
  } finally {
    cards.cleanup()
  }
})

test('Ledger identity and Skyline origin remain static, accessible, and self-contained', () => {
  const cards = generateCards({
    daily: [dailyRow('2026-01-05', [10, 20, 30, 40])],
  })

  try {
    for (const svg of [cards.light, cards.dark]) {
      assert.match(svg, /role="img" aria-labelledby="title desc"/)
      assert.match(
        svg,
        /<title id="title">Ledger 02 — AI Usage Chronicle<\/title>/,
      )
      assert.match(svg, /<desc id="desc">[^<]+<\/desc>/)
      assert.match(
        svg,
        /\.identity \{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;/,
      )
      assert.match(
        svg,
        /<circle cx="310" cy="126" r="3" fill="#D9684B" aria-hidden="true"\/>/,
      )
      assert.equal(svg.match(/#D9684B/g)?.length, 1)
      assert.doesNotMatch(
        svg,
        /(?:<script|<foreignObject|\bhref=|\bon[a-z]+=|url\(|@import)/i,
      )
    }
  } finally {
    cards.cleanup()
  }
})

test('CLI output is deterministic and does not expose untrusted input text', () => {
  const payload = {
    name: 'AI <script>alert("unsafe")</script>',
    daily: [
      dailyRow('2026-02-02', [10, 20, 30, 40]),
      dailyRow('2026-02-09"><script>alert(1)</script>', [1, 1, 1, 1]),
    ],
  }
  const first = generateCards(payload)
  const second = generateCards(payload)

  try {
    assert.equal(first.light, second.light)
    assert.equal(first.dark, second.dark)
    for (const svg of [first.light, first.dark]) {
      assert.doesNotMatch(svg, /<script|alert\(|unsafe/)
      assert.match(svg, />Feb 2, 2026</)
      assert.match(svg, />100 recorded tokens</)
    }
  } finally {
    first.cleanup()
    second.cleanup()
  }
})

test('production build regenerates cards and publisher treats both SVGs as generated artifacts', () => {
  assert.match(packageJson.scripts.build, /^npm run generate:readme-cards/)
  assert.match(
    packageJson.scripts['generate:readme-cards'],
    /generate_readme_cards\.mjs/,
  )
  assert.doesNotMatch(publishSource, /public\/ai-usage-card-\*\.svg/)
  assert.ok(
    publishSource.match(/public\/ai-usage-card-light\.svg/g)?.length >= 4,
  )
  assert.ok(
    publishSource.match(/public\/ai-usage-card-dark\.svg/g)?.length >= 4,
  )
  assert.match(
    publishSource,
    /git add -A --[\s\S]{0,160}public\/ai-usage-card-light\.svg[\s\S]{0,80}public\/ai-usage-card-dark\.svg/,
  )
})

test('weekly skyline remains inside the fixed card after years of history', () => {
  const start = Date.UTC(2022, 0, 3)
  const daily = Array.from({ length: 200 }, (_, index) =>
    dailyRow(
      new Date(start + index * 7 * 24 * 60 * 60 * 1000)
        .toISOString()
        .slice(0, 10),
      [index + 1, 2, 3, 4],
    ),
  )
  const cards = generateCards({ daily })

  try {
    const chartSegments = [
      ...cards.light.matchAll(
        /<rect x="([\d.]+)" y="[\d.]+" width="([\d.]+)" height="[\d.]+" fill="#(?:2563eb|c2410c|0d9488|7c3aed)"\/>/g,
      ),
    ]
    assert.equal(chartSegments.length, 200 * 4)
    for (const [, xValue, widthValue] of chartSegments) {
      assert.ok(Number(xValue) >= 310)
      assert.ok(Number(xValue) + Number(widthValue) <= 540.01)
    }
  } finally {
    cards.cleanup()
  }
})

test('empty history produces an honest zero-value card', () => {
  const cards = generateCards({ daily: [] })

  try {
    for (const svg of [cards.light, cards.dark]) {
      assert.match(svg, />0 recorded tokens</)
      assert.match(svg, />No recorded dates</)
      assert.match(svg, />0 cached context · 0\.0% of traffic</)
      assert.match(svg, /Weekly tokens are stacked by tool/)
    }
  } finally {
    cards.cleanup()
  }
})
