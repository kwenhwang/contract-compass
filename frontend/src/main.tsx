import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'
// 디자이너(Zippt AI) 디자인 시스템 — 토큰·페이지·Drawer 스타일
import './styles/designer/ds-tokens.css'
import './styles/designer/app.css'
import './styles/designer/source-drawer.css'
import './styles/designer/ask.css'
import './styles/designer/flow.css'
import './styles/designer/flow-responsive.css'
import './styles/designer/states.css'
import './styles/designer/glossary.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)
