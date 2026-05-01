import { useCallback, useRef } from "react";

interface OverlaySliderProps {
    value: number;
    onChange: (v: number) => void;
    mode: "overlay-swap" | "overlay-alpha";
}

export function OverlaySlider({ value, onChange, mode }: OverlaySliderProps) {
    const containerRef = useRef<HTMLDivElement>(null);
    const dragging = useRef(false);

    const updateFromPointer = useCallback(
        (clientX: number) => {
            const el = containerRef.current;
            if (!el) return;
            const rect = el.getBoundingClientRect();
            const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
            onChange(ratio);
        },
        [onChange],
    );

    const onPointerDown = useCallback(
        (e: React.PointerEvent) => {
            dragging.current = true;
            (e.target as HTMLElement).setPointerCapture(e.pointerId);
            updateFromPointer(e.clientX);
        },
        [updateFromPointer],
    );

    const onPointerMove = useCallback(
        (e: React.PointerEvent) => {
            if (!dragging.current) return;
            updateFromPointer(e.clientX);
        },
        [updateFromPointer],
    );

    const onPointerUp = useCallback(() => {
        dragging.current = false;
    }, []);

    return (
        <div className="overlay-slider-container" ref={containerRef}>
            {mode === "overlay-swap" && (
                <div
                    className="overlay-swap-divider"
                    style={{ left: `${value * 100}%` }}
                    onPointerDown={onPointerDown}
                    onPointerMove={onPointerMove}
                    onPointerUp={onPointerUp}
                />
            )}
            <div className="overlay-slider-track">
                <input
                    type="range"
                    min={0}
                    max={100}
                    step={1}
                    value={Math.round(value * 100)}
                    onChange={(e) => onChange(Number(e.target.value) / 100)}
                />
            </div>
        </div>
    );
}
