import type { FileNode, ModuleDefinition } from '../../../protocol/generated-types';
import type { AgentTraceView } from '../state/types';

export interface MentionToken {
  start: number;
  end: number;
  query: string;
}

export interface MentionItem {
  kind: 'file' | 'module';
  label: string;
  token: string;
  subtitle?: string;
  keywords?: string[];
}

export function findMentionToken(input: string, caret: number): MentionToken | null {
  if (caret < 1 || caret > input.length) return null;

  let index = caret - 1;
  while (index >= 0) {
    const char = input[index];
    if (char === '@') {
      if (index > 0 && !/\s/.test(input[index - 1])) {
        return null;
      }
      const query = input.slice(index + 1, caret);
      if (/\s/.test(query)) return null;
      return { start: index, end: caret, query };
    }
    if (/\s/.test(char)) break;
    index -= 1;
  }
  return null;
}

export function flattenFileNodes(nodes: FileNode[] | undefined): string[] {
  if (!nodes || nodes.length === 0) return [];
  const files: string[] = [];
  const visit = (entries: FileNode[], prefix: string) => {
    for (const node of entries) {
      const path = prefix ? `${prefix}/${node.name}` : node.name;
      if (node.children && node.children.length > 0) {
        visit(node.children, path);
      } else {
        files.push(path);
      }
    }
  };
  visit(nodes, "");
  files.sort((left, right) => left.localeCompare(right));
  return files;
}

function scoreMention(label: string, query: string): number {
  if (!query) return 0;
  const lower = label.toLowerCase();
  const needle = query.toLowerCase();
  if (lower.startsWith(needle)) return 0;
  if (lower.includes(`/${needle}`)) return 1;
  if (lower.includes(needle)) return 2;
  return 9;
}

function normalizeMentionPath(path: string): string {
  return path.replace(/\\/g, '/').replace(/^\.?\//, '').toLowerCase();
}

function isDeprioritizedMentionPath(path: string): boolean {
  const normalized = normalizeMentionPath(path);
  const blockedRoots = ['parts/', '.ato/', 'node_modules/', '.git/', '.venv/'];
  for (const root of blockedRoots) {
    if (normalized.startsWith(root)) return true;
    if (normalized.includes(`/${root}`)) return true;
  }
  return false;
}

function fileMentionTypeRank(path: string): number {
  const normalized = normalizeMentionPath(path);
  if (normalized.endsWith('.ato')) return 0;
  if (
    normalized.endsWith('.py')
    || normalized.endsWith('.ts')
    || normalized.endsWith('.tsx')
    || normalized.endsWith('.js')
    || normalized.endsWith('.json')
    || normalized.endsWith('.yaml')
    || normalized.endsWith('.yml')
    || normalized.endsWith('.toml')
  ) {
    return 1;
  }
  return 2;
}

function mentionPathDepth(path: string): number {
  return normalizeMentionPath(path).split('/').filter(Boolean).length;
}

export function buildMentionItems(
  mentionToken: MentionToken | null,
  projectModules: ModuleDefinition[],
  projectFiles: string[],
): MentionItem[] {
  if (!mentionToken) return [];
  const query = mentionToken.query.trim().toLowerCase();

  const moduleItems = projectModules
    .map((moduleEntry): MentionItem => ({
      kind: 'module',
      label: moduleEntry.entry,
      token: moduleEntry.entry,
      subtitle: moduleEntry.type,
      keywords: [moduleEntry.entry, moduleEntry.name].filter(Boolean),
    }))
    .filter((item) => {
      if (!query) return true;
      return (item.keywords ?? [item.label]).some((keyword) =>
        keyword.toLowerCase().includes(query),
      );
    });

  const fileItems = projectFiles
    .map((path): MentionItem => ({
      kind: 'file',
      label: path,
      token: path,
    }))
    .filter((item) => {
      const normalized = normalizeMentionPath(item.label);
      const deprioritized = isDeprioritizedMentionPath(item.label);
      if (!query) return !deprioritized;
      if (!normalized.includes(query)) return false;
      return !deprioritized || normalized.includes(query);
    });

  const combined: MentionItem[] = [...moduleItems, ...fileItems];
  const deduped = new Map<string, MentionItem>();
  for (const item of combined) {
    const key = `${item.kind}:${item.token}`;
    if (!deduped.has(key)) deduped.set(key, item);
  }

  return [...deduped.values()]
    .sort((left, right) => {
      const kindDiff = (left.kind === 'module' ? 0 : 1) - (right.kind === 'module' ? 0 : 1);
      if (kindDiff !== 0) return kindDiff;

      if (left.kind === 'file' && right.kind === 'file') {
        const deprioritizedDiff = Number(isDeprioritizedMentionPath(left.label)) - Number(isDeprioritizedMentionPath(right.label));
        if (deprioritizedDiff !== 0) return deprioritizedDiff;

        const fileTypeDiff = fileMentionTypeRank(left.label) - fileMentionTypeRank(right.label);
        if (fileTypeDiff !== 0) return fileTypeDiff;
      }

      const scoreDiff = scoreMention(left.label, query) - scoreMention(right.label, query);
      if (scoreDiff !== 0) return scoreDiff;

      const depthDiff = mentionPathDepth(left.label) - mentionPathDepth(right.label);
      if (depthDiff !== 0) return depthDiff;

      return left.label.localeCompare(right.label);
    })
    .slice(0, 12);
}
