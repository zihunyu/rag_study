<script setup>
import { ref } from 'vue';
import { request } from '../api.js';
import { useWorkspace } from '../stores/workspace.js';
import { useAuth } from '../stores/auth.js';
import AppDialog from './AppDialog.vue';
import ErrorNotice from './ErrorNotice.vue';
const opened = ref(false), rows = ref([]), busy = ref(false), error = ref(null);
async function open() { opened.value = true; error.value = null; try { const data = await request('/spaces/overview?include_deleted=true'); rows.value = data.items.filter(s => s.deleted && s.my_role === 'manager'); } catch (cause) { error.value = cause; } }
async function restore(space) { busy.value = true; error.value = null;
  try { await request(`/spaces/${space.id}:restore`, { method: 'POST', headers: { 'If-Match': String(space.row_version), 'Idempotency-Key': space.restoreKey ||= crypto.randomUUID() } }); await useAuth().refresh(); await useWorkspace().refresh(); await open(); }
  catch (cause) { error.value = cause; } finally { busy.value = false; }
}
</script>
<template><button class="btn small" @click="open">知识库回收站</button><AppDialog :open="opened" title="知识库回收站" wide @close="!busy && (opened = false)"><p class="muted">恢复知识库不会重新发布此前单独撤回或删除的文档。</p><ErrorNotice :error="error"/><div v-for="space in rows" :key="space.id" class="account-grant-row"><div><strong>{{ space.name }}</strong><p class="small muted">{{ space.description }}</p></div><button class="btn" :disabled="busy" @click="restore(space)">恢复知识库</button></div><p v-if="!rows.length && !error" class="muted">没有可恢复的知识库。</p></AppDialog></template>
