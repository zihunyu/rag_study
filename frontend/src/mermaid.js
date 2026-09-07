import mermaid from 'mermaid';
mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', suppressErrorRendering: true,
  theme: 'dark', maxTextSize: 120000, maxEdges: 1400, htmlLabels: false,
  flowchart: { useMaxWidth: true, curve: 'linear' },
  secure: ['securityLevel', 'startOnLoad', 'maxTextSize', 'maxEdges', 'htmlLabels'],
});
let queue = Promise.resolve(), sequence = 0;
export function renderDiagram(code) {
  const task = queue.catch(() => {}).then(async () => {
    await mermaid.parse(code);
    const svg = (await mermaid.render(`visual-diagram-${++sequence}`, code)).svg;
    const document = new DOMParser().parseFromString(svg, 'image/svg+xml');
    // Mermaid's SVG text renderer can leave its numeric label escapes literal.
    // Decode text nodes once; never interpret them as HTML or SVG instructions.
    const walker = document.createTreeWalker(document.documentElement, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const node = walker.currentNode;
      if (!node.parentElement?.closest('text')) continue;
      node.nodeValue = node.nodeValue.replace(/&#(\d+);/g, (match, digits) => {
        const value = Number(digits);
        return value >= 32 && value <= 0x10ffff ? String.fromCodePoint(value) : match;
      });
    }
    return new XMLSerializer().serializeToString(document.documentElement);
  });
  queue = task;
  return task;
}
