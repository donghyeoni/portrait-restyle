// 카드 생성 도구 (로컬). 서버(server.py)의 /api 만 부른다. 글자는 모두 textContent 로 넣는다.
import { initLab, openFromItem } from "./lab.js";
import { initStyles } from "./styles.js";

const HOURS = [6, 24, 72, 168, 720];
const STATUS = { PENDING: "대기", PROCESSING: "생성 중", RETRY_WAITING: "재시도 대기", RECONCILING: "결과 대조", COMPLETED: "완료", FAILED: "실패", CANCELED: "취소" };
const TARGET = { EC2_CPU: "CPU", GPU: "GPU", CLOUD_RUN_GPU: "Cloud GPU" };
const ERRORS = {
  CUTOUT_SERVICE_UNAVAILABLE: "누끼 서비스 연결 실패", CUTOUT_FAILED: "누끼 실패", AI_LEASE_EXPIRED: "처리 시간 초과",
  NO_FACE: "얼굴 인식 불가", SOURCE_DOWNLOAD: "원본 사진 없음", MODEL_ERROR: "생성 모델 오류", GPU_UNAVAILABLE: "GPU 사용 불가",
  WORKER_ERROR: "워커 오류", UNKNOWN: "원인 기록 없음",
};
const TRIGGER = { INITIAL: "최초", AUTO_RETRY: "자동 재시도", MANUAL_RETRY: "관리자 재시도" };

// ── 작은 도구 ──
function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "style") Object.assign(el.style, value);
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else el.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}
const $ = (id) => document.getElementById(id);
const statusLabel = (s) => STATUS[s] ?? s;
const errorLabel = (c) => (c ? ERRORS[c] ?? c : "원인 기록 없음");
const targetLabel = (t, src) => (src === "GEMINI_SSR" ? "Gemini" : t ? TARGET[t] ?? t : "-");
const ms = (v) => (typeof v === "number" && v >= 0 ? v : null);

function formatMs(value) {
  const v = ms(value);
  if (v === null) return "-";
  if (v < 1000) return `${Math.round(v)}ms`;
  const s = v / 1000;
  if (s < 60) return `${s < 10 ? s.toFixed(1) : Math.round(s)}초`;
  const m = Math.floor(s / 60);
  const rest = Math.round(s - m * 60);
  if (m < 60) return rest ? `${m}분 ${rest}초` : `${m}분`;
  return `${Math.floor(m / 60)}시간 ${m % 60}분`;
}
const rate = (part, total) => (total ? `${Math.round((part / total) * 1000) / 10}%` : "-");
const TIME = new Intl.DateTimeFormat("ko-KR", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false, timeZone: "Asia/Seoul" });
const time = (iso) => (iso ? TIME.format(new Date(iso)) : "-");
const status = (s) => h("span", { class: `status is-${String(s).toLowerCase()}` }, statusLabel(s));
const hoursText = (v) => (v < 48 ? `${v}시간` : `${v / 24}일`);

async function api(path, options) {
  const response = await fetch(path, options);
  const body = await response.json().catch(() => ({ error: `응답을 읽지 못했습니다 (${response.status})` }));
  if (!response.ok) throw new Error(body.error || `요청 실패 (${response.status})`);
  return body.data;
}

function showError(error) {
  const box = $("error");
  box.replaceChildren(h("span", {}, error.message || String(error)), h("button", { class: "btn btn--ghost btn--small", type: "button", onclick: () => { box.hidden = true; } }, "닫기"));
  box.hidden = false;
}

function options(select, values, label) {
  select.replaceChildren(...values.map(([value, text]) => h("option", { value }, text ?? label?.(value) ?? value)));
}

// ── 상태 ──
const SUMMARY_ROWS = 10;
const state = { filter: { hours: "72" }, member: null, items: [], cursor: null, selected: null, summaryAll: false };

