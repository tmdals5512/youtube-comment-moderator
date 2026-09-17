/* Outlier 관리자 화면.
 *
 * 빌드 도구를 쓰지 않는다. node 를 안 깔아도 되고, 파일을 저장하면 새로고침만
 * 하면 된다. FastAPI 가 같은 서버에서 내보내므로 CORS 도 안 탄다.
 *
 * 화면 6개를 해시 라우팅으로 전환한다:
 * 대시보드 / 채널 관리 / 검토 큐 / 숨김 목록 / 관리 기준 / 처리 이력.
 */

// 화면이 이유 없이 비어 보이는 게 제일 나쁘다. 잡히지 않은 오류는
// 콘솔이 아니라 화면에 띄운다 — F12 를 열지 않아도 원인을 알 수 있게.
window.addEventListener("error", (e) => showFatal(e.message));
window.addEventListener("unhandledrejection", (e) => showFatal(e.reason));

function showFatal(msg) {
  const el = document.getElementById("view");
  if (!el) return;
  el.innerHTML =
    '<div class="card"><div class="empty">화면을 그리다 멈췄습니다.<br><br>' +
    '<code style="font-size:12px;color:#dc2626">' +
    String(msg).replace(/</g, "&lt;").slice(0, 300) +
    "</code></div></div>";
}

const API = "/api";
const $ = (s, r = document) => r.querySelector(s);
const view = $("#view");

// ── 공용 ──────────────────────────────────────────────

