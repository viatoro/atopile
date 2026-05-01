import { Hammer } from "lucide-react";
import { EmptyState } from "../common/components";
import { BuildQueueItem } from "../sidebar/BuildQueueItem";
import type { Build, BuildStage } from "../../protocol/generated-types";
import "./BuildQueuePanel.css";

interface BuildQueuePanelProps {
  builds: Array<Build & { currentStage: BuildStage | null }>;
  expandLatest?: boolean;
}

export function BuildQueuePanel({ builds, expandLatest }: BuildQueuePanelProps) {
  return (
    <div className="sidebar-panel">
      <div className="build-queue-tab-panel">
        {builds.length === 0 ? (
          <EmptyState
            icon={<Hammer size={24} />}
            title="Run a build"
            description="Use the Build button above to start a build and see recent results here"
          />
        ) : (
          builds.map((build, i) => (
            <BuildQueueItem
              key={build.buildId ?? build.name}
              build={build}
              defaultExpanded={expandLatest ? i === 0 : undefined}
            />
          ))
        )}
      </div>
    </div>
  );
}
