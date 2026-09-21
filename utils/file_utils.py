# -*- coding: utf-8 -*-
"""
文件工具函数：判断文件类型、保存上传文件等
"""
import os


def detect_file_type(file_path):
    """
    判断文件类型：xml / pdf / jpg / png / unknown
    通过读取文件头（magic number）判断，不依赖文件后缀名
    """
    with open(file_path, 'rb') as f:
        header = f.read(8)  # 只读前8个字节

    # PDF文件头：%PDF-
    if header.startswith(b'%PDF-'):
        return 'pdf'

    # JPG文件头：FF D8 FF
    if header.startswith(b'\xff\xd8\xff'):
        return 'jpg'

    # PNG文件头：89 50 4E 47
    if header.startswith(b'\x89PNG'):
        return 'png'

    # 文本文件（XML）：读前几个字符判断
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            first_chars = f.read(50)
        if '<?xml' in first_chars or '<EInvoice' in first_chars:
            return 'xml'
    except:
        pass

    return 'unknown'


def save_uploaded_file(uploaded_file, save_dir="temp"):
    """
    保存Streamlit上传的文件到临时目录，返回保存后的路径
    """
    if not os.path.exists(save_dir):
        os.makedirs(save_dir)

    save_path = os.path.join(save_dir, uploaded_file.name)
    with open(save_path, 'wb') as f:
        f.write(uploaded_file.getvalue())

    return save_path
