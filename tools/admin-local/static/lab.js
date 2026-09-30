// 실험실. 테스트 사진(또는 모니터 항목의 원본)으로 프리셋을 파라미터를 바꿔 돌리고 단계별 결과를 본다.
import { h, api, formatMs, showError } from "./app.js";

const STAGES = {
  queued: "GPU 서버에 올리는 중", analyze: "사진 분석", generate: "생성", upscale: "업스케일", swap: "얼굴 교체",
  crop: "원본 크롭", cutout: "누끼", identity: "얼굴 유사도", done: "완료", failed: "실패",
};
const ENGINE = { pulid: "FLUX + PuLID", kontext: "FLUX Kontext + LoRA", inswapper: "얼굴 교체(inswapper)", original: "원본 크롭" };
const $ = (id) => document.getElementById(id);
const lab = { presets: [], image: null, fromItem: null, open: null, timer: null, compare: new Set(), ready: false };

const stageLabel = (stage) => (stage?.startsWith("step_") ? `중간 스텝 ${Number(stage.slice(5))}` : STAGES[stage] ?? stage ?? "-");
const presetOf = (code) => lab.presets.find((p) => p.stylePreset === code);
const fileUrl = (run, name) => `/lab-files/${run}/${name}`;

// ── 폼 ──
function renderFields(preset, values = preset.defaults) {
  const box = $("lab-fields");
  box.replaceChildren(...preset.fields.map((f) => {
    const value = values?.[f.key] ?? preset.defaults[f.key];
    if (f.kind === "bool") {
      return h("label", { class: "check" }, h("input", { type: "checkbox", name: `p_${f.key}`, checked: !!value }), f.label);
    }
    if (f.kind === "select") {
      const select = h("select", { name: `p_${f.key}` }, f.options.map((o) => h("option", { value: o }, o)));
      select.value = value ?? f.options[0];
      return h("label", {}, f.label, select);
    }
    if (f.kind === "text") {
      const area = h("textarea", { name: `p_${f.key}`, placeholder: f.key === "prompt" ? "비워 두면 프리셋 그대로" : "" });
      area.value = value ?? "";
      return h("label", { class: "wide" }, f.label, area);
    }
    return h("label", {}, f.label, h("input", { type: "number", name: `p_${f.key}`, step: f.step ?? "any", min: f.min, max: f.max, value: value ?? "" }));
  }));
  $("lab-capture-row").hidden = !preset.captureSteps;
  const engine = h("p", { class: "muted wide" }, `${ENGINE[preset.engine] ?? preset.engine} · ${preset.rarity ?? "-"} · ${preset.display}`);
  box.prepend(engine);
}

function formParams(preset) {
  const form = $("lab-form");
  const params = {};
  for (const f of preset.fields) {
    const el = form.elements[`p_${f.key}`];
    if (!el) continue;
    params[f.key] = f.kind === "bool" ? el.checked : el.value;
  }
  return params;
}

function setImage(file) {
  if (!file) return;
  if (file.size > 15 * 1024 * 1024) { showError(new Error("사진이 15MB 를 넘습니다")); return; }
  const reader = new FileReader();
  reader.onload = () => {
    lab.image = reader.result;
    lab.fromItem = null;
    $("lab-from").hidden = true;
    $("lab-preview").src = lab.image;
    $("lab-preview").hidden = false;
    $("lab-drop-text").textContent = file.name;
  };
  reader.readAsDataURL(file);
}

