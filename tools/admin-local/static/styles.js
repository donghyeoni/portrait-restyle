// 화풍 추가. 카테고리(= manifests 컬렉션, 모델 하나)를 만들고, 카테고리 안에 종류(= 프리셋)를 더한다.
// 서비스에 이미 있는 카테고리(직업·웹툰…)에도 종류를 더할 수 있다. 종류마다 남·여 reference 사진을 둔다.
// 저장한 종류는 "화풍 테스트" 의 화풍 목록에 나온다. 서비스에 합칠 때 옮길 manifest 를 아래에 보여 준다.
import { h, api, showError, switchView } from "./app.js";
import { fieldInputs, readFields, reloadDrafts, testDraft } from "./lab.js";

const $ = (id) => document.getElementById(id);
const ENGINE_LABEL = { kontext: "Kontext", pulid: "PuLID", inswapper: "얼굴 교체" };
const GENDERS = [["male", "남자"], ["female", "여자"]];

// 모델별 종류 문구 칸
const TEXT = {
  kontext: [
    { key: "prompt", label: "프롬프트 (공통)", placeholder: "Redraw the person from the first image in the art style of the second image. Keep the first person's face, hairstyle, clothing and pose." },
    { key: "prompt_male", label: "남자 프롬프트 (선택 — 있으면 남자는 공통 대신 이것)", placeholder: "" },
    { key: "prompt_female", label: "여자 프롬프트 (선택 — 있으면 여자는 공통 대신 이것)", placeholder: "" },
  ],
  pulid: [
    { key: "subject_male", label: "남자 주체 (옷·차림)", placeholder: "photorealistic portrait of a man, ... {MODEST}, {NECK}" },
    { key: "subject_female", label: "여자 주체 (옷·차림)", placeholder: "photorealistic portrait of a woman, ... {MODEST}, {NECK}" },
    { key: "scene", label: "장면 (배경·조명·구도)", placeholder: "upper body portrait, ... background, ... lighting" },
  ],
  inswapper: [],
};
const ENGINE_HELP = {
  kontext: "입력 사진을 참조로 넣고 문장대로 다시 그립니다. 종류의 reference 사진은 화풍 참고(두 번째 참조)로 들어갑니다.",
  pulid: "얼굴 임베딩만 가져가 문장으로 장면을 새로 그립니다. reference 사진은 생성에 쓰지 못하고(IP-Adapter·Redux 없음) 결과와 나란히 비교하는 목표 모습입니다.",
  inswapper: "종류의 reference 사진(템플릿)의 얼굴 자리에 입력 얼굴을 넣습니다. 얼굴이 정면으로 크게 보이는 상반신 그림이 잘 맞습니다.",
};
const REF_HELP = {
  kontext: "reference 남자·여자: 테스트 사진 성별에 맞는 그림을 화풍 참고로 씁니다. 그림체가 드러난 그림을 쓰세요(실사 인물 사진은 그 인물을 베낍니다). 없으면 문장만으로 돕니다.",
  pulid: "reference 남자·여자: 목표 모습. 테스트 결과 옆에 나란히 보여 비교합니다(생성에는 쓰지 않음).",
  inswapper: "reference 남자·여자: 얼굴을 넣을 템플릿. 테스트 사진 성별에 맞는 것을 씁니다. 하나 이상 필요합니다.",
};

const st = { engines: [], cats: [], cat: null, isNewCat: true, catEngine: "inswapper", kind: null, refs: {}, remove: new Set() };

