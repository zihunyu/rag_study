<script setup>
import { computed, ref } from "vue";
import { apiUrl, askStream, request, sourceUrl } from "./api.js";

const tab = ref("ask");
const question = ref("");
const stage = ref("等待输入");
const result = ref(null);
const searchQuery = ref("");
const searchResult = ref(null);
const auditEvents = ref([]);
const error = ref("");
const lifecycle = ref({ documentId: "", versionId: "", targetRevision: 2, watermark: 1 });
const versionUpload = ref({ documentRowVersion: "", file: null, sha256: "", status: null });
const cleanup = ref(null);
const feedback = ref({ rating: 5, comment: "" });
const quality = ref(null);
const documentReview = ref({ decision: "APPROVED", comment: "", result: null });
const operations = ref({ diagnostics: null, alerts: [] });
const pilot = ref({ id: "", name: "Synthetic Pilot", flag: "pilot.synthetic", revision: 0, result: null });
const uat = ref({ id: "", title: "Synthetic UAT", rowVersion: 0, evidence: null, result: null });
const observation = ref({ id: "", name: "Synthetic optional window", rowVersion: 0, result: null });
const acceptance = ref(null);
const citations = computed(() =>
  (result.value?.citations ?? []).map((citation) => ({
    ...citation,
    href: sourceUrl(citation.source_url),
  })),
);

