<script setup>
import { computed, onMounted, ref } from "vue";
import { apiUrl, askStream, authorizedFetch, request, sourceUrl } from "./api.js";
import { initializeAuth, oidcEnabled, signIn, signOut } from "./auth.js";
import { sha256File } from "./fileHash.js";

const tab = ref("ask");
const question = ref("");
const stage = ref("等待输入");
const result = ref(null);
const searchQuery = ref("");
const searchResult = ref(null);
const auditEvents = ref([]);
const error = ref("");
const spaces = ref([]);
const selectedSpaceId = ref("");
const newSpaceName = ref("");
const spaceBusy = ref(false);
const spaceError = ref("");
const documents = ref([]);
const documentsBusy = ref(false);
const selectedDocument = ref(null);
const chunks = ref([]);
const chunksBusy = ref(false);
const lifecycle = ref({ documentId: "", versionId: "", targetRevision: 2, watermark: 1 });
const versionUpload = ref({ documentRowVersion: "", file: null, sha256: "", hashProgress: 0, status: null });
const upload = ref({
  file: null,
  sha256: "",
  hashProgress: 0,
  status: null,
  job: null,
  phase: "EMPTY",
  busy: false,
  error: "",
});
const cleanup = ref(null);
const feedback = ref({ rating: 5, comment: "" });
const quality = ref(null);
const documentReview = ref({
  decision: "APPROVED",
  comment: "",
  visibility: "TENANT",
  classificationLevel: 0,
  aclScopeTokens: "",
  result: null,
});
const operations = ref({ diagnostics: null, alerts: [] });
const pilot = ref({ id: "", name: "Synthetic Pilot", flag: "pilot.synthetic", revision: 0, result: null });
const uat = ref({ id: "", title: "Synthetic UAT", rowVersion: 0, evidence: null, result: null });
const observation = ref({ id: "", name: "Synthetic optional window", rowVersion: 0, result: null });
const acceptance = ref(null);
const authenticatedUser = ref(null);
let uploadHashRevision = 0;
let versionHashRevision = 0;
let uploadWorkflowRevision = 0;
const terminalJobStates = new Set(["SUCCEEDED", "FAILED_FINAL", "CANCELLED"]);
const uploadPhaseText = computed(() => ({
  EMPTY: "请选择文件",
  HASHING: "正在分块计算文件哈希",
  READY_TO_UPLOAD: "文件已就绪，请点击上传",
  UPLOADING: "正在流式上传文件",
  PROCESSING: "Worker 正在解析、切块、生成向量并写入 Zilliz",
  READY_FOR_REVIEW: "解析入库完成，请检查质量报告并提交复核",
  REVIEWED: "复核已通过，请发布文档",
  PUBLISHED: "文档已发布，可以进入可信问答",
  FAILED: "处理失败，请查看错误信息",
  PROCESSING_TIMEOUT: "仍在处理中，可稍后刷新任务状态",
})[upload.value.phase] ?? upload.value.phase);
const canReview = computed(() => Boolean(lifecycle.value.versionId && quality.value));
const canPublish = computed(() =>
  Boolean(lifecycle.value.versionId && documentReview.value.result?.decision === "APPROVED"),
);
const citations = computed(() =>
  (result.value?.citations ?? []).map((citation) => ({
    ...citation,
    href: sourceUrl(citation.source_url),
  })),
);
const selectedSpace = computed(() =>
  spaces.value.find((item) => item.id === selectedSpaceId.value) ?? null,
);

onMounted(async () => {
  try {
    authenticatedUser.value = await initializeAuth();
    await loadSpaces();
  } catch (cause) {
    error.value = `INITIALIZATION_FAILED:${cause.message}`;
  }
});

async function loadSpaces(preferredSpaceId = selectedSpaceId.value) {
  spaceError.value = "";
  const loaded = await request("/spaces");
  spaces.value = loaded;
  const preferredExists = loaded.some((item) => item.id === preferredSpaceId);
  selectedSpaceId.value = preferredExists ? preferredSpaceId : (loaded[0]?.id ?? "");
  await loadDocuments();
}

async function createSpace() {
  const name = newSpaceName.value.trim();
  if (!name) {
    spaceError.value = "请输入知识库名称";
    return;
  }
  spaceBusy.value = true;
  spaceError.value = "";
  try {
    const created = await request("/spaces", {
      method: "POST",
      headers: { "Idempotency-Key": idempotency("space-create") },
      body: JSON.stringify({ name }),
    });
    newSpaceName.value = "";
    await loadSpaces(created.id);
  } catch (cause) {
    spaceError.value = cause.message;
  } finally {
    spaceBusy.value = false;
  }
}

