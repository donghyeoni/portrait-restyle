// 화풍 테스트. 모델·화풍(서비스 프리셋 · "화풍 추가"의 초안 · 직접 입력)을 고르고 테스트 사진(또는 모니터 항목의 원본)과
// 참고 이미지로 돌려 입력부터 결과까지 단계별로 본다.
// 실행 코드는 운영 관리자 실험 워커와 같은 serving/lab/runner.py 다.
import { h, api, formatMs, showError } from "./app.js";

const STAGES = {
  queued: "GPU 서버에 올리는 중", analyze: "사진 분석", generate: "생성", upscale: "업스케일", swap: "얼굴 교체",
  crop: "원본 크롭", cutout: "누끼", identity: "얼굴 유사도", done: "완료", failed: "실패",
};
const ENGINE = { pulid: "FLUX + PuLID", kontext: "FLUX Kontext + LoRA", inswapper: "얼굴 교체(inswapper)", original: "원본 크롭" };
const CUSTOM = "__custom__";
const $ = (id) => document.getElementById(id);
const lab = {
  engines: [], presets: [], drafts: [], engine: "inswapper", images: { image: null, styleRef: null, template: null },
  fromItem: null, open: null, timer: null, compare: new Set(), ready: false,
};

const stageLabel = (stage) => (stage?.startsWith("step_") ? `중간 스텝 ${Number(stage.slice(5))}` : STAGES[stage] ?? stage ?? "-");
const presetOf = (code) => lab.presets.find((p) => p.stylePreset === code);
const engineOf = (name) => lab.engines.find((e) => e.engine === name);
const fileUrl = (run, name) => `/lab-files/${run}/${name}`;
const draftOf = (value) => (value?.startsWith("draft:") ? lab.drafts.find((d) => d.value === value) ?? null : null);
const runTitle = (r) => r.stylePreset ?? (r.style?.name ? `${r.style.name} (추가한 종류)` : `직접 입력 · ${ENGINE[r.engine] ?? r.engine ?? "-"}`);
const GENDER_LABEL = { male: "남성", female: "여성" };
const REFERENCE_USE = { kontext: "화풍 참고로", inswapper: "템플릿으로", pulid: "비교용으로만(PuLID 는 참고 그림을 못 씀)" };

// 지금 폼이 가리키는 설정: 프리셋을 골랐으면 그 값, 직접 입력이면 모델의 기본값.
function current() {
  const engine = engineOf(lab.engine);
  const code = $("lab-form").elements.stylePreset.value;
  const draft = draftOf(code);
  const preset = code === CUSTOM || draft ? null : presetOf(code) ?? null;
  return {
    engine, preset, draft, fields: engine.fields, captureSteps: engine.captureSteps,
    defaults: preset ? preset.defaults : { ...(engine.customDefaults ?? {}), ...(draft?.params ?? {}) },
    label: preset ? `${ENGINE[preset.engine] ?? preset.engine} · ${preset.rarity ?? "-"} · ${preset.display}`
      : draft ? `${engine.model} · ${draft.categoryName} > ${draft.name} (${draft.rarity}, 추가한 종류)` : `${engine.model} · 직접 입력`,
  };
}

// ── 폼 ──
// 모델은 고정(지금은 얼굴 교체만). 서버가 내준 모델이 하나뿐이면 고르는 칸 없이 이름만 보인다.
function renderEngines() {
  const e = engineOf(lab.engine);
  $("lab-engines").replaceChildren("모델 ", h("strong", {}, e.label), " ", h("span", {}, e.model));
}

function renderPresets(selected) {
  const engine = engineOf(lab.engine);
  const select = $("lab-preset");
  const groups = new Map();
  for (const p of lab.presets.filter((item) => item.engine === lab.engine)) {
    if (!groups.has(p.display)) groups.set(p.display, []);
    groups.get(p.display).push(p);
  }
  select.replaceChildren(
    ...[...groups].map(([display, list]) => h("optgroup", { label: display },
      list.map((p) => h("option", { value: p.stylePreset }, `${p.stylePreset} (${p.rarity ?? "-"})`)))),
    ...draftGroups(),
    ...(engine.custom ? [h("option", { value: CUSTOM }, `직접 입력 (${engine.engine === "kontext" ? "프롬프트" : "템플릿 이미지"})`)] : []),
  );
  if (selected && [...select.options].some((o) => o.value === selected)) select.value = selected;
}

