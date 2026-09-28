# -*- coding: utf-8 -*-
path = r"D:\agent\财务报销项目\rag\retriever\vector_retrieval.py"
with open(path, "r", encoding="utf-8") as f:
    c = f.read()

# 改 MODEL_NAME 的引用：优先用本地 models/m3e-small，不存在再用在线
old_get_model = '''def get_model(model_name):
    global _global_model
    if _global_model is None:
        _global_model = SentenceTransformer(model_name)
    return _global_model'''

new_get_model = '''def get_model(model_name):
    """加载向量模型。优先用项目内 models/ 目录下的本地副本（云端部署用），
    本地没有则回退到 HuggingFace 在线下载。"""
    global _global_model
    if _global_model is None:
        from pathlib import Path
        local_model_dir = Path(__file__).resolve().parents[2] / "models" / "m3e-small"
        if (local_model_dir / "config.json").exists():
            # 本地有模型文件，直接加载（不联网）
            _global_model = SentenceTransformer(str(local_model_dir))
        else:
            # 本地没有，从 HuggingFace 下载（首次运行）
            _global_model = SentenceTransformer(model_name)
    return _global_model'''

if old_get_model in c:
    c = c.replace(old_get_model, new_get_model)
    with open(path, "w", encoding="utf-8") as f:
        f.write(c)
    print("OK")
else:
    print("NOT FOUND")
