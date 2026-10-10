/** Source grid proportions survive resizing; stale arrays use the legacy grid. */
export function tableTracks(raw: unknown, count: number, extent: number): number[] {
  const valid = Array.isArray(raw) && raw.length === count &&
    raw.every(value => typeof value === "number" && Number.isFinite(value) && value > 0);
  const weights: number[] = valid ? raw : Array(count).fill(1);
  const total = weights.reduce((sum, value) => sum + value, 0);
  return weights.map(value => extent * value / total);
}

export function trackOffset(tracks: number[], index: number): number {
  return tracks.slice(0, index).reduce((sum, value) => sum + value, 0);
}
