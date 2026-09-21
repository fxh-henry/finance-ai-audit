import json  # 导入 Python 标准库 json 模块，用于 JSON 数据的序列化和反序列化
import hashlib  # 导入 hashlib 库，用于生成文本的 MD5 哈希值
from pathlib import Path  # 从 pathlib 模块导入 Path 类，提供面向对象的路径操作
from typing import Any, Optional, Union, Dict  # 从 typing 模块导入类型注解工具，用于类型提示
from .logger import error, warn, info,success  # 导入日志工具


# 默认缓存目录
DEFAULT_CACHE_DIR = Path(__file__).parent.parent.parent / "cache"  # 设置缓存目录为项目根目录下的 cache 文件夹


def get_text_hash(text: str) -> str:  # 计算文本的 MD5 哈希值，用于生成缓存文件名
    """
    :param text: 输入文本
    :return: 文本的哈希值
    """
    if not text:  # 判断文本是否为空
        return ""  # 文本为空时返回空字符串
    return hashlib.md5(text.encode("utf-8")).hexdigest()  # 使用 hashlib.md5 计算文本的 MD5 哈希值，返回十六进制字符串


def get_cache_path(cache_dir: Path, prefix: str, text_hash: str, params: Optional[Dict[str, Any]] = None) -> Path:  # 定义生成缓存文件路径的函数
    """
    生成缓存文件路径
    :param cache_dir: 缓存目录
    :param prefix: 缓存文件前缀（如 'chunks'、'tokens'）
    :param text_hash: 文本哈希值
    :param params: 参数字典，用于区分不同参数的缓存
    :return: 缓存文件路径
    """
    cache_dir.mkdir(parents=True, exist_ok=True)  # 使用 Path.mkdir 创建缓存目录，parents=True 表示递归创建父目录，exist_ok=True 表示目录已存在时不报错
    
    if params:  # 判断是否有参数
        param_str = "_".join([f"{k}{v}" for k, v in sorted(params.items())])  # 将参数字典转换为字符串，按键排序后拼接
        filename = f"{prefix}_{text_hash}_{param_str}.json"  # 生成带参数的缓存文件名
    else:
        filename = f"{prefix}_{text_hash}.json"  # 生成不带参数的缓存文件名
    
    return cache_dir / filename  # 返回完整的缓存文件路径


def save_json(data: Any, file_path: Union[str, Path], ensure_ascii: bool = False, indent: int = 4) -> bool:  # 定义保存 JSON 的函数，使用类型注解，返回成功状态
    """
    保存数据到 JSON 文件
    :param data: 要保存的数据（字典、列表等）
    :param file_path: JSON 文件路径
    :param ensure_ascii: 是否确保 ASCII 编码（False 以支持中文）
    :param indent: 缩进空格数
    :return: 是否保存成功
    """
    try:
        file_path = Path(file_path)  # 将传入的文件路径转换为 Path 对象，方便后续路径操作
        file_path.parent.mkdir(parents=True, exist_ok=True)  # 使用 Path.mkdir() 方法创建文件所在目录，parents=True 表示递归创建父目录，exist_ok=True 表示目录已存在时不报错
        
        with open(file_path, 'w', encoding='utf-8') as f:  # 使用 with 语句安全打开文件，'w' 表示写入模式，encoding='utf-8' 指定文件编码
            json.dump(data, f, ensure_ascii=ensure_ascii, indent=indent)  # 调用 json.dump() 方法将数据序列化为 JSON 格式并写入文件，ensure_ascii=False 支持非 ASCII 字符，indent 控制缩进
        info(f"JSON 文件保存成功: {file_path}")
        return True
    except PermissionError as e:
        error(f"保存 JSON 文件失败 - 权限错误: {file_path}, 错误信息: {e}")
        return False
    except OSError as e:
        error(f"保存 JSON 文件失败 - 操作系统错误: {file_path}, 错误信息: {e}")
        return False
    except TypeError as e:
        error(f"保存 JSON 文件失败 - 数据类型错误（无法序列化为 JSON）: {e}")
        return False
    except Exception as e:
        error(f"保存 JSON 文件失败 - 未知错误: {file_path}, 错误信息: {e}")
        return False


