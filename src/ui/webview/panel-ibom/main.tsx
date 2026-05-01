import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CircuitBoard } from "lucide-react";
import { render } from "../common/render";
import { NoDataMessage } from "../common/components";
import { WebviewRpcClient, rpcClient } from "../common/webviewRpcClient";
import { RpcLayoutClient, type Editor, type FootprintDecoration } from "../common/layout";
import { createWebviewLogger } from "../common/logger";
import { useBomRefresh } from "../common/useBomRefresh";

import { BomSidebar } from "./components/BomSidebar";
import { LayoutViewerWrapper, type IbomViewerRef } from "./components/LayoutViewerWrapper";
import { buildBomGroups } from "./utils/bomGrouping";
import { TYPE_COLORS, DEFAULT_COLOR } from "./utils/colors";
import type { BomGroupMember } from "./utils/types";
import "./main.css";

const logger = createWebviewLogger("PanelIBom");

function App() {
  const { projectState, bomData } = useBomRefresh();
  const layoutData = WebviewRpcClient.useSubscribe("layoutData");
  const selectedBuildInProgress = WebviewRpcClient.useSubscribe("selectedBuildInProgress");

  const viewerRef = useRef<IbomViewerRef>(null);
  const [editor, setEditor] = useState<Editor | null>(null);
  const fpUuidToGroupIdRef = useRef<Map<string, string>>(new Map());
  const fpUuidToDesignatorRef = useRef<Map<string, string>>(new Map());
  const designatorToFpUuidRef = useRef<Map<string, string>>(new Map());
  const [selectedGroupId, setSelectedGroupId] = useState<string | null>(null);
  const [selectedDesignator, setSelectedDesignator] = useState<string | null>(null);
  const [hoveredGroupId, setHoveredGroupId] = useState<string | null>(null);   // sidebar hover (highlights all group members)
  const [hoveredFpUuid, setHoveredFpUuid] = useState<string | null>(null);     // PCB hover (highlights single footprint)
  const [sidebarWidth, setSidebarWidth] = useState(360);

  const panelClient = useMemo(
    () => (rpcClient ? new RpcLayoutClient(rpcClient, logger) : null),
    [],
  );

  // Build BOM groups from BOM data alone
  const { groups, designatorToGroupId } = useMemo(
    () => buildBomGroups(bomData),
    [bomData],
  );

  // Clear selection when groups change
  useEffect(() => {
    setSelectedGroupId(null);
    setSelectedDesignator(null);
    setHoveredGroupId(null);
    setHoveredFpUuid(null);
  }, [groups]);

  // Build decoration map and wire editor callbacks
  useEffect(() => {
    if (!editor) return;

    // Build reverse lookups
    const fpUuidToGroupId = new Map<string, string>();
    const fpUuidToDesignator = new Map<string, string>();
    const designatorToFpUuid = new Map<string, string>();
    for (const group of groups) {
      for (const member of group.members) {
        const uuid = editor.getFootprintUuidByDesignator(member.designator);
        if (uuid) {
          fpUuidToGroupId.set(uuid, group.id);
          fpUuidToDesignator.set(uuid, member.designator);
          designatorToFpUuid.set(member.designator, uuid);
        }
      }
    }
    fpUuidToGroupIdRef.current = fpUuidToGroupId;
    fpUuidToDesignatorRef.current = fpUuidToDesignator;
    designatorToFpUuidRef.current = designatorToFpUuid;

    // Helper to create a decoration for a group's type color
    function groupDec(group: { bomComponent: { type?: string | null } | null }): FootprintDecoration {
      const color = TYPE_COLORS[group.bomComponent?.type ?? ""] ?? DEFAULT_COLOR;
      const [cr, cg, cb] = color;
      return { color, highlightColor: [cr, cg, cb, 0.85], highlighted: true };
    }

    const fpDecs = new Map<string, FootprintDecoration>();

    // Sidebar hover: highlight all footprints in the hovered group
    if (hoveredGroupId) {
      const group = groups.find((g) => g.id === hoveredGroupId);
      if (group) {
        const dec = groupDec(group);
        for (const member of group.members) {
          const uuid = designatorToFpUuid.get(member.designator);
          if (uuid) fpDecs.set(uuid, dec);
        }
      }
    }

    // Selected group: highlight all footprints (or single designator if drilled in)
    if (selectedGroupId) {
      const group = groups.find((g) => g.id === selectedGroupId);
      if (group) {
        const dec = groupDec(group);
        if (selectedDesignator) {
          // Only highlight the single selected designator
          const uuid = designatorToFpUuid.get(selectedDesignator);
          if (uuid) fpDecs.set(uuid, dec);
        } else {
          for (const member of group.members) {
            const uuid = designatorToFpUuid.get(member.designator);
            if (uuid) fpDecs.set(uuid, dec);
          }
        }
      }
    }

    // PCB hover: highlight only the single hovered footprint
    if (hoveredFpUuid && !hoveredGroupId) {
      const groupId = fpUuidToGroupId.get(hoveredFpUuid);
      const group = groupId ? groups.find((g) => g.id === groupId) : null;
      const dec: FootprintDecoration = group ? groupDec(group) : { color: DEFAULT_COLOR, highlightColor: [0.6, 0.6, 0.6, 0.85], highlighted: true };
      fpDecs.set(hoveredFpUuid, dec);
    }

    editor.setDecorations({ footprints: fpDecs });

    editor.setOnFootprintHover((fpUuid) => {
      setHoveredFpUuid(fpUuid);
    });

    editor.setOnFootprintClick((fpUuid) => {
      if (!fpUuid) {
        setSelectedGroupId(null);
        setSelectedDesignator(null);
        return;
      }
      const groupId = fpUuidToGroupId.get(fpUuid) ?? null;
      setSelectedGroupId(groupId);
      setSelectedDesignator(fpUuidToDesignator.get(fpUuid) ?? null);
    });

    return () => {
      editor.setOnFootprintHover(null);
      editor.setOnFootprintClick(null);
    };
  }, [editor, groups, selectedGroupId, selectedDesignator, hoveredGroupId, hoveredFpUuid]);

  // Editor ready callback from LayoutViewerWrapper
  const handleEditorReady = useCallback((ed: Editor | null) => {
    setEditor(ed);
  }, []);

  // Sidebar handlers
  const handleSelectGroup = useCallback(
    (groupId: string | null) => {
      setSelectedGroupId(groupId);
      setSelectedDesignator(null);
    },
    [],
  );

  const handleSelectDesignator = useCallback(
    (member: BomGroupMember) => {
      const groupId = designatorToGroupId.get(member.designator) ?? null;
      setSelectedGroupId(groupId);
      setSelectedDesignator(member.designator);
    },
    [designatorToGroupId],
  );

  const handleSelectAllInGroup = useCallback(
    (groupId: string) => {
      setSelectedGroupId(groupId);
      setSelectedDesignator(null);
    },
    [],
  );


  // Sidebar resize
  const resizeRef = useRef<{ startX: number; startWidth: number } | null>(null);

  const onResizePointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      e.preventDefault();
      resizeRef.current = { startX: e.clientX, startWidth: sidebarWidth };
      (e.target as HTMLElement).setPointerCapture(e.pointerId);
    },
    [sidebarWidth],
  );

  const onResizePointerMove = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (!resizeRef.current) return;
      const delta = e.clientX - resizeRef.current.startX;
      setSidebarWidth(Math.max(240, Math.min(800, resizeRef.current.startWidth + delta)));
    },
    [],
  );

  const onResizePointerUp = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (resizeRef.current) {
        (e.target as HTMLElement).releasePointerCapture(e.pointerId);
        resizeRef.current = null;
      }
    },
    [],
  );

  // Derive PCB hover state for sidebar
  const pcbHoveredGroupId = hoveredFpUuid ? (fpUuidToGroupIdRef.current.get(hoveredFpUuid) ?? null) : null;
  const pcbHoveredDesignator = hoveredFpUuid ? (fpUuidToDesignatorRef.current.get(hoveredFpUuid) ?? null) : null;
  // The group shown in the detail panel: selected > sidebar hover > PCB hover
  const detailGroupId = selectedGroupId ?? hoveredGroupId ?? pcbHoveredGroupId;
  // The designator highlighted in the detail panel
  const activeDesignator = selectedDesignator ?? pcbHoveredDesignator;
  // The row highlighted in the BOM list (combine sidebar + PCB hover)
  const highlightedRowId = hoveredGroupId ?? pcbHoveredGroupId;

  const hasSelection = Boolean(projectState.selectedProjectRoot && projectState.selectedTarget);
  const hasBomData = groups.length > 0;

  return (
    <NoDataMessage
      icon={<CircuitBoard size={24} />}
      noun="BOM"
      hasSelection={hasSelection}
      isLoading={bomData.loading}
      buildInProgress={selectedBuildInProgress}
      error={bomData.error}
      hasData={hasBomData}
    >
      <div className="ibom-app">
        <div className="ibom-sidebar-container" style={{ width: sidebarWidth }}>
          <BomSidebar
            groups={groups}
            bomData={bomData}
            selectedGroupId={selectedGroupId}
            hoveredGroupId={highlightedRowId}
            detailGroupId={detailGroupId}
            activeDesignator={activeDesignator}
            onSelectGroup={handleSelectGroup}
            onHoverGroup={setHoveredGroupId}
            onSelectDesignator={handleSelectDesignator}
            onSelectAllInGroup={handleSelectAllInGroup}
          />
        </div>
        <div
          className="ibom-resize-handle"
          onPointerDown={onResizePointerDown}
          onPointerMove={onResizePointerMove}
          onPointerUp={onResizePointerUp}
          onPointerCancel={onResizePointerUp}
        />
        <div className="ibom-viewer-container">
          <LayoutViewerWrapper
            ref={viewerRef}
            client={panelClient}
            layoutPath={layoutData.path}
            layoutError={layoutData.error}
            onEditorReady={handleEditorReady}
          />
        </div>
      </div>
    </NoDataMessage>
  );
}

render(App);
