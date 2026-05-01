import { CheckCircle2, XCircle } from "lucide-react";
import type { UiAutolayoutPreCheckItem } from "../../protocol/generated-types";

export function PreCheckList({ checks }: { checks: UiAutolayoutPreCheckItem[] }) {
  return (
    <div className="al-prechecks">
      {checks.map((check) => (
        <div key={check.label} className={`al-precheck ${check.passed ? "passed" : "failed"}`}>
          {check.passed
            ? <CheckCircle2 size={14} className="al-precheck-icon passed" />
            : <XCircle size={14} className="al-precheck-icon failed" />
          }
          <span className="al-precheck-label">{check.label}</span>
          {check.detail && <span className="al-precheck-detail">{check.detail}</span>}
        </div>
      ))}
    </div>
  );
}
