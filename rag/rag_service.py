# -*- coding: utf-8 -*-
# ============================================================================
# RAG检索服务（RagService）
# ============================================================================
# 作用：封装RAG全流程，对外提供 retrieve(query) 方法
# 流程：加载知识库 → 文本分块 → BM25检索 + 向量检索 → 混合融合 → 返回结果
#
# 使用方式：
#   from rag.rag_service import RagService
#   rag = RagService()  # 初始化时自动加载知识库、构建索引（耗时几秒）
#   results = rag.retrieve("住宿费标准是多少")  # 检索
# ============================================================================

import sys
import os
from pathlib import Path

# 强制使用本地缓存的模型，不联网检查更新（避免下载超时）
# 必须在导入 sentence_transformers 之前设置
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

# 把项目根目录加入搜索路径（解决直接运行本文件时的导入问题）
sys.path.insert(0, str(Path(__file__).parent.parent))

from rag.rag_config import (
    TOP_K, CHUNK_MAX_LEN, OVERLAP_SENT,
    BM25_WEIGHT_ALPHA, USE_CACHE
)
from rag.utils import CachedTextChunkSplitter, get_text_hash
from rag.utils import save_json, load_json, get_cache_path, DEFAULT_CACHE_DIR
from rag.utils.markdown_splitter import MarkdownHeaderSplitter
from rag.retriever.bm25 import SimpleBM25
from rag.retriever.vector_retrieval import SimpleVectorRetriever
from rag.retriever.hybrid_fusion import hybrid_fusion
# 导入日志工具：info/warn/error/success 用于普通日志，log_score 用于打印检索结果
from rag.utils.logger import info, warn, error, success, log_score


# ============================================================================
# 知识库加载函数：递归读取目录下所有.md文件，拼接成完整文本
# ============================================================================
def load_knowledge_base(kb_dir):
    """
    递归读取知识库目录下所有 .md 文件，返回文档列表

    参数：
        kb_dir: 知识库目录路径（str 或 Path）

    返回：
        文档列表，每个元素 {name: 文档名, content: 文档内容}
        按文件路径排序，保证分块和缓存的稳定性
    """
    kb_path = Path(kb_dir)
    info(f"知识库目录路径: {kb_path}")

    # 检查目录是否存在
    if not kb_path.exists():
        error(f"知识库目录不存在: {kb_path}")
        return []

    # rglob("*.md") 递归查找所有 .md 文件（包括子文件夹里的）
    md_files = sorted(kb_path.rglob("*.md"))
    info(f"递归查找完成，共找到 {len(md_files)} 个文档")
    for f in md_files:
        info(f"  - {f.name}")

    docs = []
    info("开始逐个读取文档...")

    for md_file in md_files:
        try:
            with open(md_file, "r", encoding="utf-8") as f:
                content = f.read()
            docs.append({
                "name": md_file.stem,   # 文件名（不含后缀），用于分块元数据
                "content": content
            })
            success(f"已加载: {md_file.name}（{len(content)} 字符）")
        except Exception as e:
            error(f"读取文件失败 {md_file}: {e}")

    total_chars = sum(len(d["content"]) for d in docs)
    success(f"知识库加载完成，共 {len(docs)} 个文档，总长度 {total_chars} 字符")
    return docs


