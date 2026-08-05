import assert from 'node:assert/strict'
import test from 'node:test'

import {
  deriveRunLevel,
  RUN_SIGNATURE_VERSION,
} from '../src/lib/runLevel.ts'

function rowsFor(values, activeDates = values.map((_, index) => index + 1)) {
  const byDay = new Map(
    activeDates.map((day, index) => [day, values[index]]),
  )
  const lastDay = Math.max(...activeDates)
  return Array.from({ length: lastDay }, (_, index) => {
    const day = index + 1
    const tokens = byDay.get(day) ?? 0
    return {
      date: `2026-01-${String(day).padStart(2, '0')}`,
      oneapi_models: tokens
        ? [{ model: 'focus', tokens, cost: tokens / 10 }]
        : [],
    }
  })
}

test('missing model arrays stay unavailable while a covered absence is zero', () => {
  const level = deriveRunLevel(
    [
      {
        date: '2026-01-01',
        oneapi_models: [{ model: 'focus', tokens: 10, cost: 1 }],
      },
      { date: '2026-01-02' },
      { date: '2026-01-03', oneapi_models: [] },
    ],
    'oneapi',
    'focus',
  )

  assert.deepEqual(
    level.points.map((point) => [point.state, point.tokens, point.cost]),
    [
      ['active', 10, 1],
      ['unavailable', null, null],
      ['zero', 0, 0],
    ],
  )
  assert.equal(level.coverageComplete, false)
  assert.deepEqual(level.points[1], {
    index: 1,
    date: '2026-01-02',
    state: 'unavailable',
    tokens: null,
    cost: null,
    groundTokens: null,
    skyTokens: null,
  })
  assert.deepEqual(level.signature, {
    version: RUN_SIGNATURE_VERSION,
    name: 'History still forming',
    evidence: '1 of 3 dates unavailable',
    forming: true,
  })
})

test('missing calendar dates become unavailable instead of compressing the route', () => {
  const level = deriveRunLevel(
    [
      {
        date: '2026-01-01',
        oneapi_models: [{ model: 'focus', tokens: 10, cost: 1 }],
      },
      {
        date: '2026-01-03',
        oneapi_models: [{ model: 'focus', tokens: 30, cost: 3 }],
      },
    ],
    'oneapi',
    'focus',
  )

  assert.deepEqual(
    level.points.map((point) => [point.date, point.state, point.tokens]),
    [
      ['2026-01-01', 'active', 10],
      ['2026-01-02', 'unavailable', null],
      ['2026-01-03', 'active', 30],
    ],
  )
  assert.deepEqual(level.signature, {
    version: RUN_SIGNATURE_VERSION,
    name: 'History still forming',
    evidence: '1 of 3 dates unavailable',
    forming: true,
  })
})

test('duplicate dates aggregate once and remain deterministic when input reverses', () => {
  const rows = [
    {
      date: '2026-01-01',
      oneapi_models: [
        { model: 'focus', tokens: 10, cost: 1 },
        { model: 'other', tokens: 99, cost: 9.9 },
      ],
    },
    {
      date: '2026-01-01',
      oneapi_models: [
        { model: 'focus', tokens: 20, cost: 2 },
        { model: 'focus', tokens: 5, cost: 0.5 },
      ],
    },
  ]
  const first = deriveRunLevel(rows, 'oneapi', 'focus')
  const reversed = deriveRunLevel([...rows].reverse(), 'oneapi', 'focus')

  assert.deepEqual(reversed, first)
  assert.equal(first.points.length, 1)
  assert.deepEqual(first.points[0], {
    index: 0,
    date: '2026-01-01',
    state: 'active',
    tokens: 35,
    cost: 3.5,
    groundTokens: 35,
    skyTokens: 0,
  })
  assert.deepEqual(first.signature, {
    version: RUN_SIGNATURE_VERSION,
    name: 'History still forming',
    evidence: '1 active dates · 6 needed',
    forming: true,
  })
})

test('landmarks use sorted dates and the earliest maximum on ties', () => {
  const level = deriveRunLevel(
    [
      {
        date: '2026-01-03',
        oneapi_models: [{ model: 'focus', tokens: 9, cost: 0.9 }],
      },
      {
        date: '2026-01-01',
        oneapi_models: [{ model: 'focus', tokens: 1, cost: 0.1 }],
      },
      {
        date: '2026-01-02',
        oneapi_models: [{ model: 'focus', tokens: 9, cost: 0.9 }],
      },
      { date: '2026-01-04', oneapi_models: [] },
    ],
    'oneapi',
    'focus',
  )

  assert.deepEqual(level.points.map((point) => point.date), [
    '2026-01-01',
    '2026-01-02',
    '2026-01-03',
    '2026-01-04',
  ])
  assert.deepEqual(level.landmarks.firstSeen, {
    index: 0,
    date: '2026-01-01',
    tokens: 1,
  })
  assert.deepEqual(level.landmarks.record, {
    index: 1,
    date: '2026-01-02',
    tokens: 9,
  })
  assert.deepEqual(level.landmarks.lastSeen, {
    index: 2,
    date: '2026-01-03',
    tokens: 9,
  })
  assert.deepEqual(level.landmarks.archiveEdge, {
    index: 3,
    date: '2026-01-04',
    tokens: 0,
  })
  assert.equal(level.defaultDay, '2026-01-01')
})

