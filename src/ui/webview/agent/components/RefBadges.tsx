/**
 * RefBadges — inline reference badge components for the agent chat.
 *
 * Renders [[part:...]], [[module:...]], [[package:...]], [[build:...]], [[panel:...]] refs
 * as interactive inline elements with icons, live data, and click actions.
 */

import { useEffect, useMemo, useState } from 'react';
import {
  AlertCircle,
  CheckCircle2,
  Cpu,
  Eye,
  ExternalLink,
  Factory,
  Files,
  Layers,
  Package,
  Sparkles,
  Wrench,
  XCircle,
} from 'lucide-react';
import type { Build } from '../../../protocol/generated-types';
import { typeIcon } from '../../common/components/TypeIcon';
import { WebviewRpcClient, rpcClient } from '../../common/webviewRpcClient';
import { Spinner } from '../../common/components';

/* ── Build ref ────────────────────────────────────────── */

export function BuildRef({ buildId, label }: { buildId: string; label?: string }) {
  const currentBuilds: Build[] = WebviewRpcClient.useSubscribe('currentBuilds') ?? [];
  const queueBuilds: Build[] = WebviewRpcClient.useSubscribe('queueBuilds') ?? [];

  const build = useMemo(() => {
    const all = [...currentBuilds, ...queueBuilds];
    return all.find((b) => b.buildId === buildId) ?? null;
  }, [currentBuilds, queueBuilds, buildId]);

  const openBuild = () => {
    rpcClient?.sendAction('setLogViewCurrentId', { buildId, stage: null });
    void rpcClient?.requestAction('vscode.showLogsView');
  };

  if (!build) {
    return (
      <button type="button" className="agent-build-ref" onClick={openBuild} title={`Build: ${buildId}`}>
        {label || buildId}
      </button>
    );
  }

  const statusIcon = build.status === 'building' ? (
    <Spinner size={10} />
  ) : build.status === 'success' ? (
    <CheckCircle2 size={10} className="meta-status-ok" />
  ) : build.status === 'failed' ? (
    <XCircle size={10} className="meta-status-error" />
  ) : build.status === 'warning' ? (
    <AlertCircle size={10} className="meta-status-warning" />
  ) : null;

  return (
    <button type="button" className={`agent-build-ref ${build.status}`} onClick={openBuild} title={`Build: ${buildId}`}>
      {statusIcon}
      <span className="agent-build-ref-target">{label || build.name}</span>
    </button>
  );
}

/* ── Module ref ───────────────────────────────────────── */

export function ModuleRef({ name, label, projectRoot }: { name: string; label?: string; projectRoot: string }) {
  const structureData = WebviewRpcClient.useSubscribe('structureData');
  const mod = useMemo(
    () => structureData.modules.find((m) => m.entry === name || m.name === name) ?? null,
    [structureData.modules, name],
  );

  const handleClick = () => {
    if (mod?.file && mod.line != null) {
      const fullPath = projectRoot.endsWith('/')
        ? `${projectRoot}${mod.file}`
        : `${projectRoot}/${mod.file}`;
      void rpcClient?.requestAction('vscode.openFile', { path: fullPath, line: mod.line });
    }
  };

  const iconType = mod?.type || 'module';
  const isClickable = Boolean(mod?.file && mod?.line != null);

  return (
    <button
      type="button"
      className="agent-module-ref"
      title={mod ? `${mod.type}: ${mod.entry} — ${mod.file}:${mod.line}` : `Module: ${name}`}
      onClick={isClickable ? handleClick : undefined}
      style={isClickable ? undefined : { cursor: 'default' }}
    >
      <span className={`type-icon type-${iconType}`}>{typeIcon(iconType, 11)}</span>
      <span className="agent-ref-name">{label || name}</span>
    </button>
  );
}

/* ── Part ref ─────────────────────────────────────────── */

interface PartInfo {
  mpn: string;
  manufacturer: string;
  stock: number | null;
  unitCost: number | null;
  description: string;
  package: string | null;
}

const partCache = new Map<string, PartInfo | null>();

