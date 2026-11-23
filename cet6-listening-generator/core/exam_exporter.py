# -*- coding: utf-8 -*-
import json
import os
import re
import wave
from typing import Dict, List, Tuple

import numpy as np

from core.settings import SAMPLE_RATE


def extract_json_string(raw_text: str) -> str:
    """从粘贴文本中智能提取 JSON 主体（自动剥离代码块外壳）"""
    if not raw_text:
        return ""
    text = raw_text.strip()

    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if fence_match:
        return fence_match.group(1).strip()

    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        return text[first_brace : last_brace + 1].strip()

    return text


def parse_and_validate_exam_json(raw_text: str) -> Tuple[dict, List[str]]:
    """解析并校验 JSON 结构，返回 (解析后的字典, 语音规范化警告列表)"""
    cleaned = extract_json_string(raw_text)
    if not cleaned:
        raise ValueError("输入内容为空，请先粘贴 AI 生成的 JSON 数据。")

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"JSON 语法解析失败（第 {e.lineno} 行，第 {e.colno} 列）：\n{e.msg}\n\n"
            "请检查是否完整复制了大括号，或是否存在未转义的双引号。"
        ) from e

    if not isinstance(data, dict):
        raise ValueError("JSON 根节点必须是对象（以 { 开头，以 } 结尾）。")

    dialogue = data.get("dialogue")
    if not isinstance(dialogue, list) or len(dialogue) == 0:
        raise ValueError("缺少有效的 'dialogue' 对话列表（至少包含 1 轮对话）。")

    warnings_list: List[str] = []

    for idx, turn in enumerate(dialogue, start=1):
        if not isinstance(turn, dict):
            raise ValueError(f"dialogue 第 {idx} 项格式错误，必须是对象。")
        role = str(turn.get("role", "")).strip().upper()
        if role not in ("M", "F"):
            raise ValueError(
                f"dialogue 第 {idx} 轮的角色 'role' 必须为 'M' 或 'F'，当前为: '{role}'。"
            )
        spoken = str(turn.get("spoken_text") or turn.get("display_text") or "").strip()
        if not spoken:
            raise ValueError(f"dialogue 第 {idx} 轮缺少台词文本（spoken_text / display_text）。")

        bad_tokens = re.findall(r"\d+(?:[.,]\d+)?%?|\$\d+|%", spoken)
        if bad_tokens:
            warnings_list.append(
                f"对话第 {idx} 轮语音轨发现未展开的数字/符号 {bad_tokens}，Kokoro 可能会生硬乱读。"
            )

    questions = data.get("questions")
    if not isinstance(questions, list) or len(questions) == 0:
        raise ValueError("缺少有效的 'questions' 题目列表（至少包含 1 道题）。")

    for idx, q in enumerate(questions, start=1):
        if not isinstance(q, dict):
            raise ValueError(f"questions 第 {idx} 题格式错误。")
        q_spoken = str(q.get("spoken_text") or q.get("display_text") or "").strip()
        if not q_spoken:
            raise ValueError(f"第 {idx} 题缺少题干文本。")

        options = q.get("options")
        if not isinstance(options, dict) or not all(k in options for k in ("A", "B", "C", "D")):
            raise ValueError(f"第 {idx} 题的 'options' 必须完整包含 A、B、C、D 四个选项。")

        ans = str(q.get("answer", "")).strip().upper()
        if ans not in ("A", "B", "C", "D"):
            raise ValueError(f"第 {idx} 题的正确答案 'answer' 必须为 A/B/C/D 之一，当前为: '{ans}'。")

        bad_q_tokens = re.findall(r"\d+(?:[.,]\d+)?%?|\$\d+|%", q_spoken)
        if bad_q_tokens:
            warnings_list.append(
                f"第 {idx} 题题干语音轨发现未展开的数字/符号 {bad_q_tokens}。"
            )

    return data, warnings_list


