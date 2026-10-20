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
      node.value = node.value.replace(
        /(^|[。！？；：，、\s（(]|由|交给)系统(?=会|将|正在|已|负责|检查|核对|读取|自动|准备|执行|生成|保存|记录|提供|验证|提交|绑定|确认|启动|创建|收集|计算|保护)/g,
        "$1MARS",
      );
    }
    node.children?.forEach(visit);
  }
  return visit;
}
