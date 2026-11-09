import { unified } from 'unified';
import remarkParse from 'remark-parse';
import remarkGfm from 'remark-gfm';

const parser = unified().use(remarkParse).use(remarkGfm);

export const parseMarkdownStructure = source => parser.parse(source);

function headingText(node) {
    if (node.type === 'image' || node.type === 'imageReference') return node.alt || '';
    if (node.type === 'html') return '';
    if (typeof node.value === 'string') return node.value;
    return (node.children || []).map(headingText).join('');
}

// Source positions come from CommonMark, not a regexp: code and escaped markers
// cannot become headings, while Setext and headings in containers retain offsets.
export function markdownOutline(source) {
    const headings = [];
    const visit = (node) => {
        if (node.type === 'heading') {
            headings.push({
                id: headings.length,
                title: headingText(node).replace(/\s+/g, ' ').trim(),
                depth: node.depth,
                position: node.position.start.offset,
            });
        }
        for (const child of node.children || []) visit(child);
    };
    visit(parseMarkdownStructure(source));
    return headings;
}

export function visualOutline(document) {
    const headings = [];
    document.descendants((node, position) => {
        if (node.type.name !== 'heading') return;
        headings.push({
            id: headings.length, title: node.textContent.trim(), depth: node.attrs.level,
            position: position + 1, nodePosition: position,
        });
    });
    return headings;
}

export function activeHeading(headings, position) {
    let id = null;
    for (const heading of headings) {
        if (heading.position > position) break;
        id = heading.id;
    }
    return id;
}
