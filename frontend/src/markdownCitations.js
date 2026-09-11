// Markdown drops cells beyond the header width. Keep a trailing source marker
// inside its existing final cell; do not alter the stored answer or add sources.
export function normalizeTableCitations(text) {
  let fence = '', table = false, previous = '';
  return text.split('\n').map(line => {
    const marker = line.match(/^ {0,3}(`{3,}|~{3,})(.*)$/);
    if (fence) {
      if (marker && marker[1][0] === fence[0] && marker[1].length >= fence.length && !marker[2].trim()) fence = '';
      return line;
    }
    if (marker) { fence = marker[1]; table = false; previous = ''; return line; }
    if (/^(?: {4}|\t)/.test(line)) { table = false; previous = ''; return line; }
    if (/^\s*\|(?:\s*:?-+:?\s*\|)+\s*$/.test(line) && /^\s*\|/.test(previous)) {
      table = true; previous = line; return line;
    }
    if (!/^\s*\|/.test(line)) table = false;
    previous = line;
    if (!table) return line;
    const end = line.lastIndexOf('|'), suffix = line.slice(end + 1).trim();
    if (end < 0 || line[end - 1] === '\\' || !/^(?:(?:\[E\d+\]|\[\d+\]\(#citation-E\d+\))[ \t]*)+$/.test(suffix)) return line;
    return `${line.slice(0, end)} ${suffix} |`;
  }).join('\n');
}
