# -*- coding: utf-8 -*-
"""
生成消融实验用例（eval/cases/*.json）
================================================================
为什么用脚本生成，而不是手写 20 个 JSON：
    1) 发票字段之间有勾稽关系（不含税 + 税额 = 价税合计；数量 × 单价 ≈ 金额；
       明细之和 = 汇总）。手写必然算不平，一算不平基础校验就报 error，
       制度判断的用例就被污染成格式错误的用例；
    2) 每条用例必须标清楚它从哪来（真实票改写 / 合成），标错了就是编数据；
    3) 后面可能扩到 40 条，改参数比改 20 个 JSON 可靠。

用例构成（按用户决定：不做基础规则的故障注入，重点放在制度与报销单）：
    A 制度反推（8 条）
        拿真实住宿票当模板，只改 金额 / 城市 / 职级 / 晚数；
        金标准由《通用企业报销制度》第十四条唯一确定，零主观。
    B 报销单一致性（12 条）
        票面与报销单的「费用类型 / 事由 / 数量口径」是否对得上；
        这类判断读不懂语义就判不出来，是 AI 唯一可能有增量的地方。

噪声控制（避免用例被别的东西干扰）：
    - 购买方统一写「苏州城市学院」（= config 里的 COMPANY_NAME），
      避免抬头 warning 混进结果；抬头校验不在本次实验范围。
    - 发票号码用保留号段 2699000000000000xxxx，不与库里已有号码冲突，
      避免触发重复报销。
    - 金额刻意避开风控「金额临界」带（审批线 500/1000/5000/10000 的 95%~100%），
      避免低级别风险项干扰制度判断。
    - 开票日期统一在 2026-09：既不超 180 天报销期，也不是未来日期。

用法：
    python -m eval.make_cases                 # 生成 eval/cases/*.json 并打印自检预览
    python -m eval.make_cases --preview-only  # 只打印自检预览，不写文件
================================================================
"""
import argparse  # 命令行参数
import json  # 写 JSON
import sys  # 路径
from pathlib import Path  # 路径

ROOT = Path(__file__).resolve().parent.parent  # 项目根目录
if str(ROOT) not in sys.path:  # 保证能 import 到项目模块
    sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# 一、票面常量
#    真实票面：购买方/销售方/税号/项目名称取自项目 发票材料/ 下的真实数电票，
#    改写过的部分在每条用例的 provenance 字段里写明。
# ---------------------------------------------------------------------------
BUYER_NAME = "苏州城市学院"  # 统一购买方（= config.COMPANY_NAME），避免抬头 warning
BUYER_TAX_ID = "12320500MB1F99368P"  # 统一购买方税号

HOTEL_SELLER = "去哪儿网（天津）国际旅行社有限公司武清分公司"  # 真实：去哪儿网住宿票
HOTEL_SELLER_TAX = "91120222MA82BC2U42"  # 真实
MATERIAL_SELLER = "江苏泗阳永益食品有限公司"  # 真实：废料专票的销售方
MATERIAL_SELLER_TAX = "91321323MA1WBFAU8N"  # 真实
CATERING_SELLER = "宿迁市泗阳满庭芳餐饮管理有限公司"  # 合成（项目里没有真实餐饮票）
CATERING_SELLER_TAX = "91321323MA2XYQ7L4K"  # 合成
TRANSPORT_SELLER = "宿迁市泗阳汽车客运有限公司"  # 合成（高铁票金额列解析失败，只能合成）
TRANSPORT_SELLER_TAX = "91321323MA1UX9K22P"  # 合成

HOTEL_ITEM = "*生产生活服务*代订房费"  # 真实：去哪儿网票的项目名称
MATERIAL_ITEM_PIPE = "*金属制品*不锈钢管材"  # 改写（真实为 *金属制品*废铁桶）
MATERIAL_ITEM_DRUM = "*金属制品*废桶"  # 真实：废料票明细行之一
CATERING_ITEM = "*餐饮服务*餐饮费"  # 合成（结构仿照真实数电票）
TRANSPORT_ITEM = "*运输服务*公路旅客运输"  # 合成（结构仿照真实数电票）


# ---------------------------------------------------------------------------
# 二、金额小工具：保证票面字段自洽
# ---------------------------------------------------------------------------
_DIGITS = "零壹贰叁肆伍陆柒捌玖"  # 大写数字
_UNITS4 = ["", "拾", "佰", "仟"]  # 四位以内的位权


def _four(n):
    """0~9999 转中文大写（不含万位）"""
    s = ""  # 结果
    zero = False  # 是否积累了待补的零
    pos = 0  # 当前位
    while n > 0:  # 从个位往上扫
        d = n % 10  # 当前位数字
        if d == 0:  # 是零，先记下来
            zero = True
        else:  # 非零
            if zero and s:  # 前面有零且不是最高位，补一个零
                s = "零" + s
            s = _DIGITS[d] + _UNITS4[pos] + s  # 拼上本位
            zero = False  # 清零标记
        n //= 10  # 下一位
        pos += 1  # 位权右移
    return s


