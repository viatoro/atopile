import { createElement, useEffect, useRef, useState } from 'react'
import { createWebviewLogger } from '../logger'
import './ModelViewer.css'

const MODEL_VIEWER_TAG = 'model-viewer'
const DEFAULT_MODEL_VIEWER_SCRIPT_URL = 'https://ajax.googleapis.com/ajax/libs/model-viewer/4.1.0/model-viewer.min.js'
const SCRIPT_ID = 'model-viewer-script'

interface GlbViewerProps {
  src: string
  className?: string
  style?: React.CSSProperties
  isOptimizing?: boolean
}

interface GlbViewerElement extends HTMLElement {
  src?: string
  'auto-rotate'?: string
  'camera-controls'?: string
  'tone-mapping'?: string
  'environment-image'?: string
  'exposure'?: string
  'shadow-intensity'?: string
  'shadow-softness'?: string
  'ar'?: string
  'ar-modes'?: string
}

const logger = createWebviewLogger("GlbViewer")

function getModelViewerScriptUrl(): string {
  return (window as { __ATOPILE_GLB_VIEWER_SCRIPT_URL__?: string })
    .__ATOPILE_GLB_VIEWER_SCRIPT_URL__
    || DEFAULT_MODEL_VIEWER_SCRIPT_URL
}

export default function GlbViewer({ src, className, style, isOptimizing }: GlbViewerProps) {
  const viewerRef = useRef<GlbViewerElement>(null)
  const [isReady, setIsReady] = useState(() => Boolean(window.customElements?.get(MODEL_VIEWER_TAG)))
  const [isLoading, setIsLoading] = useState(true)
  const [viewerError, setViewerError] = useState<string | null>(null)
  const [modelError, setModelError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    let pollInterval: ReturnType<typeof setInterval> | null = null

    const markReady = () => {
      if (pollInterval) {
        clearInterval(pollInterval)
      }
      if (!cancelled) {
        setIsReady(true)
        setViewerError(null)
      }
    }

    const checkReady = () => {
      if (!window.customElements?.get(MODEL_VIEWER_TAG)) {
        return false
      }
      markReady()
      return true
    }

    if (checkReady()) {
      return
    }

    const startPolling = () => {
      let attempts = 0
      pollInterval = setInterval(() => {
        if (cancelled || checkReady()) {
          return
        }
        attempts += 1
        if (attempts > 50) {
          clearInterval(pollInterval!)
          pollInterval = null
          logger.error('model-viewer custom element did not register within 5s')
          if (!cancelled) {
            setViewerError('3D viewer failed to initialize')
          }
        }
      }, 100)
    }

    const existing = document.getElementById(SCRIPT_ID) as HTMLScriptElement | null
    if (existing) {
      startPolling()
      return () => {
        cancelled = true
        if (pollInterval) {
          clearInterval(pollInterval)
        }
      }
    }

    const script = document.createElement('script')
    script.id = SCRIPT_ID
    script.src = getModelViewerScriptUrl()
    script.type = 'module'
    script.async = true
    script.addEventListener('load', startPolling, { once: true })
    script.addEventListener('error', () => {
      logger.error(`failed to load model-viewer script ${script.src}`)
      if (!cancelled) {
        setViewerError('Failed to load 3D viewer')
      }
    }, { once: true })
    document.head.appendChild(script)

    return () => {
      cancelled = true
      if (pollInterval) {
        clearInterval(pollInterval)
      }
    }
  }, [])

  useEffect(() => {
    setIsLoading(true)
    setModelError(null)
    logger.info(`loading GLB src=${src}`)
  }, [src])

  useEffect(() => {
    const viewer = viewerRef.current
    if (!viewer || !isReady) return

    const handleLoad = () => {
      logger.info(`model-viewer load event for src=${src}`)
      setIsLoading(false)
    }
    const handleError = () => {
      logger.error(`model-viewer failed for src=${src}`)
      setIsLoading(false)
      setModelError('Failed to load 3D model')
    }

    viewer.addEventListener('load', handleLoad)
    viewer.addEventListener('error', handleError)
    return () => {
      viewer.removeEventListener('load', handleLoad)
      viewer.removeEventListener('error', handleError)
    }
  }, [isReady, src])


  const error = viewerError ?? modelError

  return (
    <div
      className={['shared-3d-viewer', 'shared-3d-viewer--stack', className].filter(Boolean).join(' ')}
      style={style}
    >
      {error ? (
        <div className="shared-3d-viewer__empty">{error}</div>
      ) : !isReady ? (
        <div className="shared-3d-viewer__overlay">
          <span className="shared-3d-viewer__spinner" />
          <span>Loading 3D...</span>
        </div>
      ) : (
        <>
          {createElement('model-viewer', {
            key: src,
            ref: viewerRef,
            src,
            'auto-rotate': 'true',
            'camera-controls': 'true',
            'interaction-prompt': 'none',
            'tone-mapping': 'neutral',
            exposure: '1.2',
            'shadow-intensity': '0.7',
            'shadow-softness': '0.8',
            ar: 'true',
            'ar-modes': 'webxr scene-viewer quick-look',
            style: { width: '100%', height: '100%' },
          })}
          {isLoading && (
            <div className="shared-3d-viewer__overlay">
              <span className="shared-3d-viewer__spinner" />
              <span>Loading 3D...</span>
            </div>
          )}
          {isOptimizing && !isLoading && (
            <div className="shared-3d-viewer__badge">
              <span className="shared-3d-viewer__spinner" />
              <span>Optimizing...</span>
            </div>
          )}
        </>
      )}
    </div>
  )
}
