# 财务报销智能审核系统

基于AI技术的企业财务报销智能审核系统，支持发票自动识别、基础合规校验、RAG制度校验、异常检测、多智能体对话审核。

## 项目结构

```
├── app.py                      # 主入口（Streamlit）
├── requirements.txt            # 依赖清单
│
├── pages/                      # 页面层（后续拆分多页面）
├── core/                       # 核心编排层
│   ├── audit_pipeline.py       # 审核流程编排
│   └── report_generator.py     # 审核报告生成
├── recognizers/                # 识别层
│   ├── invoice_recognizer.py   # 识别入口（自动分发）
│   ├── pdf_parser.py           # PDF坐标法提取
│   ├── xml_parser.py           # XML数电票解析（待开发）
│   ├── train_ticket_parser.py  # 高铁票解析（待开发）
│   └── ocr_service.py          # 图片OCR（阿里云API）
├── audit/                      # 审核层
│   ├── basic_check.py          # 基础合规校验
│   ├── rag_check.py            # RAG制度校验
│   └── anomaly_detect.py       # 异常检测
├── agent/                      # Agent层
│   ├── chat_agent.py           # 对话Agent
│   └── tools.py                # Agent工具函数
├── rag/                        # RAG层
│   ├── rag_service.py          # RAG检索服务
│   └── knowledge_base/         # 知识库文档
├── llm/                        # 大模型层
│   └── llm_client.py           # 大模型调用封装
├── database/                   # 数据层
│   ├── db.py                   # SQLite操作
│   └── finance_audit.db        # 数据库文件
├── config/                     # 配置层
│   └── settings.py             # 全局配置
├── utils/                      # 工具层
│   └── file_utils.py           # 文件工具
├── temp/                       # 临时文件
└── tests/                      # 测试
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置API密钥

编辑 `config/settings.py`，填入你的大模型API密钥。

### 3. 运行

```bash
streamlit run app.py
```

## 已有文件说明

- `OCR/` - 原有OCR代码（保留不动，新代码在 `recognizers/`）
- `知识库/` - 原有知识库（保留不动，已复制到 `rag/knowledge_base/`）
- `报销规则/` - 原有报销制度文档（保留不动）
- `发票材料/` - 测试用发票样本
- `main.py` - 原有入口文件（保留不动）