def cn_upper(amount):
    """
    金额转中文大写。

    为什么需要：价税合计大写是基础校验项之一（校验12 大小写一致性）。
    如果这里随便填一个字符串，Agent 可能看出来「大写和小写对不上」，
    把一条本来干净的用例判成票据瑕疵。所以必须算对。
    """
    cents = int(round(float(amount) * 100))  # 转成"分"，避免浮点误差
    yuan, rest = divmod(cents, 100)  # 分 → 元 + 余下分
    jiao, fen = divmod(rest, 10)  # 余下 → 角 + 分
    if yuan == 0:  # 不足一元
        head = ""
    elif yuan >= 10000:  # 上万，拆成 万 + 余
        wan, left = divmod(yuan, 10000)
        head = _four(wan) + "万"
        if 0 < left < 1000:  # 例如 壹万零伍拾，中间要补零
            head += "零"
        if left:  # 有余数才拼
            head += _four(left)
    else:  # 一万以内
        head = _four(yuan)
    if head:
        head += "圆"  # 完整金额加"圆"
    tail = ""  # 角分部分
    if jiao:
        tail += _DIGITS[jiao] + "角"
    if fen:
        tail += _DIGITS[fen] + "分"
    if not tail:  # 没有角分
        tail = "整"
    if not head:  # 不足一元
        head = "零圆"
    return head + tail


def _money(total, rate):
    """价税合计 → (不含税金额, 税额)，按税率拆分并保留两位小数"""
    total = round(float(total), 2)  # 价税合计
    without = round(total / (1 + rate), 2)  # 不含税
    tax = round(total - without, 2)  # 税额（用减法保证 不含税+税额=价税合计）
    return without, tax


def _rate_str(rate):
    """0.06 → 6%"""
    pct = rate * 100  # 百分数
    if abs(pct - round(pct)) < 1e-9:  # 整数百分比
        return str(int(round(pct))) + "%"
    return str(round(pct, 2)) + "%"


def _price(v):
    """单价：保留足够精度，保证 数量 × 单价 ≈ 金额（基础校验要求误差 <1%）"""
    return repr(round(float(v), 10))


def build_invoice(*, number, date, seller, seller_tax, item_name, total, rate,
                  qty=1, unit="次", remark="", drawer="",
                  invoice_type="电子发票（普通发票）", species="增值税普通发票"):
    """按勾稽关系组装一张发票数据字典"""
    without, tax = _money(total, rate)  # 拆分金额
    qty = float(qty)  # 数量
    item = {  # 明细行
        "项目名称": item_name,
        "规格型号": "",
        "单位": unit,
        "数量": ("%g" % qty),  # 1.0 → "1"
        "单价": _price(float(without) / qty),  # 单价 = 不含税金额 / 数量
        "金额": "%.2f" % without,  # 明细金额 = 不含税金额
        "税率": _rate_str(rate),
        "税额": "%.2f" % tax,  # 明细税额 = 税额
    }
    return {
        "发票类型": invoice_type,
        "发票种类": species,
        "发票号码": number,
        "开票日期": date,
        "购买方名称": BUYER_NAME,
        "购买方税号": BUYER_TAX_ID,
        "销售方名称": seller,
        "销售方税号": seller_tax,
        "不含税金额": "%.2f" % without,
        "税额": "%.2f" % tax,
        "税率": _rate_str(rate),
        "价税合计小写": "%.2f" % round(float(total), 2),
        "价税合计大写": cn_upper(total),
        "开票人": drawer,
        "备注": remark,
        "项目名称": item_name,
        "明细列表": [item],
    }


def form(expense_type, reason, total, ext_fields, occur_date="", participants="",
         trip_no="", detail_items=None):
    """组装报销单（统一主表 + 分类扩展）"""
    return {
        "expense_type": expense_type,
        "trip_no": trip_no,
        "occur_date": occur_date,
        "reason": reason,
        "total_amount": round(float(total), 2),
        "participants": participants,
        "ext_fields": ext_fields,
        "detail_items": detail_items or [],
    }


def hotel_remark(hotel, checkin, checkout, nights):
    """住宿票备注：把晚数写进票面，Agent 才有依据核对报销单填的晚数"""
    return ("酒店名称：" + hotel + " 入离店日期：" + checkin + "至" + checkout
            + " 共" + str(nights) + "晚")

# ---------------------------------------------------------------------------
# 三、20 条用例
# ---------------------------------------------------------------------------
CASES = []  # 用例收集器


def add(case_id, group, provenance, note, label, label_reason, adversarial,
        employee_id, invoice, form_):
    """追加一条用例（字段含义见 eval/README.md 第三节）"""
    CASES.append({
        "case_id": case_id,
        "group": group,
        "provenance": provenance,
        "source": group,
        "note": note,
        "label": label,
        "label_reason": label_reason,
        "is_adversarial": adversarial,
        "employee_id": employee_id,
        "invoice": invoice,
        "form": form_,
    })


# ===== A 组：制度反推（住宿费，金标准由制度第十四条唯一确定） =====

