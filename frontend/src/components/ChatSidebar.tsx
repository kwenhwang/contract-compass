/**
 * PC 데스크탑 사이드 채팅 패널 (≥1024px만 노출).
 * AskPage 모달의 핵심 흐름(SSE stream + sources)을 사이드 컬럼으로 재구성.
 *
 * 현재 wizard 화면 컨텍스트(useWizardStore)를 자동으로 LLM에 전달 →
 * "이 사업에 PQ 필요?" 같은 질문에 step1Input·candidate를 보고 구체 답변.
 */
import { useEffect, useRef, useState } from 'react'
import { useWizardStore } from '../store/wizardStore'
import { getDeviceId } from '../lib/deviceId'
import { authHeaders, login } from '../lib/auth'
import Icon from './Icon'

type ChatMsg = {
  id: string
  role: 'user' | 'assistant'
  text: string
  sources?: Array<{
    section_title: string
    source_type: string
    relevance_score: number
    matched_via?: string
    matched_question?: string
  }>
  streaming?: boolean
  loginRequired?: boolean
}

const STORAGE_OPEN = 'cc_chat_sidebar_open'

export default function ChatSidebar() {
  const { currentStep, step1Input, step1Result, step2Result, step2Conditions } = useWizardStore()
  const [open, setOpen] = useState<boolean>(() => {
    try { return localStorage.getItem(STORAGE_OPEN) !== '0' } catch { return true }
  })
  const [input, setInput] = useState('')
  const [messages, setMessages] = useState<ChatMsg[]>([
    {
      id: 'welcome',
      role: 'assistant',
      text: '안녕하세요. 계약방법·법령·시스템 사용법 무엇이든 물어보세요. 현재 화면 맥락을 함께 참고합니다.',
    },
  ])
  const [busy, setBusy] = useState(false)
  const bodyRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    try { localStorage.setItem(STORAGE_OPEN, open ? '1' : '0') } catch {}
  }, [open])

  useEffect(() => {
    if (bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight
  }, [messages])

  const buildContext = () => ({
    step: currentStep,
    project_name: step1Input.project_name,
    contract_type: step1Input.contract_type,
    estimated_price: step1Input.estimated_price,
    description: step1Input.description,
    suggested_method: step1Result?.candidates?.[0]?.method,
    suggested_rule_id: step1Result?.candidates?.[0]?.rule_id,
    final_method: step2Result?.method,
    // F38-B (2026-06-11): Step2 선택 정보(중기간·추가조건·선택법령)를 챗봇 컨텍스트에 합류 — 사용자 의견 "현재 컨텍스트가 모두 전달되는가" 정면
    is_sme_competition_product: step1Input.is_sme_competition_product,
    sme_product_codes: step1Input.sme_product_codes,
    sme_product_names: step1Input.sme_product_names,
    additional_conditions: step2Conditions,
    selected_law_keys: (step2Result as { legal_basis?: string[] } | null)?.legal_basis,
  })

  const send = async () => {
    const q = input.trim()
    if (!q || busy) return
    setInput('')
    setBusy(true)
    const userMsg: ChatMsg = { id: `u-${Date.now()}`, role: 'user', text: q }
    const asstId = `a-${Date.now()}`
    const asstMsg: ChatMsg = { id: asstId, role: 'assistant', text: '', streaming: true }
    setMessages((m) => [...m, userMsg, asstMsg])

    try {
      const resp = await fetch('/api/v1/ask/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Device-Id': getDeviceId(), ...authHeaders() },
        body: JSON.stringify({ question: q, context: buildContext() }),
      })
      if (!resp.ok || !resp.body) {
        const detail = await resp.text().catch(() => '')
        // 익명 무료 소진/토큰 만료 (2026-07-29) — 로그인 유도
        if (resp.status === 401) {
          let msg = '계속 이용하려면 Google 로그인이 필요합니다.'
          try { msg = JSON.parse(detail).detail?.message || msg } catch {}
          setMessages((m) => m.map((x) => x.id === asstId
            ? { ...x, text: msg, streaming: false, loginRequired: true } : x))
          return
        }
        const errText = resp.status === 429
          ? '요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.'
          : `오류 (${resp.status}). ${detail.slice(0, 100)}`
        setMessages((m) => m.map((x) => x.id === asstId
          ? { ...x, text: errText, streaming: false } : x))
        return
      }
      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buf = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop() || ''
        for (const line of lines) {
          if (!line.startsWith('data:')) continue
          const payload = line.slice(5).trim()
          if (!payload || payload === '[DONE]') continue
          try {
            const ev = JSON.parse(payload)
            if (ev.type === 'token') {
              setMessages((m) => m.map((x) => x.id === asstId
                ? { ...x, text: x.text + (ev.text || '') } : x))
            } else if (ev.type === 'sources') {
              setMessages((m) => m.map((x) => x.id === asstId
                ? { ...x, sources: ev.sources } : x))
            } else if (ev.type === 'done') {
              setMessages((m) => m.map((x) => x.id === asstId
                ? { ...x, streaming: false } : x))
            }
          } catch {}
        }
      }
    } catch (e: any) {
      setMessages((m) => m.map((x) => x.id === asstId
        ? { ...x, text: '네트워크 오류. 다시 시도해 주세요.', streaming: false } : x))
    } finally {
      setBusy(false)
      setMessages((m) => m.map((x) => x.id === asstId && x.streaming
        ? { ...x, streaming: false } : x))
    }
  }

  const onKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  return (
    <aside className={`dt-chat-sidebar ${open ? 'open' : 'closed'}`}>
      <header className="dt-chat-head">
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span className="dt-chat-glyph"><Icon name="message-circle" size={14} strokeWidth={2.2} /></span>
          <span className="dt-chat-title">계약 도우미</span>
        </div>
        <button
          onClick={() => setOpen((v) => !v)}
          className="dt-chat-toggle"
          aria-label={open ? '접기' : '펴기'}
          title={open ? '접기' : '펴기'}
        >
          <Icon name={open ? 'arrow-up-right' : 'message-circle'} size={14} />
        </button>
      </header>

      {open && (
        <>
          <div className="dt-chat-body" ref={bodyRef}>
            {messages.map((m) => (
              <div key={m.id} className={`dt-chat-msg ${m.role}`}>
                <div className="dt-chat-bubble">
                  {m.text}
                  {m.streaming && <span className="dt-chat-cursor">▍</span>}
                  {m.loginRequired && (
                    <button
                      onClick={() => login('decide')}
                      style={{ display: 'block', marginTop: 8, padding: '6px 12px', borderRadius: 6, border: '1px solid #d1d5db', background: '#fff', cursor: 'pointer', fontWeight: 600 }}
                    >
                      Google로 로그인
                    </button>
                  )}
                </div>
                {m.sources && m.sources.length > 0 && (
                  <div className="dt-chat-sources">
                    {m.sources.slice(0, 3).map((s, i) => (
                      <div key={i} className="dt-chat-src">
                        <span className="dt-chat-src-type">{s.source_type}</span>
                        <span className="dt-chat-src-title">{s.section_title?.slice(0, 40) || '출처'}</span>
                        {s.matched_via === 'doc2query' && s.matched_question && (
                          <em className="dt-chat-src-q">💡 {s.matched_question.slice(0, 50)}</em>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>

          <div className="dt-chat-input">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKey}
              placeholder={busy ? '답변 중...' : '질문하세요 (Enter 전송)'}
              rows={2}
              disabled={busy}
            />
            <button onClick={send} disabled={!input.trim() || busy} className="dt-chat-send">
              <Icon name="arrow-up" size={14} />
            </button>
          </div>
        </>
      )}
    </aside>
  )
}
