import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { RegexSearchBar } from "../../common/components";
import { createSearchMatcher } from "../../common/utils/searchUtils";
import { formatCurrency } from "../../common/utils/format";
import { BomRow } from "./BomRow";
import { ComponentDetailPanel } from "./ComponentDetailPanel";
import type { BomGroup, BomGroupMember } from "../utils/types";
import type { UiBOMData } from "../../../protocol/generated-types";

interface BomSidebarProps {
  groups: BomGroup[];
  bomData: UiBOMData;
  selectedGroupId: string | null;
  hoveredGroupId: string | null;
  detailGroupId: string | null;
  activeDesignator: string | null;
  onSelectGroup: (groupId: string | null) => void;
  onHoverGroup: (groupId: string | null) => void;
  onSelectDesignator: (member: BomGroupMember) => void;
  onSelectAllInGroup: (groupId: string) => void;
}

export function BomSidebar({
  groups,
  bomData,
  selectedGroupId,
  hoveredGroupId,
  detailGroupId,
  activeDesignator,
  onSelectGroup,
  onHoverGroup,
  onSelectDesignator,
  onSelectAllInGroup,
}: BomSidebarProps) {
  const [search, setSearch] = useState("");
  const [isRegex, setIsRegex] = useState(false);
  const [caseSensitive, setCaseSensitive] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);
  const detailGroup = groups.find((g) => g.id === detailGroupId) ?? null;

  const filteredGroups = useMemo(() => {
    if (!search) return groups;
    const matcher = createSearchMatcher(search, { isRegex, caseSensitive });
    return groups.filter((g) => {
      const designators = g.members.map((m) => m.designator).join(" ");
      const mpn = g.bomComponent?.mpn ?? "";
      const value = g.value;
      const text = `${designators} ${mpn} ${value}`;
      return matcher(text).matches;
    });
  }, [groups, search, isRegex, caseSensitive]);

  // Build a flat list of individually matching members when search targets designators
  const matchingMembers = useMemo(() => {
    if (!search) return null;
    const matcher = createSearchMatcher(search, { isRegex, caseSensitive });
    const matches: { groupId: string; member: BomGroupMember }[] = [];
    for (const g of filteredGroups) {
      for (const m of g.members) {
        if (matcher(m.designator).matches) {
          matches.push({ groupId: g.id, member: m });
        }
      }
    }
    return matches.length > 0 ? matches : null;
  }, [search, isRegex, caseSensitive, filteredGroups]);

  // When search matches designators, auto-select the first one
  useEffect(() => {
    if (matchingMembers && matchingMembers.length >= 1) {
      const { groupId, member } = matchingMembers[0]!;
      onSelectGroup(groupId);
      onSelectDesignator(member);
    }
  }, [matchingMembers, onSelectGroup, onSelectDesignator]);

  const totalComponents = groups.reduce((sum, g) => sum + g.quantity, 0);

  // Keyboard navigation
  const selectedIndex = filteredGroups.findIndex((g) => g.id === selectedGroupId);

  const navigate = useCallback(
    (delta: number) => {
      if (matchingMembers && matchingMembers.length > 0) {
        const currentIdx = activeDesignator
          ? matchingMembers.findIndex((m) => m.member.designator === activeDesignator)
          : -1;
        let newIdx: number;
        if (currentIdx < 0) {
          newIdx = delta > 0 ? 0 : matchingMembers.length - 1;
        } else {
          newIdx = (currentIdx + delta + matchingMembers.length) % matchingMembers.length;
        }
        const { groupId, member } = matchingMembers[newIdx]!;
        onSelectGroup(groupId);
        onSelectDesignator(member);
        return;
      }

      if (filteredGroups.length === 0) return;
      let newIndex: number;
      if (selectedIndex < 0) {
        newIndex = delta > 0 ? 0 : filteredGroups.length - 1;
      } else {
        newIndex = (selectedIndex + delta + filteredGroups.length) % filteredGroups.length;
      }
      const group = filteredGroups[newIndex];
      if (group) {
        onSelectGroup(group.id);
      }
    },
    [matchingMembers, activeDesignator, filteredGroups, selectedIndex, onSelectGroup, onSelectDesignator],
  );

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "/") {
        if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
        e.preventDefault();
        setIsRegex(true);
        setCaseSensitive(true);
        listRef.current?.closest(".ibom-sidebar")?.querySelector<HTMLInputElement>("input")?.focus();
        return;
      }
      if (e.key === "Enter") {
        e.preventDefault();
        navigate(e.shiftKey ? -1 : 1);
        return;
      }
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      switch (e.key) {
        case "j":
          navigate(1);
          break;
        case "k":
          navigate(-1);
          break;
        case "Escape":
          onSelectGroup(null);
          break;
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [navigate, onSelectGroup]);

  // Scroll selected row into view
  useEffect(() => {
    if (selectedGroupId && listRef.current) {
      const el = listRef.current.querySelector(".ibom-row.selected");
      if (el) {
        el.scrollIntoView({ block: "nearest" });
      }
    }
  }, [selectedGroupId]);

  return (
    <div className="ibom-sidebar">
      <div className="ibom-sidebar-header">
        <RegexSearchBar
          value={search}
          onChange={setSearch}
          isRegex={isRegex}
          onRegexChange={setIsRegex}
          caseSensitive={caseSensitive}
          onCaseSensitiveChange={setCaseSensitive}
          placeholder="Search designator, value, MPN..."
        />
        <div className="ibom-summary">
          <span className="ibom-summary-item">
            <span className="ibom-summary-value">{totalComponents}</span>
            <span className="ibom-summary-label">Total parts</span>
          </span>
          <span className="ibom-summary-sep">|</span>
          <span className="ibom-summary-item">
            <span className="ibom-summary-value">{groups.length}</span>
            <span className="ibom-summary-label">Unique parts</span>
          </span>
          {bomData.estimatedCost != null && (
            <span className="ibom-summary-item primary">
              <span className="ibom-summary-value">{formatCurrency(bomData.estimatedCost)}</span>
              <span className="ibom-summary-label">cost</span>
            </span>
          )}
          {bomData.outOfStock > 0 && (
            <span className="ibom-summary-item warning">
              <span className="ibom-summary-value">{bomData.outOfStock}</span>
              <span className="ibom-summary-label">OOS</span>
            </span>
          )}
        </div>
      </div>

      <div className="ibom-list" ref={listRef}>
        {filteredGroups.map((group) => (
          <BomRow
            key={group.id}
            group={group}
            selected={group.id === selectedGroupId}
            hovered={group.id === hoveredGroupId}
            onClick={() => onSelectGroup(group.id === selectedGroupId ? null : group.id)}
            onMouseEnter={() => onHoverGroup(group.id)}
            onMouseLeave={() => onHoverGroup(null)}
          />
        ))}
        {filteredGroups.length === 0 && (
          <div className="ibom-empty">
            {search ? "No matches" : "No components"}
          </div>
        )}
      </div>

      <div className="ibom-detail-section">
        <ComponentDetailPanel
          group={detailGroup}
          activeDesignator={activeDesignator}
          onDesignatorClick={onSelectDesignator}
          onSelectAll={() => { if (detailGroup) onSelectAllInGroup(detailGroup.id); }}
        />
      </div>
    </div>
  );
}
