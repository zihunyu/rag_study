<script setup>
import { computed, ref } from 'vue';
import { sourceUrl } from '../api.js';
const props=defineProps({asset:Object, target:Object});
const emit=defineEmits(['select']);
const start=ref(null), end=ref(null), host=ref(null);
const bbox=computed(()=>start.value&&end.value ? [Math.min(start.value[0],end.value[0]),Math.min(start.value[1],end.value[1]),Math.max(start.value[0],end.value[0]),Math.max(start.value[1],end.value[1])] : props.target?.bbox);
const style=computed(()=>bbox.value ? {left:bbox.value[0]*100+'%',top:bbox.value[1]*100+'%',width:(bbox.value[2]-bbox.value[0])*100+'%',height:(bbox.value[3]-bbox.value[1])*100+'%'} : {});
function point(event) { const r=host.value.getBoundingClientRect(); return [Math.max(0,Math.min(1,(event.clientX-r.left)/r.width)),Math.max(0,Math.min(1,(event.clientY-r.top)/r.height))]; }
function down(event) { if(!props.target || event.button>0) return; start.value=point(event); end.value=start.value; host.value.setPointerCapture?.(event.pointerId); }
function move(event) { if(start.value) end.value=point(event); }
function up(event) { if(!start.value) return; end.value=point(event); const selected=bbox.value; if(selected[2]-selected[0]>.003 && selected[3]-selected[1]>.003) emit('select',selected); start.value=null; end.value=null; }
</script>
<template>
<section class="visual-region-picker"><p class="small muted">选择一个节点、分组或连线后，在原图上拖动框选其真实位置。坐标仅标记原图，连接线的框应覆盖箭头及端点。</p><div ref="host" class="visual-image-canvas" :class="{ selecting:target }" @pointerdown.prevent="down" @pointermove="move" @pointerup="up" @pointercancel="start=null;end=null"><img :src="sourceUrl(asset.image_url+'?normalized=true')" :alt="asset.extraction?.title || '复核原图'" draggable="false"/><span v-if="bbox" class="visual-region" :style="style"/></div><p v-if="target" class="small">{{ target.bbox ? (target.bbox_basis==='human'?'已人工标记原图区域':'已有候选区域，请对照原图确认') : '此对象还没有原图区域' }}<button v-if="target.bbox" type="button" class="btn small-button" @click="emit('select',null)">清除区域</button></p></section>
</template>
