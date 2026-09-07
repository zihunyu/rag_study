<script setup>
import { AlertCircle, RotateCw } from '@lucide/vue';
import { errorMessage } from '../format.js';
defineProps({ error: [Object, String], retry: Boolean });
defineEmits(['retry']);
</script>
<template><div v-if="error" class="error-notice" role="alert"><AlertCircle :size="18"/><div><p>{{ errorMessage(error) }}</p><details><summary>技术详情</summary><code>{{ error.message || error }}{{ error.requestId ? ` · 请求 ${error.requestId}` : '' }}</code></details></div><button v-if="retry" class="btn small" @click="$emit('retry')"><RotateCw :size="14"/>重试</button></div></template>
