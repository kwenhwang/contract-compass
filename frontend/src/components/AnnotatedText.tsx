import React, { useEffect, useState } from 'react'
import { getGlossary, getLawArticle, type GlossaryTerm, type LawArticle } from '../api/client'

let _glossaryCache: GlossaryTerm[] | null = null

function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

const LAW_REF_PATTERN = /(?:국가계약법\s+)?(?:시행령|시행규칙|국가계약법|건설기술\s*진흥법|엔지니어링산업\s*진흥법|물품관리법|공기업[ㆍ·]?준정부기관\s*계약사무규칙|중소기업제품[^\s]*\s*법률)?\s*제\d+조(?:의\d+)?(?:\s*제\d+항)?/g

type Part =
  | { kind: 'text'; value: string }
  | { kind: 'term'; term: GlossaryTerm; key: string }
  | { kind: 'law'; ref: string; key: string }

function annotateText(text: string, terms: GlossaryTerm[]): Part[] {
  const lawSpans: { start: number; end: number; ref: string }[] = []
  for (const m of text.matchAll(LAW_REF_PATTERN)) {
    if (m.index === undefined) continue
    const matched = m[0].trim()
    if (!/제\d+조/.test(matched)) continue
    lawSpans.push({ start: m.index, end: m.index + m[0].length, ref: matched })
  }

  const sortedTerms = [...terms].sort((a, b) => b.term.length - a.term.length)
  const termPattern = sortedTerms.length > 0
    ? new RegExp(`(${sortedTerms.map((t) => escapeRegex(t.term)).join('|')})`, 'g')
    : null

  const termSpans: { start: number; end: number; term: GlossaryTerm }[] = []
  const seenTerms = new Set<string>()
  if (termPattern) {
    for (const m of text.matchAll(termPattern)) {
      if (m.index === undefined) continue
      const matched = m[0]
      if (seenTerms.has(matched)) continue
      if (lawSpans.some((ls) => m.index! >= ls.start && m.index! < ls.end)) continue
      seenTerms.add(matched)
      const term = terms.find((t) => t.term === matched)
      if (!term) continue
      termSpans.push({ start: m.index, end: m.index + matched.length, term })
    }
  }

  const allSpans = [
    ...lawSpans.map((s) => ({ ...s, type: 'law' as const })),
    ...termSpans.map((s) => ({ ...s, type: 'term' as const })),
  ].sort((a, b) => a.start - b.start)

  const out: Part[] = []
  let lastIdx = 0
  let idx = 0
  for (const span of allSpans) {
    if (span.start < lastIdx) continue
    if (span.start > lastIdx) out.push({ kind: 'text', value: text.slice(lastIdx, span.start) })
    if (span.type === 'law') {
      out.push({ kind: 'law', ref: span.ref, key: `l-${idx++}-${span.start}` })
    } else {
      out.push({ kind: 'term', term: span.term, key: `t-${idx++}-${span.start}` })
    }
    lastIdx = span.end
  }
  if (lastIdx < text.length) out.push({ kind: 'text', value: text.slice(lastIdx) })
  return out
}

function TermSpan({ term }: { term: GlossaryTerm }) {
  const [open, setOpen] = useState(false)
  return (
    <span className="relative inline-block">
      <span
        onClick={() => setOpen((v) => !v)}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        className="text-blue-700 border-b border-dotted border-blue-400 cursor-help"
      >
        {term.term}
      </span>
      {open && (
        <span className="absolute left-0 top-full mt-1 z-30 w-64 bg-white border border-gray-200 rounded-lg shadow-lg p-2.5 text-xs text-gray-700 whitespace-normal font-normal">
          <span className="block font-semibold text-gray-900 mb-1">{term.term}</span>
          <span className="block leading-relaxed">{term.definition}</span>
          {term.related.length > 0 && (
            <span className="block mt-1.5 text-[10px] text-blue-600">{term.related.join(' · ')}</span>
          )}
        </span>
      )}
    </span>
  )
}

function LawSpan({ refText }: { refText: string }) {
  const [open, setOpen] = useState(false)
  const [article, setArticle] = useState<LawArticle | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fetchArticle = () => {
    if (article || loading) return
    setLoading(true)
    setError(null)
    getLawArticle(refText)
      .then(setArticle)
      .catch(() => setError('해당 조문을 찾을 수 없습니다'))
      .finally(() => setLoading(false))
  }

  return (
    <span className="relative inline-block">
      <span
        onClick={() => { setOpen((v) => !v); fetchArticle() }}
        className="text-purple-700 border-b border-dotted border-purple-400 cursor-pointer hover:text-purple-900"
      >
        ⚖️ {refText}
      </span>
      {open && (
        <span className="absolute left-0 top-full mt-1 z-30 w-80 max-h-72 overflow-y-auto bg-white border border-purple-200 rounded-lg shadow-lg p-3 text-xs text-gray-700 whitespace-normal font-normal">
          {loading && <span className="text-gray-400">불러오는 중...</span>}
          {error && <span className="text-red-500">{error}</span>}
          {article && (
            <>
              <span className="block font-semibold text-purple-900 mb-1.5">
                {article.law_name} {article.article}
              </span>
              <span className="block leading-relaxed text-gray-700">{article.content}</span>
            </>
          )}
          <button
            onClick={(e) => { e.stopPropagation(); setOpen(false) }}
            className="block ml-auto mt-2 text-xs text-gray-400 hover:text-gray-600"
          >
            닫기
          </button>
        </span>
      )}
    </span>
  )
}

// 미니 markdown 렌더: **bold** 패턴만 처리. 외부 패키지 X.
function renderMiniMarkdown(text: string): React.ReactNode[] {
  const out: React.ReactNode[] = []
  const re = /\*\*([^*\n]+?)\*\*/g
  let last = 0
  let m: RegExpExecArray | null
  let idx = 0
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(<span key={`t${idx++}`}>{text.slice(last, m.index)}</span>)
    out.push(<strong key={`b${idx++}`} className="font-bold text-gray-900">{m[1]}</strong>)
    last = m.index + m[0].length
  }
  if (last < text.length) out.push(<span key={`t${idx++}`}>{text.slice(last)}</span>)
  return out
}

export default function AnnotatedText({ text }: { text: string }) {
  const [terms, setTerms] = useState<GlossaryTerm[]>(_glossaryCache ?? [])
  useEffect(() => {
    if (_glossaryCache) return
    getGlossary().then((ts) => {
      _glossaryCache = ts
      setTerms(ts)
    }).catch(() => {})
  }, [])
  const parts = annotateText(text, terms)
  return (
    <>
      {parts.map((p, i) => {
        if (p.kind === 'text') return <span key={i}>{renderMiniMarkdown(p.value)}</span>
        if (p.kind === 'term') return <TermSpan key={p.key} term={p.term} />
        return <LawSpan key={p.key} refText={p.ref} />
      })}
    </>
  )
}
