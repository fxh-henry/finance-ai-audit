# -*- coding: utf-8 -*-
"""
盘点发票材料：看每张真实发票能解析出什么字段
================================================================
用途：造用例之前先摸清"原材料"。
用真实发票当模板，只改需要改的字段（金额 / 城市 / 项目名称 / 日期），
这样票面结构是真的，被注入的字段是可控的、有标签的。

不烧 token：PDF 走本地坐标解析（pdfplumber），XML 直接解析。

用法：
    python -m eval.survey_invoices
    python -m eval.survey_invoices --dir 发票材料
================================================================
"""
import argparse  # 命令行参数
import glob  # 文件枚举
import os  # 路径
import sys  # 路径
from pathlib import Path  # 路径

ROOT = Path(__file__).resolve().parent.parent  # 项目根目录
if str(ROOT) not in sys.path:  # 保证能 import 到项目模块
    sys.path.insert(0, str(ROOT))

from eval.replay import silence_project_logs  # 复用日志压制


def survey(base_dir):
    """遍历发票目录，打印每张票的关键字段。"""
    from recognizers.invoice_recognizer import recognize_invoice  # 统一识别入口

    files = []  # 待处理文件
    for pat in ("pdf/*", "xml/*.xml", "国际发票/*"):  # 三类来源
        files += sorted(glob.glob(os.path.join(base_dir, pat)))

    rows = []  # 收集结果，便于后续统计
    for fp in files:  # 逐张处理
        name = os.path.basename(fp)  # 文件名
        try:
            data = recognize_invoice(fp)  # 识别
        except Exception as exc:  # 单张失败不影响整体
            print(f"ERR  {name[:50]}  {exc}")
            continue
        if data.get("error"):  # 识别器自己报的错
            print(f"ERR  {name[:50]}  {data['error']}")
            continue
        items = data.get("明细列表") or []  # 明细行
        rows.append({
            "file": name,
            "no": data.get("发票号码") or "",
            "date": data.get("开票日期") or "",
            "amount": data.get("价税合计小写") or "",
            "buyer": data.get("购买方名称") or "",
            "buyer_tax": data.get("购买方税号") or "",
            "seller": data.get("销售方名称") or "",
            "items": [i.get("项目名称") for i in items],
        })
        print(f"--- {name[:52]}")
        print(f"    号码: {data.get('发票号码')} | 类型: {data.get('发票类型')}")
        print(f"    日期: {data.get('开票日期')} | 金额: {data.get('价税合计小写')}")
        print(f"    购买方: {data.get('购买方名称')} | {data.get('购买方税号')}")
        print(f"    销售方: {data.get('销售方名称')}")
        print(f"    明细: {len(items)} {[i.get('项目名称') for i in items][:3]}")

    # ---- 汇总：去重后的主体和费用类型分布，用于判断"原材料够不够" ----
    buyers = {}  # 购买方 → 张数
    for r in rows:
        buyers[r["buyer"]] = buyers.get(r["buyer"], 0) + 1
    nums = [r["no"] for r in rows if r["no"]]  # 有号码的
    dup = {n for n in nums if nums.count(n) > 1}  # 重复号码（同一张票的多个文件）

    print("=" * 78)
    print(f"合计解析成功 {len(rows)} 张")
    print(f"购买方主体分布：{buyers}")
    print(f"重复发票号码（同一张票的多个文件）：{sorted(dup) if dup else '无'}")
    print("=" * 78)
    return rows


def main():
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="盘点发票材料")
    parser.add_argument("--dir", default=str(ROOT / "发票材料"), help="发票材料目录")
    args = parser.parse_args()
    silence_project_logs()  # 压掉识别过程的日志
    survey(args.dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())