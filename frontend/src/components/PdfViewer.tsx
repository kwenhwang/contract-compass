/**
 * PDF.js 기반 원문 viewer.
 * props로 받은 document_id → /api/v1/docs/source/{id} fetch → canvas 렌더.
 * searchText 있으면 첫 매칭 페이지로 자동 점프.
 */
import { useEffect, useRef, useState } from 'react'
import * as pdfjsLib from 'pdfjs-dist'
import pdfWorker from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import mammoth from 'mammoth'
import Icon from './Icon'

pdfjsLib.GlobalWorkerOptions.workerSrc = pdfWorker

// DOCX는 PDF.js로 렌더 불가 — 알려진 docx document_id 명시 (viewer 대신 다운로드 안내)
const DOCX_DOCS = new Set<string>()

export default function PdfViewer({
  documentId,
  searchText,
  onClose,
}: {
  documentId: string
  searchText?: string
  onClose: () => void
}) {
  const isDocx = DOCX_DOCS.has(documentId)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const docxRef = useRef<HTMLDivElement>(null)
  const [pdf, setPdf] = useState<any>(null)
  const [docxHtml, setDocxHtml] = useState<string | null>(null)
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [scale, setScale] = useState(1.4)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [searchHit, setSearchHit] = useState<{ page: number; text: string } | null>(null)

  // DOCX: mammoth로 HTML 변환
  useEffect(() => {
    if (!isDocx) return
    let cancelled = false
    const url = `/api/v1/docs/source/${encodeURIComponent(documentId)}`
    setLoading(true); setError(null)
    fetch(url)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.arrayBuffer()
      })
      .then((buf) => mammoth.convertToHtml({ arrayBuffer: buf }))
      .then((res) => {
        if (cancelled) return
        setDocxHtml(res.value || '<p style="color:#888">변환된 내용 없음</p>')
        setLoading(false)
        // searchText로 첫 매칭 위치 scroll (단순 indexOf 후 scrollIntoView는 어려워 highlight만)
        // mammoth output 안에서 highlight 처리는 다음 effect에서 처리 (docxRef)
      })
      .catch((e) => {
        if (!cancelled) {
          setError(`DOCX 변환 실패: ${e?.message || e}`)
          setLoading(false)
        }
      })
    return () => { cancelled = true }
  }, [documentId, isDocx])

  // DOCX searchText 강조 — 단순 텍스트 substring → mark 감싸기
  useEffect(() => {
    if (!isDocx || !docxHtml || !searchText || !docxRef.current) return
    const q = searchText.trim().slice(0, 30).replace(/\s+/g, ' ')
    if (q.length < 5) return
    const html = docxRef.current.innerHTML
    // HTML 안전 마킹 — 텍스트 노드만 순회 (간단 버전: innerHTML에 mark 감쌈, HTML escape 가능성)
    // mammoth가 안전한 HTML 생성하므로 단순 replace로 시도
    try {
      const escaped = q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
      const re = new RegExp(escaped.slice(0, 20), 'i')
      if (re.test(html)) {
        docxRef.current.innerHTML = html.replace(re, (m) => `<mark style="background:#fef3c7;padding:1px 3px">${m}</mark>`)
        // 첫 mark scroll
        setTimeout(() => {
          const el = docxRef.current?.querySelector('mark')
          el?.scrollIntoView({ behavior: 'smooth', block: 'center' })
        }, 100)
      }
    } catch {}
  }, [isDocx, docxHtml, searchText])

  // PDF 로드
  useEffect(() => {
    if (isDocx) return
    let cancelled = false
    const url = `/api/v1/docs/source/${encodeURIComponent(documentId)}`
    setLoading(true); setError(null)
    pdfjsLib.getDocument({ url }).promise
      .then(async (doc) => {
        if (cancelled) return
        setPdf(doc)
        setTotal(doc.numPages)
        // searchText 있으면 페이지 검색
        if (searchText && searchText.trim().length > 8) {
          const q = searchText.trim().slice(0, 80).replace(/\s+/g, ' ')
          for (let p = 1; p <= Math.min(doc.numPages, 200); p++) {
            const pg = await doc.getPage(p)
            const txt = await pg.getTextContent()
            const txtStr = txt.items.map((it: any) => it.str || '').join(' ').replace(/\s+/g, ' ')
            if (txtStr.includes(q.slice(0, 30))) {
              if (!cancelled) {
                setSearchHit({ page: p, text: q })
                setPage(p)
              }
              break
            }
          }
        }
        setLoading(false)
      })
      .catch((e) => {
        if (!cancelled) {
          setError(`PDF 로드 실패: ${e?.message || e}`)
          setLoading(false)
        }
      })
    return () => { cancelled = true }
  }, [documentId, searchText])

  // 렌더
  useEffect(() => {
    if (!pdf || !canvasRef.current) return
    let cancelled = false
    pdf.getPage(page).then((pg: any) => {
      if (cancelled || !canvasRef.current) return
      const viewport = pg.getViewport({ scale })
      const canvas = canvasRef.current
      const ctx = canvas.getContext('2d')
      if (!ctx) return
      canvas.width = viewport.width
      canvas.height = viewport.height
      pg.render({ canvasContext: ctx, viewport })
    })
    return () => { cancelled = true }
  }, [pdf, page, scale])

  return (
    <div className="pdf-viewer-scrim" onClick={onClose}>
      <div className="pdf-viewer-card" onClick={(e) => e.stopPropagation()}>
        <header className="pdf-viewer-head">
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Icon name="file-text" size={16} />
            <span style={{ fontWeight: 700, fontSize: 14 }}>원문 보기</span>
            <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>{documentId}</span>
            {searchHit && (
              <span style={{ fontSize: 11, color: 'var(--accent-secondary)', marginLeft: 8 }}>
                🔍 페이지 {searchHit.page}로 자동 이동
              </span>
            )}
          </div>
          <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            {!isDocx && (
              <>
                <button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page <= 1} className="pdf-btn">
                  <Icon name="arrow-up" size={14} style={{ transform: 'rotate(-90deg)' }} />
                </button>
                <span style={{ fontSize: 12, minWidth: 70, textAlign: 'center' }}>{page} / {total}</span>
                <button onClick={() => setPage((p) => Math.min(total, p + 1))} disabled={page >= total} className="pdf-btn">
                  <Icon name="arrow-right" size={14} />
                </button>
                <button onClick={() => setScale((s) => Math.max(0.6, s - 0.2))} className="pdf-btn">−</button>
                <span style={{ fontSize: 11, minWidth: 40, textAlign: 'center' }}>{Math.round(scale * 100)}%</span>
                <button onClick={() => setScale((s) => Math.min(3, s + 0.2))} className="pdf-btn">+</button>
                <a
                  href={`/api/v1/docs/source/${encodeURIComponent(documentId)}`}
                  target="_blank" rel="noopener noreferrer"
                  className="pdf-btn"
                  title="새 탭에서 열기"
                ><Icon name="external-link" size={13} /></a>
              </>
            )}
            <button onClick={onClose} className="pdf-btn" aria-label="닫기"><Icon name="x" size={14} /></button>
          </div>
        </header>
        <div className="pdf-viewer-body">
          {loading && <div className="pdf-loading">{isDocx ? 'DOCX 변환 중...' : 'PDF 로딩 중...'}</div>}
          {error && <div className="pdf-error">{error}</div>}
          {!loading && !error && isDocx && (
            <div className="docx-content" ref={docxRef} dangerouslySetInnerHTML={{ __html: docxHtml || '' }} />
          )}
          {!loading && !error && !isDocx && <canvas ref={canvasRef} />}
        </div>
      </div>
    </div>
  )
}