def sanitize_folder_name(name: str) -> str:
    """清理文件夹名称中的 Windows 非法路径字符"""
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", name).strip()
    cleaned = re.sub(r"\s+", "_", cleaned)
    return cleaned or "cet6_practice"


def export_exam_package(
    data: dict,
    audio_data: np.ndarray,
    target_dir: str,
    base_filename: str = "cet6_exam",
    timing_meta: Dict = None,
) -> Dict[str, str]:
    """将考场音频、纯选项打印卷、答案解析与元数据 JSON 统一导出到 target_dir"""
    os.makedirs(target_dir, exist_ok=True)
    safe_base = sanitize_folder_name(base_filename)
    timing_meta = timing_meta or {}

    # 1. 导出 16-bit PCM WAV 音频
    wav_path = os.path.join(target_dir, f"{safe_base}.wav")
    scaled = np.clip(audio_data, -1.0, 1.0) * 32767.0
    int16_audio = scaled.astype(np.int16)
    with wave.open(wav_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(int16_audio.tobytes())

    title = data.get("title", "CET-6 Listening Comprehension - Section A")
    questions = data.get("questions", [])
    dialogue = data.get("dialogue", [])

    pre_sec = timing_meta.get("pre_question_read_sec", 8.0)
    post_sec = timing_meta.get("post_question_answer_sec", 8.0)
    rate = timing_meta.get("speech_rate", 1.0)

    # 2. 导出纯选项打印卷（严格隐藏对话原文与题干，实现视听分离）
    print_path = os.path.join(target_dir, "print_sheet.md")
    with open(print_path, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n")
        f.write(
            f"> **考场节奏**：每题读题干前预留 **{pre_sec:g} 秒** 浏览选项，"
            f"读完题干后预留 **{post_sec:g} 秒** 作答（语速 `{rate}x`）。\n\n---\n\n"
        )
        for q in questions:
            f.write(f"### Question {q['id']}\n\n")
            for opt_key in ("A", "B", "C", "D"):
                opt_text = q.get("options", {}).get(opt_key, "")
                f.write(f"- **{opt_key})** {opt_text}\n")
            f.write("\n")

    # 3. 导出完整对话原文与中文解析
    key_path = os.path.join(target_dir, "answer_key.md")
    with open(key_path, "w", encoding="utf-8") as f:
        f.write(f"# {title} — 听力原文与答案解析\n\n")
        f.write("## 一、 对话原文（Transcript）\n\n")
        for turn in dialogue:
            role_label = "Man" if str(turn["role"]).strip().upper() == "M" else "Woman"
            disp = turn.get("display_text") or turn.get("spoken_text", "")
            f.write(f"**{role_label}:** {disp}\n\n")

        f.write("---\n\n## 二、 题干、答案与解析\n\n")
        for q in questions:
            q_disp = q.get("display_text") or q.get("spoken_text", "")
            f.write(f"### Question {q['id']}: {q_disp}\n\n")
            for opt_key in ("A", "B", "C", "D"):
                opt_text = q.get("options", {}).get(opt_key, "")
                mark = " ?" if opt_key == str(q.get("answer", "")).strip().upper() else ""
                f.write(f"- **{opt_key})** {opt_text}{mark}\n")
            f.write(f"\n- **正确答案**：**{q.get('answer', '')}**\n")
            f.write(f"- **解析定位**：{q.get('explanation', '')}\n\n")

    # 4. 保存结构化元数据 JSON（供刷题面板自动识别加载）
    json_path = os.path.join(target_dir, "exam_data.json")
    export_payload = {
        "title": title,
        "audio_file": f"{safe_base}.wav",
        "timing": timing_meta,
        "dialogue": dialogue,
        "questions": questions,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(export_payload, f, ensure_ascii=False, indent=2)

    return {
        "dir": target_dir,
        "wav": wav_path,
        "print_sheet": print_path,
        "answer_key": key_path,
        "json": json_path,
    }