function resetDocumentSelection() {
  selectedDocument.value = null;
  chunks.value = [];
  quality.value = null;
  documentReview.value.result = null;
}

async function changeSpace() {
  resetDocumentSelection();
  upload.value.error = "";
  await loadDocuments();
}

async function loadDocuments() {
  if (!selectedSpaceId.value) {
    documents.value = [];
    return;
  }
  documentsBusy.value = true;
  try {
    documents.value = await request(`/spaces/${selectedSpaceId.value}/documents`);
  } catch (cause) {
    spaceError.value = cause.message;
    documents.value = [];
  } finally {
    documentsBusy.value = false;
  }
}

async function openDocument(item) {
  selectedDocument.value = item;
  lifecycle.value.documentId = item.document_id;
  lifecycle.value.versionId = item.version_id;
  chunksBusy.value = true;
  chunks.value = [];
  try {
    chunks.value = await request(`/document-versions/${item.version_id}/chunks`);
  } catch (cause) {
    spaceError.value = cause.message;
  } finally {
    chunksBusy.value = false;
  }
}

async function ask() {
  if (!question.value.trim() || !selectedSpaceId.value) return;
  error.value = ""; stage.value = "检索、生成缓冲与验证中"; result.value = null;
  try {
    result.value = await askStream(
      question.value,
      (current) => { stage.value = current; },
      fetch,
      selectedSpaceId.value,
    );
    stage.value = result.value.verified ? "已验证" : "验证失败";
  } catch (cause) { error.value = cause.message; stage.value = "system_error"; }
}

async function submitFeedback() {
  if (!result.value?.rag_run_id) return;
  await request(`/rag-runs/${result.value.rag_run_id}/feedback`, {
    method: "POST",
    body: JSON.stringify({ rating: feedback.value.rating, reason_code: "user_feedback", comment: feedback.value.comment }),
  });
  feedback.value.comment = "已提交";
}

async function search() {
  searchResult.value = await request("/search", {
    method: "POST",
    body: JSON.stringify({ query: searchQuery.value, space_id: selectedSpaceId.value }),
  });
}

const idempotency = (action) => `${action}-${crypto.randomUUID()}`;
async function publish() {
  upload.value.error = "";
  try {
    cleanup.value = await request(`/document-versions/${lifecycle.value.versionId}:publish`, { method: "POST", headers: { "Idempotency-Key": idempotency("publish") } });
    upload.value.phase = "PUBLISHED";
    await loadDocuments();
    const published = documents.value.find(
      (item) => item.version_id === lifecycle.value.versionId,
    );
    if (published) await openDocument(published);
  } catch (cause) {
    upload.value.error = cause.message;
  }
}
async function rollback() {
  cleanup.value = await request(`/documents/${lifecycle.value.documentId}:rollback`, { method: "POST", headers: { "Idempotency-Key": idempotency("rollback") }, body: JSON.stringify({ version_id: lifecycle.value.versionId }) });
}
async function permissions() {
  cleanup.value = await request(`/resources/document/${lifecycle.value.documentId}/permissions`, { method: "PUT", headers: { "Idempotency-Key": idempotency("acl") }, body: JSON.stringify({ target_acl_revision: Number(lifecycle.value.targetRevision), required_watermark: Number(lifecycle.value.watermark), observed_watermark: Number(lifecycle.value.watermark), projection_ok: true }) });
}
async function revoke() {
  cleanup.value = await request(`/documents/${lifecycle.value.documentId}:revoke`, { method: "POST", headers: { "Idempotency-Key": idempotency("revoke") } });
}
async function removeDocument() {
  cleanup.value = await request(`/documents/${lifecycle.value.documentId}`, { method: "DELETE", headers: { "Idempotency-Key": idempotency("delete") } });
}
async function selectVersionFile(event) {
  const file = event.target.files?.[0] ?? null;
  versionUpload.value.file = file;
  versionUpload.value.sha256 = "";
  versionUpload.value.hashProgress = 0;
  const revision = ++versionHashRevision;
  if (!file) return;
  const digest = await sha256File(file, {
    onProgress: (value) => {
      if (revision === versionHashRevision) versionUpload.value.hashProgress = value;
    },
  });
  if (revision === versionHashRevision) versionUpload.value.sha256 = digest;
}
async function selectUploadFile(event) {
  const file = event.target.files?.[0] ?? null;
  const workflowRevision = ++uploadWorkflowRevision;
  upload.value.file = file;
  upload.value.sha256 = "";
  upload.value.hashProgress = 0;
  upload.value.status = null;
  upload.value.job = null;
  upload.value.busy = false;
  upload.value.error = "";
  upload.value.phase = file ? "HASHING" : "EMPTY";
  quality.value = null;
  documentReview.value.result = null;
  const revision = ++uploadHashRevision;
  if (!file) return;
  try {
    const digest = await sha256File(file, {
      onProgress: (value) => {
        if (revision === uploadHashRevision) upload.value.hashProgress = value;
      },
    });
    if (revision === uploadHashRevision && workflowRevision === uploadWorkflowRevision) {
      upload.value.sha256 = digest;
      upload.value.phase = "READY_TO_UPLOAD";
    }
  } catch (cause) {
    if (workflowRevision === uploadWorkflowRevision) {
      upload.value.phase = "FAILED";
      upload.value.error = cause.message;
    }
  }
}

