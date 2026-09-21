# -*- coding: utf-8 -*-
# ============================================================================
# 统一发票字段提取模块（坐标法版本）
# ============================================================================
# 核心思路：
#   发票的明细表格是固定的8列结构：
#   项目名称 | 规格型号 | 单位 | 数量 | 单价 | 金额 | 税率 | 税额
#   每一列在页面上的x坐标位置是固定的。
#   所以我们先读表头，拿到每列的中心点x坐标，再计算列边界，
#   最后把明细行的每个文字块按x坐标分配到对应列。
#
# 优点：
#   1. 不会因为某列为空（如规格型号为空）而错位
#   2. 支持多行明细
#   3. 项目名称含空格也不会被拆错
#   4. 能检测到跨列的粘连文字块（如数量+单价粘在一起）
# ============================================================================

# 导入正则表达式模块，用于提取发票号码、日期等基本字段
import re
# 导入json模块，用于格式化打印结果
import json
# 导入pdfplumber，用于读取PDF并提取文字块（带坐标信息）
import pdfplumber


# ============================================================================
# 工具函数：安全的正则搜索
# ============================================================================
def safe_search(pattern, text, default=""):
    """
    用正则表达式搜索文本，找到就返回第1个捕获组，找不到返回默认值
    作用：避免找不到时程序报错崩溃

    参数：
        pattern: 正则表达式，如 r"发票号码[：:]\s*(\S+)"
        text: 要搜索的文本
        default: 找不到时的返回值，默认空字符串
    """
    # re.search在text中搜索pattern，找到返回Match对象，找不到返回None
    match = re.search(pattern, text)
    # 如果找到了，返回第1个括号捕获的内容，并去掉首尾空格
    if match:
        return match.group(1).strip()
    # 没找到就返回默认值
    return default


