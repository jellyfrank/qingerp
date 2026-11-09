import { parseMarkdownStructure } from './outline';

// Keep an existing GFM table whole when a body block is inserted from a cell.
export function sourceInsertionPosition(source, position) {
    let target = position;
    const visit = node => {
        if (node.type === 'table' && position >= node.position.start.offset && position <= node.position.end.offset) {
            target = node.position.end.offset;
            return;
        }
        for (const child of node.children || []) visit(child);
    };
    visit(parseMarkdownStructure(source));
    return target;
}