add(
    "case_A01", "制度反推",
    "真实票改写（去哪儿网住宿票 26127000000370193970：改写金额/城市/晚数/号码）",
    "上海住宿450元×1晚，报销人李四（部门经理），一线城市部门经理标准500元/天",
    "通过",
    "制度第十四条：一线城市（上海）部门经理500元/天，450<=500，未超标",
    False, 2,
    build_invoice(number="26990000000000000001", date="2026年09月15日",
                  seller=HOTEL_SELLER, seller_tax=HOTEL_SELLER_TAX,
                  item_name=HOTEL_ITEM, total=450.00, rate=0.06, unit="次",
                  remark=hotel_remark("如家酒店(上海人民广场店)", "2026-09-14", "2026-09-15", 1),
                  drawer="邢婕"),
    form("住宿费", "赴上海参加供应商年度洽谈会，住宿1晚", 450.00,
         {"city": "上海", "nights": 1, "rooms": 1, "guests": 1, "guest_names": "李四"},
         occur_date="2026-09-14", participants="李四", trip_no="TRIP-EVAL-A01"),
)

add(
    "case_A02", "制度反推",
    "真实票改写（与 A01 同票面同金额，只换报销人）",
    "上海住宿450元×1晚，报销人张三（普通员工），一线城市普通员工标准400元/天——A01/A02 是同金额不同职级的对照",
    "不通过",
    "制度第十四条：一线城市其他员工400元/天，450>400，超标50元（对比 A01 可证明职级链路真的生效）",
    False, 1,
    build_invoice(number="26990000000000000002", date="2026年09月15日",
                  seller=HOTEL_SELLER, seller_tax=HOTEL_SELLER_TAX,
                  item_name=HOTEL_ITEM, total=450.00, rate=0.06, unit="次",
                  remark=hotel_remark("如家酒店(上海人民广场店)", "2026-09-14", "2026-09-15", 1),
                  drawer="邢婕"),
    form("住宿费", "赴上海参加供应商年度洽谈会，住宿1晚", 450.00,
         {"city": "上海", "nights": 1, "rooms": 1, "guests": 1, "guest_names": "张三"},
         occur_date="2026-09-14", participants="张三", trip_no="TRIP-EVAL-A02"),
)

add(
    "case_A03", "制度反推",
    "真实票改写",
    "上海住宿505元×1晚，报销人李四（部门经理，标准500元/天）",
    "不通过",
    "制度第十四条：一线城市部门经理500元/天，505>500，超标5元",
    False, 2,
    build_invoice(number="26990000000000000003", date="2026年09月16日",
                  seller=HOTEL_SELLER, seller_tax=HOTEL_SELLER_TAX,
                  item_name=HOTEL_ITEM, total=505.00, rate=0.06, unit="次",
                  remark=hotel_remark("汉庭酒店(上海南京东路店)", "2026-09-15", "2026-09-16", 1),
                  drawer="邢婕"),
    form("住宿费", "赴上海拜访客户，住宿1晚", 505.00,
         {"city": "上海", "nights": 1, "rooms": 1, "guests": 1, "guest_names": "李四"},
         occur_date="2026-09-15", participants="李四", trip_no="TRIP-EVAL-A03"),
)

add(
    "case_A04", "制度反推",
    "真实票改写",
    "上海住宿400元×1晚，报销人张三（普通员工，标准400元/天），刚好踩线",
    "通过",
    "制度第十四条：一线城市其他员工400元/天，400=400，未超标（边界值）",
    False, 1,
    build_invoice(number="26990000000000000004", date="2026年09月16日",
                  seller=HOTEL_SELLER, seller_tax=HOTEL_SELLER_TAX,
                  item_name=HOTEL_ITEM, total=400.00, rate=0.06, unit="次",
                  remark=hotel_remark("汉庭酒店(上海南京东路店)", "2026-09-15", "2026-09-16", 1),
                  drawer="邢婕"),
    form("住宿费", "赴上海参加培训，住宿1晚", 400.00,
         {"city": "上海", "nights": 1, "rooms": 1, "guests": 1, "guest_names": "张三"},
         occur_date="2026-09-15", participants="张三", trip_no="TRIP-EVAL-A04"),
)

add(
    "case_A05", "制度反推",
    "真实票改写",
    "南京（二线）住宿380元×1晚，报销人李四（部门经理，二线标准400元/天）",
    "通过",
    "制度第十四条：二线城市部门经理400元/天，380<=400",
    False, 2,
    build_invoice(number="26990000000000000005", date="2026年09月17日",
                  seller=HOTEL_SELLER, seller_tax=HOTEL_SELLER_TAX,
                  item_name=HOTEL_ITEM, total=380.00, rate=0.06, unit="次",
                  remark=hotel_remark("全季酒店(南京新街口店)", "2026-09-16", "2026-09-17", 1),
                  drawer="邢婕"),
    form("住宿费", "赴南京参加行业展会，住宿1晚", 380.00,
         {"city": "南京", "nights": 1, "rooms": 1, "guests": 1, "guest_names": "李四"},
         occur_date="2026-09-16", participants="李四", trip_no="TRIP-EVAL-A05"),
)

add(
    "case_A06", "制度反推",
    "真实票改写",
    "南京（二线）住宿420元×1晚，报销人李四（部门经理，二线标准400元/天）",
    "不通过",
    "制度第十四条：二线城市部门经理400元/天，420>400，超标20元",
    False, 2,
    build_invoice(number="26990000000000000006", date="2026年09月17日",
                  seller=HOTEL_SELLER, seller_tax=HOTEL_SELLER_TAX,
                  item_name=HOTEL_ITEM, total=420.00, rate=0.06, unit="次",
                  remark=hotel_remark("全季酒店(南京新街口店)", "2026-09-16", "2026-09-17", 1),
                  drawer="邢婕"),
    form("住宿费", "赴南京参加行业展会，住宿1晚", 420.00,
         {"city": "南京", "nights": 1, "rooms": 1, "guests": 1, "guest_names": "李四"},
         occur_date="2026-09-16", participants="李四", trip_no="TRIP-EVAL-A06"),
)

