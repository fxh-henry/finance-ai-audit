import sys  # 导入 Python 标准库 sys 模块，提供访问与解释器交互的变量和函数
import time  # 导入 Python 标准库 time 模块，提供时间相关函数
from datetime import datetime  # 从 datetime 模块导入 datetime 类，用于处理日期和时间
from typing import Optional, Union  # 从 typing 模块导入类型注解工具
from functools import wraps  # 从 functools 模块导入 wraps 装饰器，用于保留原函数的元数据
import os  # 导入 os 模块，用于检查环境变量

class Logger:  # 定义 Logger 类，用于日志记录
    """
    简易日志记录器，替换 print 语句
    """
    
    # 定义颜色代码字典，使用 ANSI 转义序列实现控制台彩色输出
    COLORS = {
        'RESET': '\033[0m',  # 重置颜色的 ANSI 代码
        'INFO': '\033[94m',     # INFO 级别的颜色：蓝色
        'WARN': '\033[93m',     # WARN 级别的颜色：黄色
        'ERROR': '\033[91m',    # ERROR 级别的颜色：红色
        'SUCCESS': '\033[92m',  # SUCCESS 级别的颜色：绿色
        'DEBUG': '\033[96m',    # DEBUG 级别的颜色：青色
    }
    
    def __init__(self, name: str = "RAG", use_color: bool = True):  # 构造函数，初始化 Logger 实例
        self.name = name  # 保存日志记录器的名称
        # 在 Windows 上尝试启用 ANSI 颜色支持
        if use_color and sys.platform == 'win32':
            try:
                # 使用 ctypes 调用 Windows API 启用控制台虚拟终端处理（支持 ANSI 颜色）
                import ctypes
                kernel32 = ctypes.windll.kernel32
                kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
                self.use_color = True
            except:
                # 如果启用失败，则回退到检查 ANSICON 环境变量
                self.use_color = 'ANSICON' in os.environ
        else:
            # 非 Windows 系统直接使用传入的参数
            self.use_color = use_color
        
    def _log(self, level: str, message: str, *args):  # 定义内部日志方法，以下划线开头表示是内部方法
        """内部日志方法"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # 获取当前时间并格式化为字符串
        
        if args:  # 如果有额外的参数，使用字符串格式化
            message = message % args  # 使用 % 操作符进行字符串格式化
            
        if self.use_color:  # 如果启用了彩色输出
            color = self.COLORS.get(level, self.COLORS['RESET'])  # 获取对应级别的颜色，默认使用 RESET
            print(f"{color}[{timestamp}] [{self.name}] [{level}] {message}{self.COLORS['RESET']}")  # 打印带颜色的日志，最后重置颜色
        else:  # 如果不使用彩色输出
            print(f"[{timestamp}] [{self.name}] [{level}] {message}")  # 打印普通日志
    
    def info(self, message: str, *args):  # 定义 info 日志方法
        """信息日志"""
        self._log("INFO", message, *args)  # 调用内部 _log 方法，级别为 INFO
    
    def warn(self, message: str, *args):  # 定义 warn 日志方法
        """警告日志"""
        self._log("WARN", message, *args)  # 调用内部 _log 方法，级别为 WARN
    
    def error(self, message: str, *args):  # 定义 error 日志方法
        """错误日志"""
        self._log("ERROR", message, *args)  # 调用内部 _log 方法，级别为 ERROR
    
    def success(self, message: str, *args):  # 定义 success 日志方法
        """成功日志"""
        self._log("SUCCESS", message, *args)  # 调用内部 _log 方法，级别为 SUCCESS
    
    def debug(self, message: str, *args):  # 定义 debug 日志方法
        """调试日志"""
        self._log("DEBUG", message, *args)  # 调用内部 _log 方法，级别为 DEBUG
    
    def timer(self, func):  # 定义 timer 装饰器方法，用于计时函数执行时间
        """
        函数计时装饰器
        """
        @wraps(func)  # 使用 wraps 装饰器保留原函数的元数据（如函数名、文档字符串等）
        def wrapper(*args, **kwargs):  # 定义包装函数，接收任意参数
            start_time = time.time()  # 记录函数开始执行的时间戳
            self.info(f"开始执行: {func.__name__}")  # 打印开始执行的信息
            result = func(*args, **kwargs)  # 调用原函数并获取结果
            elapsed_time = time.time() - start_time  # 计算函数执行耗时
            self.success(f"{func.__name__} 执行完成，耗时: {elapsed_time:.4f}秒")  # 打印完成信息和耗时
            return result  # 返回原函数的结果
        return wrapper  # 返回包装函数
    
    def log_score(self, retriever_name: str, query: str, results: list):  # 定义专门用于打印检索结果的方法
        """
        打印检索结果和分数
        :param retriever_name: 检索器名称
        :param query: 用户查询
        :param results: 检索结果列表，格式为 [(doc, score), ...]
        """
        self.info(f"===== {retriever_name}检索结果 =====")  # 打印检索结果标题
        self.info(f"用户查询: {query}")  # 打印用户查询
        
        for idx, (doc, score) in enumerate(results):  # 遍历检索结果，使用 enumerate 同时获取索引和值
            self.info(f"\n第{idx + 1}条 | 匹配分数: {score:.4f}")  # 打印结果序号和匹配分数（保留4位小数）
            self.info(f"文本块ID: {doc['id']}")  # 打印文本块的 ID
            self.info(f"文本内容: {doc['content']}")  # 打印文本块的内容




# 创建全局默认 logger 实例，供整个项目使用
logger = Logger()  # 实例化 Logger 类，使用默认参数


# 便捷函数，提供更简单的调用方式，不需要每次都访问 logger 实例
def info(message: str, *args):  # 定义便捷的 info 函数
    logger.info(message, *args)  # 调用 logger 实例的 info 方法


def warn(message: str, *args):  # 定义便捷的 warn 函数
    logger.warn(message, *args)  # 调用 logger 实例的 warn 方法


def error(message: str, *args):  # 定义便捷的 error 函数
    logger.error(message, *args)  # 调用 logger 实例的 error 方法


def success(message: str, *args):  # 定义便捷的 success 函数
    logger.success(message, *args)  # 调用 logger 实例的 success 方法


def debug(message: str, *args):  # 定义便捷的 debug 函数
    logger.debug(message, *args)  # 调用 logger 实例的 debug 方法


def log_score(retriever_name: str, query: str, results: list):  # 定义便捷的 log_score 函数
    logger.log_score(retriever_name, query, results)  # 调用 logger 实例的 log_score 方法
