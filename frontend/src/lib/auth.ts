// 공유 Supabase GoTrue Google 로그인 (2026-07-29) — supabase-js 없이 implicit flow 최소 구현.
// 로그인 → GoTrue authorize 리다이렉트 → 복귀 URL 해시(#access_token=...)를 캡처해 저장.
// 토큰은 백엔드 ask 게이팅(Authorization: Bearer) 검증용. 만료(1h) 시 재로그인 유도.
// 동일 오리진 /auth/v1 — 리버스 프록시가 GoTrue로 중계 (외부 도메인 하드코딩 금지).
const AUTH_BASE = `${window.location.origin}/auth/v1`
const KEY = 'cc_auth_v1'

type AuthState = { access_token: string; expires_at: number; email?: string }

function readState(): AuthState | null {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return null
    const s = JSON.parse(raw) as AuthState
    if (!s.access_token || Date.now() / 1000 >= s.expires_at) {
      localStorage.removeItem(KEY)
      return null
    }
    return s
  } catch {
    return null
  }
}

/** JWT payload에서 email 추출 (표시용 — 검증은 백엔드 몫). */
function emailFromJwt(token: string): string | undefined {
  try {
    const payload = JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')))
    return payload.email
  } catch {
    return undefined
  }
}

/** 앱 부팅 시 1회 — GoTrue 복귀 해시(#access_token=...)를 캡처해 저장하고 해시를 정리. */
export function captureAuthFromHash(): void {
  const h = window.location.hash
  if (!h.includes('access_token=')) return
  const params = new URLSearchParams(h.replace(/^#/, ''))
  const token = params.get('access_token')
  if (!token) return
  const expiresIn = parseInt(params.get('expires_in') || '3600', 10)
  const state: AuthState = {
    access_token: token,
    expires_at: Math.floor(Date.now() / 1000) + expiresIn - 30, // 30s 여유
    email: emailFromJwt(token),
  }
  try { localStorage.setItem(KEY, JSON.stringify(state)) } catch {}
  // 로그인 직후 1회 토스트용 — 복귀 후 "로그인됐다"는 표시가 없으면 사용자가
  // 같은 자리 로그아웃 버튼을 다시 눌러 로그인/로그아웃 루프에 빠진다(2026-07-29 실측)
  try { sessionStorage.setItem('cc_login_toast', '1') } catch {}
  // 해시 라우팅(#home 등)과 충돌하지 않게 토큰 해시 제거
  history.replaceState(null, '', window.location.pathname + window.location.search)
}

export function getToken(): string | null {
  return readState()?.access_token ?? null
}

export function getUserEmail(): string | null {
  return readState()?.email ?? null
}

/** Authorization 헤더 조각 — 로그인 상태일 때만 추가. */
export function authHeaders(): Record<string, string> {
  const t = getToken()
  return t ? { Authorization: `Bearer ${t}` } : {}
}

/**
 * Google 간편 로그인. context를 주면 복귀 후 그 화면으로 돌아간다(?return= 쿼리 —
 * GoTrue가 redirect_to 뒤에 #access_token을 붙이므로 해시로는 전달 불가).
 */
export function login(context?: 'ask' | 'decide'): void {
  const redirect = encodeURIComponent(window.location.origin + '/' + (context ? `?return=${context}` : ''))
  window.location.href = `${AUTH_BASE}/authorize?provider=google&redirect_to=${redirect}`
}

export function logout(): void {
  // GoTrue 서버 세션도 무효화 (fire-and-forget — 실패해도 로컬 토큰 제거로 로그아웃 성립)
  const t = getToken()
  if (t) {
    fetch(`${AUTH_BASE}/logout`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${t}` },
    }).catch(() => {})
  }
  localStorage.removeItem(KEY)
}
