// 디자이너(Zippt) 로딩 스피너 — sm/md/lg + optional label.
// 출처: design_preview_v2/states.jsx

export type SpinnerSize = 'sm' | 'md' | 'lg'

const SIZE_PX: Record<SpinnerSize, number> = { sm: 18, md: 28, lg: 40 }

export type SpinnerProps = {
  size?: SpinnerSize
  label?: string
}

export default function Spinner({ size = 'md', label }: SpinnerProps) {
  const px = SIZE_PX[size] ?? 28
  return (
    <span className="spin-wrap">
      <span
        className="spin"
        style={{
          width: px,
          height: px,
          borderWidth: Math.max(2, px / 9),
        }}
      />
      {label && <span className="spin-label">{label}</span>}
    </span>
  )
}
