// Compare content structure while ignoring serializer-only formatting choices.
export function semanticTree(node, parentType = 'root') {
    if (node.type === 'html' && /^<br\s*\/?>$/i.test(node.value.trim())) return parentType === 'paragraph' ? { type: 'break' } : null;
    if (node.type === 'paragraph' && node.children.every(child =>
        child.type === 'html' && /^<br\s*\/?>$/i.test(child.value.trim()))) return null;
    const result = {};
    for (const key of Object.keys(node).sort()) {
        if (['position', 'spread', 'data'].includes(key)) continue;
        if (key === 'children') result.children = node.children.map(child => semanticTree(child, node.type)).filter(Boolean);
        else if (key === 'value' && node.type === 'text') result.value = node.value.replace(/\s*\n\s*/g, ' ');
        else result[key] = node[key];
    }
    return result;
}

export function preservesContent(remark, source, serialized) {
    const before = semanticTree(remark.parse(source));
    const after = semanticTree(remark.parse(serialized));
    return JSON.stringify(before) === JSON.stringify(after);
}
