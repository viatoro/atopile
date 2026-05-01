import { useMemo } from "react";
import { FolderOpen, GitBranch } from "lucide-react";
import { FilesPanel } from "./FilesPanel";
import { ResizableSectionStack } from "../common/components";
import { StructurePanel } from "./StructurePanel";


export const PROJECT_SECTION_KEYS = ["files", "structure"] as const;
export type ProjectSectionKey = (typeof PROJECT_SECTION_KEYS)[number];

export const PROJECT_SECTION_DEFAULTS: Record<ProjectSectionKey, boolean> = {
  files: true,
  structure: false,
};

interface ProjectPanelProps {
  expanded: Record<ProjectSectionKey, boolean>;
  onToggleSection: (key: ProjectSectionKey) => void;
  targetHeights: Record<ProjectSectionKey, number>;
  onTargetHeightsChange: (heights: Record<ProjectSectionKey, number>) => void;
  onCollapsedHeightChange?: (height: number) => void;
}

export function ProjectPanel({
  expanded,
  onToggleSection,
  targetHeights,
  onTargetHeightsChange,
  onCollapsedHeightChange,
}: ProjectPanelProps) {
  const sections = useMemo(() => [
    {
      key: "files" as const,
      label: "Files",
      icon: <FolderOpen size={18} />,
      content: <FilesPanel />,
    },
    {
      key: "structure" as const,
      label: "Symbolic",
      icon: <GitBranch size={18} />,
      content: <StructurePanel hideHeader />,
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
      panelClassName="project-panel-shell"
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
