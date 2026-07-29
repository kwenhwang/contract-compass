// 디자이너(Zippt) 토스트 — success/warning/danger/info.
// 디자이너 마크업: design_preview_v2/states.jsx
// 색상은 디자이너 공용 tone() 매핑(components/designer/index.tsx)을 그대로 사용.

import type { ReactNode } from 'react'
import Icon from '../Icon'
import { tone } from '../designer'

export type ToastKind = 'success' | 'warning' | 'danger' | 'info'
export type ToastPosition = 'top-right' | 'top-center' | 'bottom-right' | 'inline'

const KIND_MAP: Record<ToastKind, { icon: string; tone: string }> = {
  success: { icon: 'check-circle',   tone: 'success' },
  warning: { icon: 'alert-triangle', tone: 'warning' },
  danger:  { icon: 'alert-triangle', tone: 'danger'  },
  info:    { icon: 'info',           tone: 'info'    },
}

const POS_CLASS: Record<Exclude<ToastPosition, 'inline'>, string> = {
  'top-right':     'toast-fixed tp-tr',
  'top-center':    'toast-fixed tp-tc',
  'bottom-right':  'toast-fixed tp-br',
}

export type ToastProps = {
  kind?: ToastKind
  children: ReactNode
  /** 'inline' (기본) 은 부모 흐름에 배치, 그 외엔 fixed 포지셔닝. */
  position?: ToastPosition
}

export default function Toast({ kind = 'info', children, position = 'inline' }: ToastProps) {
  const t = KIND_MAP[kind] ?? KIND_MAP.info
  const posCls = position === 'inline' ? '' : POS_CLASS[position]
  return (
    <div className={`toast toast-${kind} ${posCls}`.trim()} role="status">
      <span className="toast-ic" style={{ color: tone(t.tone).solid }}>
        <Icon name={t.icon} size={17} />
      </span>
      <span className="toast-text">{children}</span>
    </div>
  )
}
