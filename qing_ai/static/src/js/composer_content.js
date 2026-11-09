/** @odoo-module **/

// Keep the model prompt and saved conversation readable without sending editor HTML.
export function editorToMarkdown(root) {
    const children = (node) => [...node.childNodes].map(convert).join("");
    const convert = (node) => {
        if (node.nodeType === Node.TEXT_NODE) {
            return node.textContent;
        }
        if (node.nodeType !== Node.ELEMENT_NODE) {
            return "";
        }
        const tag = node.tagName.toLowerCase();
        const text = children(node);
        if (["b", "strong"].includes(tag)) return `**${text}**`;
        if (["i", "em"].includes(tag)) return `*${text}*`;
        if (["s", "del", "strike"].includes(tag)) return `~~${text}~~`;
        if (tag === "code") return `\`${text}\``;
        if (tag === "br") return "\n";
        if (tag === "a") {
            const href = node.getAttribute("href") || "";
            return /^https?:\/\//i.test(href) ? `[${text}](${href})` : text;
        }
        if (tag === "li") return `${text.trim()}\n`;
        if (tag === "ul" || tag === "ol") {
            return [...node.children].map((item, index) =>
                `${tag === "ul" ? "-" : `${index + 1}.`} ${convert(item).trim()}\n`
            ).join("") + "\n";
        }
        if (tag === "blockquote") return text.trim().split("\n").map(line => `> ${line}`).join("\n") + "\n\n";
        if (["div", "p"].includes(tag)) return `${text}\n`;
        return text;
    };
    return children(root).replace(/\n{3,}/g, "\n\n").trim();
}
