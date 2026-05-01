import { useEffect, useImperativeHandle, useRef, forwardRef } from "react";
import { createWebviewLogger } from "../../common/logger";
import {
  ensureLayoutViewerShell,
  mountLayoutViewer,
  setOverlayState,
  type Editor,
  type LayoutViewerHandle,
  type LayoutViewerShellElements,
  type LayoutTransport,
} from "../../common/layout";
import "../../common/layout/layout-shell.css";

const logger = createWebviewLogger("iBomViewer");

export interface IbomViewerRef {
  editor: Editor | null;
}

interface LayoutViewerWrapperProps {
  client: LayoutTransport | null;
  layoutPath: string | null;
  layoutError: string | null;
  /** Called when the viewer's editor is ready (or torn down). */
  onEditorReady?: (editor: Editor | null) => void;
}

export const LayoutViewerWrapper = forwardRef<IbomViewerRef, LayoutViewerWrapperProps>(
  function LayoutViewerWrapper({ client, layoutPath, layoutError, onEditorReady }, ref) {
    const hostRef = useRef<HTMLDivElement>(null);
    const viewerRef = useRef<LayoutViewerHandle | null>(null);
    const shellRef = useRef<LayoutViewerShellElements | null>(null);
    const onEditorReadyRef = useRef(onEditorReady);
    onEditorReadyRef.current = onEditorReady;

    useImperativeHandle(ref, () => ({
      get editor() {
        return viewerRef.current?.editor ?? null;
      },
    }));

    // Create shell on mount
    useEffect(() => {
      if (!hostRef.current) return;
      shellRef.current = ensureLayoutViewerShell(hostRef.current);
      return () => {
        hostRef.current?.replaceChildren();
        shellRef.current = null;
      };
    }, []);

    // Mount/update viewer
    useEffect(() => {
      const shell = shellRef.current;
      if (!shell) return;

      if (layoutError) {
        viewerRef.current?.dispose();
        viewerRef.current = null;
        onEditorReadyRef.current?.(null);
        setOverlayState(shell, "Layout unavailable", String(layoutError), { isError: true });
        return;
      }

      if (!layoutPath) {
        viewerRef.current?.dispose();
        viewerRef.current = null;
        onEditorReadyRef.current?.(null);
        setOverlayState(shell, "No layout data", "Run a build to generate layout data.");
        return;
      }

      if (!client) {
        setOverlayState(shell, "Layout unavailable", "RPC client is unavailable.", { isError: true });
        return;
      }

      if (!viewerRef.current) {
        try {
          viewerRef.current = mountLayoutViewer({
            canvas: shell.canvas,
            client,
            readOnly: true,
            initialLoadingEl: shell.initialLoadingEl,
            statusEl: shell.statusEl,
            coordsEl: shell.coordsEl,
            fpsEl: shell.fpsEl,
            helpEl: shell.helpEl,
            logger,
          });
          viewerRef.current.editor.setSuppressBuiltinSelection(true);
          onEditorReadyRef.current?.(viewerRef.current.editor);
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error);
          logger.error(`Failed to mount layout viewer: ${message}`);
          setOverlayState(shell, "Layout unavailable", message || "Failed to initialize.", { isError: true });
        }
      }
    }, [client, layoutPath, layoutError]);

    // Cleanup
    useEffect(() => {
      return () => {
        viewerRef.current?.dispose();
        viewerRef.current = null;
      };
    }, []);

    return <div className="ibom-viewer" ref={hostRef} />;
  },
);
