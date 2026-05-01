import { useEffect, useMemo, useState } from 'react'
import { Loader2, Check, AlertCircle, ArrowLeft, ChevronDown, Package, FileCode, Play, Settings, FolderTree } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { rpcClient } from '../common/webviewRpcClient'
import type { UiMigrationState } from '../../protocol/generated-types'
import './MigrateDialog.css'

type StepStatus = 'idle' | 'running' | 'success' | 'error'

interface MigrationStep {
  id: string
  label: string
  description: string
  topic: string
  mandatory: boolean
  order: number
}

interface StepGroup {
  key: string
  label: string
  icon: LucideIcon
  steps: MigrationStep[]
}

// Maps Lucide icon names (from backend) to components
const ICON_MAP: Record<string, LucideIcon> = {
  Package, FileCode, Settings, FolderTree, AlertCircle,
}

interface TopicInfo {
  id: string
  label: string
  icon: string
}

function buildGroups(steps: MigrationStep[], topics: TopicInfo[]): StepGroup[] {
  const byTopic: Record<string, MigrationStep[]> = {}
  for (const step of steps) {
    if (!byTopic[step.topic]) byTopic[step.topic] = []
    byTopic[step.topic].push(step)
  }

  const groups: StepGroup[] = []
  for (const topic of topics) {
    if (byTopic[topic.id] && byTopic[topic.id].length > 0) {
      groups.push({
        key: topic.id,
        label: topic.label,
        icon: ICON_MAP[topic.icon] || Package,
        steps: byTopic[topic.id],
      })
      delete byTopic[topic.id]
    }
  }
  // Any topics not in the backend list (shouldn't happen, but safe)
  for (const [topicId, topicSteps] of Object.entries(byTopic)) {
    if (topicSteps.length > 0) {
      groups.push({
        key: topicId,
        label: topicId,
        icon: Package,
        steps: topicSteps,
      })
    }
  }
  return groups
}

function MigrateStepRow({
  step,
  selected,
  status,
  error,
  syncProgress,
  isMigrating,
  onToggle,
}: {
  step: MigrationStep
  selected: boolean
  status?: StepStatus
  error?: string
  syncProgress?: { message: string; completed: number; total: number } | null
  isMigrating: boolean
  onToggle: () => void
}) {
  const [expanded, setExpanded] = useState(false)
  const hasDetails = step.description || error

  return (
    <div className={`migrate-step-row ${expanded ? 'expanded' : ''}`}>
      <div className="migrate-step-row-header">
        <input
          type="checkbox"
          checked={selected}
          onChange={onToggle}
          disabled={step.mandatory || isMigrating}
          onClick={(e) => e.stopPropagation()}
        />
        <span className="migrate-step-label">{step.label}</span>
        {status === 'running' && <Loader2 size={12} className="spin migrate-step-status-icon" />}
        {status === 'success' && <Check size={12} className="migrate-step-status-icon success" />}
        {status === 'error' && <AlertCircle size={12} className="migrate-step-status-icon error" />}
        {hasDetails && (
          <button
            type="button"
            className="migrate-step-expand"
            onClick={() => setExpanded((v) => !v)}
          >
            <ChevronDown size={11} className={`migrate-step-chevron ${expanded ? 'open' : ''}`} />
          </button>
        )}
      </div>
      {status === 'running' && syncProgress && (
        <div className="migrate-step-progress">
          <div className="migrate-step-progress-text">{syncProgress.message}</div>
          <progress
            className="migrate-step-progress-bar"
            value={syncProgress.completed}
            max={syncProgress.total || 1}
          />
        </div>
      )}
      {expanded && hasDetails && (
        <div className="migrate-step-details">
          {step.description && <p className="migrate-step-desc">{step.description}</p>}
          {error && <p className="migrate-step-error">{error}</p>}
        </div>
      )}
    </div>
  )
}

interface MigrateDialogProps {
  migration: UiMigrationState
  actualVersion: string
  onClose: () => void
}