// 화풍 추가에서 만든 종류를 카테고리별로 묶는다
function draftGroups() {
  const groups = new Map();
  for (const d of lab.drafts.filter((item) => item.engine === lab.engine)) {
    const label = `${d.categoryName} · 추가한 종류${d.categoryService ? "" : " (새 카테고리)"}`;
    if (!groups.has(label)) groups.set(label, []);
    groups.get(label).push(d);
  }
  return [...groups].map(([label, list]) => h("optgroup", { label },
    list.map((d) => h("option", { value: d.value }, `${d.name} · ${d.code} (${d.rarity})`))));
}

function renderImages() {
  const engine = engineOf(lab.engine);
  document.querySelector('[data-image="styleRef"]').hidden = !engine.styleReference;
  document.querySelector('[data-image="template"]').hidden = !engine.template;
  const help = $("lab-engine-help");
  help.textContent = engine.customHelp;
  help.hidden = !engine.customHelp;
}

function chooseEngine(name, presetCode) {
  lab.engine = name;
  renderEngines();
  renderPresets(presetCode);
  renderImages();
  renderFields();
}

// 조절값 입력 칸. "화풍 추가" 화면도 같은 칸으로 초안의 기본값을 받는다.
export function fieldInputs(fields, values, defaults, promptHint = "비워 두면 프리셋 그대로") {
  return fields.map((f) => {
    const value = values?.[f.key] ?? defaults?.[f.key];
    if (f.kind === "bool") {
      return h("label", { class: "check" }, h("input", { type: "checkbox", name: `p_${f.key}`, checked: !!value }), f.label);
    }
    if (f.kind === "select") {
      const select = h("select", { name: `p_${f.key}` }, f.options.map((o) => h("option", { value: o }, o)));
      select.value = value ?? f.options[0];
      return h("label", {}, f.label, select);
    }
    if (f.kind === "text") {
      const area = h("textarea", { name: `p_${f.key}`, placeholder: promptHint });
      area.value = value ?? "";
      return h("label", { class: "wide" }, f.label, area);
    }
    return h("label", {}, f.label, h("input", { type: "number", name: `p_${f.key}`, step: f.step ?? "any", min: f.min, max: f.max, value: value ?? "" }));
  });
}

export function readFields(form, fields) {
  const params = {};
  for (const f of fields) {
    const el = form.elements[`p_${f.key}`];
    if (!el) continue;
    params[f.key] = f.kind === "bool" ? el.checked : el.value;
  }
  return params;
}

function renderFields(values) {
  const cur = current();
  const hint = cur.preset ? "비워 두면 프리셋 그대로" : cur.draft ? "비워 두면 초안 문구 그대로" : "직접 입력은 프롬프트가 필요합니다";
  $("lab-fields").replaceChildren(h("p", { class: "muted wide" }, cur.label), ...fieldInputs(cur.fields, values ?? cur.defaults, cur.defaults, hint));
  $("lab-capture-row").hidden = !cur.captureSteps;
  const note = $("lab-draft-note");
  note.hidden = !cur.draft;
  if (cur.draft) {
    const refs = (cur.draft.references ?? []).map((g) => GENDER_LABEL[g]);
    note.textContent = refs.length
      ? `이 종류의 ${refs.join("·")} reference 를 ${REFERENCE_USE[cur.draft.engine]} 씁니다(테스트 사진 성별에 맞는 것). 아래에 따로 올린 그림이 있으면 그것이 먼저입니다.`
      : "이 종류는 reference 사진 없이 문구만 씁니다.";
  }
}

function formParams(fields) {
  return readFields($("lab-form"), fields);
}

function setImage(key, file) {
  if (!file) return;
  if (file.size > 15 * 1024 * 1024) { showError(new Error("이미지가 15MB 를 넘습니다")); return; }
  const drop = document.querySelector(`[data-image="${key}"]`);
  const reader = new FileReader();
  reader.onload = () => {
    lab.images[key] = reader.result;
    if (key === "image") {
      lab.fromItem = null;
      $("lab-from").hidden = true;
    }
    const img = drop.querySelector("img");
    img.src = reader.result;
    img.hidden = false;
    drop.querySelector("span").textContent = file.name;
  };
  reader.readAsDataURL(file);
}

