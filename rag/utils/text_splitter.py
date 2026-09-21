import jieba  # 导入 jieba 中文分词库，用于中文文本分词

from .logger import error, warn, info, success  # 导入日志工具


# 停用词集合
STOP_WORDS = {
    # 助词
    "的", "地", "得", "了", "着", "过", "之", "其",
    # 介词、连词
    "和", "与", "及", "或", "在", "对于", "为了", "通过", "由", "把",
    # 副词、代词
    "这", "那", "该", "此", "可以", "能够", "会", "要", "很", "非常",
    # 过渡、无意义引导词
    "首先", "其次", "综上", "如图", "如下", "例如", "一般", "通常"
}

# 财务/报销领域专业名词词典（用于BM25检索时额外加权）
MAJOR_TERMS = {
    # 发票相关
    "增值税", "专用发票", "普通发票", "电子发票", "数电票", "发票号码", "纳税人识别号",
    "价税合计", "不含税金额", "进项税额", "销项税额", "税率", "开票日期",
    # 报销相关
    "差旅费", "住宿费", "业务招待费", "交通费", "餐饮费", "通讯费", "办公费",
    "报销", "报销单", "报销标准", "报销制度", "费用标准",
    # 财务相关
    "财务", "会计", "凭证", "入账", "抵扣", "税前扣除", "成本", "费用",
    # 企业相关
    "部门经理", "总经理", "审批", "审核", "复核",
}

# 给 jieba 加载专业词，防止多字术语被拆分
try:
    for word in MAJOR_TERMS:  # 遍历所有专业术语
        jieba.add_word(word)  # 使用 jieba.add_word 将专业术语添加到 jieba 词典中
    info("成功加载专业词典到 jieba")  # 记录成功日志
except Exception as e:
    warn(f"加载专业词典失败: {e}")  # 记录警告日志

# 分词且过滤停用词
def cut_text(text):
    try:
        if not text or not text.strip():  # 判断文本是否为空或只包含空白字符
            warn("输入文本为空，返回空列表")  # 记录警告日志
            return []  # 返回空列表
        
        # 原生分词
        raw_words = jieba.lcut(text)  # 使用 jieba.lcut 对文本进行分词，返回词语列表
        # 过滤停用词 + 单字无意义字符
        valid_words = [
            w for w in raw_words
            if w not in STOP_WORDS and len(w.strip()) > 1
        ]  # 使用列表推导式过滤停用词和单字词语
        return valid_words  # 返回有效的词语列表
    except Exception as e:
        error(f"分词失败: {e}")  # 记录错误日志
        return []  # 返回空列表


