# -*- coding: utf-8 -*-
"""
RAG召回率测试
============
构造20个真实查询，每个查询标注"应该命中的条款关键词"，
跑一遍混合检索，统计召回率。
"""
import sys, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

from rag.rag_service import RagService

# 测试用例：(查询词, 应该命中的条款关键词)
# 关键词只要出现在返回文本里就算命中
TEST_CASES = [
    # 住宿费
    ("住宿费一线城市标准是多少", "第十四条"),
    ("住宿超标了怎么办", "超标部分由个人承担"),
    ("两个人合住酒店怎么报销", "合住"),
    ("部门经理住二线城市酒店多少钱", "部门经理"),
    # 交通费
    ("出差坐高铁什么级别能坐一等座", "交通工具标准"),
    ("机票经济舱需要审批吗", "经济舱（需提前审批）"),
    ("市内交通每天补贴多少钱", "80元"),
    ("出差伙食补助多少", "100元"),
    # 业务招待
    ("请客户吃饭人均标准", "业务招待"),
    ("招待费可以抵税吗", "不得抵扣"),
    ("陪餐人数有限制吗", "陪餐人数"),
    # 发票要求
    ("数电发票需要XML文件吗", "XML源文件"),
    ("发票抬头开个人了能报吗", "抬头为个人"),
    ("跨年度发票最晚什么时候报", "次年3月31日"),
    # 审批流程
    ("5000元以上报销谁审批", "总经理审批"),
    ("报销单多久能付款", "5个工作日"),
    # 违规
    ("重复报销电子发票会怎样", "重复报销"),
    ("拆分发票规避审批是什么后果", "拆分发票"),
    # 其他
    ("私车公用怎么报销", "私车公用"),
    ("通讯补贴多少", "通讯补贴"),
]

def main():
    print("=" * 60)
    print("RAG召回率测试")
    print("=" * 60)

    rag = RagService()

    hit1 = hit3 = hit5 = 0
    total = len(TEST_CASES)
    failures = []

    for i, (query, keyword) in enumerate(TEST_CASES, 1):
        results = rag.retrieve(query, top_k=5, return_scores=True)
        contents = [doc["content"] for doc, score in results]

        r1 = keyword in contents[0] if contents else False
        r3 = any(keyword in c for c in contents[:3])
        r5 = any(keyword in c for c in contents[:5])

        if r1: hit1 += 1
        if r3: hit3 += 1
        if r5: hit5 += 1

        status = "✓" if r3 else "✗"
        print(f"\n[{i}/{total}] {status} 查询: {query}")
        print(f"    期望命中: {keyword}")
        print(f"    top1={r1}  top3={r3}  top5={r5}")
        if not r3:
            failures.append((query, keyword, contents))

    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)
    print(f"总查询数: {total}")
    print(f"Recall@1: {hit1}/{total} = {hit1/total*100:.1f}%")
    print(f"Recall@3: {hit3}/{total} = {hit3/total*100:.1f}%")
    print(f"Recall@5: {hit5}/{total} = {hit5/total*100:.1f}%")

    if failures:
        print(f"\n未命中top3的 {len(failures)} 个查询:")
        for query, kw, contents in failures:
            print(f"  - {query} (期望: {kw})")

if __name__ == "__main__":
    main()