const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

async function monitorIngestionJob(jobId, workflowRevision) {
  try {
    for (let poll = 0; poll < 300; poll += 1) {
      if (workflowRevision !== uploadWorkflowRevision) return;
      const job = await request(`/ingestion-jobs/${jobId}`);
      upload.value.job = job;
      upload.value.status = { ...upload.value.status, job_state: job.state, attempt: job.attempt };
      if (terminalJobStates.has(job.state)) {
        upload.value.busy = false;
        if (job.state !== "SUCCEEDED") {
          upload.value.phase = "FAILED";
          upload.value.error = job.error_code || `INGESTION_${job.state}`;
          return;
        }
        quality.value = await request(
          `/document-versions/${lifecycle.value.versionId}/quality-report`,
        );
        upload.value.phase = "READY_FOR_REVIEW";
        await loadDocuments();
        const ingested = documents.value.find(
          (item) => item.version_id === lifecycle.value.versionId,
        );
        if (ingested) await openDocument(ingested);
        return;
      }
      await wait(1000);
    }
    if (workflowRevision === uploadWorkflowRevision) {
      upload.value.busy = false;
      upload.value.phase = "PROCESSING_TIMEOUT";
    }
  } catch (cause) {
    if (workflowRevision === uploadWorkflowRevision) {
      upload.value.busy = false;
      upload.value.phase = "FAILED";
      upload.value.error = cause.message;
    }
  }
}

async function refreshIngestionJob() {
  const jobId = upload.value.status?.job_id;
  if (!jobId) return;
  upload.value.busy = true;
  upload.value.phase = "PROCESSING";
  await monitorIngestionJob(jobId, uploadWorkflowRevision);
}

