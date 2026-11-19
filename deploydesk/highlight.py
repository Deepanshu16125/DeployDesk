import re

from .retriever import tokenize

_SENT = re.compile(r"[^.!?\n]+(?:[.!?]+|\n|$)")


def sentence_spans(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in _SENT.finditer(text) if m.group().strip()]


def highlights(answer: str, chunks: list, refs: list[int]) -> dict[int, list[list[int]]]:
    # for each cited chunk: char spans of the sentences that best match the answer sentences citing it
    out: dict[int, list[list[int]]] = {}
    for sent in re.split(r"(?<=[.!?])\s+|\n+", answer):
        cited = [int(n) for n in re.findall(r"\[(\d+)\]", sent) if int(n) in refs]
        words = set(tokenize(re.sub(r"\[\d+\]", "", sent)))
        if not cited or len(words) < 2:
            continue
        for ref in cited:
            text = chunks[ref - 1].text
            best, best_score = None, 0.0
            for start, end in sentence_spans(text):
                w = set(tokenize(text[start:end]))
                if not w:
                    continue
                score = len(words & w) / (len(words | w) ** 0.5)
                if score > best_score:
                    best, best_score = [start, end], score
            if best and best_score >= 0.6 and best not in out.setdefault(ref, []):
                out[ref].append(best)
    return out
