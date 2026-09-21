# -*- coding: utf-8 -*-
# ============================================================================
# 数电发票XML解析器
# ============================================================================
# 核心原理：
#   数电发票XML是法定原件，数据100%准确，直接按标签路径读取即可，
#   不需要OCR、不需要坐标法、不需要正则表达式。
#
# XML结构：
#   EInvoice
#     ├── Header                    头部信息
#     │     ├── EIid                发票号码
#     │     └── InherentLabel       固有标签（专票/普票等）
#     ├── EInvoiceData              发票数据
#     │     ├── SellerInformation   销售方信息
#     │     ├── BuyerInformation    购买方信息
#     │     ├── BasicInformation    基本信息（金额汇总、开票人）
#     │     ├── IssuItemInformation 明细信息（可有多条）
#     │     └── AdditionalInformation 备注
#     └── TaxSupervisionInfo        税务监管信息（开票日期等）
# ============================================================================

# 导入XML解析模块，Python标准库自带，不需要安装
import xml.etree.ElementTree as ET


def safe_find(element, path, default=""):
    """
    安全的XML节点查找：找不到返回默认值，不报错

    参数：
        element: 父节点
        path: 节点路径，如 "SellerInformation/SellerName"
        default: 找不到时的返回值

    返回：
        节点的文本内容，找不到返回default
    """
    # find()按路径查找子节点，找不到返回None
    node = element.find(path)
    # 如果节点存在且有文本内容，返回文本（去掉首尾空格）
    if node is not None and node.text:
        return node.text.strip()
    # 否则返回默认值
    return default


