import { ChevronDown } from "lucide-react";
import type { CSSProperties, ReactNode, HTMLAttributes } from "react";
import "./SidebarDockPanel.css";

export type ResizeHandleProps = Pick<
  HTMLAttributes<HTMLDivElement>,
  "onPointerDown" | "onPointerMove" | "onPointerUp" | "onPointerCancel"
>;

interface SidebarDockHeaderProps {
  className?: string;
  title: ReactNode;
  subtitle?: ReactNode;
  badge?: ReactNode;
  actions?: ReactNode;
  collapsed?: boolean;
  onToggleCollapsed?: () => void;
}

export function SidebarDockHeader({
  className,
  title,
  subtitle,
  badge,
  actions,
  collapsed = false,
  onToggleCollapsed,
}: SidebarDockHeaderProps) {
  return (
    <div className={["sidebar-dock-header", className ?? ""].filter(Boolean).join(" ")}>
      <button
        type="button"
        className="sidebar-dock-header-toggle"
        onClick={onToggleCollapsed}
        aria-expanded={!collapsed}
      >
        <ChevronDown size={12} className={["sidebar-dock-chevron", collapsed ? "" : "open"].filter(Boolean).join(" ")} />
        <span className="sidebar-dock-heading">
          <span className="sidebar-dock-title">{title}</span>
          {subtitle ? <span className="sidebar-dock-subtitle">{subtitle}</span> : null}
        </span>
      </button>
      {badge ? <div className="sidebar-dock-header-badge">{badge}</div> : null}
      {actions ? <div className="sidebar-dock-actions">{actions}</div> : null}
    </div>
  );
}

interface SidebarDockPanelProps {
  className?: string;
  children: ReactNode;
  collapsed?: boolean;
  height?: number;
  collapsedHeight?: number;
  maxHeight?: number | string;
  resizing?: boolean;
  resizeHandleProps?: ResizeHandleProps;
}

export function SidebarDockPanel({
  className,
  children,
  collapsed = false,
  height,
  collapsedHeight,
  maxHeight,
  resizing = false,
  resizeHandleProps,
}: SidebarDockPanelProps) {
  const classes = [
    "sidebar-dock-panel",
    collapsed ? "collapsed" : "",
    resizing ? "resizing" : "",
    className ?? "",
  ]
    .filter(Boolean)
    .join(" ");

  const panelStyle: CSSProperties = {};
  if (collapsed) {
    if (collapsedHeight !== undefined) {
      panelStyle.height = `${collapsedHeight}px`;
    }
  } else if (height !== undefined) {
    panelStyle.height = `${height}px`;
  }
  if (maxHeight !== undefined) {
    panelStyle.maxHeight = typeof maxHeight === "number" ? `${maxHeight}px` : maxHeight;
  }

  return (
    <section className={classes} style={panelStyle}>
      {resizeHandleProps ? (
        <div className="sidebar-dock-resize-handle" {...resizeHandleProps} />
      ) : null}
      <div className="sidebar-dock-panel-body">{children}</div>
    </section>
  );
}