add(
    "case_A07", "制度反推",
    "真实票改写",
    "宿迁住宿200元×1晚，报销人张三（普通员工）。宿迁不在城市清单里，按其他城市处理，标准200元/天",
    "通过",
    "制度第十四条：其他城市其他员工200元/天，200=200，未超标（同时验证未列城市按其他城市处理，不直接拦截）",
    False, 1,
    build_invoice(number="26990000000000000007", date="2026年09月18日",
                  seller=HOTEL_SELLER, seller_tax=HOTEL_SELLER_TAX,
                  item_name=HOTEL_ITEM, total=200.00, rate=0.06, unit="次",
                  remark=hotel_remark("锦江之星(宿迁项王故里店)", "2026-09-17", "2026-09-18", 1),
                  drawer="邢婕"),
    form("住宿费", "赴宿迁客户现场技术支持，住宿1晚", 200.00,
         {"city": "宿迁", "nights": 1, "rooms": 1, "guests": 1, "guest_names": "张三"},
         occur_date="2026-09-17", participants="张三", trip_no="TRIP-EVAL-A07"),
)

add(
    "case_A08", "制度反推",
    "真实票改写（票面备注写共2晚）",
    "上海住宿900元×2晚（折合450元/晚），报销人李四（部门经理，一线标准500元/天）。注意票面总额900元大于500元",
    "通过",
    "制度第十四条 说明4 住宿天数按实际入住天数计算：900元÷2晚=450元/晚<=500元/晚。把总额直接当单日标准比会误判为超标，这是口径陷阱",
    True, 2,
    build_invoice(number="26990000000000000008", date="2026年09月18日",
                  seller=HOTEL_SELLER, seller_tax=HOTEL_SELLER_TAX,
                  item_name=HOTEL_ITEM, total=900.00, rate=0.06, unit="次",
                  remark=hotel_remark("如家精选酒店(上海陆家嘴店)", "2026-09-16", "2026-09-18", 2),
                  drawer="邢婕"),
    form("住宿费", "赴上海参加为期两天的供应商评审会，住宿2晚", 900.00,
         {"city": "上海", "nights": 2, "rooms": 1, "guests": 1, "guest_names": "李四"},
         occur_date="2026-09-16", participants="李四", trip_no="TRIP-EVAL-A08"),
)

# ===== B 组：报销单一致性（票面 vs 报销单填的内容） =====

add(
    "case_B01", "报销单一致性",
    "真实票改写（废料专票 26322000007820713546：项目名称改为不锈钢管材）",
    "票面是金属材料350元，报销单却按「住宿费/上海/1晚」填报——费用类型与票面不符，但金额350元落在上海部门经理500元标准内，只看金额判不出来",
    "不通过",
    "制度第五条5 发票内容应与实际业务一致：票面为材料采购，报销单按住宿费填报，费用类型与票面不符",
    True, 2,
    build_invoice(number="26990000000000000009", date="2026年09月19日",
                  seller=MATERIAL_SELLER, seller_tax=MATERIAL_SELLER_TAX,
                  item_name=MATERIAL_ITEM_PIPE, total=350.00, rate=0.13, unit="批",
                  invoice_type="电子发票（增值税专用发票）", species="增值税专用发票",
                  drawer="陈静"),
    form("住宿费", "赴上海出差住宿1晚", 350.00,
         {"city": "上海", "nights": 1, "rooms": 1, "guests": 1, "guest_names": "李四"},
         occur_date="2026-09-19", participants="李四", trip_no="TRIP-EVAL-B01"),
)

add(
    "case_B02", "报销单一致性",
    "合成票（结构仿照真实数电票；项目里没有真实餐饮票）",
    "票面是餐饮服务60元，报销单却按「交通费」填报。60元在80元/天的市内交通标准内，纯看标准一定放过",
    "不通过",
    "制度第五条5：票面为餐饮服务，报销单按交通费填报，费用类型与票面不符",
    True, 3,
    build_invoice(number="26990000000000000010", date="2026年09月19日",
                  seller=CATERING_SELLER, seller_tax=CATERING_SELLER_TAX,
                  item_name=CATERING_ITEM, total=60.00, rate=0.06, unit="次",
                  drawer="周敏"),
    form("交通费", "市区往返客户现场打车费", 60.00,
         {"from_city": "泗阳", "to_city": "宿迁", "vehicle": "出租车/网约车", "passengers": 1},
         occur_date="2026-09-19", participants="王五", trip_no="TRIP-EVAL-B02"),
)