export function PartRef({ lcsc, label, projectRoot }: { lcsc: string; label?: string; projectRoot: string }) {
  const [info, setInfo] = useState<PartInfo | null>(partCache.get(lcsc) ?? null);
  const [loaded, setLoaded] = useState(partCache.has(lcsc));

  useEffect(() => {
    if (loaded || !rpcClient) return;
    let cancelled = false;
    rpcClient.requestAction<PartInfo>('lookupPart', { lcsc }).then(
      (result) => {
        if (cancelled) return;
        partCache.set(lcsc, result);
        setInfo(result);
        setLoaded(true);
      },
      () => {
        if (cancelled) return;
        partCache.set(lcsc, null);
        setLoaded(true);
      },
    );
    return () => { cancelled = true; };
  }, [lcsc, loaded]);

  const openDetails = () => {
    rpcClient?.sendAction('showPartDetails', {
      projectRoot,
      identifier: lcsc,
      lcsc,
      installed: false,
    });
  };

  const displayName = label || info?.mpn || lcsc;
  const stockText = info?.stock != null
    ? info.stock >= 1000 ? `${Math.floor(info.stock / 1000)}k` : String(info.stock)
    : null;
  const priceText = info?.unitCost != null ? `$${info.unitCost.toFixed(2)}` : null;

  return (
    <button type="button" className="agent-part-ref" title={info?.description || `Part: ${displayName}`} onClick={openDetails}>
      <Cpu size={11} />
      <span className="agent-part-ref-name">{displayName}</span>
      {loaded && (priceText || stockText) && (
        <span className="agent-part-ref-meta">
          {priceText && <span>{priceText}</span>}
          {stockText && <span className="agent-part-ref-stock">{stockText}</span>}
        </span>
      )}
    </button>
  );
}

/* ── Package ref ──────────────────────────────────────── */

export function PackageRef({ packageId, label, projectRoot }: { packageId: string; label?: string; projectRoot: string }) {
  const pkgDisplay = label || (packageId.includes('/') ? packageId.split('/').pop()! : packageId);
  return (
    <button
      type="button"
      className="agent-package-ref"
      title={packageId}
      onClick={() => {
        rpcClient?.sendAction('showPackageDetails', {
          projectRoot,
          packageId,
        });
      }}
    >
      <Package size={11} />
      <span className="agent-ref-name">{pkgDisplay}</span>
    </button>
  );
}

/* ── Panel ref ───────────────────────────────────────── */

/** Known panels/views and their display metadata. */
const PANEL_META: Record<string, { label: string; icon: React.ReactNode }> = {
  'layout':       { label: 'Layout',          icon: <Layers size={11} /> },
  'autolayout':   { label: 'Autolayout',      icon: <Sparkles size={11} /> },
  'manufacture':  { label: 'Manufacture',     icon: <Factory size={11} /> },
  '3d':           { label: '3D Model',        icon: <Eye size={11} /> },
  'pinout':       { label: 'Pinout',          icon: <Eye size={11} /> },
  'parameters':   { label: 'Parameters',      icon: <Eye size={11} /> },
  'stackup':      { label: 'Stackup',         icon: <Layers size={11} /> },
  'ibom':         { label: 'Interactive BOM',  icon: <Eye size={11} /> },
  'pcb-diff':     { label: 'PCB Diff',        icon: <Eye size={11} /> },
  'tree':         { label: 'Trees',           icon: <Eye size={11} /> },
  // Sidebar tabs (not standalone panels)
  'project':      { label: 'Project',         icon: <Files size={11} /> },
  'components':   { label: 'Components',      icon: <Package size={11} /> },
  'inspect':      { label: 'Inspect',         icon: <Eye size={11} /> },
  'tools':        { label: 'Tools',           icon: <Wrench size={11} /> },
};

const SIDEBAR_TABS = new Set(['project', 'components', 'inspect', 'tools']);

export function PanelRef({ panelKey, label }: { panelKey: string; label?: string }) {
  const meta = PANEL_META[panelKey];
  const displayLabel = label || meta?.label || panelKey;
  const icon = meta?.icon || <ExternalLink size={11} />;

  const handleClick = () => {
    if (SIDEBAR_TABS.has(panelKey)) {
      // Sidebar tabs are within the sidebar webview — reveal it so the user can navigate.
      void rpcClient?.requestAction('vscode.revealSidebar');
    } else {
      void rpcClient?.requestAction('vscode.openPanel', { panelId: `panel-${panelKey}` });
    }
  };

  return (
    <button
      type="button"
      className="agent-panel-ref"
      title={`Open ${displayLabel}`}
      onClick={handleClick}
    >
      {icon}
      <span className="agent-ref-name">{displayLabel}</span>
    </button>
  );
}
