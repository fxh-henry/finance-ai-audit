from pathlib import Path
from typing import List, Dict, Any, Optional
from .logger import error, warn, info, success
from .text_tools import save_json, load_json, get_text_hash, get_cache_path, DEFAULT_CACHE_DIR


class TextChunkSplitter:
    """
    自定义文本滑动重叠分块工具类，专门用于 RAG 知识库切分
    """

    def __init__(self, max_chunk_length: int = 150, overlap_sent_num: int = 2):
        """
        初始化分块器

        :param max_chunk_length: 单个文本块最大字符长度
        :param overlap_sent_num: 块之间重叠保留的句子数量，防止知识点被截断
        """
        try:
            if max_chunk_length <= 0:
                raise ValueError(f"max_chunk_length 必须为正数，当前值: {max_chunk_length}")
            if overlap_sent_num < 0:
                raise ValueError(f"overlap_sent_num 不能为负数，当前值: {overlap_sent_num}")

            self.max_chunk_len = max_chunk_length
            self.overlap_sent = overlap_sent_num
            info(f"TextChunkSplitter 初始化成功 - max_chunk_length: {max_chunk_length}, overlap_sent_num: {overlap_sent_num}")
        except ValueError as e:
            error(f"TextChunkSplitter 初始化失败 - 参数错误: {e}")
            raise

    def split(self, raw_text: str) -> List[Dict[str, Any]]:
        """
        对长文本进行分句、滑动窗口分块

        :param raw_text: 原始完整文本
        :return: 列表，每个元素 {id:块编号, content:块内容}
        """
        try:
            if not raw_text or not raw_text.strip():
                warn("输入文本为空，返回空列表")
                return []

            info("开始文本分块...")

            # 去除所有换行、首尾空格，统一文本格式
            text = raw_text.replace("\n", "").strip()
            # 以句号为分隔符，把整篇文章切成句子列表
            raw_sentences = text.split("。")

            # 过滤空句子、补回句号，保证句子格式完整
            sentences = []
            for sent in raw_sentences:
                sent = sent.strip()
                if sent:
                    full_sent = sent + "。"
                    # 关键修复：如果单个句子超过最大长度，按字符数硬切分成多个子句
                    # （markdown表格、标题等没有句号，会形成超长句子）
                    if len(full_sent) > self.max_chunk_len:
                        for start in range(0, len(full_sent), self.max_chunk_len):
                            sub_sent = full_sent[start:start + self.max_chunk_len]
                            if sub_sent.strip():
                                sentences.append(sub_sent)
                    else:
                        sentences.append(full_sent)

            if not sentences:
                warn("没有有效的句子可供分块")
                return []

            chunk_result = []
            window_buffer = []
            current_total_len = 0
            chunk_id = 0

            # 遍历所有清洗后的完整句子
            for single_sent in sentences:
                sent_char_len = len(single_sent)

                # 临界判断：加入新句子会超出最大块长度，先归档当前块
                if current_total_len + sent_char_len > self.max_chunk_len:
                    chunk_content = "".join(window_buffer)
                    chunk_result.append({
                        "id": chunk_id,
                        "content": chunk_content
                    })
                    chunk_id += 1

                    # 重叠核心逻辑：保留最后 N 句，作为下一块的开头
                    if len(window_buffer) > self.overlap_sent:
                        window_buffer = window_buffer[-self.overlap_sent:]
                    current_total_len = sum(len(s) for s in window_buffer)

                # 把当前句子加入滑动窗口
                window_buffer.append(single_sent)
                current_total_len += sent_char_len

            # 处理最后剩余、不足一个完整窗口的句子
            if window_buffer:
                chunk_content = "".join(window_buffer)
                chunk_result.append({
                    "id": chunk_id,
                    "content": chunk_content
                })

            info(f"文本分块完成，共生成 {len(chunk_result)} 个文本块")
            return chunk_result

        except Exception as e:
            error(f"文本分块失败: {e}")
            return []


class CachedTextChunkSplitter(TextChunkSplitter):
    """
    带缓存功能的文本分块器
    在 TextChunkSplitter 的基础上添加了 JSON 缓存支持
    """

    def __init__(self, max_chunk_length: int = 150, overlap_sent_num: int = 2,
                 cache_dir: Optional[Path] = None, use_cache: bool = True):
        super().__init__(max_chunk_length, overlap_sent_num)
        self.cache_dir = cache_dir or DEFAULT_CACHE_DIR
        self.use_cache = use_cache
        info(f"CachedTextChunkSplitter 初始化成功 - use_cache: {use_cache}")

    def split(self, raw_text: str, cache_key: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        对长文本进行分句、滑动窗口分块（支持缓存）
        """
        try:
            if not raw_text or not raw_text.strip():
                warn("输入文本为空，返回空列表")
                return []

            text_hash = cache_key or get_text_hash(raw_text)
            cache_path = None

            if self.use_cache:
                params = {
                    "max_chunk_len": self.max_chunk_len,
                    "overlap_sent": self.overlap_sent
                }
                cache_path = get_cache_path(self.cache_dir, "chunks", text_hash, params)

                if cache_path.exists():
                    cached_chunks = load_json(cache_path)
                    if cached_chunks and isinstance(cached_chunks, list):
                        info(f"使用文本分块缓存: {cache_path}")
                        return cached_chunks
                    else:
                        warn("缓存文件损坏或格式不正确，将重新生成分块")

            chunk_result = super().split(raw_text)

            if self.use_cache and cache_path is not None and chunk_result:
                save_json(chunk_result, cache_path)
                success(f"文本分块缓存已保存: {cache_path}")

            return chunk_result

        except Exception as e:
            error(f"文本分块失败: {e}")
            return []
