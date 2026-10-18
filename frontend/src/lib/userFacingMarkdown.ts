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
      node.value = node.value.replaceAll("宿主", "系统");
    }
    node.children?.forEach(visit);
  }
  return visit;
}
