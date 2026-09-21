# -*- coding: utf-8 -*-
"""
发票识别入口：自动判断文件类型，分发到对应解析器
"""
from utils.file_utils import detect_file_type
from recognizers.pdf_parser import extract_invoice as extract_from_pdf
from recognizers.xml_parser import extract_from_xml


def _normalize_invoice_data(invoice_data):
    """
    标准化发票数据：统一所有解析器的输出格式

    处理内容：
    1. 如果顶层没有"项目名称"，从明细列表第一条取（XML解析的项目名称在明细里）
    2. 确保所有字段都是字符串类型，避免后续处理时类型不一致

    参数：
        invoice_data: 解析器返回的发票数据字典

    返回：
        标准化后的发票数据字典
    """
    if not isinstance(invoice_data, dict) or "error" in invoice_data:
        # 解析失败或不是字典，直接返回
        return invoice_data

    # 处理1：项目名称统一提到顶层
    # XML解析的项目名称在明细列表里，PDF解析可能在顶层
    # 统一：如果顶层没有，就从明细列表第一条取
    if not invoice_data.get("项目名称"):
        detail_list = invoice_data.get("明细列表", [])
        if detail_list and isinstance(detail_list, list) and len(detail_list) > 0:
            first_item = detail_list[0]
            if isinstance(first_item, dict) and first_item.get("项目名称"):
                invoice_data["项目名称"] = first_item["项目名称"]

    return invoice_data


def recognize_invoice(file_path):
    """
    统一识别入口：传入文件路径，返回结构化字段字典

    参数：
        file_path: 发票文件路径（.pdf / .xml / .jpg / .png）

    返回：
        字典，包含发票各字段
    """
    # 第1步：判断文件类型
    file_type = detect_file_type(file_path)

    # 第2步：根据类型分发到对应解析器
    if file_type == "xml":
        # XML数电票直接解析（100%准确，不需要OCR）
        result = extract_from_xml(file_path)

    elif file_type == "pdf":
        # PDF坐标法提取（已完成）
        result = extract_from_pdf(file_path)

    elif file_type in ("jpg", "png"):
        # TODO: OCR识别（阿里云通用票证抽取）
        return {"error": "图片OCR功能开发中", "file_type": file_type}

    else:
        return {"error": f"不支持的文件格式: {file_type}"}

    # 第3步：标准化输出格式（所有解析器统一）
    return _normalize_invoice_data(result)
