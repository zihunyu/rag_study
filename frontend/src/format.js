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
  return [locator.paragraph ? `第 ${locator.paragraph} 段` : '', locator.part ? `图片位置：${locator.part}` : '', page != null ? `第 ${page} 页` : '', locator.slide != null ? `幻灯片 ${locator.slide}` : '', locator.heading ?? section ?? '', sheet ? `工作表 ${sheet}` : '', locator.cell_range ?? '', locator.row != null ? `第 ${locator.row} 行` : '', Array.isArray(locator.char_range) ? `字符 ${locator.char_range.join('–')}` : '', locator.start_time != null ? `${locator.start_time}–${locator.end_time ?? '?'} 秒` : ''].filter(Boolean).join(' · ') || '原文片段';
}
export function errorMessage(error) {
  const code = error?.message ?? String(error);
  const visualMessage = visualIssueMessage(code);
  if (visualMessage !== code) return visualMessage;
  const messages = { LAST_SUPER_ADMIN_REQUIRED: '至少需要保留一个已设置正式密码的有效总管理员。', USERNAME_ALREADY_EXISTS: '这个账号已存在，请使用其他账号名称。', USERNAME_INVALID: '账号需为 3–64 位字母、数字或 . _ @ -。', RESET_SELF_USE_PASSWORD_CHANGE: '请通过右上角的修改密码更新自己的密码。', KNOWLEDGE_BASE_ACCESS_REVOKED: '知识库权限已变更，请刷新页面或联系管理员。', SUPER_ADMIN_REQUIRED: '此操作需要总管理员权限。', PASSWORD_CHANGE_REQUIRED: '请先设置新密码，再进入工作台。', MEMBER_DISABLED: '该账号已停用，请先启用账号。', AUTHENTICATION_REQUIRED: '登录已失效，请重新登录。', VISUAL_REVIEW_REQUIRED: '图片还有未解决的识别问题，请先对照原图核对。', OCR_CONFIGURATION_REQUIRED: 'OCR 尚未配置，请检查独立的 OCR 模型配置。', SOURCE_CHUNK_NOT_FOUND: '当前版本中未找到引用片段，请重新生成回答以获取有效来源。', SPACE_NAME_EXISTS: '这个知识库名称已存在，请换一个名称。', CONFLICT_ETAG: '内容状态已更新，请刷新后再操作。', PUBLICATION_QUALITY_NOT_READY: '文档尚未通过质量检查，暂时不能发布。', FORBIDDEN: '当前账号没有执行此操作的权限。', NOT_FOUND: '内容不存在或已不可用。', CONVERSATION_BUSY: '当前对话正在回答，请等待完成或停止当前回答。', SOURCE_REFERENCE_NOT_FOUND: '引用已过期或来源已发生变化，请重新打开对话。', DOC_SIZE_LIMIT: '文件超过上传大小限制。' };
  if (messages[code]) return messages[code];
  if (code === 'REVIEW_PASSED_PUBLICATION_FAILED') return '复核已通过，发布失败。点击“继续发布”可从发布步骤重试。';
  if (error instanceof TypeError || code === 'Failed to fetch') return '无法连接服务，请检查网络后重试。';
  return '操作未完成，请重试或展开技术详情查看原因。';
}

export function visualIssueMessage(code) {
  return ({
    VISUAL_HISTORICAL_SOURCE_REQUIRES_RESTORE: '所选版本已经不是最新版本，请刷新并切换到最新版本；确需恢复旧原文时，先对照历史恢复差异。',
    VISUAL_ISSUE_RESOLUTIONS_REQUIRED: '原识别问题需要逐项填写处理结果和理由，刷新复核面板后检查遗漏项。',
    VISUAL_REVIEW_CORRECTION_SCOPE_MISMATCH: '修正字段与该问题的所属图表不一致，请关联对应图表内确实改动的内容。',
    VISUAL_REVIEW_CORRECTION_MISSING: '选择了已经修正，但内容没有变化；请修改相应内容，或如实选择已核对原识别正确。',
    VISUAL_REVIEW_EXCLUSION_MISSING: '请明确选择并排除无法确认的对象，相关连接也需要一并排除。',
    VISUAL_UNRESOLVED_UNCERTAINTIES: '仍有待核对内容，请逐项处理或明确排除后再提交。',
    VISUAL_EXCLUDED_EDGE_DEPENDENCY: '已排除对象的关联连线仍未排除，请检查依赖关系。',
    VISUAL_EXCLUDED_GROUP_DEPENDENCY: '已排除分组中仍有保留对象，请检查并排除依赖内容。',
    VISUAL_METADATA_TITLE_INFERRED: '识别说明：展示标题来自上下文，不作为原图可见文字。',
    VISUAL_EDGE_LABEL_ABSENT: '识别说明：原图连线没有可见文字标签。',
    VISUAL_TRANSCRIPTION_VISIBLE_SPELLING: '识别说明：保留原图可见拼写，未自动修改。',
    VISUAL_TABLE_ROW_TOO_LARGE: '表格单行内容过长，暂未入库。请将原表按完整记录分成较小的表格后重新上传。',
    OCR_IMAGE_BYTES_LIMIT: '原图文件超过识别大小限制，请缩小文件后重新上传。',
    OCR_IMAGE_PIXELS_LIMIT: '原图像素超过识别限制，请按完整内容分成较小的图片。',
    OCR_IMAGE_TILE_LIMIT: '图片过长或过大，请按完整内容分成多张图片后重新上传。',
    OCR_NORMALIZED_IMAGE_BYTES_LIMIT: '图片展开后的清晰视图超过请求限制，请分成较小的图片。',
    OCR_ANIMATED_IMAGE_REQUIRES_REVIEW: '动态图暂不能可靠识别，请提供需要读取的静态画面。',
    OCR_STRUCTURE_INVALID: '识别结果的结构不完整，请对照原图重新识别。',
    OCR_RESPONSE_INVALID_OR_INCOMPLETE: '图片识别未返回完整结果，请重试或补充更清晰的图片。',
  })[code] || code;
}

export function visualEvidenceNotice(result = {}) {
  const statuses = new Set((result.warnings || []).filter(code => code.startsWith('VISUAL_EVIDENCE_EXCLUDED:')).map(code => code.split(':')[1]));
  if (statuses.has('conflict')) return '本轮发现图片与旧识别文字存在冲突，已排除相关内容。请对照原图重新识别该文档。';
  if (statuses.has('uncertain') || statuses.has('verification_failed')) return '本轮未能从原图确认部分内容，已排除相关图片证据。请补充清晰原图或重新识别后再试。';
  if (statuses.has('budget_exceeded')) return '本轮需要核对的图片较多，尚未核对的图片未用于回答。可以缩小问题范围后再试。';
  if (statuses.has('not_relevant') && (!result.verified || !result.answer)) return '本轮找到的图片未提供问题所需的信息，已排除相关图片证据。可以补充对象、版本或条件后再试。';
  return '';
}