// ── 요약 ──
async function loadSummary() {
  const hours = $("summary-hours").value;
  try {
    const data = await api(`/api/summary?hours=${hours}`);
    const totals = data.presets.reduce((a, p) => ({ total: a.total + p.total, completed: a.completed + p.completed, failed: a.failed + p.failed, active: a.active + p.active }), { total: 0, completed: 0, failed: 0, active: 0 });
    const stat = (label, value, note, bad) => h("div", { class: `stat${bad ? " is-bad" : ""}` }, h("span", {}, label), h("strong", {}, value.toLocaleString()), note ? h("span", {}, note) : null);
    $("summary-stats").replaceChildren(
      stat("요청", totals.total), stat("완료", totals.completed, rate(totals.completed, totals.total)),
      stat("실패", totals.failed, rate(totals.failed, totals.total), totals.failed > 0), stat("진행 중", totals.active),
    );
    $("summary-errors").replaceChildren(...data.errors.map((e) => h("button", {
      type: "button", class: "chip", title: "이 실패만 목록에서 보기",
      onclick: () => applyFilter({ ...state.filter, errorCode: e.errorCode === "UNKNOWN" ? "" : e.errorCode, status: "FAILED" }),
    }, errorLabel(e.errorCode), h("strong", {}, e.count))));
    // 실패가 많은 프리셋부터. 기본은 10줄만 보이고 나머지는 펼쳐서 본다.
    const presets = [...data.presets].sort((a, b) => b.failed - a.failed || b.total - a.total);
    const shown = state.summaryAll ? presets : presets.slice(0, SUMMARY_ROWS);
    const head = h("thead", {}, h("tr", {}, ["프리셋", "등급", "실행", "요청", "완료율", "실패", "평균 대기", "평균 생성", "p90 생성"].map((t) => h("th", {}, t))));
    const body = h("tbody", {}, shown.map((p) => h("tr", {},
      h("td", {}, h("button", { type: "button", class: "link", onclick: () => applyFilter({ ...state.filter, stylePreset: p.stylePreset }) }, p.stylePreset), p.collection ? h("span", { class: "meta" }, p.collection) : null),
      h("td", {}, p.rarity ?? "-"),
      h("td", {}, targetLabel(p.executionTarget, p.generationSource)),
      h("td", {}, p.total, p.active ? h("span", { class: "meta" }, `진행 ${p.active}`) : null),
      h("td", {}, rate(p.completed, p.total)),
      h("td", { class: p.failed ? "bad" : null }, p.failed),
      h("td", {}, formatMs(p.avgQueueMs)), h("td", {}, formatMs(p.avgRunMs)), h("td", {}, formatMs(p.p90RunMs)),
    )));
    $("summary-table").replaceChildren(head, body);
    if (presets.length > SUMMARY_ROWS) {
      $("summary-table").append(h("tfoot", {}, h("tr", {}, h("td", { colspan: "9" },
        h("button", { type: "button", class: "link", onclick: () => { state.summaryAll = !state.summaryAll; void loadSummary(); } },
          state.summaryAll ? "접기" : `모두 보기 (${presets.length}개)`)))));
    }
    if (!data.presets.length) $("summary-table").replaceChildren(h("tbody", {}, h("tr", {}, h("td", { class: "muted" }, "이 기간에 생성 요청이 없습니다."))));
  } catch (error) {
    showError(error);
  }
}

// ── 목록 ──
function filterForm() {
  const form = $("filters");
  const data = Object.fromEntries(new FormData(form).entries());
  if (state.member) data.userId = state.member.userId;
  return data;
}

function fillForm() {
  const form = $("filters");
  for (const name of ["hours", "status", "rarity", "generationType", "stylePreset", "errorCode"]) {
    form.elements[name].value = state.filter[name] ?? (name === "hours" ? "72" : "");
  }
  renderMember();
}

function applyFilter(next) {
  state.filter = next;
  if (!next.userId) state.member = null;
  fillForm();
  void loadItems();
}

async function loadItems(append = false) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(state.filter)) if (value) query.set(key, value);
  if (append && state.cursor) query.set("cursor", state.cursor);
  $("items-more").disabled = true;
  try {
    const page = await api(`/api/items?${query}`);
    state.items = append ? [...state.items, ...page.items] : page.items;
    state.cursor = page.nextCursor;
    renderItems();
  } catch (error) {
    showError(error);
  } finally {
    $("items-more").disabled = false;
  }
}

