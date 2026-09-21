from rag.utils.logger import error, warn, info


# 混合融合函数，接收bm25结果、向量结果、bm25权重
def hybrid_fusion(bm25_res, vec_res, alpha=0.6):
    """
    BM25 + 向量  加权混合检索融合
    :param bm25_res: [(doc, bm25_score), ...] BM25输出的列表
    :param vec_res: [(doc, vec_score), ...] 向量检索输出的列表
    :param alpha: BM25权重 [0~1]
    :return: 融合后 [(doc, final_score)] 降序列表
    """
    try:
        if not bm25_res and not vec_res:
            warn("两种检索结果都为空，无法进行融合")
            return []
        
        # 验证 alpha 参数
        if not (0 <= alpha <= 1):
            warn(f"alpha 参数 {alpha} 不在 0-1 之间，将使用默认值 0.6")
            alpha = 0.6
        
        all_chunk = {}  # 空字典，key=块id，value存储文本块、bm25分数、向量分数，用于去重合并
        
        # 存入BM25结果
        for doc, bs in bm25_res:  # 循环遍历每一条BM25召回数据，doc是文本块字典，bs是bm25原始分数
            try:
                cid = doc["id"]  # 提取当前文本块唯一id作为字典键
                all_chunk[cid] = {"doc": doc, "bm25": bs, "vec": 0.0}  # 存入字典，向量分数初始化为0
            except Exception as e:
                warn(f"处理 BM25 结果时出错: {e}")
                continue
        
        # 存入向量结果，已存在则更新vec分数
        for doc, vs in vec_res:  # 循环遍历每一条向量召回数据，vs为余弦相似度分数
            try:
                cid = doc["id"]  # 获取当前文本块id
                if cid in all_chunk:  # 判断该块是否已经在字典中（同时被两种检索召回）
                    all_chunk[cid]["vec"] = vs  # 存在则更新向量分数
                else:  # 该块仅被向量检索召回，无bm25分数
                    all_chunk[cid] = {"doc": doc, "bm25": 0.0, "vec": vs}  # bm25分数置0保存
            except Exception as e:
                warn(f"处理向量结果时出错: {e}")
                continue
        
        if not all_chunk:
            warn("没有有效的检索结果可以融合")
            return []

        # 2. BM25分数归一化到 0~1
        bm25_scores = [item["bm25"] for item in all_chunk.values()]  # 提取全部块的bm25原始分数列表
        
        # 处理除零错误
        min_b = min(bm25_scores) if bm25_scores else 0
        max_b = max(bm25_scores) if bm25_scores else 0
        
        fused_list = []  # 定义列表存放融合后的(文本块,综合得分)二元组
        for info_item in all_chunk.values():  # 遍历合并去重后的全部文本块信息
            try:
                doc = info_item["doc"]  # 取出文本块字典
                b_score = info_item["bm25"]  # 取出该块bm25原始分数
                v_score = info_item["vec"]  # 取出该块向量相似度分数
                
                # 归一化BM25
                if max_b - min_b == 0:  # 边界判断：所有bm25分数完全相同，避免除以0报错
                    norm_b = 0  # 归一化值直接设为0
                else:
                    norm_b = (b_score - min_b) / (max_b - min_b)  # 最小最大归一化，把分数缩放到0~1区间
                
                # 加权融合
                final = alpha * norm_b + (1 - alpha) * v_score  # 加权计算综合分数，alpha控制bm25占比
                fused_list.append((doc, final))  # 将文本块和融合分数存入结果列表
            except Exception as e:
                warn(f"融合单个文档分数时出错: {e}")
                continue

        # 3. 按融合总分降序
        fused_list.sort(key=lambda x: x[1], reverse=True)  # 根据元组第二个元素（融合分数）从大到小排序
        return fused_list  # 返回排序完成的混合检索结果
        
    except Exception as e:
        error(f"混合检索融合失败: {e}")
        # 返回至少一种检索的结果作为降级方案
        return bm25_res if bm25_res else vec_res