async function submit(event) {
  event.preventDefault();
  const form = $("lab-form");
  const preset = presetOf(form.elements.stylePreset.value);
  if (!lab.image && !lab.fromItem) { showError(new Error("사진을 골라 주세요")); return; }
  $("lab-run").disabled = true;
  try {
    const body = {
      stylePreset: preset.stylePreset, gender: form.elements.gender.value, params: formParams(preset),
      captureSteps: Number(form.elements.captureSteps.value || 0), label: form.elements.label.value,
      ...(lab.fromItem ? { fromItem: lab.fromItem } : { image: lab.image }),
    };
    const { runId } = await api("/api/lab/runs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    void loadHistory();
    openRun(runId);
  } catch (error) {
    showError(error);
  } finally {
    $("lab-run").disabled = false;
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
      h("h2", {}, `${data.request.stylePreset} 실행 중`),
      h("div", { class: "progress" },
        h("span", {}, `${stageLabel(s.stage)} · ${formatMs(s.elapsedMs ?? null)}`),
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
      h("a", { href: url, target: "_blank", rel: "noopener" }, h("img", { src: url, alt: label, loading: "lazy" }))));
}

function cardFigure(run, result) {
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

function renderRun(data) {
  const { runId, request, result, status } = data;
  const view = $("lab-view");
  if (!result) {
    view.replaceChildren(
      h("h2", {}, `${request.stylePreset} 실패`),
      h("p", { class: "bad" }, status?.error ?? "원인을 알 수 없습니다"),
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
        h("h2", {}, `${result.stylePreset} `, h("span", { class: "muted" }, `${ENGINE[result.engine] ?? result.engine} · ${result.rarity ?? "-"}`)),
        h("p", { class: "muted" }, `${runId}${request.label ? ` · ${request.label}` : ""}${request.source ? ` · 모니터 항목 #${request.source.itemId} 원본` : ""}`)),
      h("button", { type: "button", class: "btn btn--ghost btn--small", onclick: () => loadIntoForm(request) }, "이 설정을 폼에 불러오기")),
    h("h3", {}, "단계별 결과"),
    h("div", { class: "strip" },
      figure(`1 사진 분석 (얼굴 ${a.faceHeightPct ?? "-"}%)`, fileUrl(runId, "analysis.png"), true),
      result.reference ? figure("참조 그림", fileUrl(runId, result.reference)) : null,
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
  const preset = presetOf(request.stylePreset);
  if (!preset) return;
  form.elements.stylePreset.value = preset.stylePreset;
  form.elements.gender.value = request.gender || "auto";
  form.elements.captureSteps.value = request.captureSteps ?? 0;
  renderFields(preset, { ...preset.defaults, ...(request.params || {}) });
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
      h("div", { class: "row" }, h("strong", {}, r.stylePreset ?? "-"), h("span", { class: "muted" }, r.identity ?? "")),
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
    h("strong", {}, `${r.result.stylePreset} · ${r.request.label || r.runId.slice(4, 15)}`),
    h("img", { src: fileUrl(r.runId, "result.png"), alt: "" }),
    h("span", {}, `얼굴 유사도 ${r.result.identity ?? "-"} · ${formatMs(r.result.totalMs)}`),
    h("dl", { class: "kv" }, keys.map((k) => {
      const v = r.result.used?.[k];
      const changed = JSON.stringify(v) !== JSON.stringify(base[k]);
      return h("div", {}, h("dt", {}, k), h("dd", { class: changed ? "diff" : null }, v === undefined ? "-" : String(v)));
    })),
  )));
}

// ── 모니터에서 넘어오기 ──
export function openFromItem(item) {
  lab.fromItem = item.itemId;
  lab.image = null;
  $("lab-preview").hidden = true;
  $("lab-drop-text").textContent = "테스트 사진을 끌어 놓거나 눌러서 고르세요 (고르면 아래 원본 대신 씁니다)";
  $("lab-from").hidden = false;
  $("lab-from").textContent = `모니터 항목 #${item.itemId} (${item.stylePreset}) 의 회원 원본 사진을 씁니다. 실행할 때 원본을 받아 GPU 서버로 올리며 열람 기록이 남습니다.`;
  const preset = presetOf(item.stylePreset);
  if (preset) {
    $("lab-form").elements.stylePreset.value = preset.stylePreset;
    renderFields(preset);
  }
}

export async function initLab() {
  try {
    const status = await api("/api/lab/status");
    lab.ready = status.ready;
    $("lab-setup").hidden = status.ready;
    $("lab-setup").textContent = "GPU 서버 설정(local.json 의 jupyter·gpu)이 없어 실행할 수 없습니다. README 의 실험실 준비를 보세요.";
    $("lab-run").disabled = !status.ready;
    lab.presets = await api("/api/lab/presets");
  } catch (error) {
    showError(error);
    return;
  }
  const select = $("lab-preset");
  const groups = new Map();
  for (const p of lab.presets) {
    if (!groups.has(p.display)) groups.set(p.display, []);
    groups.get(p.display).push(p);
  }
  select.replaceChildren(...[...groups].map(([display, list]) => h("optgroup", { label: display },
    list.map((p) => h("option", { value: p.stylePreset }, `${p.stylePreset} (${p.rarity ?? "-"})`)))));
  select.value = presetOf("CYBERPUNK") ? "CYBERPUNK" : lab.presets[0]?.stylePreset;
  renderFields(presetOf(select.value));
  select.addEventListener("change", () => renderFields(presetOf(select.value)));
  $("lab-defaults").addEventListener("click", () => renderFields(presetOf(select.value)));
  $("lab-file").addEventListener("change", (e) => setImage(e.target.files?.[0]));
  const drop = $("lab-drop");
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("is-over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("is-over"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("is-over"); setImage(e.dataTransfer.files?.[0]); });
  $("lab-form").addEventListener("submit", submit);
  $("lab-history-reload").addEventListener("click", loadHistory);
  void loadHistory();
}
