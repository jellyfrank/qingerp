// Markdown formatting changes for CodeMirror. Return one transaction so undo is atomic.
const MARKERS = { bold: '**', italic: '*', strike: '~~' };

export function sourceFormatChange(source, from, to, kind, placeholder = 'Text') {
    if (kind === 'quote') {
        const start = from ? source.lastIndexOf('\n', from - 1) + 1 : 0;
        const end = source.indexOf('\n', to > from && source[to - 1] === '\n' ? to - 1 : to);
        const stop = end < 0 ? source.length : end;
        const lines = source.slice(start, stop).split('\n');
        const remove = lines.every(line => line.startsWith('>'));
        const insert = lines.map(line => remove ? line.slice(line[1] === ' ' ? 2 : 1) : '> ' + (line || placeholder)).join('\n');
        return { from: start, to: stop, insert, anchor: !source.slice(start, stop) ? start + 2 : start, head: start + insert.length };
    }
    let text = source.slice(from, to), marker = MARKERS[kind];
    if (!marker && kind !== 'code') return null;
    // Spaces outside emphasis delimiters keep CommonMark emphasis valid.
    const lead = text.match(/^\s*/)[0].length, tail = text.match(/\s*$/)[0].length;
    if (text.trim()) { from += lead; to -= tail; text = source.slice(from, to); }
    else { text = placeholder; }
    if (kind === 'code') {
        const runs = text.match(/`+/g) || [];
        marker = '`'.repeat(1 + Math.max(0, ...runs.map(run => run.length)));
    }
    const leftRun = source.slice(0, from).match(/\*+$/)?.[0].length || 0;
    const rightRun = source.slice(to).match(/^\*+/)?.[0].length || 0;
    const surrounding = source.slice(from - marker.length, from) === marker && source.slice(to, to + marker.length) === marker;
    if (surrounding && (kind !== 'italic' || (leftRun % 2 === 1 && rightRun % 2 === 1))) {
        return { from: from - marker.length, to: to + marker.length, insert: text,
            anchor: from - marker.length, head: from - marker.length + text.length };
    }
    if (kind !== 'code' && text.startsWith(marker) && text.endsWith(marker) && text.length > marker.length * 2) {
        const insert = text.slice(marker.length, -marker.length);
        return { from, to, insert, anchor: from, head: from + insert.length };
    }
    const pad = kind === 'code' && /^`|`$/.test(text) ? ' ' : '';
    const insert = marker + pad + text + pad + marker;
    return { from, to, insert, anchor: from + marker.length + pad.length,
        head: from + marker.length + pad.length + text.length };
}
