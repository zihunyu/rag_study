<script setup>
import { computed } from 'vue';
const props=defineProps({report:{type:Object,default:()=>({})}});
const count=computed(()=>Math.max(0,Number(props.report.checked_images)||0));
const confirmed=computed(()=>(props.report.image_checks || []).some(check=>check.status==='supported'));
</script>
<template><div v-if="count || confirmed" class="reading-image-coverage"><p class="reading-image-summary">{{ count ? `图像复查 ${count} 张（可复用已确认结果）` : '已使用已确认的图片资料' }}</p><details v-if="!count && confirmed"><summary>图像处理统计</summary><p>进入图像复查预算：0 张。已确认的结构化资料可直接复用，这不代表本轮重新调用模型看图。</p></details></div></template>