function renderItems() {
  const table = $("items");
  $("items-empty").hidden = state.items.length > 0;
  $("items-empty").textContent = "조건에 맞는 생성 항목이 없습니다.";
  $("items-more").hidden = !state.cursor;
  if (!state.items.length) { table.replaceChildren(); return; }
  const head = h("thead", {}, h("tr", {}, ["항목", "회원", "프리셋", "실행", "상태", "대기", "생성", "요청 시각"].map((t) => h("th", {}, t))));
  const body = h("tbody", {}, state.items.map((it) => h("tr", {
    class: it.itemId === state.selected ? "is-selected" : null,
    onclick: () => select(it.itemId),
  },
    h("td", {}, `#${it.itemId}`, h("span", { class: "meta" }, it.generationType === "USER_CARD" ? "가입" : "뽑기")),
    h("td", {}, it.subject ? [it.subject.nickname ?? "-", h("span", { class: "meta" }, `#${it.subject.publicUserNumber ?? it.subject.userId}`)] : "-"),
    h("td", {}, it.stylePreset, h("span", { class: "meta" }, `${it.rarity ?? "-"}${it.collection ? ` · ${it.collection}` : ""}`)),
    h("td", {}, targetLabel(it.executionTarget, it.generationSource)),
    h("td", {}, status(it.status), it.errorCode ? h("span", { class: "meta" }, errorLabel(it.errorCode)) : null, it.attemptCount > 1 ? h("span", { class: "meta" }, `${it.attemptCount}회 실행`) : null),
    h("td", {}, formatMs(it.queueMs)), h("td", {}, formatMs(it.runMs)), h("td", {}, time(it.createdAt)),
  )));
  table.replaceChildren(head, body);
}

function renderMember() {
  const picked = $("member-picked");
  picked.hidden = !state.member;
  $("member-search").hidden = !!state.member;
  picked.replaceChildren();
  if (state.member) {
    picked.append(h("button", { type: "button", class: "chip is-on", title: "회원 조건 지우기", onclick: () => { state.member = null; applyFilter({ ...filterForm(), userId: "" }); } },
      `${state.member.nickname} #${state.member.publicUserNumber} ✕`));
  }
}

async function findMember() {
  const q = $("member-q").value.trim();
  if (!q) return;
  try {
    const users = await api(`/api/users?q=${encodeURIComponent(q)}`);
    $("member-results").replaceChildren(...(users.length ? users : []).map((u) => h("button", {
      type: "button", class: "chip", onclick: () => { state.member = u; $("member-results").replaceChildren(); renderMember(); },
    }, `${u.nickname} #${u.publicUserNumber}`)));
    if (!users.length) $("member-results").replaceChildren(h("span", { class: "muted" }, "찾는 회원이 없습니다."));
  } catch (error) {
    showError(error);
  }
}

// ── 상세 ──
async function select(itemId) {
  state.selected = itemId;
  renderItems();
  const panel = $("detail");
  panel.replaceChildren(h("p", { class: "muted" }, "불러오는 중…"));
  try {
    const detail = await api(`/api/items/${itemId}`);
    if (state.selected === itemId) renderDetail(detail, null);
  } catch (error) {
    panel.replaceChildren(h("p", { class: "muted" }, "항목을 불러오지 못했습니다."));
    showError(error);
  }
}

async function openMedia(detail) {
  try {
    const access = await api(`/api/items/${detail.item.itemId}/media`, { method: "POST" });
    const urls = {};
    for (const m of access.media) if (m.url) urls[m.role] = m.url;
    if (state.selected === detail.item.itemId) renderDetail(detail, { urls, expiresAt: Date.parse(access.expiresAt), missing: access.media.filter((m) => !m.url).map((m) => m.role) });
  } catch (error) {
    showError(error);
  }
}

function frame(label, index, available, url, missing) {
  return h("figure", {},
    h("figcaption", {}, h("span", { class: "index" }, index), label),
    h("div", { class: "frame" }, url
      ? h("a", { href: url, target: "_blank", rel: "noopener", title: "원본 크기로 열기" }, h("img", { src: url, alt: label }))
      : h("span", { class: "empty" }, missing ? "받지 못함" : available ? "이미지 열기를 누르세요" : "없음")));
}

function cardFrame(detail, urls) {
  const subject = urls?.CUTOUT ?? urls?.RESULT ?? null;
  const box = h("div", { class: "frame frame--card" });
  if (!subject) {
    const has = detail.media.some((m) => m.role === "CUTOUT" || m.role === "RESULT");
    box.append(h("span", { class: "empty" }, has ? "이미지를 열면 합성" : "결과 없음"));
  } else if (window.MotionCardFace) {
    const item = detail.item;
    window.MotionCardFace.render(box, {
      rarity: item.rarity, gender: detail.gender === "MALE" || detail.gender === "FEMALE" ? detail.gender : "UNSPECIFIED",
      style: item.stylePreset, userId: item.subject?.userId ?? null, imageUrl: subject, name: item.subject?.nickname ?? null,
    });
  } else {
    box.append(h("span", { class: "empty" }, "카드 합성 모듈이 없습니다 (README: 카드 미리보기)"));
  }
  return h("figure", {}, h("figcaption", {}, h("span", { class: "index" }, 4), "완성 카드"), box);
}

