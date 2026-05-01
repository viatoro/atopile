import {
  Eye,
  Files,
  Package,
  Wrench,
} from "lucide-react";
import "./TabBar.css";

export type TabId =
  | "project"
  | "components"
  | "inspect"
  | "tools";

interface TabBarProps {
  activeTab: TabId;
  onTabChange: (tab: TabId) => void;
}

export function TabBar({ activeTab, onTabChange }: TabBarProps) {
  const tabs: Array<{
    id: TabId;
    label: string;
    tooltip: string;
    icon: React.ReactNode;
  }> = [
    { id: "project", label: "Project", tooltip: "Project", icon: <Files size={15} /> },
    { id: "components", label: "Components", tooltip: "Components", icon: <Package size={15} /> },
    { id: "inspect", label: "Inspect", tooltip: "Inspect", icon: <Eye size={15} /> },
    { id: "tools", label: "Tools", tooltip: "Tools", icon: <Wrench size={15} /> },
  ];

  return (
    <div className="tab-bar" role="tablist" aria-label="Sidebar sections">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          className={`tab-button${activeTab === tab.id ? " active" : ""}`}
          data-tooltip={tab.tooltip}
          data-active={activeTab === tab.id ? "true" : "false"}
          role="tab"
          aria-selected={activeTab === tab.id}
          onClick={() => onTabChange(tab.id)}
        >
          <span className="tab-icon">{tab.icon}</span>
          <span className="tab-label">{tab.label}</span>
        </button>
      ))}
    </div>
  );
}