async function ask() {
  if (!question.value.trim()) return;
  error.value = ""; stage.value = "检索、生成缓冲与验证中"; result.value = null;
  try {
    result.value = await askStream(question.value, (current) => { stage.value = current; });
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
  searchResult.value = await request("/search", { method: "POST", body: JSON.stringify({ query: searchQuery.value }) });
}

const idempotency = (action) => `${action}-${crypto.randomUUID()}`;
async function publish() {
  cleanup.value = await request(`/document-versions/${lifecycle.value.versionId}:publish`, { method: "POST", headers: { "Idempotency-Key": idempotency("publish") } });
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
  if (!file) return;
  const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  versionUpload.value.sha256 = [...new Uint8Array(digest)]
    .map((value) => value.toString(16).padStart(2, "0"))
    .join("");
}
async function loadDocumentVersionEtag() {
  const document = await request(`/documents/${lifecycle.value.documentId}`);
  versionUpload.value.documentRowVersion = String(document.row_version);
}
async function uploadNewVersion() {
  const file = versionUpload.value.file;
  if (!file || !versionUpload.value.documentRowVersion) return;
  const createdResponse = await fetch(
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
  const uploadedResponse = await fetch(apiUrl(created.upload_path), {
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
  quality.value = await request(`/document-versions/${lifecycle.value.versionId}/quality-report`);
}
async function submitDocumentReview() {
  documentReview.value.result = await request(
    `/document-versions/${lifecycle.value.versionId}/review`,
    {
      method: "POST",
      headers: { "Idempotency-Key": idempotency("document-review") },
      body: JSON.stringify({
        decision: documentReview.value.decision,
        comment: documentReview.value.comment,
      }),
    },
  );
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
    <aside><h1>RAG KB <small>IMPLEMENTATION</small></h1><button v-for="name in ['ask','admin','debug','governance']" :key="name" :class="{active: tab===name}" @click="tab=name">{{ {ask:'可信问答',admin:'知识管理',debug:'检索调试',governance:'试点与验收'}[name] }}</button><p>simulated=true<br>real_acceptance=false<br>final validation deferred</p></aside>
    <main>
      <header><div><span>ENTERPRISE KNOWLEDGE</span><h2>可信问答与治理控制台</h2></div><b>real_acceptance=false</b></header>
      <section v-if="tab==='ask'">
        <article><label>问题</label><textarea v-model="question" rows="4"/><button class="primary" @click="ask">提交可信问答</button><span>{{ stage }}</span></article>
        <article><h3>{{ result?.status ?? '尚未运行' }}</h3><p class="answer">{{ result?.answer ?? '答案仅在引用与权限复核后显示。' }}</p><a v-for="citation in citations" :key="citation.evidence_id" :href="citation.href" target="_blank">{{ citation.evidence_id }} · 签名来源</a><form v-if="result" @submit.prevent="submitFeedback"><select v-model="feedback.rating"><option :value="5">有帮助</option><option :value="1">无帮助</option></select><input v-model="feedback.comment" placeholder="反馈说明"><button>提交反馈</button></form><p class="error">{{ error }}</p></article>
      </section>
      <section v-if="tab==='admin'">
        <article><h3>发布 / 回滚 / 权限 / 删除</h3><input v-model="lifecycle.documentId" placeholder="Document ID"><input v-model="lifecycle.versionId" placeholder="Version ID"><input v-model="lifecycle.targetRevision" type="number" placeholder="ACL revision"><input v-model="lifecycle.watermark" type="number" placeholder="Watermark"><div class="actions"><button @click="publish">发布</button><button @click="rollback">回滚</button><button @click="permissions">权限转换</button><button @click="revoke">撤权</button><button class="danger" @click="removeDocument">删除</button></div></article>
        <article><h3>既有文档新版本</h3><button @click="loadDocumentVersionEtag">读取 Document row version</button><input v-model="versionUpload.documentRowVersion" placeholder="If-Match row version"><input type="file" @change="selectVersionFile"><button @click="uploadNewVersion">上传不可变新版本</button><p>PROCESSING 不可发布；Worker 验证为 STAGED 后再使用上方“发布”。</p><pre>{{ JSON.stringify(versionUpload.status,null,2) }}</pre></article>
        <article><h3>单文档质量复核</h3><button @click="loadQuality">读取质量报告</button><select v-model="documentReview.decision"><option>APPROVED</option><option>NEEDS_REWORK</option><option>REJECTED</option></select><input v-model="documentReview.comment" placeholder="复核说明"><button @click="submitDocumentReview">提交复核</button><pre>{{ JSON.stringify({quality,review:documentReview.result},null,2) }}</pre></article>
        <article><h3>清理 Outbox 状态</h3><button @click="runLocalCleanup">运行受控本地清理</button><p>MySQL / Redis / Zilliz 需要外部授权，保持 PENDING_APPROVAL。</p><pre>{{ JSON.stringify(cleanup,null,2) }}</pre></article>
        <article><h3>追加式审计</h3><button @click="loadAudit">刷新</button><pre>{{ JSON.stringify(auditEvents,null,2) }}</pre></article>
      </section>
      <section v-if="tab==='debug'"><article><h3>独立检索调试</h3><input v-model="searchQuery" placeholder="查询"><button @click="search">运行 /search</button><pre>{{ JSON.stringify(searchResult,null,2) }}</pre></article></section>
      <section v-if="tab==='governance'">
        <article><h3>运行诊断与告警</h3><button @click="loadOperations">刷新诊断</button><pre>{{ JSON.stringify(operations,null,2) }}</pre></article>
        <article><h3>Pilot Go/No-Go 与灰度</h3><input v-model="pilot.name"><input v-model="pilot.flag"><button @click="createPilot">创建 simulated pilot</button><button v-for="role in ['technical','security','sre']" :key="role" @click="signPilot(role)">签字 {{ role }}</button><button @click="evaluatePilot">评估</button><button @click="canaryPilot">运行 synthetic canary</button><button @click="rolloutPilot">生成 5/25/50/100 灰度</button><button @click="rollbackPilot">触发回滚</button><pre>{{ JSON.stringify(pilot.result,null,2) }}</pre></article>
        <article><h3>合成 UAT</h3><input v-model="uat.title"><button @click="createUat">创建用例</button><button @click="completeUat('PASSED')">记录 synthetic PASS</button><button @click="completeUat('FAILED')">记录 FAIL</button><pre>{{ JSON.stringify(uat.result,null,2) }}</pre></article>
        <article><h3>可选观察窗与最终报告</h3><input v-model="observation.name"><button @click="createObservation">创建 simulated window</button><button @click="recordObservationMetrics">记录完整 synthetic metrics</button><button v-for="role in ['business','technical','security','operations']" :key="role" @click="signObservation(role)">签字 {{ role }}</button><button @click="generateAcceptance">生成最终验收报告</button><p>真实7天观察 deferred_by_user；代码能力保留。真实证据缺失时必须保持 BLOCKED。</p><pre>{{ JSON.stringify({observation:observation.result,acceptance},null,2) }}</pre></article>
      </section>
    </main>
  </div>
</template>
