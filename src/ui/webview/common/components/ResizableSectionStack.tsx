import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { ChevronRight } from "lucide-react";
import { SectionLayoutModel } from "./SectionLayoutModel";
import "./ResizableSectionStack.css";
export const SECTION_HEADER_HEIGHT = 44;
const SECTION_INITIAL_HEIGHT = 240;

function clampTargetHeight(height: number, maxHeight: number): number {
  return Math.max(0, Math.min(height, maxHeight));
}

function useSectionResize({
  onStart,
  onMove,
}: {
  onStart: () => void;
  onMove: (delta: number) => void;
}) {
  const [isResizing, setIsResizing] = useState(false);
  const startYRef = useRef<number | null>(null);

  const endResize = useCallback((element?: HTMLElement | null, pointerId?: number) => {
    if (element && pointerId !== undefined && element.hasPointerCapture(pointerId)) {
      element.releasePointerCapture(pointerId);
    }
    startYRef.current = null;
    setIsResizing(false);
  }, []);

  const onPointerDown = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.stopPropagation();
    startYRef.current = event.clientY;
    setIsResizing(true);
    event.currentTarget.setPointerCapture(event.pointerId);
    onStart();
  }, [onStart]);

  const onPointerMove = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    const startY = startYRef.current;
    if (startY === null) return;
    onMove(event.clientY - startY);
  }, [onMove]);

  const onPointerUp = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    endResize(event.currentTarget, event.pointerId);
  }, [endResize]);

  const onPointerCancel = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    endResize(event.currentTarget, event.pointerId);
  }, [endResize]);

  const reset = useCallback(() => {
    startYRef.current = null;
    setIsResizing(false);
  }, []);

  return {
    isResizing,
    reset,
    resizeHandleProps: {
      onPointerDown,
      onPointerMove,
      onPointerUp,
      onPointerCancel,
    },
  };
}

export interface ResizableSectionDefinition<T extends string> {
  key: T;
  label: string;
  expanded: boolean;
  icon?: React.ReactNode;
  content: React.ReactNode;
}

interface ResizableSectionStackProps<T extends string> {
  sections: ResizableSectionDefinition<T>[];
  onToggleSection: (key: T) => void;
  /** Controlled target heights — persisted across tab switches. */
  targetHeights?: Record<T, number>;
  onTargetHeightsChange?: (heights: Record<T, number>) => void;
  /** Reports the collapsed stack height (sum of all section headers). */
  onCollapsedHeightChange?: (height: number) => void;
  headerHeight?: number;
  panelClassName: string;
  stackClassName: string;
  sectionClassName: string;
  headerClassName: string;
  chevronClassName: string;
  copyClassName: string;
  iconClassName?: string;
  headerVariant?: "default" | "icon-rail";
}

