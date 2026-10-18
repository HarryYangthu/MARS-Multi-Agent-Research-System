type MarkdownNode = {
  type: string;
  value?: string;
  children?: MarkdownNode[];
};

/** Translate implementation terminology in assistant prose at display time. */
export function remarkUserFacingLanguage(): (tree: MarkdownNode) => void {
  function visit(node: MarkdownNode): void {
    if (["code", "inlineCode", "html", "blockquote"].includes(node.type)) return;
    if (node.type === "text" && typeof node.value === "string") {
      node.value = node.value.replaceAll("\u5bbf\u4e3b", "MARS");
    }
    node.children?.forEach(visit);
  }
  return visit;
}
