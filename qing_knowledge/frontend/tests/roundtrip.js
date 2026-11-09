import assert from 'node:assert/strict';
import test from 'node:test';
import { unified } from 'unified';
import remarkParse from 'remark-parse';
import remarkGfm from 'remark-gfm';
import { preservesContent } from '../src/roundtrip';
import { markdownOutline, visualOutline, activeHeading } from '../src/outline';
import { Schema } from '@milkdown/kit/prose/model';

const parser = unified().use(remarkParse).use(remarkGfm);

test('soft wrapping and list spacing preserve prose', () => {
    assert.ok(preservesContent(parser, 'First line\ncontinues.\n\n- First\n- Second',
        'First line continues.\n\n* First\n\n* Second\n'));
});
test('empty visual paragraphs do not force source mode', () => {
    assert.ok(preservesContent(parser, '## Purpose\n\n## Steps\n',
        '## Purpose\n\n<br />\n\n## Steps\n\n<br />\n'));
});
test('inline breaks remain significant', () => {
    assert.ok(preservesContent(parser, 'First<br />Last', 'First\\\nLast'));
    assert.equal(preservesContent(parser, 'First<br />Last', 'FirstLast'), false);
});
test('GFM table formatting preserves cells and alignment', () => {
    assert.ok(preservesContent(parser, '| A | B |\n| :- | -: |\n| One | Two |',
        '|A|B|\n|:---|---:|\n|One|Two|'));
    assert.equal(preservesContent(parser, '| A | B |\n| - | - |\n| One | Two |',
        '| A | B |\n| - | - |\n| One | Lost |'), false);
});
test('code whitespace and unsupported HTML cannot be discarded', () => {
    assert.equal(preservesContent(parser, '```\n  keep indent\n```', '```\nkeep indent\n```'), false);
    assert.equal(preservesContent(parser, '<details><summary>Note</summary>Keep</details>', 'Keep'), false);
});
test('asset destinations cannot silently change', () => {
    assert.equal(preservesContent(parser, '![Photo](assets/original.png)', '![Photo](assets/other.png)'), false);
});


test('outline keeps duplicate Chinese headings distinct and matches UTF-16 source offsets', () => {
    const source = '前言😀\n\n## 第一周\n\n正文\n\n## 第一周\n';
    const headings = markdownOutline(source);
    assert.deepEqual(headings.map(({ id, title, depth }) => ({ id, title, depth })), [
        { id: 0, title: '第一周', depth: 2 }, { id: 1, title: '第一周', depth: 2 },
    ]);
    assert.equal(headings[0].position, source.indexOf('##'));
    assert.equal(headings[1].position, source.lastIndexOf('##'));
    assert.equal(activeHeading(headings, 0), null);
    assert.equal(activeHeading(headings, headings[1].position), 1);
});

test('outline recognizes Setext, nested headings, inline code, links and image alt text', () => {
    const source = '部署 **准备**\n=========\n\n> ## [第一天](https://example.com) `配置` ![示意图](assets/demo.png)\n\n- ### 收尾\n';
    const headings = markdownOutline(source);
    assert.deepEqual(headings.map(({ title, depth }) => ({ title, depth })), [
        { title: '部署 准备', depth: 1 },
        { title: '第一天 配置 示意图', depth: 2 },
        { title: '收尾', depth: 3 },
    ]);
    assert.equal(source.slice(headings[1].position, headings[1].position + 2), '##');
    assert.equal(source.slice(headings[2].position, headings[2].position + 3), '###');
});

test('outline ignores fenced and indented code, escaped markers and HTML blocks', () => {
    const source = '```markdown\n# Not a heading\n```\n\n~~~\n## Still code\n~~~\n\n    # Indented code\n\n\\# Escaped heading\n\n<div>\n# Raw HTML block\n</div>\n\n## Real heading';
    assert.deepEqual(markdownOutline(source).map(({ title }) => title), ['Real heading']);
});

test('outline offsets refresh after inserting, renaming and deleting headings', () => {
    const initial = '# Old\n\n## Keep';
    const changed = 'Introduction\n\n# Renamed\n\n## Added\n\n## Keep';
    const before = markdownOutline(initial);
    const after = markdownOutline(changed);
    assert.equal(before[1].position, initial.indexOf('##'));
    assert.equal(after[2].position, changed.lastIndexOf('##'));
    assert.equal(after[0].title, 'Renamed');
    assert.equal(activeHeading(after, changed.length), 2);
    assert.deepEqual(markdownOutline('Body without headings'), []);
    assert.equal(activeHeading([], 0), null);
});

