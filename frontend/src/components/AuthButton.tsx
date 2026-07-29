// 헤더 로그인 위젯 (2026-07-29) — Google 간편로그인 상태 표시 + 로그인/로그아웃.
// App 헤더(dt-top-right)와 홈 대시보드(tb-right) 양쪽에 마운트.
import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { getUserEmail, login, logout } from '../lib/auth'

// 로그인 복귀 직후 1회 토스트 — 첫 렌더 시점 지연 소비 후 결과 공유(위젯이 2곳 마운트라
// 컴포넌트별로 읽으면 한쪽만 소비. 모듈 로드 시 즉시 읽으면 captureAuthFromHash가 플래그를
// 심기 전이라 항상 false. fixed 위치라 두 인스턴스 토스트가 겹쳐도 무해)
let _justIn: boolean | undefined
function consumeLoginToast(): boolean {
  if (_justIn === undefined) {
    _justIn = false
    try {
      if (sessionStorage.getItem('cc_login_toast')) {
        sessionStorage.removeItem('cc_login_toast')
        _justIn = true
      }
    } catch { /* ignore */ }
  }
  return _justIn
}

function GoogleGlyph() {
  return (
    <svg width="14" height="14" viewBox="0 0 48 48" aria-hidden="true">
      <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" />
      <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z" />
      <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z" />
      <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" />
    </svg>
  )
}

export default function AuthButton() {
  const [email, setEmail] = useState<string | null>(getUserEmail())
  const [toast, setToast] = useState(consumeLoginToast)

  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(false), 4000)
    return () => clearTimeout(t)
  }, [toast])

  if (!email) {
    return (
      <button className="dt-help auth-btn" onClick={() => login()} title="Google 계정으로 간편 로그인">
        <GoogleGlyph /> <span>로그인</span>
      </button>
    )
  }
  return (
    <span className="auth-user">
      {/* 헤더(.dt-top z-index:50)가 스태킹 컨텍스트라 fixed가 모달에 깔림 — body 포털로 탈출 */}
      {toast && createPortal(
        <span className="auth-toast">✓ {email} 계정으로 로그인되었습니다</span>,
        document.body
      )}
      <span className="auth-email" title={email}>{email}</span>
      <button className="dt-help auth-btn" onClick={() => { logout(); setEmail(null) }} title="로그아웃">
        <span>로그아웃</span>
      </button>
    </span>
  )
}