export function ResizableSectionStack<T extends string>({
  sections,
  onToggleSection,
  targetHeights: controlledTargetHeights,
  onTargetHeightsChange,
  onCollapsedHeightChange,
  headerHeight = SECTION_HEADER_HEIGHT,
  panelClassName,
  stackClassName,
  sectionClassName,
  headerClassName,
  chevronClassName,
  copyClassName,
  iconClassName,
  headerVariant = "default",
}: ResizableSectionStackProps<T>) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [measuredHeight, setMeasuredHeight] = useState(520);
  const [measuredHeaderHeights, setMeasuredHeaderHeights] = useState<Record<T, number>>(() =>
    Object.fromEntries(sections.map((section) => [section.key, headerHeight])) as Record<T, number>,
  );
  const [uncontrolledTargetHeights, setUncontrolledTargetHeights] = useState<Record<T, number>>(() =>
    Object.fromEntries(sections.map((section) => [section.key, SECTION_INITIAL_HEIGHT])) as Record<T, number>,
  );
  const targetHeights = controlledTargetHeights ?? uncontrolledTargetHeights;

  const totalHeaderHeight = sections.reduce(
    (sum, section) => sum + (measuredHeaderHeights[section.key] ?? headerHeight),
    0,
  );
  const availableHeight = Math.max(totalHeaderHeight, measuredHeight);
  const maxTargetHeight = Number.POSITIVE_INFINITY;

  useEffect(() => {
    const element = containerRef.current;
    if (!element) {
      return;
    }

    const updateHeight = () => {
      setMeasuredHeight(element.clientHeight);
    };

    updateHeight();
    const observer = new ResizeObserver(updateHeight);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (controlledTargetHeights) return;
    setUncontrolledTargetHeights((prev) =>
      Object.fromEntries(
        sections.map((section) => [
          section.key,
          prev[section.key] ?? SECTION_INITIAL_HEIGHT,
        ]),
      ) as Record<T, number>,
    );
  }, [controlledTargetHeights, sections]);

  useEffect(() => {
    setMeasuredHeaderHeights((prev) =>
      Object.fromEntries(
        sections.map((section) => [
          section.key,
          prev[section.key] ?? headerHeight,
        ]),
      ) as Record<T, number>,
    );
  }, [headerHeight, sections]);

  const layoutModels = useMemo(
    () =>
      SectionLayoutModel.solve(
        sections.map(
          (section) =>
            new SectionLayoutModel({
              key: section.key,
              expanded: section.expanded,
              headerHeight: measuredHeaderHeights[section.key] ?? headerHeight,
              targetHeight: section.expanded ? (targetHeights[section.key] ?? SECTION_INITIAL_HEIGHT) : 0,
            }),
        ),
        availableHeight,
      ),
    [availableHeight, headerHeight, targetHeights, measuredHeaderHeights, sections],
  );

  useEffect(() => {
    onCollapsedHeightChange?.(totalHeaderHeight);
  }, [onCollapsedHeightChange, totalHeaderHeight]);

  const layoutModelsRef = useRef(layoutModels);
  layoutModelsRef.current = layoutModels;
  const sectionsRef = useRef(sections);
  sectionsRef.current = sections;
  const heightsRef = useRef(targetHeights);
  heightsRef.current = targetHeights;

  const dragRef = useRef<{
    sourceKey: T;
    targetKey: T;
    startSourceHeight: number;
    startTargetHeight: number;
    scale: number;
  } | null>(null);

  const onDividerDragStart = useCallback((dividerIndex: number) => {
    const currentSections = sectionsRef.current;
    let sourceIndex = -1;
    for (let i = dividerIndex; i >= 0; i--) {
      if (currentSections[i].expanded) {
        sourceIndex = i;
        break;
      }
    }
    let targetIndex = -1;
    for (let i = dividerIndex + 1; i < currentSections.length; i++) {
      if (currentSections[i].expanded) {
        targetIndex = i;
        break;
      }
    }
    if (sourceIndex < 0 || targetIndex < 0) {
      dragRef.current = null;
      return;
    }
    const sourceKey = currentSections[sourceIndex].key;
    const targetKey = currentSections[targetIndex].key;
    const heights = heightsRef.current;
    const sourceModel = layoutModelsRef.current.find((m) => m.key === sourceKey);
    const scale = sourceModel && sourceModel.targetHeight > 0
      ? sourceModel.actualHeight / sourceModel.targetHeight
      : 1;
    dragRef.current = {
      sourceKey,
      targetKey,
      startSourceHeight: heights[sourceKey] ?? SECTION_INITIAL_HEIGHT,
      startTargetHeight: heights[targetKey] ?? SECTION_INITIAL_HEIGHT,
      scale: scale || 1,
    };
  }, []);

  const onDividerDragMove = useCallback((delta: number) => {
    const state = dragRef.current;
    if (!state) return;
    const { sourceKey, targetKey, startSourceHeight, startTargetHeight, scale } = state;
    const rawDelta = delta / scale;
    const bounded = rawDelta > 0
      ? Math.min(rawDelta, startTargetHeight)
      : -Math.min(-rawDelta, startSourceHeight);
    const sourceNew = clampTargetHeight(startSourceHeight + bounded, maxTargetHeight);
    const targetNew = clampTargetHeight(startTargetHeight - bounded, maxTargetHeight);
    const heights = heightsRef.current;
    const updated = { ...heights, [sourceKey]: sourceNew, [targetKey]: targetNew } as Record<T, number>;
    if (controlledTargetHeights) {
      onTargetHeightsChange?.(updated);
    } else {
      setUncontrolledTargetHeights(updated);
    }
  }, [controlledTargetHeights, maxTargetHeight, onTargetHeightsChange]);

  return (
    <div
      ref={containerRef}
      className={`sidebar-panel sidebar-panel-shell resizable-panel-shell ${panelClassName}`}
      style={
        {
          "--sidebar-section-header-height": `${headerHeight}px`,
        } as CSSProperties
      }
    >
      <div className={`resizable-section-stack ${stackClassName}`}>
        {sections.map((section, index) => {
          const layoutModel = layoutModels.find((model) => model.key === section.key);
          const isLast = index === sections.length - 1;
          return (
            <Fragment key={section.key}>
              <ResizableSection
                section={section}
                actualHeight={layoutModel?.actualHeight ?? 0}
                onToggle={() => onToggleSection(section.key)}
                onHeaderHeightChange={(nextHeight) =>
                  setMeasuredHeaderHeights((prev) => (
                    prev[section.key] === nextHeight
                      ? prev
                      : {
                          ...prev,
                          [section.key]: nextHeight,
                        }
                  ))
                }
                sectionClassName={sectionClassName}
                headerClassName={headerClassName}
                chevronClassName={chevronClassName}
                copyClassName={copyClassName}
                iconClassName={iconClassName}
                headerVariant={headerVariant}
              />
              {!isLast ? (
                <ResizableSectionDivider
                  dividerIndex={index}
                  sectionKey={section.key}
                  onDragStart={onDividerDragStart}
                  onDragMove={onDividerDragMove}
                />
              ) : null}
            </Fragment>
          );
        })}
      </div>
    </div>
  );
}

