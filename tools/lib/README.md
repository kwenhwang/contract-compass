# tools/lib — 스크립트 공통 헬퍼

법령 취득·감시 스크립트가 **중복 정의하던 코드를 한 곳**으로 모은 곳.
새 수집 스크립트를 만들거나 기존 것을 고칠 때 **여기부터 재사용**한다(복붙 금지).

---

## 모듈

### `lawgo.py`

law.go.kr(국가법령정보센터) 오픈API 헬퍼 + `tools/laws/` 법령 스냅샷 매니페스트.

| 심볼 | 시그니처 | 용도 |
|---|---|---|
| `LAW_MANIFEST` | `tuple[LawSpec, ...]` | **어떤 법령이 어떤 파일명으로 있어야 하는가의 단일 출처.** 취득·감시가 같은 표를 본다. |
| `MANUAL_FILES` | `frozenset[str]` | 수동 반입 파일(예규 전문 hwpx) — 감시 제외 |
| `load_api_key` | `load_api_key(env_var="LAW_API_KEY") -> str` | 환경변수 우선, 없으면 repo 루트 `.env`. 둘 다 없으면 `SystemExit`. |
| `load_oc` | `load_oc() -> str` | OC 키(`LAW_API_KEY`) 로딩 = `load_api_key("LAW_API_KEY")` |
| `search` | `search(query, oc, target="law"\|"admrul", display, timeout) -> list[LawEntry]` | `lawSearch.do` 결과 정규화 |
| `find_exact` | `find_exact(name, oc, target, timeout) -> LawEntry \| None` | **법령명 정확 일치만.** 없으면 None |
| `fetch_xml` | `fetch_xml(mst, oc, target, timeout) -> bytes` | `lawService.do` 원문. 인증실패·빈응답은 `RuntimeError` |
| `read_local_meta` | `read_local_meta(path) -> LawEntry \| None` | 로컬 XML 헤더(법령명·시행일자·공포번호) |
| `norm_name` / `norm_no` | `(str) -> str` | 중점(·/ㆍ)·공백 정규화 / 공포번호 `제00028호`→`28` |

> **`find_exact`에 폴백이 없는 이유** — 과거 취득 스크립트들이 "정확 일치 실패 시
> 검색 첫 결과 사용" 폴백을 갖고 있었다. `"물품관리법"` 질의의 law.go.kr 1순위가
> 「공유재산 및 물품 관리법」이라 무관 법령 수백 청크가 RAG 코퍼스에 들어간 채
> '성공'으로 보고된 사고가 있었다. 이 함수는 못 찾으면 못 찾았다고 한다.

MST는 개정마다 바뀌는 연혁 식별자라 매니페스트에 고정하지 않는다 — 오취득 방어는
정확 일치가 담당한다.

---

## 새 fetch 스크립트가 지켜야 할 규약

1. **import 부트스트랩** — `tools` 는 namespace 패키지라 스크립트를 직접 실행
   (`python3 tools/foo.py`)하면 repo 루트가 `sys.path`에 없다. 상수 정의 직전에 추가:
   ```python
   ROOT = Path(__file__).resolve().parents[1]
   sys.path.insert(0, str(ROOT))
   from tools.lib.lawgo import find_exact, fetch_xml, load_api_key  # noqa: E402
   ```
2. **`__init__.py` 추가 금지** — `tools/`·`tools/lib/`에 두면 namespace 해석이 깨져
   `backend` 등의 기존 import가 실패한다.
3. **에러정책·반환형은 스크립트 고유로** — HTTP 재시도 루프, 에러 처리(`raise SystemExit`
   vs `return None`), timeout은 서비스마다 달라 공통화하지 않는다.

---

## 현재 사용처

`lawgo` 적용: `check_law_freshness.py`(스냅샷·레지스트리 신선도 감시),
`build_law_registry.py`(레지스트리 재생성).

회귀 보호: `tests/unit/test_lawgo_lib.py`(네트워크 불필요 순수 단위테스트),
`tests/unit/test_law_registry_integrity.py`.
