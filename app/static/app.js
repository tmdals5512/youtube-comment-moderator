/* Outlier 관리자 화면.
 *
 * 빌드 도구를 쓰지 않는다. node 를 안 깔아도 되고, 파일을 저장하면 새로고침만
 * 하면 된다. FastAPI 가 같은 서버에서 내보내므로 CORS 도 안 탄다.
 *
 * 화면 5개를 해시 라우팅으로 전환한다: 대시보드 / 검토 큐 / 숨김 / 관리 기준 / 이력.
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
  if (!r.ok) {
    const body = await r.text();
    throw new Error(`${r.status} ${body.slice(0, 200)}`);
  }
  return r.status === 204 ? null : r.json();
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

// ── 대시보드 ──────────────────────────────────────────

async function viewDashboard() {
  loading();
  const period = localStorage.getItem("period") || "all";
  const s = await api(`/channels/${channelId()}/stats?period=${period}`);
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
        <div class="h">목표 30% 이하</div></div>
    </div>

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
        <button class="danger" data-act="hide">숨김 처리</button>
        <button data-act="ban_author">채널 차단</button>
        <button class="good" data-act="keep">${
          x.status === "hidden" ? "복구(공개)" : "유지(정상)"
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
  const actor = localStorage.getItem("actor") || "관리자";
  await api(`/comments/${id}/action`, {
    method: "POST",
    body: JSON.stringify({ action, actor }),
  });
  toast({ hide: "숨김 처리했습니다", keep: "유지했습니다", ban_author: "채널 차단했습니다" }[action]);
  // 처리한 건은 목록에서 빠진다. 다음 건으로 자동으로 넘어가야 손이 안 멈춘다.
  const i = rows.findIndex((r) => r.comment_id === id);
  rows.splice(i, 1);
  selected = rows[Math.min(i, rows.length - 1)] || null;
  paint();
  refreshBadge();
}

function paint() {
  $("#list").innerHTML = rows.length
    ? rows.map((x) => renderItem(x, selected?.comment_id)).join("")
    : `<div class="empty">비어 있습니다.</div>`;
  $("#count").textContent = `${num(rows.length)}건`;
  $("#list")
    .querySelectorAll(".item")
    .forEach((el) => {
      el.onclick = () => {
        selected = rows.find((r) => r.comment_id === Number(el.dataset.id));
        paint();
      };
    });
  renderDetail(selected);
}

async function viewList(kind) {
  loading();
  const isQueue = kind === "queue";
  const path = isQueue
    ? `/channels/${channelId()}/queue?limit=200`
    : `/channels/${channelId()}/hidden?limit=200`;
  rows = await api(path);
  selected = rows[0] || null;

  view.innerHTML = `
    <h1>${isQueue ? "검토 큐" : "숨김 목록"}</h1>
    <div class="sub">${esc(channelName())} · <span id="count"></span>${
      isQueue
        ? " · 위험도 높은 순, 같은 등급이면 많이 퍼진 순"
        : " · 자동으로 가려진 댓글입니다"
    }</div>
    ${isQueue
      ? ""
      : `<div class="note" style="margin-bottom:14px">자동 숨김은 관리자를 거치지 않은 조치입니다.
         정상 댓글이 잘못 가려졌으면 <b>복구(공개)</b>로 되돌리세요.</div>`}
    <div class="split">
      <div class="card"><div class="list" id="list"></div></div>
      <div id="detail"></div>
    </div>`;
  paint();
  startRefresh(kind);
}

// ── 관리 기준 ─────────────────────────────────────────

const ACTION_KO = { block: "차단", review: "검토", allow: "예외" };

async function viewRules() {
  loading();
  const list = await api(`/channels/${channelId()}/rules`);

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
            <option value="allow">예외 — 통과</option>
          </select>
          <button class="slim" id="add">추가</button>
        </div>
        <div class="chips" id="chips">
          ${list.length
            ? list
                .map(
                  (r) => `<span class="chip">
                    <span class="tag ${r.action === "block" ? "hide" : r.action === "allow" ? "keep" : "rule"}">${
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
        <h2>자동 숨김 대상</h2>
        <div style="font-size:12.5px;color:var(--muted);margin-bottom:12px">
          여기 켜진 분류만 관리자 확인 없이 바로 가려집니다. 나머지는 전부 검토 큐로 옵니다.
        </div>
        ${[
          ["욕설", "대상을 겨냥한 욕설", true],
          ["신상털기", "실명·주소·연락처 노출", false],
          ["위협", "신체적 위해 암시", false],
          ["자해", "자해·자살 관련", false],
          ["혐오", "지역·성별·국적 비하", false],
          ["성희롱", "성적 대상화", false],
          ["모욕", "인신공격·조롱", false],
          ["괴롭힘", "반복적 시달림", false],
          ["스팸", "홍보·링크 도배", false],
        ]
          .map(
            ([n, d, on]) => `
          <div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--line-soft)">
            <div style="flex:1;font-size:13px;color:${on ? "var(--text)" : "var(--muted)"}">
              ${n}<span style="color:#b4b4c0;font-size:11px;margin-left:6px">${d}</span></div>
            <div style="width:34px;height:19px;border-radius:11px;background:${
              on ? "var(--accent)" : "#dcdce4"
            };position:relative">
              <i style="position:absolute;top:2px;${
                on ? "right:2px" : "left:2px"
              };width:15px;height:15px;border-radius:50%;background:#fff;display:block"></i></div>
          </div>`
          )
          .join("")}
        <div class="note" style="margin-top:14px">자동 숨김은 되돌릴 기회가 없어 기본값을
          <b>욕설 하나</b>로 두었습니다. 판단이 갈리는 분류(모욕·혐오 등)는 채널이 직접 정하도록
          검토 큐로 보냅니다.</div>
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
      <div class="note" style="margin-top:14px">유튜브 실제 반영은 채널 소유자 인증(OAuth)이 필요합니다.
        지금은 우리 DB 에만 기록됩니다.</div>`
        : `<div class="empty">아직 처리한 댓글이 없습니다.</div>`}
    </div>`;
}

/** 사이드바 배지. 지금 보고 있는 화면과 무관하게 각 목록의 실제 건수다.
 *  목록을 그리는 함수에서 세면 숨김 목록을 열었을 때 그 수가 검토 큐에 찍힌다.
 *  또 목록은 200건씩만 받아오므로 화면에 보이는 개수와도 다르다. */
