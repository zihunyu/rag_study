// Keep review readiness, progress and field errors on the same rules.
function substantive(value) {
  if (Array.isArray(value)) return value.map(substantive);
  if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort()
    .filter(key => !['uncertainties', 'review_status', 'bbox_basis', 'bbox'].includes(key))
    .map(key => [key, substantive(value[key])]));
  return value;
}

export function reviewTargetValue(extraction, path) {
  return path.split('/').reduce((value, key) => value?.[key], extraction);
}

export function reviewProblems(issue, original, draft) {
  const problems = [];
  const add = (field, message, invalid = false) => problems.push({ field, message, invalid });
  if (!issue.disposition) add('disposition', '请选择处理结果。');
  const reason = issue.reason.trim();
  if (reason.length < 2) add('reason', `核对说明至少需要 2 个字符，当前 ${reason.length} 个。请说明原图位置和核对依据。`, !!reason);
  if (['corrected', 'excluded'].includes(issue.disposition)) {
    if (!issue.targets.length) add('targets', issue.disposition === 'corrected' ? '请选择实际修改的字段或对象。' : '请选择需要排除的关系对象。');
    else if (issue.targets.some(path => reviewTargetValue(draft, path) === undefined)) add('targets', '关联对象已变化，请重新选择。', true);
    else if (issue.disposition === 'corrected') {
      if (issue.targets.some(path => JSON.stringify(substantive(reviewTargetValue(original, path))) === JSON.stringify(substantive(reviewTargetValue(draft, path))))) {
        add('targets', '选择了“已经修正”，但关联字段尚未实际修改；原识别正确时请选择“已核对，原识别正确”。', true);
      }
      const scope = issue.scope || '';
      if (/^(graphs|tables)(\/|$)/.test(scope) && !issue.targets.some(path => path === scope || path.startsWith(scope + '/') ||
        (path === scope.split('/')[0] && (original[path] || []).length !== (draft[path] || []).length))) {
        add('targets', '关联字段与该问题所属的图或表不一致，请重新选择。', true);
      }
    } else if (issue.targets.some(path => reviewTargetValue(draft, path)?.review_status !== 'excluded')) {
      add('targets', '所选对象尚未排除，请先点击“排除选中对象及关联连线”。', true);
    }
  }
  return problems;
}
