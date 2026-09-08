<script setup>
import { ref } from 'vue';
import { useRouter } from 'vue-router';
import { request } from '../api.js';
import { useWorkspace } from '../stores/workspace.js';
import { useAuth } from '../stores/auth.js';
import AppDialog from './AppDialog.vue';
import ErrorNotice from './ErrorNotice.vue';
const props = defineProps({ space: Object });
const open = ref(false), typed = ref(''), busy = ref(false), error = ref(null);
const workspace = useWorkspace(), auth = useAuth(), router = useRouter();
let key = '';
async function remove() { busy.value = true; error.value = null;
  try { await request(`/spaces/${props.space.id}:delete`, { method: 'POST', headers: { 'If-Match': String(props.space.row_version), 'Idempotency-Key': key ||= crypto.randomUUID() } }); open.value = false; await auth.refresh(); await workspace.refresh(); await router.push('/knowledge-bases'); }
  catch (cause) { error.value = cause; } finally { busy.value = false; }
}
</script>
<template><section class="panel settings-panel"><h2>删除知识库</h2><p class="muted">删除后所有成员停止访问，原文件、版本和审核记录保留。可在知识库回收站恢复。</p><button class="btn danger" @click="open = true; typed = ''; key = ''">删除这个知识库</button><AppDialog :open="open" title="确认删除知识库" @close="!busy && (open = false)"><p>你将删除“{{ space?.name }}”，所有成员将无法继续使用这个知识库问答。</p><label class="field-label">输入知识库名称确认</label><input v-model="typed" autocomplete="off" :placeholder="space?.name"/><ErrorNotice :error="error"/><div class="dialog-actions"><button class="btn" :disabled="busy" @click="open = false">取消</button><button class="btn danger" :disabled="busy || typed !== space?.name" @click="remove">确认删除</button></div></AppDialog></section></template>