async function uploadDocument() {
  const file = upload.value.file;
  if (!file) {
    upload.value.error = "UPLOAD_FILE_REQUIRED";
    return;
  }
  if (!upload.value.sha256) {
    upload.value.error = "UPLOAD_HASH_PENDING";
    return;
  }
  upload.value.error = "";
  upload.value.busy = true;
  upload.value.phase = "UPLOADING";
  const workflowRevision = ++uploadWorkflowRevision;
  try {
    if (!selectedSpaceId.value) throw new Error("SPACE_NOT_AVAILABLE");
    upload.value.status = {
      stage: "CREATING_UPLOAD_SESSION",
      space_id: selectedSpaceId.value,
      filename: file.name,
      expected_size: file.size,
      declared_mime: file.type || "application/octet-stream",
    };
    const created = await request(`/spaces/${selectedSpaceId.value}/upload-sessions`, {
      method: "POST",
      headers: { "Idempotency-Key": idempotency("upload-create") },
      body: JSON.stringify({
        filename: file.name,
        expected_size: file.size,
        expected_sha256: upload.value.sha256,
        declared_mime: file.type || "application/octet-stream",
      }),
    });
    upload.value.status = {
      stage: "UPLOADING_CONTENT",
      upload_session_id: created.upload_session_id,
    };
    const uploadedResponse = await authorizedFetch(sourceUrl(created.upload_path), {
      method: "PUT",
      headers: { "If-Match": `"${created.row_version}"` },
      body: file,
    });
    if (!uploadedResponse.ok) throw new Error("UPLOAD_CONTENT_FAILED");
    const uploaded = await uploadedResponse.json();
    upload.value.status = {
      stage: "COMPLETING_UPLOAD",
      upload_session_id: created.upload_session_id,
    };
    upload.value.status = await request(`/upload-sessions/${created.upload_session_id}:complete`, {
      method: "POST",
      headers: {
        "If-Match": `"${uploaded.row_version}"`,
        "Idempotency-Key": idempotency("upload-complete"),
      },
    });
    lifecycle.value.documentId = upload.value.status.document_id;
    lifecycle.value.versionId = upload.value.status.document_version_id;
    upload.value.phase = "PROCESSING";
    void monitorIngestionJob(upload.value.status.job_id, workflowRevision);
  } catch (cause) {
    upload.value.busy = false;
    upload.value.phase = "FAILED";
    upload.value.error = cause.message;
  }
}
async function loadDocumentVersionEtag() {
  const document = await request(`/documents/${lifecycle.value.documentId}`);
  versionUpload.value.documentRowVersion = String(document.row_version);
}
async function uploadNewVersion() {
  const file = versionUpload.value.file;
  if (!file || !versionUpload.value.documentRowVersion) return;
  const createdResponse = await authorizedFetch(
    apiUrl(`/documents/${lifecycle.value.documentId}/versions/upload-sessions`),
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "If-Match": `"${versionUpload.value.documentRowVersion}"`,
        "Idempotency-Key": idempotency("new-version"),
      },
      body: JSON.stringify({
        filename: file.name,
        expected_size: file.size,
        expected_sha256: versionUpload.value.sha256,
        declared_mime: file.type || "application/octet-stream",
      }),
    },
  );
  if (!createdResponse.ok) throw new Error("VERSION_SESSION_CREATE_FAILED");
  const created = await createdResponse.json();
  const uploadedResponse = await authorizedFetch(sourceUrl(created.upload_path), {
    method: "PUT",
    headers: { "If-Match": `"${created.row_version}"` },
    body: file,
  });
  if (!uploadedResponse.ok) throw new Error("VERSION_UPLOAD_FAILED");
  const uploaded = await uploadedResponse.json();
  versionUpload.value.status = await request(
    `/upload-sessions/${created.upload_session_id}:complete`,
    {
      method: "POST",
      headers: {
        "If-Match": `"${uploaded.row_version}"`,
        "Idempotency-Key": idempotency("complete-version"),
      },
    },
  );
  lifecycle.value.versionId = versionUpload.value.status.document_version_id;
}
async function runLocalCleanup() {
  cleanup.value = await request(`/documents/${lifecycle.value.documentId}/cleanup/local_file:run`, { method: "POST", headers: { "Idempotency-Key": idempotency("cleanup-local") } });
}
async function loadAudit() { auditEvents.value = await request("/admin/audit-events"); }
async function loadQuality() {
  upload.value.error = "";
  try {
    quality.value = await request(`/document-versions/${lifecycle.value.versionId}/quality-report`);
    upload.value.phase = "READY_FOR_REVIEW";
  } catch (cause) {
    upload.value.error = cause.message;
  }
}
async function submitDocumentReview() {
  upload.value.error = "";
  try {
    documentReview.value.result = await request(
      `/document-versions/${lifecycle.value.versionId}/review`,
      {
        method: "POST",
        headers: { "Idempotency-Key": idempotency("document-review") },
        body: JSON.stringify({
          decision: documentReview.value.decision,
          comment: documentReview.value.comment,
          security_projection:
            documentReview.value.decision === "APPROVED"
              ? {
                  visibility: documentReview.value.visibility,
                  classification_level: Number(documentReview.value.classificationLevel),
                  acl_scope_tokens: documentReview.value.aclScopeTokens
                    .split(",")
                    .map((value) => value.trim())
                    .filter(Boolean),
                }
              : null,
        }),
      },
    );
    upload.value.phase = documentReview.value.result.decision === "APPROVED" ? "REVIEWED" : "READY_FOR_REVIEW";
  } catch (cause) {
    upload.value.error = cause.message;
  }
}
async function loadOperations() {
  operations.value.diagnostics = await request("/admin/diagnostics");
  operations.value.alerts = await request("/admin/alerts");
}
async function createPilot() {
  pilot.value.result = await request("/governance/pilots", {
    method: "POST",
    headers: { "Idempotency-Key": idempotency("pilot-create") },
    body: JSON.stringify({ name: pilot.value.name, feature_flag: pilot.value.flag }),
  });
  pilot.value.id = pilot.value.result.pilot_id;
  pilot.value.revision = pilot.value.result.revision;
}
async function signPilot(role, decision = "APPROVE") {
  await request(`/governance/pilots/${pilot.value.id}/signoffs`, {
    method: "POST",
    headers: { "If-Match": `"${pilot.value.revision}"`, "Idempotency-Key": idempotency(`pilot-signoff-${role}`) },
    body: JSON.stringify({ role, decision, comment: "synthetic signoff" }),
  });
}
async function evaluatePilot() {
  pilot.value.result = await request(`/governance/pilots/${pilot.value.id}:evaluate`, { method: "POST", headers: { "If-Match": `"${pilot.value.revision}"`, "Idempotency-Key": idempotency("pilot-evaluate") } });
  const current = await request(`/governance/pilots/${pilot.value.id}`);
  pilot.value.revision = current.revision;
}
async function rolloutPilot() {
  pilot.value.result = await request(`/governance/pilots/${pilot.value.id}:rollout`, { method: "POST", headers: { "If-Match": `"${pilot.value.revision}"`, "Idempotency-Key": idempotency("pilot-rollout") } });
}
async function canaryPilot() {
  pilot.value.result = await request(`/governance/pilots/${pilot.value.id}:canary?seed=20260901`, { method: "POST", headers: { "If-Match": `"${pilot.value.revision}"`, "Idempotency-Key": idempotency("pilot-canary") } });
  pilot.value.revision = pilot.value.result.pilot_revision;
}
async function rollbackPilot() {
  pilot.value.result = await request(`/governance/pilots/${pilot.value.id}:rollback`, {
    method: "POST",
    headers: { "If-Match": `"${pilot.value.revision}"`, "Idempotency-Key": idempotency("pilot-rollback") },
    body: JSON.stringify({ trigger: "synthetic rollback trigger" }),
  });
}
async function createUat() {
  const revision = `uat-${crypto.randomUUID()}`;
  uat.value.evidence = await request("/admin/evidence-index", {
    method: "POST",
    body: JSON.stringify({ category: "uat", revision, metadata: { simulated: true } }),
  });
  uat.value.result = await request("/governance/uat-cases", {
    method: "POST",
    headers: { "Idempotency-Key": idempotency("uat-create") },
    body: JSON.stringify({ pilot_id: pilot.value.id, title: uat.value.title, steps: ["synthetic step"], expected: ["safe result"] }),
  });
  uat.value.id = uat.value.result.case_id;
  uat.value.rowVersion = uat.value.result.row_version;
}
async function completeUat(result) {
  uat.value.result = await request(`/governance/uat-cases/${uat.value.id}/result`, {
    method: "PUT",
    headers: { "If-Match": `"${uat.value.rowVersion}"`, "Idempotency-Key": idempotency("uat-result") },
    body: JSON.stringify({ result, step_results: ["safe result"], evidence: uat.value.evidence ? [{ category: uat.value.evidence.category, revision: uat.value.evidence.revision, content_hash: uat.value.evidence.content_hash }] : [] }),
  });
  uat.value.rowVersion = uat.value.result.row_version;
}
async function createObservation() {
  observation.value.result = await request("/governance/observations", {
    method: "POST",
    headers: { "Idempotency-Key": idempotency("observation-create") },
    body: JSON.stringify({ name: observation.value.name }),
  });
  observation.value.id = observation.value.result.window_id;
  observation.value.rowVersion = observation.value.result.row_version;
}
async function recordObservationMetrics() {
  observation.value.result = await request(`/governance/observations/${observation.value.id}/metrics`, {
    method: "PUT",
    headers: { "If-Match": `"${observation.value.rowVersion}"`, "Idempotency-Key": idempotency("observation-metrics") },
    body: JSON.stringify({ metrics: { availability: 1, error_rate: 0, latency_p95: 0.01, sample_count: 100, coverage_ratio: 1, sampling_gap_count: 0 } }),
  });
  observation.value.rowVersion = observation.value.result.row_version;
}
async function signObservation(role) {
  await request(`/governance/observations/${observation.value.id}/signoffs`, {
    method: "POST",
    headers: { "If-Match": `"${observation.value.rowVersion}"`, "Idempotency-Key": idempotency(`observation-signoff-${role}`) },
    body: JSON.stringify({ role, decision: "APPROVE", comment: "synthetic signoff" }),
  });
}
async function generateAcceptance() {
  acceptance.value = await request(`/governance/observations/${observation.value.id}/final-acceptance-report`);
}
</script>