# ============================================================================
# 核心函数1：从表头行识别8个标准列及其中心点坐标
# ============================================================================
def get_header_columns(header_words):
    """
    从表头行的文字块中，用关键词匹配识别8个标准列，返回每列的中心点x坐标

    为什么不用简单的间距合并？
      因为不同发票的列间距不一样：
      - 保险发票："规格型号"和"单位"间距14像素（很近）
      - 酒店发票："单"和"位"间距9像素（字被拆开了）
      用固定阈值会把不该合并的合并了，或者该合并的没合并。
      所以改用关键词匹配，更准确。

    参数：
        header_words: 表头行所有文字块的列表，每个文字块是字典
                      包含 text（文字）、x0（左x坐标）、x1（右x坐标）、top（y坐标）

    返回：
        列表，每个元素是 {"name": 列名, "center": 中心点x坐标, "x0": 左边界, "x1": 右边界}
    """
    # 第1步：把表头文字块按x0（左x坐标）从小到大排序
    # 这样文字就是从左到右排列的
    sorted_words = sorted(header_words, key=lambda w: w["x0"])

    # 第2步：定义8个标准列的关键词匹配规则
    # 每个规则是 (列名, 匹配函数)
    # 匹配函数接收文字内容，返回True表示这个文字块属于该列
    column_rules = [
        ("项目名称", lambda t: "项目" in t),       # 包含"项目"就是项目名称列
        ("规格型号", lambda t: "规格" in t),       # 包含"规格"就是规格型号列
        ("单位",     lambda t: t == "单位" or t == "单"),  # "单位"或拆开的"单"
        ("数量",     lambda t: t == "数量" or t == "数"),  # "数量"或拆开的"数"
        ("单价",     lambda t: t == "单价" or t == "单"),  # "单价"或拆开的"单"
        ("金额",     lambda t: t == "金额" or t == "金"),  # "金额"或拆开的"金"
        ("税率",     lambda t: "税率" in t),       # 包含"税率"（如"税率/征收率"）
        ("税额",     lambda t: t == "税额" or t == "税"),  # "税额"或拆开的"税"
    ]

    # 第3步：遍历8个列规则，逐个匹配
    columns = []            # 存放识别到的列
    used_indices = set()    # 记录已经被使用的文字块索引，避免重复匹配

    for col_name, match_func in column_rules:
        # 遍历所有表头文字块，找第一个匹配且未被使用的
        for i, w in enumerate(sorted_words):
            # 跳过已经被其他列使用的文字块
            if i in used_indices:
                continue

            text = w["text"]  # 文字块的文字内容

            # 用匹配函数判断这个文字块是不是当前列
            if match_func(text):
                # 找到了！记录这一列的坐标
                center = (w["x0"] + w["x1"]) / 2  # 中心点 = (左x + 右x) / 2
                x0 = w["x0"]                     # 左边界
                x1 = w["x1"]                     # 右边界
                used_indices.add(i)              # 标记这个文字块已被使用

                # 第4步：特殊处理——单位、数量、单价、金额、税额可能被拆成两个字
                # 例如"单"后面跟着"位"，需要合并成"单位"
                # 判断逻辑：当前文字是"单"，下一个文字是"位"，且间距小于20像素
                if text == "单" and i + 1 < len(sorted_words):
                    next_w = sorted_words[i + 1]
                    if next_w["text"] == "位" and next_w["x0"] - w["x1"] < 20:
                        x1 = next_w["x1"]               # 右边界扩展到"位"的右边界
                        center = (x0 + x1) / 2          # 重新计算中心点
                        used_indices.add(i + 1)         # 标记"位"也被使用了

                # 同理处理"数"+"量"
                elif text == "数" and i + 1 < len(sorted_words):
                    next_w = sorted_words[i + 1]
                    if next_w["text"] == "量" and next_w["x0"] - w["x1"] < 20:
                        x1 = next_w["x1"]
                        center = (x0 + x1) / 2
                        used_indices.add(i + 1)

                # 同理处理"单"+"价"（单价的单）
                elif text == "单" and i + 1 < len(sorted_words):
                    next_w = sorted_words[i + 1]
                    if next_w["text"] == "价" and next_w["x0"] - w["x1"] < 20:
                        x1 = next_w["x1"]
                        center = (x0 + x1) / 2
                        used_indices.add(i + 1)

                # 同理处理"金"+"额"
                elif text == "金" and i + 1 < len(sorted_words):
                    next_w = sorted_words[i + 1]
                    if next_w["text"] == "额" and next_w["x0"] - w["x1"] < 20:
                        x1 = next_w["x1"]
                        center = (x0 + x1) / 2
                        used_indices.add(i + 1)

                # 同理处理"税"+"额"（税额的税）
                elif text == "税" and i + 1 < len(sorted_words):
                    next_w = sorted_words[i + 1]
                    if next_w["text"] == "额" and next_w["x0"] - w["x1"] < 20:
                        x1 = next_w["x1"]
                        center = (x0 + x1) / 2
                        used_indices.add(i + 1)

                # 把这一列加入结果列表
                columns.append({"name": col_name, "center": center, "x0": x0, "x1": x1})
                break  # 找到一个就跳出，继续匹配下一列

    return columns


# ============================================================================
# 核心函数2：智能拆分粘连的"数量单价"字符串
# ============================================================================
def smart_split_quantity_price(merged_str, total_amount):
    """
    当数量和单价被pdfplumber识别成一个文字块时（如"12437.735849056604"），
    用业务逻辑"数量 × 单价 ≈ 金额"来反推正确的分割点。

    原理：
      已知不含税金额 = 2437.74
      粘连字符串 = "12437.735849056604"
      尝试在每个位置分割：
        在第1位切：数量="1"，单价="2437.735849056604"
                  1 × 2437.7358 = 2437.74 ≈ 金额 ✓ 正确！
        在第2位切：数量="12"，单价="437.7358..."
                  12 × 437.74 = 5252.83 ≠ 金额 ✗
      选误差最小的那个分割点。

    参数：
        merged_str: 粘连的字符串，如 "12437.735849056604"
        total_amount: 已知的不含税金额，如 "2437.74"

    返回：
        (数量字符串, 单价字符串) 元组，拆分失败返回 ("", "")
    """
    # 去掉首尾空格
    merged_str = merged_str.strip()
    # 把金额转成浮点数，后面用来比较
    total_amount = float(total_amount)

    # 初始化最优解
    best_qty = ""       # 最优的数量
    best_price = ""     # 最优的单价
    min_diff = float('inf')  # 最小误差，初始化为无穷大

    # 遍历所有可能的分割位置（从第1个字符后到最后一个字符前）
    for i in range(1, len(merged_str)):
        # 在位置i分割：左边是数量，右边是单价
        qty_str = merged_str[:i]    # 从开头到第i个字符（不包含i）
        price_str = merged_str[i:]  # 从第i个字符到结尾

        # 尝试把两边转成数字，转不了就跳过这个分割位置
        try:
            qty = float(qty_str)
            price = float(price_str)
        except ValueError:
            continue  # 分割后有一边不是数字，跳过

        # 计算 数量 × 单价
        calc = qty * price
        # 计算和真实金额的误差（绝对值）
        diff = abs(calc - total_amount)

        # 如果误差小于总金额的1%，且比之前的最优解误差更小
        if diff < total_amount * 0.01 and diff < min_diff:
            # 更新最优解
            min_diff = diff
            best_qty = qty_str
            best_price = price_str

    # 返回最优的数量和单价
    return best_qty, best_price


