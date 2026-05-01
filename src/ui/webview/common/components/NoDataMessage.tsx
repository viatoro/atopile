import type { CSSProperties, ReactNode } from 'react'
import { EmptyState } from './EmptyState'
import { CenteredSpinner } from './CenteredSpinner'

interface NoDataMessageProps {
  /** Lucide icon matching the panel's domain, rendered at 24px. */
  icon: ReactNode
  /** Panel name used to build messages, e.g. "stackup", "3D model". */
  noun: string
  /** Whether a project/target has been selected. */
  hasSelection: boolean
  /** Whether data is currently loading. */
  isLoading?: boolean
  /** Whether a build is currently in progress for the selected target. */
  buildInProgress?: boolean
  /** Error message to display, if any. */
  error?: string | null
  /** Whether the panel has data to display. */
  hasData: boolean
  /** Override the "no project" description. */
  noSelectionDescription?: string
  /** Override the "no data" description shown when no build is in progress. */
  noDataDescription?: string
  /** Content to render when data is available. */
  children: ReactNode
}

const fullSizeStyle: CSSProperties = {
  width: '100%',
  height: '100%',
  background: 'var(--bg-primary)',
}

/**
 * Shared wrapper that handles the four standard panel states:
 *   1. No project selected
 *   2. Loading
 *   3. Error
 *   4. No data (with build-in-progress variant)
 *
 * When none of those apply, renders `children`.
 */
export function NoDataMessage({
  icon,
  noun,
  hasSelection,
  isLoading = false,
  buildInProgress = false,
  error,
  hasData,
  noSelectionDescription,
  noDataDescription,
  children,
}: NoDataMessageProps) {
  if (!hasSelection) {
    return (
      <div style={fullSizeStyle}>
        <EmptyState
          icon={icon}
          title="No project selected"
          description={noSelectionDescription ?? `Select a project to view the ${noun}.`}
        />
      </div>
    )
  }

  if (isLoading) {
    return (
      <div style={fullSizeStyle}>
        <CenteredSpinner />
      </div>
    )
  }

  if (error) {
    return (
      <div style={fullSizeStyle}>
        <EmptyState
          icon={icon}
          title={`${capitalize(noun)} unavailable`}
          description={error}
        />
      </div>
    )
  }

  if (!hasData) {
    return (
      <div style={fullSizeStyle}>
        <EmptyState
          icon={icon}
          title={`No ${noun} data`}
          description={
            buildInProgress
              ? `Build in progress. ${capitalize(noun)} data will appear when the build completes.`
              : (noDataDescription ?? `Run a build to generate ${noun} data.`)
          }
        />
      </div>
    )
  }

  return <>{children}</>
}

function capitalize(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1)
}
