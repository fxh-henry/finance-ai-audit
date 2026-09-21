# -*- coding: utf-8 -*-
"""追加测试代码到markdown_splitter.py末尾"""

path = r'D:\agent\财务报销项目\rag\utils\markdown_splitter.py'

test_code = '''

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
    print(f"\\n读取测试文件: {test_file.name}")
    with open(test_file, "r", encoding="utf-8") as f:
        markdown_text = f.read()
    print(f"文件总长度: {len(markdown_text)} 字符")

    # 初始化分块器
    print(f"\\n初始化分块器（max_chunk_length=800）...")
    splitter = MarkdownHeaderSplitter(
        max_chunk_length=800,
        doc_name="通用企业报销制度"
    )

    # 执行分块
    print("\\n开始分块...")
    chunks = splitter.split(markdown_text)

    # 打印分块结果汇总
    print("\\n" + "=" * 70)
    print(f"分块完成！共 {len(chunks)} 个块")
    print("=" * 70)

    # 打印每个块的元数据
    print("\\n--- 块列表 ---")
    for i, chunk in enumerate(chunks):
        meta = chunk["metadata"]
        print(f"  块{i:2d} | {meta['chapter']:20s} > {meta['section']:30s} | {meta['char_count']:4d}字符")

    # 详细展示前3个块的完整内容
    print("\\n" + "=" * 70)
    print("前3个块的完整内容（用于验证分块质量）")
    print("=" * 70)
    for i, chunk in enumerate(chunks[:3]):
        print(f"\\n--- 块 {i} ---")
        print(chunk["content"])
        print("-" * 40)

    # 质量检查
    print("\\n" + "=" * 70)
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

    print("\\n测试完成！")
'''

with open(path, 'a', encoding='utf-8') as f:
    f.write(test_code)

print("测试代码已追加到文件末尾")