# ============================================================================
# 核心函数3：用坐标法提取明细表格
# ============================================================================
def extract_detail_by_coordinates(page, total_amount):
    """
    用坐标法从PDF页面提取明细表格

    步骤：
      1. 找到表头行（包含"项目名称"的行）
      2. 用关键词匹配得到8列的中心点x坐标
      3. 计算列边界（相邻列中心点的中点）
      4. 找到明细行区域（表头和合计行之间）
      5. 按y坐标分组（同一行的文字y坐标接近）
      6. 每个文字块按中心点x坐标分配到对应列
      7. 检测跨列文字块（数量+单价粘连），用业务逻辑拆分

    参数：
        page: pdfplumber的页面对象
        total_amount: 不含税总金额（用于智能拆分数量单价）

    返回：
        列表，每个元素是一行明细的字典
    """
    # 第1步：提取页面上所有文字块
    # 每个文字块包含：text（文字）、x0（左x）、x1（右x）、top（y坐标）等
    words = page.extract_words()

    # 第2步：找到表头行的y坐标
    # 表头行一定包含"项目名称"这四个字
    header_y = None
    for w in words:
        if "项目名称" in w["text"]:
            header_y = w["top"]  # 记录表头行的y坐标
            break
    # 如果没找到表头，返回空列表
    if header_y is None:
        return []

    # 第3步：提取表头行所有文字块（y坐标接近header_y，容差5像素）
    header_words = [w for w in words if abs(w["top"] - header_y) < 5]

    # 第4步：用关键词匹配得到8列的中心点坐标
    columns = get_header_columns(header_words)
    # 提取列名列表，如 ["项目名称", "规格型号", "单位", ...]
    col_names = [c["name"] for c in columns]

    # 如果识别到的列少于3列，说明表头识别失败，返回空
    if len(columns) < 3:
        return []

    # 第5步：计算列边界
    # 列边界 = 相邻两列中心点的中点
    # 例如：项目名称center=65，规格型号center=184，边界=(65+184)/2=124.5
    # 中心点小于124.5的文字属于项目名称列，大于124.5的属于规格型号列
    boundaries = []
    for i in range(1, len(columns)):
        boundary = (columns[i - 1]["center"] + columns[i]["center"]) / 2
        boundaries.append(boundary)

    # 计算每列的左右边界（方便后面判断跨列）
    # col_left[i] = 第i列的左边界，col_right[i] = 第i列的右边界
    col_left = [0] + boundaries       # 第一列左边界是0（页面最左）
    col_right = boundaries + [9999]   # 最后一列右边界是9999（页面最右）

    # 第6步：找到合计行的y坐标（明细区域的下边界）
    total_y = None
    for w in words:
        # 合计行有"合"或"计"字，且在表头下方
        if w["text"] in ("合", "计") and w["top"] > header_y + 5:
            total_y = w["top"]
            break

    # 第7步：筛选明细区域的文字块（表头下方、合计行上方）
    if total_y:
        detail_words = [w for w in words if header_y + 5 < w["top"] < total_y - 5]
    else:
        # 没找到合计行就取表头下方所有文字
        detail_words = [w for w in words if w["top"] > header_y + 5]

    # 第8步：按y坐标分组（同一行的文字y坐标接近，容差8像素）
    # 为什么需要容差？因为同一行的文字y坐标可能有微小差异（如160.3和160.5）
    rows = {}  # key是y坐标，value是这一行的所有文字块
    for w in detail_words:
        # 遍历已有的行，找y坐标差小于8的
        found_key = None
        for y_key in rows:
            if abs(w["top"] - y_key) < 8:
                found_key = y_key
                break
        # 没找到就新建一行
        if found_key is None:
            found_key = round(w["top"], 1)
            rows[found_key] = []
        # 把文字块加入这一行
        rows[found_key].append(w)

    # 第8.5步：项目名称换行预合并（关键修复）
    # 场景：项目名称太长，在PDF里被分成两行
    #   第一行：*金融服务*1727-道路危  1  1132.08  1132.08  6%  67.92（6个文字块）
    #   第二行：险货运承运人责任保险（只有1个文字块，且在项目名称列x范围内）
    # 处理：如果某一行只有1个文字块，且x0 < 第一列边界（项目名称列右边界），
    #       就把它合并到上一行，不单独成行
    sorted_ys = sorted(rows.keys())
    merged_rows = {}  # 合并后的行
    prev_y = None     # 上一行的y坐标
    for y in sorted_ys:
        row_words = rows[y]
        # 判断是否是项目名称换行：只有1个文字块，且x0在项目名称列范围内
        if len(row_words) == 1 and prev_y is not None and len(boundaries) > 0:
            w = row_words[0]
            # 项目名称列右边界 = boundaries[0]（项目名称和规格型号之间的边界）
            if w["x0"] < boundaries[0]:
                # 合并到上一行
                merged_rows[prev_y].append(w)
                continue  # 不单独成行
        # 正常行，保留
        merged_rows[y] = row_words
        prev_y = y

    # 第9步：逐行提取明细数据
    result = []
    for y in sorted(merged_rows.keys()):  # 按y坐标从小到大（从上到下）
        # 把这一行的文字块按x坐标排序（从左到右）
        row_words = sorted(merged_rows[y], key=lambda x: x["x0"])

        # 跳过文字太少的行（可能是噪点）
        if len(row_words) < 2:
            continue

        # 初始化这一行的8个字段，默认空字符串
        row_data = {name: "" for name in col_names}

        # 记录跨列的粘连文字块（后面统一处理）
        spanning_words = []

        # 第10步：遍历这一行的每个文字块，分配到对应列
        for w in row_words:
            # 计算文字块的中心点x坐标
            center = (w["x0"] + w["x1"]) / 2

            # 根据中心点判断属于哪一列
            # 遍历列边界，中心点大于边界就往右移一列
            col_idx = 0
            for b in boundaries:
                if center > b:
                    col_idx += 1

            # 如果列索引超出范围，跳过
            if col_idx >= len(col_names):
                continue

            # 第11步：检测这个文字块是否跨列
            # 正常文字块：x0 > 列左边界，x1 < 列右边界
            # 跨列文字块：x0 < 列左边界 或 x1 > 列右边界
            # （如数量"1"和单价"2437.73..."粘在一起，从数量列跨到单价列）
            is_spanning = False
            if col_idx < len(col_left):
                left_bound = col_left[col_idx]    # 这一列的左边界
                right_bound = col_right[col_idx]  # 这一列的右边界
                # 容差2像素，避免边界判断误差
                if w["x0"] < left_bound - 2 or w["x1"] > right_bound + 2:
                    is_spanning = True

            if is_spanning:
                # 跨列文字块先记录下来，后面用业务逻辑拆分
                spanning_words.append((col_idx, w))
            else:
                # 正常文字块，追加到对应列
                # （同一列可能有多个文字块，如项目名称被拆成多块，需要拼接）
                if row_data[col_names[col_idx]]:
                    row_data[col_names[col_idx]] += w["text"]
                else:
                    row_data[col_names[col_idx]] = w["text"]

        # 第12步：处理跨列的粘连文字块
        for col_idx, w in spanning_words:
            merged_text = w["text"]  # 粘连的文字，如 "12437.735849056604"
            # 用业务逻辑智能拆分数量和单价
            qty, price = smart_split_quantity_price(merged_text, total_amount)

            if qty and price:
                # 拆分成功，分别填入数量列和单价列
                for i, name in enumerate(col_names):
                    if name == "数量":
                        row_data[name] = qty
                    if name == "单价":
                        row_data[name] = price
            else:
                # 拆分失败，把整个文字块放到起始列
                if row_data[col_names[col_idx]]:
                    row_data[col_names[col_idx]] += merged_text
                else:
                    row_data[col_names[col_idx]] = merged_text

        # 第13步：只有至少有项目名称或金额时，才保留这一行
        # （过滤掉空行或噪点行）
        if row_data.get("项目名称") or row_data.get("金额"):
            result.append(row_data)

    # 第14步：项目名称换行合并
    # 场景：项目名称太长，在PDF里被分成两行显示
    #   第一行：*金融服务*1727-道路危  1  1132.08  1132.08  6%  67.92
    #   第二行：险货运承运人责任保险（只有项目名称，其他列全空）
    # 处理：如果某一行只有项目名称有值，其他列全空，就把它追加到上一行的项目名称后面
    merged_result = []  # 合并后的结果
    for row in result:
        # 判断这一行是不是"只有项目名称有值"
        # 即：项目名称非空，且其他所有列（规格型号、单位、数量、单价、金额、税率、税额）都是空的
        other_cols_empty = True
        for col in col_names:
            if col != "项目名称" and row.get(col, "").strip():
                other_cols_empty = False
                break

        # 如果是"只有项目名称有值"的行，且前面已有行，就合并到上一行
        if row.get("项目名称") and other_cols_empty and merged_result:
            # 把当前行的项目名称追加到上一行的项目名称后面
            merged_result[-1]["项目名称"] += row["项目名称"]
        else:
            # 正常行，直接加入结果
            merged_result.append(row)

    return merged_result