def extract_from_xml(xml_path):
    """
    从数电发票XML文件中提取结构化字段

    参数：
        xml_path: XML文件路径

    返回：
        字典，包含所有发票字段（格式与PDF解析器一致）
    """
    # ========================================================================
    # 第1步：解析XML文件，得到根节点
    # ========================================================================
    # ET.parse() 读取XML文件，返回ElementTree对象
    # .getroot() 获取根节点 <EInvoice>
    tree = ET.parse(xml_path)
    root = tree.getroot()

    # ========================================================================
    # 第2步：提取头部信息（发票类型、发票号码）
    # ========================================================================
    header = root.find("Header")

    # 发票号码：Header/EIid
    invoice_number = safe_find(header, "EIid")

    # 发票种类（专票/普票）：Header/InherentLabel/GeneralOrSpecialVAT/LabelName
    # 如 "增值税专用发票" 或 "增值税普通发票"
    invoice_species = safe_find(header, "InherentLabel/GeneralOrSpecialVAT/LabelName")

    # 发票类型：Header/InherentLabel/EInvoiceType/LabelName
    # 如 "电子发票"
    invoice_type_name = safe_find(header, "InherentLabel/EInvoiceType/LabelName")
    # 组合成完整的发票类型，如 "电子发票（增值税专用发票）"
    invoice_type = f"{invoice_type_name}（{invoice_species}）" if invoice_species else invoice_type_name

    # ========================================================================
    # 第3步：提取销售方和购买方信息
    # ========================================================================
    data = root.find("EInvoiceData")

    # 销售方信息
    seller = data.find("SellerInformation")
    seller_name = safe_find(seller, "SellerName")       # 销售方名称
    seller_tax_id = safe_find(seller, "SellerIdNum")    # 销售方税号

    # 购买方信息
    buyer = data.find("BuyerInformation")
    buyer_name = safe_find(buyer, "BuyerName")          # 购买方名称
    buyer_tax_id = safe_find(buyer, "BuyerIdNum")       # 购买方税号

    # ========================================================================
    # 第4步：提取基本信息（金额、开票人）
    # ========================================================================
    basic = data.find("BasicInformation")

    # 不含税总金额
    total_without_tax = safe_find(basic, "TotalAmWithoutTax")
    # 总税额
    total_tax = safe_find(basic, "TotalTaxAm")
    # 价税合计（小写）
    total_with_tax = safe_find(basic, "TotalTax-includedAmount")
    # 价税合计（大写）
    total_with_tax_cn = safe_find(basic, "TotalTax-includedAmountInChinese")
    # 开票人
    drawer = safe_find(basic, "Drawer")

    # ========================================================================
    # 第5步：提取开票日期
    # ========================================================================
    # 开票日期在 TaxSupervisionInfo/IssueTime，格式 "2026-08-31"
    tax_supervision = root.find("TaxSupervisionInfo")
    issue_date = safe_find(tax_supervision, "IssueTime")
    # 转成 "2026年08月31日" 格式，与PDF解析器一致
    if issue_date and len(issue_date) >= 10:
        year, month, day = issue_date[:4], issue_date[5:7], issue_date[8:10]
        issue_date = f"{year}年{month}月{day}日"

    # ========================================================================
    # 第6步：提取明细列表（可以有多条）
    # ========================================================================
    # findall() 返回所有匹配的节点列表
    item_nodes = data.findall("IssuItemInformation")

    detail_list = []
    for item in item_nodes:
        # 税率在XML里是小数（如0.13），转成百分比（13%）
        tax_rate = safe_find(item, "TaxRate")
        if tax_rate:
            try:
                tax_rate = f"{int(float(tax_rate) * 100)}%"
            except:
                pass

        # 组装一条明细
        detail = {
            "项目名称": safe_find(item, "ItemName"),
            "规格型号": "",  # XML里没有规格型号字段，留空
            "单位": safe_find(item, "MeaUnits"),
            "数量": safe_find(item, "Quantity"),
            "单价": safe_find(item, "UnPrice"),
            "金额": safe_find(item, "Amount"),
            "税率": tax_rate,
            "税额": safe_find(item, "ComTaxAm"),
        }
        detail_list.append(detail)

    # 取第一条明细的税率作为发票整体税率（如果有多条且税率不同，后续可以处理）
    invoice_tax_rate = detail_list[0]["税率"] if detail_list else ""

    # ========================================================================
    # 第7步：提取备注
    # ========================================================================
    # 备注在 AdditionalInformation 节点，可能为空
    remark = safe_find(data, "AdditionalInformation")

    # ========================================================================
    # 第8步：组装统一格式的结果
    # ========================================================================
    result = {
        "发票类型": invoice_type,
        "发票种类": invoice_species,
        "发票号码": invoice_number,
        "开票日期": issue_date,
        "购买方名称": buyer_name,
        "购买方税号": buyer_tax_id,
        "销售方名称": seller_name,
        "销售方税号": seller_tax_id,
        "不含税金额": total_without_tax,
        "税额": total_tax,
        "税率": invoice_tax_rate,
        "价税合计小写": total_with_tax,
        "价税合计大写": total_with_tax_cn,
        "明细列表": detail_list,
        "备注": remark,
        "开票人": drawer,
    }

    return result


# ============================================================================
# 测试代码
# ============================================================================
if __name__ == "__main__":
    import json
    from pathlib import Path

    # 获取项目根目录：当前文件在 recognizers/ 下，.parent 就是项目根目录
    # Path(__file__) = D:\agent\财务报销项目\recognizers\xml_parser.py
    # .parent = D:\agent\财务报销项目\recognizers
    # .parent.parent = D:\agent\财务报销项目
    project_root = Path(__file__).parent.parent

    # 拼接测试文件路径（用 / 运算符，跨平台兼容）
    xml_path = project_root / "发票材料" / "xml" / "dzfp_26322000006822375241_20260820095339.xml"

    print(f"测试文件路径: {xml_path}")
    result = extract_from_xml(xml_path)

    print("=" * 60)
    print("XML解析结果")
    print("=" * 60)
    # ensure_ascii=False 让中文正常显示，indent=2 格式化缩进
    print(json.dumps(result, ensure_ascii=False, indent=2))
