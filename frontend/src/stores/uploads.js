import { defineStore } from 'pinia';
import { computed, markRaw, ref, watch } from 'vue';
import { apiUrl, request, sourceUrl } from '../api.js';
import { sha256File } from '../fileHash.js';
import { useWorkspace } from './workspace.js';

const pending = new Set(['queued', 'hashing', 'uploading', 'completing']);
export const useUploads = defineStore('uploads', () => {
  let saved = [];
  try { saved = JSON.parse(sessionStorage.getItem('ragkb.uploads') || '[]'); } catch { /* damaged browser state */ }
  if (!Array.isArray(saved)) saved = [];
  const items = ref(saved.map(item => ({ ...item, state: pending.has(item.state) ? 'interrupted' : item.state })));
  const activeCount = computed(() => items.value.filter(item => pending.has(item.state)).length);
  let running = 0;
  watch(items, () => sessionStorage.setItem('ragkb.uploads', JSON.stringify(items.value.map(({ file, ...item }) => item))), { deep: true });
  function enqueue(files, spaceId, documentId = null) {
    const config = useWorkspace().capabilities;
    if (!config) throw new Error('UPLOAD_CAPABILITIES_UNAVAILABLE');
    for (const file of files) {
      const extension = '.' + file.name.split('.').pop().toLowerCase();
      const error = !config.accepted_extensions?.includes(extension) ? '不支持这个文件类型。'
        : file.size > config.max_file_size_bytes ? '文件超过大小限制。' : file.size === 0 ? '不能上传空文件。' : null;
      const interrupted = items.value.find(row => row.state === 'interrupted' && row.filename === file.name && row.size === file.size && row.spaceId === spaceId && (!documentId || row.documentId === documentId));
      if (interrupted && !error) { interrupted.file = markRaw(file); interrupted.state = 'queued'; interrupted.error = null; continue; }
      items.value.unshift({ id: crypto.randomUUID(), file: markRaw(file), filename: file.name,
        size: file.size, spaceId, documentId, state: error ? 'failed' : 'queued', error, progress: 0,
        mime: config.file_mime_types?.[extension] || file.type || 'application/octet-stream',
        createdAt: Date.now() / 1000 });
    }
    pump();
  }
  function pump() {
    while (running < 2) {
      const item = items.value.find(row => row.state === 'queued' && row.file);
      if (!item) break;
      item.state = 'hashing'; running++;
      run(item).finally(() => { running--; pump(); });
    }
  }
  async function run(item) {
    try {
      const digest = await sha256File(item.file, { onProgress: value => { item.progress = value; } });
      if (item.digest && item.digest !== digest) throw new Error('重新选择的文件内容已变化，请使用原文件恢复上传。');
      item.digest = digest;
      const headers = { 'Idempotency-Key': `create-${item.id}` };
      let path = `/spaces/${item.spaceId}/upload-sessions`;
      if (item.documentId) {
        const document = await request(`/documents/${item.documentId}/preview`);
        headers['If-Match'] = `"${document.row_version}"`;
        path = `/documents/${item.documentId}/versions/upload-sessions`;
      }
      const created = await request(path, { method: 'POST', headers, body: JSON.stringify({
        filename: item.filename, expected_size: item.size, expected_sha256: digest, declared_mime: item.mime,
      }) });
      item.uploadSessionId = created.upload_session_id; item.state = 'uploading'; item.progress = 0;
      const status = await request(`/upload-sessions/${item.uploadSessionId}`);
      if (status.state === 'COMPLETED') { submitted(item, status); return; }
      if (status.state === 'UPLOADED') { await complete(item, status.row_version); return; }
      const uploadTarget = new URL(sourceUrl(created.upload_path), location.href);
      const base = new URL(apiUrl('/'), location.href);
      if (uploadTarget.origin !== base.origin || !uploadTarget.pathname.startsWith(base.pathname)) throw new Error('UNTRUSTED_UPLOAD_URL');
      const uploaded = await new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest(); xhr.open('PUT', uploadTarget); xhr.timeout = 300000;
        xhr.setRequestHeader('If-Match', `"${created.row_version}"`);
        xhr.upload.onprogress = event => { if (event.lengthComputable) item.progress = event.loaded / event.total; };
        xhr.onerror = () => reject(new Error('UPLOAD_NETWORK_FAILED'));
        xhr.ontimeout = () => reject(new Error('UPLOAD_TIMEOUT'));
        xhr.onload = () => {
          let result; try { result = JSON.parse(xhr.responseText); } catch { reject(new Error('UPLOAD_RESPONSE_INVALID')); return; }
          if (xhr.status >= 200 && xhr.status < 300) resolve(result); else reject(new Error(result.code || 'UPLOAD_FAILED'));
        };
        xhr.send(item.file);
      });
      await complete(item, uploaded.row_version);
    } catch (error) { item.state = 'failed'; item.error = error.message; }
  }
  function submitted(item, result) { item.documentId = result.document_id || item.documentId; item.versionId = result.document_version_id; item.jobId = result.job_id; item.state = 'submitted'; item.progress = 1; item.file = null; item.error = null; }
  async function complete(item, rowVersion) {
    item.state = 'completing';
    const result = await request(`/upload-sessions/${item.uploadSessionId}:complete`, { method: 'POST', headers: { 'If-Match': `"${rowVersion}"`, 'Idempotency-Key': `complete-${item.id}` } });
    submitted(item, result); await useWorkspace().refresh();
  }
  async function restore() {
    for (const item of items.value.filter(row => row.uploadSessionId && ['interrupted', 'failed'].includes(row.state))) {
      try { const status = await request(`/upload-sessions/${item.uploadSessionId}`); if (status.state === 'COMPLETED') submitted(item, status); else if (status.state === 'UPLOADED') await complete(item, status.row_version); }
      catch (cause) { item.error = cause.message; }
    }
  }
  function retry(item) { if (!item.file || pending.has(item.state)) return; item.error = null; item.state = 'queued'; pump(); }
  function dismissFinished() { items.value = items.value.filter(item => pending.has(item.state)); }
  return { items, activeCount, enqueue, dismissFinished, restore, retry };
});
