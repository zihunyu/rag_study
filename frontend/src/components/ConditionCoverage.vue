<script setup>
defineProps({ report: Object });
</script>

<template>
  <div v-if="report?.checked" class="condition-coverage answer-notice" role="status">
    <p v-if="report.missing">仍有 {{ report.missing }} 项关键条件未完整保留，答案未展示。请缩小问题范围后重试。</p>
    <p v-else>关键条件核对：{{ report.covered }} 项已保留<span v-if="report.not_applicable"> · {{ report.not_applicable }} 项与本题无关</span>。</p>
  </div>
</template>