add(
    "case_B03", "报销单一致性",
    "合成票（结构仿照真实数电票）",
    "票面是餐饮服务300元，报销单按「住宿费/南京/1晚」填报。300元落在南京部门经理400元标准内，规则会放过",
    "不通过",
    "制度第五条5：票面为餐饮服务，报销单按住宿费填报，费用类型与票面不符",
    True, 2,
    build_invoice(number="26990000000000000011", date="2026年09月20日",
                  seller=CATERING_SELLER, seller_tax=CATERING_SELLER_TAX,
                  item_name=CATERING_ITEM, total=300.00, rate=0.06, unit="次",
                  drawer="周敏"),
    form("住宿费", "赴南京出差住宿1晚", 300.00,
         {"city": "南京", "nights": 1, "rooms": 1, "guests": 1, "guest_names": "李四"},
         occur_date="2026-09-20", participants="李四", trip_no="TRIP-EVAL-B03"),
)

add(
    "case_B04", "报销单一致性",
    "合成票（结构仿照真实数电票）",
    "票面是公路旅客运输380元，报销单按「住宿费/上海/1晚」填报。380元落在上海普通员工400元标准内，规则会放过",
    "不通过",
    "制度第五条5：票面为旅客运输服务，报销单按住宿费填报，费用类型与票面不符",
    True, 1,
    build_invoice(number="26990000000000000012", date="2026年09月20日",
                  seller=TRANSPORT_SELLER, seller_tax=TRANSPORT_SELLER_TAX,
                  item_name=TRANSPORT_ITEM, total=380.00, rate=0.09, unit="次",
                  drawer="孙磊"),
    form("住宿费", "上海出差酒店住宿1晚", 380.00,
         {"city": "上海", "nights": 1, "rooms": 1, "guests": 1, "guest_names": "张三"},
         occur_date="2026-09-20", participants="张三", trip_no="TRIP-EVAL-B04"),
)

add(
    "case_B05", "报销单一致性",
    "合成票（结构仿照真实数电票）",
    "餐饮票900元，报销单事由写「销售部同事们团建聚餐」，招待对象为空。票面类型与报销单费用类型一致，只能靠事由语义判断",
    "不通过",
    "制度第十八条 业务招待费限于宴请客户/合作方；内部团建聚餐不属于业务招待，属第六条7 与经营业务无关的消费，且缺第十九条要求的招待申请单",
    True, 2,
    build_invoice(number="26990000000000000013", date="2026年09月21日",
                  seller=CATERING_SELLER, seller_tax=CATERING_SELLER_TAX,
                  item_name=CATERING_ITEM, total=900.00, rate=0.06, unit="次",
                  drawer="周敏"),
    form("餐饮费", "销售部同事们团建聚餐", 900.00,
         {"diners": 6, "meal_count": 1, "guest_names": ""},
         occur_date="2026-09-21", participants="李四、王五等6人", trip_no="TRIP-EVAL-B05"),
)

add(
    "case_B06", "报销单一致性",
    "合成票（结构与 B05 完全相同，金额也相同）",
    "餐饮票900元，报销单事由写「招待供应商洽谈采购合同」，招待对象5人、就餐6人（陪餐1人）。与 B05 只有事由和招待对象不同——规则引擎无法区分这两条",
    "通过",
    "制度第二十条：一般客户/部门经理接待人均150-300元/人/次，6人共900元=人均150元，未超标；第十九条3 陪餐1人<=接待对象5人的1/3",
    False, 2,
    build_invoice(number="26990000000000000014", date="2026年09月21日",
                  seller=CATERING_SELLER, seller_tax=CATERING_SELLER_TAX,
                  item_name=CATERING_ITEM, total=900.00, rate=0.06, unit="次",
                  drawer="周敏"),
    form("餐饮费", "招待供应商（泗阳益亿再生资源有限公司）洽谈年度采购合同", 900.00,
         {"diners": 6, "meal_count": 1,
          "guest_names": "泗阳益亿再生资源有限公司采购部王经理等5人"},
         occur_date="2026-09-21", participants="李四（主陪）、王五（陪同）",
         trip_no="TRIP-EVAL-B06"),
)

add(
    "case_B07", "报销单一致性",
    "真实票改写",
    "住宿票450元（上海/1晚，金额本身合规），但报销单事由写「个人周末到上海旅游住宿」，与公务无关",
    "不通过",
    "制度第六条7 与公司经营业务无关的个人消费发票不予报销；第十四条 说明2/4 住宿费限于出差期间且按实际入住天数",
    True, 2,
    build_invoice(number="26990000000000000015", date="2026年09月22日",
                  seller=HOTEL_SELLER, seller_tax=HOTEL_SELLER_TAX,
                  item_name=HOTEL_ITEM, total=450.00, rate=0.06, unit="次",
                  remark=hotel_remark("如家酒店(上海人民广场店)", "2026-09-19", "2026-09-20", 1),
                  drawer="邢婕"),
    form("住宿费", "个人周末到上海旅游住宿", 450.00,
         {"city": "上海", "nights": 1, "rooms": 1, "guests": 1, "guest_names": "李四"},
         occur_date="2026-09-19", participants="李四", trip_no="TRIP-EVAL-B07"),
)

