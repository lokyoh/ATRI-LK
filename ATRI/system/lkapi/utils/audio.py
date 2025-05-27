from ATRI.system.lkbot.tools.rec_editor import RECEditor as AudioEditor


def get_tts_audio(text: str) -> str:
    """获取来自edge_tts的文本转语音，需要大陆外网络"""
    return AudioEditor.get_tts_file(text)


def audio_path_to_base64(audio_path: str) -> str:
    """语音文件转base64"""
    return AudioEditor.audio_to_base64(audio_path)
