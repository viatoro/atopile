import { CheckCircle2, Star } from "lucide-react";
import type { UiAutolayoutCandidateData } from "../../protocol/generated-types";
import { getCandidateLabel } from "./helpers";

export function CandidateCard({
  candidate,
  index,
  isRecommended,
  isApplied,
  isActive,
  onPreview,
  onApply,
  disabled,
}: {
  candidate: UiAutolayoutCandidateData;
  index: number;
  isRecommended: boolean;
  isApplied: boolean;
  isActive: boolean;
  onPreview: () => void;
  onApply: () => void;
  disabled: boolean;
}) {
  const routedPct = candidate.routedPct;
  const vias = candidate.viaCount;
  const label = getCandidateLabel(candidate, index);

  return (
    <div className={`al-candidate ${isActive ? "active" : ""} ${isApplied ? "applied" : ""}`}>
      <button className="al-candidate-main" onClick={onPreview} disabled={disabled}>
        <span className="al-candidate-name">
          {label}
        </span>
        <span className="al-candidate-stats">
          {routedPct != null && (
            <span className="al-candidate-stat al-stat-routed">
              <span className="al-stat-value">{routedPct}%</span>
              <span className="al-stat-unit"> routed</span>
            </span>
          )}
          {vias != null && (
            <span className="al-candidate-stat al-stat-vias">
              <span className="al-stat-value">{vias}</span>
              <span className="al-stat-unit"> vias</span>
            </span>
          )}
        </span>
        {isRecommended && (
          <span className="al-recommended-badge">
            <Star size={10} />
            <span className="al-recommended-label">Recommended</span>
          </span>
        )}
        {isApplied && (
          <span className="al-applied-badge">
            <CheckCircle2 size={10} />
            Applied
          </span>
        )}
      </button>
      {!isApplied && isActive && (
        <button
          className="al-candidate-apply"
          onClick={onApply}
          disabled={disabled}
        >
          Apply
        </button>
      )}
    </div>
  );
}
