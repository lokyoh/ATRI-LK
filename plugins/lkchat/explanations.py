from .database import word_table
import jieba

word_dict = {}

for _word in word_table.select_all():
    word_dict[_word[0]] = {
        "meaning": _word[1],
        "import": _word[2]
    }
    if len(_word[0]) > 1:
        jieba.add_word(_word[0])


def get_top_explanations(content):
    words = jieba.lcut_for_search(content)
    matched_words = []
    for word in words:
        if word in word_dict:
            entry = word_dict[word]
            matched_words.append({
                'word': word,
                'meaning': entry['meaning'],
                'importance': entry.get('import', 0)  # 默认为0如果不存在import字段
            })
    matched_words.sort(key=lambda x: x['importance'], reverse=True)
    top_explanations = [
        f"{item['word']}:{item['meaning']}"
        for item in matched_words[:3]
    ]
    return top_explanations


def add_word(wor, mea, imp):
    if wor in word_dict:
        word_dict[wor]['meaning'] = mea
        word_dict[wor]['import'] = imp
        word_table.update((('MEANING', 'IMPORT'), (mea, imp)), (("WORD",), (wor,)))
        return
    if len(wor) > 1:
        jieba.add_word(wor)
    word_dict[wor] = {}
    word_dict[wor]['meaning'] = mea
    word_dict[wor]['import'] = imp
    word_table.insert("WORD, MEANING, IMPORT", (wor, mea, imp))
