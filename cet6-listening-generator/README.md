# CET-6 Listening Section A Generator (Kokoro-TTS)

基于本地轻量级语音模型 **Kokoro-TTS** 的大学英语六级（CET-6）长对话高仿模拟题与考场音频自动化生成工具。

## 核心特性
- **双轨文本规范化**：分离 `display_text`（正常排版供复盘）与 `spoken_text`（数字、年份、货币、缩略词全英文展开，彻底规避轻量级 TTS 乱报问题）。
- **录音棚级声学处理**：自动裁剪 Kokoro 单句首尾多余静音，并执行三音色（男主、女主、考官）RMS 响度归一化。
- **高仿考场时间轴**：内置读题前看选项静音窗口、柔和广播“叮”提示音及读题后作答窗口，参数均可在 `config.py` 中一键微调。
- **视听分离导出**：单次运行同步输出 `cet6_exam.wav`、`print_sheet.md`（纯选项做题卷）与 `answer_key.md`（原文与中文解析）。

## 快速开始
1. 确保本地已配置 Python 环境并安装 `kokoro`、`torch`、`numpy` 及 `eSpeak NG`。
2. 在 `config.py` 中按需调整语速（`SPEECH_RATE`）或停顿秒数。
3. 运行生成脚本：
   ```bash
   python generate.py