const WINDOWS_PATH_RE = /^(?:[A-Za-z]:|\/\/[^/]+\/[^/]+)/;
const WINDOWS_DRIVE_ROOT_RE = /^[A-Za-z]:\/$/;

function slashPath(path: string): string {
  return path.replace(/\\/g, "/");
}

function collapseSlashes(path: string): string {
  return path.startsWith("//")
    ? `//${path.slice(2).replace(/\/+/g, "/")}`
    : path.replace(/\/+/g, "/");
}

function normalizePath(path: string): string {
  if (!path) {
    return "";
  }

  const normalized = collapseSlashes(slashPath(path));
  if (normalized === "/" || WINDOWS_DRIVE_ROOT_RE.test(normalized)) {
    return normalized;
  }
  return normalized.replace(/\/+$/, "") || "/";
}

export function pathKey(path: string): string {
  const normalized = normalizePath(path);
  return WINDOWS_PATH_RE.test(normalized) ? normalized.toLowerCase() : normalized;
}

export function samePath(
  left: string | null | undefined,
  right: string | null | undefined,
): boolean {
  if (!left || !right) {
    return left === right;
  }
  return pathKey(left) === pathKey(right);
}

function splitPath(path: string): string[] {
  return normalizePath(path).split("/").filter(Boolean);
}

export function formatPath(path: string): string {
  return splitPath(path).slice(-2).join("/");
}

export function joinPath(base: string, relativePath: string): string {
  const left = normalizePath(base);
  const right = normalizePath(relativePath).replace(/^\/+/, "");
  if (!left) {
    return right;
  }
  if (!right) {
    return left;
  }
  return left === "/" ? `/${right}` : `${left}/${right}`;
}

export function dirname(path: string): string {
  const normalized = normalizePath(path);
  if (!normalized || normalized === "/" || WINDOWS_DRIVE_ROOT_RE.test(normalized)) {
    return normalized;
  }

  const index = normalized.lastIndexOf("/");
  if (index <= 0) {
    return index === 0 ? "/" : "";
  }
  return index === 2 && /^[A-Za-z]:/.test(normalized)
    ? normalized.slice(0, 3)
    : normalized.slice(0, index);
}

export function relativeToProject(projectRoot: string, fullPath: string): string | null {
  const root = normalizePath(projectRoot);
  const absolute = normalizePath(fullPath);
  const rootKey = pathKey(root);
  const absoluteKey = pathKey(absolute);

  if (absoluteKey === rootKey) {
    return "";
  }
  return absoluteKey.startsWith(`${rootKey}/`) ? absolute.slice(root.length + 1) : null;
}

export function parentRelativePath(relativePath: string): string {
  const index = relativePath.lastIndexOf("/");
  return index === -1 ? "" : relativePath.slice(0, index);
}

export function basename(filePath: string): string {
  const normalized = normalizePath(filePath);
  const index = normalized.lastIndexOf("/");
  return index === -1 ? normalized : normalized.slice(index + 1);
}

export function validateName(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) {
    return "Name cannot be empty.";
  }
  if (trimmed === "." || trimmed === ".." || trimmed.includes("/") || trimmed.includes("\\")) {
    return "Name cannot be '.', '..', or contain path separators.";
  }
  return null;
}

export function ancestorPaths(relativePath: string): string[] {
  const segments = relativePath.split("/").filter(Boolean);
  return segments.slice(0, -1).map((_, index) => segments.slice(0, index + 1).join("/"));
}

export function relativeTargetRoot(projectRoot: string, targetRoot: string): string | null {
  const relative = relativeToProject(projectRoot, targetRoot);
  return relative === null ? formatPath(targetRoot) : relative || null;
}

/**
 * Parse a "file:line:col" source location string into its components.
 * Returns null if the string cannot be parsed.
 */
export function parseSrcLoc(srcLoc: string): { file: string; line: number; column: number } {
  // Format: "relative/path:line:col"
  const [file, lineStr, colStr] = srcLoc.split(":");
  return {
    file: file ?? srcLoc,
    line: parseInt(lineStr, 10) || 0,
    column: parseInt(colStr, 10) || 0,
  };
}

export function resolveUsageFilePath(projectRoot: string | null, path: string): string | null {
  if (!path) return null;
  const primary = path.split("|")[0] ?? path;
  const filePart = primary.split("::")[0] ?? "";
  if (!filePart.endsWith(".ato")) return null;
  if (filePart.startsWith("/") || /^[A-Za-z]:[\\/]/.test(filePart)) return filePart;
  if (!projectRoot) return filePart;
  return joinPath(projectRoot, filePart);
}
