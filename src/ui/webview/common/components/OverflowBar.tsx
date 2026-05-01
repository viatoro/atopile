import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "./Select";
import "./OverflowBar.css";

export interface OverflowItem {
  key: string;
  icon: ReactNode;
  label: string;
  tooltip?: string;
  onClick: () => void;
  disabled?: boolean;
  className?: string;
}

interface OverflowBarProps {
  items: OverflowItem[];
  minItemWidth: number;
  overflowTriggerWidth?: number;
  className?: string;
  renderItem: (item: OverflowItem, index: number) => ReactNode;
}

export function OverflowBar({
  items,
  minItemWidth,
  overflowTriggerWidth = 32,
  className,
  renderItem,
}: OverflowBarProps) {
  const rowRef = useRef<HTMLDivElement>(null);
  const [visibleCount, setVisibleCount] = useState(Infinity);

  const recalc = useCallback(() => {
    const row = rowRef.current;
    if (!row) return;
    const available = row.clientWidth;
    let fits = Math.floor(available / minItemWidth);
    if (fits < items.length) {
      fits = Math.max(1, Math.floor((available - overflowTriggerWidth) / minItemWidth));
    }
    setVisibleCount(fits);
  }, [items.length, minItemWidth, overflowTriggerWidth]);

  useEffect(() => {
    recalc();
    const ro = new ResizeObserver(recalc);
    if (rowRef.current) ro.observe(rowRef.current);
    return () => ro.disconnect();
  }, [recalc]);

  const visible = items.slice(0, visibleCount);
  const overflowed = items.slice(visibleCount);

  return (
    <div className={`overflow-bar${className ? ` ${className}` : ""}`} ref={rowRef}>
      {visible.map((item, i) => renderItem(item, i))}

      {overflowed.length > 0 && (
        <DropdownMenu className="overflow-bar-menu">
          <DropdownMenuTrigger className="overflow-bar-trigger" />
          <DropdownMenuContent className="overflow-bar-content">
            {overflowed.map((item) => (
              <DropdownMenuItem
                key={item.key}
                onClick={item.onClick}
                disabled={item.disabled}
                className={item.className}
              >
                {item.icon}
                <span>{item.label}</span>
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      )}
    </div>
  );
}