# ============================================================================
# RagService 类
# ============================================================================
class RagService:
    """
    RAG检索服务类
    一次性初始化（加载知识库、分块、构建BM25和向量索引），
    之后多次调用 retrieve() 检索，不需要重复初始化。
    """

    def __init__(self, kb_dir=None, use_cache=USE_CACHE):
        """
        初始化RAG服务

        参数：
            kb_dir: 知识库目录，默认使用 rag/knowledge_base/
            use_cache: 是否启用缓存（分块、BM25、向量）
        """
        info("=" * 60)
        info("【第1步】开始初始化 RagService 检索服务")
        info("=" * 60)

        # ===== 确定知识库目录 =====
        if kb_dir is None:
            # Path(__file__).parent = rag/  （当前脚本所在文件夹）
            kb_dir = Path(__file__).parent / "knowledge_base"
        self.kb_dir = Path(kb_dir)
        info(f"知识库目录: {self.kb_dir}")

        # ===== 加载知识库文本 =====
        info("【第2步】加载知识库文本")
        kb_docs = load_knowledge_base(self.kb_dir)

        if not kb_docs:
            error("知识库内容为空，请检查知识库目录")
            raise ValueError("知识库内容为空，请检查知识库目录")

        # ===== 文本分块（按Markdown标题层级，带缓存） =====
        info("【第3步】文本分块（Markdown标题层级分块）")

        # 计算缓存key：所有文档名+内容长度的哈希，文档内容变化时缓存自动失效
        cache_signature = "|".join(f"{d['name']}:{len(d['content'])}" for d in kb_docs)
        cache_key = get_text_hash(cache_signature)
        cache_path = get_cache_path(DEFAULT_CACHE_DIR, "md_chunks", cache_key, {
            "max_chunk_len": CHUNK_MAX_LEN,
            "doc_count": len(kb_docs)
        })

        # 尝试从缓存加载
        if use_cache and cache_path.exists():
            cached_chunks = load_json(cache_path)
            if cached_chunks and isinstance(cached_chunks, list):
                info(f"使用Markdown分块缓存: {cache_path}")
                self.docs = cached_chunks
            else:
                warn("缓存文件损坏，将重新生成分块")
                self.docs = []
        else:
            self.docs = []

        # 缓存不存在或损坏，重新分块
        if not self.docs:
            for doc in kb_docs:
                info(f"  分块文档: {doc['name']}")
                # 给每个文档单独分块，传入文档名作为元数据和上下文前缀
                splitter_doc = MarkdownHeaderSplitter(
                    max_chunk_length=CHUNK_MAX_LEN,
                    doc_name=doc["name"]
                )
                doc_chunks = splitter_doc.split(doc["content"])
                self.docs.extend(doc_chunks)

            # 重新编号（多个文档的块id会重复）
            for i, chunk in enumerate(self.docs):
                chunk["id"] = i

            # 保存缓存
            if use_cache and self.docs:
                save_json(self.docs, cache_path)
                success(f"Markdown分块缓存已保存: {cache_path}")

        success(f"文本分块完成，共 {len(self.docs)} 个文本块")
        # 打印前3个文本块的预览，方便调试
        for i, doc in enumerate(self.docs[:3]):
            info(f"  块{i}: {doc['content'][:80]}...")

        # ===== 初始化BM25检索器 =====
        info("【第4步】初始化BM25检索器（关键词匹配）")
        self.bm25 = SimpleBM25(self.docs, use_cache=use_cache)

        # ===== 初始化向量检索器 =====
        info("【第5步】初始化向量检索器（语义匹配）")
        self.vector_retriever = SimpleVectorRetriever(self.docs)

        info("=" * 60)
        success("RagService 检索服务初始化完成，全部5步执行成功")
        info("=" * 60)

    def retrieve(self, query, top_k=TOP_K, return_scores=False):
        """
        检索与查询最相关的知识库内容

        参数：
            query: 用户查询问题
            top_k: 返回前N个结果，默认3
            return_scores: 是否返回分数，默认False（只返回文本）

        返回：
            return_scores=False: 返回拼接后的文本字符串
            return_scores=True:  返回 [(文本块, 分数), ...] 列表
        """
        if not query or not query.strip():
            warn("查询内容为空，返回空字符串")
            return ""

        info("=" * 60)
        info(f"开始检索，用户查询: {query}")
        info("=" * 60)

        # ===== 第1步：BM25检索（关键词匹配） =====
        info("【检索1/3】BM25关键词检索...")
        bm25_results = self.bm25.search(query, top_k=top_k)
        log_score("BM25", query, bm25_results)

        # ===== 第2步：向量检索（语义匹配） =====
        info("【检索2/3】向量语义检索...")
        vec_results = self.vector_retriever.search(query, top_k=top_k)
        log_score("向量", query, vec_results)

        # ===== 第3步：混合融合（BM25权重alpha，向量权重1-alpha） =====
        info(f"【检索3/3】混合融合（BM25权重={BM25_WEIGHT_ALPHA}，向量权重={1-BM25_WEIGHT_ALPHA}）...")
        fused = hybrid_fusion(bm25_results, vec_results, alpha=BM25_WEIGHT_ALPHA)
        # 取top_k
        fused = fused[:top_k]
        log_score("混合融合", query, fused)

        success(f"检索完成，共返回 {len(fused)} 条结果")

        if return_scores:
            return fused

        # 拼接成文本字符串（用于传给大模型）
        context_parts = []
        for i, (doc, score) in enumerate(fused, 1):
            context_parts.append(f"[资料{i}]\n{doc['content']}")

        return "\n\n".join(context_parts)


# ============================================================================
# 测试代码
# ============================================================================
if __name__ == "__main__":
    # 初始化RAG服务
    rag = RagService()

    # 测试检索
    query = "住宿费报销标准是多少"
    info("=" * 60)
    info(f"测试查询: {query}")
    info("=" * 60)

    results = rag.retrieve(query, top_k=3, return_scores=True)

    info("=" * 60)
    success("最终检索结果汇总")
    info("=" * 60)
    for i, (doc, score) in enumerate(results, 1):
        info(f"第{i}条 | 分数: {score:.4f} | 内容: {doc['content'][:80]}...")
