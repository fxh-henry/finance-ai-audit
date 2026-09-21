from collections import Counter
import math
import sys
import os
from rag.utils import get_text_hash, DEFAULT_CACHE_DIR, get_cache_path, save_json, load_json, json_cache, success

# 添加项目根目录到模块搜索路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from rag.utils.text_splitter import cut_text, MAJOR_TERMS
from rag.utils.logger import error, warn, info

# 专有名词加分权重
TERM_WEIGHT_SCALE = 2.0

class SimpleBM25:
    def __init__(self, docs, k1=1.5, b=0.75, use_cache=True):
        try:
            if not docs:
                raise ValueError("文档列表为空，无法初始化 BM25 检索器")
            
            # 验证文档格式
            for i, doc in enumerate(docs):
                if not isinstance(doc, dict) or "content" not in doc:
                    raise ValueError(f"第 {i} 个文档格式不正确，需要包含 'content' 字段")
                if not doc["content"] or not doc["content"].strip():
                    warn(f"第 {i} 个文档内容为空")
            
            self.docs = docs   #docs文本块列表
            self.N = len(docs)
            
            # 验证参数
            if k1 <= 0:
                raise ValueError(f"k1 必须为正数，当前值: {k1}")
            if not (0 <= b <= 1):
                raise ValueError(f"b 必须在 0-1 之间，当前值: {b}")
            
            self.k1 = k1
            self.b = b

            info("开始初始化 BM25 检索器...")
            doc_hash = get_text_hash("".join([d["content"] for d in docs]))
            cache_params = {"k1": k1, "b": b}
            cache_path = get_cache_path(DEFAULT_CACHE_DIR, "bm25", doc_hash, cache_params)

             # 尝试加载缓存
            if use_cache and cache_path.exists():
                cached = load_json(cache_path)
                if cached and all(k in cached for k in ("doc_lengths", "avgdl", "doc_tf", "idf")):
                    self.doc_lengths = cached["doc_lengths"]
                    self.avgdl = cached["avgdl"]
                    # doc_tf 存储为 [{"word": count}, ...] 格式，转回 Counter
                    self.doc_tf = [Counter(tf_dict) for tf_dict in cached["doc_tf"]]
                    self.idf = cached["idf"]
                    info(f"BM25 从缓存加载成功: {cache_path}")
                    return

            
            # 缓存不存在，执行原有计算
            info("开始初始化 BM25 检索器（无缓存）...")
            # 预计算每篇文档长度、全局平均长度（文档长度抑制）
            self.doc_lengths = [len(doc["content"]) for doc in docs]
            
            # 处理除零错误
            if self.N == 0:
                self.avgdl = 0
            else:
                self.avgdl = sum(self.doc_lengths) / self.N

            # 每篇文档过滤停用词后的词频
            self.doc_tf = []
            for doc in docs:
                words = cut_text(doc["content"])   #分词
                self.doc_tf.append(Counter(words))  #统计每个词出现的次数（去除了过滤词）

            # 预计算所有词IDF（df：关键词在文多少个本块出现过
            self.idf = {}
            all_words = set()
            for tf in self.doc_tf:
                all_words.update(tf.keys())
            
            for word in all_words:
                df = sum(1 for tf in self.doc_tf if word in tf)  #统计df
                # 计算关键词的权重，越稀有，文本块出现次数越少
                # 使用安全的计算方式
                self.idf[word] = math.log((self.N - df + 0.5) / (df + 0.5) + 1)
            
            cache_data = {
            "doc_lengths": self.doc_lengths,
            "avgdl": self.avgdl,
            "doc_tf": [dict(tf) for tf in self.doc_tf],
            "idf": self.idf
             }
            if use_cache:
                save_json(cache_data, cache_path)
                success(f"BM25 缓存已保存: {cache_path}")
                info(f"BM25 检索器初始化完成，共 {self.N} 个文档")
            
        except ValueError as e:
            error(f"初始化 BM25 检索器失败 - 参数错误: {e}")
            raise
        except Exception as e:
            error(f"初始化 BM25 检索器失败 - 未知错误: {e}")
            raise

    def search(self, query, top_k=2):
        try:
            if not query or not query.strip():
                raise ValueError("查询内容为空")
            
            if top_k <= 0:
                warn(f"top_k 参数为 {top_k}，将调整为 1")
                top_k = 1
            
            info("开始执行 BM25 检索...")
            
            # 过滤停用词
            query_words = cut_text(query)
            
            if not query_words:
                warn("查询分词后没有有效关键词")
                return []

            scores = []

            for idx in range(self.N):
                total_score = 0.0
                dl = self.doc_lengths[idx]
                word_count = self.doc_tf[idx]

                for w in query_words:
                    if w not in word_count:
                        continue
                    tf = word_count[w]
                    
                    # 标准BM25公式
                    numerator = tf * (self.k1 + 1)
                    
                    # 安全处理分母
                    if self.avgdl == 0:
                        denominator = tf + self.k1
                    else:
                        denominator = tf + self.k1 * (1 - self.b + self.b * (dl / self.avgdl))
                    
                    # 避免除零
                    if denominator == 0:
                        score = 0
                    else:
                        score = self.idf.get(w, 0) * (numerator / denominator)

                    # 如果是408专有名词，额外加权放大分数
                    if w in MAJOR_TERMS:
                        score *= TERM_WEIGHT_SCALE

                    total_score += score

                scores.append((idx, total_score))

            # 按分数降序排序，返回top-k结果
            scores.sort(key=lambda x: x[1], reverse=True)
            
            # 确保 top_k 不超过实际文档数量
            actual_top_k = min(top_k, len(scores))
            
            info(f"BM25 检索完成，返回前 {actual_top_k} 个结果")
            
            return [(self.docs[idx], score) for idx, score in scores[:actual_top_k]]
            
        except ValueError as e:
            error(f"BM25 检索失败 - 参数错误: {e}")
            return []
        except Exception as e:
            error(f"BM25 检索失败 - 未知错误: {e}")
            return []