def load_json(file_path: Union[str, Path], default: Any = None) -> Any:  # 定义加载 JSON 的函数，使用类型注解
    """
    从 JSON 文件加载数据
    :param file_path: JSON 文件路径
    :param default: 文件不存在时返回的默认值
    :return: 加载的数据
    """
    try:
        file_path = Path(file_path)  # 将传入的文件路径转换为 Path 对象
        if not file_path.exists():  # 使用 Path.exists() 方法判断文件是否存在
            warn(f"JSON 文件不存在，将返回默认值: {file_path}")
            return default  # 如果文件不存在，返回默认值
        
        with open(file_path, 'r', encoding='utf-8') as f:  # 使用 with 语句安全打开文件，'r' 表示读取模式
            data = json.load(f)  # 调用 json.load() 方法从文件对象中读取并反序列化 JSON 数据
            info(f"JSON 文件加载成功: {file_path}")
            return data
    except json.JSONDecodeError as e:
        error(f"加载 JSON 文件失败 - JSON 解析错误: {file_path}, 错误信息: {e}")
        return default
    except PermissionError as e:
        error(f"加载 JSON 文件失败 - 权限错误: {file_path}, 错误信息: {e}")
        return default
    except OSError as e:
        error(f"加载 JSON 文件失败 - 操作系统错误: {file_path}, 错误信息: {e}")
        return default
    except Exception as e:
        error(f"加载 JSON 文件失败 - 未知错误: {file_path}, 错误信息: {e}")
        return default


def json_cache(cache_path: Union[str, Path]):  # 定义 JSON 缓存装饰器工厂函数，接收缓存文件路径作为参数
    """
    JSON 缓存装饰器，用于缓存函数返回结果
    :param cache_path: 缓存文件路径
    """
    def decorator(func):  # 定义内层装饰器函数，接收被装饰的函数作为参数
        def wrapper(*args, **kwargs):  # 定义包装函数，接收任意位置参数和关键字参数
            try:
                cached_data = load_json(cache_path)  # 调用 load_json 函数尝试加载缓存数据
                if cached_data is not None:  # 判断缓存数据是否存在
                    info(f"使用缓存数据: {cache_path}")
                    return cached_data  # 如果缓存存在，直接返回缓存数据，不再执行原函数
            except Exception as e:
                warn(f"加载缓存失败，将重新执行原函数: {e}")
            
            try:
                result = func(*args, **kwargs)  # 如果缓存不存在，调用原函数并获取结果
                
                try:
                    save_json(result, cache_path)  # 将原函数的结果保存到缓存文件
                except Exception as e:
                    warn(f"保存缓存失败: {e}")
                
                return result  # 返回原函数的结果
            except Exception as e:
                error(f"执行被装饰函数时发生错误: {func.__name__}, 错误信息: {e}")
                raise  # 重新抛出异常，让调用者处理
        return wrapper  # 返回包装函数
    return decorator  # 返回装饰器函数


def clear_cache(cache_dir: Optional[Path] = None, 
                prefix: Optional[str] = None, 
                filename: Optional[str] = None,
                text_hash: Optional[str] = None,
                delete_all: bool = False) -> int:  # 定义清理缓存的函数，支持多种删除方式
    """
    清理缓存文件，支持多种删除方式
    
    :param cache_dir: 缓存目录
    :param prefix: 按文件名前缀删除（如 "chunks"、"tokens"）
    :param filename: 按完整文件名删除（包括 .json 后缀）
    :param text_hash: 按文本哈希值删除（会删除所有包含该哈希的缓存）
    :param delete_all: 是否删除所有缓存
    :return: 删除的文件数量
    """
    cache_dir = cache_dir or DEFAULT_CACHE_DIR  # 设置缓存目录，使用传入的目录或默认目录
    
    if not cache_dir.exists():  # 使用 Path.exists 检查缓存目录是否存在
        info(f"缓存目录不存在: {cache_dir}")  # 记录信息日志
        return 0  # 返回 0 表示没有删除任何文件
    
    count = 0  # 初始化删除文件计数器
    
    # 遍历所有 JSON 缓存文件
    for file_path in cache_dir.glob("*.json"):  # 使用 Path.glob 遍历所有 JSON 文件
        delete_this_file = False
        
        if delete_all:  # 如果要删除所有文件
            delete_this_file = True
        elif filename and file_path.name == filename:  # 按完整文件名匹配
            delete_this_file = True
        elif text_hash and text_hash in file_path.name:  # 按哈希值匹配
            delete_this_file = True
        elif prefix and file_path.name.startswith(prefix):  # 按前缀匹配
            delete_this_file = True
        
        if delete_this_file:
            try:
                file_path.unlink()  # 使用 Path.unlink 删除文件
                info(f"删除缓存文件: {file_path}")  # 记录删除成功的日志
                count += 1  # 计数器加 1
            except Exception as e:
                warn(f"删除缓存文件失败: {file_path}, 错误: {e}")  # 记录警告日志
    
    success(f"缓存清理完成，共删除 {count} 个文件")  # 记录缓存清理完成的日志
    return count  # 返回删除的文件数量

