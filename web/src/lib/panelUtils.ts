const MIN_PANEL_WIDTH = 280;
const MAX_PANEL_RATIO = 0.55;

/** Clamp a desired panel width to fit within a container. */
export function clampPanelWidth(desired: number, containerWidth: number): number {
  if (containerWidth <= 0) return desired;
  return Math.max(MIN_PANEL_WIDTH, Math.min(desired, containerWidth * MAX_PANEL_RATIO));
}
