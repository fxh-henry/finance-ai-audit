# -*- coding: utf-8 -*-
import json
from alibabacloud_ocr_api20210707.client import Client as OcrClient
from alibabacloud_tea_openapi import models as open_api_models
from alibabacloud_darabonba_stream.client import Client as StreamClient
from alibabacloud_ocr_api20210707 import models as ocr_models
from alibabacloud_tea_util import models as util_models

from config.settings import OCR_ACCESS_KEY_ID, OCR_ACCESS_KEY_SECRET, OCR_ENDPOINT

# ===== 创建客户端（密钥从 config.settings 读，不再硬编码） =====
config = open_api_models.Config(
    access_key_id=OCR_ACCESS_KEY_ID,
    access_key_secret=OCR_ACCESS_KEY_SECRET,
    endpoint=OCR_ENDPOINT,
)
client = OcrClient(config)

# 函数作用：传入一张发票图片路径，返回统一格式的字段字典
def recognize_invoice(image_path):

    #读取本地图片文件为二进制流
    # 注意：这里的文件名必须是英文
    body_stream = StreamClient.read_from_file_path(image_path)
    # ocr_models.RecognizeGeneralStructureRequest() 创建"通用票证抽取"请求
    request = ocr_models.RecognizeGeneralStructureRequest(body=body_stream)
    # 可以设置超时时间、重试次数等，这里用默认配置
    runtime = util_models.RuntimeOptions(
        connect_timeout=10000,  # 10秒（单位是毫秒）
        read_timeout=60000,  # 60秒（单位是毫秒）
    )

    # 这是真正发起HTTP请求的地方，把图片发给阿里云服务器，等待识别结
    # 返回的 resp 是响应对象，包含HTTP状态码、响应头、响应体
    resp = client.recognize_general_structure_with_options(request, runtime)

    # 第36行：把响应体转成字典
    # resp.body 是响应体，.data 是响应体里的数据字段
    # .to_map() 是SDK对象的方法，把对象转成Python字典
    # 转成字典后才能用 ["key"] 的方式取值
    result = resp.body.data.to_map()

    # 提取真正的字段数据
    data = result["SubImages"][0]["KvInfo"]["Data"]

    # 统一字段格式
    normalized = {
        "发票类型": data.get("发票名称", ""),
        "发票号码": data.get("发票号码", ""),
        "开票日期": data.get("开票日期", ""),
        "购买方名称": data.get("购买方名称", ""),
        "购买方税号": data.get("购买方统一社会信用代码/纳税人识别号", ""),
        "销售方名称": data.get("销售方名称", ""),
        "销售方税号": data.get("销售方统一社会信用代码/纳税人识别号", ""),
        "不含税金额": data.get("金额", ""),
        "税额": data.get("税额", ""),
        "税率": data.get("税率/征收率", ""),
        "价税合计小写": data.get("价税合计(小写)", "").replace("￥", ""),
        "价税合计大写": data.get("价税合计(大写)", ""),
        "项目名称": data.get("项目名称", ""),
        "规格型号": data.get("规格型号", ""),
        "单位": data.get("单位", ""),
        "数量": data.get("数量", ""),
        "单价": data.get("单价", ""),
        "金额": data.get("金额", ""),
        "备注": data.get("备注", ""),
        "开票人": data.get("开票人", ""),
    }

    return normalized

# ============================================================================
# 下面这段只在"直接运行本文件"时执行（python recognizers/ocr_service.py）
# 被别人 import 时不会执行，避免 import 就去读桌面文件 / 调阿里云 OCR
# ============================================================================
if __name__ == "__main__":
    image_path = r"C:\Users\Administrator\Desktop\invoice2.jpg"  # 改成你要测的图片路径
    result = recognize_invoice(image_path)
    print("=== 统一格式的识别结果 ===")
    print(json.dumps(result, ensure_ascii=False, indent=2))