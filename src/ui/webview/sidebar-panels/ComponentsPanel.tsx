import { useMemo } from "react";
import { BookOpen, Boxes, Microchip } from "lucide-react";
import { LibraryPanel } from "./LibraryPanel";
import { PackagesPanel } from "./PackagesPanel";
import { PartsPanel } from "./PartsPanel";
import { ResizableSectionStack, SECTION_HEADER_HEIGHT } from "../common/components";

export const COMPONENTS_SECTION_KEYS = ["packages", "parts", "library"] as const;
export type ComponentsSectionKey = (typeof COMPONENTS_SECTION_KEYS)[number];

export const COMPONENTS_SECTION_DEFAULTS: Record<ComponentsSectionKey, boolean> = {
  packages: true,
  parts: true,
  library: true,
};

interface ComponentsPanelProps {
  expanded: Record<ComponentsSectionKey, boolean>;
  onToggleSection: (key: ComponentsSectionKey) => void;
  targetHeights: Record<ComponentsSectionKey, number>;
  onTargetHeightsChange: (heights: Record<ComponentsSectionKey, number>) => void;
  onCollapsedHeightChange?: (height: number) => void;
}

export function ComponentsPanel({
  expanded,
  onToggleSection,
  targetHeights,
  onTargetHeightsChange,
  onCollapsedHeightChange,
}: ComponentsPanelProps) {
  const sections = useMemo(() => [
    {
      key: "packages" as const,
      label: "Packages",
      icon: <Boxes size={16} />,
      content: <PackagesPanel />,
    },
    {
      key: "parts" as const,
      label: "Parts",
      icon: <Microchip size={16} />,
      content: <PartsPanel />,
    },
    {
      key: "library" as const,
      label: "Std Lib",
      icon: <BookOpen size={16} />,
      content: <LibraryPanel />,
    },
  ], []);

  return (
    <ResizableSectionStack
      sections={sections.map((section) => ({
        ...section,
        expanded: expanded[section.key],
      }))}
      onToggleSection={onToggleSection}
      targetHeights={targetHeights}
      onTargetHeightsChange={onTargetHeightsChange}
      onCollapsedHeightChange={onCollapsedHeightChange}
      headerHeight={SECTION_HEADER_HEIGHT}
      panelClassName="components-panel-shell"
      stackClassName="stacked-layout"
      sectionClassName="stacked-section"
      headerClassName="section-header section-toggle"
      chevronClassName="section-chevron"
      copyClassName="section-copy"
      iconClassName="section-mark"
      headerVariant="icon-rail"
    />
  );
}