function attemptBlock(attempt, queueMs) {
  const segments = [];
  if (ms(queueMs)) segments.push({ cls: "is-queue", label: "큐 대기", v: queueMs });
  if (ms(attempt.totalMs) !== null) segments.push({ cls: attempt.status === "FAILED" ? "is-fail" : "is-run", label: "실행", v: attempt.totalMs });
  const total = segments.reduce((sum, s) => sum + s.v, 0);
  return h("li", { class: "attempt" },
    h("div", { class: "attempt__head" },
      h("strong", {}, `${attempt.attemptNo}회차`),
      h("span", { class: "meta" }, `${TRIGGER[attempt.triggerType] ?? attempt.triggerType} · ${attempt.stage}`),
      status(attempt.status),
      h("span", { class: "meta" }, `${time(attempt.startedAt)} → ${time(attempt.completedAt)} · ${formatMs(attempt.totalMs)}`)),
    segments.length ? [
      h("div", { class: "timeline", title: segments.map((s) => `${s.label} ${formatMs(s.v)}`).join(", ") },
        segments.map((s) => h("span", { class: s.cls, style: { flexGrow: String(Math.max(s.v, total * 0.02)) } }))),
      h("div", { class: "legend" }, segments.map((s) => h("span", {}, h("i", { class: s.cls }), `${s.label} ${formatMs(s.v)}`))),
    ] : h("p", { class: "muted" }, "아직 끝나지 않았습니다."));
}

