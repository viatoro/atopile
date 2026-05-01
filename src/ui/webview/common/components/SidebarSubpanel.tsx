import type { ReactNode } from "react";

interface SidebarSubpanelProps {
  className?: string;
  children: ReactNode;
}

export function SidebarSubpanel({ className, children }: SidebarSubpanelProps) {
  return (
    <div className={["sidebar-panel", className ?? ""].filter(Boolean).join(" ")}>
      {children}
    </div>
  );
}
