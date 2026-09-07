<script setup>
import { availabilityLabels, processingLabels } from '../format.js';
defineProps({ value: String, status: String, kind: String, processing: Boolean, label: String });
</script>
<template><span class="status-badge" :data-status="value || status"><i aria-hidden="true" />{{ label || ((processing || kind === 'processing') ? processingLabels[value || status] : availabilityLabels[value || status]) || '未检测' }}</span></template>
