import type { BadgeProps } from "../components/Badge";

export function formatCurrency(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "-";
  if (value < 0.01) return `$${value.toFixed(4)}`;
  if (value < 1) return `$${value.toFixed(3)}`;
  return `$${value.toFixed(2)}`;
}

export function formatStock(stock: number | null | undefined): string {
  if (stock == null) return "-";
  if (stock <= 0) return "Out of stock";
  if (stock >= 1_000_000) return `${(stock / 1_000_000).toFixed(1)}M`;
  if (stock >= 1_000) return `${(stock / 1_000).toFixed(0)}K`;
  return stock.toLocaleString();
}

export function getTypeLabel(type: string | null | undefined): string {
  switch (type) {
    case "resistor": return "R";
    case "capacitor": return "C";
    case "inductor": return "L";
    case "ic": return "IC";
    case "connector": return "J";
    case "led": return "LED";
    case "diode": return "D";
    case "transistor": return "Q";
    case "crystal": return "Y";
    default: return "";
  }
}

export function getTypeBadgeVariant(type: string | null | undefined): BadgeProps["variant"] {
  switch (type) {
    case "ic": return "info";
    case "capacitor":
    case "connector": return "success";
    case "inductor":
    case "led": return "warning";
    case "diode":
    case "transistor": return "destructive";
    default: return "default";
  }
}
