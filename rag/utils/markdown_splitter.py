# -*- coding: utf-8 -*-
"""
Markdown 标题层级分块器
专门用于结构化的制度文档（# 标题 → ## 章 → ### 条）

分块策略：
1. 以 ###（三级标题，即"第X条"）为基本分块单位
2. 每个 ### 下的所有内容（段落、表格、列表）作为一个完整的块
3. 如果某个 ### 下内容太长，在内部按空行拆分段落
4. 每个块的内容前面加上"文档名 > 章标题 > 节标题"作为上下文前缀
5. 块的元数据记录：文档名、章标题、节标题、块编号

与滑动窗口分块的区别：
- 滑动窗口：按字符数硬切，可能切断表格、拆断语义单元
- 标题分块：按文档结构切，每个块是完整的一条制度，语义完整
"""
from typing import List, Dict, Any, Optional

# 兼容两种运行方式：
# 1. 作为模块被导入：from rag.utils.markdown_splitter import ...（相对导入）
# 2. 直接运行：python rag/utils/markdown_splitter.py（绝对导入）
try:
    from .logger import info, success, warn, error
except ImportError:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from rag.utils.logger import info, success, warn, error


class MarkdownHeaderSplitter:
    """
    基于 Markdown 标题层级的分块器

    适用场景：结构规范的 Markdown 文档（制度、手册、指南等）
    不适用：纯文本、无标题结构的文档
    """

    def __init__(self, max_chunk_length: int = 800, doc_name: str = ""):
        """
        初始化分块器

        :param max_chunk_length: 单个块最大字符数（超过则在 ### 内部按段落拆分）
        :param doc_name: 文档名称，用于块的元数据和上下文前缀
        """
        self.max_chunk_len = max_chunk_length
        self.doc_name = doc_name
        info(f"MarkdownHeaderSplitter 初始化成功 - max_chunk_length: {max_chunk_length}, doc_name: {doc_name}")

    def split(self, markdown_text: str) -> List[Dict[str, Any]]:
        """
        对 Markdown 文档按标题层级分块

        :param markdown_text: 原始 Markdown 文本
        :return: 列表，每个元素 {id, content, metadata}
        """
        if not markdown_text or not markdown_text.strip():
            warn("输入文本为空，返回空列表")
            return []

        info("开始按 Markdown 标题层级分块...")

        # 按行解析，追踪当前所在的章（##）和节（###）
        lines = markdown_text.split("\n")
        current_chapter = ""   # 当前章标题（## 级别）
        current_section = ""   # 当前节标题（### 级别）
        section_buffer = []    # 当前节的内容行
        chunks = []            # 最终分块结果
        chunk_id = 0

        def flush_section():
            """把当前节的内容归档为一个或多个块"""
            nonlocal chunk_id, section_buffer
            if not section_buffer:
                return

            # 把行合并成文本，去掉首尾空行
            section_text = "\n".join(section_buffer).strip()
            if not section_text:
                section_buffer = []
                return

            # 构造上下文前缀：文档名 > 章 > 节
            prefix_parts = [self.doc_name] if self.doc_name else []
            if current_chapter:
                prefix_parts.append(current_chapter)
            if current_section:
                prefix_parts.append(current_section)
            prefix = " > ".join(prefix_parts)

            # 如果节内容不太长，直接作为一个块
            if len(section_text) <= self.max_chunk_len:
                chunk_content = f"【{prefix}】\n{section_text}"
                chunks.append({
                    "id": chunk_id,
                    "content": chunk_content,
                    "metadata": {
                        "doc_name": self.doc_name,
                        "chapter": current_chapter,
                        "section": current_section,
                        "char_count": len(section_text)
                    }
                })
                chunk_id += 1
            else:
                # 节内容太长：按空行拆分成段落，再组合成块
                chunks.extend(self._split_long_section(
                    section_text, prefix, chunk_id
                ))
                chunk_id = chunks[-1]["id"] + 1 if chunks else chunk_id

            section_buffer = []

        # 逐行解析
        for line in lines:
            stripped = line.strip()

            # 检测一级标题（#）：文档标题，不参与分块，但更新文档名
            if stripped.startswith("# ") and not stripped.startswith("## "):
                # 遇到新的文档标题，先把之前的节归档
                flush_section()
                current_chapter = ""
                current_section = ""
                # 如果没有指定doc_name，用一级标题作为doc_name
                if not self.doc_name:
                    self.doc_name = stripped[2:].strip()
                continue

            # 检测二级标题（##）：章
            if stripped.startswith("## ") and not stripped.startswith("### "):
                # 遇到新的章，先把之前的节归档
                flush_section()
                current_chapter = stripped[3:].strip()
                current_section = ""
                continue

            # 检测三级标题（###）：节（基本分块单位）
            if stripped.startswith("### "):
                # 遇到新的节，先把之前的节归档
                flush_section()
                current_section = stripped[4:].strip()
                # 节标题本身也作为内容的一部分
                section_buffer.append(stripped)
                continue

            # 分隔线（---）：跳过，不作为内容
            if stripped == "---" or stripped == "***":
                continue

            # 普通内容行：加入当前节的缓冲区
            # 如果还没有遇到任何 ###，说明是文档开头的介绍性内容
            # 这些内容也作为一个块（章="前言"，节=""）
            if not current_section and not current_chapter and section_buffer == []:
                current_chapter = "前言"
            section_buffer.append(line)

        # 文档结束，归档最后一个节
        flush_section()

        success(f"Markdown 标题分块完成，共 {len(chunks)} 个块")
        # 打印每个块的概要
        for c in chunks:
            meta = c["metadata"]
            info(f"  块{c['id']}: [{meta['chapter']} > {meta['section']}] {meta['char_count']}字符")

        return chunks

    def _split_long_section(
        self,
        section_text: str,
        prefix: str,
        start_id: int
    ) -> List[Dict[str, Any]]:
        """
        对超长的节内容按空行拆分段落，再组合成块

        :param section_text: 节的完整文本
        :param prefix: 上下文前缀
        :param start_id: 起始块编号
        :return: 块列表
        """
        # 按空行拆分成段落
        paragraphs = [p.strip() for p in section_text.split("\n\n") if p.strip()]

        chunks = []
        current_paragraphs = []
        current_len = 0
        chunk_id = start_id

        for para in paragraphs:
            para_len = len(para)

            # 如果单个段落就超过最大长度，按字符硬切（兜底）
            if para_len > self.max_chunk_len:
                # 先把已有的段落归档
                if current_paragraphs:
                    chunk_content = f"【{prefix}】\n" + "\n\n".join(current_paragraphs)
                    chunks.append({
                        "id": chunk_id,
                        "content": chunk_content,
                        "metadata": {
                            "doc_name": self.doc_name,
                            "chapter": prefix.split(" > ")[1] if " > " in prefix else "",
                            "section": prefix.split(" > ")[2] if " > " in prefix and prefix.count(" > ") >= 2 else "",
                            "char_count": current_len
                        }
                    })
                    chunk_id += 1
                    current_paragraphs = []
                    current_len = 0

                # 硬切超长段落
                for start in range(0, para_len, self.max_chunk_len):
                    sub_para = para[start:start + self.max_chunk_len]
                    chunk_content = f"【{prefix}】\n{sub_para}"
                    chunks.append({
                        "id": chunk_id,
                        "content": chunk_content,
                        "metadata": {
                            "doc_name": self.doc_name,
                            "chapter": "",
                            "section": "",
                            "char_count": len(sub_para)
                        }
                    })
                    chunk_id += 1
                continue

            # 加入这个段落会超出最大长度，先归档当前块
            if current_len + para_len > self.max_chunk_len and current_paragraphs:
                chunk_content = f"【{prefix}】\n" + "\n\n".join(current_paragraphs)
                chunks.append({
                    "id": chunk_id,
                    "content": chunk_content,
                    "metadata": {
                        "doc_name": self.doc_name,
                        "chapter": "",
                        "section": "",
                        "char_count": current_len
                    }
                })
                chunk_id += 1
                current_paragraphs = []
                current_len = 0

            current_paragraphs.append(para)
            current_len += para_len

        # 归档最后一个块
        if current_paragraphs:
            chunk_content = f"【{prefix}】\n" + "\n\n".join(current_paragraphs)
            chunks.append({
                "id": chunk_id,
                "content": chunk_content,
                "metadata": {
                    "doc_name": self.doc_name,
                    "chapter": "",
                    "section": "",
                    "char_count": current_len
                }
            })

        return chunks


