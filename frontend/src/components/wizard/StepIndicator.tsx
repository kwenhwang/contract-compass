const STEPS = ['기본정보 입력', 'AI 1차 분석', '최종 추천 확인', '양식 작성']

interface Props {
  current: number
  onStepClick?: (step: number) => void
}

export default function StepIndicator({ current, onStepClick }: Props) {
  return (
    <div className="flex items-center justify-center mb-8">
      {STEPS.map((label, i) => {
        const step = i + 1
        const done = step < current
        const active = step === current
        const clickable = done && !!onStepClick

        return (
          <div key={step} className="flex items-center">
            <div className="flex flex-col items-center">
              <button
                type="button"
                onClick={() => clickable && onStepClick(step)}
                disabled={!clickable}
                title={clickable ? `${label}로 돌아가기` : undefined}
                className={`w-9 h-9 rounded-full flex items-center justify-center text-sm font-bold transition-all
                  ${done
                    ? clickable
                      ? 'bg-blue-600 text-white hover:bg-blue-700 hover:ring-4 hover:ring-blue-100 cursor-pointer'
                      : 'bg-blue-600 text-white cursor-default'
                    : active
                      ? 'bg-blue-600 text-white ring-4 ring-blue-100 cursor-default'
                      : 'bg-gray-200 text-gray-500 cursor-default'
                  }`}
              >
                {done ? '✓' : step}
              </button>
              <span className={`mt-1 text-xs whitespace-nowrap transition-colors
                ${active ? 'text-blue-600 font-semibold' : done && clickable ? 'text-blue-500 hover:underline cursor-pointer' : 'text-gray-400'}`}
                onClick={() => clickable && onStepClick(step)}
              >
                {label}
              </span>
            </div>
            {i < STEPS.length - 1 && (
              <div className={`h-0.5 w-16 mx-1 mb-4 ${done ? 'bg-blue-600' : 'bg-gray-200'}`} />
            )}
          </div>
        )
      })}
    </div>
  )
}