# ============================================================================
# 辅助函数：提取备注栏内容
# ============================================================================
def extract_remark(text):
    """
    提取备注栏内容
    注意：电子发票的备注内容通常在"备\n注"标题的上方，不是下方

    文本结构：
      价税合计（大写）...
      酒店名称：富驿时尚酒店... 共3晚   ← 这是备注内容
      备                              ← 竖排"备注"标题
      注
      开票人：邢婕
    """
    # 按换行符把文本分成一行一行
    lines = text.split("\n")

    # 找到"价税合计"行和"开票人"行的索引
    total_idx = -1
    invoicer_idx = -1
    for i, line in enumerate(lines):
        # 找到第一个包含"价税合计"的行
        if "价税合计" in line and total_idx == -1:
            total_idx = i
        # 找到包含"开票人"的行
        if "开票人" in line:
            invoicer_idx = i
            break

    # 没找到就返回空
    if total_idx == -1 or invoicer_idx == -1:
        return ""

    # 价税合计行之后、开票人行之前的所有行，都是备注区域
    remark_lines = lines[total_idx + 1:invoicer_idx]

    # 去掉竖排的"备"和"注"单独成行的情况
    cleaned = []
    for line in remark_lines:
        stripped = line.strip()
        # 跳过纯"备"或纯"注"的行（竖排备注标题）
        if stripped in ("备", "注", "备注"):
            continue
        # 跳过空行
        if stripped:
            cleaned.append(stripped)

    # 用空格拼接成一行
    return " ".join(cleaned)