<template>
  <div class="shell">
    <aside><h1>RAG <small>KNOWLEDGE</small></h1><button v-for="name in ['ask','admin','debug','governance']" :key="name" :class="{active: tab===name}" @click="tab=name">{{ {ask:'知识问答',admin:'知识库',debug:'检索调试',governance:'系统治理'}[name] }}</button><p>知识库、文档、分块与问答使用同一个后端数据源。</p></aside>
    <main>
      <header><div><span>KNOWLEDGE BASE WORKSPACE</span><h2>企业知识库</h2></div><div class="session"><label>当前知识库</label><select v-model="selectedSpaceId" data-testid="global-space-select" @change="changeSpace"><option v-if="!spaces.length" value="">尚未创建</option><option v-for="item in spaces" :key="item.id" :value="item.id">{{ item.name }}</option></select><button v-if="oidcEnabled && !authenticatedUser" @click="signIn">OIDC 登录</button><button v-if="oidcEnabled && authenticatedUser" @click="signOut">退出 {{ authenticatedUser.profile?.name ?? authenticatedUser.profile?.sub }}</button></div></header>
      <section v-if="tab==='ask'">
        <article class="hero-card"><span class="eyebrow">在指定知识库中检索</span><h3>{{ selectedSpace?.name ?? '请先创建知识库' }}</h3><label>问答知识库</label><select v-model="selectedSpaceId" @change="changeSpace"><option v-for="item in spaces" :key="item.id" :value="item.id">{{ item.name }}</option></select><label>问题</label><textarea v-model="question" rows="5" placeholder="例如：这份制度的有效期是多久？"/><button class="primary" :disabled="!selectedSpaceId || !question.trim()" @click="ask">从此知识库回答</button><span class="stage">{{ stage }}</span></article>
        <article><h3>{{ result?.status ?? '尚未运行' }}</h3><p class="answer">{{ result?.answer ?? '答案仅在引用与权限复核后显示。' }}</p><a v-for="citation in citations" :key="citation.evidence_id" :href="citation.href" target="_blank">{{ citation.evidence_id }} · 签名来源</a><form v-if="result" @submit.prevent="submitFeedback"><select v-model="feedback.rating"><option :value="5">有帮助</option><option :value="1">无帮助</option></select><input v-model="feedback.comment" placeholder="反馈说明"><button>提交反馈</button></form><p class="error">{{ error }}</p></article>
      </section>
      <section v-if="tab==='admin'">
        <article><div class="section-title"><div><span class="eyebrow">KNOWLEDGE BASES</span><h3>创建和选择知识库</h3></div><span class="badge">{{ spaces.length }} 个</span></div><div class="inline-form"><input v-model="newSpaceName" data-testid="new-space-name" placeholder="知识库名称，例如：产品手册" @keyup.enter="createSpace"><button class="primary" data-testid="create-space-submit" :disabled="spaceBusy || !newSpaceName.trim()" @click="createSpace">{{ spaceBusy ? '创建中…' : '创建知识库' }}</button></div><div class="space-grid" data-testid="space-list"><button v-for="item in spaces" :key="item.id" class="space-card" :class="{selected:selectedSpaceId===item.id}" @click="selectedSpaceId=item.id;changeSpace()"><b>{{ item.name }}</b><small>{{ item.status }} · {{ item.id }}</small></button></div><p class="error">{{ spaceError }}</p></article>
        <article class="workflow"><h3>文档解析入库流程</h3><ol><li :class="{done: upload.status}">上传文件并创建任务</li><li :class="{done: upload.job?.state==='SUCCEEDED'}">Worker 解析、切块、Embedding、写入 Zilliz</li><li :class="{done: documentReview.result?.decision==='APPROVED'}">检查质量并提交安全复核</li><li :class="{done: upload.phase==='PUBLISHED'}">发布到检索空间</li><li :class="{done: upload.phase==='PUBLISHED'}">进入可信问答</li></ol><p class="workflow-status" :data-phase="upload.phase">{{ uploadPhaseText }}</p></article>
        <article><h3>1. 上传到“{{ selectedSpace?.name ?? '-' }}”并解析入库</h3><input data-testid="initial-upload-file" type="file" @change="selectUploadFile"><button class="primary" data-testid="initial-upload-submit" :disabled="!selectedSpaceId || !upload.sha256 || upload.busy" @click="uploadDocument">{{ upload.busy ? '处理中…' : '上传并开始解析入库' }}</button><button v-if="upload.status?.job_id && !upload.busy && upload.job?.state!=='SUCCEEDED'" @click="refreshIngestionJob">刷新解析状态</button><progress :value="upload.hashProgress" max="1"/><code data-testid="initial-upload-hash">{{ upload.sha256 || '等待选择文件' }}</code><p class="error" data-testid="initial-upload-error">{{ upload.error }}</p><dl v-if="upload.status" class="result-grid" data-testid="initial-upload-result"><dt>当前阶段</dt><dd>{{ upload.status.stage ?? 'INGESTION_JOB' }}</dd><dt>Document ID</dt><dd>{{ upload.status.document_id ?? '-' }}</dd><dt>Version ID</dt><dd>{{ upload.status.document_version_id ?? '-' }}</dd><dt>Job ID</dt><dd>{{ upload.status.job_id ?? '-' }}</dd><dt>任务状态</dt><dd>{{ upload.job?.state ?? upload.status.status ?? '-' }}</dd><dt>尝试次数</dt><dd>{{ upload.job?.attempt ?? 0 }}</dd></dl><p v-else data-testid="initial-upload-result" class="empty-result">选择文件并点击“上传并开始解析入库”后，这里会显示任务进度。</p></article>
        <article><div class="section-title"><div><span class="eyebrow">DOCUMENTS</span><h3>2. 已入库文件</h3></div><button :disabled="documentsBusy || !selectedSpaceId" @click="loadDocuments">{{ documentsBusy ? '刷新中…' : '刷新列表' }}</button></div><div v-if="documents.length" class="table-wrap"><table data-testid="document-list"><thead><tr><th>文件</th><th>解析状态</th><th>发布状态</th><th>分块</th><th>版本</th><th></th></tr></thead><tbody><tr v-for="item in documents" :key="item.document_id"><td><b>{{ item.filename }}</b><small>{{ item.document_id }}</small></td><td><span class="badge" :class="item.processing_state==='VALIDATED'?'success':'warning'">{{ item.processing_state }}</span></td><td><span class="badge">{{ item.publication_state }}</span></td><td>{{ item.chunk_count }}</td><td>v{{ item.version_no }}</td><td><button data-testid="view-chunks" @click="openDocument(item)">查看分块</button></td></tr></tbody></table></div><p v-else class="empty-result">该知识库还没有文档。</p></article>
        <article v-if="selectedDocument" data-testid="chunk-panel"><div class="section-title"><div><span class="eyebrow">CHUNKS</span><h3>{{ selectedDocument.filename }} · 分块状态</h3></div><span class="badge">{{ chunks.length }} 个分块</span></div><p v-if="chunksBusy">正在读取分块…</p><div v-else-if="chunks.length" class="chunk-list"><details v-for="chunk in chunks" :key="chunk.chunk_id" class="chunk-card"><summary><span>#{{ chunk.ordinal+1 }} · {{ chunk.kind }}</span><span><b>{{ chunk.status }}</b> · {{ chunk.token_count ?? '-' }} tokens</span></summary><p>{{ chunk.text }}</p><code>{{ JSON.stringify(chunk.locator) }}</code></details></div><p v-else class="empty-result">解析任务尚未生成分块。</p></article>
        <article><h3>2. 检查质量并提交复核</h3><button :disabled="!lifecycle.versionId" @click="loadQuality">读取质量报告</button><select v-model="documentReview.decision"><option>APPROVED</option><option>NEEDS_REWORK</option><option>REJECTED</option></select><select v-model="documentReview.visibility"><option>TENANT</option><option>RESTRICTED</option></select><input v-model.number="documentReview.classificationLevel" type="number" min="0" max="3" placeholder="密级 0-3"><input v-model="documentReview.aclScopeTokens" placeholder="ACL scopes，普通本机文档可留空"><input v-model="documentReview.comment" placeholder="复核说明"><button class="primary" :disabled="!canReview" @click="submitDocumentReview">提交复核</button><p v-if="!quality" class="hint">解析成功后会自动加载质量报告；也可以点击上方按钮手动读取。</p><pre v-else>{{ JSON.stringify({quality,review:documentReview.result},null,2) }}</pre></article>
        <article><h3>3. 发布并开始问答</h3><input v-model="lifecycle.documentId" placeholder="Document ID"><input v-model="lifecycle.versionId" placeholder="Version ID"><div class="actions"><button class="primary" :disabled="!canPublish" @click="publish">发布文档</button><button :disabled="upload.phase!=='PUBLISHED'" @click="tab='ask'">进入可信问答</button></div><p class="hint">只有质量复核通过并发布后，文档才会参与检索和回答。</p><pre v-if="cleanup">{{ JSON.stringify(cleanup,null,2) }}</pre></article>
        <article><h3>高级生命周期操作</h3><input v-model="lifecycle.targetRevision" type="number" placeholder="ACL revision"><input v-model="lifecycle.watermark" type="number" placeholder="Watermark"><div class="actions"><button @click="rollback">回滚</button><button @click="permissions">权限转换</button><button @click="revoke">撤权</button><button class="danger" @click="removeDocument">删除</button></div></article>
        <article><h3>既有文档新版本</h3><button @click="loadDocumentVersionEtag">读取 Document row version</button><input v-model="versionUpload.documentRowVersion" placeholder="If-Match row version"><input type="file" @change="selectVersionFile"><progress :value="versionUpload.hashProgress" max="1"/><button :disabled="!versionUpload.sha256" @click="uploadNewVersion">上传不可变新版本</button><p>PROCESSING 不可发布；Worker 验证为 STAGED 后再使用上方“发布”。</p><pre>{{ JSON.stringify(versionUpload.status,null,2) }}</pre></article>
        <article><h3>清理 Outbox 状态</h3><button @click="runLocalCleanup">运行受控本地清理</button><p>MySQL / Redis / Zilliz 需要外部授权，保持 PENDING_APPROVAL。</p><pre>{{ JSON.stringify(cleanup,null,2) }}</pre></article>
        <article><h3>追加式审计</h3><button @click="loadAudit">刷新</button><pre>{{ JSON.stringify(auditEvents,null,2) }}</pre></article>
      </section>
      <section v-if="tab==='debug'"><article><h3>{{ selectedSpace?.name ?? '-' }} · 独立检索调试</h3><input v-model="searchQuery" placeholder="查询"><button :disabled="!selectedSpaceId" @click="search">在当前知识库检索</button><pre>{{ JSON.stringify(searchResult,null,2) }}</pre></article></section>
      <section v-if="tab==='governance'">
        <article><h3>运行诊断与告警</h3><button @click="loadOperations">刷新诊断</button><pre>{{ JSON.stringify(operations,null,2) }}</pre></article>
        <article><h3>Pilot Go/No-Go 与灰度</h3><input v-model="pilot.name"><input v-model="pilot.flag"><button @click="createPilot">创建 simulated pilot</button><button v-for="role in ['technical','security','sre']" :key="role" @click="signPilot(role)">签字 {{ role }}</button><button @click="evaluatePilot">评估</button><button @click="canaryPilot">运行 synthetic canary</button><button @click="rolloutPilot">生成 5/25/50/100 灰度</button><button @click="rollbackPilot">触发回滚</button><pre>{{ JSON.stringify(pilot.result,null,2) }}</pre></article>
        <article><h3>合成 UAT</h3><input v-model="uat.title"><button @click="createUat">创建用例</button><button @click="completeUat('PASSED')">记录 synthetic PASS</button><button @click="completeUat('FAILED')">记录 FAIL</button><pre>{{ JSON.stringify(uat.result,null,2) }}</pre></article>
        <article><h3>可选观察窗与最终报告</h3><input v-model="observation.name"><button @click="createObservation">创建 simulated window</button><button @click="recordObservationMetrics">记录完整 synthetic metrics</button><button v-for="role in ['business','technical','security','operations']" :key="role" @click="signObservation(role)">签字 {{ role }}</button><button @click="generateAcceptance">生成最终验收报告</button><p>真实7天观察 deferred_by_user；代码能力保留。真实证据缺失时必须保持 BLOCKED。</p><pre>{{ JSON.stringify({observation:observation.result,acceptance},null,2) }}</pre></article>
      </section>
    </main>
  </div>
</template>
