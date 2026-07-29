import { useWizardStore } from '../store/wizardStore'
import Icon from '../components/Icon'
import type { OrgType } from '../types'

// Step4 — 결정 요약 리포트.
// wizardStore의 step1 입력 + step2 최종 결과(선택 방법·근거 룰·적용 법령·적격심사 점수 등)를
// 정리된 리포트 뷰로 렌더 + 인쇄(window.print). 신규 API 호출 없음.

const ORG_LABEL: Record<OrgType, string> = {
  national: '국가기관',
  local: '지방자치단체',
  public_corp: '공기업·준정부기관',
}

const CT_LABEL: Record<string, string> = {
  service: '용역', product: '물품', construction: '공사',
}

function priceText(price: number): string {
  if (!price) return '-'
  const eok = Math.floor(price / 100_000_000)
  const man = Math.floor((price % 100_000_000) / 10_000)
  const human = [eok ? `${eok}억` : '', man ? `${man.toLocaleString()}만` : ''].filter(Boolean).join(' ') + '원'
  return `${price.toLocaleString()}원 (${human} · 부가세 제외)`
}

const CONDITION_LABELS: Record<string, string> = {
  sme_restriction: '중소기업자간 경쟁',
  small_enterprise_restriction: '소기업·소상공인 제한',
  regional_restriction: '지역제한',
  performance_restriction: '실적제한',
  joint_contract: '공동도급',
  joint_contract_kind: '공동도급 방식',
  regional_restriction_region: '지역명',
}

