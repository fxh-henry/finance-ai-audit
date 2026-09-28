# -*- coding: utf-8 -*-
path = r"D:\agent\财务报销项目\rag\retriever\vector_retrieval.py"
with open(path, "r", encoding="utf-8") as f:
    c = f.read()

old = (
    "from rag.rag_config import HF_ENDPOINT,MODEL_NAME,USE_CACHE\n"
    'os.environ["HF_ENDPOINT"] = HF_ENDPOINT\n'
)

new = (
    "from rag.rag_config import HF_ENDPOINT,MODEL_NAME,USE_CACHE\n"
    "\n"
    "# 只有本地开发才用国内镜像；Streamlit Cloud 服务器在海外，直连 huggingface.co 更快\n"
    '# 云端特征路径：/mount/src/...\n'
    'if "/mount/src/" not in os.getcwd():\n'
    '    os.environ["HF_ENDPOINT"] = HF_ENDPOINT\n'
)

if old in c:
    c = c.replace(old, new)
    with open(path, "w", encoding="utf-8") as f:
        f.write(c)
    print("OK")
else:
    print("NOT FOUND")