# ============================================================================
# 测试代码：直接运行本文件即可测试分块效果
# 运行方式：python rag/utils/markdown_splitter.py
# ============================================================================
if __name__ == "__main__":
    from pathlib import Path

    print("=" * 70)
    print("MarkdownHeaderSplitter 分块器测试")
    print("=" * 70)

    # 测试文件：通用企业报销制度（结构最典型，章→条清晰）
    test_file = Path(__file__).parent.parent / "knowledge_base" / "04-费用报销标准" / "通用企业报销制度.md"

    if not test_file.exists():
        print(f"测试文件不存在: {test_file}")
        print("请检查知识库目录结构")
        exit(1)

    # 读取测试文件
    print(f"\n读取测试文件: {test_file.name}")
    with open(test_file, "r", encoding="utf-8") as f:
        markdown_text = f.read()
    print(f"文件总长度: {len(markdown_text)} 字符")

    # 初始化分块器
    print(f"\n初始化分块器（max_chunk_length=800）...")
    splitter = MarkdownHeaderSplitter(
        max_chunk_length=800,
        doc_name="通用企业报销制度"
    )

    # 执行分块
    print("\n开始分块...")
    chunks = splitter.split(markdown_text)

    # 打印分块结果汇总
    print("\n" + "=" * 70)
    print(f"分块完成！共 {len(chunks)} 个块")
    print("=" * 70)

    # 打印每个块的元数据
    print("\n--- 块列表 ---")
    for i, chunk in enumerate(chunks):
        meta = chunk["metadata"]
        print(f"  块{i:2d} | {meta['chapter']:20s} > {meta['section']:30s} | {meta['char_count']:4d}字符")

    # 详细展示前3个块的完整内容
    print("\n" + "=" * 70)
    print("前3个块的完整内容（用于验证分块质量）")
    print("=" * 70)
    for i, chunk in enumerate(chunks[:3]):
        print(f"\n--- 块 {i} ---")
        print(chunk["content"])
        print("-" * 40)

    # 质量检查
    print("\n" + "=" * 70)
    print("质量检查")
    print("=" * 70)

    # 检查1：每个块都有上下文前缀
    has_prefix = all(chunk["content"].startswith("【") for chunk in chunks)
    print(f"  [{'OK' if has_prefix else 'FAIL'}] 所有块都有上下文前缀")

    # 检查2：每个块都有元数据
    has_meta = all("chapter" in chunk["metadata"] and "section" in chunk["metadata"] for chunk in chunks)
    print(f"  [{'OK' if has_meta else 'FAIL'}] 所有块都有元数据（章/节）")

    # 检查3：块大小分布
    sizes = [chunk["metadata"]["char_count"] for chunk in chunks]
    print(f"  [INFO] 块大小：最小={min(sizes)}，最大={max(sizes)}，平均={sum(sizes)//len(sizes)}")

    # 检查4：关键章节是否完整
    has_hotel = any("住宿费标准" in chunk["metadata"]["section"] for chunk in chunks)
    print(f"  [{'OK' if has_hotel else 'FAIL'}] 包含'住宿费标准'章节")

    print("\n测试完成！")
