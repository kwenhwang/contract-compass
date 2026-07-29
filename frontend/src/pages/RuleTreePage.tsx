// RuleTreePage — 계약방법 룰엔진을 '동치 의사결정트리'로 시각화(도메인 전문가 검증용).
// 라이브 API: getRuleTree(/rules-public/tree). 트리는 RuleEngine.match와 1:1 동치로 자동 도출되며
// (학습된 결정트리 아님), coverage 전수검증으로 "트리=엔진"을 보장한다.
import { useEffect, useRef, useState } from 'react'
import mermaid from 'mermaid'
import { getRuleTree, type RuleTree, type RuleTreeNode } from '../api/client'

mermaid.initialize({
  startOnLoad: false,
  securityLevel: 'loose',
  theme: 'neutral',
  flowchart: { htmlLabels: true, curve: 'basis', nodeSpacing: 40, rankSpacing: 55 },
})

const TABS: { id: string; label: string }[] = [
  { id: 'service', label: '용역' },
  { id: 'product', label: '물품' },
  { id: 'construction', label: '공사' },
]

function NodeDetail({ node }: { node: RuleTreeNode }) {
  if (node.type === 'decision') {
    return (
      <div className="text-sm text-gray-700">
        <div className="font-semibold text-gray-900 mb-1">분기 질문</div>
        <div className="text-base">{node.question}</div>
        <p className="mt-3 text-xs text-gray-500">이 항목의 값에 따라 적용 룰이 갈립니다.</p>
      </div>
    )
  }
  return (
    <div className="text-sm text-gray-700 space-y-2">
      <div>
        <div className="text-xs text-gray-400">계약방법</div>
        <div className="text-base font-bold text-blue-700">{node.method}</div>
      </div>
      {node.name && <div className="text-gray-600">{node.name}</div>}
      <div className="flex flex-wrap gap-2 text-xs">
        {node.rule_id && <span className="px-2 py-0.5 rounded bg-gray-100 font-mono">{node.rule_id}</span>}
        {node.bidder_selection && <span className="px-2 py-0.5 rounded bg-gray-100">{node.bidder_selection}</span>}
      </div>
      {(node.pass_score || node.lower_limit_rate) && (
        <div className="text-sm">
          {node.pass_score ? <span className="mr-3">적격심사 <b>{node.pass_score}점</b></span> : null}
          {node.lower_limit_rate ? <span>낙찰하한율 <b>{(node.lower_limit_rate * 100).toFixed(3)}%</b></span> : null}
        </div>
      )}
      {node.legal_basis && node.legal_basis.length > 0 && (
        <div>
          <div className="text-xs text-gray-400 mb-1">법적 근거</div>
          <ul className="list-disc list-inside text-xs text-gray-600 space-y-0.5">
            {node.legal_basis.map((l, i) => <li key={i}>{l}</li>)}
          </ul>
        </div>
      )}
      {node.alternatives && node.alternatives.length > 0 && (
        <div>
          <div className="text-xs text-gray-400 mb-1">대안 방법</div>
          <div className="text-xs text-gray-600">{node.alternatives.filter(Boolean).join(' · ')}</div>
        </div>
      )}
    </div>
  )
}

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))