export default function Step4Page() {
  const { step1Input, step2Result, setStep, reset } = useWizardStore()

  if (!step2Result) {
    return (
      <div className="op-card" style={{ textAlign: 'center', padding: 32 }}>
        <p style={{ fontSize: 'var(--text-sm)', color: 'var(--text-secondary)', marginBottom: 16 }}>
          아직 분석 결과가 없습니다. 1단계부터 진행해 주세요.
        </p>
        <button type="button" className="fl-cta" onClick={() => setStep(1)} style={{ maxWidth: 280, margin: '0 auto' }}>
          처음부터 시작
        </button>
      </div>
    )
  }

  const { method, rule_id, details, legal_basis, ai_rationale, confidence } = step2Result
  const decisionPack = step2Result.decision_pack
  const appliedConditions = step2Result.applied_conditions as Record<string, unknown> | undefined
  const bidderDecision = (details as any)?.bidder_decision || (details as any)?.bidder_selection || (method.includes('수의') ? '수의계약' : '적격심사')
  const now = new Date()

  const num: React.CSSProperties = { fontFamily: 'var(--mono)', fontVariantNumeric: 'tabular-nums' }

  const row: React.CSSProperties = {
    display: 'flex', gap: 12, padding: '8px 0',
    borderBottom: '1px dashed var(--line)', fontSize: 13, lineHeight: 1.6,
  }
  const rowLabel: React.CSSProperties = {
    flex: '0 0 140px', fontWeight: 700, color: 'var(--ink-2)',
  }
  const rowVal: React.CSSProperties = { flex: 1, color: 'var(--ink)' }

  return (
    <div className="fl-body" style={{ padding: 0 }}>
      {/* 인쇄 시 리포트 본문만 남기기 */}
      <style>{`
        @media print {
          .dt-top, .dt-rail, .dt-chat-sidebar, .report-actions, .fixed { display: none !important; }
          .report-paper { border: none !important; box-shadow: none !important; }
        }
      `}</style>

      {/* 액션 바 */}
      <div className="report-actions" style={{ display: 'flex', gap: 10, marginBottom: 14, flexWrap: 'wrap' }}>
        <button type="button" className="fl-cta" style={{ flex: 1, minWidth: 180 }} onClick={() => window.print()}>
          <Icon name="file-text" size={16} /> 리포트 인쇄 / PDF 저장
        </button>
        <button
          type="button"
          onClick={() => setStep(3)}
          style={{
            fontFamily: 'inherit', fontSize: 'var(--text-sm)', fontWeight: 700,
            color: 'var(--text-secondary)', background: 'var(--bg-secondary)',
            border: '1px solid var(--border-medium)', borderRadius: 12,
            padding: '13px 18px', cursor: 'pointer',
          }}
        >← 이전</button>
        <button
          type="button"
          onClick={reset}
          style={{
            fontFamily: 'inherit', fontSize: 'var(--text-sm)', fontWeight: 700,
            color: 'var(--text-secondary)', background: 'var(--bg-secondary)',
            border: '1px solid var(--border-medium)', borderRadius: 12,
            padding: '13px 18px', cursor: 'pointer',
          }}
        >처음부터 다시</button>
      </div>

      {/* 리포트 본문 */}
      <div className="report-paper" style={{
        background: '#fff', border: '1px solid var(--line)', borderRadius: 16,
        padding: '28px 30px', boxShadow: 'var(--sh-2)',
      }}>
        <div style={{ textAlign: 'center', marginBottom: 6 }}>
          <h2 style={{ fontSize: 20, fontWeight: 900, color: 'var(--ink)', margin: 0 }}>계약방법 결정 요약 리포트</h2>
          <p style={{ fontSize: 11, color: 'var(--ink-3)', marginTop: 4 }}>
            계약나침반 — 공공계약 방법 결정 도우미 · 출력일 {now.toLocaleDateString('ko-KR')}
          </p>
        </div>

        {/* 1. 사업 개요 */}
        <h3 style={{ fontSize: 14, fontWeight: 800, color: 'var(--ink)', margin: '18px 0 4px' }}>1. 사업 개요</h3>
        <div style={row}><span style={rowLabel}>사업명</span><span style={rowVal}>{step1Input.project_name || '(미입력)'}</span></div>
        <div style={row}><span style={rowLabel}>기관유형</span><span style={rowVal}>{step1Input.org_type ? ORG_LABEL[step1Input.org_type] : '공기업·준정부기관'}</span></div>
        <div style={row}><span style={rowLabel}>계약유형</span><span style={rowVal}>{CT_LABEL[step1Input.contract_type ?? ''] ?? step1Input.contract_type ?? '-'}</span></div>
        <div style={row}><span style={rowLabel}>추정가격</span><span style={{ ...rowVal, ...num }}>{priceText(step1Input.estimated_price || 0)}</span></div>
        {step1Input.description && (
          <div style={row}><span style={rowLabel}>사업개요</span><span style={rowVal}>{step1Input.description}</span></div>
        )}

        {/* 2. 계약방법 결정 */}
        <h3 style={{ fontSize: 14, fontWeight: 800, color: 'var(--ink)', margin: '18px 0 4px' }}>2. 계약방법 결정</h3>
        <div style={{
          margin: '8px 0 10px', padding: '14px 16px',
          background: 'var(--brand-tint)', border: '1.5px solid var(--brand)', borderRadius: 12,
        }}>
          <div style={{ fontSize: 11, fontWeight: 800, color: 'var(--brand-ink)', marginBottom: 4 }}>최종 계약방법</div>
          <div style={{ fontSize: 18, fontWeight: 900, color: 'var(--ink)' }}>{method}</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginTop: 6, fontSize: 12, color: 'var(--ink-2)' }}>
            <span>낙찰자결정 · <b>{bidderDecision}</b></span>
            {confidence ? <span>신뢰도 <b style={num}>{Math.round(confidence * 100)}%</b></span> : null}
            {rule_id && <span>근거 룰 <b style={{ fontFamily: 'var(--mono)' }}>{rule_id}</b></span>}
          </div>
        </div>
        {details?.qualification_score != null && (
          <div style={row}><span style={rowLabel}>적격심사 통과점수</span><span style={{ ...rowVal, ...num }}>{String(details.qualification_score)}점</span></div>
        )}
        {details?.lower_limit_rate != null && (
          <div style={row}><span style={rowLabel}>낙찰하한율</span><span style={{ ...rowVal, ...num }}>{String(details.lower_limit_rate)}</span></div>
        )}
        {appliedConditions && Object.keys(appliedConditions).length > 0 && (
          <div style={row}>
            <span style={rowLabel}>적용 조건</span>
            <span style={rowVal}>
              {Object.entries(appliedConditions).map(([k, v]) => {
                const label = CONDITION_LABELS[k] ?? k
                const valStr = typeof v === 'boolean' ? (v ? '적용' : '미적용') : String(v ?? '')
                return `${label}: ${valStr}`
              }).join(' · ')}
            </span>
          </div>
        )}

        {/* 3. 결정 근거 */}
        <h3 style={{ fontSize: 14, fontWeight: 800, color: 'var(--ink)', margin: '18px 0 4px' }}>3. 결정 근거</h3>
        {(ai_rationale || decisionPack?.human_explanation) && (
          <p style={{ fontSize: 13, lineHeight: 1.7, color: 'var(--ink-2)', margin: '6px 0 10px' }}>
            {(ai_rationale || decisionPack?.human_explanation || '').replace(/\*\*(.+?)\*\*/g, '$1')}
          </p>
        )}
        {legal_basis && legal_basis.length > 0 && (
          <ul style={{ margin: '0 0 10px', paddingLeft: 18, fontSize: 12.5, lineHeight: 1.7, color: 'var(--ink-2)' }}>
            {legal_basis.map((lb, i) => <li key={i}>{lb}</li>)}
          </ul>
        )}

        {/* 4. 적용 법령 조문 (decision_pack laws_applied) */}
        {decisionPack?.laws_applied && decisionPack.laws_applied.length > 0 && (
          <>
            <h3 style={{ fontSize: 14, fontWeight: 800, color: 'var(--ink)', margin: '18px 0 8px' }}>
              4. 적용 법령 조문 <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--ink-3)' }}>(룰엔진 lookup · {decisionPack._summary?.law_count ?? decisionPack.laws_applied.length}개)</span>
            </h3>
            {decisionPack.laws_applied.map((law) => (
              <div key={law.key} style={{ marginBottom: 10, padding: '10px 12px', background: 'var(--surface-2)', border: '1px solid var(--line)', borderRadius: 8 }}>
                <div style={{ fontSize: 12.5, fontWeight: 800, color: 'var(--ink)' }}>
                  {law.key} <span style={{ fontWeight: 500, color: 'var(--ink-3)' }}>— {law.law_name}</span>
                </div>
                {law.articles.map((art, i) => (
                  <div key={i} style={{ marginTop: 8, fontSize: 12, lineHeight: 1.65 }}>
                    <div style={{ fontWeight: 700, color: 'var(--ink-2)' }}>{art.title}</div>
                    <div style={{ color: 'var(--ink-2)', whiteSpace: 'pre-wrap', marginTop: 3 }}>{art.body}</div>
                  </div>
                ))}
              </div>
            ))}
          </>
        )}

        <p style={{ marginTop: 18, fontSize: 11, color: 'var(--ink-3)', lineHeight: 1.6, borderTop: '1px solid var(--line)', paddingTop: 12 }}>
          ※ 본 리포트는 결정론 룰엔진과 AI 분석의 참고 자료입니다. AI는 부정확할 수 있으므로
          중요한 결정 시 관련 법령·계약 실무 기준을 반드시 확인하세요.
        </p>
      </div>
    </div>
  )
}
