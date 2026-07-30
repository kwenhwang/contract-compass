/**
 * sallim-mail-in — sallim.app 수신 메일을 ops 수신함으로 중계 (2026-07-30)
 *
 * CF Email Routing(custom address, 예: contract@sallim.app)이 이 워커로 메일을
 * 넘기면, naru.build 패턴(2026-07-22)과 동일하게 studio-ops `/mail-in`에
 * Bearer(MAIL_RELAY_TOKEN) POST한다 → /data/ops/mail/ 저장 + 보드·텔레그램 알림.
 *
 * MIME 파싱은 하지 않는다(워커 경량 유지) — raw 앞부분을 text로 실어 보내고,
 * 사람이 읽을 핵심(발신자·제목)은 헤더에서 뽑는다. 첨부·HTML은 raw 안에 있음.
 * 배포: wrangler-mail-sallim.toml (secret: MAIL_RELAY_TOKEN)
 */

const INGEST = "https://api.naru.build/ops/mail-in";
const MAX_RAW = 100 * 1024; // 본문 전달 상한 100KB — ops 수신함은 요지 확인용

export default {
  async email(message, env, ctx) {
    let text = "";
    try {
      const reader = message.raw.getReader();
      const chunks = [];
      let total = 0;
      while (total < MAX_RAW) {
        const { done, value } = await reader.read();
        if (done) break;
        chunks.push(value);
        total += value.length;
      }
      text = new TextDecoder("utf-8", { fatal: false })
        .decode(concat(chunks))
        .slice(0, MAX_RAW);
    } catch (e) {
      text = `(raw 읽기 실패: ${e})`;
    }
    const payload = {
      from: message.from || "unknown",
      subject: message.headers.get("subject") || "(제목 없음)",
      date: message.headers.get("date") || new Date().toISOString(),
      text: `[to: ${message.to}]\n${text}`,
    };
    const resp = await fetch(INGEST, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: `Bearer ${env.MAIL_RELAY_TOKEN}`,
      },
      body: JSON.stringify(payload),
    });
    if (!resp.ok) {
      // 인제스트 실패 시 반송하지 않고 로그만 — 발신자에겐 정상 수신으로 보인다
      console.log(`mail-in ingest failed: ${resp.status}`);
    }
  },
};

function concat(chunks) {
  const total = chunks.reduce((n, c) => n + c.length, 0);
  const out = new Uint8Array(total);
  let o = 0;
  for (const c of chunks) { out.set(c, o); o += c.length; }
  return out;
}
