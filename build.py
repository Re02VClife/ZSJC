# build.py
import os
import re

module_files = [
    'config.py',
    'widgets.py',
    'detection.py',
    'preview.py',
    'ui.py',
    'main.py'
]

# 收集所有内部模块名（不含.py）
internal_names = [os.path.splitext(m)[0] for m in module_files]

def remove_internal_imports(source_code):
    """
    移除所有对内部模块的 import 语句，支持：
    - import xxx
    - from xxx import yyy
    - from xxx import (yyy, zzz)
    处理跨行和缩进。
    """
    lines = source_code.splitlines(True)
    result_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()
        # 检测是否是 from 内部模块 import ...
        match = re.match(r'from\s+(\S+)\s+import\s+', stripped)
        if match:
            module = match.group(1)
            if module in internal_names:
                # 检查是否以括号开始的多行导入
                after_import = line[match.end():].strip()
                if after_import.startswith('('):
                    # 跳过直到闭合括号
                    # 查找当前行是否已经闭合
                    if ')' in after_import:
                        # 单行括号，直接跳过本行
                        i += 1
                        continue
                    # 多行，跳过后续行直到找到 ')'
                    i += 1
                    while i < len(lines):
                        if ')' in lines[i]:
                            i += 1  # 跳过包含 ')' 的这一行
                            break
                        i += 1
                    continue
                else:
                    # 普通单行导入，跳过本行
                    i += 1
                    continue
        # 检测是否是 import 内部模块
        match2 = re.match(r'import\s+(\S+)', stripped)
        if match2:
            module = match2.group(1)
            if module in internal_names:
                i += 1
                continue
        # 非内部模块导入，保留该行
        result_lines.append(line)
        i += 1
    return ''.join(result_lines)

# 读取文件内容，同时跳过开头的 shebang/coding 声明
skip_prefix_re = re.compile(r'^#!|^#.*coding')

final_lines = []

# ====================== 自动生成的头部长注释（在这里修改内容） ======================
header_comment = """#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ==============================================================================
# 项目名称：动漫张数检测系统
# 功能说明：dxcam捕获、多重过滤非新作画帧、实时预览、UI交互一体化工具
# 模块组成：config.py / widgets.py / detection.py / preview.py / ui.py / main.py
#
# 注意：以下为本项目目前合并后的完整代码，
# 若无确认需求，请先向我提问以确认，确认核心框架，并帮我思考可能遗漏的方面，不要直接编写代码；
# 若已经确认了方向，请根据要求对代码进行修改，对于修改的地方，请描述其所在的类和方法，若改动较大则直接给出修改后的完整类或方法，并加上注释
# 对于要改动或新增的代码，请附带其在原代码位置的上下两行代码方便我进行查找
# 如非必要，原代码的注释没什么问题就不要省略，保持原样即可
# ==============================================================================
"""
# 先把头部注释加入最终文件
final_lines.append(header_comment.strip() + "\n\n")

final_lines.append("# Auto-merged file from multiple modules\n")
final_lines.append("import sys, os, time, threading, json, copy, subprocess\n")
final_lines.append("from collections import deque\n")
final_lines.append("import cv2\n")
final_lines.append("import numpy as np\n")
final_lines.append("import tkinter as tk\n\n")
final_lines.append("# --- Begin merged modules ---\n")

for mod_file in module_files:
    with open(mod_file, 'r', encoding='utf-8') as f:
        content = f.read()

    lines = content.splitlines(True)
    start_idx = 0
    while start_idx < len(lines) and skip_prefix_re.match(lines[start_idx].strip()):
        start_idx += 1
    content = ''.join(lines[start_idx:])

    # 移除内部模块导入
    content = remove_internal_imports(content)

    final_lines.append(f"\n# ====== Module: {mod_file} ======\n")
    final_lines.append(content)

# 写入最终文件
with open('ZhangShuJianCe.py', 'w', encoding='utf-8') as f:
    f.writelines(final_lines)

print("合并完成，输出文件: ZhangShuJianCe.py")