add(
    "case_B08", "报销单一致性",
    "真实票改写（与 B07 同票面同金额，只换事由）",
    "住宿票450元（上海/1晚），报销单事由写「赴上海参加EAI国际会议，会期2天，会议通知已审批」——与 B07 构成对照",
    "通过",
    "制度第二十九条 会议费：参加外部会议凭会议通知和发票报销，会议期间食宿按差旅费标准执行；450元<=500元/天",
    False, 2,
    build_invoice(number="26990000000000000016", date="2026年09月22日",
                  seller=HOTEL_SELLER, seller_tax=HOTEL_SELLER_TAX,
                  item_name=HOTEL_ITEM, total=450.00, rate=0.06, unit="次",
                  remark=hotel_remark("如家酒店(上海人民广场店)", "2026-09-19", "2026-09-20", 1),
                  drawer="邢婕"),
    form("住宿费", "赴上海参加EAI国际会议（会期2天），会议通知已审批", 450.00,
         {"city": "上海", "nights": 1, "rooms": 1, "guests": 1, "guest_names": "李四"},
         occur_date="2026-09-19", participants="李四", trip_no="TRIP-EVAL-B08"),
)

add(
    "case_B09", "报销单一致性",
    "真实票改写（票面备注写共1晚）",
    "发票备注写明入离店共1晚、金额450元，报销单却填「住宿3晚」。金额在标准内（450<=500），只有把票面备注和报销单口径对起来才能发现",
    "不通过",
    "制度第十四条 说明4 住宿天数按实际入住天数计算；票面备注为1晚，报销单填3晚，住宿天数与票据不符（第三条1 真实性原则）",
    True, 2,
    build_invoice(number="26990000000000000017", date="2026年09月23日",
                  seller=HOTEL_SELLER, seller_tax=HOTEL_SELLER_TAX,
                  item_name=HOTEL_ITEM, total=450.00, rate=0.06, unit="次",
                  remark=hotel_remark("如家酒店(上海人民广场店)", "2026-09-14", "2026-09-15", 1),
                  drawer="邢婕"),
    form("住宿费", "赴上海出差住宿3晚", 450.00,
         {"city": "上海", "nights": 3, "rooms": 1, "guests": 1, "guest_names": "李四"},
         occur_date="2026-09-14", participants="李四", trip_no="TRIP-EVAL-B09"),
)

add(
    "case_B10", "报销单一致性",
    "真实票改写（与 B09 同票面同金额，只换报销单填的晚数）",
    "发票备注共1晚，报销单也填1晚——与 B09 构成对照",
    "通过",
    "制度第十四条 说明4：票面备注1晚与报销单1晚一致；450元<=500元/天",
    False, 2,
    build_invoice(number="26990000000000000018", date="2026年09月23日",
                  seller=HOTEL_SELLER, seller_tax=HOTEL_SELLER_TAX,
                  item_name=HOTEL_ITEM, total=450.00, rate=0.06, unit="次",
                  remark=hotel_remark("如家酒店(上海人民广场店)", "2026-09-14", "2026-09-15", 1),
                  drawer="邢婕"),
    form("住宿费", "赴上海出差住宿1晚", 450.00,
         {"city": "上海", "nights": 1, "rooms": 1, "guests": 1, "guest_names": "李四"},
         occur_date="2026-09-14", participants="李四", trip_no="TRIP-EVAL-B10"),
)

add(
    "case_B11", "报销单一致性",
    "真实票改写（废料专票 26322000007820713546，项目名称保留真实的废桶）",
    "材料票1200元，报销单填材料费并提供用途/领用人/使用部门，事由明确。票面与报销单一致，且制度对材料费没有金额标准",
    "通过",
    "票面要素齐全、事由明确、无超标标准可适用，未发现可判定违规点。制度第二十六条对材料/办公采购只提流程留痕要求（采购申请、入库单），不在本实验可判定范围内",
    False, 3,
    build_invoice(number="26990000000000000019", date="2026年09月24日",
                  seller=MATERIAL_SELLER, seller_tax=MATERIAL_SELLER_TAX,
                  item_name=MATERIAL_ITEM_DRUM, total=1200.00, rate=0.13, unit="批",
                  invoice_type="电子发票（增值税专用发票）", species="增值税专用发票",
                  drawer="陈静"),
    form("材料费", "车间废料回收过磅结算（附过磅单）", 1200.00,
         {"purpose": "车间废料回收过磅结算", "receiver": "王五", "use_dept": "技术部"},
         occur_date="2026-09-24", participants="王五", trip_no="TRIP-EVAL-B11"),
)

add(
    "case_B12", "报销单一致性",
    "合成票（结构仿照真实数电票）",
    "出租车票60元，报销单填交通费并写明起止地点与事由（泗阳→宿迁客户现场，当日往返），票面与报销单一致",
    "通过",
    "制度第十六条 市内交通费80元/天包干，60<=80；第二十四条 出租车需注明起止地点和事由，均已填写",
    False, 3,
    build_invoice(number="26990000000000000020", date="2026年09月25日",
                  seller=TRANSPORT_SELLER, seller_tax=TRANSPORT_SELLER_TAX,
                  item_name=TRANSPORT_ITEM, total=60.00, rate=0.09, unit="次",
                  drawer="孙磊"),
    form("交通费", "赴宿迁客户现场技术支持（当日往返，出租车）", 60.00,
         {"from_city": "泗阳", "to_city": "宿迁", "vehicle": "出租车/网约车", "passengers": 1},
         occur_date="2026-09-25", participants="王五", trip_no="TRIP-EVAL-B12"),
)


