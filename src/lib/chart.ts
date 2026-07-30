function mixChannel(channel: number, amount: number) {
  return Math.round(channel + (255 - channel) * amount)
}

function mixHex(hex: string, amount: number) {
  const value = hex.replace('#', '')
  const red = mixChannel(parseInt(value.slice(0, 2), 16), amount)
  const green = mixChannel(parseInt(value.slice(2, 4), 16), amount)
  const blue = mixChannel(parseInt(value.slice(4, 6), 16), amount)
  return `#${[red, green, blue]
    .map((channel) => channel.toString(16).padStart(2, '0'))
    .join('')}`
}

export function modelSeriesColor(
  base: string,
  index: number,
  kind: 'model' | 'legacy' | 'other',
) {
  if (kind === 'legacy') return '#64748b'
  if (kind === 'other') return mixHex(base, 0.66)
  return mixHex(base, [0, 0.18, 0.34, 0.48][index] ?? 0.56)
}
