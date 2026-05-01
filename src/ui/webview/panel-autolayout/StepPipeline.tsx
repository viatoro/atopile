import type { AutolayoutState } from "../../protocol/generated-types";

// Chevron progress indicator. Each step is active for exactly one
// lifecycle state; the "run" step also shows a progress percentage
// (air-wires connected / total) while active.
const PIPELINE_STEPS: { key: string; label: string; states: AutolayoutState[] }[] = [
  { key: "build",  label: "Build",  states: ["building"] },
  { key: "submit", label: "Submit", states: ["submitting"] },
  { key: "queue",  label: "Queue",  states: ["queued"] },
  { key: "run",    label: "Run",    states: ["running"] },
];

export function StepPipeline({
  state,
  progress,
}: {
  state: AutolayoutState;
  progress: number | null;
}) {
  const activeIdx = PIPELINE_STEPS.findIndex((s) => s.states.includes(state));

  return (
    <div className="al-pipeline">
      {PIPELINE_STEPS.map((step, i) => {
        const status =
          i < activeIdx ? "done" :
          i === activeIdx ? "active" :
          "pending";
        return (
          <div key={step.key} className={`al-pipeline-step ${status}`}>
            <span className="al-pipeline-step-label">{step.label}</span>
            {status === "active" && step.key === "run" && progress != null && (
              <span className="al-pipeline-step-pct">{Math.round(progress * 100)}%</span>
            )}
          </div>
        );
      })}
    </div>
  );
}
