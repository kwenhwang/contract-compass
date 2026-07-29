// GlossaryPage — 디자이너(Zippt) v2 핸드오프 디자인 적용.
// 출처: design_preview_v2/glossary.jsx · glossary.css
// - 라이브 API 유지: getGlossary · getLawArticle · searchLaw
// - 모달 셸(onClose) 인터페이스 유지 (App.tsx 호출부 유지)
// - 카테고리 필터: 백엔드 응답에 cat 필드가 있을 때만 노출 (graceful)
// - 상태 컴포넌트는 components/states (EmptyState · ErrorState · Spinner) 사용

import { useEffect, useMemo, useState, type ReactNode } from 'react'
import {
  getGlossary,
  getLawArticle,
  searchLaw,
  type GlossaryTerm as ApiGlossaryTerm,
  type LawArticle,
  type LawSearchHit,
} from '../api/client'
import Icon from '../components/Icon'
import { EmptyState, ErrorState, Spinner } from '../components/states'

// 백엔드 응답이 cat 필드를 포함할 수 있으므로 옵션 확장 타입을 사용한다.
type GlossaryTerm = ApiGlossaryTerm & { cat?: string }

// ── Inline law-reference badge (expands article inline) ──────────────
function LawRefBadge({ refText }: { refText: string }) {
  const [open, setOpen] = useState(false)
  const [article, setArticle] = useState<LawArticle | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const toggle = () => {
    if (!open && !article && !loading) {
      setLoading(true)
      setError(null)
      getLawArticle(refText)
        .then(setArticle)
        .catch(() => setError('해당 조문을 찾을 수 없습니다'))
        .finally(() => setLoading(false))
    }
    setOpen((v) => !v)
  }

  return (
    <span className="lawref-wrap">
      <button className={`lawref ${open ? 'on' : ''}`} onClick={toggle} type="button">
        <Icon name="scale" size={11} /> {refText}
        <Icon name={open ? 'chevron-up' : 'chevron-down'} size={11} className="lawref-chev" />
      </button>
      {open && (
        <div className="lawref-panel">
          {loading && <p className="lawref-content">불러오는 중…</p>}
          {error && <p className="lawref-missing">{error}</p>}
          {article && (
            <>
              <p className="lawref-title">{article.law_name} · {article.article}</p>
              <p className="lawref-content">{article.content}</p>
            </>
          )}
        </div>
      )}
    </span>
  )
}

// ── Law search result (expandable) ──────────────────────────────────
function LawSearchResult({ hit }: { hit: LawSearchHit }) {
  const [expanded, setExpanded] = useState(false)
  return (
    <button
      className="lawhit"
      onClick={() => setExpanded((v) => !v)}
      type="button"
    >
      <div className="lawhit-head">
        <span className="lawhit-ic"><Icon name="scale" size={13} /></span>
        <span className="lawhit-name">{hit.law_name}</span>
        <span className="lawhit-art">{hit.article}</span>
        <span className="lawhit-toggle">
          {expanded ? '접기' : '전문'}{' '}
          <Icon name={expanded ? 'chevron-up' : 'chevron-down'} size={12} />
        </span>
      </div>
      <p className={`lawhit-body ${expanded ? 'full' : ''}`}>
        {expanded ? hit.content : hit.snippet}
      </p>
    </button>
  )
}

// ── 검색어 강조 ─────────────────────────────────────────────────────
function highlight(text: string, q: string): ReactNode {
  if (!q) return text
  const lower = text.toLowerCase()
  const idx = lower.indexOf(q.toLowerCase())
  if (idx < 0) return text
  return (
    <>
      {text.slice(0, idx)}
      <mark className="gl-hl">{text.slice(idx, idx + q.length)}</mark>
      {text.slice(idx + q.length)}
    </>
  )
}

const DEFAULT_CAT = '전체'

