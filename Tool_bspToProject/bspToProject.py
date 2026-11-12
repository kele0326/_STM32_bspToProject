#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bspToProject.py - 同步 bsp 目录中的 .c 文件到 Keil / EIDE 工程

功能：
  1. 递归扫描工程根目录下 bsp 文件夹（含所有子文件夹）中的 .c 文件；
  2. 把新增的 .c 添加到 Keil .uvprojx 的 bsp 组，以及 EIDE .eide/eide.yml 的 bsp 虚拟文件夹；
  3. 把 bsp 中已经删除的 .c 从上述工程文件中移除；
  4. 修改前自动备份，修改后校验，失败回滚。

设计约束：
  - 只使用 Python 标准库（不依赖 PyYAML）；
  - 不写死工程名、工程文件名；
  - 按行编辑，尽量保持原文件的缩进、换行、BOM 和 XML 声明不变。

用法：
  python bspToProject.py
  python bspToProject.py --dry-run
  python bspToProject.py D:\\path\\to\\Project
  python bspToProject.py D:\\path\\to\\Project\\MDK-ARM\\xxx.uvprojx
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import sys
import traceback
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape as xml_escape


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_ROOT = SCRIPT_DIR.parent
LOG_PATH = SCRIPT_DIR / "bspToProject_log.txt"

# 扫描工程时跳过的目录（大小写不敏感）
EXCLUDE_DIRS = {
    ".git",
    ".svn",
    ".vs",
    ".idea",
    "node_modules",
    "__pycache__",
    "tool_bsptoproject",
    "backup",
}

# 扫描 bsp 时跳过的目录（大小写不敏感）
EXCLUDE_BSP_DIRS = {
    ".git",
    ".svn",
    ".vs",
    ".idea",
    "node_modules",
    "__pycache__",
}


# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
class Logger:
    def __init__(self, verbose=False, log_path=LOG_PATH):
        self.verbose = verbose
        self.log_path = log_path
        self.lines = []

    def _emit(self, line):
        print(line)
        self.lines.append(line)

    def info(self, message):
        self._emit(message)

    def detail(self, message):
        if self.verbose:
            self._emit("  " + message)

    def warn(self, message):
        self._emit("[警告] " + message)

    def error(self, message):
        self._emit("[错误] " + message)

    def save(self):
        if not self.log_path:
            return
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            text = "\n".join(self.lines) + "\n"
            self.log_path.write_text(text, encoding="utf-8-sig")
        except Exception as exc:  # 日志失败不应影响主流程
            print("[警告] 无法写入日志 %s: %s" % (self.log_path, exc))


# ---------------------------------------------------------------------------
# 文件读写（保留 BOM / 编码 / 换行）
# ---------------------------------------------------------------------------
BOM_UTF8 = b"\xef\xbb\xbf"
BOM_UTF16_LE = b"\xff\xfe"
BOM_UTF16_BE = b"\xfe\xff"


class TextDoc:
    def __init__(self, path):
        self.path = Path(path)
        self.raw = self.path.read_bytes()
        self.bom = b""
        self.encoding = "utf-8"

        if self.raw.startswith(BOM_UTF8):
            self.bom = BOM_UTF8
            body = self.raw[len(BOM_UTF8):]
            self.encoding = "utf-8"
        elif self.raw.startswith(BOM_UTF16_LE):
            self.bom = BOM_UTF16_LE
            body = self.raw[len(BOM_UTF16_LE):]
            self.encoding = "utf-16-le"
        elif self.raw.startswith(BOM_UTF16_BE):
            self.bom = BOM_UTF16_BE
            body = self.raw[len(BOM_UTF16_BE):]
            self.encoding = "utf-16-be"
        else:
            body = self.raw
            self.encoding = "utf-8"

        try:
            self.text = body.decode(self.encoding)
        except UnicodeDecodeError:
            self.encoding = "gb18030"
            self.bom = b""
            self.text = self.raw.decode(self.encoding)

        self.newline = "\r\n" if "\r\n" in self.text else "\n"
        self.final_newline = self.text.endswith(("\n", "\r"))
        self.lines = self.text.splitlines()

    def encode(self, text):
        return self.bom + text.encode(self.encoding)

    def render(self, lines):
        if not lines:
            return ""
        text = self.newline.join(lines)
        if self.final_newline:
            text += self.newline
        return text


# ---------------------------------------------------------------------------
# 路径 / 文本小工具
# ---------------------------------------------------------------------------
def _norm(path):
    if path is None:
        return ""
    return os.path.normcase(os.path.abspath(str(path)))


def _is_under(child, parent):
    if child is None or parent is None:
        return False
    c = _norm(child)
    p = _norm(parent)
    try:
        return os.path.commonpath([c, p]) == p
    except ValueError:
        return False


