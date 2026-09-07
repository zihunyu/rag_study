<script setup>
import { computed, ref } from 'vue';
import { UploadCloud, FileText, Check, ArrowUpRight, LoaderCircle } from '@lucide/vue';
import AppDialog from './AppDialog.vue';
import { useUploads } from '../stores/uploads.js';
import { useWorkspace } from '../stores/workspace.js';
import { fileSize } from '../format.js';
const props = defineProps({ open: Boolean, spaceId: String, documentId: String });
defineEmits(['close']);
const uploads = useUploads(), workspace = useWorkspace(), drag = ref(false), picker = ref(), error = ref('');
const rows = computed(() => uploads.items.filter(row => row.spaceId === props.spaceId && (!props.documentId || row.documentId === props.documentId)));
const phases = { queued: '等待上传', hashing: '校验文件', uploading: '上传中', completing: '提交处理任务', submitted: '已提交处理', failed: '上传失败', interrupted: '上传已中断，请重新选择文件' };
function add(files) { error.value = ''; try { uploads.enqueue(props.documentId ? [...files].slice(0, 1) : [...files], props.spaceId, props.documentId); } catch (cause) { error.value = '暂时无法读取上传限制，请刷新页面后重试。'; } }
</script>
<template><AppDialog :open="open" :title="documentId ? '上传新版本' : '上传文档'" wide @close="$emit('close')">
  <p class="muted">{{ documentId ? '新版本发布前，当前已发布版本继续提供问答。' : '文件上传后自动解析与构建索引，检查质量后即可发布。' }}</p>
  <input ref="picker" class="sr-only" type="file" :multiple="!documentId" :accept="workspace.capabilities?.accepted_extensions?.join(',')" aria-label="选择上传文件" data-testid="upload-files" @change="event => { add(event.target.files); event.target.value = ''; }"/>
  <button class="dropzone" :class="{ dragging: drag }" :disabled="!workspace.capabilities" @click="picker.click()" @dragover.prevent="drag = true" @dragleave="drag = false" @drop.prevent="event => { drag = false; add(event.dataTransfer.files); }"><span class="upload-symbol"><UploadCloud :size="30"/></span><strong>拖拽文件到这里，或<span class="accent">选择文件</span></strong><span>文档、表格、演示文稿、图片与文本</span><small>单文件上限 {{ fileSize(workspace.capabilities?.max_file_size_bytes) }} · 最多同时上传 2 个文件</small></button>
  <p v-if="error" class="error-text" role="alert">{{ error }}</p>
  <div v-if="rows.length" class="upload-list"><div v-for="row in rows" :key="row.id" class="upload-row"><FileText :size="20" class="muted"/><div class="upload-row-main"><strong>{{ row.filename }}</strong><small>{{ fileSize(row.size) }} · {{ phases[row.state] }}</small><progress v-if="['hashing','uploading'].includes(row.state)" :value="row.progress" max="1"/><p v-if="row.error" class="error-text">{{ row.error }}</p></div><button v-if="row.state === 'failed' && row.file" class="btn small" @click="uploads.retry(row)">重试上传</button><Check v-if="row.state === 'submitted'" :size="18" class="success-text"/><LoaderCircle v-else-if="['hashing','uploading','completing'].includes(row.state)" :size="18" class="spin"/><RouterLink v-if="row.documentId && row.state === 'submitted'" :to="`/knowledge-bases/${spaceId}/documents/${row.documentId}`" class="icon-button" aria-label="查看已上传文档" @click="$emit('close')"><ArrowUpRight :size="17"/></RouterLink></div></div>
  <div class="dialog-actions"><span class="muted small">关闭此窗口后，上传继续进行</span><button class="btn" @click="$emit('close')">完成</button></div>
</AppDialog></template>
