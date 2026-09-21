import os
import sys
from rag.utils import get_text_hash, DEFAULT_CACHE_DIR, get_cache_path, save_json, load_json, json_cache, success

# 添加项目根目录到模块搜索路径，以便导入 config
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.append(project_root)

from rag.rag_config import HF_ENDPOINT,MODEL_NAME,USE_CACHE
os.environ["HF_ENDPOINT"] = HF_ENDPOINT

# 导入数值计算库numpy，用于向量点积、范数计算，实现手写余弦相似度
import numpy as np
# 导入文本向量模型工具，加载预训练中文embedding模型生成文本语义向量
from sentence_transformers import SentenceTransformer

# 导入日志工具
sys.path.insert(0, project_root)
from rag.utils.logger import error, warn, info

_global_model = None

def get_model(model_name):
    global _global_model
    if _global_model is None:
        _global_model = SentenceTransformer(model_name)
    return _global_model

# 自研语义向量检索类，仅使用预训练模型生成向量，相似度、排序逻辑全部手写，不调用向量数据库
class SimpleVectorRetriever:
    # 初始化方法：加载向量模型、批量生成所有文档的语义向量并保存
    def __init__(self, docs):
        try:
            if not docs:
                raise ValueError("文档列表为空，无法初始化向量检索器")
            
            self.docs = docs  # 保存所有分块文档
            self.model = get_model(MODEL_NAME) 
            
            # 检查文档格式是否正确
            for i, doc in enumerate(docs):
                if not isinstance(doc, dict) or "content" not in doc:
                    raise ValueError(f"第 {i} 个文档格式不正确，需要包含 'content' 字段")
                if not doc["content"] or not doc["content"].strip():
                    warn(f"第 {i} 个文档内容为空")
            
            info("开始初始化向量检索器...")
            info("检测是否存在缓存文件...")            

            # 生成文档哈希（基于所有文档内容拼接）
            doc_hash = get_text_hash("".join([d["content"] for d in docs]))
            # 缓存目录：cache/vectors/
            cache_dir = DEFAULT_CACHE_DIR / "vectors"
            cache_dir.mkdir(parents=True, exist_ok=True)
            # 缓存文件名：包含模型名（替换 / 为 _）和文档哈希，扩展名 .npy
            safe_model_name = MODEL_NAME.replace('/', '_')
            cache_path = cache_dir / f"vectors_{safe_model_name}_{doc_hash}.npy"
            
            if USE_CACHE and cache_path.exists():
                # 尝试加载缓存
                if USE_CACHE and cache_path.exists():
                    try:
                        self.doc_vectors = np.load(cache_path)
                        info(f"向量缓存加载成功: {cache_path}")
                        success(f"向量检索器初始化完成（使用缓存），共 {len(self.doc_vectors)} 个向量")
                        return  # 直接返回，不再编码
                    except Exception as e:
                        warn(f"加载向量缓存失败: {e}，将重新计算")
            
            info("开始加载向量模型...")
            # 加载轻量级中文embedding模型m3e-small，用于文本转语义向量
            self.model = SentenceTransformer(MODEL_NAME)
            info("向量模型加载成功")

            # 提取所有文档块的纯文本内容
            doc_texts = [doc["content"] for doc in docs]
            
            info("开始生成文档向量...")
            # 批量编码所有文档，生成二维numpy向量矩阵，自行管理存储
            self.doc_vectors = self.model.encode(doc_texts)
            info(f"文档向量生成完成，共 {len(self.doc_vectors)} 个向量")
            
            # 保存缓存
            if USE_CACHE:
                try:
                    np.save(cache_path, self.doc_vectors)
                    success(f"向量缓存已保存: {cache_path}")
                except Exception as e:
                    warn(f"保存向量缓存失败: {e}")
            
            success(f"向量检索器初始化完成，共 {len(self.doc_vectors)} 个向量")
            
        except ValueError as e:
            error(f"初始化向量检索器失败 - 数据错误: {e}")
            raise
        except Exception as e:
            error(f"初始化向量检索器失败 - 模型加载或向量生成错误: {e}")
            raise

    # 静态工具方法：手写余弦相似度计算公式，输入两个向量，返回0~1之间相似度值
    @staticmethod
    def cosine_sim(a, b):
        """手写余弦相似度"""
        try:
            # 计算向量范数
            norm_a = np.linalg.norm(a)
            norm_b = np.linalg.norm(b)
            
            # 处理除零错误
            if norm_a == 0 or norm_b == 0:
                warn("检测到零向量，余弦相似度返回 0.0")
                return 0.0
            
            # 分子：两个向量点积；分母：两个向量二范数的乘积
            return np.dot(a, b) / (norm_a * norm_b)
        except ValueError as e:
            error(f"计算余弦相似度失败 - 向量维度不匹配: {e}")
            return 0.0
        except Exception as e:
            error(f"计算余弦相似度失败 - 未知错误: {e}")
            return 0.0

    # 向量检索主方法：输入用户提问，返回语义最相似的top_k文档
    def search(self, query, top_k=2):
        try:
            if not query or not query.strip():
                raise ValueError("查询内容为空")
            
            if top_k <= 0:
                warn(f"top_k 参数为 {top_k}，将调整为 1")
                top_k = 1
            
            info("开始执行向量检索...")
            
            # 将用户提问转换为一维查询语义向量
            query_vec = self.model.encode(query)
            
            scores = []  # 存储(文档索引,余弦相似度)二元组
            # 遍历全部文档向量，逐个计算与查询向量的相似度
            for i, vec in enumerate(self.doc_vectors):
                sim = self.cosine_sim(query_vec, vec)
                scores.append((i, sim))
            
            # 按相似度从高到低排序
            scores.sort(key=lambda x: x[1], reverse=True)
            
            # 确保 top_k 不超过实际文档数量
            actual_top_k = min(top_k, len(scores))
            
            info(f"向量检索完成，返回前 {actual_top_k} 个结果")
            
            # 截取top_k最相似文档，绑定原文和相似度返回结果
            return [(self.docs[idx], score) for idx, score in scores[:actual_top_k]]
            
        except ValueError as e:
            error(f"向量检索失败 - 参数错误: {e}")
            return []
        except Exception as e:
            error(f"向量检索失败 - 未知错误: {e}")
            return []

