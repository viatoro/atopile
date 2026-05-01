/**
 * CopyableCodeBlock - Reusable code display component with copy and open buttons.
 * Used in ProjectCard and DependencyCard for import/usage examples.
 */

import { useWaitFlag } from '../hooks/useWaitFlag'
import { Copy, Check, FileCode } from 'lucide-react'
import { highlightAtoCode } from '../utils/codeHighlight'
import './CopyableCodeBlock.css'

interface CopyableCodeBlockProps {
  /** The code to display */
  code: string
  /** Label shown in the header (e.g., "Import", "Usage") */
  label: string
  /** Optional callback when "open" button is clicked */
  onOpen?: () => void
  /** Whether to apply ato syntax highlighting */
  highlightAto?: boolean
}

export function CopyableCodeBlock({
  code,
  label,
  onOpen,
  highlightAto = false
}: CopyableCodeBlockProps) {
  const [copied, raiseCopied] = useWaitFlag([code], 2000)

  const handleCopy = async (e: React.MouseEvent) => {
    e.stopPropagation()
    try {
      await navigator.clipboard.writeText(code)
      raiseCopied()
    } catch (err) {
      console.error("Failed to copy to clipboard:", err);
    }
  }

  return (
    <div className="copyable-code">
      <div className="copyable-code-header">
        <span>{label}</span>
        <div className="actions">
          <button
            className={`copy-btn ${copied ? 'copied' : ''}`}
            onClick={handleCopy}
            title={copied ? 'Copied!' : 'Copy to clipboard'}
          >
            {copied ? <Check size={12} /> : <Copy size={12} />}
          </button>
          {onOpen && (
            <button
              className="open-btn"
              onClick={(e) => {
                e.stopPropagation()
                onOpen()
              }}
              title="Open in editor"
            >
              <FileCode size={12} />
            </button>
          )}
        </div>
      </div>
      <pre className="copyable-code-content">
        {highlightAto ? highlightAtoCode(code) : code}
      </pre>
    </div>
  )
}
