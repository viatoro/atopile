import { useState, useEffect, useMemo, useCallback } from "react";
import { useWaitFlag } from "../common/hooks/useWaitFlag";
import { Package, Download, Trash2 } from "lucide-react";
import {
  EmptyState,
  CenteredSpinner,
  Spinner,
  PanelSearchBox,
} from "../common/components";
import { WebviewRpcClient, rpcClient } from "../common/webviewRpcClient";
import type { PackageSummaryItem } from "../../protocol/generated-types";
import "./ComponentEntry.css";
import "./PackagesPanel.css";

function PackageRow({
  pkg,
  projectRoot,
}: {
  pkg: PackageSummaryItem;
  projectRoot: string;
}) {
  const [busy, raiseBusy] = useWaitFlag([pkg]);

  const handleInstall = useCallback(() => {
    raiseBusy();
    rpcClient?.sendAction("installPackage", {
      projectRoot,
      packageId: pkg.identifier,
    });
  }, [projectRoot, pkg.identifier, raiseBusy]);

  const handleRemove = useCallback(() => {
    raiseBusy();
    rpcClient?.sendAction("removePackage", {
      projectRoot,
      packageId: pkg.identifier,
    });
  }, [projectRoot, pkg.identifier, raiseBusy]);

  const openDetails = useCallback(() => {
    rpcClient?.sendAction("showPackageDetails", {
      projectRoot,
      packageId: pkg.identifier,
    });
  }, [projectRoot, pkg.identifier]);

  const isLocal = pkg.identifier.startsWith("local/");

  return (
    <div
      className="component-entry-row"
      role={isLocal ? undefined : "button"}
      tabIndex={isLocal ? undefined : 0}
      onClick={isLocal ? undefined : openDetails}
      onKeyDown={
        isLocal
          ? undefined
          : (event) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              openDetails();
            }
          }
      }
    >
      <div className="component-entry-mark">
        <Package size={16} />
      </div>
      <div className="component-entry-body">
        <div className="component-entry-line">
          <span className="component-entry-title">{pkg.name}</span>
          <span className="component-entry-detail">{pkg.publisher}</span>
        </div>
        <div className="component-entry-line">
          <span className="component-entry-description">
            {pkg.summary || pkg.identifier}
          </span>
        </div>
      </div>
      <div className="component-entry-actions">
        {busy ? (
          <span className="component-entry-icon-action">
            <Spinner size={14} />
          </span>
        ) : pkg.installed ? (
          <button
            type="button"
            className="component-entry-icon-action"
            onClick={(event) => {
              event.stopPropagation();
              handleRemove();
            }}
            aria-label="Remove package"
            title="Remove package"
          >
            <Trash2 size={14} />
          </button>
        ) : (
          <button
            type="button"
            className="component-entry-icon-action install"
            onClick={(event) => {
              event.stopPropagation();
              handleInstall();
            }}
            aria-label="Install package"
            title="Install package"
          >
            <Download size={14} />
          </button>
        )}
      </div>
    </div>
  );
}

export function PackagesPanel() {
  const { selectedProjectRoot: projectRoot } = WebviewRpcClient.useSubscribe("projectState");
  const packagesSummary = WebviewRpcClient.useSubscribe("packagesSummary");
  const [installedOnly, setInstalledOnly] = useState(false);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (projectRoot) {
      setLoading(true);
      rpcClient?.sendAction("getPackagesSummary", { projectRoot });
    }
  }, [projectRoot]);

  useEffect(() => {
    if (packagesSummary.packages.length > 0 || packagesSummary.total > 0) {
      setLoading(false);
    }
  }, [packagesSummary]);

  const filtered = useMemo(() => {
    let items = packagesSummary.packages;
    if (installedOnly) {
      items = items.filter((p) => p.installed);
    }
    if (!search) return items;
    const q = search.toLowerCase();
    return items.filter(
      (p) =>
        p.name.toLowerCase().includes(q) ||
        p.publisher.toLowerCase().includes(q) ||
        (p.summary?.toLowerCase().includes(q) ?? false),
    );
  }, [installedOnly, packagesSummary.packages, search]);

  if (!projectRoot) {
    return (
      <EmptyState
        icon={<Package size={24} />}
        title="No project selected"
        description="Select a project to browse packages"
      />
    );
  }

  return (
    <div className="sidebar-panel">
      <div className="panel-search-toolbar">
        <PanelSearchBox
          className="no-margin"
          value={search}
          onChange={setSearch}
          placeholder="Search packages..."
        />
        <button
          type="button"
          className={`panel-search-filter${installedOnly ? " active" : ""}`}
          onClick={() => setInstalledOnly((value) => !value)}
        >
          Installed only
        </button>
      </div>
      <div className="sidebar-panel-scroll">
        {loading ? (
          <CenteredSpinner />
        ) : filtered.length === 0 ? (
          <EmptyState
            title={search ? "No matches" : installedOnly ? "No installed packages" : "No packages"}
            description={search ? `No packages match "${search}"` : undefined}
          />
        ) : (
          <div className="component-entry-list">
            {filtered.map((pkg) => (
              <PackageRow
                key={pkg.identifier}
                pkg={pkg}
                projectRoot={projectRoot}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
