# 계약나침반 🧭

**공공계약 방법 결정 도우미** — 국가계약법·지방계약법 등 공공계약 법령을 기반으로,
발주하려는 계약(공사·용역·물품)에 적용 가능한 계약방법(입찰·수의계약·제한경쟁 등)과
법령 근거를 결정론적으로 안내하는 오픈소스 웹서비스입니다.

- **결정 위저드**: 계약유형·추정가격·조건을 입력하면 룰엔진(법령 직접 인코딩 94룰)이
  적용 가능한 계약방법·낙찰자 결정방법·적격심사 기준을 후보와 근거 조문과 함께 제시
- **법령 챗봇(Ask)**: 국가계약법령·계약예규·감사원 공공계약 실무가이드 등 공개 코퍼스
  기반 RAG 검색 + 인용 답변
- **기관유형 지원**: 국가기관 / 지방자치단체 / 공기업·준정부기관 프로파일
- **결정론 우선**: 계약방법 결정은 LLM이 아닌 룰엔진이 수행 — 같은 입력엔 항상 같은
  결과. LLM은 설명 생성·챗봇에만 사용(키 없이도 핵심 기능 동작)

> ⚠️ **면책**: 이 서비스는 정보 제공 목적이며 법적 자문·유권해석이 아닙니다.
> 적격심사 통과점수·낙찰하한율·각종 한도는 발주기관별 세부기준과 법령 개정에 따라
> 다를 수 있으므로, 실제 발주 전 반드시 소속 기관 계약 부서와 현행 법령을 확인하세요.

## 구성

```
backend/    FastAPI — 룰엔진·RAG·LLM 연동 (frontend/dist 정적 서빙 포함)
frontend/   React + TypeScript (Vite) — 위저드 UI
rules/      계약 룰셋 JSON (contract_rules·law_registry 등) ← 결정론 핵심
tools/      법령·예규 수집/인덱싱 파이프라인 (law.go.kr Open API)
etl/        PDF/DOCX → 청크 → ChromaDB 파이프라인
tests/      단위·회귀 테스트
```

## 빠른 시작

```bash
# 1) 백엔드 의존성
pip install -r backend/requirements.txt

# 2) 프론트 빌드 (Node.js는 빌드 때만 필요)
cd frontend && npm install && npm run build && cd ..

# 3) 환경설정 (선택 — LLM 키 없이도 동작)
cp .env.example .env

# 4) 실행 (rate limiter가 인메모리라 workers=1 필수)
python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 8200
```

## RAG 코퍼스 구축 (선택)

법령 챗봇·근거 검색을 쓰려면 공개 코퍼스를 인덱싱합니다:

```bash
# 법령 XML을 tools/laws/ 에 준비(law.go.kr Open API — tools/lib/lawgo.py 헬퍼 참조) 후 인덱싱
python3 tools/index_laws.py            # → law_articles 컬렉션

# 계약예규(행정규칙) 수집·인덱싱
python3 tools/fetch_admin_rules.py && python3 tools/parse_admin_rules.py
python3 tools/index_admin_rules.py     # → admin_rules 컬렉션

# 공개 간행물(감사원 공공계약 실무가이드 등)을 data/source_docs/ 에 넣고
python3 tools/reindex_qa.py            # → public_guides 컬렉션

# BM25 하이브리드 인덱스
python3 tools/build_bm25_index.py
```

코퍼스가 없으면 위저드(룰엔진)는 정상 동작하고, RAG 검색 결과만 비어 있습니다.

## 데이터 출처 (전부 공개 자료)

- 법령·시행령·시행규칙: [국가법령정보센터](https://law.go.kr) Open API
- 계약예규(적격심사기준·공동계약운영요령 등): 기획재정부 행정규칙
- 공공계약 실무가이드: 감사원 공개 간행물
- 물품분류·중소기업자간 경쟁제품: 조달청·중소벤처기업부 고시

## 라이선스

MIT
