/**
 * Build selector combobox with searchable dropdown and rich build rows.
 * Shared between the VS Code log panel and the dev viewer.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";
import type { Build } from "../../protocol/generated-types";
import { STATUS_ICONS, formatDuration } from "../common/utils";
import { formatPath } from "../../protocol/paths";

function StatusIcon({ status }: { status: Build["status"] }) {
  const Icon = STATUS_ICONS[status];
  if (!Icon) return null;
  return <Icon size={12} className={`lv-si-${status}`} />;
}

function buildSearchText(build: Build): string {
  return [build.projectName, build.name, build.status, build.projectRoot, build.buildId]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

function formatBuildTime(startedAt: number | null): string {
  if (!startedAt) return "";
  const d = new Date(startedAt * 1000);
  const now = new Date();
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (d.toDateString() === now.toDateString()) return time;
  return `${d.toLocaleDateString([], { month: "short", day: "numeric" })} ${time}`;
}

function BuildRowInline({ build }: { build: Build }) {
  const project = build.projectName || formatPath(build.projectRoot ?? "");
  const name = build.name || "default";
  const time = formatBuildTime(build.startedAt);
  const duration = build.elapsedSeconds > 0 ? formatDuration(build.elapsedSeconds) : "";

  return (
    <>
      <span className="lv-build-option-project">{project}</span>
      <span className="lv-build-option-sep">/</span>
      <span className="lv-build-option-name">{name}</span>
      <span className="lv-build-option-time">{time}</span>
      {duration && <span className="lv-build-option-duration">{duration}</span>}
      <StatusIcon status={build.status} />
    </>
  );
}

export function BuildCombobox({
  builds,
  value,
  onSelect,
}: {
  builds: Build[];
  value: string;
  onSelect: (buildId: string) => void;
}) {
  const [isOpen, setIsOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [highlightedIndex, setHighlightedIndex] = useState(0);
  const ref = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const filtered = useMemo(
    () =>
      search
        ? builds.filter((b) => buildSearchText(b).includes(search.toLowerCase()))
        : builds,
    [builds, search],
  );

  useEffect(() => {
    setHighlightedIndex(0);
  }, [filtered.length]);

  useEffect(() => {
    if (!isOpen) return;
    const handleClickOutside = (e: MouseEvent) => {
      if (ref.current?.contains(e.target as Node)) return;
      setIsOpen(false);
      setSearch("");
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [isOpen]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Escape") {
        setIsOpen(false);
        setSearch("");
        inputRef.current?.blur();
        return;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        if (!isOpen) { setIsOpen(true); return; }
        setHighlightedIndex((i) => Math.min(i + 1, filtered.length - 1));
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setHighlightedIndex((i) => Math.max(i - 1, 0));
        return;
      }
      if (e.key === "Enter") {
        e.preventDefault();
        const build = filtered[highlightedIndex];
        if (!build?.buildId) return;
        onSelect(build.buildId);
        setIsOpen(false);
        setSearch("");
        inputRef.current?.blur();
      }
    },
    [filtered, highlightedIndex, isOpen, onSelect],
  );

  const selected = builds.find((b) => b.buildId === value);

  return (
    <div className="lv-build-combobox" ref={ref}>
      {isOpen ? (
        <div className="lv-build-combobox-input-wrapper open" onClick={() => inputRef.current?.focus()}>
          <input
            ref={inputRef}
            type="text"
            className="lv-build-combobox-input"
            placeholder="Search builds..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={handleKeyDown}
            autoFocus
          />
          <ChevronDown size={14} className="lv-build-combobox-chevron open" />
        </div>
      ) : (
        <button
          className="lv-build-combobox-closed"
          onClick={() => { setIsOpen(true); }}
        >
          {selected ? <BuildRowInline build={selected} /> : <span className="lv-build-option-name">Select build...</span>}
          <ChevronDown size={14} className="lv-build-combobox-chevron" />
        </button>
      )}
      {isOpen && (
        <div className="lv-build-combobox-dropdown">
          {filtered.length === 0 ? (
            <div className="lv-build-combobox-empty">No matching builds</div>
          ) : (
            filtered.map((build, index) => (
              <button
                key={build.buildId}
                className={`lv-build-combobox-option ${build.buildId === value ? "active" : ""} ${index === highlightedIndex ? "highlighted" : ""}`}
                onClick={() => {
                  if (build.buildId) onSelect(build.buildId);
                  setIsOpen(false);
                  setSearch("");
                }}
                onMouseEnter={() => setHighlightedIndex(index)}
              >
                <BuildRowInline build={build} />
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}
