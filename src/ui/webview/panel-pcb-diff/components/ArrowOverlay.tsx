import type { DiffElementStatus } from "../../common/diff/types";
import { STATUS_CSS_COLORS } from "../../common/diff/types";
import { elementId } from "../../common/diff/diff_state";
import type { DiffViewerHandle } from "./DiffViewerWrapper";
import { Vec2 } from "../../common/layout/math";

interface ArrowOverlayProps {
    elements: DiffElementStatus[];
    selectedId: string | null;
    handleA: DiffViewerHandle | null;
    handleB: DiffViewerHandle | null;
    containerWidth: number;
    /** Incremented on camera change to trigger re-render */
    cameraVersion?: number;
}

export function ArrowOverlay({
    elements,
    selectedId,
    handleA,
    handleB,
    containerWidth,
}: ArrowOverlayProps) {
    if (!handleA || !handleB || !selectedId) return null;

    const halfWidth = containerWidth / 2;
    const arrows: { x1: number; y1: number; x2: number; y2: number; color: string }[] = [];

    for (const el of elements) {
        const id = elementId(el);
        if (id !== selectedId) continue;
        if (el.status !== "moved" && el.status !== "modified") continue;
        if (!el.position_a || !el.position_b) continue;

        const screenA = handleA.camera.world_to_screen(
            new Vec2(el.position_a.x, el.position_a.y),
        );
        const screenB = handleB.camera.world_to_screen(
            new Vec2(el.position_b.x, el.position_b.y),
        );

        arrows.push({
            x1: screenA.x,
            y1: screenA.y,
            x2: halfWidth + screenB.x,
            y2: screenB.y,
            color: STATUS_CSS_COLORS[el.status],
        });
    }

    if (arrows.length === 0) return null;

    return (
        <svg className="pcb-diff-arrow-overlay">
            <defs>
                <marker
                    id="diff-arrow"
                    viewBox="0 0 10 7"
                    refX="10"
                    refY="3.5"
                    markerWidth="8"
                    markerHeight="6"
                    orient="auto-start-reverse"
                >
                    <polygon points="0 0, 10 3.5, 0 7" fill="context-stroke" />
                </marker>
            </defs>
            {arrows.map((a, i) => (
                <line
                    key={i}
                    x1={a.x1}
                    y1={a.y1}
                    x2={a.x2}
                    y2={a.y2}
                    stroke={a.color}
                    strokeWidth={2}
                    strokeDasharray="6,4"
                    markerEnd="url(#diff-arrow)"
                    opacity={0.8}
                />
            ))}
        </svg>
    );
}
