import base64
import random
import string
import re
import edge_tts

from ATRI import TEMP_DIR


class RECEditor:
    @staticmethod
    def get_tts_file(text):
        """获取来自edge_tts的文本转语音，需要大陆外网络"""
        spath = "output_" + ''.join(random.choice(string.ascii_letters + string.digits) for _ in range(5)) + ".mp3"
        tts = edge_tts.Communicate(text=re.sub('[³_*]', '', text), voice='zh-CN-XiaoyiNeural', rate='+0%',
                                   volume='+0%')
        tts.save_sync(f'{TEMP_DIR}/{spath}')
        return f'{TEMP_DIR}/{spath}'

    @staticmethod
    def audio_to_base64(audio_path):
        """语音文件转base64"""
        with open(audio_path, "rb") as audio_file:
            audio_data = audio_file.read()
        base64_encoded_audio = base64.b64encode(audio_data).decode('utf-8')
        return f'base64://{base64_encoded_audio}'
