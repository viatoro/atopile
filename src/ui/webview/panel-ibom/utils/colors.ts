import type { Color } from "../../common/layout";

/** Component type → highlight color (used on both PCB and sidebar badges). */
export const TYPE_COLORS: Record<string, Color> = {
  resistor:   [0.3, 0.6, 1.0, 0.45],
  capacitor:  [0.3, 0.85, 0.5, 0.45],
  inductor:   [0.9, 0.6, 0.2, 0.45],
  ic:         [0.8, 0.4, 0.9, 0.45],
  connector:  [0.95, 0.75, 0.2, 0.45],
  diode:      [1.0, 0.45, 0.45, 0.45],
  led:        [1.0, 0.85, 0.3, 0.45],
  crystal:    [0.5, 0.8, 0.9, 0.45],
  transistor: [0.7, 0.5, 0.85, 0.45],
};

export const DEFAULT_COLOR: Color = [0.6, 0.6, 0.6, 0.3];
