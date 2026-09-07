<script setup>
import { onMounted, ref, watch } from 'vue';
import { X } from '@lucide/vue';
const props = defineProps({ open: Boolean, title: String, wide: Boolean });
const emit = defineEmits(['close']);
const dialog = ref();
const sync = () => { if (props.open && !dialog.value.open) dialog.value.showModal(); else if (!props.open && dialog.value.open) dialog.value.close(); };
onMounted(sync); watch(() => props.open, sync);
</script>
<template><dialog ref="dialog" :class="['app-dialog', { wide }]" :aria-label="title" @cancel.prevent="emit('close')" @click="e => { if (e.target === dialog) emit('close'); }"><header><h2>{{ title }}</h2><button class="icon-button" aria-label="关闭" @click="emit('close')"><X :size="20"/></button></header><div class="dialog-body"><slot/></div></dialog></template>
