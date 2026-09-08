<script setup>
import { ref } from 'vue';
import { useRouter } from 'vue-router';
import { KeyRound, Eye, EyeOff } from '@lucide/vue';
import { useAuth } from '../stores/auth.js';
const auth = useAuth(), router = useRouter();
const current = ref(''), password = ref(''), confirm = ref(''), visible = ref(false), busy = ref(false), error = ref('');
async function save() {
  error.value = ''; if (password.value !== confirm.value) { error.value = '两次输入的新密码不一致。'; return; }
  if (busy.value) return; busy.value = true;
  try { await auth.changePassword(current.value, password.value); current.value = password.value = confirm.value = ''; await router.replace('/login?reason=password'); }
  catch (cause) { error.value = cause.status === 401 ? '当前密码不正确或登录已过期。' : cause.message === 'NEW_PASSWORD_MUST_DIFFER' ? '新密码不能与当前密码相同。' : '修改失败，请检查密码长度并重试。'; }
  finally { busy.value = false; }
}
</script>
<template><main class="password-page"><form class="panel password-card" @submit.prevent="save"><span class="library-symbol"><KeyRound :size="26"/></span><h1>{{ auth.user?.must_change_password ? '设置你的新密码' : '修改密码' }}</h1><p class="muted">{{ auth.user?.must_change_password ? '临时密码仅用于首次登录，请先设置自己的密码。' : '修改后，所有设备需要使用新密码重新登录。' }}</p><label class="field-label" for="current-password">当前密码</label><input id="current-password" v-model="current" :type="visible ? 'text' : 'password'" autocomplete="current-password" required maxlength="128"/><label class="field-label" for="new-password">新密码 · 15–128 个字符</label><input id="new-password" v-model="password" :type="visible ? 'text' : 'password'" autocomplete="new-password" required minlength="15" maxlength="128"/><label class="field-label" for="confirm-password">再次输入新密码</label><input id="confirm-password" v-model="confirm" :type="visible ? 'text' : 'password'" autocomplete="new-password" required minlength="15" maxlength="128"/><button type="button" class="text-button auth-help" @click="visible = !visible"><component :is="visible ? EyeOff : Eye" :size="16"/>{{ visible ? '隐藏密码' : '显示密码' }}</button><p v-if="error" role="alert" class="error-notice">{{ error }}</p><button class="btn primary auth-submit" :disabled="busy">{{ busy ? '正在保存…' : '保存新密码' }}</button><RouterLink v-if="!auth.user?.must_change_password" :to="auth.home()" class="back-link">返回工作台</RouterLink><button v-else type="button" class="text-button auth-help" @click="auth.logout(); router.replace('/login')">退出登录</button></form></main></template>
