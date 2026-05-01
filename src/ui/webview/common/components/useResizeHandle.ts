import { useCallback, useRef, useState } from "react";

interface UseResizeHandleOptions {
  minHeight: number;
  initialHeight?: number;
  height?: number;
  maxHeight?: number;
  onHeightChange?: (height: number) => void;
  onDragStart?: () => void;
}

/** Hook for a pointer-draggable vertical resize handle. */
export function useResizeHandle({
  minHeight,
  initialHeight = minHeight,
  height: controlledHeight,
  maxHeight = Number.POSITIVE_INFINITY,
  onHeightChange,
  onDragStart,
}: UseResizeHandleOptions) {
  const [uncontrolledHeight, setUncontrolledHeight] = useState(initialHeight);
  const [isResizing, setIsResizing] = useState(false);
  const resizeStartRef = useRef<{ y: number; height: number } | null>(null);

  const height = controlledHeight ?? uncontrolledHeight;

  const clampHeight = useCallback(
    (nextHeight: number) => Math.max(minHeight, Math.min(nextHeight, maxHeight)),
    [maxHeight, minHeight],
  );

  const updateHeight = useCallback(
    (nextHeight: number) => {
      const clampedHeight = clampHeight(nextHeight);
      if (controlledHeight === undefined) {
        setUncontrolledHeight(clampedHeight);
      }
      onHeightChange?.(clampedHeight);
    },
    [clampHeight, controlledHeight, onHeightChange],
  );

  const endResize = useCallback((element?: HTMLElement | null, pointerId?: number) => {
    if (element && pointerId !== undefined && element.hasPointerCapture(pointerId)) {
      element.releasePointerCapture(pointerId);
    }
    resizeStartRef.current = null;
    setIsResizing(false);
  }, []);

  const onPointerDown = useCallback(
    (event: React.PointerEvent<HTMLElement>) => {
      event.preventDefault();
      onDragStart?.();
      resizeStartRef.current = { y: event.clientY, height };
      setIsResizing(true);
      event.currentTarget.setPointerCapture(event.pointerId);
    },
    [height, onDragStart],
  );

  const onPointerMove = useCallback(
    (event: React.PointerEvent<HTMLElement>) => {
      const start = resizeStartRef.current;
      if (!start) return;
      const delta = start.y - event.clientY;
      updateHeight(start.height + delta);
    },
    [updateHeight],
  );

  const onPointerUp = useCallback((event: React.PointerEvent<HTMLElement>) => {
    endResize(event.currentTarget, event.pointerId);
  }, [endResize]);

  const onPointerCancel = useCallback((event: React.PointerEvent<HTMLElement>) => {
    endResize(event.currentTarget, event.pointerId);
  }, [endResize]);

  return {
    height,
    isResizing,
    resizeHandleProps: {
      onPointerDown,
      onPointerMove,
      onPointerUp,
      onPointerCancel,
    },
  };
}