# ===== C 组：陷阱句（解析器对抗，全部为「不通过」） =====
# 设计意图：这几条的票面/金额大多本身合规，唯一违规点在事由、人员或比例上，
# 目的是逼模型先陈述「合规的部分」再给结论 —— 这是旧解析器最容易翻车的句式。
# 注意：这几条同时也在实测「模型是否会自然写出 通过…不通过 的语序」，
# 如果实测触发率为 0，那是诚实的负结果，要原样写进报告，
# 并使用 eval/inject_trap_text.py 的注入测量给出可控数字。

add(
    "case_T01", "陷阱句", "真实票改写（住宿票）",
    "票面金额与住宿标准完全合规（450≤500），唯一违规点是事由属于个人事务：陪同家人就医",
    "不通过",
    "制度第六条7 与公司经营业务无关的个人消费发票不予报销；第十四条 住宿费限于因公出差期间",
    True, 2,
    build_invoice(number="26990000000000000021", date="2026年09月26日",
                  seller=HOTEL_SELLER, seller_tax=HOTEL_SELLER_TAX,
                  item_name=HOTEL_ITEM, total=450.00, rate=0.06, unit="次",
                  remark=hotel_remark("如家酒店(上海人民广场店)", "2026-09-25", "2026-09-26", 1),
                  drawer="邢婕"),
    form("住宿费", "陪同家人到上海就医，就近住宿1晚", 450.00,
         {"city": "上海", "nights": 1, "rooms": 1, "guests": 1, "guest_names": "李四"},
         occur_date="2026-09-25", participants="李四", trip_no="TRIP-EVAL-T01"),
)

add(
    "case_T02", "陷阱句", "合成票（结构仿照真实数电票）",
    "出租车票60元在80元/天标准内，唯一违规点是起讫地点是家到公司，属通勤而非公务",
    "不通过",
    "制度第二十五条 不予报销的情况：上下班通勤费用；且无明确公务事由",
    True, 3,
    build_invoice(number="26990000000000000022", date="2026年09月26日",
                  seller=TRANSPORT_SELLER, seller_tax=TRANSPORT_SELLER_TAX,
                  item_name=TRANSPORT_ITEM, total=60.00, rate=0.09, unit="次",
                  drawer="孙磊"),
    form("交通费", "上下班通勤打车（家到公司）", 60.00,
         {"from_city": "家", "to_city": "公司", "vehicle": "出租车/网约车", "passengers": 1},
         occur_date="2026-09-26", participants="王五", trip_no="TRIP-EVAL-T02"),
)

add(
    "case_T03", "陷阱句", "合成票（结构仿照真实数电票）",
    "餐饮票1200元、人均200元都在标准内，唯一违规点是陪餐比例：我方陪餐3人、接待对象仅3人，超过「不高于接待对象1/3」",
    "不通过",
    "制度第十九条3 陪餐人数不得超过接待对象人数的1/3（3人接待对象最多陪餐1人）；人均标准合规但比例违规",
    True, 2,
    build_invoice(number="26990000000000000023", date="2026年09月27日",
                  seller=CATERING_SELLER, seller_tax=CATERING_SELLER_TAX,
                  item_name=CATERING_ITEM, total=1200.00, rate=0.06, unit="次",
                  drawer="周敏"),
    form("餐饮费", "招待客户洽谈合作（我方陪餐3人）", 1200.00,
         {"diners": 6, "meal_count": 1,
          "guest_names": "某某材料有限公司采购部等3人"},
         occur_date="2026-09-27", participants="李四、王五、张三（陪餐3人）",
         trip_no="TRIP-EVAL-T03"),
)

add(
    "case_T04", "陷阱句", "真实票改写（废料专票）",
    "材料票1200元要素齐全，唯一违规点是用途写的是个人家庭装修自用",
    "不通过",
    "制度第六条7 与公司经营业务无关的个人消费发票不予报销；第二十六条 个人消费的采购不予报销",
    True, 3,
    build_invoice(number="26990000000000000024", date="2026年09月27日",
                  seller=MATERIAL_SELLER, seller_tax=MATERIAL_SELLER_TAX,
                  item_name=MATERIAL_ITEM_DRUM, total=1200.00, rate=0.13, unit="批",
                  invoice_type="电子发票（增值税专用发票）", species="增值税专用发票",
                  drawer="陈静"),
    form("材料费", "员工个人家庭装修材料采购", 1200.00,
         {"purpose": "个人家庭装修自用", "receiver": "王五", "use_dept": "技术部"},
         occur_date="2026-09-27", participants="王五", trip_no="TRIP-EVAL-T04"),
)


# ---------------------------------------------------------------------------
# 三·补、取用例
# ---------------------------------------------------------------------------
def build_cases():
    """返回 20 条用例（模块加载时 CASES 已经装好）"""
    return CASES


