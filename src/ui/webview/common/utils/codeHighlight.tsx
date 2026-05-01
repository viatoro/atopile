/**
 * Shared utilities for code highlighting and display.
 * Used across agent panel, AtoTraceback, CopyableCodeBlock, etc.
 */

import React from 'react'

const ATO_TOKEN_RE = /(\b(?:new|from|import|module|component|interface|pin|signal|assert|within|pass|trait)\b)|(#[^\n]*)|("[^"]*"|'[^']*')|(\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?(?:\s*[a-zA-Z%Ωμ]+)?\b)|(~)|([a-zA-Z_]\w*)/g;

/**
 * Ato syntax highlighter.
 * Tokenizes ato code and wraps tokens in spans with CSS classes:
 * ato-kw, ato-comment, ato-str, ato-num, ato-conn, ato-type, ato-ident
 */
export function highlightAtoCode(code: string): React.ReactNode {
  const segments: React.ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  ATO_TOKEN_RE.lastIndex = 0;
  while ((match = ATO_TOKEN_RE.exec(code)) !== null) {
    if (match.index > lastIndex) {
      segments.push(code.slice(lastIndex, match.index));
    }
    const [, keyword, comment, str, num, conn, ident] = match;
    if (keyword) {
      segments.push(<span key={match.index} className="ato-kw">{keyword}</span>);
    } else if (comment) {
      segments.push(<span key={match.index} className="ato-comment">{comment}</span>);
    } else if (str) {
      segments.push(<span key={match.index} className="ato-str">{str}</span>);
    } else if (num) {
      segments.push(<span key={match.index} className="ato-num">{num}</span>);
    } else if (conn) {
      segments.push(<span key={match.index} className="ato-conn">{conn}</span>);
    } else if (ident) {
      const isType = ident[0] >= 'A' && ident[0] <= 'Z';
      segments.push(
        <span key={match.index} className={isType ? 'ato-type' : 'ato-ident'}>{ident}</span>,
      );
    } else {
      segments.push(match[0]);
    }
    lastIndex = match.index + match[0].length;
  }
  if (lastIndex < code.length) {
    segments.push(code.slice(lastIndex));
  }
  return <>{segments}</>;
}

/**
 * Generate import statement for a package.
 */
export function generateImportStatement(packageId: string, moduleName: string): string {
  const className = moduleName.split('-').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join('')
  return `from "${packageId}/${moduleName}.ato" import ${className}`
}

/**
 * Generate usage example for a package.
 */
export function generateUsageExample(moduleName: string): string {
  const varName = moduleName.replace(/-/g, '_')
  const className = moduleName.split('-').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join('')
  return `module MyModule:\n    ${varName} = new ${className}`
}
