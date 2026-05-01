import { useCallback, useEffect, useMemo, useState } from "react";
import { WebviewRpcClient, rpcClient } from "../common/webviewRpcClient";
import { getVscodeApi } from "../common/vscodeApi";
import {
  Spinner,
  Alert,
  AlertTitle,
  AlertDescription,
  CopyableCodeBlock,
} from "../common/components";
import { useWaitFlag } from "../common/hooks/useWaitFlag";
import { ProjectTargetSelector } from "./ProjectTargetSelector";
import { samePath } from "../../protocol/paths";
import type { Build } from "../../protocol/generated-types";
import { SidebarHeader } from "./SidebarHeader";
import { TabBar, type TabId } from "./TabBar";
import {
  ComponentsPanel,
  COMPONENTS_SECTION_DEFAULTS,
  ToolsPanel,
  ProjectPanel,
  PROJECT_SECTION_DEFAULTS,
  InspectPanel,
  type ProjectSectionKey,
  type ComponentsSectionKey,
} from "../sidebar-panels";
import { PackageDetailPanel } from "../sidebar-details/PackageDetailPanel";
import { PartsDetailPanel } from "../sidebar-details/PartsDetailPanel";
import { MigrateDialog } from "../sidebar-details/MigrateDialog";
import { requestPanel } from "./sidebarActions";
import "./sidebar.css";

const POST_CONNECT_DISCONNECT_GRACE_MS = 5_000;

function DisconnectedOverlay({
  isConnected,
  extensionError,
  extensionTraceback,
  connected,
}: {
  isConnected: boolean;
  extensionError: string | null;
  extensionTraceback: string | null;
  connected: boolean;
}) {
  const [hasEverConnected, setHasEverConnected] = useState(false);
  const [show, setShow] = useState(false);

  useEffect(() => {
    if (isConnected) {
      setHasEverConnected(true);
      setShow(Boolean(extensionError));
      return;
    }
    if (extensionError) {
      setShow(true);
      return;
    }
    if (!hasEverConnected) {
      return;
    }
    const timeoutId = window.setTimeout(() => {
      setShow(true);
    }, POST_CONNECT_DISCONNECT_GRACE_MS);
    return () => window.clearTimeout(timeoutId);
  }, [isConnected, hasEverConnected, extensionError]);

  if (!show) return null;

  const overlayTitle = extensionError
    ? hasEverConnected
      ? "Extension Error"
      : "Failed to Start"
    : "Connection Lost";

  return (
    <div className="disconnected-overlay">
      <Alert variant={extensionError ? "destructive" : "warning"}>
        <AlertTitle>{overlayTitle}</AlertTitle>
        <AlertDescription>
          {extensionError ? (
            <code>{extensionError}</code>
          ) : (
            !connected
              ? "Unable to connect to the core server."
              : "Disconnected from the extension bridge."
          )}
        </AlertDescription>
        {extensionError && extensionTraceback ? (
          <details className="startup-traceback">
            <summary>Traceback</summary>
            <CopyableCodeBlock code={extensionTraceback} label="Traceback" />
          </details>
        ) : null}
        {extensionError && (
          <button
            className="open-settings-button"
            onClick={() => {
              getVscodeApi()?.postMessage({
                type: "extension:openSettings",
                query: "atopile.ato",
              });
            }}
          >
            Open atopile Settings
          </button>
        )}
        <AlertDescription>
          Run <code>Restart Extension Host</code> from the command palette.
          Check the <code>atopile</code> output channel for errors.
        </AlertDescription>
        <AlertDescription>
          Need help?{" "}
          <a href="https://discord.gg/CRe5xaDBr3" target="_blank" rel="noopener noreferrer">
            Join our Discord
          </a>
        </AlertDescription>
      </Alert>
    </div>
  );
}