# ============================================================================
# 统一入口函数
# ============================================================================
def extract_invoice(file_path):
    """
    统一入口：传入发票文件路径，返回结构化字段字典

    参数：
        file_path: PDF发票文件路径

    返回：
        字典，包含所有提取到的字段
    """
    # 判断文件是不是PDF
    if not file_path.lower().endswith(".pdf"):
        return {"error": "目前只支持PDF电子发票，图片请用qwen-vl OCR"}

    # 打开PDF，提取第1页的页面对象和纯文本
    with pdfplumber.open(file_path) as pdf:
        page = pdf.pages[0]       # 页面对象（用于坐标法提取明细）
        text = page.extract_text()  # 纯文本（用于正则提取基本字段）

    result = {}

    # ===== 1. 发票类型（第一行标题）=====
    first_line = text.split("\n")[0].strip()
    result["发票类型"] = first_line
    # 根据标题判断是专票还是普票
    if "专用发票" in first_line:
        result["发票种类"] = "增值税专用发票"
    elif "普通发票" in first_line:
        result["发票种类"] = "增值税普通发票"
    else:
        result["发票种类"] = "未知"

    # ===== 2. 发票号码、开票日期 =====
    # [：:] 表示匹配中文冒号或英文冒号
    # \s* 表示冒号后面可以有0个或多个空格
    # (\S+) 表示捕获后面的非空白字符（直到遇到空格）
    result["发票号码"] = safe_search(r"发票号码[：:]\s*(\S+)", text)
    result["开票日期"] = safe_search(r"开票日期[：:]\s*(\S+)", text)

    # ===== 3. 购买方/销售方名称和税号 =====
    # findall找到所有"名称：XXX"，第1个是购买方，第2个是销售方
    name_matches = re.findall(r"名称[：:]\s*(\S+)", text)
    if len(name_matches) >= 1:
        result["购买方名称"] = name_matches[0]
    if len(name_matches) >= 2:
        result["销售方名称"] = name_matches[1]

    # 税号：兼容PDF特殊字符（㇐代替"一"，⼈代替"人"）
    # 有些PDF渲染时会把"统一社会信用代码"的"一"变成特殊字符㇐
    # 用字符类 [㇐一] 同时匹配正常字和特殊字
    tax_matches = re.findall(
        r"(?:统[㇐一]社会信用代码/纳税[⼈人]识别号|纳税[⼈人]识别号|统[㇐一]社会信用代码)[：:]\s*(\S+)",
        text
    )
    if len(tax_matches) >= 1:
        result["购买方税号"] = tax_matches[0]
    if len(tax_matches) >= 2:
        result["销售方税号"] = tax_matches[1]

    # ===== 4. 金额信息 =====
    # 匹配"合 计 ¥2437.74 ¥146.26"
    # \s* 匹配"合"和"计"之间的空格
    # ¥([\d.]+) 捕获金额数字
    total_match = re.search(r"合\s*计\s*¥([\d.]+)\s*¥([\d.]+)", text)
    total_amount = "0"  # 不含税总金额，用于后面智能拆分数量单价
    if total_match:
        result["不含税金额"] = total_match.group(1)
        result["税额"] = total_match.group(2)
        total_amount = total_match.group(1)

    # 价税合计小写：兼容中文括号（小写）和英文括号(小写)
    # [（(] 匹配中文左括号或英文左括号，[）)] 同理
    result["价税合计小写"] = safe_search(r"[（(]小写[）)]\s*¥?\s*([\d.]+)", text)
    # 价税合计大写：兼容中英文括号，以及大写金额前面可能有的⊗或×符号
    result["价税合计大写"] = safe_search(r"价税合计[（(]大写[）)]\s*[⊗×]?\s*(\S+)", text)

    # 税率：兼容数字百分比（6%、13%）和特殊税率（免税、不征税、零税率）
    result["税率"] = safe_search(r"(\d{1,2}%|免税|不征税|零税率|出口免税)", text)

    # ===== 5. 商品明细（坐标法，核心）=====
    result["明细列表"] = extract_detail_by_coordinates(page, total_amount)

    # ===== 6. 备注栏 =====
    result["备注"] = extract_remark(text)

    # ===== 7. 开票人 =====
    result["开票人"] = safe_search(r"开票人[：:]\s*(\S+)", text)

    return result


# ============================================================================
# 测试代码
# ============================================================================
if __name__ == "__main__":
    # 测试1：保险发票（规格型号有值、免税、英文括号）
    print("=" * 60)
    print("测试1：保险发票（规格型号有值、免税）")
    print("=" * 60)
    result1 = extract_invoice(r"D:\agent\财务报销项目\发票材料\pdf\增值税发票.pdf")
    print(json.dumps(result1, ensure_ascii=False, indent=2))

    # 明细校验：数量 × 单价 是否等于 金额
    print("\n=== 明细校验 ===")
    for i, item in enumerate(result1.get("明细列表", [])):
        try:
            qty = float(item.get("数量", 0) or 0)
            price = float(item.get("单价", 0) or 0)
            amount = float(item.get("金额", 0) or 0)
            calc = qty * price
            status = "✓一致" if abs(calc - amount) < 0.01 else "✗不一致"
            print(f"第{i+1}行：数量{qty} × 单价{price} = {calc:.2f}，金额{amount}，{status}")
        except (ValueError, TypeError):
            print(f"第{i+1}行：数据无法计算")
