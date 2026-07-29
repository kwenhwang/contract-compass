/** @type {import('tailwindcss').Config} */
// 디자인 토큰(docs/design system.css)으로 Tailwind 색 팔레트 리맵 —
// 기존 text-gray-*/bg-blue-* 등 클래스가 한 번에 토큰 색으로 렌더된다.
// 규칙: 액션·강조(blue계열)=브랜드 오렌지 / 회색=warm slate / 의미색=high·med·safe·violet.
const warm = { // gray/slate/zinc/neutral → warm slate
  50: '#FAF8F4', 100: '#F3EFE8', 200: '#E8E1D6', 300: '#DCD3C5',
  400: '#B7AE9F', 500: '#938A7C', 600: '#5B544A', 700: '#5B544A',
  800: '#1D1813', 900: '#1D1813', 950: '#1D1813',
}
const brand = { // blue/sky/indigo → 브랜드 오렌지(단일 액션색)
  50: '#FDEEE2', 100: '#FBE0CD', 200: '#FBE0CD', 300: '#F26B1E',
  400: '#F26B1E', 500: '#F26B1E', 600: '#E8590C', 700: '#C2410C',
  800: '#9A3A06', 900: '#9A3A06', 950: '#9A3A06',
}
const safe = { 50: '#E2F2EA', 100: '#E2F2EA', 200: '#C9E6D6', 500: '#1C8A5A', 600: '#1C8A5A', 700: '#136241', 800: '#136241', 900: '#136241' }
const high = { 50: '#FBE8E5', 100: '#FBE8E5', 200: '#F6CFC9', 500: '#D43A2C', 600: '#D43A2C', 700: '#9F2017', 800: '#9F2017', 900: '#9F2017' }
const med = { 50: '#FAF0D9', 100: '#FAF0D9', 200: '#EFDBA8', 500: '#C07A00', 600: '#C07A00', 700: '#7E5200', 800: '#7E5200', 900: '#7E5200' }
const violet = { 50: '#EFE9FB', 100: '#EFE9FB', 200: '#DCCFF4', 500: '#6E48C9', 600: '#6E48C9', 700: '#4A2D94', 800: '#4A2D94', 900: '#4A2D94' }

export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        gray: warm, slate: warm, zinc: warm, neutral: warm, stone: warm,
        blue: brand, sky: brand, indigo: brand, cyan: brand,
        green: safe, emerald: safe, teal: safe, lime: safe,
        red: high, rose: high,
        amber: med, yellow: med, orange: brand,
        violet: violet, purple: violet, fuchsia: violet,
        brand: { DEFAULT: '#E8590C', ink: '#9A3A06', tint: '#FDEEE2' },
      },
      fontFamily: {
        sans: ['Pretendard', '-apple-system', 'BlinkMacSystemFont', 'Noto Sans KR', 'sans-serif'],
        mono: ['IBM Plex Mono', 'ui-monospace', 'monospace'],
      },
    },
  },
  plugins: [],
}