const refUrl = (k, g) => `/lab-style-files/${k.category}/${k.code}/reference_${g}.png?v=${encodeURIComponent(k.updatedAt ?? "")}`;
const engineMeta = (engine) => st.engines.find((e) => e.engine === engine);
const kindFields = () => (engineMeta(st.cat?.engine)?.fields ?? []).filter((f) => f.key !== "prompt");
const post = (url, body) => api(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

// ── 카테고리 목록 ──
async function loadCategories(selectId) {
  try {
    st.cats = await api(`/api/lab/categories${$("cat-archived").checked ? "?archived=1" : ""}`);
  } catch (error) {
    showError(error);
    return;
  }
  $("cat-list").replaceChildren(...st.cats.map((c) => h("button", {
    type: "button", class: `style-item style-item--cat${c.id === st.cat?.id ? " is-open" : ""}${c.archived ? " is-archived" : ""}`,
    "data-cat": c.id, onclick: () => openCategory(c.id),
  },
    h("div", {},
      h("strong", {}, c.name, h("span", { class: "style-item__badge" }, c.service ? "서비스" : "새 카테고리")),
      h("span", {}, `${c.id} · ${ENGINE_LABEL[c.engine]} · ${c.rarity ?? "-"} · 추가한 종류 ${c.kinds.length}${c.service ? ` / 기존 ${c.presets.length}` : ""}`)))));
  if (selectId) await openCategory(selectId);
}

// ── 카테고리 폼 ──
// 모델은 고정(지금은 얼굴 교체만) — 고르는 칸 없이 이름만 보인다
function renderCatEngines() {
  const e = engineMeta(st.catEngine);
  $("cat-engines").replaceChildren("모델 ", h("strong", {}, ENGINE_LABEL[st.catEngine]), " ", h("span", {}, e?.model ?? ""));
}

function renderCategory() {
  const cat = st.cat;
  const form = $("cat-form");
  const service = !!cat?.service;
  $("cat-title").textContent = cat ? cat.name : "새 카테고리";
  $("cat-badge").textContent = cat ? `${cat.id} · ${service ? "서비스 카테고리" : "새 카테고리(초안)"}` : "";
  $("cat-edit").hidden = service;
  $("cat-service").hidden = !service;
  if (service) {
    $("cat-service").textContent = `모델 ${ENGINE_LABEL[cat.engine]} · 기본 등급 ${cat.rarity} · 기존 종류 ${cat.presets.length}개(아래 사진). 이름·모델은 서비스 manifest 그대로이고, 아래에서 종류만 더합니다.`;
  } else {
    st.catEngine = cat?.engine ?? st.catEngine;
    form.elements.id.value = cat?.id ?? "";
    form.elements.id.readOnly = !st.isNewCat;
    form.elements.name.value = cat?.name ?? "";
    form.elements.rarity.value = cat?.rarity ?? "SR";
    form.elements.memo.value = cat?.memo ?? "";
    renderCatEngines();
    $("cat-engine-help").textContent = ENGINE_HELP[st.catEngine];
    $("cat-archive").hidden = st.isNewCat;
    $("cat-archive").textContent = cat?.archived ? "보관 풀기" : "보관";
  }
  $("kind-panel").hidden = !cat;
  $("manifest-panel").hidden = !cat?.manifest;
  $("cat-manifest").textContent = cat?.manifest ?? "";
  document.querySelectorAll("#cat-list .style-item").forEach((el) => el.classList.toggle("is-open", el.dataset.cat === cat?.id));
  if (cat) renderKinds();
}

async function openCategory(cid, kindCode) {
  try {
    st.cat = await api(`/api/lab/categories/${cid}`);
  } catch (error) {
    showError(error);
    return;
  }
  st.isNewCat = false;
  renderCategory();
  const kind = kindCode ? st.cat.kinds.find((k) => k.code === kindCode) : null;
  if (kind) openKind(kind);
  else closeKind();
}

function newCategory() {
  st.cat = null;
  st.isNewCat = true;
  renderCategory();
  $("cat-form").elements.id.focus();
}

async function saveCategory(event) {
  event.preventDefault();
  const form = $("cat-form");
  $("cat-save").disabled = true;
  try {
    const saved = await post("/api/lab/categories", {
      id: form.elements.id.value.trim().toLowerCase(), name: form.elements.name.value, engine: st.catEngine,
      rarity: form.elements.rarity.value, memo: form.elements.memo.value, isNew: st.isNewCat,
    });
    await loadCategories(saved.id);
  } catch (error) {
    showError(error);
  } finally {
    $("cat-save").disabled = false;
  }
}

async function archiveCategory() {
  if (!st.cat || st.cat.service) return;
  try {
    await post(`/api/lab/categories/${st.cat.id}/archive`, { archived: !st.cat.archived });
    await loadCategories(st.cat.id);
    await reloadDrafts();
  } catch (error) {
    showError(error);
  }
}

// ── 종류 ──
const serviceRefUrl = (cid, code, g) => `/lab-service-refs/${cid}/${code}/${g}`;

// 작은 종류 칸: 남·여 reference 를 나란히. 없는 성별은 빈 칸
function kindTile({ refs, title, sub, open, archived, onclick, href }) {
  const thumbs = h("div", { class: "kind-tile__refs" }, GENDERS.map(([g, label]) => (refs[g]
    ? h("img", { src: refs[g], alt: `${title} ${label}`, loading: "lazy", title: `${title} · ${label}` })
    : h("div", { title: `${label} 없음` }))));
  const body = [thumbs, h("strong", {}, title), h("span", {}, sub)];
  const cls = `kind-tile${open ? " is-open" : ""}${archived ? " is-archived" : ""}`;
  return onclick ? h("button", { type: "button", class: cls, onclick }, body) : h("a", { class: cls, href, target: "_blank", rel: "noopener" }, body);
}

function renderKinds() {
  const cat = st.cat;
  $("kind-help").textContent = `${cat.name} 안의 화풍입니다. 코드는 서비스 stylePreset 코드가 됩니다.`;
  const kinds = cat.kinds.filter((k) => $("cat-archived").checked || !k.archived);
  const service = cat.serviceKinds ?? [];
  const blocks = [];
  if (service.length) {
    blocks.push(h("h4", { class: "kind-group" }, `서비스에 있는 종류 ${service.length}개`),
      h("div", { class: "kind-list" }, service.map((k) => kindTile({
        refs: Object.fromEntries(k.references.map((g) => [g, serviceRefUrl(cat.id, k.code, g)])),
        title: k.code, sub: `${k.key} · ${k.rarity}`, href: serviceRefUrl(cat.id, k.code, k.references[0] ?? "male"),
      }))));
  }
  blocks.push(h("h4", { class: "kind-group" }, `추가한 종류 ${kinds.length}개`),
    kinds.length
      ? h("div", { class: "kind-list" }, kinds.map((k) => kindTile({
        refs: Object.fromEntries((k.references ?? []).map((g) => [g, refUrl(k, g)])),
        title: k.name, sub: `${k.code} · ${k.rarity}${k.archived ? " · 보관" : ""}`,
        open: k.code === st.kind?.code, archived: k.archived, onclick: () => openKind(k),
      })))
      : h("p", { class: "muted" }, "아직 더한 종류가 없습니다. 종류 추가를 누르세요."));
  $("kind-list").replaceChildren(...blocks);
}

function refDrop(g, label) {
  const kind = st.kind;
  const saved = kind?.references?.includes(g) && !st.remove.has(g);
  const src = st.refs[g] ?? (saved ? refUrl(kind, g) : null);
  const input = h("input", { type: "file", accept: "image/*", hidden: true });
  const drop = h("label", { class: "drop drop--ref" }, input,
    src ? h("img", { src, alt: "" }) : null,
    h("span", {}, src ? `reference ${label}` : `reference ${label} — 끌어 놓거나 눌러서 고르세요`),
    src ? h("span", { class: "drop__actions" }, h("button", {
      type: "button", class: "btn btn--ghost btn--small",
      onclick: (e) => { e.preventDefault(); delete st.refs[g]; if (saved) st.remove.add(g); renderRefs(); },
    }, "빼기")) : null);
  const take = (file) => {
    if (!file) return;
    if (file.size > 15 * 1024 * 1024) { showError(new Error("이미지가 15MB 를 넘습니다")); return; }
    const reader = new FileReader();
    reader.onload = () => { st.refs[g] = reader.result; st.remove.delete(g); renderRefs(); };
    reader.readAsDataURL(file);
  };
  input.addEventListener("change", () => take(input.files?.[0]));
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("is-over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("is-over"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("is-over"); take(e.dataTransfer.files?.[0]); });
  return drop;
}

function renderRefs() {
  $("kind-refs").replaceChildren(...GENDERS.map(([g, label]) => refDrop(g, label)));
}

function renderKindForm() {
  const kind = st.kind;
  const cat = st.cat;
  const form = $("kind-form");
  form.hidden = false;
  form.elements.code.value = kind?.code ?? "";
  form.elements.code.readOnly = !!kind;
  form.elements.name.value = kind?.name ?? "";
  form.elements.rarity.value = kind?.rarity ?? cat.rarity ?? "SR";
  form.elements.memo.value = kind?.memo ?? "";
  $("kind-title").textContent = kind ? `${kind.name} 고치기` : `${cat.name} 에 새 종류`;
  $("kind-updated").textContent = kind ? `저장 ${kind.updatedAt.replace("T", " ")}` : "";
  $("kind-ref-help").textContent = REF_HELP[cat.engine];
  renderRefs();
  $("kind-text").replaceChildren(...TEXT[cat.engine].map((t) => {
    const area = h("textarea", { name: `t_${t.key}`, placeholder: t.placeholder });
    area.value = kind?.[t.key] ?? "";
    return h("label", {}, t.label, area);
  }));
  if (cat.engine === "pulid") {
    $("kind-text").append(h("p", { class: "muted" }, "화질 문구(concept 공통)는 뒤에 자동으로 붙고, 주체에 {MODEST} {NECK} 자리를 쓸 수 있습니다."));
  }
  const defaults = engineMeta(cat.engine)?.customDefaults ?? {};
  $("kind-params").replaceChildren(...fieldInputs(kindFields(), kind?.params ?? defaults, defaults));
  $("kind-test").disabled = !kind;
  $("kind-archive").hidden = !kind;
  $("kind-archive").textContent = kind?.archived ? "보관 풀기" : "보관";
}

function openKind(kind) {
  st.kind = kind;
  st.refs = {};
  st.remove = new Set();
  renderKinds();
  renderKindForm();
}

function newKind() {
  openKind(null);
  $("kind-form").elements.code.focus();
}

function closeKind() {
  st.kind = null;
  $("kind-form").hidden = true;
}

async function saveKind(event) {
  event?.preventDefault();
  const form = $("kind-form");
  const body = {
    code: form.elements.code.value.trim().toUpperCase(), name: form.elements.name.value, rarity: form.elements.rarity.value,
    memo: form.elements.memo.value, isNew: !st.kind, params: readFields(form, kindFields()), remove: [...st.remove],
    ...Object.fromEntries(Object.entries(st.refs).map(([g, url]) => [`reference_${g}`, url])),
  };
  for (const t of TEXT[st.cat.engine]) body[t.key] = form.elements[`t_${t.key}`].value;
  $("kind-save").disabled = true;
  try {
    const saved = await post(`/api/lab/categories/${st.cat.id}/kinds`, body);
    await loadCategories();
    await openCategory(st.cat.id, saved.code);
    await reloadDrafts();
    return saved;
  } catch (error) {
    showError(error);
    return null;
  } finally {
    $("kind-save").disabled = false;
  }
}

async function archiveKind() {
  if (!st.kind) return;
  try {
    await post(`/api/lab/categories/${st.cat.id}/kinds/${st.kind.code}/archive`, { archived: !st.kind.archived });
    await openCategory(st.cat.id, st.kind.code);
    await loadCategories();
    await reloadDrafts();
  } catch (error) {
    showError(error);
  }
}

async function testKind() {
  // 고친 내용이 있으면 먼저 저장한다 — 테스트는 저장된 종류를 쓴다
  const saved = await saveKind();
  if (!saved) return;
  switchView("lab");
  await testDraft(`draft:${saved.category}/${saved.code}`);
}

export async function initStyles() {
  try {
    st.engines = await api("/api/lab/engines");
    st.catEngine = st.engines[0]?.engine ?? st.catEngine;
  } catch (error) {
    showError(error);
    return;
  }
  renderCategory();
  $("cat-form").addEventListener("submit", saveCategory);
  $("cat-new").addEventListener("click", newCategory);
  $("cat-archive").addEventListener("click", archiveCategory);
  $("cat-archived").addEventListener("change", () => { void loadCategories(st.cat?.id); });
  $("kind-new").addEventListener("click", newKind);
  $("kind-form").addEventListener("submit", saveKind);
  $("kind-test").addEventListener("click", testKind);
  $("kind-archive").addEventListener("click", archiveKind);
  void loadCategories();
}
