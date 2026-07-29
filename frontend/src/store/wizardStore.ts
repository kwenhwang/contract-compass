import { create } from 'zustand'
import type {
  Step1Input, Step1Response, FinalRecommendation, RagSource, KnowledgeWebSources, OrgType,
} from '../types'

// 기관유형 기본값 — 공기업·준정부기관 (변경은 Step1의 기관유형 선택 UI)
export const DEFAULT_ORG_TYPE: OrgType = 'public_corp'

interface WizardState {
  currentStep: number
  sessionId: string
  step1Input: Partial<Step1Input>
  step1Result: Step1Response | null
  step2Conditions: Record<string, boolean>
  step2Result: FinalRecommendation | null
  step2RagSources: RagSource[]
  step2KnowledgeWeb: KnowledgeWebSources | null
  isLoading: boolean
  error: string | null

  setStep: (step: number) => void
  setStep1Input: (input: Partial<Step1Input>) => void
  setStep1Result: (result: Step1Response) => void
  setSessionId: (id: string) => void
  setStep2Conditions: (conditions: Record<string, boolean>) => void
  setStep2Result: (result: FinalRecommendation) => void
  setStep2RagSources: (sources: RagSource[]) => void
  setStep2KnowledgeWeb: (kw: KnowledgeWebSources | null) => void
  setLoading: (v: boolean) => void
  setError: (e: string | null) => void
  reset: () => void
}

export const useWizardStore = create<WizardState>((set) => ({
  currentStep: 1,
  sessionId: '',
  step1Input: { org_type: DEFAULT_ORG_TYPE },
  step1Result: null,
  step2Conditions: {},
  step2Result: null,
  step2RagSources: [],
  step2KnowledgeWeb: null,
  isLoading: false,
  error: null,

  setStep: (step) => set({ currentStep: step }),
  setStep1Input: (input) => set((s) => ({ step1Input: { ...s.step1Input, ...input } })),
  setStep1Result: (result) => set({ step1Result: result }),
  setSessionId: (id) => set({ sessionId: id }),
  setStep2Conditions: (conditions) => set({ step2Conditions: conditions }),
  setStep2Result: (result) => set({ step2Result: result }),
  setStep2RagSources: (sources) => set({ step2RagSources: sources }),
  setStep2KnowledgeWeb: (kw) => set({ step2KnowledgeWeb: kw }),
  setLoading: (v) => set({ isLoading: v }),
  setError: (e) => set({ error: e }),
  reset: () => set({
    currentStep: 1, sessionId: '', step1Input: { org_type: DEFAULT_ORG_TYPE }, step1Result: null,
    step2Conditions: {}, step2Result: null, step2RagSources: [], step2KnowledgeWeb: null, error: null,
  }),
}))