def _rel(target, base, sep):
    try:
        value = os.path.relpath(str(target), str(base))
    except ValueError:
        value = str(target)
    value = value.replace("\\", "/")
    if sep == "/":
        return value
    return value.replace("/", sep)


def _resolve(raw, base):
    value = (raw or "").strip()
    if not value:
        return None
    value = value.replace("\\", "/")
    path = Path(value)
    if not path.is_absolute():
        path = Path(base) / path
    try:
        return path.resolve()
    except OSError:
        return Path(os.path.abspath(str(path)))


def _indent_len(line):
    return len(line) - len(line.lstrip(" \t"))


def _indent_of(line):
    return line[:_indent_len(line)]


def _line_indent(text):
    lines = text.splitlines()
    return _indent_of(lines[0]) if lines else ""


def _yaml_strip_comment(value):
    value = value.strip()
    if not value:
        return value
    quote = None
    i = 0
    while i < len(value):
        ch = value[i]
        if quote:
            if ch == quote:
                if quote == "'" and i + 1 < len(value) and value[i + 1] == "'":
                    i += 2
                    continue
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "#" and (i == 0 or value[i - 1].isspace()):
            return value[:i].rstrip()
        i += 1
    return value


def _yaml_unquote(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            return json.loads(value)
        except Exception:
            return value[1:-1]
    return value


def _yaml_quote(value):
    if value == "":
        return "''"
    unsafe = set(":#{}[],&*!|>'\"%@`")
    if (
        any(ch in unsafe for ch in value)
        or value != value.strip()
        or value.startswith(("- ", "? "))
    ):
        return "'" + value.replace("'", "''") + "'"
    return value


# ---------------------------------------------------------------------------
# 工程发现
# ---------------------------------------------------------------------------
def find_bsp(start_dir):
    current = Path(start_dir).resolve()
    if current.is_file():
        current = current.parent
    while True:
        candidate = current / "bsp"
        if candidate.is_dir():
            return candidate.resolve()
        if current.parent == current:
            break
        current = current.parent
    return None


def scan_sources(bsp_dir, logger):
    result = []
    for dirpath, dirnames, filenames in os.walk(str(bsp_dir)):
        dirnames[:] = sorted(
            [d for d in dirnames if d.lower() not in EXCLUDE_BSP_DIRS],
            key=str.lower,
        )
        for filename in sorted(filenames, key=str.lower):
            path = Path(dirpath) / filename
            if path.suffix.lower() == ".c" and path.is_file():
                result.append(path.resolve())
    result.sort(key=lambda p: str(p).lower())

    by_name = {}
    for path in result:
        by_name.setdefault(path.name.lower(), []).append(path)
    for paths in by_name.values():
        if len(paths) > 1:
            logger.warn(
                "bsp 下存在同名 .c，请确认不会产生目标文件冲突："
                + "；".join(str(p) for p in paths)
            )
    return result


class Project:
    def __init__(self, kind, path):
        self.kind = kind
        self.path = Path(path).resolve()
        if kind == "eide":
            parent = self.path.parent
            self.base = parent.parent if parent.name.lower() == ".eide" else parent
        else:
            self.base = self.path.parent
        self.bsp = find_bsp(self.base)


def discover_projects(roots):
    keil_paths = []
    eide_paths = []

    for root in roots:
        root = Path(root)
        if not root.exists():
            raise FileNotFoundError("路径不存在：%s" % root)
        if root.is_file():
            name = root.name.lower()
            if name.endswith(".uvprojx"):
                keil_paths.append(root.resolve())
            elif name == "eide.yml":
                eide_paths.append(root.resolve())
            else:
                raise ValueError("无法识别的文件（需要 .uvprojx 或 eide.yml）：%s" % root)
            continue

        for dirpath, dirnames, filenames in os.walk(str(root)):
            dirnames[:] = sorted(
                [d for d in dirnames if d.lower() not in EXCLUDE_DIRS],
                key=str.lower,
            )
            for filename in filenames:
                path = Path(dirpath) / filename
                lower = filename.lower()
                if lower.endswith(".uvprojx"):
                    keil_paths.append(path.resolve())
                elif lower == "eide.yml" and path.parent.name.lower() == ".eide":
                    eide_paths.append(path.resolve())

    def dedup(items):
        seen = set()
        result = []
        for item in items:
            key = _norm(item)
            if key not in seen:
                seen.add(key)
                result.append(item)
        result.sort(key=lambda p: str(p).lower())
        return result

    projects = [Project("keil", p) for p in dedup(keil_paths)]
    projects.extend(Project("eide", p) for p in dedup(eide_paths))
    return projects


# ---------------------------------------------------------------------------
# Keil .uvprojx 编辑
# ---------------------------------------------------------------------------
RE_TARGET = re.compile(r"(?P<indent>[ \t]*)<Target(?:\s[^>]*)?>.*?</Target>", re.S)
RE_GROUPS = re.compile(r"(?P<indent>[ \t]*)<Groups(?:\s[^>]*)?>.*?</Groups>", re.S)
RE_GROUP = re.compile(r"(?P<indent>[ \t]*)<Group(?:\s[^>]*)?>.*?</Group>", re.S)
RE_FILES = re.compile(r"(?P<indent>[ \t]*)<Files(?:\s[^>]*)?>.*?</Files>", re.S)
RE_FILE = re.compile(r"(?P<indent>[ \t]*)<File(?:\s[^>]*)?>.*?</File>", re.S)

RE_TARGET_NAME = re.compile(r"<TargetName>(.*?)</TargetName>", re.S)
RE_GROUP_NAME = re.compile(r"<GroupName>(.*?)</GroupName>", re.S)
RE_FILE_PATH = re.compile(r"<FilePath>(.*?)</FilePath>", re.S)


def _keil_name(block, regex):
    match = regex.search(block)
    return html.unescape(match.group(1)).strip() if match else ""


def _keil_file_block(indent, name, rel_path):
    return (
        indent
        + "<File>\n"
        + indent
        + "  <FileName>"
        + xml_escape(name)
        + "</FileName>\n"
        + indent
        + "  <FileType>1</FileType>\n"
        + indent
        + "  <FilePath>"
        + xml_escape(rel_path)
        + "</FilePath>\n"
        + indent
        + "</File>"
    )


def _keil_files_block(files_indent, desired, base):
    if not desired:
        return files_indent + "<Files></Files>"
    file_indent = files_indent + "  "
    blocks = [
        _keil_file_block(file_indent, path.name, _rel(path, base, "\\"))
        for path in desired
    ]
    return (
        files_indent
        + "<Files>\n"
        + "\n".join(blocks)
        + "\n"
        + files_indent
        + "</Files>"
    )


def _keil_group_block(group_indent, desired, base):
    files_indent = group_indent + "  "
    return (
        group_indent
        + "<Group>\n"
        + group_indent
        + "  <GroupName>bsp</GroupName>\n"
        + _keil_files_block(files_indent, desired, base)
        + "\n"
        + group_indent
        + "</Group>"
    )


def _keil_groups_block(groups_indent, desired, base):
    group_indent = groups_indent + "  "
    return (
        groups_indent
        + "<Groups>\n"
        + _keil_group_block(group_indent, desired, base)
        + "\n"
        + groups_indent
        + "</Groups>"
    )


def _keil_edit_files(files_text, desired, base, bsp_dir, newline, logger):
    entries = []
    for match in RE_FILE.finditer(files_text):
        path_match = RE_FILE_PATH.search(match.group(0))
        raw = html.unescape(path_match.group(1)).strip() if path_match else ""
        resolved = _resolve(raw, base)
        entries.append(
            {
                "match": match,
                "raw": raw,
                "resolved": resolved,
            }
        )

    remove_entries = []
    existing = set()
    for entry in entries:
        resolved = entry["resolved"]
        if resolved is None:
            continue
        is_bsp_c = (
            resolved.suffix.lower() == ".c" and _is_under(resolved, bsp_dir)
        )
        if is_bsp_c:
            if resolved.is_file():
                existing.add(_norm(resolved))
            else:
                remove_entries.append(entry)

    add_paths = [p for p in desired if _norm(p) not in existing]
    if not remove_entries and not add_paths:
        return files_text, []

    work = files_text
    for entry in sorted(
        remove_entries, key=lambda item: item["match"].start(), reverse=True
    ):
        start = entry["match"].start()
        end = entry["match"].end()
        if work[end:end + 2] == "\r\n":
            end += 2
        elif work[end:end + 1] in ("\n", "\r"):
            end += 1
        work = work[:start] + work[end:]

    if add_paths:
        close = re.search(r"(?P<indent>[ \t]*)</Files>", work)
        if not close:
            raise ValueError("bsp 组中找不到 </Files>，无法插入文件")
        first_file = RE_FILE.search(work)
        if first_file:
            file_indent = _indent_of(first_file.group(0))
        else:
            file_indent = close.group("indent") + "  "
        blocks = [
            _keil_file_block(
                file_indent, path.name, _rel(path, base, "\\")
            )
            for path in add_paths
        ]
        insert = newline.join(blocks) + newline
        position = close.start()
        if position > 0 and work[position - 1] not in "\r\n":
            insert = newline + insert
        work = work[:position] + insert + work[position:]

    changes = []
    for entry in remove_entries:
        changes.append("删除 " + _rel(entry["resolved"], base, "\\"))
        logger.detail("删除 Keil 文件项：" + entry["raw"])
    for path in add_paths:
        changes.append("新增 " + _rel(path, base, "\\"))
        logger.detail("新增 Keil 文件项：" + _rel(path, base, "\\"))
    return work, changes


def _keil_edit_group(group_text, desired, base, bsp_dir, newline, logger):
    files_match = RE_FILES.search(group_text)
    if not files_match:
        if not desired:
            return group_text, []
        group_indent = _line_indent(group_text)
        files_indent = group_indent + "  "
        block = _keil_files_block(files_indent, desired, base)
        close = re.search(r"(?P<indent>[ \t]*)</Group>", group_text)
        if not close:
            raise ValueError("bsp 组中找不到 </Group>")
        new_group = (
            group_text[:close.start()]
            + block
            + newline
            + group_text[close.start():]
        )
        return new_group, ["新增 bsp 组文件列表"]

    new_files, changes = _keil_edit_files(
        files_match.group(0), desired, base, bsp_dir, newline, logger
    )
    if new_files == files_match.group(0):
        return group_text, []
    new_group = (
        group_text[:files_match.start()]
        + new_files
        + group_text[files_match.end():]
    )
    return new_group, changes


def _keil_edit_target(target_text, desired, base, bsp_dir, create, newline, logger):
    target_name = _keil_name(target_text, RE_TARGET_NAME) or "(未命名 Target)"
    groups_match = RE_GROUPS.search(target_text)

    if not groups_match:
        if not create or not desired:
            return target_text, []
        groups_indent = _indent_of(target_text) + "  "
        block = _keil_groups_block(groups_indent, desired, base)
        close = re.search(r"(?P<indent>[ \t]*)</Target>", target_text)
        if not close:
            raise ValueError("Target 中找不到 </Target>")
        new_target = (
            target_text[:close.start()]
            + block
            + newline
            + target_text[close.start():]
        )
        logger.detail(
            "Target %s：新建 <Groups> 和 bsp 组" % target_name
        )
        return new_target, ["Target %s：新建 bsp 组" % target_name]

    groups_text = groups_match.group(0)
    bsp_match = None
    for group_match in RE_GROUP.finditer(groups_text):
        if _keil_name(group_match.group(0), RE_GROUP_NAME).lower() == "bsp":
            bsp_match = group_match
            break

    if bsp_match is None:
        if not create or not desired:
            return target_text, []
        groups_indent = _line_indent(groups_text)
        group_block = _keil_group_block(groups_indent + "  ", desired, base)
        close = re.search(r"(?P<indent>[ \t]*)</Groups>", groups_text)
        if not close:
            raise ValueError("Target 中找不到 </Groups>")
        new_groups = (
            groups_text[:close.start()]
            + group_block
            + newline
            + groups_text[close.start():]
        )
        new_target = (
            target_text[:groups_match.start()]
            + new_groups
            + target_text[groups_match.end():]
        )
        logger.detail("Target %s：新建 bsp 组" % target_name)
        return new_target, ["Target %s：新建 bsp 组" % target_name]

    new_group, changes = _keil_edit_group(
        bsp_match.group(0), desired, base, bsp_dir, newline, logger
    )
    if new_group == bsp_match.group(0):
        return target_text, []
    new_groups = (
        groups_text[:bsp_match.start()]
        + new_group
        + groups_text[bsp_match.end():]
    )
    new_target = (
        target_text[:groups_match.start()]
        + new_groups
        + target_text[groups_match.end():]
    )
    return new_target, changes


def plan_keil(project, desired, all_targets, logger):
    doc = TextDoc(project.path)
    matches = list(RE_TARGET.finditer(doc.text))
    if not matches:
        raise ValueError("工程文件中没有找到 <Target>：%s" % project.path)

    info = []
    has_any_bsp = False
    for target_match in matches:
        target_text = target_match.group(0)
        target_name = _keil_name(target_text, RE_TARGET_NAME) or "(未命名 Target)"
        groups_match = RE_GROUPS.search(target_text)
        has_bsp = False
        if groups_match:
            for group_match in RE_GROUP.finditer(groups_match.group(0)):
                if _keil_name(group_match.group(0), RE_GROUP_NAME).lower() == "bsp":
                    has_bsp = True
                    break
        has_any_bsp = has_any_bsp or has_bsp
        info.append((target_text, target_name, has_bsp))

    new_text = doc.text
    changes = []
    for index in range(len(matches) - 1, -1, -1):
        target_match = matches[index]
        target_text, target_name, has_bsp = info[index]
        if has_bsp:
            create = True
        elif all_targets:
            create = True
        elif not has_any_bsp and index == 0:
            create = True
        else:
            create = False
        if not create:
            continue
        if not desired and not has_bsp:
            logger.detail("Target %s：bsp 为空，不创建空组" % target_name)
            continue
        new_target, target_changes = _keil_edit_target(
            target_text,
            desired,
            project.base,
            project.bsp,
            create,
            doc.newline,
            logger,
        )
        if new_target != target_text:
            new_text = (
                new_text[:target_match.start()]
                + new_target
                + new_text[target_match.end():]
            )
            changes.extend(
                "%s：%s" % (target_name, change) for change in target_changes
            )

    if new_text == doc.text:
        return None, []

    # 写前校验：必须仍然是合法 XML
    try:
        ET.fromstring(doc.encode(new_text))
    except ET.ParseError as exc:
        raise ValueError("修改后的 uvprojx XML 校验失败：%s" % exc)

    return new_text, changes


# ---------------------------------------------------------------------------
# EIDE .eide/eide.yml 编辑
# ---------------------------------------------------------------------------
def _eide_layout(lines):
    vf = -1
    for index, line in enumerate(lines):
        if line.strip() == "virtualFolder:":
            vf = index
            break
    if vf < 0:
        return None

    vf_end = len(lines)
    for index in range(vf + 1, len(lines)):
        line = lines[index]
        if (
            line.strip()
            and not line.lstrip().startswith("#")
            and _indent_len(line) == 0
        ):
            vf_end = index
            break

    folders = -1
    for index in range(vf + 1, vf_end):
        if _indent_len(lines[index]) == 2 and lines[index].strip().startswith(
            "folders:"
        ):
            folders = index
            break
    return {"vf": vf, "vf_end": vf_end, "folders": folders}


def _eide_find_bsp(lines, folders_idx, vf_end):
    item_indent = _indent_len(lines[folders_idx]) + 2
    for index in range(folders_idx + 1, vf_end):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if _indent_len(line) != item_indent:
            continue
        match = re.match(r"^\s*-\s+name:\s*(?P<name>.+?)\s*$", line)
        if not match:
            continue
        name = _yaml_unquote(_yaml_strip_comment(match.group("name")))
        if name.lower() == "bsp":
            return index, item_indent
    return -1, item_indent


def _eide_block_end(lines, start, limit, item_indent):
    index = start + 1
    while index < limit:
        line = lines[index]
        if (
            line.strip()
            and not line.lstrip().startswith("#")
            and _indent_len(line) <= item_indent
        ):
            break
        index += 1
    return index


def _eide_insert_index(lines, folders_idx, vf_end):
    last = folders_idx
    for index in range(folders_idx + 1, vf_end):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if _indent_len(line) <= 2:
            break
        last = index
    return last + 1


def _eide_create_block(item_indent, desired, base):
    indent6 = " " * (item_indent + 2)
    entry_indent = " " * (item_indent + 4)
    block = [" " * item_indent + "- name: bsp"]
    if desired:
        block.append(indent6 + "files:")
        for path in desired:
            block.append(
                entry_indent + "- path: " + _yaml_quote(_rel(path, base, "/"))
            )
    else:
        block.append(indent6 + "files: []")
    block.append(indent6 + "folders: []")
    return block


def _eide_edit_block(block, desired, base, bsp_dir, logger):
    item_indent = _indent_len(block[0])
    indent6 = " " * (item_indent + 2)

    files_idx = -1
    folders_idx = -1
    for index in range(1, len(block)):
        line = block[index]
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if _indent_len(line) != item_indent + 2:
            continue
        stripped = line.strip()
        if stripped.startswith("files:") and files_idx < 0:
            files_idx = index
        elif stripped.startswith("folders:") and folders_idx < 0:
            folders_idx = index

    if folders_idx >= 0 and files_idx > folders_idx:
        raise ValueError("eide.yml 中 bsp 的 files 出现在 folders 之后，暂不支持")

    files_end = folders_idx if folders_idx >= 0 else len(block)
    entries = []
    entry_indent = indent6 + "  "
    if files_idx >= 0:
        rest = block[files_idx].strip()[len("files:"):].strip()
        if rest not in ("", "[]"):
            raise ValueError("不支持 eide.yml 中 files 的流式写法：%s" % block[files_idx])
        for index in range(files_idx + 1, files_end):
            match = re.match(
                r"^(?P<indent>[ \t]*)- path:\s*(?P<value>.*)$", block[index]
            )
            if not match:
                continue
            if len(entries) == 0:
                entry_indent = _indent_of(block[index])
            raw = _yaml_unquote(_yaml_strip_comment(match.group("value")))
            resolved = _resolve(raw, base)
            entries.append({"index": index, "raw": raw, "resolved": resolved})

    remove_entries = []
    kept = []
    for entry in entries:
        resolved = entry["resolved"]
        is_bsp_c = (
            resolved is not None
            and resolved.suffix.lower() == ".c"
            and _is_under(resolved, bsp_dir)
        )
        if is_bsp_c and not resolved.is_file():
            remove_entries.append(entry)
        else:
            kept.append(entry)

    existing = set()
    for entry in kept:
        resolved = entry["resolved"]
        if (
            resolved is not None
            and resolved.suffix.lower() == ".c"
            and _is_under(resolved, bsp_dir)
            and resolved.is_file()
        ):
            existing.add(_norm(resolved))

    add_paths = [p for p in desired if _norm(p) not in existing]
    if not remove_entries and not add_paths:
        return block, []

    all_entries = list(kept)
    for path in add_paths:
        all_entries.append(
            {"raw": _rel(path, base, "/"), "resolved": path}
        )

    if all_entries:
        files_lines = [indent6 + "files:"]
        for entry in all_entries:
            files_lines.append(
                entry_indent + "- path: " + _yaml_quote(entry["raw"])
            )
    else:
        files_lines = [indent6 + "files: []"]

    if files_idx >= 0:
        prefix = block[:files_idx]
    else:
        prefix = block[:1]
    if folders_idx >= 0:
        suffix = block[folders_idx:]
    else:
        suffix = [indent6 + "folders: []"]

    new_block = prefix + files_lines + suffix
    changes = []
    for entry in remove_entries:
        changes.append("删除 " + entry["raw"])
        logger.detail("删除 EIDE 文件项：" + entry["raw"])
    for path in add_paths:
        rel_path = _rel(path, base, "/")
        changes.append("新增 " + rel_path)
        logger.detail("新增 EIDE 文件项：" + rel_path)
    return new_block, changes


def _eide_validate(text, original_text, desired, base, bsp_dir):
    lines = text.splitlines()
    layout = _eide_layout(lines)
    if layout is None:
        if desired:
            raise ValueError("修改后的 eide.yml 没有 virtualFolder")
        return

    folders_idx = layout["folders"]
    if folders_idx < 0:
        if desired:
            raise ValueError("修改后的 eide.yml 没有 virtualFolder.folders")
        return

    bsp_start, item_indent = _eide_find_bsp(
        lines, folders_idx, layout["vf_end"]
    )
    if bsp_start < 0:
        if desired:
            raise ValueError("修改后的 eide.yml 没有 bsp 虚拟文件夹")
        return

    block_end = _eide_block_end(
        lines, bsp_start, layout["vf_end"], item_indent
    )
    found = set()
    for index in range(bsp_start + 1, block_end):
        match = re.match(
            r"^(?P<indent>[ \t]*)- path:\s*(?P<value>.*)$", lines[index]
        )
        if not match:
            continue
        raw = _yaml_unquote(_yaml_strip_comment(match.group("value")))
        resolved = _resolve(raw, base)
        if (
            resolved is not None
            and resolved.suffix.lower() == ".c"
            and _is_under(resolved, bsp_dir)
        ):
            found.add(_norm(resolved))

    expected = {_norm(path) for path in desired}
    if found != expected:
        missing = expected - found
        extra = found - expected
        detail = []
        if missing:
            detail.append("缺少 " + ", ".join(sorted(missing)))
        if extra:
            detail.append("多余 " + ", ".join(sorted(extra)))
        raise ValueError("eide.yml 校验失败：" + "；".join(detail))

    # 如果环境里恰好有 PyYAML，再做一次完整语法校验；
    # 原文件本身无法解析时跳过，避免因为环境差异误报。
    try:
        import yaml  # type: ignore
    except Exception:
        return
    try:
        yaml.safe_load(original_text)
    except Exception:
        return
    try:
        yaml.safe_load(text)
    except Exception as exc:
        raise ValueError("eide.yml YAML 语法校验失败：%s" % exc)


def plan_eide(project, desired, logger):
    doc = TextDoc(project.path)
    lines = list(doc.lines)
    layout = _eide_layout(lines)

    if layout is None:
        if not desired:
            return None, []
        insert_at = len(lines)
        for index, line in enumerate(lines):
            if _indent_len(line) == 0 and line.strip().startswith(
                ("dependenceList:", "outDir:")
            ):
                insert_at = index
                break
        block = [
            "virtualFolder:",
            "  name: <virtual_root>",
            "  files: []",
            "  folders:",
        ] + _eide_create_block(4, desired, project.base)
        lines[insert_at:insert_at] = block
        changes = ["新建 virtualFolder 和 bsp 虚拟文件夹"]
    else:
        folders_idx = layout["folders"]
        if folders_idx < 0:
            if not desired:
                return None, []
            block = ["  folders:"] + _eide_create_block(
                4, desired, project.base
            )
            lines[layout["vf_end"]:layout["vf_end"]] = block
            changes = ["新建 virtualFolder.folders 和 bsp 虚拟文件夹"]
        else:
            folders_rest = lines[folders_idx].strip()[len("folders:"):].strip()
            if folders_rest == "[]":
                if not desired:
                    return None, []
                lines[folders_idx] = "  folders:"
                block = _eide_create_block(4, desired, project.base)
                lines[folders_idx + 1:folders_idx + 1] = block
                changes = ["新建 bsp 虚拟文件夹"]
            elif folders_rest:
                raise ValueError(
                    "不支持 eide.yml 中 virtualFolder.folders 的流式写法：%s"
                    % lines[folders_idx]
                )
            else:
                bsp_start, item_indent = _eide_find_bsp(
                    lines, folders_idx, layout["vf_end"]
                )
                if bsp_start >= 0:
                    block_end = _eide_block_end(
                        lines, bsp_start, layout["vf_end"], item_indent
                    )
                    new_block, changes = _eide_edit_block(
                        lines[bsp_start:block_end],
                        desired,
                        project.base,
                        project.bsp,
                        logger,
                    )
                    if new_block != lines[bsp_start:block_end]:
                        lines[bsp_start:block_end] = new_block
                    else:
                        changes = []
                else:
                    if not desired:
                        return None, []
                    insert_at = _eide_insert_index(
                        lines, folders_idx, layout["vf_end"]
                    )
                    block = _eide_create_block(
                        item_indent, desired, project.base
                    )
                    lines[insert_at:insert_at] = block
                    changes = ["新建 bsp 虚拟文件夹"]

    if lines == doc.lines:
        return None, []

    new_text = doc.render(lines)
    _eide_validate(
        new_text, doc.text, desired, project.base, project.bsp
    )
    return new_text, changes


# ---------------------------------------------------------------------------
# 备份 / 写入
# ---------------------------------------------------------------------------
class Change:
    def __init__(self, project, doc, new_text, changes):
        self.project = project
        self.path = project.path
        self.doc = doc
        self.old_bytes = doc.raw
        self.old_text = doc.text
        self.new_text = new_text
        self.changes = changes


def _backup_path(path):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = path.with_name(path.name + "." + stamp + ".bak")
    counter = 1
    while candidate.exists():
        candidate = path.with_name(
            path.name + "." + stamp + "_" + str(counter) + ".bak"
        )
        counter += 1
    return candidate


def _prune_backups(path, keep):
    if keep <= 0:
        return
    try:
        backups = sorted(
            path.parent.glob(path.name + ".*.bak"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return
    for old in backups[keep:]:
        try:
            old.unlink()
        except OSError:
            pass


def apply_changes(changes, dry_run, keep_backups, logger):
    if dry_run:
        for change in changes:
            logger.info("  [dry-run] 将修改：" + str(change.path))
        return

    written = []
    try:
        for change in changes:
            if change.new_text == change.old_text:
                continue
            backup = _backup_path(change.path)
            shutil.copy2(str(change.path), str(backup))
            logger.info("  备份：" + str(backup))

            temp = change.path.with_name(
                change.path.name + ".tmp." + str(os.getpid())
            )
            try:
                temp.write_bytes(change.doc.encode(change.new_text))
                os.replace(str(temp), str(change.path))
            except Exception:
                try:
                    temp.unlink()
                except OSError:
                    pass
                raise
            written.append(change)
            logger.info("  已写入：" + str(change.path))
    except Exception:
        # 回滚本次已经写入的文件
        for change in reversed(written):
            try:
                change.path.write_bytes(change.old_bytes)
                logger.warn("已回滚：" + str(change.path))
            except Exception as exc:
                logger.error("回滚失败：" + str(change.path) + "：" + str(exc))
        raise
    finally:
        for change in changes:
            _prune_backups(change.path, keep_backups)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="将 bsp 目录（含子目录）中的 .c 文件同步到 Keil .uvprojx 和 EIDE eide.yml。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  _bspToProject.bat\n"
            "  _bspToProject.bat --dry-run\n"
            "  _bspToProject.bat D:\\path\\to\\Project\n"
            "  _bspToProject.bat D:\\path\\to\\Project\\MDK-ARM\\xxx.uvprojx\n"
        ),
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="工程目录、.uvprojx 或 eide.yml；省略时使用脚本所在的工程根目录",
    )
    parser.add_argument("--root", help="工程根目录（与位置参数二选一）")
    parser.add_argument("--bsp", help="手动指定 bsp 目录")
    parser.add_argument(
        "--dry-run", action="store_true", help="只显示将要修改的内容，不写文件"
    )
    parser.add_argument(
        "--verbose", action="store_true", help="输出详细过程"
    )
    parser.add_argument(
        "--all-targets",
        action="store_true",
        help="给所有 Keil Target 都创建/同步 bsp 组",
    )
    parser.add_argument(
        "--keep-backups",
        type=int,
        default=10,
        help="每个文件保留最近 N 份 .bak，0 表示不清理（默认 10）",
    )
    parser.add_argument(
        "--no-log", action="store_true", help="不写日志文件"
    )
    args = parser.parse_args(argv)
    if args.root and args.paths:
        parser.error("--root 与位置参数不能同时使用")
    if args.keep_backups < 0:
        parser.error("--keep-backups 不能为负数")
    return args


def main(argv=None):
    args = parse_args(argv)
    logger = Logger(
        verbose=args.verbose,
        log_path=None if args.no_log else LOG_PATH,
    )

    logger.info("=" * 64)
    logger.info("bspToProject - Keil / EIDE bsp 源文件同步")
    logger.info("时间：" + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("脚本：" + str(Path(__file__).resolve()))
    logger.info("=" * 64)

    try:
        if args.root:
            roots = [Path(args.root)]
        elif args.paths:
            roots = [Path(p) for p in args.paths]
        else:
            roots = [DEFAULT_ROOT]

        projects = discover_projects(roots)
        if not projects:
            logger.error(
                "没有找到 Keil .uvprojx 或 EIDE .eide/eide.yml。"
                "请把工程目录作为参数传入，或把本工具放在工程根目录下。"
            )
            return 1

        bsp_override = None
        if args.bsp:
            bsp_override = Path(args.bsp).resolve()
            if not bsp_override.is_dir():
                logger.error("--bsp 指定的目录不存在：" + str(bsp_override))
                return 1

        logger.info("发现 %d 个工程文件：" % len(projects))
        for project in projects:
            logger.info(
                "  [%s] %s"
                % ("Keil" if project.kind == "keil" else "EIDE", project.path)
            )

        changes = []
        failed = False
        for project in projects:
            logger.info("")
            logger.info("-" * 64)
            logger.info(
                "[%s] %s"
                % ("Keil" if project.kind == "keil" else "EIDE", project.path)
            )
            bsp_dir = bsp_override or project.bsp
            if bsp_dir is None:
                logger.warn(
                    "在工程附近没有找到 bsp 目录，跳过。"
                    "可用 --bsp 手动指定。"
                )
                continue
            if not bsp_dir.is_dir():
                logger.error("bsp 目录不存在：" + str(bsp_dir))
                failed = True
                continue
            project.bsp = bsp_dir
            desired = scan_sources(bsp_dir, logger)
            logger.info("bsp 目录：" + str(bsp_dir))
            logger.info("扫描到 .c 文件：%d 个" % len(desired))
            for path in desired:
                logger.detail("  " + _rel(path, project.base, "\\"))

            try:
                doc = TextDoc(project.path)
                if project.kind == "keil":
                    new_text, changed = plan_keil(
                        project, desired, args.all_targets, logger
                    )
                else:
                    new_text, changed = plan_eide(
                        project, desired, logger
                    )
            except Exception as exc:
                logger.error("处理失败：" + str(exc))
                if args.verbose:
                    traceback.print_exc()
                failed = True
                continue

            if new_text is None:
                logger.info("已同步，无需修改。")
                continue

            logger.info("计划修改：")
            for item in changed:
                logger.info("  - " + item)
            changes.append(Change(project, doc, new_text, changed))

        if failed:
            logger.error("有工程处理失败，本次不写入任何文件。")
            return 1

        if not changes:
            logger.info("")
            logger.info("所有工程均已同步，没有需要修改的内容。")
            return 0

        logger.info("")
        logger.info("=" * 64)
        logger.info("开始写入 %d 个工程文件……" % len(changes))
        apply_changes(changes, args.dry_run, args.keep_backups, logger)
        if args.dry_run:
            logger.info("dry-run 完成，未写入任何文件。")
        else:
            logger.info("完成：共修改 %d 个工程文件。" % len(changes))
        return 0

    except Exception as exc:
        logger.error(str(exc))
        if args.verbose:
            traceback.print_exc()
        return 1
    finally:
        logger.save()


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