async function api(path, opts) {
  const r = await fetch(API + path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (r.status === 401) {
    // 자동으로 넘기지 않는다. 로그인이 실패하면 튕겨 나왔다가 다시 보내지는
    // 무한 왕복이 되고, 그러면 원인이 뭔지 볼 기회가 없다.
    showLogin();
    const e = new Error("로그인이 필요합니다");
    e.unauthorized = true;   // route() 가 오류 화면으로 덮지 않게 표시해둔다
    throw e;
  }
  if (!r.ok) {
    const body = await r.text();
    throw new Error(`${r.status} ${body.slice(0, 200)}`);
  }
  return r.status === 204 ? null : r.json();
}

// 구글에서 돌아오다 실패하면 /app?login_error=... 로 온다. 왜 안 됐는지를
// 여기서 말해준다 — 전에는 400 JSON 이 브라우저에 그대로 떴다.
const 로그인오류 = {
  cancelled: "로그인을 취소하셨습니다.",
  access_denied:
    "구글에서 거부됐습니다. 미검증 앱이라 '테스트 사용자'로 등록된 계정만 됩니다.",
  state:
    "로그인 절차가 중간에 끊겼습니다 (서버가 재시작됐거나 너무 오래 걸렸습니다). " +
    "다시 시도해주세요.",
  password_account:
    "이 이메일은 비밀번호로 가입한 계정입니다. 첫 화면에서 이메일과 비밀번호로 로그인하세요.",
};

function showLogin() {
  stopRefresh();
  const code = new URLSearchParams(location.search).get("login_error");
  const 오류 = code
    ? `<div class="note" style="margin:0 auto 18px;max-width:420px;text-align:left;
                border-color:var(--critical)">${esc(로그인오류[code] || code)}</div>`
    : "";
  view.innerHTML = `
    <div class="empty" style="padding:60px 20px;text-align:center">
      <div style="font-size:16px;font-weight:600;color:var(--text);margin-bottom:8px">
        로그인이 필요합니다</div>
      ${오류}
      <div style="margin-bottom:8px">채널 데이터는 로그인한 사람에게만 보입니다.</div>
      <div style="margin-bottom:20px;font-size:12.5px;color:var(--muted)">
        이메일로 가입한 계정이나 Google 계정으로 들어옵니다.<br>
        유튜브 채널 연결은 로그인 뒤 채널 주인 계정으로 따로 합니다.</div>
      <div style="display:flex;gap:8px;justify-content:center;flex-wrap:wrap">
        <button id="login-email" style="flex:0 0 auto;padding:10px 24px">이메일로 로그인</button>
        <button id="login" class="slim" style="flex:0 0 auto;padding:10px 24px">Google로 계속하기</button>
      </div>
    </div>`;
  $("#login").onclick = () => {
    // login_error 는 다시 들고 가지 않는다. 성공하고 돌아왔는데 옛 오류가
    // 남아 있으면 헷갈린다.
    location.href =
      "/api/auth/start?next=" +
      encodeURIComponent(location.pathname + location.hash);
  };
  // 이메일 로그인 폼은 첫 화면에 있다.
  $("#login-email").onclick = () => (location.href = "/");
}

/** 화면에 넣기 전에 반드시 통과시킨다. 댓글 본문은 남이 쓴 글이라 그대로
 *  넣으면 태그가 실행된다. */
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

const num = (n) => (n ?? 0).toLocaleString("ko-KR");

/** '3분 전' 처럼. 관리자는 절대시각보다 얼마나 됐는지를 먼저 본다. */
function ago(iso) {
  if (!iso) return "";
  const m = Math.floor((Date.now() - new Date(iso + "Z").getTime()) / 60000);
  if (m < 1) return "방금";
  if (m < 60) return `${m}분 전`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}시간 전`;
  const d = Math.floor(h / 24);
  return d < 30 ? `${d}일 전` : new Date(iso + "Z").toLocaleDateString("ko-KR");
}

let toastTimer;
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2200);
}

const SEV_KO = { critical: "긴급", high: "높음", medium: "보통", low: "낮음" };
const sevBadge = (s) => `<span class="sev ${s}">${SEV_KO[s] || s}</span>`;

function loading() {
  view.innerHTML = `<div class="empty">불러오는 중…</div>`;
}
function failed(e) {
  view.innerHTML = `<div class="card"><div class="empty">불러오지 못했습니다.<br><br>
    <code style="font-size:12px">${esc(e.message)}</code></div></div>`;
}

// ── 채널 ──────────────────────────────────────────────

let channels = [];
const channelId = () => Number($("#channel").value || 0);
const channelName = () =>
  channels.find((c) => c.id === channelId())?.channel_title || "";

async function loadChannels() {
  channels = await api("/channels");
  const saved = Number(localStorage.getItem("channel") || 0);
  const pick = channels.some((c) => c.id === saved) ? saved : channels[0]?.id;
  $("#channel").innerHTML = channels
    .map((c) => `<option value="${c.id}">${esc(c.channel_title)}</option>`)
    .join("");
  if (pick) $("#channel").value = pick;
}

/** 지금 고른 채널이 유튜브 권한 없이 돌고 있으면 그렇다고 말해준다.
 *
 *  수집·판별은 API 키만으로도 되지만, 숨김을 실제로 반영하려면 채널
 *  주인이 OAuth 로 권한을 줘야 한다. 이 구분이 화면에 없으면, 숨김을
 *  눌러 놓고 유튜브에 반영된 줄 알게 된다 — 가장 나쁜 종류의 오해다.
 */
function 연동배너() {
  const 배너 = $("#banner");
  if (!배너) return;
  const c = channels.find((x) => x.id === channelId());
  if (!c || c.connected || location.hash.split("?")[0] === "#/channels") {
    배너.innerHTML = "";
    return;
  }
  배너.innerHTML = `
    <div class="note" style="margin:0 0 4px;border-color:var(--critical)">
      <b>${esc(c.channel_title)}</b> 는 유튜브 권한이 없습니다.
      댓글 수집과 판별은 되지만, <b>숨김을 눌러도 유튜브에는 반영되지 않고
      우리 기록에만 남습니다.</b>
      <a href="#/channels" style="margin-left:6px">채널 관리에서 연동하기</a>
    </div>`;
}

// ── 대시보드 ──────────────────────────────────────────

/** 관리자가 실제로 처리한 건수. 세는 값만 보여준다.
 *
 *  '건당 N초' '전부 보면 N시간' 을 여기 띄웠었다. 개발 중 클릭 간격을 잰
 *  거라 관리자 판단 시간이 아니었는데 '실측' 이라고 써 있었다. 뺐다.
 */
function 일한양(s) {
  const w = s.workload;
  if (!w || !w.reviewed) return "";
  return `
    <div class="card" style="margin-bottom:16px">
      <h2>처리 현황</h2>
      <div style="display:flex;gap:28px;flex-wrap:wrap;margin-top:10px">
        <div>
          <div style="font-size:12px;color:var(--muted)">직접 처리한 댓글</div>
          <div style="font-size:20px;font-weight:700">${num(w.reviewed)}건
            <span style="font-size:12px;font-weight:400;color:var(--muted)">
              전체의 ${(w.seen_ratio * 100).toFixed(2)}%</span></div>
        </div>
        <div>
          <div style="font-size:12px;color:var(--muted)">검토 대기</div>
          <div style="font-size:20px;font-weight:700">${num(s.unreviewed)}건</div>
        </div>
      </div>
    </div>`;
}

/** 자동 감시가 살아 있는지. JSON 을 읽게 하지 않는다.
 *
 *  /api/health/watch 를 브라우저로 열면 {"errors": []} 가 보이는데, 그걸
 *  오류로 읽은 사람이 실제로 있었다. 관리자가 봐야 하는 건 세 가지다 —
 *  돌고 있나, 마지막에 언제 돌았나, 오늘 돈을 얼마나 썼나.
 */
function 감시상태(w) {
  if (!w) return "";
  if (!w.enabled)
    return `<div class="note" style="margin-bottom:16px;border-color:var(--critical)">
      자동 감시가 꺼져 있습니다 (WATCH_ENABLED=false). 새 댓글이 들어오지 않습니다.</div>`;

  const r = w.last_result;
  const 언제 = w.last_run_at ? ago(w.last_run_at) : "아직 안 돌았습니다 (서버 시작 90초 뒤 첫 실행)";
  const 오류 = r?.errors?.length
    ? `<span style="color:var(--critical)"> · 오류 ${r.errors.length}건: ${esc(r.errors[0])}</span>`
    : "";
  const 건너뜀 = r?.skipped?.length
    ? `<span style="color:var(--critical)"> · ${esc(r.skipped[0])}</span>`
    : "";
  return `
    <div class="card" style="margin-bottom:16px">
      <h2>자동 감시 <span style="font-size:12px;font-weight:500;color:var(--ok)">● 켜짐</span></h2>
      <div style="display:flex;gap:28px;flex-wrap:wrap;margin-top:10px;font-size:13px">
        <div><div style="font-size:12px;color:var(--muted)">마지막 실행</div>
          <div style="font-weight:600">${esc(언제)}</div></div>
        ${r ? `
        <div><div style="font-size:12px;color:var(--muted)">그때 한 일</div>
          <div style="font-weight:600">채널 ${num(r.channels)}개 · 새 댓글 ${num(r.collected)}건 · 판별 ${num(r.judged)}건</div></div>` : ""}
        <div><div style="font-size:12px;color:var(--muted)">오늘 AI 호출</div>
          <div style="font-weight:600">${num(w.llm_calls_today)} / ${num(w.llm_daily_cap)}건</div></div>
        <div><div style="font-size:12px;color:var(--muted)">주기</div>
          <div style="font-weight:600">${Math.round(w.interval_seconds / 60)}분마다 · 채널당 최신 영상 ${w.videos_per_channel}개</div></div>
      </div>
      <div class="note" style="margin-top:12px">연동하고 AI 판별에 동의한 채널만 봅니다.
        새 댓글이 없으면 판별도 없고 비용도 없습니다.${오류}${건너뜀}</div>
    </div>`;
}

async function viewDashboard() {
  loading();
  // localStorage 값은 사용자가 고친 적이 있을 수도 있고, 예전 버전이 남긴
  // 값일 수도 있다. 그대로 API 에 넘기면 422 가 나서 화면이 통째로 깨진다.
  const PERIODS = ["today", "7d", "30d", "all"];
  const saved = localStorage.getItem("period");
  const period = PERIODS.includes(saved) ? saved : "all";
  const [s, w] = await Promise.all([
    api(`/channels/${channelId()}/stats?period=${period}`),
    api("/health/watch").catch(() => null),
  ]);
  const judged = s.total - s.pending;
  const pct = (n) => (judged ? (n / judged) * 100 : 0);

  const cats = Object.entries(s.by_category).filter(([k]) => k && k !== "정상");
  const top = Math.max(1, ...cats.map(([, v]) => v));

  view.innerHTML = `
    <h1>대시보드</h1>
    <div class="sub">${esc(channelName())} · 수집 ${num(s.total)}건</div>

    <div style="margin-bottom:14px">
      <select class="mini" id="period">
        ${[["today", "오늘"], ["7d", "최근 7일"], ["30d", "최근 30일"], ["all", "전체"]]
          .map(([v, l]) => `<option value="${v}"${v === period ? " selected" : ""}>${l}</option>`)
          .join("")}
      </select>
    </div>

    <div class="grid g4" style="margin-bottom:16px">
      <div class="card stat"><div class="l">검토 대기</div>
        <div class="n" style="color:var(--critical)">${num(s.unreviewed)}</div>
        <div class="h">관리자 확인 필요</div></div>
      <div class="card stat"><div class="l">숨김</div>
        <div class="n">${num(s.hidden)}</div>
        <div class="h">자동 + 관리자 조치</div></div>
      <div class="card stat"><div class="l">통과</div>
        <div class="n">${num(s.passed)}</div>
        <div class="h">그대로 공개</div></div>
      <div class="card stat"><div class="l">검토 전환율</div>
        <div class="n">${(s.review_rate * 100).toFixed(1)}<span style="font-size:16px">%</span></div>
        <div class="h">판별한 것 중 큐로 온 비율 · 목표 30% 이하</div></div>
    </div>

    ${감시상태(w)}
    ${일한양(s)}

    <div class="split">
      <div class="card">
        <h2>판별 결과 분포</h2>
        <div class="bar">
          <i style="width:${pct(s.hidden)}%;background:var(--critical)"></i>
          <i style="width:${pct(s.queued)}%;background:var(--medium)"></i>
          <i style="width:${pct(s.passed)}%;background:var(--ok)"></i>
        </div>
        <div style="display:flex;gap:16px;margin-top:10px;font-size:12.5px;color:var(--muted)">
          <span><b style="color:var(--critical)">■</b> 숨김 ${num(s.hidden)}</span>
          <span><b style="color:var(--medium)">■</b> 검토 큐 ${num(s.queued)}</span>
          <span><b style="color:var(--ok)">■</b> 통과 ${num(s.passed)}</span>
        </div>
        ${s.pending
          ? `<div class="note" style="margin-top:14px">아직 판별하지 않은 댓글이 ${num(s.pending)}건 있습니다.
             연동·동의된 채널이면 서버가 매시간 자동으로 판별합니다. 지금 바로 하려면
             <code>python -m scripts.run_pipeline --channel ${channelId()} --save</code></div>`
          : ""}
      </div>

      <div class="card">
        <h2>유해 카테고리</h2>
        ${cats.length
          ? cats
              .map(
                ([k, v]) => `
          <div style="display:flex;align-items:center;gap:10px;margin-bottom:7px">
            <div style="width:58px;font-size:12.5px;font-weight:600">${esc(k)}</div>
            <div style="flex:1;height:8px;background:var(--low-bg);border-radius:4px;overflow:hidden">
              <i style="display:block;height:100%;width:${(v / top) * 100}%;background:var(--accent)"></i>
            </div>
            <div style="width:44px;text-align:right;font-size:12.5px;color:var(--muted)">${num(v)}</div>
          </div>`
              )
              .join("")
          : `<div class="empty">아직 없습니다.</div>`}
      </div>
    </div>`;

  $("#period").onchange = (e) => {
    localStorage.setItem("period", e.target.value);
    viewDashboard();
  };
}

// ── 검토 큐 / 숨김 목록 (같은 구조) ───────────────────

function renderItem(x, sel) {
  const when = x.published_at ? ago(x.published_at) : "";
  return `
    <div class="item${x.comment_id === sel ? " on" : ""}" data-id="${x.comment_id}">
      <div class="meta">
        ${sevBadge(x.severity)}
        ${x.category ? `<span class="tag">${esc(x.category)}</span>` : ""}
        ${x.rule_value ? `<span class="tag rule">등록어 ${esc(x.rule_value)}</span>` : ""}
        <small>${when}</small>
        <small>♥ ${num(x.like_count)}</small>
        ${x.total_reply_count ? `<small>답글 ${num(x.total_reply_count)}</small>` : ""}
      </div>
      <div class="body">${esc(x.content).slice(0, 220)}</div>
    </div>`;
}

async function renderDetail(x) {
  const box = $("#detail");
  if (!x) {
    box.innerHTML = `<div class="card"><div class="empty">왼쪽에서 댓글을 고르세요.</div></div>`;
    return;
  }
  box.innerHTML = `
    <div class="card detail">
      <h2>${esc(x.video_title || x.video_id || "")}</h2>
      ${x.parent_content
        ? `<div style="font-size:12px;color:var(--muted);margin-bottom:8px">
             ↳ 부모 댓글: ${esc(x.parent_content).slice(0, 120)}</div>`
        : ""}
      <div class="quote">${esc(x.content)}</div>

      <div class="facts">
        <div class="fact"><div class="l">위험도</div>
          <div class="v" style="color:var(--${x.severity})">${SEV_KO[x.severity]}</div></div>
        <div class="fact"><div class="l">분류</div>
          <div class="v">${esc(x.category || "-")}</div></div>
        <div class="fact"><div class="l">확산도</div>
          <div class="v">♥ ${num(x.like_count)}</div></div>
      </div>

      <div class="section">
        <div class="t">판단 근거</div>
        <div class="reason">
          <span class="tag ${x.decided_by === "rule" ? "rule" : "ai"}">${
            x.decided_by === "rule" ? "관리자 등록어" : "AI 판별"
          }</span>
          ${esc(x.reason || "근거 없음")}
        </div>
      </div>

      <div class="section">
        <div class="t">과거 유사 사례
          <span style="font-weight:500;color:var(--muted);margin-left:6px">
            앞의 숫자는 뜻이 얼마나 비슷한지 (1에 가까울수록 같은 말)</span>
        </div>
        <div id="similar"><div class="empty" style="padding:14px">찾는 중…</div></div>
      </div>

      <div class="acts">
        ${/* 이미 가려진 댓글에 [숨김 처리] 를 또 보여주면, 눌렀을 때 아무
              일도 안 일어나는 버튼이 된다. 할 수 있는 것만 남긴다. */ ""}
        ${x.status === "hidden"
          ? ""
          : `<button class="danger" data-act="hide">숨김 처리</button>`}
        <button data-act="ban_author">채널 차단</button>
        <button class="good" data-act="keep">${
          x.status === "hidden" ? "큐로 되돌리기" : "유지(정상)"
        }</button>
      </div>
    </div>`;

  box.querySelectorAll("[data-act]").forEach((b) => {
    b.onclick = () => act(x.comment_id, b.dataset.act);
  });

  // 유사 사례는 늦게 와도 되므로 본문을 먼저 그리고 나서 채운다.
  try {
    const sims = await api(`/comments/${x.comment_id}/similar?limit=3`);
    $("#similar").innerHTML = sims.length
      ? sims
          .map(
            (s) => `<div class="sim">
              <div class="top">
                <span><b>${s.similarity.toFixed(2)}</b> ${esc(s.content).slice(0, 60)}</span>
                <span class="tag ${s.action === "hide" ? "hide" : s.action === "keep" ? "keep" : ""}">${
                  s.reviewed
                    ? `${{ hide: "숨김", keep: "유지", ban_author: "차단" }[s.action] || s.action}${
                        s.actor ? " · " + esc(s.actor) : ""
                      }`
                    : { hidden: "숨김", queued: "검토 대기", passed: "통과" }[s.status] || s.status
                }</span>
              </div>
              ${s.note ? `<div style="color:var(--muted);margin-top:4px">${esc(s.note)}</div>` : ""}
            </div>`
          )
          .join("")
      : `<div class="empty" style="padding:14px">비슷한 댓글이 없습니다.</div>`;
  } catch {
    $("#similar").innerHTML = `<div class="empty" style="padding:14px">불러오지 못했습니다.</div>`;
  }
}

let rows = [];
let selected = null;

async function act(id, action) {
  // 조치자는 보내지 않는다. 서버가 로그인 세션에서 읽는다 — 화면이 보낸
  // 이름을 믿으면 남의 이름으로 기록을 남길 수 있다.
  const r = await api(`/comments/${id}/action`, {
    method: "POST",
    body: JSON.stringify({ action }),
  });
  // 유튜브에 실제로 반영됐는지는 서버가 읽어서 정한다. 특히 가린 댓글의 되돌리기는
  // 유튜브 API 가 받아만 주고 안 풀어준다 — 그걸 "유지했습니다" 로 덮으면 관리자는
  // 공개된 줄 안다. 반영 안 됐으면 서버가 남긴 이유를 그대로 보여준다.
  const 돼야하는데_안됨 = r.youtube_synced === false && r.note && /유튜브/.test(r.note);
  toast(
    돼야하는데_안됨
      ? r.note.replace(/^\[|\]$/g, "")
      : { hide: "숨김 처리했습니다", keep: "유지했습니다", ban_author: "채널 차단했습니다" }[action]
  );
  // 처리한 건은 목록에서 빠진다. 다음 건으로 자동으로 넘어가야 손이 안 멈춘다.
  const i = rows.findIndex((r) => r.comment_id === id);
  rows.splice(i, 1);
  // 전체 건수도 하나 줄인다. 안 줄이면 "4건 중 3건 표시" 가 되어 있지도 않은
  // '1건 더 불러오기' 버튼이 다음 자동 갱신(20초)까지 떠 있다 — 리허설에서 봤다.
  total = Math.max(total - 1, 0);
  selected = rows[Math.min(i, rows.length - 1)] || null;
  paint();
  refreshBadge();
}

// 한 번에 받아오는 건수. API 상한(200)과 같다.
const PAGE = 200;

// 지금 보고 있는 목록의 종류. '더 불러오기'가 같은 목록을 이어받아야 해서
// viewList 가 정해둔 값을 loadMore 와 자동갱신이 함께 쓴다.
let listKind = "queue";
const listPath = () =>
  `/channels/${channelId()}/${listKind}?limit=${PAGE}`;

// 서버에 남아 있는 전체 건수. 받아온 것보다 많을 수 있어서 따로 들고 있는다.
// 이 값이 없으면 화면이 "200건"이라고 말하는데, 실제로는 1,098건 중
// 200건만 받은 것이라 관리자가 다 봤다고 착각한다.
let total = 0;

function paint() {
  const more = Math.max(total - rows.length, 0);

  $("#list").innerHTML = rows.length
    ? rows.map((x) => renderItem(x, selected?.comment_id)).join("") +
      (more
        ? `<div style="padding:14px;text-align:center;border-top:1px solid var(--line-soft)">
             <button class="slim" id="more">${num(Math.min(more, PAGE))}건 더 불러오기</button>
             <div style="font-size:11.5px;color:var(--muted);margin-top:7px">남은 ${num(more)}건</div>
           </div>`
        : "")
    : `<div class="empty">비어 있습니다.</div>`;

  // 받은 것과 전체가 다르면 둘 다 보여준다. 같으면 전체만.
  $("#count").textContent =
    more > 0 ? `${num(total)}건 중 ${num(rows.length)}건 표시` : `${num(rows.length)}건`;

  $("#list")
    .querySelectorAll(".item")
    .forEach((el) => {
      el.onclick = () => {
        selected = rows.find((r) => r.comment_id === Number(el.dataset.id));
        paint();
      };
    });

  const btn = $("#more");
  if (btn) btn.onclick = () => loadMore(btn);

  renderDetail(selected);
}

async function loadMore(btn) {
  btn.disabled = true;
  btn.textContent = "불러오는 중…";
  try {
    const next = await api(`${listPath()}&offset=${rows.length}`);
    // 이미 있는 건 빼고 붙인다. 그 사이 판정이 바뀌면 겹칠 수 있다.
    const known = new Set(rows.map((r) => r.comment_id));
    rows = rows.concat(next.filter((r) => !known.has(r.comment_id)));
    paint();
  } catch (e) {
    btn.disabled = false;
    btn.textContent = "다시 시도";
    toast(`불러오지 못했습니다: ${e.message}`);
  }
}

async function viewList(kind) {
  loading();
  const isQueue = kind === "queue";
  listKind = isQueue ? "queue" : "hidden";

  // 목록과 전체 건수를 같이 받는다. 목록만 받으면 상한(200)에 걸린 건지
  // 정말 그게 전부인지 구분할 수 없다.
  const [list, stats] = await Promise.all([
    api(listPath()),
    api(`/channels/${channelId()}/stats?period=all`).catch(() => null),
  ]);
  rows = list;
  total = stats ? (isQueue ? stats.unreviewed : stats.hidden) : rows.length;
  selected = rows[0] || null;

  view.innerHTML = `
    <h1>${isQueue ? "검토 큐" : "숨김 목록"}</h1>
    <div class="sub">${esc(channelName())} · <span id="count"></span>${
      isQueue
        ? " · 위험도 높은 순, 같은 등급이면 많이 퍼진 순"
        : " · 지금 유튜브에서 가려져 있는 댓글입니다"
    }</div>
    ${isQueue
      ? ""
      : `<div class="note" style="margin-bottom:14px">여기 있는 댓글은 유튜브에서
         시청자에게 보이지 않습니다. 잘못 가렸다면 <b>큐로 되돌리기</b>를 누르세요 —
         우리 기록에서 검토 큐로 돌아가 다시 판단할 수 있습니다.<br>
         <b>유튜브 API 는 한 번 가린 댓글을 되돌리지 못합니다.</b> 유튜브에서 다시
         공개하려면 YouTube 스튜디오에서 직접 해야 합니다. 그래서 숨김은 신중해야 합니다.</div>`}
    <div class="split">
      <div class="card"><div class="list" id="list"></div></div>
      <div id="detail"></div>
    </div>`;
  paint();
  startRefresh(kind);
}

// ── 관리 기준 ─────────────────────────────────────────

const ACTION_KO = { block: "차단", review: "검토" };

// 토글 한 줄. 켜짐/꺼짐이 색만이 아니라 위치로도 드러나야 한다 —
// 되돌릴 수 없는 조치를 켜는 스위치라 상태를 잘못 읽으면 곤란하다.
function autoHideRows(options) {
  return options
    .map(
      (o) => `
      <div class="ah" data-name="${esc(o.name)}" role="switch" tabindex="0"
           aria-checked="${o.enabled}"
           style="display:flex;align-items:center;gap:10px;padding:8px 0;
                  border-bottom:1px solid var(--line-soft);cursor:pointer">
        <div style="flex:1;font-size:13px;color:${o.enabled ? "var(--text)" : "var(--muted)"}">
          ${esc(o.name)}<span style="color:#b4b4c0;font-size:11px;margin-left:6px">${esc(
            o.description
          )}</span></div>
        <div style="width:34px;height:19px;border-radius:11px;background:${
          o.enabled ? "var(--accent)" : "#dcdce4"
        };position:relative;transition:background .15s">
          <i style="position:absolute;top:2px;${
            o.enabled ? "right:2px" : "left:2px"
          };width:15px;height:15px;border-radius:50%;background:#fff;display:block"></i></div>
      </div>`
    )
    .join("");
}

async function viewRules() {
  loading();
  // 자동 숨김 설정은 없어도 나머지는 보여준다. 한쪽이 실패했다고 등록어
  // 목록까지 같이 사라지면, 화면이 왜 안 뜨는지 알 수 없게 된다.
  // (서버가 옛 코드로 떠 있으면 이 엔드포인트만 404 가 난다)
  const [list, auto, ctx] = await Promise.all([
    api(`/channels/${channelId()}/rules`),
    api(`/channels/${channelId()}/auto-hide`).catch(() => null),
    api(`/channels/${channelId()}/context`).catch(() => null),
  ]);

  view.innerHTML = `
    <h1>관리 기준</h1>
    <div class="sub">${esc(channelName())} · 등록하신 단어는 AI 판별 전에 먼저 적용됩니다</div>

    <div class="split">
      <div class="card">
        <h2>차단 단어 · 표현</h2>
        <div style="display:flex;gap:8px;margin-bottom:14px;flex-wrap:wrap">
          <input type="text" id="word" placeholder="단어 입력"
                 style="flex:1 1 150px;min-width:130px" autocomplete="off">
          <select class="mini" id="action">
            <option value="review">검토 — 큐로 보냄</option>
            <option value="block">차단 — 바로 숨김</option>
          </select>
          <button class="slim" id="add">추가</button>
        </div>
        <div class="chips" id="chips">
          ${list.length
            ? list
                .map(
                  (r) => `<span class="chip">
                    <span class="tag ${r.action === "block" ? "hide" : "rule"}">${
                      ACTION_KO[r.action]
                    }</span>${esc(r.rule_value)}
                    <span class="x" data-id="${r.id}">✕</span></span>`
                )
                .join("")
            : `<div class="empty" style="padding:18px">등록된 단어가 없습니다.</div>`}
        </div>
        <div class="note" style="margin-top:16px">기본 제공 목록은 없습니다. 등록하신 단어만 적용돼요 —
          문맥을 못 보는 규칙이 정상 댓글을 가리는 사고를 막기 위해서입니다.</div>
      </div>

      <div class="card">
        <h2>이 채널의 판단 기준</h2>
        <div style="font-size:12.5px;color:var(--muted);margin-bottom:12px">
          여기 적은 내용이 AI 판별에 함께 전달됩니다.
          <b>영상이 바뀌어도 그대로인 것</b>만 적으세요 —
          특정 영상 설명은 그 영상에 따로 답니다.
        </div>
        ${
          ctx
            ? `<textarea id="ctx" rows="9" placeholder="예)
우리 채널에서 'ㄹㅈㄷ'는 '레전드'의 초성으로 칭찬 표현이다.
출연자의 행동·판단을 평하는 것은 정상이다.
다만 외모·지능을 깎아내리는 것은 모욕이다."
              style="width:100%;box-sizing:border-box;padding:11px 13px;
                     border:1px solid var(--line);border-radius:9px;
                     font:13px/1.65 inherit;resize:vertical"
              >${esc(ctx.context)}</textarea>
             <div style="display:flex;gap:8px;align-items:center;margin-top:10px">
               <span id="ctxinfo" style="flex:1;font-size:11.5px;color:var(--muted)">
                 판정 지문 ${esc(ctx.prompt_version)}</span>
               <button class="slim" id="ctxsave" style="flex:0 0 auto">저장</button>
             </div>`
            : `<div class="empty" style="padding:18px">불러오지 못했습니다.
                 서버를 재시작해보세요.</div>`
        }
        <div class="note" style="margin-top:14px">바꾼 기준은 <b>다음 판별부터</b>
          적용됩니다. 이미 판별한 댓글은 그대로 둡니다 — 기준을 고칠 때마다
          수천 건이 자동으로 다시 돌면 비용을 예측할 수 없기 때문입니다.</div>
      </div>

      <div class="card">
        <h2>자동 숨김 대상</h2>
        <div style="font-size:12.5px;color:var(--muted);margin-bottom:12px">
          여기 켜진 분류만 관리자 확인 없이 바로 가려집니다. 나머지는 전부 검토 큐로 옵니다.
        </div>
        <div id="autohide">${
          auto
            ? autoHideRows(auto.options)
            : `<div class="empty" style="padding:18px">설정을 불러오지 못했습니다.
                 서버가 옛 코드로 떠 있을 수 있어요 — 재시작하면 됩니다.</div>`
        }</div>
        <div class="note" style="margin-top:14px">자동 숨김은 되돌릴 기회가 없어 기본값은
          <b>전부 꺼짐</b>입니다. 켜면 그 분류는 관리자가 보기 전에 가려지므로,
          숨김 목록을 주기적으로 확인해 주세요.<br>
          바꾼 설정은 <b>다음 판별부터</b> 적용됩니다. 이미 판별된 댓글은 그대로 둡니다 —
          누르자마자 예전 댓글이 무더기로 사라지지 않게 하기 위해서입니다.</div>
      </div>
    </div>`;

  const add = async () => {
    const v = $("#word").value.trim();
    if (!v) return;
    await api(`/channels/${channelId()}/rules`, {
      method: "POST",
      body: JSON.stringify({ rule_value: v, action: $("#action").value, expand_variants: true }),
    });
    toast(`'${v}' 등록했습니다`);
    viewRules();
  };
  $("#add").onclick = add;
  $("#word").onkeydown = (e) => {
    if (e.key === "Enter") add();
  };
  view.querySelectorAll(".chip .x").forEach((x) => {
    x.onclick = async () => {
      await api(`/channels/${channelId()}/rules/${x.dataset.id}`, { method: "DELETE" });
      toast("삭제했습니다");
      viewRules();
    };
  });

  // 채널 기준 저장. 저장하면 판정 지문이 바뀌는데, 그게 바뀌었다는 건
  // "이제부터 다른 기준으로 판정된다"는 뜻이라 화면에 같이 보여준다.
  if (ctx) {
    const box = $("#ctx");
    const info = $("#ctxinfo");
    const btn = $("#ctxsave");
    const before = ctx.prompt_version;

    btn.onclick = async () => {
      btn.disabled = true;
      btn.textContent = "저장 중…";
      try {
        const next = await api(`/channels/${channelId()}/context`, {
          method: "PUT",
          body: JSON.stringify({ context: box.value }),
        });
        info.textContent =
          next.prompt_version === before
            ? `판정 지문 ${next.prompt_version} (그대로)`
            : `판정 지문 ${before} → ${next.prompt_version} · 다음 판별부터 적용`;
        toast("기준을 저장했습니다");
      } catch (e) {
        toast(`저장하지 못했습니다: ${e.message}`);
      } finally {
        btn.disabled = false;
        btn.textContent = "저장";
      }
    };
  }

  // 자동 숨김 토글. 화면의 현재 상태를 그대로 읽어 보내므로, 연달아 눌러도
  // 마지막 상태가 저장된다. 서버 응답으로 다시 그려 화면과 DB 를 맞춘다.
  let saving = false;
  const box = $("#autohide");
  if (!auto) return;   // 설정을 못 불러왔으면 토글도 없다
  const toggle = async (row) => {
    if (saving) return;
    const name = row.dataset.name;
    const turningOn = row.getAttribute("aria-checked") !== "true";
    const on = [...box.querySelectorAll(".ah")]
      .filter((r) =>
        r === row ? turningOn : r.getAttribute("aria-checked") === "true"
      )
      .map((r) => r.dataset.name);

    saving = true;
    try {
      const next = await api(`/channels/${channelId()}/auto-hide`, {
        method: "PUT",
        body: JSON.stringify({ categories: on }),
      });
      box.innerHTML = autoHideRows(next.options);
      bindAutoHide();
      toast(
        turningOn
          ? `'${name}'은 이제 관리자 확인 없이 가려집니다`
          : `'${name}'은 검토 큐로 옵니다`
      );
    } finally {
      saving = false;
    }
  };
  const bindAutoHide = () => {
    box.querySelectorAll(".ah").forEach((row) => {
      row.onclick = () => toggle(row);
      row.onkeydown = (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          toggle(row);
        }
      };
    });
  };
  bindAutoHide();
}

// ── 처리 이력 ─────────────────────────────────────────

async function viewHistory() {
  loading();
  const rowsH = await api(`/channels/${channelId()}/history?limit=100`);
  const KO = { hide: "숨김", keep: "유지", ban_author: "채널 차단" };

  view.innerHTML = `
    <h1>처리 이력</h1>
    <div class="sub">${esc(channelName())} · ${num(rowsH.length)}건 · 최신순</div>
    <div class="card">
      ${rowsH.length
        ? `<table>
        <tr><th>일시</th><th>조치</th><th>처리자</th><th>댓글</th><th>분류</th><th>메모</th><th>유튜브</th></tr>
        ${rowsH
          .map(
            (h) => `<tr>
          <td style="white-space:nowrap;color:var(--muted)">${ago(h.executed_at)}</td>
          <td><span class="tag ${h.action === "hide" ? "hide" : h.action === "keep" ? "keep" : ""}">${
            KO[h.action] || h.action
          }</span></td>
          <td>${esc(h.actor || "-")}</td>
          <td class="cut">${esc(h.content)}</td>
          <td>${esc(h.category || "-")}</td>
          <td class="cut" style="color:var(--muted)">${esc(h.note || "")}</td>
          <td style="color:var(--muted)">${h.youtube_synced ? "반영됨" : "미반영"}</td>
        </tr>`
          )
          .join("")}
      </table>
      <div class="note" style="margin-top:14px"><b>반영됨</b>은 유튜브에서 실제로
        가려졌다는 뜻입니다. <b>미반영</b>은 우리 기록에만 남은 것으로, 채널을 아직
        연동하지 않았거나 유튜브 호출이 실패한 경우입니다 (메모에 이유가 적힙니다).
        가린 댓글을 되돌리는 것은 유튜브 API 가 지원하지 않아 늘 미반영입니다 —
        다시 공개하려면 YouTube 스튜디오에서 직접 해야 합니다.</div>`
        : `<div class="empty">아직 처리한 댓글이 없습니다.</div>`}
    </div>`;
}

/** 사이드바 배지. 지금 보고 있는 화면과 무관하게 각 목록의 실제 건수다.
 *  목록을 그리는 함수에서 세면 숨김 목록을 열었을 때 그 수가 검토 큐에 찍힌다.
 *  또 목록은 200건씩만 받아오므로 화면에 보이는 개수와도 다르다. */
async function refreshBadge() {
  const q = $("#badge"), h = $("#badge-hidden");
  if (!q && !h) return;
  // 채널이 하나도 없으면 물을 곳이 없다. 0번 채널을 묻어 404 를 만들지 않는다
  // (새 계정으로 들어온 첫 화면에서 실제로 그랬다).
  if (!channelId()) {
    if (q) q.textContent = "";
    if (h) h.textContent = "";
    return;
  }
  try {
    const s = await api(`/channels/${channelId()}/stats?period=all`);
    if (q) q.textContent = s.unreviewed ? num(s.unreviewed) : "";
    if (h) h.textContent = s.hidden ? num(s.hidden) : "";
  } catch {
    if (q) q.textContent = "";
    if (h) h.textContent = "";
  }
}

// ── 자동 갱신 ─────────────────────────────────────────
//
// watch.py 가 새 댓글을 넣으면 화면이 저절로 따라와야 한다. 유튜브가 댓글
// 알림(웹훅)을 안 주므로 서버도 폴링이고, 화면도 폴링이다.
// 관리자가 보던 걸 뺏지 않으려고, 목록에 없던 건이 생겼을 때만 다시 그린다.

const REFRESH_MS = 20000;
let refreshTimer = null;

function stopRefresh() {
  clearInterval(refreshTimer);
  refreshTimer = null;
}

function startRefresh(kind) {
  stopRefresh();
  refreshTimer = setInterval(async () => {
    if (document.hidden) return;                 // 보고 있지 않으면 쉰다
    try {
      const fresh = await api(listPath());
      const known = new Set(rows.map((r) => r.comment_id));
      const added = fresh.filter((r) => !known.has(r.comment_id));

      // 전체 건수는 새 댓글이 없어도 움직인다 (다른 관리자가 처리했을 수 있다).
      const stats = await api(`/channels/${channelId()}/stats?period=all`).catch(
        () => null
      );
      if (stats) total = kind === "queue" ? stats.unreviewed : stats.hidden;

      if (!added.length) return paint();

      // '더 불러오기'로 받아둔 뒷장을 버리지 않는다. 첫 장만 다시 받았으므로
      // 새로 온 것만 앞에 얹고 나머지는 그대로 둔다.
      const keep = selected?.comment_id;
      rows = added.concat(rows);
      selected = rows.find((r) => r.comment_id === keep) || rows[0] || null;
      paint();
      toast(`새 댓글 ${added.length}건이 들어왔습니다`);
    } catch {
      /* 잠깐 실패해도 다음 주기에 다시 시도한다 */
    }
  }, REFRESH_MS);
}

// ── 라우팅 ────────────────────────────────────────────

// ── 채널 관리 ─────────────────────────────────────────

const 연동오류 = {
  cancelled: "연동을 취소하셨습니다.",
  access_denied:
    "구글에서 거부됐습니다. 미검증 앱이라 '테스트 사용자'에 등록된 계정만 됩니다.",
  no_refresh_token:
    "이미 허용해둔 계정이라 권한을 새로 못 받았습니다. " +
    "구글 계정 설정 > 보안 > 서드파티 앱에서 Outlier 접근을 지운 뒤 다시 해주세요.",
  no_channel: "이 계정에 유튜브 채널이 없습니다.",
};

async function viewChannels() {
  loading();
  const list = await api("/channels");

  // 연동 후 돌아오면 ?connected=1 이나 ?error=... 가 붙어 온다
  const q = new URLSearchParams(location.hash.split("?")[1] || "");
  const 알림 = q.get("connected")
    ? `<div class="note" style="margin-bottom:14px">
         채널 ${esc(q.get("connected"))}개를 연결했습니다. 이제 숨김이 유튜브에 반영됩니다.</div>`
    : q.get("error")
      ? `<div class="note" style="margin-bottom:14px;border-color:var(--critical)">
           ${esc(연동오류[q.get("error")] || q.get("error"))}</div>`
      : "";

  view.innerHTML = `
    <h1>채널 관리</h1>
    <div class="sub">유튜브 권한이 있어야 숨김이 실제로 반영됩니다</div>
    ${알림}
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;
                  margin-bottom:14px">
        <h2 style="margin:0">채널 ${list.length}개</h2>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="slim" id="invite" style="flex:0 0 auto">유튜버에게 연결 링크 보내기</button>
          <button class="slim" id="connect" style="flex:0 0 auto">+ 채널 연결</button>
        </div>
      </div>
      <div id="chlist"></div>
      <div id="invites" style="margin-top:18px"></div>
      <div class="note" style="margin-top:16px">채널 연결은 로그인이 아닙니다.
        <b>채널 주인의 구글 계정</b>으로 'YouTube 관리' 권한을 받는 단계이고, 채널마다
        따로 저장됩니다. 채널이 로그인한 계정과 다른 계정에 있으면 구글 화면에서 그
        계정을 고르세요. 그 권한이 있어야 <b>숨김이 실제 유튜브에 반영</b>됩니다.
        권한 없이도 댓글 수집과 판별은 되지만, 조치는 우리 기록에만 남습니다.</div>
    </div>`;

  $("#connect").onclick = () => (location.href = "/api/channels/connect/start");

  // ── 초대 링크: 유튜버가 옆에 없을 때. 관리자는 유튜버 구글 계정을 모른다 ──
  async function 초대목록() {
    const box = $("#invites");
    if (!box) return;
    let items = [];
    try { items = await api("/channels/invites"); } catch { return; }
    if (!items.length) { box.innerHTML = ""; return; }
    box.innerHTML = `
      <h2 style="font-size:14px;margin:0 0 8px">보낸 연결 링크</h2>
      ${items.map((i) => `
        <div style="display:flex;gap:10px;align-items:center;padding:9px 0;border-bottom:1px solid var(--line-soft);font-size:12.5px">
          <div style="flex:1;min-width:0">
            <div><b>${esc(i.note || "메모 없음")}</b> · ${
              i.used_at
                ? `<span style="color:var(--ok)">연결됨 — ${esc(i.result || "")}</span>`
                : new Date(i.expires_at + "Z") < new Date()
                  ? `<span style="color:var(--muted)">기한 지남</span>`
                  : `<span style="color:var(--muted)">대기 중 · ${new Date(i.expires_at + "Z").toLocaleDateString("ko-KR")}까지</span>`
            }</div>
            <div style="color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(i.url)}</div>
          </div>
          ${i.used_at ? "" : `<button class="slim" data-copy="${esc(i.url)}" style="flex:0 0 auto">복사</button>`}
        </div>`).join("")}
      <div class="note" style="margin-top:12px">링크를 받은 유튜버는 가입·로그인 없이 링크를 열고
        <b>자기 구글 계정</b>으로 권한만 누르면 됩니다. 채널은 이 워크스페이스에 붙습니다.
        링크는 한 번 쓰면 끝나고 7일 뒤 만료됩니다.</div>`;
    box.querySelectorAll("[data-copy]").forEach((b) => {
      b.onclick = async () => {
        try { await navigator.clipboard.writeText(b.dataset.copy); toast("링크를 복사했습니다"); }
        catch { prompt("복사해서 보내세요", b.dataset.copy); }
      };
    });
  }
  $("#invite").onclick = async () => {
    const note = prompt("누구에게 보내는 링크인가요? (예: 진용진 채널)") ;
    if (note === null) return;
    try {
      const inv = await api("/channels/invites", { method: "POST", body: JSON.stringify({ note }) });
      try { await navigator.clipboard.writeText(inv.url); toast("링크를 만들어 복사했습니다. 유튜버에게 보내세요"); }
      catch { toast("링크를 만들었습니다. 아래에서 복사하세요"); }
      초대목록();
    } catch (e) { toast(`링크를 만들지 못했습니다: ${e.message}`); }
  };
  초대목록();

  // 채널별 동의 상태를 같이 물어본다 (목록 API 는 가벼워야 해서 따로 둔다)
  const 상태 = await Promise.all(
    list.map((c) =>
      api(`/channels/${c.id}/consent`)
        .then((x) => ({ ...c, ...x }))
        .catch(() => ({ ...c, agreed: false }))
    )
  );

  $("#chlist").innerHTML = 상태.length
    ? 상태
        .map(
          (c) => `
      <div style="display:flex;align-items:center;gap:12px;padding:14px 0;
                  border-bottom:1px solid var(--line-soft)">
        <div style="flex:1">
          <div style="font-size:14px;font-weight:600">${esc(c.channel_title)}</div>
          <div style="font-size:11.5px;color:var(--muted);margin-top:4px">
            채널 #${c.id} ·
            ${
              c.agreed
                ? `<span style="color:var(--ok)">AI 판별 동의함</span>`
                : `<span style="color:var(--critical)">동의 전 — 판별이 돌지 않습니다</span>`
            } ·
            ${
              c.connected
                ? `<span style="color:var(--ok)">유튜브 권한 있음</span>${
                    c.connected_by
                      ? ` · ${esc(c.connected_by)} 님이 연결${
                          c.connected_at ? ` (${new Date(c.connected_at + "Z").toLocaleDateString("ko-KR")})` : ""
                        }`
                      : ""
                  }`
                : `<span style="color:var(--critical)">유튜브 권한 없음 — 숨김이 반영되지 않습니다</span>`
            }
          </div>
        </div>
        ${
          c.connected
            ? ""
            : `<button class="slim" data-link="${c.id}"
                       style="flex:0 0 auto">유튜브 연동</button>`
        }
        <button class="slim" data-consent="${c.id}" data-now="${c.agreed}"
                style="flex:0 0 auto">${c.agreed ? "동의 철회" : "AI 판별 동의"}</button>
        <button class="slim danger" data-off="${c.id}"
                style="flex:0 0 auto">연동 해제</button>
      </div>`
        )
        .join("")
    : `<div class="empty" style="padding:24px">연동된 채널이 없습니다.<br>
         위 [+ 채널 연결]로 시작하세요.</div>`;

  view.querySelectorAll("[data-link]").forEach((b) => {
    b.onclick = () => (location.href = "/api/channels/connect/start");
  });

  view.querySelectorAll("[data-consent]").forEach((b) => {
    b.onclick = async () => {
      const 켜는중 = b.dataset.now !== "true";
      if (
        켜는중 &&
        !confirm(
          "이 채널의 댓글을 AI가 자동으로 분석합니다.\n\n" +
            "· AI는 위험도를 매기고 순서만 정합니다\n" +
            "· 댓글을 가리는 것은 관리자가 누를 때만입니다\n" +
            "· 댓글 원문은 30일 뒤 삭제됩니다\n\n" +
            "동의하시겠습니까?"
        )
      )
        return;
      await api(`/channels/${b.dataset.consent}/consent`, {
        method: "PUT",
        body: JSON.stringify({ agreed: 켜는중 }),
      });
      toast(켜는중 ? "동의했습니다" : "동의를 철회했습니다");
      viewChannels();
    };
  });

  view.querySelectorAll("[data-off]").forEach((b) => {
    b.onclick = async () => {
      if (
        !confirm(
          "연동을 해제하면 이 채널에서 수집한 댓글·판정·조치 이력이 " +
            "전부 삭제됩니다.\n되돌릴 수 없습니다. 계속할까요?"
        )
      )
        return;
      const r = await api(`/channels/${b.dataset.off}/disconnect`, {
        method: "POST",
      });
      toast(`연동 해제 · 댓글 ${num(r.deleted_comments)}건을 삭제했습니다`);
      await loadChannels();
      viewChannels();
    };
  });
}

// ── 로그인한 사람 ─────────────────────────────────────

async function showMe() {
  try {
    const me = await api("/auth/me");
    const 이름 = me.name || me.email.split("@")[0];
    $("#me-name").textContent = 이름;
    $("#me-ws").textContent = me.workspace_name;
    $("#me-av").textContent = 이름.slice(0, 1);
    $("#me").title = me.email;
  } catch {
    /* 로그인 전이면 그냥 둔다 — api() 가 이미 안내 화면을 그린다 */
  }
}

const ROUTES = {
  "#/dashboard": viewDashboard,
  "#/channels": viewChannels,
  "#/queue": () => viewList("queue"),
  "#/hidden": () => viewList("hidden"),
  "#/rules": viewRules,
  "#/history": viewHistory,
};

/** 아직 채널이 없을 때. 새로 가입한 사람이 처음 보는 화면이다.
 *
 *  전에는 여기서 /api/channels/0/queue 를 불러 404 가 났고, 화면에
 *  "불러오지 못했습니다 404" 가 떴다. 새 사용자가 가입하고 처음 보는 게
 *  오류 메시지면 안 된다.
 */
function showNoChannel() {
  stopRefresh();
  view.innerHTML = `
    <h1>시작하기</h1>
    <div class="sub">아직 연동된 채널이 없습니다</div>
    <div class="card">
      <div class="empty" style="padding:44px 20px;text-align:center">
        <div style="font-size:15px;font-weight:600;color:var(--text);margin-bottom:8px">
          유튜브 채널을 연결해주세요</div>
        <div style="margin-bottom:10px">연결하면 댓글을 모아 위험한 것부터
          보여드립니다.</div>
        <div style="margin-bottom:22px;font-size:12.5px;color:var(--muted)">
          로그인은 끝났습니다. 다음은 <b>채널 주인의 구글 계정</b>으로 유튜브 권한을
          받는 단계입니다. 채널이 다른 계정에 있으면 그 계정을 고르세요.</div>
        <button id="go-connect" style="flex:0 0 auto;padding:10px 28px">
          채널 연결하기</button>
      </div>
    </div>`;
  $("#go-connect").onclick = () => (location.hash = "#/channels");
}

async function route() {
  // 연동에서 돌아오면 '#/channels?connected=1' 처럼 뒤에 값이 붙는다.
  // 앞부분만 떼어 경로를 고른다.
  const base = location.hash.split("?")[0];
  const hash = ROUTES[base] ? base : "#/queue";
  if (base !== hash) return (location.hash = hash);

  // 채널 관리 화면은 채널이 없어도 열려야 한다 — 거기서 연결하니까.
  // 나머지 화면은 채널을 골라야 뜻이 있다.
  if (!channels.length && hash !== "#/channels") {
    document.querySelectorAll(".nav-item").forEach((a) => {
      a.classList.toggle("on", a.getAttribute("href") === hash);
    });
    연동배너();
    return showNoChannel();
  }
  document.querySelectorAll(".nav-item").forEach((a) => {
    a.classList.toggle("on", a.getAttribute("href") === hash);
  });
  stopRefresh();
  연동배너();
  try {
    await ROUTES[hash]();
  } catch (e) {
    // 401 은 api() 가 이미 로그인 안내를 그려뒀다. 덮어쓰면 안 된다.
    if (!e.unauthorized) failed(e);
  }
  refreshBadge();
}

(async function start() {
  try {
    await loadChannels();
  } catch (e) {
    // 로그인 전이면 채널 목록부터 401 이 난다. 그건 오류가 아니라
    // '아직 로그인 안 함' 이므로 안내 화면을 그대로 둔다.
    return e.unauthorized ? undefined : failed(e);
  }
  $("#channel").onchange = (e) => {
    localStorage.setItem("channel", e.target.value);
    route();
  };
  window.addEventListener("hashchange", route);
  showMe();
  route();
})();
