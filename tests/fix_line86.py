# -*- coding: utf-8 -*-
path = r"D:\agent\财务报销项目\rag\retriever\vector_retrieval.py"
with open(path, "r", encoding="utf-8") as f:
    c = f.read()

old = (
    "            info(\"开始加载向量模型...\")\n"
    "            # 加载轻量级中文embedding模型m3e-small，用于文本转语义向量\n"
    "            self.model = SentenceTransformer(MODEL_NAME)\n"
    "            info(\"向量模型加载成功\")\n"
)

new = (
    "            info(\"开始加载向量模型...\")\n"
    "            # 走 get_model()，优先用本地 models/m3e-small/，不联网\n"
    "            self.model = get_model(MODEL_NAME)\n"
    "            info(\"向量模型加载成功\")\n"
)

if old in c:
    c = c.replace(old, new)
    with open(path, "w", encoding="utf-8") as f:
        f.write(c)
    print("OK")
else:
    print("NOT FOUND")