function renderDetail(detail, media) {
  const item = detail.item;
  const has = (role) => detail.media.some((m) => m.role === role);
  const urls = media?.urls ?? null;
  const missing = new Set(media?.missing ?? []);
  const expired = media && Date.now() > media.expiresAt;
  const panel = $("detail");
  panel.replaceChildren(
    h("div", { class: "panel__head" },
      h("div", {},
        h("h2", {}, `#${item.itemId} · ${item.stylePreset} `, h("span", { class: "muted" }, item.rarity ?? "-")),
        h("p", { class: "muted" }, `${item.subject ? `${item.subject.nickname ?? "-"} #${item.subject.publicUserNumber ?? item.subject.userId} · ` : ""}${item.generationType === "USER_CARD" ? "가입 카드" : "뽑기 카드"} · 배치 #${item.batchId} (${statusLabel(detail.batchStatus)})`)),
      h("button", { type: "button", class: "btn btn--ghost btn--small", onclick: () => { state.selected = null; renderItems(); panel.replaceChildren(h("p", { class: "muted" }, "왼쪽 목록에서 항목을 고르면 단계별 결과와 실행 기록을 보여 줍니다.")); } }, "닫기")),
    h("div", { class: "status-row" },
      status(item.status),
      item.errorCode ? h("span", { class: "bad" }, `${errorLabel(item.errorCode)} `, h("code", {}, item.errorCode)) : null,
      h("span", { class: "meta" }, `${targetLabel(item.executionTarget, item.generationSource)}${item.status === "PROCESSING" ? ` · 진행 ${item.progress}%` : ""} · ${item.attemptCount}회 실행`)),
    h("h3", {}, "단계별 결과"),
    h("div", { class: "pipeline" },
      frame("원본 사진", 1, has("SOURCE"), urls?.SOURCE, missing.has("SOURCE")),
      frame("AI 결과", 2, has("RESULT"), urls?.RESULT, missing.has("RESULT")),
      frame("누끼", 3, has("CUTOUT"), urls?.CUTOUT, missing.has("CUTOUT")),
      cardFrame(detail, urls)),
    h("div", { class: "toolbar" },
      h("button", { type: "button", class: "btn btn--primary btn--small", disabled: detail.media.length === 0, onclick: (event) => { event.currentTarget.disabled = true; void openMedia(detail); } }, urls && !expired ? "이미지 다시 열기" : "이미지 열기"),
      has("SOURCE") ? h("button", { type: "button", class: "btn btn--ghost btn--small", onclick: () => { switchView("lab"); openFromItem(item); } }, "이 사진으로 테스트") : null,
      h("span", { class: "muted" }, `${has("SOURCE") ? "회원 원본 사진이 포함됩니다. 열 때마다 이 PC 의 logs/media-access.log 에 기록됩니다. " : ""}${expired ? "이미지가 만료되었습니다(5분)." : ""}`)),
    h("h3", {}, "실행 기록"),
    detail.attempts.length
      ? h("ol", { class: "attempts" }, detail.attempts.map((a, i) => attemptBlock(a, i === 0 ? item.queueMs : null)))
      : h("p", { class: "muted" }, "실행 기록이 없습니다(아직 워커가 가져가지 않음)."),
    detail.failures.length ? [
      h("h3", {}, "실패 이벤트"),
      h("ul", { class: "failures" }, detail.failures.map((f) => h("li", {},
        h("span", {}, time(f.at)), h("strong", {}, errorLabel(f.errorCode)), h("code", {}, f.errorCode ?? "-"),
        h("span", { class: "meta" }, `${f.attempt ? `${f.attempt}번째 실행` : ""}${f.retryable === null || f.retryable === undefined ? "" : f.retryable ? " · 재시도 가능" : " · 재시도 불가"}`)))),
    ] : null,
    h("h3", {}, "생성 정보"),
    h("dl", { class: "info" }, [
      ["요청", time(item.createdAt)], ["첫 실행", time(item.startedAt)], ["끝", time(item.completedAt)], ["큐 대기", formatMs(item.queueMs)],
      ["모델", detail.modelVersion ?? "-"], ["프롬프트 버전", detail.promptTemplateVersion ?? "-"], ["카탈로그", detail.presetCatalogVersion ?? "-"],
      ["시드", detail.seed ?? "-"], ["성별", detail.gender ?? "-"], ["요청 방식", detail.requestMode ?? "-"],
      ["카드 자산", detail.cardAssetId ? `#${detail.cardAssetId} (${detail.cardAssetStatus ?? "-"})` : "-"], ["테마 공개", detail.themeReleaseId ? `#${detail.themeReleaseId}` : "-"],
    ].map(([k, v]) => h("div", {}, h("dt", {}, k), h("dd", {}, v)))),
    h("p", { class: "muted" }, "단계별(받기·생성·올리기) 시간은 서비스 DB 에 기록되지 않아 여기서는 실행 전체 시간만 보입니다."),
  );
}

// ── 화면 전환 ──
function switchView(name) {
  for (const tab of document.querySelectorAll(".tabs__tab")) {
    if (tab.dataset.view === name) tab.setAttribute("aria-current", "page");
    else tab.removeAttribute("aria-current");
  }
  $("view-monitor").hidden = name !== "monitor";
  $("view-lab").hidden = name !== "lab";
  $("view-styles").hidden = name !== "styles";
  window.scrollTo({ top: 0 });
}

// ── 시작 ──
function init() {
  for (const tab of document.querySelectorAll(".tabs__tab")) tab.addEventListener("click", () => switchView(tab.dataset.view));
  void initLab();
  void initStyles();
  options($("summary-hours"), HOURS.map((v) => [String(v), `최근 ${hoursText(v)}`]));
  $("summary-hours").value = "24";
  const form = $("filters");
  options(form.elements.hours, HOURS.map((v) => [String(v), hoursText(v)]));
  options(form.elements.status, [["", "전체"], ...Object.entries(STATUS)]);
  options(form.elements.rarity, [["", "전체"], ["N"], ["R"], ["SR"], ["SSR"]]);
  options(form.elements.generationType, [["", "전체"], ["USER_CARD", "가입 카드"], ["DRAW_CARD_ASSET", "뽑기 카드"]]);
  fillForm();
  $("summary-hours").addEventListener("change", loadSummary);
  $("summary-reload").addEventListener("click", loadSummary);
  form.addEventListener("submit", (event) => { event.preventDefault(); applyFilter(filterForm()); });
  $("filters-reset").addEventListener("click", () => { state.member = null; applyFilter({ hours: "72" }); });
  $("items-more").addEventListener("click", () => loadItems(true));
  $("member-find").addEventListener("click", findMember);
  $("member-q").addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); void findMember(); } });
  void loadSummary();
  void loadItems();
}

init();

export { h, api, formatMs, showError, switchView };
