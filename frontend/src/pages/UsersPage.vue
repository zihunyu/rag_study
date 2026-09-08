<script setup>
import { computed, onMounted, ref } from 'vue';
import { Plus, Search, ShieldCheck, Users, KeyRound, SlidersHorizontal } from '@lucide/vue';
import { request } from '../api.js';
import { useAuth } from '../stores/auth.js';
import AppDialog from '../components/AppDialog.vue';
import ErrorNotice from '../components/ErrorNotice.vue';
import EmptyState from '../components/EmptyState.vue';
const auth = useAuth();
const rows = ref([]), q = ref(''), status = ref(''), loading = ref(false), error = ref(null), next = ref(null);
const editing = ref(null), form = ref({}), busy = ref(false), secret = ref(''), confirmation = ref(null);
let loadRevision = 0, grantRevision = 0;
const grants = ref(null), assignments = ref([]), results = ref([]);
const changed = computed(() => assignments.value.filter(s => s.assigned_role !== s.original_role));
const roleName = role => ({ manager: '知识库管理员', qa: '仅问答', none: '无权限' }[role] || role);
async function load(more = false) {
  const own = ++loadRevision; loading.value = true; error.value = null;
  try { const data = await request(`/admin/users?${new URLSearchParams({ q: q.value, ...(status.value === '' ? {} : { enabled: status.value }), offset: more ? next.value : 0 })}`); if (own !== loadRevision) return; rows.value = more ? [...rows.value, ...data.items] : data.items; next.value = data.next_offset; }
  catch (cause) { if (own === loadRevision) error.value = cause; } finally { if (own === loadRevision) loading.value = false; }
}
function edit(user = null) { editing.value = user || { new: true }; form.value = { username: user?.username || '', display_name: user?.display_name || '', global_role: user?.global_role || 'member' }; }
async function saveUser() {
  busy.value = true; error.value = null;
  try { if (editing.value.new) { const data = await request('/admin/users', { method: 'POST', body: JSON.stringify(form.value) }); secret.value = data.temporary_password; }
    else await request(`/admin/users/${editing.value.id}`, { method: 'PATCH', headers: { 'If-Match': String(editing.value.row_version) }, body: JSON.stringify({ display_name: form.value.display_name, global_role: form.value.global_role }) });
    editing.value = null; await load(); await auth.refresh();
  } catch (cause) { error.value = cause; } finally { busy.value = false; }
}
async function confirmedAction() {
  busy.value = true; error.value = null; const { user, action } = confirmation.value;
  try { if (action === 'reset') { const data = await request(`/admin/users/${user.id}:reset-password`, { method: 'POST', headers: { 'If-Match': String(user.row_version) } }); secret.value = data.temporary_password; }
    else await request(`/admin/users/${user.id}`, { method: 'PATCH', headers: { 'If-Match': String(user.row_version) }, body: JSON.stringify({ enabled: !user.enabled }) });
    confirmation.value = null; await load();
  } catch (cause) { error.value = cause; } finally { busy.value = false; }
}
async function openGrants(user) { const own = ++grantRevision; error.value = null; grants.value = user; assignments.value = []; results.value = [];
  try { const data = await request(`/admin/users/${user.id}/spaces`); if (own !== grantRevision) return; assignments.value = data.items.filter(s => !s.deleted).map(s => ({ ...s, original_role: s.assigned_role })); }
  catch (cause) { error.value = cause; }
}
async function saveGrants() {
  busy.value = true; results.value = [];
  for (const row of [...changed.value]) {
    try { const result = await request(`/admin/users/${grants.value.id}/spaces`, { method: 'PUT', headers: { 'If-Match': String(row.row_version) }, body: JSON.stringify({ space_id: row.id, role: row.assigned_role }) }); row.row_version = result.row_version; row.original_role = row.assigned_role; results.value.push(`${row.name}：已保存`); }
    catch (cause) { error.value = cause; results.value.push(`${row.name}：保存失败，请重新加载分配状态`); break; }
  }
  busy.value = false; await load();
}
onMounted(() => load());
</script>
<template><div class="page"><div class="page-heading"><div><span class="eyebrow">PEOPLE & ACCESS</span><h1>用户管理</h1><p class="page-description">管理团队账号，为每个人分配合适的知识范围。</p></div><button class="btn primary" @click="edit()"><Plus :size="17"/>创建账号</button></div>
<form class="account-toolbar" @submit.prevent="load()"><label class="search-field"><Search :size="16"/><input v-model="q" placeholder="搜索账号或姓名" aria-label="搜索用户"/></label><select v-model="status" aria-label="账号状态" @change="load()"><option value="">全部状态</option><option value="1">已启用</option><option value="0">已停用</option></select><button class="btn">搜索</button></form><ErrorNotice :error="error"/>
<div v-if="loading && !rows.length" class="skeleton-card"/><section v-else-if="rows.length" class="panel table-scroll"><table class="data-table"><thead><tr><th>用户</th><th>身份</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="user in rows" :key="user.id"><td><strong>{{ user.display_name }}</strong><small class="account-row-details">{{ user.username }}</small></td><td><span class="role-chip">{{ user.global_role === 'super_admin' ? '总管理员' : '按知识库分配' }}</span></td><td>{{ user.enabled ? '已启用' : '已停用' }}<small v-if="user.must_change_password" class="account-row-details">首次登录需改密</small></td><td><div class="account-actions"><button class="btn small" @click="edit(user)">编辑</button><button v-if="user.global_role !== 'super_admin'" class="btn small" @click="openGrants(user)"><SlidersHorizontal :size="14"/>分配知识库</button><button v-if="user.id !== auth.user.id" class="btn small" @click="confirmation = { user, action: 'reset' }"><KeyRound :size="14"/>重置密码</button><button class="btn small" @click="confirmation = { user, action: 'toggle' }">{{ user.enabled ? '停用' : '启用' }}</button></div></td></tr></tbody></table><footer class="table-footer"><span>普通账号的权限分别在每个知识库中生效。</span><button v-if="next != null" class="btn small" :disabled="loading" @click="load(true)">加载更多</button></footer></section><EmptyState v-else title="没有符合条件的用户" description="调整账号名称或状态筛选。"/>
<AppDialog :open="!!editing" :title="editing?.new ? '创建账号' : '编辑账号'" @close="!busy && (editing = null)"><form @submit.prevent="saveUser"><label class="field-label" for="account-username">账号</label><input id="account-username" v-model="form.username" :disabled="!editing?.new" required minlength="3" maxlength="64" pattern="[a-zA-Z0-9][a-zA-Z0-9_.@-]{2,63}" placeholder="3–64 位字母、数字或 . _ @ -"/><label class="field-label" for="account-name">姓名</label><input id="account-name" v-model="form.display_name" required maxlength="100"/><label class="field-label" for="account-role">全局身份</label><select id="account-role" v-model="form.global_role"><option value="member">普通账号 · 按知识库分配权限</option><option value="super_admin">总管理员 · 全部账号和知识库</option></select><p v-if="form.global_role === 'super_admin'" class="auth-message">该账号将拥有全部知识库和用户管理权限，并可在审计页查看他人问答。</p><p v-if="editing?.new" class="small muted">创建后显示一次临时密码。用户首次登录时必须设置新密码。</p><ErrorNotice :error="error"/><div class="dialog-actions"><button type="button" class="btn" :disabled="busy" @click="editing = null">取消</button><button class="btn primary" :disabled="busy">{{ busy ? '保存中…' : '保存账号' }}</button></div></form></AppDialog>
<AppDialog :open="!!secret" title="请妥善交付临时密码" @close="secret = ''"><p>临时密码仅显示这一次，24 小时内有效。请通过可信方式交给账号使用者。</p><code class="temporary-password">{{ secret }}</code><p class="small muted">首次登录必须修改密码；如果未保存，可重新重置。</p><div class="dialog-actions"><button class="btn primary" @click="secret = ''">已记录，关闭</button></div></AppDialog>
<AppDialog :open="!!confirmation" :title="confirmation?.action === 'reset' ? '重置用户密码' : confirmation?.user.enabled ? '停用账号' : '启用账号'" @close="!busy && (confirmation = null)"><p>{{ confirmation?.user.display_name }}（{{ confirmation?.user.username }}）</p><p class="muted">{{ confirmation?.action === 'reset' ? '重置将撤销该账号所有登录状态，并生成新的临时密码。' : confirmation?.user.enabled ? '停用后将退出所有设备，不能继续访问知识库。资料和历史记录会保留。' : '启用后，用户可重新登录并访问原来分配的知识库。' }}</p><ErrorNotice :error="error"/><div class="dialog-actions"><button class="btn" :disabled="busy" @click="confirmation = null">取消</button><button class="btn primary" :disabled="busy" @click="confirmedAction">确认</button></div></AppDialog>
<AppDialog :open="!!grants" :title="`分配知识库 · ${grants?.display_name || ''}`" wide @close="!busy && (grants = null)"><p class="muted">同一个人可以管理 A 库，同时只能对 B 库问答。未分配的库不可访问。</p><div class="account-grants"><div v-for="space in assignments" :key="space.id" class="account-grant-row"><div><strong>{{ space.name }}</strong><small class="account-row-details">{{ space.description || '团队知识库' }}</small></div><select v-model="space.assigned_role" :aria-label="`${space.name}权限`" :disabled="busy"><option value="none">无权限</option><option value="qa">仅问答</option><option value="manager">知识库管理员</option></select></div></div><div v-if="changed.length" class="permission-diff"><strong>本次修改 {{ changed.length }} 个知识库</strong><p v-for="space in changed" :key="space.id">{{ space.name }}：{{ roleName(space.original_role) }} → {{ roleName(space.assigned_role) }}</p></div><p v-for="line in results" :key="line" class="small">{{ line }}</p><ErrorNotice :error="error"/><div class="dialog-actions"><button class="btn" :disabled="busy" @click="openGrants(grants)">重新加载</button><button class="btn primary" :disabled="busy || !changed.length" @click="saveGrants">{{ busy ? '保存中…' : '确认分配' }}</button></div></AppDialog>
</div></template>
