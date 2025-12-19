"""Utility functions for text processing."""
from typing import List

def split_long_lines(segments: List[str], max_words: int = 500) -> List[str]:
    """Split lines that exceed the max word limit while preserving speaker labels and sentence boundaries."""
    split_segments = []
    sentence_endings = [". ", "! ", "? ", ".", "!", "?"]

    for segment in segments:
        speaker_end = segment.find(": ", segment.find("SPEAKER_"))
        if speaker_end == -1:
            split_segments.append(segment)
            continue
            
        speaker_label = segment[:speaker_end]
        text = segment[speaker_end + 2:]
        
        sentences = _split_into_sentences(text, sentence_endings)
        grouped_sentences = _group_sentences(sentences, max_words, speaker_label)
        split_segments.extend(grouped_sentences)

    return split_segments

def _split_into_sentences(text: str, sentence_endings: List[str]) -> List[str]:
    """Split text into sentences based on common sentence endings."""
    sentences = []
    current_pos = 0
    
    while current_pos < len(text):
        next_end = float('inf')
        for ending in sentence_endings:
            pos = text.find(ending, current_pos)
            if pos != -1 and pos < next_end:
                next_end = pos + len(ending)
        
        if next_end == float('inf'):
            sentences.append(text[current_pos:])
            break
        else:
            sentences.append(text[current_pos:next_end])
            current_pos = next_end
    
    return sentences

def _group_sentences(
    sentences: List[str],
    max_words: int,
    speaker_label: str
) -> List[str]:
    """Group sentences into chunks that don't exceed max_words."""
    grouped_segments = []
    current_chunk = []
    current_word_count = 0
    
    for sentence in sentences:
        sentence_words = sentence.split()
        sentence_word_count = len(sentence_words)
        
        if current_word_count + sentence_word_count > max_words and current_chunk:
            chunk_text = ' '.join(current_chunk).strip()
            if chunk_text:
                grouped_segments.append(f"{speaker_label}: {chunk_text}")
            current_chunk = []
            current_word_count = 0
        
        current_chunk.append(sentence)
        current_word_count += sentence_word_count
    
    if current_chunk:
        chunk_text = ' '.join(current_chunk).strip()
        if chunk_text:
            grouped_segments.append(f"{speaker_label}: {chunk_text}")
    
    return grouped_segments
