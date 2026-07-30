/**
 * contract-edge — 계약나침반 엣지 워커 (2026-07-30)
 *
 * ① 오리진 폴백 게이트: 게이트웨이급 장애(fetch 실패·502·503·504·52x)에서 HTML은
 *    안내 페이지, API는 구조화 503을 반환. 앱 레벨 500은 그대로 통과(버그를 가리지
 *    않는다). 검증용 강제 폴백: X-Edge-Fallback-Test 헤더.
 * ② GET /api/v1/law/*는 caches.default 엣지 캐시(오리진 Cache-Control 존중) —
 *    워커가 CF 캐시 룰보다 앞단이라 워커 안에서 캐시를 유지해야 P2 효과가 보존된다.
 *
 * ※ 판례 API를 엣지에서 직접 law.go.kr 호출하는 안(2026-07-30 시도)은 **불가 판명**:
 *   법제처 Open API가 OC 키에 등록된 서버 IP만 허용(응답: "정확한 서버장비의 IP...")
 *   — CF 워커 이그레스는 유동 IP라 등록 불가. 판례는 오리진(naru, IP 등록됨) 경유
 *   + 엣지 캐시로 유지한다. 관련 핸들러는 제거(git 히스토리 603425be 참조).
 * 배포: edge/wrangler.toml + `wrangler deploy`
 */

const STATUS_PAGE = "https://status.naru.build";

export default {
  async fetch(request, env, ctx) {
    const p = new URL(request.url).pathname;
    if (request.method === "GET" && p.startsWith("/api/v1/law/")) {
      // P2 캐시 보존 — 오리진 Cache-Control(200만 부여)을 존중해 워커 캐시에 저장
      return edgeCached(request, ctx, null, () => passthrough(request));
    }
    return passthrough(request);
  },
};

// ── 캐시 래퍼: caches.default, TTL은 명시값 또는 응답 Cache-Control 존중 ─────
async function edgeCached(request, ctx, ttl, produce) {
  const cache = caches.default;
  const key = new Request(new URL(request.url).toString(), { method: "GET" });
  const hit = await cache.match(key);
  if (hit) {
    const h = new Response(hit.body, hit);
    h.headers.set("x-edge-cache", "HIT");
    return h;
  }
  const resp = await produce();
  if (resp.status === 200) {
    const toStore = new Response(resp.clone().body, resp);
    if (ttl) toStore.headers.set("Cache-Control", `public, max-age=${ttl}`);
    if (toStore.headers.get("Cache-Control")?.includes("max-age")) {
      ctx.waitUntil(cache.put(key, toStore));
    }
  }
  const out = new Response(resp.body, resp);
  out.headers.set("x-edge-cache", "MISS");
  return out;
}

// ── ② 오리진 통과 + 폴백 ────────────────────────────────────────────────────
async function passthrough(request) {
  if (request.headers.get("x-edge-fallback-test")) return fallback(request, 599);
  let resp;
  try {
    resp = await fetch(request);
  } catch {
    return fallback(request, 0);
  }
  if ([502, 503, 504].includes(resp.status) || resp.status >= 520) {
    // 백엔드가 의도적으로 내는 503(캡·키 미설정)은 detail 본문이 있다 — 그대로 통과
    const ct = resp.headers.get("content-type") || "";
    if (ct.includes("json")) return resp;
    return fallback(request, resp.status);
  }
  return resp;
}

function fallback(request, code) {
  const wantsJson =
    new URL(request.url).pathname.startsWith("/api/") ||
    new URL(request.url).pathname.startsWith("/mcp") ||
    (request.headers.get("accept") || "").includes("json");
  if (wantsJson) {
    return json(
      {
        error: "origin_unavailable",
        message: "계약나침반 서버에 일시적으로 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.",
        status_page: STATUS_PAGE,
        origin_status: code,
      },
      503,
      { "Retry-After": "120", "x-edge-fallback": "1" },
    );
  }
  const html = `<!doctype html><html lang="ko"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>계약나침반 — 일시 점검 중</title>
<body style="font-family:system-ui,sans-serif;max-width:32rem;margin:15vh auto;padding:0 1rem;line-height:1.6">
<h1 style="font-size:1.4rem">🧭 계약나침반이 잠시 쉬고 있습니다</h1>
<p>서버에 일시적으로 연결할 수 없습니다. 보통 몇 분 안에 복구됩니다.</p>
<p><a href="${STATUS_PAGE}">실시간 상태 확인</a> · <a href="javascript:location.reload()">새로고침</a></p>
</body></html>`;
  return new Response(html, {
    status: 503,
    headers: { "content-type": "text/html; charset=utf-8", "Retry-After": "120", "x-edge-fallback": "1" },
  });
}

function json(obj, status = 200, headers = {}) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", ...headers },
  });
}
