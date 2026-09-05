# LlamaManager 项目记忆

## ASR 音频格式兼容

- Qwen3-ASR 的上传入口不以文件扩展名作为格式支持依据；文件先由本机 FFmpeg/FFprobe 按内容探测，再切分并导出为 FLAC 后发送给 ASR 服务。
- 已验证运行环境提供 `/usr/bin/ffmpeg` 和 `/usr/bin/ffprobe`。若部署环境缺失 FFmpeg，应安装系统包 `ffmpeg` 后再启动服务。
- `.m4s` 可以上传并在包含完整初始化信息时正常处理。独立 DASH 媒体片段通常缺少 `moov` 初始化信息，必须先与对应初始化段或 MPD 合并成完整媒体文件，不能由单一 M4S 文件还原。
