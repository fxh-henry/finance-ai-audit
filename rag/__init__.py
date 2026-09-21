# -*- coding: utf-8 -*-
# ============================================================================
# RAG包初始化模块
# ============================================================================
# 作用：提供RAG服务的全局单例，避免重复加载m3e模型（每次加载要8秒）
#
# 使用方式：
#   from rag import get_rag_service
#   rag = get_rag_service()       # 第一次调用时初始化，之后直接返回已有实例
#   context = rag.retrieve("住宿费标准是多少")
#
# 或者直接导入类：
#   from rag import RagService
#   rag = RagService()
# ============================================================================

from rag.rag_service import RagService

# 全局单例变量，初始为None，第一次调用时才初始化
_rag_service = None


def init_rag(kb_dir=None, use_cache=True):
    """
    初始化RAG服务（全局单例）

    第一次调用时创建RagService实例（加载知识库、构建BM25和向量索引，耗时几秒），
    之后再调用直接返回已有实例，不会重复初始化。

    参数：
        kb_dir: 知识库目录，默认使用 rag/knowledge_base/
        use_cache: 是否启用缓存，默认True

    返回：
        RagService 实例
    """
    global _rag_service

    # 如果已经初始化过，直接返回已有实例
    if _rag_service is not None:
        return _rag_service

    # 第一次调用，执行初始化
    _rag_service = RagService(kb_dir=kb_dir, use_cache=use_cache)
    return _rag_service


def get_rag_service():
    """
    获取RAG服务单例（如果还没初始化，自动用默认参数初始化）

    返回：
        RagService 实例
    """
    return init_rag()


def reset_rag():
    """
    重置RAG单例（下次调用init_rag时会重新初始化）

    用途：知识库内容更新后，需要重新加载时调用
    """
    global _rag_service
    _rag_service = None


# 导出给外部使用
__all__ = [
    'RagService',
    'init_rag',
    'get_rag_service',
    'reset_rag',
]
