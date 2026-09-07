export const availabilityLabels = { available: '可问答', pending_review: '待复核', processing: '处理中', failed: '处理失败', cancelled: '已取消', withdrawn: '已撤回', inconsistent: '状态异常', deleted: '已删除', unknown: '未检测' };
export const processingLabels = { DRAFT: '等待处理', CREATED: '等待处理', QUEUED: '等待处理', PROCESSING: '解析中', VALIDATED: '解析完成', FAILED: '处理失败', QUARANTINED: '已隔离', CANCELLED: '已取消', RUNNING: '处理中', RETRY_WAIT: '等待重试', FAILED_FINAL: '处理失败', SUCCEEDED: '处理完成', CANCEL_REQUESTED: '取消中' };
export function fileSize(value = 0) {
  if (!value) return '—';
  const i = Math.min(Math.floor(Math.log(value) / Math.log(1024)), 3);
  return `${(value / 1024 ** i).toFixed(i ? 1 : 0)} ${['B', 'KB', 'MB', 'GB'][i]}`;
}
export function dateTime(value) {
  if (!value) return '尚无更新';
  return new Date(Number(value) * 1000).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false });
}
export function locationLabel(locator = {}) {
  const page = locator.page ?? locator.page_number ?? locator.page_no;
  const section = Array.isArray(locator.section_path) ? locator.section_path.join(' / ') : locator.section_path;
  const sheet = locator.sheet ?? locator.sheet_name;
  return [page != null ? `第 ${page} 页` : '', locator.slide != null ? `幻灯片 ${locator.slide}` : '', locator.heading ?? section ?? '', sheet ? `工作表 ${sheet}` : '', locator.cell_range ?? '', locator.row != null ? `第 ${locator.row} 行` : '', Array.isArray(locator.char_range) ? `字符 ${locator.char_range.join('–')}` : '', locator.start_time != null ? `${locator.start_time}–${locator.end_time ?? '?'} 秒` : ''].filter(Boolean).join(' · ') || '原文片段';
}
export function errorMessage(error) {
  const code = error?.message ?? String(error);
  const messages = { SOURCE_CHUNK_NOT_FOUND: '当前版本中未找到引用片段，请重新生成回答以获取有效来源。', SPACE_NAME_EXISTS: '这个知识库名称已存在，请换一个名称。', CONFLICT_ETAG: '内容状态已更新，请刷新后再操作。', PUBLICATION_QUALITY_NOT_READY: '文档尚未通过质量检查，暂时不能发布。', FORBIDDEN: '当前运行环境不允许执行此操作。', NOT_FOUND: '内容不存在或已不可用。', CONVERSATION_BUSY: '当前对话正在回答，请等待完成或停止当前回答。', SOURCE_REFERENCE_NOT_FOUND: '引用已过期或来源已发生变化，请重新打开对话。', DOC_SIZE_LIMIT: '文件超过上传大小限制。' };
  if (messages[code]) return messages[code];
  if (code === 'REVIEW_PASSED_PUBLICATION_FAILED') return '复核已通过，发布失败。点击“继续发布”可从发布步骤重试。';
  if (error instanceof TypeError || code === 'Failed to fetch') return '无法连接服务，请检查网络后重试。';
  return '操作未完成，请重试或展开技术详情查看原因。';
}
