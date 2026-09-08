<script setup>
import { ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { Layers, ArrowRight, Eye, EyeOff, ShieldCheck, BookOpen, Quote } from '@lucide/vue';
import { useAuth } from '../stores/auth.js';
const auth = useAuth(), route = useRoute(), router = useRouter();
const username = ref(''), password = ref(''), visible = ref(false), busy = ref(false), error = ref(''), help = ref(false);
async function submit() {
  if (busy.value) return; busy.value = true; error.value = '';
  try { const target = await auth.login(username.value.trim(), password.value); password.value = ''; await router.replace(target); }
  catch (cause) { error.value = cause.status === 429 ? `登录尝试过于频繁，请在 ${Math.ceil((cause.retryAfter || 900) / 60)} 分钟后重试。` : cause.status === 401 ? '账号、密码或账号状态无效，请检查后重试。' : '暂时无法登录，请检查服务连接后重试。'; }
  finally { busy.value = false; }
}
</script>
<template><main class="auth-page"><section class="auth-story">
  <div class="brand"><span class="brand-symbol"><Layers :size="25"/></span><strong>RAG<span>SPACE</span></strong></div>
  <div class="auth-story-main"><span class="eyebrow">YOUR KNOWLEDGE, CONNECTED</span><h1>让每一个问题，<br/>都有据可依。</h1><p>将团队的文档、图表与经验汇聚在一起。<br/>从可靠的知识中，找到清晰的答案。</p>
    <div class="auth-illustration" aria-hidden="true"><div class="auth-orbit orbit-one"/><div class="auth-orbit orbit-two"/><div class="auth-knowledge-node"><BookOpen :size="30"/><span>团队知识</span></div><div class="auth-note"><Quote :size="17"/><span>答案，连接真实来源</span></div><div class="auth-check"><ShieldCheck :size="18"/>按授权访问</div></div>
  </div><p class="auth-story-footer">文档 · 图片 · 流程图 · 有来源的回答</p>
</section><section class="auth-form-side"><div class="auth-card">
  <span class="eyebrow">WELCOME BACK</span><h2>登录知识工作台</h2><p class="muted">使用管理员为你创建的账号登录。</p>
  <p v-if="route.query.reason === 'expired'" class="auth-message">登录已过期，请重新登录。</p><p v-if="route.query.reason === 'password'" class="auth-message">密码已修改，请使用新密码登录。</p>
  <form @submit.prevent="submit"><label for="username" class="field-label">账号</label><input id="username" v-model="username" autocomplete="username" required maxlength="64" placeholder="输入账号" autofocus/>
    <label for="password" class="field-label">密码</label><div class="password-input"><input id="password" v-model="password" :type="visible ? 'text' : 'password'" autocomplete="current-password" required maxlength="128" placeholder="输入密码"/><button type="button" class="icon-button" :aria-label="visible ? '隐藏密码' : '显示密码'" @click="visible = !visible"><component :is="visible ? EyeOff : Eye" :size="18"/></button></div>
    <p v-if="error" class="error-notice" role="alert">{{ error }}</p><button class="btn primary auth-submit" :disabled="busy || !username.trim() || !password">{{ busy ? '正在登录…' : '登录' }}<ArrowRight v-if="!busy" :size="17"/></button>
  </form><button class="text-button auth-help" @click="help = !help">忘记密码？</button><p v-if="help" class="auth-message">请联系总管理员重置密码。收到临时密码后，首次登录需要设置新密码。</p>
  <div class="auth-card-footer"><ShieldCheck :size="16"/><span>登录后仅展示你有权访问的知识库</span></div>
</div></section></main></template>