test('two linear bands conserve every available token without transforming values', () => {
  const level = deriveRunLevel(rowsFor([10, 20, 1_000]), 'oneapi', 'focus')

  assert.equal(level.split.enabled, true)
  assert.equal(level.split.floor, 23.2)
  assert.equal(level.split.skyMax, 976.8)
  for (const point of level.points) {
    assert.equal(point.groundTokens + point.skyTokens, point.tokens)
  }
  assert.deepEqual(
    level.points.map((point) => [point.tokens, point.groundTokens, point.skyTokens]),
    [
      [10, 10, 0],
      [20, 20, 0],
      [1_000, 23.2, 976.8],
    ],
  )
  assert.equal(
    level.points.reduce((sum, point) => sum + point.tokens, 0),
    level.points.reduce(
      (sum, point) => sum + point.groundTokens + point.skyTokens,
      0,
    ),
  )
})

test('Signature v1 uses fixed Pulsar before Sprinter precedence', () => {
  const level = deriveRunLevel(
    rowsFor([100, 1, 50, 1, 5, 1, 1]),
    'oneapi',
    'focus',
  )

  assert.equal(level.signature.name, 'Pulsar')
  assert.equal(
    level.signature.evidence,
    '3 pulse groups · 97% of tokens on pulse dates',
  )
  assert.equal(level.signature.forming, false)
})

test('Signature v1 selects Sprinter from top-two concentration', () => {
  const level = deriveRunLevel(
    rowsFor([50, 30, 5, 5, 5, 3, 2]),
    'oneapi',
    'focus',
  )

  assert.equal(level.signature.name, 'Sprinter')
  assert.equal(level.signature.evidence, 'Top 2 dates hold 80% of tokens')
})

test('Signature v1 selects Marathon from dense continuous history', () => {
  const level = deriveRunLevel(
    rowsFor(Array.from({ length: 10 }, () => 10)),
    'oneapi',
    'focus',
  )

  assert.equal(level.signature.name, 'Marathon')
  assert.equal(
    level.signature.evidence,
    '10 of 10 dates active · longest run 10 days',
  )
})

test('Signature v1 selects Hopper from sparse separated runs', () => {
  const level = deriveRunLevel(
    rowsFor([10, 10, 10, 10, 10, 10], [1, 3, 6, 9, 12, 15]),
    'oneapi',
    'focus',
  )

  assert.equal(level.signature.name, 'Hopper')
  assert.equal(level.signature.evidence, '6 active runs · 40% density')
})

test('Signature v1 selects Climber from calendar-half means', () => {
  const level = deriveRunLevel(
    rowsFor([1, 1, 1, 0, 5, 5, 5, 5]),
    'oneapi',
    'focus',
  )

  assert.equal(level.signature.name, 'Climber')
  assert.equal(
    level.signature.evidence,
    '6.7× early half · 4 late active dates',
  )
})

test('Signature v1 falls through to Trailblazer deterministically', () => {
  const rows = rowsFor([10, 10, 10, 10, 10, 10], [1, 2, 3, 6, 7, 8])
  const first = deriveRunLevel(rows, 'oneapi', 'focus')
  const second = deriveRunLevel([...rows].reverse(), 'oneapi', 'focus')

  assert.equal(first.signature.name, 'Trailblazer')
  assert.equal(
    first.signature.evidence,
    '6 of 8 dates active · longest run 3 days',
  )
  assert.deepEqual(second, first)
})

test('forming rules cover zero tokens, too few active dates, and short spans', () => {
  const cases = [
    {
      rows: rowsFor([0, 0, 0, 0, 0, 0, 0]),
      evidence: 'No recorded tokens in this scope',
    },
    {
      rows: rowsFor([1, 1, 1, 1, 1], [1, 2, 3, 4, 7]),
      evidence: '5 active dates · 6 needed',
    },
    {
      rows: rowsFor([1, 1, 1, 1, 1, 1]),
      evidence: '6 calendar days · 7 needed',
    },
  ]

  for (const fixture of cases) {
    const signature = deriveRunLevel(
      fixture.rows,
      'oneapi',
      'focus',
    ).signature
    assert.equal(signature.name, 'History still forming')
    assert.equal(signature.forming, true)
    assert.equal(signature.evidence, fixture.evidence)
  }
})
