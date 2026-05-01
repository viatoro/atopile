/**
 * AtoTraceback — structured ato traceback renderer.
 *
 * Renders source frames from ato compilation errors with
 * syntax-highlighted code and file locations.
 */

import { useState } from "react";
import { ChevronDown } from "lucide-react";
import { highlightAtoCode } from "../common/utils/codeHighlight";
import { InlineFileRef } from "../common/components/InlineFileRef";
import "./AtoTraceback.css";

// ---- Types ----

interface AtoSourceFrame {
  file: string;
  line: number;
  column: number;
  code: string;
  start_line: number;
  highlight_lines: number[];
}

export interface StructuredAtoTraceback {
  title: string;
  message: string;
  frames: AtoSourceFrame[];
  origin: AtoSourceFrame | null;
}

export function parseAtoTraceback(
  raw: string | null | undefined,
): StructuredAtoTraceback | null {
  if (!raw) return null;
  try {
    return JSON.parse(raw) as StructuredAtoTraceback;
  } catch {
    return null;
  }
}

// ---- Helpers ----

function shortPath(fullPath: string): string {
  const parts = fullPath.replace(/\\/g, "/").split("/");
  const atoIdx = parts.findIndex((p) => p.endsWith(".ato") || p === "src");
  if (atoIdx >= 0) return parts.slice(atoIdx).join("/");
  return parts.length <= 3 ? parts.join("/") : ".../" + parts.slice(-3).join("/");
}

// ---- Components ----

function SourceFrame({
  frame,
  defaultOpen,
}: {
  frame: AtoSourceFrame;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen ?? true);
  const lines = frame.code.split("\n");
  const highlightSet = new Set(frame.highlight_lines);

  return (
    <div className={`ato-tb-frame${open ? " open" : ""}`}>
      <button className="ato-tb-frame-header" onClick={() => setOpen(!open)}>
        <ChevronDown size={12} className={`ato-tb-chevron${open ? " open" : ""}`} />
        <InlineFileRef path={frame.file} label={shortPath(frame.file)} line={frame.line} />
      </button>
      {open && (
        <div className="ato-tb-code ato-code">
          {lines.map((line, i) => {
            const lineNo = frame.start_line + i;
            const isHighlighted = highlightSet.has(lineNo);
            return (
              <div
                key={i}
                className={`ato-tb-line${isHighlighted ? " highlighted" : ""}`}
              >
                <span className="ato-tb-lineno">{lineNo}</span>
                <span className="ato-tb-line-code">
                  {highlightAtoCode(line)}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export function AtoTraceback({ traceback }: { traceback: StructuredAtoTraceback }) {
  // Skip origin if it duplicates the last frame
  const lastFrame = traceback.frames[traceback.frames.length - 1];
  const showOrigin = traceback.origin
    && !(lastFrame && lastFrame.file === traceback.origin.file && lastFrame.line === traceback.origin.line);

  return (
    <div className="ato-tb">
      {traceback.frames.map((frame, i) => (
        <SourceFrame
          key={`frame-${i}`}
          frame={frame}
          defaultOpen={i === traceback.frames.length - 1}
        />
      ))}
      {showOrigin && (
        <SourceFrame
          frame={traceback.origin!}
          defaultOpen
        />
      )}
    </div>
  );
}
