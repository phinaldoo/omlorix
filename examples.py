from pathlib import Path
EXAMPLES = {
 'quiz': dict(title='Biology · Check your understanding', summary='Three self-study questions about cells, with feedback and saved progress.', question='Quiz me on cell biology, one question at a time. Explain my mistakes and remember my progress.', intro='Try these three questions. Submit an answer to see the explanation, then review any you missed.', after='Your answers stay with this visualization. You can return later or ask me to explain the topics you found difficult.'),
 'flashcards': dict(title='Spanish · Everyday vocabulary', summary='A four-card recall session with reveal, review ratings, and saved progress.', question='Make Spanish flashcards with reveal, recall ratings, and saved progress.', intro='Say the English meaning before revealing the answer. Rate your recall to decide what to practise next.', after='Again and Hard return the card later in this session. Got it marks it mastered. This is a practice session, not a scheduled repetition service.'),
}
def example_content(name): return Path(__file__).with_name(name+'.html').read_text()