export function MigrateDialog({ migration, actualVersion, onClose }: MigrateDialogProps) {
  const [selectedSteps, setSelectedSteps] = useState<Set<string>>(new Set())
  const stepKey = useMemo(
    () => migration.steps.map((step) => step.id).join('|'),
    [migration.steps],
  )
  const groups = useMemo(
    () => buildGroups(migration.steps, migration.topics),
    [migration.steps, migration.topics],
  )
  const stepStatuses = useMemo<Record<string, StepStatus>>(
    () =>
      Object.fromEntries(
        migration.stepResults.map((result) => [result.stepId, result.status]),
      ) as Record<string, StepStatus>,
    [migration.stepResults],
  )
  const stepErrors = useMemo<Record<string, string>>(
    () =>
      Object.fromEntries(
        migration.stepResults
          .filter((result) => result.error)
          .map((result) => [result.stepId, result.error!]),
      ),
    [migration.stepResults],
  )
  const isMigrating = migration.running
  const allDone = migration.completed
  const hasErrors =
    migration.stepResults.some((result) => result.status === 'error') || Boolean(migration.error)

  useEffect(() => {
    setSelectedSteps(new Set(migration.steps.map((step) => step.id)))
  }, [migration.projectRoot, stepKey])

  const toggleStep = (stepId: string) => {
    if (isMigrating) return
    const step = migration.steps.find(s => s.id === stepId)
    if (step?.mandatory) return
    setSelectedSteps(prev => {
      const next = new Set(prev)
      if (next.has(stepId)) {
        next.delete(stepId)
      } else {
        next.add(stepId)
      }
      return next
    })
  }

  const handleMigrate = () => {
    const selected = migration.steps
      .filter(s => selectedSteps.has(s.id))
      .map(s => s.id)
    if (selected.length === 0) return

    rpcClient?.sendAction('migrateProjectSteps', {
      projectRoot: migration.projectRoot,
      steps: selected,
    })
  }

  // Keyboard: Escape to close when not migrating
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !isMigrating) {
        onClose()
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [isMigrating, onClose])

  if (migration.loading) {
    return (
      <div className="migrate-page">
        <div className="migrate-header">
          <button type="button" className="detail-back-btn" onClick={onClose} title="Back">
            <ArrowLeft size={18} />
          </button>
          <div className="migrate-header-info">
            <h1 className="migrate-title">Migrate Project</h1>
            <p className="migrate-subtitle">Loading migration steps…</p>
          </div>
        </div>
        <div className="migrate-loading">
          <Loader2 size={24} className="spin" />
        </div>
      </div>
    )
  }

  if (migration.error && migration.steps.length === 0) {
    return (
      <div className="migrate-page">
        <div className="migrate-header">
          <button type="button" className="detail-back-btn" onClick={onClose} title="Back">
            <ArrowLeft size={18} />
          </button>
          <div className="migrate-header-info">
            <h1 className="migrate-title">Migrate Project</h1>
            <p className="migrate-subtitle">Failed to load migration steps.</p>
          </div>
        </div>
        <div className="migrate-banner error">
          <AlertCircle size={16} />
          <span>{migration.error}</span>
        </div>
      </div>
    )
  }

  const canRun = !allDone && !isMigrating && selectedSteps.size > 0

  return (
    <div className="migrate-page">
      {/* Header */}
      <div className="migrate-header">
        <button
          type="button"
          className="detail-back-btn"
          onClick={onClose}
          disabled={isMigrating && !allDone}
          title="Back"
        >
          <ArrowLeft size={18} />
        </button>
        <div className="migrate-header-info">
          <h1 className="migrate-title">Migrate Project</h1>
          <p className="migrate-subtitle">Project out of date, requires updates.</p>
        </div>
      </div>

      {/* Run row */}
      {!allDone && (
        <div className="migrate-run-row">
          <button
            type="button"
            className={`migrate-run-btn ${isMigrating ? 'installing' : ''}`}
            onClick={handleMigrate}
            disabled={!canRun}
          >
            {isMigrating ? (
              <>
                <Loader2 size={14} className="spin" />
                Migrating…
              </>
            ) : (
              <>
                <Play size={14} />
                Run Migration
              </>
            )}
          </button>
        </div>
      )}

      {/* Git warning */}
      {!isMigrating && !allDone && (
        <div className="migrate-banner warning">
          <AlertCircle size={16} />
          <span>We recommend committing your changes to git before migrating.</span>
        </div>
      )}

      {/* Success / Error banner */}
      {allDone && !hasErrors && (
        <div className="migrate-banner success">
          <Check size={16} />
          <span>Migration complete! You can now close this tab and build your project.</span>
        </div>
      )}
      {allDone && hasErrors && (
        <div className="migrate-banner error">
          <AlertCircle size={16} />
          <span>{migration.error || 'Some steps failed. Check the errors above and try again or fix them manually.'}</span>
        </div>
      )}

      {/* Step Groups */}
      <div className="migrate-groups">
        {groups.map(group => {
          const GroupIcon = group.icon
          const groupSteps = group.steps
          if (groupSteps.length === 0) return null

          return (
            <div key={group.key} className="migrate-group">
              <div className="migrate-group-header">
                <GroupIcon size={14} className="migrate-group-icon" />
                <span className="migrate-group-label">{group.label}</span>
              </div>

              <div className="migrate-group-steps">
                {groupSteps.map(step => (
                  <MigrateStepRow
                    key={step.id}
                    step={step}
                    selected={selectedSteps.has(step.id)}
                    status={stepStatuses[step.id]}
                    error={stepErrors[step.id]}
                    syncProgress={migration.stepResults.find(r => r.stepId === step.id)?.syncProgress}
                    isMigrating={isMigrating}
                    onToggle={() => toggleStep(step.id)}
                  />
                ))}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
