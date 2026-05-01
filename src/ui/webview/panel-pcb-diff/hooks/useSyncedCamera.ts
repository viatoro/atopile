import { useEffect, useRef } from "react";
import type { DiffViewerHandle } from "../components/DiffViewerWrapper";

/**
 * Bidirectionally syncs camera state between two DiffViewerHandle instances.
 * Calls `onCameraChange` whenever either camera moves.
 */
export function useSyncedCamera(
    handleA: DiffViewerHandle | null,
    handleB: DiffViewerHandle | null,
    onCameraChange?: () => void,
) {
    const syncingRef = useRef(false);
    const callbackRef = useRef(onCameraChange);
    callbackRef.current = onCameraChange;

    useEffect(() => {
        if (!handleA || !handleB) return;

        const syncAtoB = () => {
            if (syncingRef.current) return;
            syncingRef.current = true;
            handleB.camera.center = handleA.camera.center.copy();
            handleB.camera.zoom = handleA.camera.zoom;
            handleB.requestFrame();
            syncingRef.current = false;
            callbackRef.current?.();
        };

        const syncBtoA = () => {
            if (syncingRef.current) return;
            syncingRef.current = true;
            handleA.camera.center = handleB.camera.center.copy();
            handleA.camera.zoom = handleB.camera.zoom;
            handleA.requestFrame();
            syncingRef.current = false;
            callbackRef.current?.();
        };

        handleA.onCameraChange = syncAtoB;
        handleB.onCameraChange = syncBtoA;

        return () => {
            handleA.onCameraChange = undefined;
            handleB.onCameraChange = undefined;
        };
    }, [handleA, handleB]);
}
