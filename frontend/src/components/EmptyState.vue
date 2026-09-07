<script setup>
import { FolderOpen } from '@lucide/vue';
defineProps({ title: String, description: String });
</script>
<template><div class="empty-state"><div class="empty-icon"><FolderOpen :size="28"/></div><h3>{{ title }}</h3><p>{{ description }}</p><slot/></div></template>
