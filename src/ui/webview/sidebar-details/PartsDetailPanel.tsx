import { useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle,
  Cuboid,
  Download,
  FileCode,
  Image,
  Info,
  List,
  Loader2,
  Package,
} from "lucide-react";
import type { UiPartDetailState } from "../../protocol/generated-types";
import { CenteredSpinner, CopyableCodeBlock, LayoutPreview, StepViewer } from "../common/components";
import type { RenderModel } from "../common/layout";
import { useBlobAssetUrl } from "../common/utils";
import { FileIcon } from "../common/utils/fileIcons";
import { formatCurrency, formatStock } from "../common/utils/format";
import { rpcClient } from "../common/webviewRpcClient";
import "./PackageDetailPanel.css";
import "./PartsDetailPanel.css";

function datasheetFilename(url: string): string {
  try {
    const name = new URL(url).pathname.split("/").pop();
    return name ? decodeURIComponent(name) : "Datasheet.pdf";
  } catch {
    return "Datasheet.pdf";
  }
}

interface PartsDetailPanelProps {
  partState: UiPartDetailState;
  onClose: () => void;
}

export function PartsDetailPanel({
  partState,
  onClose,
}: PartsDetailPanelProps) {
  const part = partState.details;
  const [activeVisualTab, setActiveVisualTab] = useState<"image" | "layout" | "3d">("image");
  const [imageLoadFailed, setImageLoadFailed] = useState(false);

  const attributes = useMemo(() => {
    if (!part?.attributes) return [];
    return Object.entries(part.attributes).slice(0, 12);
  }, [part?.attributes]);

  const isInstalling = part?.action === "installing";
  const isUninstalling = part?.action === "uninstalling";
  const isConverting = part?.action === "converting";
  const isBusy = part != null && part.action !== "idle";

  const layoutPreviewLoad = useMemo(() => {
    const client = rpcClient;
    if (!part?.lcsc || !client) {
      return null;
    }
    return () =>
      client.requestAction<RenderModel>("getPartFootprintRenderModel", {
        lcsc: part.lcsc,
      });
  }, [part?.lcsc]);

  const modelAsset = useBlobAssetUrl(
    activeVisualTab === "3d" && part?.lcsc ? "getPartModelData" : null,
    activeVisualTab === "3d" && part?.lcsc ? { lcsc: part.lcsc } : null,
  );
  const imageAsset = useBlobAssetUrl(
    activeVisualTab === "image" && part?.imageUrl ? "getRemoteAsset" : null,
    activeVisualTab === "image" && part?.imageUrl
      ? {
        url: part.imageUrl,
        filename: `${part.lcsc || part.mpn || "part-image"}.img`,
      }
      : null,
  );
  const resolvedImageUrl =
    imageAsset.url || (imageAsset.loading ? "" : part?.imageUrl || "");

  useEffect(() => {
    setImageLoadFailed(false);
  }, [resolvedImageUrl, part?.lcsc]);

  if (!part) {
    return null;
  }

  return (
    <div className="package-detail-panel parts-detail-panel">
      <div className="detail-panel-header">
        <button className="detail-back-btn" onClick={onClose} title="Back">
          <ArrowLeft size={18} />
        </button>
        <div className="detail-header-info">
          <div className="detail-title-row">
            <h2 className="detail-package-name">{part.mpn || part.identifier || part.lcsc}</h2>
            {part.installed ? (
              <span className="detail-installed">
                <CheckCircle size={14} />
                Installed
              </span>
            ) : null}
          </div>
          <p className="detail-package-blurb">
            {part.description || "No description available."}
          </p>
        </div>
      </div>

      <div className="detail-panel-content">
        <div className="parts-detail-grid">
          <div>
            <div className="detail-install-row">
              {part.installed ? (
                <div className="detail-install-split">
                  <button
                    className={`detail-install-btn uninstall ${isUninstalling ? "installing" : ""}`}
                    onClick={() => {
                      if (!partState.projectRoot || !part.lcsc) {
                        return;
                      }
                      rpcClient?.sendAction("uninstallPart", {
                        lcsc: part.lcsc,
                        projectRoot: partState.projectRoot,
                      });
                    }}
                    disabled={isBusy || !partState.projectRoot || !part.lcsc}
                  >
                    {isUninstalling ? (
                      <>
                        <Loader2 size={14} className="animate-spin" />
                        Uninstalling...
                      </>
                    ) : (
                      <>
                        <Download size={14} />
                        Uninstall
                      </>
                    )}
                  </button>
                  <button
                    className={`detail-install-btn install-package ${isConverting ? "installing" : ""}`}
                    onClick={() => {
                      if (!partState.projectRoot || !part.lcsc) {
                        return;
                      }
                      rpcClient?.sendAction("convertPartToPackage", {
                        lcsc: part.lcsc,
                        projectRoot: partState.projectRoot,
                      });
                    }}
                    disabled={isBusy || !partState.projectRoot || !part.lcsc}
                    title="Promote this part into a local package you can edit"
                  >
                    {isConverting ? (
                      <>
                        <Loader2 size={14} className="animate-spin" />
                        Converting...
                      </>
                    ) : (
                      <>
                        <Package size={14} />
                        Convert to package
                      </>
                    )}
                  </button>
                </div>
              ) : (
                <button
                  className={`detail-install-btn install ${isInstalling ? "installing" : ""}`}
                  onClick={() => {
                    if (!partState.projectRoot || !part.lcsc) {
                      return;
                    }
                    rpcClient?.sendAction("installPart", {
                      lcsc: part.lcsc,
                      projectRoot: partState.projectRoot,
                    });
                  }}
                  disabled={isBusy || !partState.projectRoot || !part.lcsc}
                >
                  {isInstalling ? (
                    <>
                      <Loader2 size={14} className="animate-spin" />
                      Installing...
                    </>
                  ) : (
                    <>
                      <Download size={14} />
                      Install
                    </>
                  )}
                </button>
              )}
            </div>
            {partState.actionError ? (
              <div className="detail-install-error">
                <AlertCircle size={12} />
                <span>{partState.actionError}</span>
              </div>
            ) : null}
            {partState.error ? (
              <div className="detail-install-error">
                <AlertCircle size={12} />
                <span>{partState.error}</span>
              </div>
            ) : null}
            {part.importStatement ? (
              <div className="detail-usage-code">
                <CopyableCodeBlock code={part.importStatement} label="Import" highlightAto />
              </div>
            ) : null}
          </div>

          <section className="detail-section">
            <h3 className="detail-section-title">
              <Info size={14} />
              Overview
            </h3>
            <dl className="detail-info-list">
              <div className="detail-info-row">
                <dt>Manufacturer</dt>
                <dd className="detail-info-value">{part.manufacturer || "-"}</dd>
              </div>
              <div className="detail-info-row">
                <dt>MPN</dt>
                <dd className="detail-info-value">{part.mpn || part.identifier || "-"}</dd>
              </div>
              <div className="detail-info-row">
                <dt>LCSC</dt>
                <dd className="detail-info-value">
                  <span className="detail-info-mono">{part.lcsc || "-"}</span>
                </dd>
              </div>
              <div className="detail-info-row">
                <dt>Package</dt>
                <dd className="detail-info-value">
                  <span className="detail-info-mono">{part.package || "-"}</span>
                </dd>
              </div>
              <div className="detail-info-row">
                <dt>Stock</dt>
                <dd className="detail-info-value">{formatStock(part.stock)}</dd>
              </div>
              <div className="detail-info-row">
                <dt>Unit price</dt>
                <dd className="detail-info-value">{formatCurrency(part.unitCost)}</dd>
              </div>
              <div className="detail-info-row">
                <dt>Type</dt>
                <dd className="detail-info-value">
                  <span className={`parts-type-badge ${part.isBasic ? "basic" : part.isPreferred ? "preferred" : "extended"}`}>
                    {part.isBasic ? "Basic" : part.isPreferred ? "Preferred" : "Extended"}
                  </span>
                </dd>
              </div>
              {part.datasheetUrl ? (
                <div className="detail-info-row">
                  <dt>Datasheet</dt>
                  <dd className="detail-info-value">
                    <a
                      className="detail-datasheet-link"
                      href={part.datasheetUrl}
                      onClick={(event) => {
                        event.preventDefault();
                        rpcClient?.sendAction("vscode.openExternal", {
                          url: part.datasheetUrl,
                        });
                      }}
                    >
                      <FileIcon name="datasheet.pdf" size={14} />
                      <span className="detail-datasheet-name">
                        {datasheetFilename(part.datasheetUrl)}
                      </span>
                    </a>
                  </dd>
                </div>
              ) : null}
            </dl>
          </section>

          <section className="detail-section">
            <h3 className="detail-section-title">
              <List size={14} />
              Attributes
            </h3>
            {attributes.length === 0 ? (
              <div className="detail-empty">None</div>
            ) : (
              <dl className="detail-info-list">
                {attributes.map(([key, value]) => (
                  <div key={key} className="detail-info-row">
                    <dt>{key}</dt>
                    <dd className="detail-info-value">
                      <span className="detail-info-mono">
                        {typeof value === "string" ? value : JSON.stringify(value)}
                      </span>
                    </dd>
                  </div>
                ))}
              </dl>
            )}
          </section>

          <div className="parts-visual-section">
            <div className="parts-visual-tabs">
              <button
                className={`parts-visual-tab ${activeVisualTab === "image" ? "active" : ""}`}
                onClick={() => setActiveVisualTab("image")}
              >
                <Image size={14} />
                Image
              </button>
              <button
                className={`parts-visual-tab ${activeVisualTab === "layout" ? "active" : ""}`}
                onClick={() => setActiveVisualTab("layout")}
              >
                <FileCode size={14} />
                Footprint
              </button>
              <button
                className={`parts-visual-tab ${activeVisualTab === "3d" ? "active" : ""}`}
                onClick={() => setActiveVisualTab("3d")}
              >
                <Cuboid size={14} />
                3D Model
              </button>
            </div>
            <div className="parts-visual-content">
              {activeVisualTab === "image" ? (
                resolvedImageUrl && !imageLoadFailed ? (
                  <img
                    src={resolvedImageUrl}
                    alt={part.mpn || part.identifier || "Part image"}
                    className="parts-visual-image"
                    onError={() => setImageLoadFailed(true)}
                  />
                ) : imageAsset.loading ? (
                  <CenteredSpinner />
                ) : (
                  <div className="parts-visual-empty">
                    {imageAsset.error || "No image available"}
                  </div>
                )
              ) : activeVisualTab === "layout" ? (
                <LayoutPreview
                  cacheKey={part.lcsc ?? null}
                  load={layoutPreviewLoad}
                  emptyMessage="No footprint available"
                  loadingMessage="Loading footprint..."
                />
              ) : modelAsset.url ? (
                <StepViewer src={modelAsset.url} />
              ) : modelAsset.loading ? (
                <div className="parts-visual-empty">Loading 3D model...</div>
              ) : (
                <div className="parts-visual-empty">
                  {modelAsset.error || "No 3D model available"}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