function bindDrop(drop) {
  const key = drop.dataset.image;
  drop.querySelector("input").addEventListener("change", (e) => setImage(key, e.target.files?.[0]));
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("is-over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("is-over"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("is-over"); setImage(key, e.dataTransfer.files?.[0]); });
}

async function submit(event) {
  event.preventDefault();
  const form = $("lab-form");
  const cur = current();
  const params = formParams(cur.fields);
  if (!lab.images.image && !lab.fromItem) { showError(new Error("입력 사진을 골라 주세요")); return; }
  if (!cur.preset && !cur.draft && cur.engine.engine === "kontext" && !params.prompt?.trim()) { showError(new Error("직접 입력은 프롬프트가 필요합니다")); return; }
  if (!cur.preset && !cur.draft && cur.engine.engine === "inswapper" && !lab.images.template) { showError(new Error("직접 입력은 템플릿 이미지가 필요합니다")); return; }
  $("lab-run").disabled = true;
  try {
    const body = {
      engine: cur.engine.engine, stylePreset: cur.preset?.stylePreset ?? cur.draft?.value ?? null, gender: form.elements.gender.value, params,
      captureSteps: cur.captureSteps ? Number(form.elements.captureSteps.value || 0) : 0, label: form.elements.label.value,
      ...(lab.images.image ? { image: lab.images.image } : { fromItem: lab.fromItem }),
      ...(cur.engine.styleReference && lab.images.styleRef ? { styleRef: lab.images.styleRef } : {}),
      ...(cur.engine.template && lab.images.template ? { template: lab.images.template } : {}),
    };
    const { runId } = await api("/api/lab/runs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    void loadHistory();
    openRun(runId);
  } catch (error) {
    showError(error);
  } finally {
    $("lab-run").disabled = !lab.ready;
  }
}

// ── 실행 보기 ──
function openRun(runId) {
  lab.open = runId;
  clearTimeout(lab.timer);
  $("lab-view").replaceChildren(h("p", { class: "muted" }, "불러오는 중…"));
  void poll(runId);
  markHistory();
}

async function poll(runId) {
  try {
    const data = await api(`/api/lab/runs/${runId}`);
    if (lab.open !== runId) return;
    if (data.result || data.status?.error) {
      renderRun(data);
      void loadHistory();
      return;
    }
    const s = data.status || {};
    $("lab-view").replaceChildren(
      h("h2", {}, `${runTitle(data.request)} 실행 중`),
      h("div", { class: "progress" },
        h("span", {}, `${s.label ?? stageLabel(s.stage)} · ${formatMs(s.elapsedMs ?? null)}`),
        h("div", { class: "progress__bar" }, h("span"))),
      h("p", { class: "muted" }, "PuLID·Kontext 는 중간 스텝마다 처음부터 다시 그려 스텝 수만큼 오래 걸립니다. 첫 실행은 모델을 올리느라 더 걸립니다."),
    );
    lab.timer = setTimeout(() => poll(runId), 2000);
  } catch (error) {
    showError(error);
    lab.timer = setTimeout(() => poll(runId), 5000);
  }
}

function figure(label, url, free = false) {
  return h("figure", {}, h("figcaption", {}, label),
    h("div", { class: `frame${free ? " frame--free" : ""}` },
      h("a", { href: url, target: "_blank", rel: "noopener" }, h("img", { src: url, alt: label }))));
}

function cardFigure(run, result) {
  if (!result.stylePreset) return null;          // 직접 입력은 서비스 카드 디자인이 없다
  const box = h("div", { class: "frame frame--card" });
  if (window.MotionCardFace) {
    window.MotionCardFace.render(box, {
      rarity: result.rarity, gender: result.analysis?.gender === "female" ? "FEMALE" : "MALE",
      style: result.stylePreset, imageUrl: fileUrl(run, "cutout.png"), name: "실험",
    });
  } else {
    box.append(h("span", { class: "empty" }, "카드 합성 모듈 없음"));
  }
  return h("figure", {}, h("figcaption", {}, "완성 카드"), box);
}

function inputFigures(runId, request, result) {
  const sent = request.images ?? ["input.png"];
  return [
    figure(request.source ? `입력 (회원 #${request.source.itemId} 원본)` : "입력 사진", fileUrl(runId, "input.png"), true),
    sent.includes("style_ref.png") ? figure("화풍 참고 (올린 그림)", fileUrl(runId, "style_ref.png"), true) : null,
    sent.includes("template.png") ? figure("템플릿 (올린 그림)", fileUrl(runId, "template.png"), true) : null,
    ...["male", "female"].flatMap((g) => [
      sent.includes(`template_${g}.png`) ? figure(`${GENDER_LABEL[g]} reference (템플릿)`, fileUrl(runId, `template_${g}.png`), true) : null,
      sent.includes(`style_ref_${g}.png`) ? figure(`${GENDER_LABEL[g]} reference (화풍 참고)`, fileUrl(runId, `style_ref_${g}.png`), true) : null,
      sent.includes(`reference_${g}.png`) ? figure(`${GENDER_LABEL[g]} reference (비교용)`, fileUrl(runId, `reference_${g}.png`), true) : null,
    ]),
    result?.engine === "inswapper" && !sent.some((n) => n.startsWith("template")) && result.reference
      ? figure("프리셋 템플릿", fileUrl(runId, result.reference)) : null,
  ];
}

function renderRun(data) {
  const { runId, request, result, status } = data;
  const view = $("lab-view");
  if (!result) {
    view.replaceChildren(
      h("h2", {}, `${runTitle(request)} 실패`),
      h("p", { class: "bad" }, status?.error ?? "원인을 알 수 없습니다"),
      h("div", { class: "strip" }, inputFigures(runId, request, null)),
      status?.trace ? h("pre", { class: "prompt" }, status.trace) : null,
    );
    return;
  }
  const a = result.analysis || {};
  const used = { ...(result.used || {}) };
  const prompt = used.prompt;
  delete used.prompt;
  view.replaceChildren(
    h("div", { class: "panel__head" },
      h("div", {},
        h("h2", {}, `${runTitle(result)} `, h("span", { class: "muted" }, `${ENGINE[result.engine] ?? result.engine} · ${result.rarity ?? "-"}`)),
        h("p", { class: "muted" }, `${runId}${request.label ? ` · ${request.label}` : ""}`)),
      h("button", { type: "button", class: "btn btn--ghost btn--small", onclick: () => loadIntoForm(request) }, "이 설정을 폼에 불러오기")),
    result.notes?.length ? h("ul", { class: "notes" }, result.notes.map((n) => h("li", {}, n))) : null,
    h("h3", {}, "입력"),
    h("div", { class: "strip" }, inputFigures(runId, request, result)),
    h("h3", {}, "단계별 결과"),
    h("div", { class: "strip" },
      figure(`사진 분석 (얼굴 ${a.faceHeightPct ?? "-"}%)`, fileUrl(runId, "analysis.png"), true),
      (result.steps || []).map((s) => figure(`중간 ${s.step}/${s.total}`, fileUrl(runId, s.file))),
      figure("AI 결과", fileUrl(runId, "result.png")),
      figure("누끼", fileUrl(runId, "cutout.png")),
      cardFigure(runId, result)),
    h("h3", {}, "지표"),
    h("dl", { class: "kv" }, [
      ["얼굴 유사도(원본 대비)", result.identity ?? "검출 실패"], ["얼굴 높이 비율", `${a.faceHeightPct ?? "-"}%`],
      ["성별", a.gender ?? "-"], ["안경", a.glasses ? `있음 (${a.glassesRatio})` : "없음"], ["얼굴 수", a.faces ?? "-"],
      ["결과 크기", (result.resultSize || []).join("×")], ["전체 시간", formatMs(result.totalMs)],
    ].map(([k, v]) => h("div", {}, h("dt", {}, k), h("dd", {}, String(v))))),
    h("h3", {}, "적용된 값"),
    h("dl", { class: "kv" }, Object.entries(used).map(([k, v]) => h("div", {}, h("dt", {}, k), h("dd", {}, typeof v === "object" ? JSON.stringify(v) : String(v))))),
    prompt ? h("pre", { class: "prompt" }, prompt) : null,
    h("h3", {}, "단계별 시간"),
    h("table", { class: "timings" }, h("tbody", {}, (result.timings || []).map((t) => h("tr", {}, h("td", {}, t.label), h("td", {}, formatMs(t.ms)))))),
  );
}

function loadIntoForm(request) {
  const form = $("lab-form");
  const engine = request.engine ?? presetOf(request.stylePreset)?.engine;
  if (!engineOf(engine)) return;
  const draft = request.style?.code && lab.drafts.find((d) => d.code === request.style.code);
  chooseEngine(engine, request.stylePreset ?? draft?.value ?? CUSTOM);
  form.elements.gender.value = request.gender || "auto";
  form.elements.captureSteps.value = request.captureSteps ?? 0;
  renderFields({ ...current().defaults, ...(request.params || {}) });
  window.scrollTo({ top: 0, behavior: "smooth" });
}

// ── 실행 기록 · 비교 ──
async function loadHistory() {
  try {
    const rows = await api("/api/lab/runs");
    lab.rows = rows;
    $("lab-history").replaceChildren(...rows.map((r) => h("div", {
      class: `run-card${r.runId === lab.open ? " is-open" : ""}`, "data-run": r.runId, onclick: () => openRun(r.runId),
    },
      r.state === "done" ? h("img", { src: fileUrl(r.runId, "result.png"), alt: "", loading: "lazy" }) : h("div", { class: "frame" }, h("span", { class: "empty" }, stageLabel(r.state))),
      h("div", { class: "row" }, h("strong", {}, runTitle(r)), h("span", { class: "muted" }, r.identity ?? "")),
      r.label ? h("span", { class: "muted" }, r.label) : null,
      h("label", { class: "row muted", onclick: (e) => e.stopPropagation() },
        h("span", {}, r.runId.slice(4, 15)),
        h("input", { type: "checkbox", checked: lab.compare.has(r.runId), disabled: r.state !== "done", onchange: (e) => { e.target.checked ? lab.compare.add(r.runId) : lab.compare.delete(r.runId); void renderCompare(); } })),
    )));
    if (!rows.length) $("lab-history").replaceChildren(h("p", { class: "muted" }, "아직 실행 기록이 없습니다."));
  } catch (error) {
    showError(error);
  }
}

function markHistory() {
  document.querySelectorAll(".run-card").forEach((el) => el.classList.toggle("is-open", el.dataset.run === lab.open));
}

async function renderCompare() {
  const box = $("lab-compare");
  const ids = [...lab.compare];
  box.hidden = ids.length < 2;
  if (ids.length < 2) { box.replaceChildren(); return; }
  const runs = await Promise.all(ids.map((id) => api(`/api/lab/runs/${id}`).catch(() => null)));
  const valid = runs.filter((r) => r?.result);
  const keys = [...new Set(valid.flatMap((r) => Object.keys(r.result.used || {}).filter((k) => k !== "prompt" && k !== "note")))];
  const base = valid[0]?.result.used || {};
  box.replaceChildren(...valid.map((r) => h("div", { class: "compare__col" },
    h("strong", {}, `${runTitle(r.result)} · ${r.request.label || r.runId.slice(4, 15)}`),
    h("img", { src: fileUrl(r.runId, "result.png"), alt: "" }),
    h("span", {}, `얼굴 유사도 ${r.result.identity ?? "-"} · ${formatMs(r.result.totalMs)}`),
    h("dl", { class: "kv" }, keys.map((k) => {
      const v = r.result.used?.[k];
      const changed = JSON.stringify(v) !== JSON.stringify(base[k]);
      return h("div", {}, h("dt", {}, k), h("dd", { class: changed ? "diff" : null }, v === undefined ? "-" : String(v)));
    })),
  )));
}

// ── "화풍 추가" 와 잇기 ──
export async function reloadDrafts() {
  try {
    lab.drafts = await api("/api/lab/styles");
  } catch (error) {
    showError(error);
    return;
  }
  if (lab.engines.length) {
    const keep = $("lab-preset").value;
    renderPresets(keep);
    if ($("lab-preset").value !== keep) renderFields();
  }
}

export async function testDraft(value) {
  await reloadDrafts();
  const draft = draftOf(value);
  if (draft) chooseEngine(draft.engine, value);
}

// ── 모니터에서 넘어오기 ──
export function openFromItem(item) {
  lab.fromItem = item.itemId;
  lab.images.image = null;
  const drop = document.querySelector('[data-image="image"]');
  drop.querySelector("img").hidden = true;
  drop.querySelector("span").textContent = "입력 사진 — 고르면 아래 회원 원본 대신 씁니다";
  $("lab-from").hidden = false;
  $("lab-from").textContent = `모니터 항목 #${item.itemId} (${item.stylePreset}) 의 회원 원본 사진을 씁니다. 실행할 때 원본을 받아 GPU 서버로 올리며 열람 기록이 남습니다.`;
  const preset = presetOf(item.stylePreset);
  if (preset && engineOf(preset.engine)) chooseEngine(preset.engine, preset.stylePreset);
}

export async function initLab() {
  try {
    const status = await api("/api/lab/status");
    lab.ready = status.ready;
    $("lab-setup").hidden = status.ready;
    $("lab-setup").textContent = "GPU 서버 설정(local.json 의 jupyter·gpu)이 없어 실행할 수 없습니다. README 의 실험실 준비를 보세요.";
    $("lab-run").disabled = !status.ready;
    [lab.engines, lab.presets, lab.drafts] = await Promise.all([api("/api/lab/engines"), api("/api/lab/presets"), api("/api/lab/styles")]);
  } catch (error) {
    showError(error);
    return;
  }
  lab.engine = lab.engines[0]?.engine ?? lab.engine;
  chooseEngine(lab.engine);
  $("lab-preset").addEventListener("change", () => renderFields());
  $("lab-defaults").addEventListener("click", () => renderFields());
  document.querySelectorAll("#lab-form .drop").forEach(bindDrop);
  $("lab-form").addEventListener("submit", submit);
  $("lab-history-reload").addEventListener("click", loadHistory);
  void loadHistory();
}
