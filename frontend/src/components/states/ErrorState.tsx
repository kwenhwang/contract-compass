// 디자이너(Zippt) 에러 상태 컴포넌트 — 코드·메시지·재시도 버튼.
// 일러스트는 인라인 SVG. 출처: design_preview_v2/states.jsx

import type { ReactNode } from 'react'
import Icon from '../Icon'

function IllustError() {
  return (
    <svg viewBox="0 0 120 100" className="illust" width="140" height="116">
      <ellipse cx="60" cy="90" rx="40" ry="6" fill="var(--bg-tertiary)" />
      <path d="M60 20l34 56H26z" fill="var(--danger-soft)" stroke="var(--danger)" strokeWidth="2.5" strokeLinejoin="round" />
      <line x1="60" y1="42" x2="60" y2="58" stroke="var(--danger)" strokeWidth="4" strokeLinecap="round" />
      <circle cx="60" cy="67" r="2.6" fill="var(--danger)" />
    </svg>
  )
}

export type ErrorStateProps = {
  code?: string
  title?: ReactNode
  sub?: ReactNode
  onRetry?: () => void
  retryLabel?: string
}

export default function ErrorState({
  code = '오류',
  title = '문제가 발생했습니다',
  sub = '잠시 후 다시 시도해 주세요.',
  onRetry,
  retryLabel = '다시 시도',
}: ErrorStateProps) {
  return (
    <div className="empty-state">
      <IllustError />
      <span className="err-code">{code}</span>
      <p className="empty-title">{title}</p>
      <p className="empty-sub">{sub}</p>
      {onRetry && (
        <button className="empty-action" onClick={onRetry}>
          <Icon name="repeat" size={14} /> {retryLabel}
        </button>
      )}
    </div>
  )
}
