// 사내망 NAT 공유 IP 뒤에서도 브라우저 단위로 접속자를 구분하기 위한 영구 익명 ID.
// localStorage에 1회 생성 후 재사용(로그인 없는 앱이라 이게 유일한 기기 식별 수단).
const KEY = 'cc_device_id'

export function getDeviceId(): string {
  try {
    let id = localStorage.getItem(KEY)
    if (!id) {
      id = `dev-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
      localStorage.setItem(KEY, id)
    }
    return id
  } catch {
    return 'no-storage'
  }
}