async function refreshBadge() {
  const q = $("#badge"), h = $("#badge-hidden");
  if (!q && !h) return;
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
      const path =
        kind === "queue"
          ? `/channels/${channelId()}/queue?limit=200`
          : `/channels/${channelId()}/hidden?limit=200`;
      const fresh = await api(path);
      const known = new Set(rows.map((r) => r.comment_id));
      const added = fresh.filter((r) => !known.has(r.comment_id));
      if (!added.length) return;

      // 보고 있던 댓글은 유지한 채 새 것만 얹는다.
      const keep = selected?.comment_id;
      rows = fresh;
      selected = rows.find((r) => r.comment_id === keep) || rows[0] || null;
      paint();
      toast(`새 댓글 ${added.length}건이 들어왔습니다`);
    } catch {
      /* 잠깐 실패해도 다음 주기에 다시 시도한다 */
    }
  }, REFRESH_MS);
}

// ── 라우팅 ────────────────────────────────────────────

const ROUTES = {
  "#/dashboard": viewDashboard,
  "#/queue": () => viewList("queue"),
  "#/hidden": () => viewList("hidden"),
  "#/rules": viewRules,
  "#/history": viewHistory,
};

async function route() {
  const hash = ROUTES[location.hash] ? location.hash : "#/queue";
  if (location.hash !== hash) return (location.hash = hash);
  document.querySelectorAll(".nav-item").forEach((a) => {
    a.classList.toggle("on", a.getAttribute("href") === hash);
  });
  stopRefresh();
  try {
    await ROUTES[hash]();
  } catch (e) {
    failed(e);
  }
  refreshBadge();
}

(async function start() {
  try {
    await loadChannels();
  } catch (e) {
    return failed(e);
  }
  $("#channel").onchange = (e) => {
    localStorage.setItem("channel", e.target.value);
    route();
  };
  window.addEventListener("hashchange", route);
  route();
})();
