/**
 * InlineFileRef — shared clickable file reference with icon.
 * Used in agent panel, build logs, tracebacks, etc.
 */

import { FileIcon } from '../utils/fileIcons';
import { rpcClient } from '../webviewRpcClient';
import './InlineFileRef.css';

/** Shorten a long file path for display */
function compactPath(path: string): string {
  const segments = path.replace(/\\/g, '/').split('/').filter(Boolean);
  if (segments.length <= 2) return path;
  return `\u2026/${segments.slice(-2).join('/')}`;
}

export function InlineFileRef({
  path,
  projectRoot,
  label,
  line,
  size = 11,
}: {
  path: string;
  projectRoot?: string;
  label?: string;
  line?: number | null;
  size?: number;
}) {
  const displayText = label || (path.length > 40 ? compactPath(path) : path);
  const fullPath = projectRoot
    ? (projectRoot.endsWith('/') ? `${projectRoot}${path}` : `${projectRoot}/${path}`)
    : path;

  const handleClick = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    void rpcClient?.requestAction('vscode.openFile', {
      path: fullPath,
      ...(line != null ? { line } : {}),
    });
  };

  return (
    <button type="button" className="inline-file-ref" onClick={handleClick} title={path}>
      <FileIcon name={path} size={size} />
      <span className="inline-file-ref-name">{displayText}</span>
      {line != null && <span className="inline-file-ref-line">:{line}</span>}
    </button>
  );
}