export function SidebarApp() {
  const projectState = WebviewRpcClient.useSubscribe("projectState");
  const projects = WebviewRpcClient.useSubscribe("projects");
  const connected = WebviewRpcClient.useSubscribe("connected");
  const coreStatus = WebviewRpcClient.useSubscribe("coreStatus");
  const selectedBuildInProgress = WebviewRpcClient.useSubscribe("selectedBuildInProgress");
  const queueBuilds: Build[] = WebviewRpcClient.useSubscribe("queueBuilds") ?? [];
  const sidebarDetails = WebviewRpcClient.useSubscribe("sidebarDetails");
  const structureData = WebviewRpcClient.useSubscribe("structureData");
  const extensionErrorState = WebviewRpcClient.useSubscribe("extensionError");
  const authState = WebviewRpcClient.useSubscribe("authState");

  const [activeTab, setActiveTab] = useState<TabId>("project");
  const [detailsDismissed, setDetailsDismissed] = useState(false);
  const [isPackageInstalling, setIsPackageInstalling] = useState(false);
  const [kicadOpening, setKicadOpening] = useState(false);
  const [kicadError, setKicadError] = useState<string | null>(null);
  const [collapsedStackHeight, setCollapsedStackHeight] = useState(0);
  const [projectExpanded, setProjectExpanded] = useState(PROJECT_SECTION_DEFAULTS);
  const [projectTargetHeights, setProjectTargetHeights] = useState<Record<ProjectSectionKey, number>>(
    () => {
      const available = typeof window !== 'undefined' ? Math.floor(window.innerHeight * 0.4) : 320;
      return { files: Math.max(240, available), structure: 240 };
    },
  );
  const [componentsExpanded, setComponentsExpanded] = useState(COMPONENTS_SECTION_DEFAULTS);
  const [componentsTargetHeights, setComponentsTargetHeights] = useState<Record<ComponentsSectionKey, number>>(
    () => ({ packages: 240, parts: 240, library: 240 }),
  );
  const MIN_EXPAND_HEIGHT = 120;
  const toggleProjectSection = useCallback((key: ProjectSectionKey) => {
    setProjectExpanded((prev) => {
      const needsExpand = !prev[key];
      const visuallyCollapsed = prev[key] && projectTargetHeights[key] < MIN_EXPAND_HEIGHT;
      if (needsExpand || visuallyCollapsed) {
        setProjectTargetHeights((h) =>
          h[key] < MIN_EXPAND_HEIGHT ? { ...h, [key]: 240 } : h,
        );
        return { ...prev, [key]: true };
      }
      return { ...prev, [key]: false };
    });
  }, [projectTargetHeights]);
  const toggleComponentsSection = useCallback((key: ComponentsSectionKey) => {
    setComponentsExpanded((prev) => {
      const needsExpand = !prev[key];
      const visuallyCollapsed = prev[key] && componentsTargetHeights[key] < MIN_EXPAND_HEIGHT;
      if (needsExpand || visuallyCollapsed) {
        setComponentsTargetHeights((h) =>
          h[key] < MIN_EXPAND_HEIGHT ? { ...h, [key]: 240 } : h,
        );
        return { ...prev, [key]: true };
      }
      return { ...prev, [key]: false };
    });
  }, [componentsTargetHeights]);
  const isConnected = connected;
  const extensionError = extensionErrorState.error;
  const extensionTraceback = extensionErrorState.traceback;
  const [hasEverConnected, setHasEverConnected] = useState(false);

  useEffect(() => {
    if (isConnected) {
      setHasEverConnected(true);
    }
  }, [isConnected]);

  const selectedProject = useMemo(
    () =>
      projects.find((project) => samePath(project.root, projectState.selectedProjectRoot)) ??
      null,
    [projects, projectState.selectedProjectRoot],
  );

  const [buildRequested, raiseBuildRequested] = useWaitFlag([selectedBuildInProgress]);
  const isBuilding = selectedBuildInProgress || buildRequested;
  const buildDisabled =
    !projectState.selectedProjectRoot ||
    !projectState.selectedTarget ||
    Boolean(selectedProject?.needsMigration) ||
    Boolean(selectedProject?.error);
  const usesFixedSectionStack = activeTab === "project" || activeTab === "components";

  const panelMap: Record<TabId, React.ReactNode> = {
    project: (
      <ProjectPanel
        expanded={projectExpanded}
        onToggleSection={toggleProjectSection}
        targetHeights={projectTargetHeights}
        onTargetHeightsChange={setProjectTargetHeights}
        onCollapsedHeightChange={setCollapsedStackHeight}
      />
    ),
    components: (
      <ComponentsPanel
        expanded={componentsExpanded}
        onToggleSection={toggleComponentsSection}
        targetHeights={componentsTargetHeights}
        onTargetHeightsChange={setComponentsTargetHeights}
        onCollapsedHeightChange={setCollapsedStackHeight}
      />
    ),
    inspect: <InspectPanel disabled={buildDisabled} />,
    tools: (
      <ToolsPanel
        disabled={buildDisabled}
        kicadOpening={kicadOpening}
        kicadError={kicadError}
        onOpenKicad={async () => {
          if (kicadOpening) return;
          setKicadOpening(true);
          setKicadError(null);
          try {
            await rpcClient?.requestAction("openKicad", {
              projectRoot: projectState.selectedProjectRoot,
              target: projectState.selectedTarget,
            });
          } catch (e) {
            const msg = e instanceof Error ? e.message : String(e);
            setKicadError(msg);
            setTimeout(() => setKicadError(null), 5000);
          } finally {
            setKicadOpening(false);
          }
        }}
        onOpenManufacture={() => {
          void requestPanel("panel-manufacture");
        }}
        onOpenAutolayout={() => {
          void requestPanel("panel-autolayout");
        }}
      />
    ),
  };

  // Closing the details panel is dispatched through the websocket, which serializes
  // actions behind any slow in-flight fetch (JLC image, 3D model). Track a local
  // dismissal so the back button hides the panel immediately; the server close
  // still goes out and the dismissal clears once the view actually changes.
  const handleCloseDetails = useCallback(() => {
    setDetailsDismissed(true);
    rpcClient?.sendAction("closeSidebarDetails");
  }, []);

  const detailsViewKey =
    sidebarDetails.view === "part"
      ? `part:${sidebarDetails.part.lcsc ?? ""}`
      : sidebarDetails.view === "package"
      ? `package:${sidebarDetails.package.summary?.identifier ?? ""}`
      : sidebarDetails.view === "migration"
      ? `migration:${sidebarDetails.migration.projectRoot ?? ""}`
      : "none";

  useEffect(() => {
    setDetailsDismissed(false);
  }, [detailsViewKey]);

  const detailContent = useMemo(() => {
    if (detailsDismissed) {
      return null;
    }
    switch (sidebarDetails.view) {
      case "package":
        if (!sidebarDetails.package.summary) {
          return null;
        }
        return (
          <PackageDetailPanel
            package={{
              name: sidebarDetails.package.summary.name,
              fullName: sidebarDetails.package.summary.identifier,
              version: sidebarDetails.package.summary.version ?? undefined,
              description: sidebarDetails.package.summary.description ?? undefined,
              installed: sidebarDetails.package.summary.installed,
              availableVersions: sidebarDetails.package.details?.versions.map((version) => ({
                version: version.version,
                released: version.releasedAt ?? "",
              })),
              homepage: sidebarDetails.package.summary.homepage ?? undefined,
              repository: sidebarDetails.package.summary.repository ?? undefined,
            }}
            packageDetails={sidebarDetails.package.details}
            isLoading={sidebarDetails.package.loading}
            isInstalling={isPackageInstalling}
            installError={sidebarDetails.package.actionError}
            error={sidebarDetails.package.error}
            packageSyncProgress={sidebarDetails.package.syncProgress}
            onClose={handleCloseDetails}
            onInstall={(version) => {
              setIsPackageInstalling(true);
              rpcClient?.sendAction("installPackage", {
                projectRoot: sidebarDetails.package.projectRoot,
                packageId: sidebarDetails.package.summary?.identifier,
                version,
              });
            }}
            onUninstall={() => {
              setIsPackageInstalling(true);
              rpcClient?.sendAction("removePackage", {
                projectRoot: sidebarDetails.package.projectRoot,
                packageId: sidebarDetails.package.summary?.identifier,
              });
            }}
          />
        );
      case "part":
        if (!sidebarDetails.part.details) {
          return null;
        }
        return (
          <PartsDetailPanel
            partState={sidebarDetails.part}
            onClose={handleCloseDetails}
          />
        );
      case "migration":
        if (!sidebarDetails.migration.projectRoot) {
          return null;
        }
        return (
          <MigrateDialog
            migration={sidebarDetails.migration}
            actualVersion={coreStatus.version}
            onClose={handleCloseDetails}
          />
        );
      default:
        return null;
    }
  }, [coreStatus.version, detailsDismissed, handleCloseDetails, isPackageInstalling, sidebarDetails]);

  useEffect(() => {
    if (!sidebarDetails.package.loading) {
      setIsPackageInstalling(false);
    }
  }, [sidebarDetails.package.loading, sidebarDetails.package.details?.installedVersion, sidebarDetails.package.actionError]);

  useEffect(() => {
    if (!usesFixedSectionStack) {
      setCollapsedStackHeight(0);
    }
  }, [usesFixedSectionStack]);

  return (
    <div className="sidebar">
      <div>
        <SidebarHeader
          authState={authState}
          coreStatus={coreStatus}
          hasExtensionError={Boolean(extensionError)}
          connected={connected}
        />
      </div>

      {!isConnected && !hasEverConnected ? (
        <div className="sidebar-status">
          <Spinner size={14} />
          <span>Connecting...</span>
        </div>
      ) : (
        <>
          <div className="sidebar-top">
            <ProjectTargetSelector
              projects={projects}
              modules={structureData.modules}
              selectedProjectRoot={projectState.selectedProjectRoot}
              onSelectProject={(root) =>
                rpcClient?.sendAction("selectProject", { projectRoot: root })
              }
              selectedTarget={projectState.selectedTarget}
              onSelectTarget={(target) =>
                rpcClient?.sendAction("selectTarget", { target })
              }
              onBuild={() => {
                raiseBuildRequested();
                rpcClient?.sendAction("startBuild", {
                  projectRoot: projectState.selectedProjectRoot,
                  targets: projectState.selectedTarget ? [projectState.selectedTarget] : [],
                });
                rpcClient?.requestAction("vscode.showLogsView");
              }}
              buildDisabled={buildDisabled}
              isBuilding={isBuilding}
            />
            {selectedProject?.needsMigration && projectState.selectedProjectRoot && sidebarDetails.view !== "migration" ? (
              <button
                className="sidebar-migration-banner"
                onClick={() => {
                  rpcClient?.sendAction("showMigrationDetails", {
                    projectRoot: projectState.selectedProjectRoot,
                  });
                }}
              >
                <span className="sidebar-migration-banner-eyebrow">Migration Required</span>
                <span className="sidebar-migration-banner-copy">
                  Update this project before running builds or opening design views.
                </span>
              </button>
            ) : null}
            {kicadError ? (
              <Alert variant="destructive">
                <AlertTitle>KiCad Error</AlertTitle>
                <AlertDescription>{kicadError}</AlertDescription>
              </Alert>
            ) : null}
            {selectedProject?.error ? (
              <Alert variant="destructive">
                <AlertTitle>Project Error</AlertTitle>
                <AlertDescription>{selectedProject.error}</AlertDescription>
              </Alert>
            ) : null}
          </div>

          <div style={detailContent ? { display: "none" } : undefined}>
            <TabBar activeTab={activeTab} onTabChange={setActiveTab} />
          </div>
          <div
            className={`sidebar-tab-content${usesFixedSectionStack ? " sidebar-tab-content-fixed" : ""}`}
            style={{
              ...(usesFixedSectionStack ? { minHeight: collapsedStackHeight } : {}),
              ...(detailContent ? { display: "none" } : {}),
            }}
          >
            {panelMap[activeTab]}
          </div>
          {detailContent ? (
            <div className="sidebar-tab-content sidebar-tab-content-detail">
              {detailContent}
            </div>
          ) : null}

        </>
      )}

      <DisconnectedOverlay
        isConnected={isConnected}
        extensionError={extensionError}
        extensionTraceback={extensionTraceback}
        connected={connected}
      />
    </div>
  );
}