test('visual outline uses document positions and excludes code blocks', () => {
    const schema = new Schema({ nodes: {
        doc: { content: 'block+' },
        paragraph: { content: 'inline*', group: 'block' },
        heading: { attrs: { level: { default: 1 } }, content: 'inline*', group: 'block' },
        code_block: { content: 'text*', group: 'block', code: true },
        text: { group: 'inline' },
    } });
    const document = schema.node('doc', null, [
        schema.node('paragraph', null, schema.text('Introduction')),
        schema.node('heading', { level: 2 }, schema.text('重复标题')),
        schema.node('code_block', null, schema.text('# Not a heading')),
        schema.node('heading', { level: 3 }, schema.text('重复标题')),
    ]);
    const headings = visualOutline(document);
    assert.equal(headings.length, 2);
    assert.equal(headings[0].depth, 2);
    assert.equal(headings[1].depth, 3);
    for (const heading of headings) {
        assert.equal(document.resolve(heading.position).parent.type.name, 'heading');
        assert.equal(document.nodeAt(heading.nodePosition).textContent, '重复标题');
        assert.equal(activeHeading(headings, heading.position), heading.id);
    }
});


test('GFM heading labels display text rather than source formatting', () => {
    const [heading] = markdownOutline('## ~~旧流程~~ **新流程** &amp; 检查');
    assert.equal(heading.title, '旧流程 新流程 & 检查');
});

// These assertions compare Markdown structure, not the insertion implementation.
import { sourceInsertionPosition } from '../src/insertion';
import { parseMarkdownStructure } from '../src/outline';

test('inserting a body block from a source table cell preserves every original cell', () => {
    const source = '| 项目 | 内容 |\n| --- | --- |\n| 数量 | 12 |\n| 外观 | 完好 |\n\n后续段落';
    const position = sourceInsertionPosition(source, source.indexOf('12'));
    const edited = source.slice(0, position) + '\n\n> 提醒：核对单据。\n\n' + source.slice(position);
    const before = parseMarkdownStructure(source), after = parseMarkdownStructure(edited);
    const values = table => table.children.map(row => row.children.map(cell => cell.children.map(child => child.value || '').join('')));
    assert.deepEqual(values(after.children[0]), values(before.children[0]));
    assert.equal(after.children.filter(node => node.type === 'table').length, 1);
    assert.equal(after.children[1].type, 'blockquote');
    assert.equal(after.children.at(-1).children[0].value, '后续段落');
});

test('literal pipes in a code block do not move a source insertion to a false table', () => {
    const source = '```text\n| Item | Value |\n| --- | --- |\n| A | 1 |\n```';
    const position = source.indexOf('Value');
    assert.equal(sourceInsertionPosition(source, position), position);
});


import { sourceFormatChange } from '../src/formatting';
const applyFormat = (source, from, to, kind) => {
    const change = sourceFormatChange(source, from, to, kind, '文字');
    return source.slice(0, change.from) + change.insert + source.slice(change.to);
};
test('Chinese emphasis keeps surrounding spaces outside Markdown markers and toggles atomically', () => {
    const source = '前言  重要内容  结尾';
    const result = applyFormat(source, 2, 10, 'bold');
    assert.equal(result, '前言  **重要内容**  结尾');
    const start = result.indexOf('重要');
    assert.equal(applyFormat(result, start, start + 4, 'bold'), source);
    assert.equal(parser.parse(result).children[0].children[1].type, 'strong');
});
test('emphasis, deletion and inline code retain distinct Markdown semantics', () => {
    for (const [kind, type] of [['bold', 'strong'], ['italic', 'emphasis'], ['strike', 'delete'], ['code', 'inlineCode']]) {
        const formatted = applyFormat('待强调内容', 0, 5, kind);
        assert.equal(parser.parse(formatted).children[0].children[0].type, type);
        assert.equal(applyFormat(formatted, formatted.indexOf('待'), formatted.indexOf('待') + 5, kind), '待强调内容');
    }
});
test('inline code handles literal backticks without losing content', () => {
    const text = '`item`';
    const formatted = applyFormat(text, 0, text.length, 'code');
    assert.equal(parser.parse(formatted).children[0].children[0].value, text);
});
test('reminder changes only selected lines and toggles without changing adjacent content', () => {
    const source = '# 原标题\n\n第一点\n第二点\n\n后文';
    const from = source.indexOf('第一'), to = source.indexOf('第二') + 3;
    const result = applyFormat(source, from, to, 'quote');
    assert.equal(result, '# 原标题\n\n> 第一点\n> 第二点\n\n后文');
    assert.equal(applyFormat(result, from, to + 4, 'quote'), source);
});
test('empty source selection inserts and selects editable placeholder', () => {
    assert.deepEqual(sourceFormatChange('', 0, 0, 'bold', '文字'),
        { from: 0, to: 0, insert: '**文字**', anchor: 2, head: 4 });
});

test('source italic inside bold adds emphasis instead of stripping a bold delimiter', () => {
    assert.equal(applyFormat('**重点**', 2, 4, 'italic'), '***重点***');
    assert.equal(applyFormat('***重点***', 3, 5, 'italic'), '**重点**');
});
test('empty source callout keeps editable placeholder inside the quote marker', () => {
    assert.deepEqual(sourceFormatChange('', 0, 0, 'quote', '提醒内容'),
        { from: 0, to: 0, insert: '> 提醒内容', anchor: 2, head: 6 });
});
