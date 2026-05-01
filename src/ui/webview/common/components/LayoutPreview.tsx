import { useEffect, useRef, useState } from 'react'
import { createWebviewLogger } from '../logger'
import {
  mountLayoutViewer,
  StaticLayoutClient,
  type LayoutViewerHandle,
  type RenderModel,
} from '../layout'
import '../layout/layout-theme.css'
import './LayoutPreview.css'

interface LayoutPreviewProps {
  cacheKey: string | null
  load: (() => Promise<RenderModel>) | null
  className?: string
  style?: React.CSSProperties
  emptyMessage?: string
  loadingMessage?: string
}

const logger = createWebviewLogger('LayoutPreview')

function LayoutPreviewInstance({
  cacheKey,
  load,
  className,
  style,
  emptyMessage = 'No layout available',
  loadingMessage = 'Loading layout...',
}: LayoutPreviewProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const loadingRef = useRef<HTMLDivElement | null>(null)
  const viewerRef = useRef<LayoutViewerHandle | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    viewerRef.current?.dispose()
    viewerRef.current = null
    setError(null)

    const canvas = canvasRef.current
    if (!canvas || !load) {
      setLoading(false)
      return
    }

    let cancelled = false
    setLoading(true)

    void load().then((scene) => {
      if (cancelled) {
        return
      }

      viewerRef.current = mountLayoutViewer({
        canvas,
        client: new StaticLayoutClient(scene),
        readOnly: true,
        initialLoadingEl: loadingRef.current,
        logger,
      })
      setLoading(false)
    }).catch((err: unknown) => {
      if (cancelled) {
        return
      }

      const message = err instanceof Error ? err.message : String(err)
      logger.error(`Failed to load layout preview: ${message}`)
      setError(message || 'Failed to load layout')
      setLoading(false)
    })

    return () => {
      cancelled = true
      viewerRef.current?.dispose()
      viewerRef.current = null
    }
  }, [cacheKey, load])

  const classes = ['layout-preview', className].filter(Boolean).join(' ')

  if (!load) {
    return (
      <div className={`${classes} layout-preview-empty`} style={style}>
        {emptyMessage}
      </div>
    )
  }

  if (error) {
    return (
      <div className={`${classes} layout-preview-empty`} style={style}>
        {error}
      </div>
    )
  }

  return (
    <div className={classes} style={style}>
      <div className="layout-preview__shell">
        <canvas ref={canvasRef} className="layout-preview__canvas" />
        <div
          ref={loadingRef}
          className={`layout-preview__loading${loading ? '' : ' hidden'}`}
          aria-busy={loading ? 'true' : 'false'}
        >
          <div className="initial-loading-content">
            <div className="initial-loading-spinner" />
            <div className="initial-loading-message">{loadingMessage}</div>
            <div className="initial-loading-subtext">Preparing preview...</div>
          </div>
        </div>
      </div>
    </div>
  )
}

export default function LayoutPreview(props: LayoutPreviewProps) {
  return <LayoutPreviewInstance key={props.cacheKey ?? 'empty'} {...props} />
}