export default function RuleTreePage({ onClose }: { onClose: () => void }) {
  const [ct, setCt] = useState('service')
  const [tree, setTree] = useState<RuleTree | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [sel, setSel] = useState<string | null>(null)
  const svgRef = useRef<HTMLDivElement>(null)
  const viewportRef = useRef<HTMLDivElement>(null)
  // 팬/줌 상태
  const [scale, setScale] = useState(1)
  const [offset, setOffset] = useState({ x: 0, y: 0 })
  const [dragging, setDragging] = useState(false)
  const dragRef = useRef({ x: 0, y: 0, ox: 0, oy: 0, moved: false })
  const natRef = useRef({ w: 0, h: 0 })

  // 다이어그램 자연 크기를 뷰포트에 맞춰 중앙 배치(화면맞춤)
  const fit = () => {
    const vp = viewportRef.current
    const nat = natRef.current
    if (!vp || !nat.w) return
    const pad = 32
    const s = clamp(Math.min((vp.clientWidth - pad) / nat.w, (vp.clientHeight - pad) / nat.h), 0.2, 2)
    setScale(s)
    setOffset({ x: (vp.clientWidth - nat.w * s) / 2, y: Math.max(pad / 2, (vp.clientHeight - nat.h * s) / 2) })
  }

  const zoomBy = (factor: number) => {
    const vp = viewportRef.current
    if (!vp) return
    const cx = vp.clientWidth / 2, cy = vp.clientHeight / 2
    setScale((s) => {
      const ns = clamp(s * factor, 0.15, 6)
      setOffset((o) => ({ x: cx - (cx - o.x) * (ns / s), y: cy - (cy - o.y) * (ns / s) }))
      return ns
    })
  }

  useEffect(() => {
    let alive = true
    setLoading(true); setError(null); setSel(null)
    getRuleTree(ct)
      .then((t) => { if (alive) setTree(t) })
      .catch(() => { if (alive) setError('트리를 불러오지 못했습니다') })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [ct])

  useEffect(() => {
    if (!tree || !svgRef.current) return
    const host = svgRef.current
    let cancelled = false
    mermaid.render(`ruletree-${ct}-${Date.now()}`, tree.mermaid).then(({ svg, bindFunctions }) => {
      if (cancelled || !host) return
      host.innerHTML = svg
      bindFunctions?.(host)
      const svgEl = host.querySelector('svg')
      if (svgEl) {
        // 자연 크기 확보 후 변환은 래퍼가 담당하도록 inline 크기 제약 제거.
        // mermaid가 계산한 viewBox는 HTML 라벨 실측과 어긋나 우측이 잘릴 수 있어
        // 렌더된 실제 bounding box로 재설정한다(+여백).
        const vb = svgEl.viewBox?.baseVal
        let w = vb && vb.width ? vb.width : svgEl.getBoundingClientRect().width
        let h = vb && vb.height ? vb.height : svgEl.getBoundingClientRect().height
        try {
          const bb = (svgEl as unknown as SVGGraphicsElement).getBBox()
          if (bb.width > 0) {
            const pad = 24
            svgEl.setAttribute('viewBox', `${bb.x - pad} ${bb.y - pad} ${bb.width + pad * 2} ${bb.height + pad * 2}`)
            w = bb.width + pad * 2
            h = bb.height + pad * 2
          }
        } catch { /* getBBox 미지원/미부착 시 viewBox 값 유지 */ }
        natRef.current = { w, h }
        svgEl.removeAttribute('width'); svgEl.removeAttribute('height')
        svgEl.style.maxWidth = 'none'
        svgEl.style.overflow = 'visible'
        svgEl.style.width = `${natRef.current.w}px`
        svgEl.style.height = `${natRef.current.h}px`
      }
      // 노드 클릭 → 상세 패널. mermaid DOM id 예: "<diagramId>-flowchart-n4-7" → 노드키 n4 추출
      host.querySelectorAll<SVGGElement>('g.node').forEach((el) => {
        const m = el.id.match(/(n\d+)-\d+$/)
        const key = m ? m[1] : ''
        if (tree.nodes[key]) {
          el.style.cursor = 'pointer'
          el.addEventListener('click', () => { if (!dragRef.current.moved) setSel(key) })
        }
      })
      // 렌더 직후 화면맞춤
      requestAnimationFrame(() => { if (!cancelled) fit() })
    }).catch(() => { if (!cancelled) setError('다이어그램 렌더 실패') })
    return () => { cancelled = true }
  }, [tree, ct])

  // 마우스 휠 확대/축소(커서 기준). React onWheel은 passive라 native 리스너로 preventDefault.
  useEffect(() => {
    const vp = viewportRef.current
    if (!vp) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const rect = vp.getBoundingClientRect()
      const px = e.clientX - rect.left, py = e.clientY - rect.top
      const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12
      setScale((s) => {
        const ns = clamp(s * factor, 0.15, 6)
        setOffset((o) => ({ x: px - (px - o.x) * (ns / s), y: py - (py - o.y) * (ns / s) }))
        return ns
      })
    }
    vp.addEventListener('wheel', onWheel, { passive: false })
    return () => vp.removeEventListener('wheel', onWheel)
  }, [])

  const cov = tree?.coverage
  const fidelityOk = cov && cov.cells > 0 && cov.reproduced === cov.cells

  return (
    <div className="fixed inset-0 z-50 bg-white flex flex-col">
      {/* 헤더 */}
      <div className="border-b px-5 py-3 flex items-center justify-between shrink-0">
        <div>
          <h2 className="text-lg font-bold text-gray-900">계약방법 결정 트리 <span className="text-sm font-normal text-gray-400">(도메인 전문가 검증용)</span></h2>
          <p className="text-xs text-gray-500 mt-0.5">
            도메인을 직접 인코딩한 <b>룰엔진을 동치 의사결정트리로 자동 변환</b>한 것입니다 — 학습된 모델이 아닙니다.
          </p>
        </div>
        <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl leading-none">✕</button>
      </div>

      {/* 탭 + 충실도 배지 */}
      <div className="px-5 py-2 flex items-center gap-3 border-b shrink-0">
        <div className="flex gap-1">
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => setCt(t.id)}
              className={`px-3 py-1.5 rounded text-sm font-semibold ${ct === t.id ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'}`}
            >{t.label}</button>
          ))}
        </div>
        {fidelityOk && (
          <span className="ml-auto text-xs px-2 py-1 rounded bg-emerald-50 text-emerald-700 border border-emerald-200">
            ✓ 입력 {cov!.cells.toLocaleString()}개 영역 전수에서 엔진과 100% 일치
          </span>
        )}
      </div>

      {/* 본문: 트리 + 상세 (모바일=세로 스택, 데스크탑=좌우 분할) */}
      <div className="flex-1 flex flex-col md:flex-row min-h-0">
        <div
          ref={viewportRef}
          className="flex-1 relative overflow-hidden bg-gray-50 select-none"
          onMouseDown={(e) => {
            dragRef.current = { x: e.clientX, y: e.clientY, ox: offset.x, oy: offset.y, moved: false }
            setDragging(true)
          }}
          onMouseMove={(e) => {
            if (!dragging) return
            const dx = e.clientX - dragRef.current.x, dy = e.clientY - dragRef.current.y
            if (Math.abs(dx) > 3 || Math.abs(dy) > 3) dragRef.current.moved = true
            setOffset({ x: dragRef.current.ox + dx, y: dragRef.current.oy + dy })
          }}
          onMouseUp={() => setDragging(false)}
          onMouseLeave={() => setDragging(false)}
          style={{ cursor: dragging ? 'grabbing' : 'grab' }}
        >
          {loading && <div className="absolute inset-0 flex items-center justify-center text-gray-400 text-sm">불러오는 중…</div>}
          {error && <div className="absolute inset-0 flex items-center justify-center text-red-500 text-sm">{error}</div>}
          <div
            ref={svgRef}
            className="[&_svg]:max-w-none absolute top-0 left-0 origin-top-left"
            style={{ transform: `translate(${offset.x}px, ${offset.y}px) scale(${scale})` }}
          />
          {/* 줌 컨트롤 */}
          <div className="absolute bottom-4 left-4 flex items-center gap-1 bg-white/95 rounded-lg shadow border px-1.5 py-1">
            <button onClick={() => zoomBy(1 / 1.2)} title="축소" className="w-8 h-8 rounded hover:bg-gray-100 text-gray-700 text-lg font-bold">−</button>
            <span className="w-12 text-center text-xs text-gray-500 tabular-nums">{Math.round(scale * 100)}%</span>
            <button onClick={() => zoomBy(1.2)} title="확대" className="w-8 h-8 rounded hover:bg-gray-100 text-gray-700 text-lg font-bold">＋</button>
            <div className="w-px h-5 bg-gray-200 mx-1" />
            <button onClick={fit} title="화면맞춤" className="px-2 h-8 rounded hover:bg-gray-100 text-gray-600 text-xs font-semibold">화면맞춤</button>
          </div>
          <p className="hidden sm:block absolute bottom-4 right-4 text-[11px] text-gray-400 bg-white/80 rounded px-2 py-1 pointer-events-none whitespace-nowrap">
            휠=확대/축소 · 드래그=이동 · 노드 클릭=상세
          </p>
        </div>
        <div className="w-full md:w-80 max-h-[38vh] md:max-h-none border-t md:border-t-0 md:border-l p-4 overflow-auto shrink-0 bg-white">
          {sel && tree?.nodes[sel]
            ? <NodeDetail node={tree.nodes[sel]} />
            : <div className="text-sm text-gray-400">
                트리의 노드를 클릭해 상세를 확인하세요.<br />
                <span className="text-xs text-gray-300">한 화살표에 여러 선택지가 나열된 것은 결과가 동일해 묶인 것입니다.</span>
              </div>}
        </div>
      </div>
    </div>
  )
}
