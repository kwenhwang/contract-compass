import { useState } from 'react'
import { submitFeedback } from '../../api/client'

const STORAGE_KEY = 'cc_feedback_submitted'

// 캡처 데이터 URL 상한(디코드 대략치 ~7MB) — 초과 시 화면은 버리고 의견만 전송(백엔드 8MB 거부 방어).
const MAX_SHOT_DATAURL_LEN = 9_400_000

function wasSubmitted(sessionId: string): boolean {
  try {
    const ids: string[] = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]')
    return ids.includes(sessionId)
  } catch { return false }
}

function markSubmitted(sessionId: string) {
  try {
    const ids: string[] = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]')
    if (!ids.includes(sessionId)) {
      ids.push(sessionId)
      localStorage.setItem(STORAGE_KEY, JSON.stringify(ids.slice(-50)))
    }
  } catch { /* ignore */ }
}

/** 현재 보이는 화면(뷰포트)을 html2canvas로 PNG data URL로 캡처.
 *  - 라이브러리는 동적 import(코드 스플릿) — 로드/캡처 실패해도 null을 반환해 의견 전송은 유지.
 *  - 뷰포트 영역만 scale 1로 캡처해 용량을 상한 아래로 억제. */
async function captureScreen(): Promise<string | null> {
  try {
    const { default: html2canvas } = await import('html2canvas')
    const canvas = await html2canvas(document.body, {
      logging: false,
      useCORS: true,
      backgroundColor: '#ffffff',
      scale: 1,
      x: window.scrollX,
      y: window.scrollY,
      width: window.innerWidth,
      height: window.innerHeight,
    })
    const dataUrl = canvas.toDataURL('image/png')
    if (dataUrl.length > MAX_SHOT_DATAURL_LEN) return null // 너무 크면 화면은 포기
    return dataUrl
  } catch {
    return null // 브라우저 제약·CORS·미지원 → 의견만 전송
  }
}

function currentContext() {
  return {
    url: typeof window !== 'undefined' ? window.location.href : undefined,
    viewport: typeof window !== 'undefined'
      ? `${window.innerWidth}x${window.innerHeight}@${window.devicePixelRatio || 1}`
      : undefined,
    user_agent: typeof navigator !== 'undefined' ? navigator.userAgent : undefined,
  }
}

export default function FeedbackBox({ sessionId, label, page, step }: { sessionId: string; label?: string; page?: string; step?: string }) {
  const [rating, setRating] = useState<1 | -1 | null>(null)
  const [comment, setComment] = useState('')
  const [attachShot, setAttachShot] = useState(true) // 기본 첨부
  const [capturing, setCapturing] = useState(false)
  const [submitted, setSubmitted] = useState(() => wasSubmitted(sessionId))

  if (!sessionId) return null

  const handleSubmit = async (r: 1 | -1) => {
    setRating(r)
    // 의견(👎)에만 원클릭 화면 캡처를 시도 — 캡처는 실패해도 의견은 반드시 전송(방어).
    let screenshot: string | undefined
    const ctx = r === -1 ? currentContext() : {}
    if (r === -1 && attachShot) {
      setCapturing(true)
      try {
        screenshot = (await captureScreen()) ?? undefined
      } finally {
        setCapturing(false)
      }
    }
    try {
      await submitFeedback({
        session_id: sessionId,
        rating: r,
        comment: comment || undefined,
        page,
        step,
        screenshot,
        ...ctx,
      })
    } catch {
      // 캡처 첨부 시 백엔드 거부(예: 용량) 가능성 → 화면 없이 의견만 재전송(의견 유실 방지)
      if (screenshot) {
        try {
          await submitFeedback({ session_id: sessionId, rating: r, comment: comment || undefined, page, step, ...ctx })
        } catch { /* 최종 실패는 조용히 — UX상 감사 메시지는 그대로 노출 */ }
      }
    }
    markSubmitted(sessionId)
    setSubmitted(true)
  }

  if (submitted) {
    return (
      <div className="border border-green-200 bg-green-50 rounded-lg px-4 py-3 text-sm text-green-700 text-center">
        피드백을 보내주셔서 감사합니다.
      </div>
    )
  }

  return (
    <div className="border border-gray-200 rounded-lg p-4 space-y-3">
      <p className="text-sm font-semibold text-gray-700">{label ?? '이 단계 결과가 도움이 되었나요?'}</p>
      <div className="flex gap-3">
        <button
          onClick={() => handleSubmit(1)}
          className={`flex-1 py-2 rounded-lg text-sm font-semibold border-2 transition-colors
            ${rating === 1 ? 'border-green-500 bg-green-50 text-green-700' : 'border-gray-200 text-gray-600 hover:border-green-300'}`}
        >
          👍 도움됨
        </button>
        <button
          onClick={() => setRating(-1)}
          className={`flex-1 py-2 rounded-lg text-sm font-semibold border-2 transition-colors
            ${rating === -1 ? 'border-red-400 bg-red-50 text-red-600' : 'border-gray-200 text-gray-600 hover:border-red-300'}`}
        >
          👎 아쉬움
        </button>
      </div>
      {rating === -1 && (
        <div className="space-y-2">
          <textarea
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            rows={2}
            placeholder="어떤 점이 아쉬웠나요? (선택)"
            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500"
          />
          <label className="flex items-center gap-2 text-xs text-gray-600 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={attachShot}
              onChange={(e) => setAttachShot(e.target.checked)}
              className="rounded border-gray-300"
            />
            📸 현재 화면을 함께 보내기 (캡처가 자동 첨부됩니다)
          </label>
          {attachShot && (
            <p className="text-[11px] leading-tight text-gray-400">
              지금 보이는 화면이 이미지로 함께 전송됩니다. 민감정보가 보이면 체크를 해제하세요. (화면은 서버에만 저장되고 외부로 전송되지 않습니다.)
            </p>
          )}
          <button
            onClick={() => handleSubmit(-1)}
            disabled={capturing}
            className="w-full py-2 bg-red-500 hover:bg-red-600 disabled:opacity-60 text-white text-sm font-semibold rounded-lg"
          >
            {capturing ? '화면 캡처 중…' : '의견 전송'}
          </button>
        </div>
      )}
    </div>
  )
}