function ResizableSection<T extends string>({
  section,
  actualHeight,
  onToggle,
  onHeaderHeightChange,
  sectionClassName,
  headerClassName,
  chevronClassName,
  copyClassName,
  iconClassName,
  headerVariant,
}: {
  section: ResizableSectionDefinition<T>;
  actualHeight: number;
  onToggle: () => void;
  onHeaderHeightChange: (height: number) => void;
  sectionClassName: string;
  headerClassName: string;
  chevronClassName: string;
  copyClassName: string;
  iconClassName?: string;
  headerVariant: "default" | "icon-rail";
}) {
  const headerRef = useRef<HTMLButtonElement | null>(null);
  const isCompressed = actualHeight <= 0.5;
  const isVisuallyExpanded = section.expanded && !isCompressed;

  // Scale padding proportionally as the body shrinks below the
  // natural padding so there is no dead-space bump.
  const fullPadding = 2;
  const paddingScale = Math.min(1, actualHeight / Math.max(fullPadding, 1));
  const topPad = fullPadding * paddingScale;
  const bottomPad = 0;

  useEffect(() => {
    const element = headerRef.current;
    if (!element) {
      return;
    }

    const updateHeight = () => {
      onHeaderHeightChange(element.offsetHeight);
    };

    updateHeight();
    const observer = new ResizeObserver(updateHeight);
    observer.observe(element);
    return () => observer.disconnect();
  }, [onHeaderHeightChange]);

  return (
    <section className={sectionClassName}>
      <button
        ref={headerRef}
        className={headerClassName}
        onClick={onToggle}
        aria-expanded={section.expanded}
        data-visual-expanded={isVisuallyExpanded}
      >
        {headerVariant === "icon-rail" ? (
          <>
            <span className={iconClassName}>{section.icon}</span>
            <span className={copyClassName}>
              <h3>{section.label}</h3>
            </span>
            <span className={`${chevronClassName}${isVisuallyExpanded ? " expanded" : ""}`}>
              <ChevronRight size={14} />
            </span>
          </>
        ) : (
          <>
            <span className={`${chevronClassName}${isVisuallyExpanded ? " expanded" : ""}`}>
              <ChevronRight size={14} />
            </span>
            <span className={copyClassName}>
              <h3>{section.label}</h3>
            </span>
          </>
        )}
      </button>
      {section.expanded ? (
        <div
          className={`resizable-section-content${isCompressed ? " compressed" : ""}`}
          style={{
            height: `${actualHeight}px`,
            padding: `${topPad}px 0 ${bottomPad}px`,
          }}
        >
          {section.content}
        </div>
      ) : null}
    </section>
  );
}

function ResizableSectionDivider<T extends string>({
  dividerIndex,
  sectionKey,
  onDragStart,
  onDragMove,
}: {
  dividerIndex: number;
  sectionKey: T;
  onDragStart: (dividerIndex: number) => void;
  onDragMove: (delta: number) => void;
}) {
  const handleStart = useCallback(() => onDragStart(dividerIndex), [onDragStart, dividerIndex]);
  const resize = useSectionResize({ onStart: handleStart, onMove: onDragMove });
  return (
    <div className={`resizable-section-divider${resize.isResizing ? " resizing" : ""}`}>
      <div
        className="resizable-section-resize-handle"
        aria-label={`Resize panel above ${String(sectionKey)}`}
        {...resize.resizeHandleProps}
      />
    </div>
  );
}
