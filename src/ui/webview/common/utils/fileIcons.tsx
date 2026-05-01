/**
 * File type icon resolver — maps file extensions to lucide icons.
 * Shared between FilesPanel, agent changed files, and other file lists.
 */

import {
  File,
  FileText,
  FileCode,
  FileJson,
  CircuitBoard,
  FileArchive,
  FileCog,
  Hash,
  Code,
  FileSpreadsheet,
  Image,
  Terminal,
  GitBranch,
} from "lucide-react";
import { logoUrl } from "../render";

interface FileStyle {
  className: string;
  icon?: typeof File;
  imageSrc?: string;
}

export function fileStyle(name: string): FileStyle {
  const ext = name.includes(".") ? name.split(".").pop()?.toLowerCase() : "";
  switch (ext) {
    case "ato":
      return { imageSrc: logoUrl, className: "file-ato" };
    case "py":
    case "pyi":
      return { icon: FileCode, className: "file-py" };
    case "json":
      return { icon: FileJson, className: "file-json" };
    case "yaml":
    case "yml":
    case "toml":
      return { icon: FileCog, className: "file-config" };
    case "md":
    case "markdown":
    case "txt":
    case "rst":
      return { icon: FileText, className: "file-docs" };
    case "ts":
    case "tsx":
    case "js":
    case "jsx":
      return { icon: FileCode, className: "file-ts" };
    case "css":
    case "scss":
    case "less":
      return { icon: Hash, className: "file-css" };
    case "html":
    case "xml":
      return { icon: Code, className: "file-html" };
    case "pdf":
      return { icon: File, className: "file-pdf" };
    case "csv":
      return { icon: FileSpreadsheet, className: "file-csv" };
    case "kicad_pcb":
    case "kicad_sch":
      return { icon: CircuitBoard, className: "file-kicad" };
    case "kicad_pro":
      return { icon: File, className: "file-default" };
    case "png":
    case "jpg":
    case "jpeg":
    case "gif":
    case "svg":
    case "ico":
      return { icon: Image, className: "file-image" };
    case "zip":
    case "tar":
    case "gz":
    case "7z":
      return { icon: FileArchive, className: "file-archive" };
    case "sh":
    case "bash":
    case "zsh":
      return { icon: Terminal, className: "file-shell" };
    case "gitignore":
    case "gitattributes":
      return { icon: GitBranch, className: "file-git" };
    default:
      return { icon: File, className: "file-default" };
  }
}

export function FileIcon({ name, size = 14 }: { name: string; size?: number }) {
  const { icon: Icon, imageSrc, className } = fileStyle(name);
  return (
    <span className={`file-icon ${className}`}>
      {imageSrc ? (
        <img src={imageSrc} alt="" style={{ width: size, height: size }} />
      ) : Icon ? (
        <Icon size={size} />
      ) : null}
    </span>
  );
}