export default function GlossaryPage({ onClose }: { onClose: () => void }) {
  const [mode, setMode] = useState<'term' | 'law'>('term')
  const [terms, setTerms] = useState<GlossaryTerm[]>([])
  const [termsLoading, setTermsLoading] = useState(true)
  const [termsError, setTermsError] = useState<string | null>(null)

  const [query, setQuery] = useState('')
  const [cat, setCat] = useState<string>(DEFAULT_CAT)

  const [lawHits, setLawHits] = useState<LawSearchHit[]>([])
  const [lawLoading, setLawLoading] = useState(false)
  const [lawError, setLawError] = useState<string | null>(null)

  // 용어 목록 로드 (에러 시 재시도 가능)
  const loadTerms = () => {
    setTermsLoading(true)
    setTermsError(null)
    getGlossary()
      .then((data) => setTerms(data as GlossaryTerm[]))
      .catch(() => setTermsError('용어 목록을 불러오지 못했습니다'))
      .finally(() => setTermsLoading(false))
  }
  useEffect(loadTerms, [])

  // 법령 검색 — 디바운스 320ms (디자이너 데모와 동일)
  useEffect(() => {
    if (mode !== 'law') return
    const q = query.trim()
    if (!q) {
      setLawHits([])
      setLawLoading(false)
      return
    }
    setLawLoading(true)
    setLawError(null)
    const t = setTimeout(() => {
      searchLaw(q)
        .then(setLawHits)
        .catch(() => setLawError('검색 중 오류가 발생했습니다'))
        .finally(() => setLawLoading(false))
    }, 320)
    return () => clearTimeout(t)
  }, [mode, query])

  // 카테고리 목록 — 백엔드 응답에 cat 필드가 있을 때만 노출
  const catList = useMemo(() => {
    const set = new Set<string>()
    terms.forEach((t) => { if (t.cat) set.add(t.cat) })
    if (set.size === 0) return null
    return [DEFAULT_CAT, ...Array.from(set)]
  }, [terms])

  const filteredTerms = useMemo(() => {
    const q = query.trim().toLowerCase()
    return terms.filter((t) => {
      const catOk = !catList || cat === DEFAULT_CAT || t.cat === cat
      const qOk = !q || t.term.toLowerCase().includes(q) || t.definition.toLowerCase().includes(q)
      return catOk && qOk
    })
  }, [terms, query, cat, catList])

  const switchMode = (m: 'term' | 'law') => {
    if (m === mode) return
    setMode(m)
    setQuery('')
    setLawHits([])
    setLawError(null)
  }

  const headerSub = mode === 'term'
    ? `${terms.length}개 용어 · 법령 배지 클릭 시 조문 표시`
    : '법령 조문 · 키워드 또는 조문번호로 검색'

  return (
    <div
      className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4 max-[600px]:p-0"
      onClick={onClose}
    >
      <div
        className="gl-shell"
        style={{
          width: '100%',
          maxWidth: 720,
          height: '85dvh',
          maxHeight: 760,
          borderRadius: 'var(--radius-xl)',
          boxShadow: 'var(--shadow-lg)',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* header */}
        <header className="gl-head">
          <div className="gl-head-title">
            <span className="gl-head-glyph">
              <Icon name="book-open" size={16} strokeWidth={2.2} />
            </span>
            <div style={{ flex: 1, minWidth: 0 }}>
              <h2>용어사전 · 법령 검색</h2>
              <p>{headerSub}</p>
            </div>
            <button
              onClick={onClose}
              aria-label="닫기"
              type="button"
              className="gl-search-clear"
              style={{ width: 28, height: 28, flexShrink: 0 }}
            >
              <Icon name="x" size={14} />
            </button>
          </div>
        </header>

        {/* mode tabs + search */}
        <div className="gl-controls">
          <div className="gl-modes">
            <button
              type="button"
              className={`gl-mode ${mode === 'term' ? 'on term' : ''}`}
              onClick={() => switchMode('term')}
            >
              <Icon name="book-open" size={14} /> 용어사전
            </button>
            <button
              type="button"
              className={`gl-mode ${mode === 'law' ? 'on law' : ''}`}
              onClick={() => switchMode('law')}
            >
              <Icon name="scale" size={14} /> 법령 검색
            </button>
          </div>

          <div className={`gl-search ${mode === 'law' ? 'law' : ''}`}>
            <Icon name="search" size={16} className="gl-search-ic" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={
                mode === 'term'
                  ? '용어 또는 설명으로 검색…'
                  : '법령 키워드·조문번호 (예: 수의계약, 제26조)'
              }
              autoFocus
            />
            {query && (
              <button
                type="button"
                className="gl-search-clear"
                onClick={() => setQuery('')}
                aria-label="검색어 지우기"
              >
                <Icon name="x" size={14} />
              </button>
            )}
          </div>

          {mode === 'term' && catList && (
            <div className="gl-cats">
              {catList.map((c) => (
                <button
                  key={c}
                  type="button"
                  className={`gl-cat ${cat === c ? 'on' : ''}`}
                  onClick={() => setCat(c)}
                >
                  {c}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* body */}
        <div className="gl-body">
          {mode === 'term' ? (
            termsLoading ? (
              <div className="gl-loading"><Spinner size="md" label="용어를 불러오는 중" /></div>
            ) : termsError ? (
              <ErrorState
                code="GLOSSARY"
                title={termsError}
                sub="네트워크 상태를 확인하고 다시 시도해 주세요."
                onRetry={loadTerms}
              />
            ) : filteredTerms.length === 0 ? (
              <EmptyState
                variant="search"
                title={query ? `"${query}" 검색 결과가 없습니다` : '표시할 용어가 없습니다'}
                sub="다른 키워드나 카테고리로 찾아보세요."
                action={query || cat !== DEFAULT_CAT
                  ? { label: '전체 보기', onClick: () => { setQuery(''); setCat(DEFAULT_CAT) } }
                  : undefined}
              />
            ) : (
              <div className="gl-grid">
                {filteredTerms.map((t) => (
                  <div key={t.term} className="gl-card">
                    <div className="gl-card-head">
                      <h3 className="gl-term">{highlight(t.term, query.trim())}</h3>
                      {t.cat && <span className="gl-term-cat">{t.cat}</span>}
                    </div>
                    <p className="gl-def">{highlight(t.definition, query.trim())}</p>
                    {t.related.length > 0 && (
                      <div className="gl-refs">
                        {t.related.map((r) => <LawRefBadge key={r} refText={r} />)}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )
          ) : (
            !query.trim() ? (
              <div className="gl-law-hint">
                <span className="gl-law-hint-ic"><Icon name="scale" size={26} /></span>
                <p className="gl-law-hint-title">법령·내부 사규 조문을 검색하세요</p>
                <p className="gl-law-hint-sub">키워드 또는 조문번호로 찾을 수 있습니다.</p>
                <div className="gl-law-examples">
                  {['수의계약', '제26조', '지역제한', '물가변동', '계약보증금'].map((ex) => (
                    <button
                      key={ex}
                      type="button"
                      className="gl-law-ex"
                      onClick={() => setQuery(ex)}
                    >
                      {ex}
                    </button>
                  ))}
                </div>
              </div>
            ) : lawLoading ? (
              <div className="gl-loading"><Spinner size="md" label="조문 검색 중" /></div>
            ) : lawError ? (
              <ErrorState
                code="LAW"
                title={lawError}
                sub="잠시 후 다시 시도하거나 다른 키워드로 검색해 주세요."
                onRetry={() => setQuery((q) => q)}
              />
            ) : lawHits.length === 0 ? (
              <EmptyState
                variant="search"
                title={`"${query}" 조문이 없습니다`}
                sub="조문번호(제26조) 또는 핵심 키워드로 검색해 보세요."
              />
            ) : (
              <div className="gl-law-results">
                <p className="gl-law-count">{lawHits.length}개 조문 매칭</p>
                {lawHits.map((h) => <LawSearchResult key={h.law_ref} hit={h} />)}
              </div>
            )
          )}
        </div>
      </div>
    </div>
  )
}