# ---------------------------------------------------------------------------
# 四、自检预览：不烧 token，直接用 C 组（纯规则）跑一遍
# ---------------------------------------------------------------------------
def preview(cases):
    """
    用例自检。

    目的不是出实验结论，而是确认「用例本身没问题」：
        - 票面字段能不能过基础校验（算不平的话 C 组会因为格式错误被判不通过，
          把制度判断的用例污染掉）；
        - 每条用例的金标准与现有规则预测差多少（差异清单就是 AI 要证明自己的地方）。
    真正的实验数字必须由 run_model_once.py 跑模型、replay.py 出，不能用这里的。
    """
    from database.db import get_employee  # 查职级
    from eval.groups import group_C  # 纯规则基线
    from eval.replay import silence_project_logs  # 压日志
    silence_project_logs()  # 12 项基础校验会打 INFO 日志，压掉
    ok = 0  # 规则判对数
    print("")
    print("用例自检（组 C 纯规则预测 vs 金标准）")
    print("-" * 92)
    for c in cases:  # 逐条
        emp = get_employee(c["employee_id"]) or {}  # 查员工
        run = {  # group_C 只读这几个字段，不需要模型输出
            "case_id": c["case_id"],
            "invoice_data": c["invoice"],
            "expense_form": c["form"],
            "employee_id": c["employee_id"],
            "employee_level": emp.get("level"),
            "anomaly_result": None,  # 传空让 group_C 现算风控
        }
        try:
            pred = group_C(run)  # 规则预测
        except Exception as exc:  # 单条异常不影响自检
            pred = "异常:" + str(exc)
        hit = (pred == c["label"])  # 是否与金标准一致
        ok += 1 if hit else 0
        print("  ".join([  # 一行一条
            c["case_id"],
            c["group"],
            "对抗" if c["is_adversarial"] else "常规",
            "金标准=" + c["label"],
            "规则预测=" + str(pred),
            "√" if hit else "×",
        ]))
    print("-" * 92)
    print("规则基线：判对 " + str(ok) + " / " + str(len(cases)))
    print("说明：这里只是自检，确认用例能被现有规则正确处理，不是实验结论。")
    print("      实验数字要跑 run_model_once.py 再用 replay.py 出。")



# ---------------------------------------------------------------------------
# 四·补、用例清单（cases/INDEX.md），报告里直接引用，避免手工抄错
# ---------------------------------------------------------------------------
def write_index(cases, out_dir):
    """把 20 条用例的构成与逐条依据写成 markdown"""
    n_adv = sum(1 for c in cases if c["is_adversarial"])  # 对抗样本数
    n_ok = sum(1 for c in cases if c["label"] == "通过")  # 金标准通过数
    lines = []  # 输出行
    lines.append("# 用例清单（" + str(len(cases)) + " 条）")
    lines.append("")
    lines.append("本文件由 eval/make_cases.py 自动生成，不要手改；改用例请改脚本后重跑。")
    lines.append("")
    lines.append("- 构成：制度反推 8 条 + 报销单一致性 12 条")
    lines.append("- 对抗样本：" + str(n_adv) + " 条（专门用来拉开方案差距的高危样本）")
    lines.append("- 金标准分布：通过 " + str(n_ok) + " / 不通过 " + str(len(cases) - n_ok))
    lines.append("- 来源：真实票改写 15 条 + 合成票 5 条（合成部分在 provenance 里逐条标明）")
    lines.append("")
    lines.append("| 用例 | 来源 | 对抗 | 金标准 | 票面金额 | 报销费用类型 | 关键点 |")
    lines.append("|---|---|---|---|---|---|---|")
    for c in cases:  # 逐条一行
        amount = (c["invoice"].get("价税合计小写") or "").strip()  # 票面金额
        lines.append("| " + " | ".join([
            c["case_id"],
            c["group"],
            "是" if c["is_adversarial"] else "",
            c["label"],
            amount,
            c["form"].get("expense_type") or "",
            c["note"],
        ]) + " |")
    lines.append("")
    lines.append("## 逐条金标准依据")
    lines.append("")
    for c in cases:  # 每条的依据
        lines.append("- **" + c["case_id"] + "**（" + c["label"] + "）：" + c["label_reason"])
        lines.append("  - 票面来源：" + c["provenance"])
    (Path(out_dir) / "INDEX.md").write_text("\n".join(lines), encoding="utf-8")  # 写 UTF-8


# ---------------------------------------------------------------------------
# 五、命令行入口
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="生成消融实验用例")
    parser.add_argument("--out", default=str(Path(__file__).parent / "cases"),
                        help="用例输出目录")
    parser.add_argument("--preview-only", action="store_true",
                        help="只打印自检预览，不写文件")
    args = parser.parse_args()

    cases = build_cases()  # 20 条用例
    out_dir = Path(args.out)  # 输出目录

    if not args.preview_only:  # 需要写文件
        out_dir.mkdir(parents=True, exist_ok=True)  # 确保目录存在
        for c in cases:  # 逐条落盘
            payload = {k: v for k, v in c.items() if k not in ("invoice", "form")}  # 去掉内部键
            payload["invoice_data"] = c["invoice"]  # 转成 runs / replay 认的字段名
            payload["expense_form"] = c["form"]
            target = out_dir / (c["case_id"] + ".json")  # 目标文件名
            target.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                              encoding="utf-8")  # 写 UTF-8
        print("已写入 " + str(len(cases)) + " 个用例到 " + str(out_dir))
        write_index(cases, out_dir)  # 同步生成用例清单

    preview(cases)  # 打印自检表
    return 0


if __name__ == "__main__":
    sys.exit(main())
